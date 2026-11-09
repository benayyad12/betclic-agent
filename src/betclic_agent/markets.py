"""Analyse des marchés : probabilités implicites, marge du bookmaker, anomalies.

La marge (overround) est le « frais » intégré par le bookmaker :
    marge = (somme des 1/cote sur toutes les issues) − 1

Une marge basse indique un bookmaker généreux sur ce marché → meilleure valeur
pour le parieur. Une marge très basse ou incohérente peut aussi signaler une
cote temporairement erronée.
"""

from __future__ import annotations


def implied_probability(odds: float) -> float:
    """Probabilité implicite d'une cote décimale (1 / cote)."""
    if odds <= 1.0:
        return 0.0
    return 1.0 / odds


# Marchés dont les sélections sont mutuellement exclusives ET couvrent tous les cas
# possibles (donc marge interprétable). Deux conditions obligatoires :
#   1. le type de marché est reconnu exhaustif ;
#   2. le nombre de sélections correspond exactement à la structure attendue.
# Sans ces garanties, la somme des probabilités implicites n'a pas de sens :
#   - marché partiel            → somme < 1 → « marge négative » trompeuse
#   - issues qui se recouvrent  → somme > 1 artificiellement (double chance : le nul
#     apparaît dans 2 des 3 issues → ~115 % ; « nombre total de buts » regroupe
#     plusieurs paires Over/Under → 10 sélections → ~390 %)
EXHAUSTIVE_MARKET_RULES: tuple[tuple[str, int], ...] = (
    ("résultat du match", 3),        # 1X2 : trois issues exclusives et complètes
    ("les 2 équipes marquent", 2),   # Oui / Non
)


def is_exhaustive_market(market_name: str, selections: list[dict]) -> bool:
    """Indique si la marge de ce marché est interprétable (issues exhaustives)."""
    lowered = market_name.lower()
    return any(
        pattern in lowered and len(selections) == expected
        for pattern, expected in EXHAUSTIVE_MARKET_RULES
    )


def market_margin(selections: list[dict], expected_outcomes: int | None = None) -> float | None:
    """Marge du bookmaker en pourcentage pour un marché.

    Args:
        selections: liste de dicts ``{"name": str, "odds": float}``.
        expected_outcomes: nombre d'issues attendues (2, 3…). Si fourni et que le
            nombre de sélections diffère, la marge n'est pas calculable → None.

    Returns:
        Marge en pourcentage (ex. 7.5 pour 7,5 %), ou None si non calculable.
    """
    if not selections:
        return None
    if expected_outcomes is not None and len(selections) != expected_outcomes:
        return None
    total = sum(implied_probability(s["odds"]) for s in selections)
    if total <= 0:
        return None
    return round((total - 1.0) * 100.0, 2)


def analyze_markets(markets: dict[str, list[dict]]) -> list[dict]:
    """Analyse tous les marchés d'un match et retourne leurs marges.

    ``reliable`` vaut True uniquement pour les marchés exhaustifs : la marge des
    autres n'a pas de sens (elle peut être négative sans que ce soit une anomalie).

    Returns:
        Liste de dicts ``{"market", "margin_pct", "outcomes", "reliable", "selections"}``,
        triée par marge croissante parmi les marchés fiables.
    """
    analyzed = []
    for name, selections in markets.items():
        analyzed.append(
            {
                "market": name,
                "margin_pct": market_margin(selections),
                "outcomes": len(selections),
                "reliable": is_exhaustive_market(name, selections),
                "selections": selections,
            }
        )
    return sorted(
        analyzed,
        key=lambda m: m["margin_pct"] if m["margin_pct"] is not None else 999.0,
    )


def find_opportunities(
    analyzed: list[dict],
    min_margin_pct: float = 6.0,
    max_margin_pct: float = 12.0,
) -> list[dict]:
    """Repère les marchés FIABLES dont la marge sort de la fourchette habituelle.

    - marge < min_margin_pct  → bookmaker généreux (valeur potentielle)
    - marge > max_margin_pct  → marché peu intéressant (à ignorer)

    Les marchés non exhaustifs sont ignorés : leur marge n'est pas interprétable.
    """
    return [
        m
        for m in analyzed
        if m.get("reliable")
        and m["margin_pct"] is not None
        and min_margin_pct <= m["margin_pct"] <= max_margin_pct
    ]
