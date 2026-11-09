"""Validation des réponses Betclic avant toute écriture en base.

Une réponse suspecte (vide, tronquée, marché incohérent) est refusée avec une
``ValidationError`` : l'appelant la journalise et ne l'enregistre pas.
"""

from __future__ import annotations

import math

from .markets import EXHAUSTIVE_MARKET_RULES

MAX_MARKETS_PER_MATCH = 500
MAX_ODDS = 1000.0


class ValidationError(ValueError):
    """Réponse refusée : elle ne doit pas être persistée."""


def validate_markets(match_id: int, markets: dict[str, list[dict]]) -> dict[str, list[dict]]:
    if not markets:
        raise ValidationError(f"match {match_id} : aucun marché (réponse vide)")
    if len(markets) > MAX_MARKETS_PER_MATCH:
        raise ValidationError(f"match {match_id} : {len(markets)} marchés, valeur aberrante")

    for name, selections in markets.items():
        if not name or not name.strip():
            raise ValidationError(f"match {match_id} : marché sans nom")
        if not selections:
            raise ValidationError(f"match {match_id} : marché « {name} » sans sélection (tronqué)")
        for selection in selections:
            odds = selection.get("odds")
            if not isinstance(odds, (int, float)) or not math.isfinite(odds):
                raise ValidationError(f"match {match_id} : cote invalide « {odds} » sur « {name} »")
            if not 1.0 < odds <= MAX_ODDS:
                raise ValidationError(f"match {match_id} : cote hors plage {odds} sur « {name} »")
            if not str(selection.get("name", "")).strip():
                raise ValidationError(f"match {match_id} : sélection sans libellé sur « {name} »")

        lowered = name.lower()
        for pattern, expected in EXHAUSTIVE_MARKET_RULES:
            if pattern in lowered and len(selections) != expected:
                raise ValidationError(
                    f"match {match_id} : « {name} » a {len(selections)} issues, {expected} attendues (tronqué)"
                )
    return markets


def validate_match_ids(matches: list[dict]) -> list[dict]:
    """Refuse une liste de matchs dont les identifiants sont absents ou dupliqués."""
    ids = [m.get("id") for m in matches]
    if any(not isinstance(i, int) or i <= 0 for i in ids):
        raise ValidationError("identifiant de match invalide dans la liste")
    if len(set(ids)) != len(ids):
        raise ValidationError("identifiants de match dupliqués dans la liste")
    return matches
