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


def normalize(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii").lower()
    tokens = re.findall(r"[a-z0-9]+", ascii_only)
    kept = [t for t in tokens if t not in NOISE]
    return " ".join(kept or tokens)


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
        if key in self._by_norm:
            return self._by_norm[key]
        close = difflib.get_close_matches(key, self._by_norm.keys(), n=1, cutoff=CUTOFF)
        return self._by_norm[close[0]] if close else None
