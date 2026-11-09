"""Rapprochement des noms d'équipes Betclic ↔ jeu historique.

Les libellés diffèrent (accents, « FC », abréviations). Un rapprochement flou est accepté
seulement au-dessus d'un seuil élevé ; sinon l'équipe est reconnue comme inconnue.
"""

from __future__ import annotations

import difflib
import re
import unicodedata

NOISE = {"fc", "cf", "afc", "sc", "ac", "as", "us", "ssc", "calcio", "club"}
CUTOFF = 0.85

# Libellés Betclic courants → libellés du jeu historique (xgabora / Football-Data).
ALIASES = {
    "manchester city": "Man City",
    "manchester united": "Man United",
    "paris saint germain": "Paris SG",
    "paris saint-germain": "Paris SG",
    "psg": "Paris SG",
    "atletico madrid": "Ath Madrid",
    "atletico de madrid": "Ath Madrid",
    "inter milan": "Inter",
    "olympique de marseille": "Marseille",
    "olympique marseille": "Marseille",
    "om": "Marseille",
    "olympique lyonnais": "Lyon",
    "olympique de lyon": "Lyon",
    "ol": "Lyon",
    "stade rennais": "Rennes",
    "stade rennais fc": "Rennes",
    "rc lens": "Lens",
    "racing club de lens": "Lens",
    "ogc nice": "Nice",
    "stade brestois": "Brest",
    "stade brestois 29": "Brest",
    "stade de reims": "Reims",
    "losc": "Lille",
    "losc lille": "Lille",
    "borussia dortmund": "Dortmund",
    "bayer leverkusen": "Leverkusen",
    "rb leipzig": "RB Leipzig",
    "rasenballsport leipzig": "RB Leipzig",
    "eintracht frankfurt": "Ein Frankfurt",
    "borussia monchengladbach": "M'gladbach",
    "monchengladbach": "M'gladbach",
    "werder bremen": "Werder Bremen",
    "athletic bilbao": "Ath Bilbao",
    "athletic club": "Ath Bilbao",
    "real sociedad": "Sociedad",
    "sporting cp": "Sp Lisbon",
    "sporting lisbon": "Sp Lisbon",
    "sporting lisboa": "Sp Lisbon",
    "sporting braga": "Sp Braga",
    "sc braga": "Sp Braga",
    "fc porto": "Porto",
}


def normalize(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii").lower()
    tokens = re.findall(r"[a-z0-9]+", ascii_only)
    kept = [t for t in tokens if t not in NOISE]
    return " ".join(kept or tokens)


ALIASES_NORM = {normalize(k): v for k, v in ALIASES.items()}


class TeamIndex:
    """Index normalisé des noms canoniques du jeu historique."""

    def __init__(self, canonical_names) -> None:
        self._by_norm: dict[str, str] = {}
        for name in canonical_names:
            self._by_norm.setdefault(normalize(name), name)

    def resolve(self, name: str) -> str | None:
        key = normalize(name)
        if not key:
            return None
        if key in ALIASES_NORM:
            target = ALIASES_NORM[key]
            return target if normalize(target) in self._by_norm else None
        if key in self._by_norm:
            return self._by_norm[key]
        close = difflib.get_close_matches(key, self._by_norm.keys(), n=1, cutoff=CUTOFF)
        return self._by_norm[close[0]] if close else None
