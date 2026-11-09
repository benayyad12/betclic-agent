"""Backtest des modèles de référence sur les 1X2 historiques.

Modèles comparés (aucun paramètre n'est ajusté sur les résultats, donc pas de fuite) :
- ``market`` : probabilités implicites des cotes, dé-marginées proportionnellement ;
- ``elo``    : probabilité issue de la formule Elo, avec une part de nul fixe.

Métriques : Brier multi-classes, log loss, et ROI à mise fixe d'une règle de valeur
(pari uniquement si EV = p × cote − 1 dépasse un seuil), avec IC bootstrap et drawdown.

Limite : les cotes du jeu de données sont des cotes de référence de fin de marché, pas
des cotes Betclic accessibles au moment du pari. Le ROI est donc optimiste.
"""

from __future__ import annotations

import argparse
import math
import random
from collections import defaultdict
from pathlib import Path
from typing import Callable

from betclic_agent.historical import DEFAULT_DIR, download_matches, load_matches

OUTCOMES = ("H", "D", "A")
ELO_DRAW_SHARE = 0.27  # part de nul fixe, non ajustée sur les données


def market_probs(match: dict) -> tuple[float, float, float]:
    """Probabilités implicites dé-marginées (proportionnel) à partir des cotes 1X2."""
    raw = [1.0 / odd for odd in match["odds"]]
    total = sum(raw)
    return tuple(p / total for p in raw)  # type: ignore[return-value]


def elo_probs(match: dict, draw_share: float = ELO_DRAW_SHARE) -> tuple[float, float, float]:
    """Probabilité Elo : espérance de score 1/(1+10^(-Δ/400)), répartie avec un nul fixe."""
    diff = match["home_elo"] - match["away_elo"]
    expected_home = 1.0 / (1.0 + 10 ** (-diff / 400.0))
    return ((1 - draw_share) * expected_home, draw_share, (1 - draw_share) * (1 - expected_home))


MODELS: dict[str, Callable[[dict], tuple[float, float, float]]] = {
    "market": market_probs,
    "elo": elo_probs,
}


def season_of(date: str) -> str:
    """Saison européenne : juillet à juin (ex. 2023-08-12 → « 2023/24 »)."""
    year, month = int(date[:4]), int(date[5:7])
    start = year if month >= 7 else year - 1
    return f"{start}/{str(start + 1)[2:]}"


def brier(probs: list[tuple[float, float, float]], results: list[str]) -> float:
    total = 0.0
    for (ph, pd, pa), result in zip(probs, results):
        actual = (result == "H", result == "D", result == "A")
        total += sum((p - float(o)) ** 2 for p, o in zip((ph, pd, pa), actual))
    return total / len(results)


def log_loss(probs: list[tuple[float, float, float]], results: list[str]) -> float:
    total = 0.0
    for p, result in zip(probs, results):
        total -= math.log(min(max(p[OUTCOMES.index(result)], 1e-15), 1 - 1e-15))
    return total / len(results)


def value_bets(match: dict, probs: tuple[float, float, float], threshold: float) -> list[tuple[int, float]]:
    """Issues dont l'EV (p × cote − 1) dépasse le seuil. Renvoie (indice, EV)."""
    picks = []
    for index, (p, odd) in enumerate(zip(probs, match["odds"])):
        ev = p * odd - 1.0
        if ev > threshold:
            picks.append((index, ev))
    return picks


def flat_stake_profits(
    matches: list[dict],
    probs: list[tuple[float, float, float]],
    threshold: float,
) -> list[float]:
    """Profit par pari à mise fixe de 1 (une seule issue retenue par match : la meilleure EV)."""
    profits = []
    for match, p in zip(matches, probs):
        picks = value_bets(match, p, threshold)
        if not picks:
            continue
        index, _ = max(picks, key=lambda item: item[1])
        won = OUTCOMES[index] == match["result"]
        profits.append(match["odds"][index] - 1.0 if won else -1.0)
    return profits


def bootstrap_roi_ci(profits: list[float], n_resamples: int = 2000, seed: int = 0) -> tuple[float, float]:
    """IC 95 % du ROI moyen par pari (bootstrap percentile, graine fixe pour la reproductibilité)."""
    rng = random.Random(seed)
    n = len(profits)
    means = sorted(sum(rng.choice(profits) for _ in range(n)) / n for _ in range(n_resamples))
    return means[int(0.025 * n_resamples)], means[int(0.975 * n_resamples) - 1]


def max_drawdown(profits: list[float]) -> float:
    """Plus forte baisse du cumul de profit, en unités de mise."""
    peak = equity = worst = 0.0
    for profit in profits:
        equity += profit
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def evaluate(matches: list[dict], model: str, threshold: float = 0.05) -> dict:
    """Évalue un modèle, saison par saison puis globalement (walk-forward sans ajustement)."""
    probs_fn = MODELS[model]
    by_season: dict[str, list[dict]] = defaultdict(list)
    for match in matches:
        by_season[season_of(match["date"])].append(match)

    report = {}
    for season, season_matches in sorted(by_season.items()):
        probs = [probs_fn(m) for m in season_matches]
        results = [m["result"] for m in season_matches]
        profits = flat_stake_profits(season_matches, probs, threshold)
        report[season] = {
            "matches": len(season_matches),
            "brier": round(brier(probs, results), 4),
            "log_loss": round(log_loss(probs, results), 4),
            "bets": len(profits),
            "roi": round(sum(profits) / len(profits), 4) if profits else None,
        }
    probs = [probs_fn(m) for m in matches]
    results = [m["result"] for m in matches]
    profits = flat_stake_profits(matches, probs, threshold)
    overall = {
        "matches": len(matches),
        "brier": round(brier(probs, results), 4),
        "log_loss": round(log_loss(probs, results), 4),
        "bets": len(profits),
        "roi": round(sum(profits) / len(profits), 4) if profits else None,
        "roi_ci95": None,
        "max_drawdown": round(max_drawdown(profits), 2),
    }
    if len(profits) >= 30:
        low, high = bootstrap_roi_ci(profits)
        overall["roi_ci95"] = [round(low, 4), round(high, 4)]
    return {"seasons": report, "overall": overall}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backtest des modèles de référence (1X2).")
    parser.add_argument("--csv", type=Path, default=None, help="Matches.csv (téléchargé sinon)")
    parser.add_argument("--division", default=None, help="Filtrer une division, ex. E0, F1, SP1")
    parser.add_argument("--threshold", type=float, default=0.05, help="Seuil d'EV pour parier")
    args = parser.parse_args(argv)

    path = args.csv or download_matches(DEFAULT_DIR)
    matches = load_matches(path)
    if args.division:
        matches = [m for m in matches if m["division"] == args.division]
    print(f"{len(matches)} matchs exploitables")
    for model in MODELS:
        result = evaluate(matches, model, args.threshold)
        print(f"\n== {model} ==")
        print("overall:", result["overall"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
