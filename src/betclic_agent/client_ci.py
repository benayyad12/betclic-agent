"""Client Betclic ciblant le marché ivoirien (ou tout autre régulateur).

S'appuie sur le protocole gRPC-web public découvert et validé le 17/09/2026 :
le backend `offering.begmedia.com` sert plusieurs marchés (FR, CI, SN…) et se
distingue uniquement par les en-têtes `x-bg-regulation` + Origin/Referer.

Usage :
    from betclic_agent.client_ci import BetclicMarketClient
    client = BetclicMarketClient(market="CI")
    result = client.get_matches("football")
"""

from __future__ import annotations

import time

from betclic_api import BetclicClient
from betclic_api.client import SPORTS
from betclic_api.parser import parse_matches_response
from betclic_api.proto import encode_field_string, encode_field_varint

from .config import Settings

# Le backend renvoie des pages de taille fixe (40) : la pagination se fait par offset.
PAGE_SIZE = 40
# Le serveur ne ferme jamais le flux : chaque page coûte ~15 s (timeout de lecture).
# On borne donc le nombre d'appels pour ne jamais partir en boucle infinie.
MAX_PAGES = 30

# En-têtes qui déterminent le marché servi par le backend
REGULATION_HEADERS = {
    "x-bg-regulation": "{}",
    "x-bg-ref-regulator-zone": "{}",
}


class BetclicMarketClient(BetclicClient):
    """Client Betclic paramétré sur un marché donné (CI par défaut)."""

    def __init__(self, market: str = "CI", settings: Settings | None = None):
        cfg = settings or Settings()
        super().__init__(
            locale="fr",
            timeout=(cfg.timeout_connect, cfg.timeout_read),
        )
        self.market = market.upper()
        origin = {
            "CI": "https://www.betclic.ci",
            "FR": "https://www.betclic.fr",
            "SN": "https://www.betclic.sn",
        }.get(self.market, "https://www.betclic.ci")
        self.origin = origin
        self._session.headers.update(
            {
                "x-bg-regulation": self.market,
                "x-bg-ref-regulator-zone": self.market,
                "Origin": origin,
                "Referer": origin + "/",
            }
        )

    # ─── Helpers métier ──────────────────────────────────────────────────────

    def get_matches_page(self, sport: str, offset: int = 0, size: int = PAGE_SIZE) -> dict:
        """Comme ``get_matches`` mais avec une taille de page demandée (le serveur peut la plafonner)."""
        payload = (
            encode_field_string(1, SPORTS.get(sport, sport))
            + encode_field_string(2, self.locale)
            + encode_field_varint(4, offset)
            + encode_field_varint(5, size)
        )
        frames = self._post(
            "offering.access.api.MatchService",
            "GetMatchesBySportWithNotifications",
            payload,
        )
        return parse_matches_response(frames)

    def get_sports_names(self) -> list[str]:
        """Retourne la liste des sports supportés par le client."""
        return sorted(SPORTS.keys())

    def _to_dict(self, match, sport: str) -> dict:
        """Convertit un ``Match`` de la lib en dict simplifié.

        ``live_stream`` reprend l'indicateur ``is_live`` de la lib, dont le nom est
        trompeur : il NE signifie PAS que le match est en cours (vérifié le
        17/09/2026 — 18 matchs marqués ainsi avaient un coup d'envoi à venir).
        Le statut réel se déduit de l'heure via ``timing.match_status``.
        """
        return {
            "id": match.id,
            "name": match.name,
            "competition": match.competition,
            "date": match.date,
            "live_stream": bool(match.is_live),
            "sport": sport,
            "market": self.market,
        }

    def collect_matches(self, sport: str = "football", limit: int = 40) -> list[dict]:
        """Retourne une liste simplifiée de matchs (dicts) prêts à être traités."""
        result = self.get_matches(sport)
        matches = result.get("matches", [])[:limit]
        return [self._to_dict(m, sport) for m in matches]

    def collect_all_matches(
        self,
        sport: str = "football",
        max_pages: int = MAX_PAGES,
        delay_s: float = 0.0,
        until_day: str | None = None,
        idle_pages: int = 3,
        on_page=None,
    ) -> list[dict]:
        """Pagine jusqu'à épuisement et retourne TOUS les matchs du sport.

        Chaque appel dure ~15 s (le backend ne ferme pas le flux) : ``max_pages``
        borne le coût. On s'arrête dès qu'une page n'apporte aucun match nouveau
        (fin de liste) ou que ``total`` est atteint.

        Args:
            until_day: jour cible ``AAAA-MM-JJ``. Le backend mélange les jours et
                ne garantit pas l'ordre : on s'arrête après ``idle_pages`` pages
                consécutives sans aucun match de ce jour (ou antérieur).
            on_page: callback optionnel ``(page_num, cumul, taille_page)`` pour le suivi.
        """
        matches: list[dict] = []
        seen: set = set()
        offset = 0
        idle = 0
        for page_num in range(1, max_pages + 1):
            result = self.get_matches(sport, offset=offset)
            page = result.get("matches", [])
            if not page:
                break
            fresh = [self._to_dict(m, sport) for m in page if m.id not in seen]
            seen.update(m["id"] for m in fresh)
            matches.extend(fresh)
            if on_page:
                on_page(page_num, len(matches), len(page))
            total = result.get("total") or 0
            offset += len(page)
            if total and len(matches) >= total:
                break
            if until_day:
                touched = any((m["date"] or "")[:10] <= until_day for m in fresh)
                idle = 0 if touched else idle + 1
                if idle >= idle_pages:
                    break
            if delay_s:
                time.sleep(delay_s)
        return matches

    @staticmethod
    def matches_on(matches: list[dict], day: str) -> list[dict]:
        """Filtre les matchs d'une journée (``day`` au format AAAA-MM-JJ, en UTC).

        Les dates du backend sont en UTC ; la Côte d'Ivoire étant à UTC+0 toute
        l'année, la date UTC correspond à la date locale.
        """
        return [m for m in matches if (m.get("date") or "")[:10] == day]

    @staticmethod
    def sort_by_time(matches: list[dict]) -> list[dict]:
        """Trie les matchs par heure de coup d'envoi puis par compétition."""
        return sorted(matches, key=lambda m: (m.get("date") or "", m.get("competition") or ""))

    def get_main_markets(self, match_id: int) -> dict[str, list[dict]]:
        """Récupère les marchés d'un match sous forme {nom_marche: [sélections]}."""
        match = self.get_match(match_id)
        return {
            market.name: [
                {"name": sel.name, "odds": sel.odds}
                for sel in market.selections
            ]
            for market in match.markets
        }
