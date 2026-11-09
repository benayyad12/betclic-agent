"""Analyse fine d'un match : probabilités dé-margées et cohérence entre marchés.

Le dé-margement consiste à retirer la marge du bookmaker en normalisant les
probabilités implicites (méthode proportionnelle, la plus simple). Elle suppose que la
marge est répartie uniformément — approximation acceptable sur un marché à 2-3 issues,
beaucoup plus grossière sur un score exact (26 issues, ~25-29 % de marge).

L'intérêt est ailleurs : comparer les agrégats d'un marché secondaire (score exact) aux
marchés principaux (1X2, BTTS) révèle si le bookmaker se contredit — et donc s'il existe
une incohérence interne exploitable.
"""

from __future__ import annotations

import re

from .devig import proportional

SCORE_PATTERN = re.compile(r"^(\d+)\s*-\s*(\d+)$")


def implied_probability(odds: float) -> float:
    """Probabilité implicite brute d'une cote décimale."""
    return 1.0 / odds if odds > 1.0 else 0.0


def demargin(selections: list[dict]) -> dict[str, float]:
    """Probabilités implicites normalisées (marge retirée proportionnellement)."""
    raw = [implied_probability(s["odds"]) for s in selections]
    if sum(raw) <= 0:
        return {}
    return {s["name"]: p for s, p in zip(selections, proportional(raw))}


def parse_score(name: str) -> tuple[int, int] | None:
    """Convertit « 2 - 1 » en (2, 1) ; None si le libellé n'est pas un score."""
    match = SCORE_PATTERN.match(name.strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def score_aggregates(selections: list[dict]) -> dict[str, float] | None:
    """Agrège un marché « score exact » dé-margé en issues classiques.

    Returns:
        ``{"home", "draw", "away", "btts_yes", "over_2_5", "scores"}`` ou None si le
        marché ne contient aucun score exploitable.
    """
    scores = []
    for selection in selections:
        parsed = parse_score(selection["name"])
        if parsed:
            scores.append((parsed, selection["odds"]))
    if not scores:
        return None

    normalized = demargin([{"name": f"{home}-{away}", "odds": odds} for (home, away), odds in scores])
    total = {"home": 0.0, "draw": 0.0, "away": 0.0, "btts_yes": 0.0, "over_2_5": 0.0}
    for score, probability in normalized.items():
        home, away = (int(part) for part in score.split("-"))
        if home > away:
            total["home"] += probability
        elif home == away:
            total["draw"] += probability
        else:
            total["away"] += probability
        if home > 0 and away > 0:
            total["btts_yes"] += probability
        if home + away > 2.5:
            total["over_2_5"] += probability

    total["scores"] = normalized  # type: ignore[assignment]
    return total
