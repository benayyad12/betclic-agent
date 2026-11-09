"""Validation walk-forward du moteur (Phase 7).

Pour chaque saison de test, le modèle est ajusté uniquement sur les matchs antérieurs à la
date de coupure (1er juillet), puis évalué sur la saison suivante. Aucune donnée future
n'entre dans l'entraînement ; ``assert_no_leakage`` le vérifie explicitement.

Les cotes de référence sont celles du jeu historique (pré-match, bookmaker de référence),
pas les cotes Betclic. La CLV exige des cotes de clôture : elle est signalée absente.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from ..backtest_engine.metrics import Bet, bootstrap_roi_ci, brier_score, log_loss, max_drawdown, roi
from .dataset import MatchRecord, load_matches
from .markets import derive_markets
from .poisson import fit

CUTOFF_MONTH, CUTOFF_DAY = 7, 1
MIN_EV = 0.0
DEFAULT_TEST_YEARS = (2022, 2023, 2024, 2025)
REPORT_PATH = Path(__file__).resolve().parents[3] / "data" / "validation_report.json"


@dataclass(frozen=True)
class Window:
    cutoff: date
    end: date


def windows(years) -> list[Window]:
    return [Window(date(y, CUTOFF_MONTH, CUTOFF_DAY), date(y + 1, CUTOFF_MONTH, CUTOFF_DAY)) for y in years]


def assert_no_leakage(train: list[MatchRecord], test: list[MatchRecord], cutoff: date) -> None:
    if train and max(m.date for m in train) >= cutoff:
        raise AssertionError("fuite : données d'entraînement postérieures à la coupure")
    if test and min(m.date for m in test) < cutoff:
        raise AssertionError("fuite : match de test antérieur à la coupure")


def _proportional_probs(m: MatchRecord):
    if not (m.odd_home and m.odd_draw and m.odd_away):
        return None
    raw = [1 / m.odd_home, 1 / m.odd_draw, 1 / m.odd_away]
    total = sum(raw)
    return [p / total for p in raw]


def _outcome(m: MatchRecord) -> int:
    if m.ft_home > m.ft_away:
        return 0
    return 1 if m.ft_home == m.ft_away else 2


def evaluate_window(matches: list[MatchRecord], window: Window) -> dict:
    train = [m for m in matches if m.date < window.cutoff]
    test = [m for m in matches if window.cutoff <= m.date < window.end and m.odd_home]
    assert_no_leakage(train, test, window.cutoff)
    model = fit(train, window.cutoff)

    model_probs, market_probs, outcomes, bets = [], [], [], []
    calib = {}
    for m in test:
        if m.home not in model.ratings or m.away not in model.ratings:
            continue
        lam, mu = model.expected_goals(m.home, m.away)
        markets = derive_markets(lam, mu, model.rho, model.ht_share)
        p = markets["match_result"]
        probs = [p["home"], p["draw"], p["away"]]
        market = _proportional_probs(m)
        if market is None:
            continue
        actual = _outcome(m)
        model_probs.append(probs)
        market_probs.append(market)
        outcomes.append(actual)

        odds = [m.odd_home, m.odd_draw, m.odd_away]
        best_edge, best_idx = max(
            ((probs[i] * odds[i] - 1.0, i) for i in range(3)), key=lambda t: t[0]
        )
        if best_edge > MIN_EV:
            bets.append(Bet(
                placed_at=_as_datetime(m.date),
                model_prob=probs[best_idx],
                odds=odds[best_idx],
                won=actual == best_idx,
            ))
        bucket = min(int(probs[0] * 5), 4)
        calib.setdefault(bucket, []).append((probs[0], 1 if actual == 0 else 0))

    return {
        "window": {"cutoff": window.cutoff.isoformat(), "end": window.end.isoformat()},
        "train_matches": len(train),
        "test_matches": len(outcomes),
        "rho": model.rho,
        "model": _score(model_probs, outcomes),
        "bookmaker_demargined": _score(market_probs, outcomes),
        "bets": {
            "count": len(bets),
            "roi": roi(bets),
            "max_drawdown_units": max_drawdown(bets) if bets else None,
            "roi_ci95": bootstrap_roi_ci(bets) if bets else None,
        },
        "clv": None,
        "calibration_home_win": _calibration(calib),
        "_bets": bets,
    }


def _as_datetime(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def _score(probs, outcomes) -> dict:
    if not probs:
        return {"brier": None, "log_loss": None, "accuracy": None}
    flat_p = [x for row in probs for x in row]
    flat_o = [1 if i == o else 0 for row, o in zip(probs, outcomes) for i in range(3)]
    picks = [max(range(3), key=lambda i: row[i]) for row in probs]
    accuracy = sum(1 for pk, o in zip(picks, outcomes) if pk == o) / len(outcomes)
    return {
        "brier": brier_score(flat_p, flat_o),
        "log_loss": log_loss(flat_p, flat_o),
        "accuracy": accuracy,
    }


def _calibration(bins: dict) -> list[dict]:
    out = []
    for bucket in sorted(bins):
        items = bins[bucket]
        out.append({
            "bin": f"{bucket * 20}-{(bucket + 1) * 20} %",
            "n": len(items),
            "mean_predicted": sum(p for p, _ in items) / len(items),
            "observed": sum(o for _, o in items) / len(items),
        })
    return out


def run(years=DEFAULT_TEST_YEARS, matches: list[MatchRecord] | None = None, report_path: Path = REPORT_PATH) -> dict:
    matches = matches if matches is not None else load_matches()
    results = [evaluate_window(matches, w) for w in windows(years)]
    all_bets = [b for r in results for b in r.pop("_bets")]
    ci = bootstrap_roi_ci(all_bets) if all_bets else None
    summary = {
        "bets": {
            "count": len(all_bets),
            "roi": roi(all_bets),
            "yield_per_bet": (sum(b.profit for b in all_bets) / len(all_bets)) if all_bets else None,
            "max_drawdown_units": max_drawdown(all_bets) if all_bets else None,
            "roi_ci95": ci,
        },
        "clv": None,
        "profitability_claim": (
            "intervalle à 95 % positif : à confirmer hors échantillon"
            if ci and ci[0] > 0 else "non démontré"
        ),
    }
    report = {"years": list(years), "windows": results, "summary": summary,
              "note": "cotes de référence = jeu historique ; CLV absente (pas de clôture Betclic)."}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def load_report(path: Path = REPORT_PATH) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
