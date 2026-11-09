"""Tests du client marché — vérifient le paramétrage des en-têtes (sans réseau)."""

from __future__ import annotations

from datetime import datetime, timezone

from betclic_agent.client_ci import BetclicMarketClient
from betclic_agent.config import Settings
from betclic_agent.timing import UPCOMING, match_status


def test_headers_marche_ci():
    settings = Settings()
    settings.market = "CI"
    client = BetclicMarketClient(market="CI", settings=settings)
    headers = client._session.headers
    assert headers["x-bg-regulation"] == "CI"
    assert headers["x-bg-ref-regulator-zone"] == "CI"
    assert headers["Origin"] == "https://www.betclic.ci"
    assert headers["Referer"] == "https://www.betclic.ci/"


def test_headers_marche_fr():
    client = BetclicMarketClient(market="FR")
    headers = client._session.headers
    assert headers["x-bg-regulation"] == "FR"
    assert headers["Origin"] == "https://www.betclic.fr"


def test_marche_inconnu_retombe_sur_ci():
    client = BetclicMarketClient(market="XX")
    assert client.origin == "https://www.betclic.ci"


def test_timeouts_issus_de_la_config():
    settings = Settings()
    settings.timeout_connect = 7
    settings.timeout_read = 21
    client = BetclicMarketClient(market="CI", settings=settings)
    assert client.timeout == (7, 21)


def test_repr_config_ne_divulgue_pas_les_secrets():
    settings = Settings()
    settings.telegram_token = "secret-token-123"
    assert "secret-token-123" not in repr(settings)


# ─── Filtrage par jour et tri ────────────────────────────────────────────────


def _match(mid: int, date: str, competition: str = "Ligue 1", stream: bool = False) -> dict:
    return {
        "id": mid,
        "name": f"Equipe A {mid} - Equipe B {mid}",
        "competition": competition,
        "date": date,
        "live_stream": stream,
        "sport": "football",
        "market": "CI",
    }


def test_matches_on_filtre_la_journee():
    matches = [
        _match(1, "2026-09-17T18:00:00.0000000Z"),
        _match(2, "2026-09-18T09:30:00.0000000Z"),
        _match(3, "2026-09-17T22:45:00.0000000Z"),
    ]
    today = BetclicMarketClient.matches_on(matches, "2026-09-17")
    assert [m["id"] for m in today] == [1, 3]


def test_matches_on_ignore_les_dates_absentes():
    matches = [{"id": 1, "date": None}, {"id": 2}]
    assert BetclicMarketClient.matches_on(matches, "2026-09-17") == []


def test_live_stream_ne_signifie_pas_match_en_cours():
    """Régression : le drapeau du backend renseigne une diffusion, pas le statut.

    Constaté le 17/09/2026 : 18 matchs marqués « live » avaient un coup d'envoi futur.
    """
    match = _match(1, "2026-09-17T18:30:00.0000000Z", stream=True)
    now = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)
    assert match["live_stream"] is True
    assert match_status(match["date"], now) == UPCOMING


def test_sort_by_time_ordonne_par_heure_puis_competition():
    matches = [
        _match(1, "2026-09-17T20:00:00.0000000Z", "Zeta Cup"),
        _match(2, "2026-09-17T18:00:00.0000000Z", "Alpha Cup"),
        _match(3, "2026-09-17T18:00:00.0000000Z", "Beta Cup"),
    ]
    ordered = BetclicMarketClient.sort_by_time(matches)
    assert [m["id"] for m in ordered] == [2, 3, 1]


# ─── Pagination ──────────────────────────────────────────────────────────────


class _FakeMatch:
    """Objet minimal imitant un ``Match`` de la lib (seul ``id`` est lu)."""

    def __init__(self, mid):  # noqa: D107
        self.id = mid


class _FakeClient(BetclicMarketClient):
    """Client sans réseau : sert des pages prédéfinies."""

    def __init__(self, pages):  # noqa: D107 - pas d'appel au constructeur parent
        self.pages = pages
        self.calls: list[int] = []
        self.market = "CI"

    def get_matches(self, sport: str = "football", offset: int = 0) -> dict:  # noqa: D102
        self.calls.append(offset)
        page = self.pages.get(offset, [])
        return {"matches": page, "total": 0}

    def _to_dict(self, match, sport: str) -> dict:  # noqa: D102
        return {"id": match.id, "date": "2026-09-17T10:00:00.0000000Z"}


def test_collect_all_matches_pagine_jusqu_a_la_fin():
    client = _FakeClient({0: [_FakeMatch(i) for i in range(40)], 40: [_FakeMatch(i) for i in range(40, 47)], 47: []})
    matches = client.collect_all_matches("football")
    assert client.calls == [0, 40, 47]  # l'offset avance du nombre réel d'entrées reçues
    assert len(matches) == 47


def test_collect_all_matches_deduplique_les_matchs_deja_vus():
    page = [_FakeMatch(i) for i in range(40)]
    client = _FakeClient({0: page, 40: page[:10], 50: []})  # seconde page déjà connue
    matches = client.collect_all_matches("football")
    assert client.calls == [0, 40, 50]
    assert len(matches) == 40


def test_collect_all_matches_respecte_le_plafond_de_pages():
    client = _FakeClient(
        {offset: [_FakeMatch(offset + i) for i in range(40)] for offset in (0, 40, 80, 120)}
    )
    matches = client.collect_all_matches("football", max_pages=2)
    assert client.calls == [0, 40]
    assert len(matches) == 80


def test_collect_all_matches_s_arrete_apres_le_jour_cible():
    """Sortir du jour visé pendant N pages consécutives → fin de collecte."""
    client = _FakeClient({offset: [_FakeMatch(offset + i) for i in range(40)] for offset in (0, 40, 80, 120)})
    client._to_dict = lambda match, sport: {"id": match.id, "date": "2026-09-20T10:00:00.0000000Z"}
    matches = client.collect_all_matches("football", until_day="2026-09-17", idle_pages=3)
    assert client.calls == [0, 40, 80]  # 3 pages hors jour → arrêt avant la 4e
    assert len(matches) == 120


def test_collect_all_matches_ne_s_arrete_pas_tant_que_le_jour_est_present():
    client = _FakeClient({offset: [_FakeMatch(offset + i) for i in range(40)] for offset in (0, 40)})
    client._to_dict = lambda match, sport: {"id": match.id, "date": "2026-09-17T10:00:00.0000000Z"}
    matches = client.collect_all_matches("football", until_day="2026-09-17", idle_pages=1)
    assert client.calls == [0, 40, 80]  # jour toujours présent → on va jusqu'à la page vide
    assert len(matches) == 80
