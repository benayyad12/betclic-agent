"""Validation des réponses et collecteur (client factice, aucun appel réseau)."""

import logging

import pytest
from sqlalchemy import func, select

from betclic_agent.collector import collect_once
from betclic_agent.db.models import CollectionRun, OddsSnapshot
from betclic_agent.db.repository import create_schema, make_engine, make_session_factory
from betclic_agent.validation import ValidationError, validate_markets, validate_match_ids

GOOD = {
    "résultat du match (t. rég)": [
        {"name": "A", "odds": 2.0},
        {"name": "Nul", "odds": 3.5},
        {"name": "B", "odds": 3.8},
    ]
}


@pytest.mark.parametrize(
    "markets",
    [
        {},  # réponse vide
        {"résultat du match": []},  # marché sans sélection
        {"résultat du match": [{"name": "A", "odds": 2.0}, {"name": "B", "odds": 3.0}]},  # 1X2 tronqué
        {"x": [{"name": "A", "odds": float("nan")}]},  # cote non finie
        {"x": [{"name": "A", "odds": 0.9}]},  # cote <= 1
        {"x": [{"name": "", "odds": 2.0}]},  # sélection sans libellé
    ],
)
def test_reponses_suspectes_sont_refusees(markets):
    with pytest.raises(ValidationError):
        validate_markets(1, markets)


def test_reponse_correcte_est_acceptee():
    assert validate_markets(1, GOOD) == GOOD


def test_identifiants_dupliques_refuses():
    with pytest.raises(ValidationError):
        validate_match_ids([{"id": 1}, {"id": 1}])


class FakeClient:
    market = "CI"

    def __init__(self, markets_by_id, matches):
        self._markets = markets_by_id
        self._matches = matches

    def collect_matches(self, sport, limit):
        return self._matches

    def get_main_markets(self, match_id):
        result = self._markets[match_id]
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def factory():
    engine = make_engine("sqlite://")
    create_schema(engine)
    return make_session_factory(engine)


def _match(i):
    return {"id": i, "name": f"Equipe {i} - Autre", "competition": "L1", "date": "2026-11-10T19:00:00.0000000Z"}


def test_collecte_ignore_les_reponses_refusees_et_respecte_le_delai(factory, caplog):
    client = FakeClient(
        {1: GOOD, 2: {}, 3: RuntimeError("timeout")},
        [_match(1), _match(2), _match(3)],
    )
    delays = []
    with caplog.at_level(logging.WARNING):
        result = collect_once(client, factory, delay_s=1.5, sleep=delays.append)

    assert result["status"] == "ok"
    assert result["matches_seen"] == 1
    assert result["rejected"] == 2
    assert delays == [1.5, 1.5, 1.5]  # une pause par match, y compris les refusés
    assert "réponse refusée" in caplog.text
    assert "timeout" in caplog.text  # l'erreur est journalisée, pas avalée

    with factory() as s:
        assert s.scalar(select(func.count()).select_from(OddsSnapshot)) == 3
        run = s.get(CollectionRun, result["run_id"])
        assert run.status == "ok" and run.finished_at is not None


def test_echec_de_la_liste_marque_le_run_failed(factory):
    class Broken(FakeClient):
        def collect_matches(self, sport, limit):
            raise ConnectionError("dns")

    result = collect_once(Broken({}, []), factory, sleep=lambda _: None)
    assert result["status"] == "failed"
    with factory() as s:
        assert "dns" in s.get(CollectionRun, result["run_id"]).error
