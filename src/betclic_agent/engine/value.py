"""Moteur de value bets : EV, cote juste, intervalle d'incertitude, qualité, classement.

EV = p × cote − 1 (sans push). Avec push (lignes asiatiques), le pari rendu ne gagne ni ne perd :
EV = gagne × (cote − 1) − perd.
La confiance mesure la fiabilité de l'estimation (taille d'échantillon, largeur de l'intervalle),
jamais la probabilité de gagner. Le classement privilégie la borne basse de l'EV.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Z_95 = 1.96
FULL_QUALITY_SAMPLE = 60


@dataclass(frozen=True)
class Selection:
    market: str
    label: str
    win: float
    lose: float
    push: float
    odds: float | None
    implied: float | None
    n_effective: int


def expected_value(win: float, lose: float, odds: float) -> float:
    return win * (odds - 1.0) - lose


def interval(p: float, n: int) -> tuple[float, float]:
    """Intervalle normal de la probabilité estimée, à taille d'échantillon effective n."""
    if n <= 0:
        return 0.0, 1.0
    half = Z_95 * math.sqrt(max(p * (1 - p), 1e-9) / n)
    return max(0.0, p - half), min(1.0, p + half)


def data_quality(n: int) -> str:
    if n >= FULL_QUALITY_SAMPLE:
        return "élevée"
    if n >= 20:
        return "moyenne"
    return "faible"


def confidence(n: int, width: float) -> float:
    """Score 0..1 de fiabilité de l'estimation, indépendant de la probabilité de gagner."""
    sample = min(n / FULL_QUALITY_SAMPLE, 1.0)
    precision = max(0.0, 1.0 - width / 0.4)
    return round(sample * precision, 3)


def evaluate(sel: Selection) -> dict:
    p = sel.win / (sel.win + sel.lose) if sel.win + sel.lose > 0 else None
    if p is None or sel.odds is None:
        return {"market": sel.market, "label": sel.label, "available": False,
                "reason": "cote Betclic absente" if sel.odds is None else "probabilité indéfinie"}
    ev = expected_value(sel.win, sel.lose, sel.odds)
    low, high = interval(p, sel.n_effective)
    ev_low = expected_value(low, 1 - low, sel.odds)
    ev_high = expected_value(high, 1 - high, sel.odds)
    width = high - low
    return {
        "market": sel.market,
        "label": sel.label,
        "available": True,
        "betclic_odds": sel.odds,
        "implied_probability": sel.implied,
        "model_probability": p,
        "fair_odds": 1 / p if p > 0 else None,
        "expected_value": ev,
        "ev_interval": [ev_low, ev_high],
        "probability_interval": [low, high],
        "confidence": confidence(sel.n_effective, width),
        "data_quality": data_quality(sel.n_effective),
        "rank_key": ev_low,
    }


def rank(evaluations: list[dict], min_quality: str = "faible") -> list[dict]:
    """Opportunités à EV positive, triées par borne basse de l'EV puis fiabilité.

    Une EV basse ou négative n'est jamais recommandée. Rien n'est forcé : liste possiblement vide.
    """
    order = {"faible": 0, "moyenne": 1, "élevée": 2}
    floor = order[min_quality]
    picks = [
        e for e in evaluations
        if e.get("available") and e["expected_value"] > 0 and e["ev_interval"][0] > 0
        and order[e["data_quality"]] >= floor
    ]
    return sorted(picks, key=lambda e: (e["rank_key"], e["confidence"]), reverse=True)
