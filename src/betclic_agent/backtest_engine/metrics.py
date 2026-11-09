"""Métriques de backtest : calibration, ROI, drawdown, CLV, intervalle de confiance.

Convention : ``Bet.stake`` est la mise (1.0 = mise unitaire), ``Bet.odds`` la cote prise,
``Bet.won`` le résultat. Les fonctions ne renvoient jamais de valeur inventée : une
métrique sans donnée suffisante renvoie ``None``.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Bet:
    placed_at: datetime
    model_prob: float
    odds: float
    won: bool
    stake: float = 1.0
    closing_odds: float | None = None

    @property
    def profit(self) -> float:
        return self.stake * (self.odds - 1.0) if self.won else -self.stake


def brier_score(probs: Sequence[float], outcomes: Sequence[int]) -> float:
    """Moyenne de (p - issue)². Plus bas = mieux ; 0 = parfait."""
    _check_pairs(probs, outcomes)
    return sum((p - o) ** 2 for p, o in zip(probs, outcomes)) / len(probs)


def log_loss(probs: Sequence[float], outcomes: Sequence[int], eps: float = 1e-15) -> float:
    """Entropie croisée moyenne. Probabilités bornées à [eps, 1-eps]."""
    _check_pairs(probs, outcomes)
    total = 0.0
    for p, o in zip(probs, outcomes):
        p = min(max(p, eps), 1.0 - eps)
        total += -math.log(p) if o else -math.log(1.0 - p)
    return total / len(probs)


def calibration_curve(
    probs: Sequence[float], outcomes: Sequence[int], bins: int = 10
) -> list[dict[str, float]]:
    """Par tranche de probabilité : moyenne prédite, fréquence observée, effectif.

    Seules les tranches non vides sont renvoyées.
    """
    _check_pairs(probs, outcomes)
    buckets: list[list[tuple[float, int]]] = [[] for _ in range(bins)]
    for p, o in zip(probs, outcomes):
        index = min(int(p * bins), bins - 1)
        buckets[index].append((p, o))
    curve = []
    for bucket in buckets:
        if not bucket:
            continue
        n = len(bucket)
        curve.append(
            {
                "predicted": sum(p for p, _ in bucket) / n,
                "observed": sum(o for _, o in bucket) / n,
                "n": n,
            }
        )
    return curve


def roi(bets: Sequence[Bet]) -> float | None:
    """Profit total divisé par la mise totale."""
    staked = sum(b.stake for b in bets)
    if staked <= 0:
        return None
    return sum(b.profit for b in bets) / staked


def yield_per_bet(bets: Sequence[Bet]) -> float | None:
    """Profit moyen par pari, en unités de mise."""
    if not bets:
        return None
    return sum(b.profit for b in bets) / len(bets)


def max_drawdown(bets: Sequence[Bet]) -> float:
    """Plus forte baisse du profit cumulé (en unités), du plus haut au plus bas suivant."""
    ordered = sorted(bets, key=lambda b: b.placed_at)
    cumulative = 0.0
    peak = 0.0
    worst = 0.0
    for bet in ordered:
        cumulative += bet.profit
        peak = max(peak, cumulative)
        worst = max(worst, peak - cumulative)
    return worst


def closing_line_value(bets: Sequence[Bet]) -> dict[str, float | int] | None:
    """CLV moyenne : cote prise / cote de clôture - 1, sur les paris ayant une clôture.

    Renvoie None si aucun pari n'a de cote de clôture observée.
    """
    values = [b.odds / b.closing_odds - 1.0 for b in bets if b.closing_odds]
    if not values:
        return None
    return {"mean": sum(values) / len(values), "n": len(values)}


def bootstrap_roi_ci(
    bets: Sequence[Bet],
    n_boot: int = 2000,
    alpha: float = 0.05,
    seed: int = 0,
) -> tuple[float, float] | None:
    """Intervalle de confiance percentile (1 - alpha) du ROI par rééchantillonnage."""
    if not bets:
        return None
    rng = random.Random(seed)
    samples = []
    for _ in range(n_boot):
        draw = [bets[rng.randrange(len(bets))] for _ in bets]
        value = roi(draw)
        if value is not None:
            samples.append(value)
    if not samples:
        return None
    samples.sort()
    low = samples[int((alpha / 2) * (len(samples) - 1))]
    high = samples[int((1 - alpha / 2) * (len(samples) - 1))]
    return low, high


def _check_pairs(probs: Sequence[float], outcomes: Sequence[int]) -> None:
    if len(probs) != len(outcomes):
        raise ValueError("probs et outcomes de tailles différentes")
    if not probs:
        raise ValueError("aucune observation")
    if any(not 0.0 <= p <= 1.0 for p in probs):
        raise ValueError("probabilité hors de [0, 1]")
    if any(o not in (0, 1) for o in outcomes):
        raise ValueError("issue doit valoir 0 ou 1")
