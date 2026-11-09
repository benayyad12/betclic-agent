"""Tests du chargeur historique et du backtest (données synthétiques, sans réseau)."""

from __future__ import annotations

import pytest

from betclic_agent.backtest import (
    brier,
    elo_probs,
    flat_stake_profits,
    log_loss,
    market_probs,
    max_drawdown,
    season_of,
)
from betclic_agent.historical import parse_matches

HEADER = "Division,MatchDate,HomeTeam,AwayTeam,FTResult,OddHome,OddDraw,OddAway,HomeElo,AwayElo\n"


def test_parse_matches_drops_rows_without_complete_data():
    csv_text = HEADER + (
        "E0,2023-08-12,A,B,H,2.0,3.5,4.0,1600,1500\n"
        "E0,2023-08-13,C,D,H,,3.5,4.0,1600,1500\n"
        "E0,2023-08-14,E,F,X,2.0,3.5,4.0,1600,1500\n"
        "E0,2023-08-15,G,H,D,2.5,3.2,2.9,,1500\n"
    )
    matches = parse_matches(csv_text.splitlines(keepends=True))
    assert [m["home"] for m in matches] == ["A"]
    assert matches[0]["odds"] == (2.0, 3.5, 4.0)


def test_market_probs_removes_margin_and_sums_to_one():
    match = {"odds": (2.0, 3.5, 4.0)}
    probs = market_probs(match)
    assert sum(probs) == pytest.approx(1.0)
    assert probs[0] > probs[2]


def test_elo_probs_equal_teams_are_symmetric():
    home, draw, away = elo_probs({"home_elo": 1500, "away_elo": 1500})
    assert home == pytest.approx(away)
    assert home + draw + away == pytest.approx(1.0)


def test_season_of_uses_july_boundary():
    assert season_of("2023-06-30") == "2022/23"
    assert season_of("2023-07-01") == "2023/24"


def test_brier_and_log_loss_on_perfect_and_uniform_predictions():
    perfect = [(1.0, 0.0, 0.0)]
    assert brier(perfect, ["H"]) == pytest.approx(0.0)
    assert log_loss(perfect, ["H"]) == pytest.approx(0.0, abs=1e-9)
    uniform = [(1 / 3, 1 / 3, 1 / 3)]
    assert log_loss(uniform, ["A"]) == pytest.approx(1.0986, rel=1e-3)


def test_flat_stake_profits_bets_only_above_threshold():
    matches = [
        {"odds": (2.0, 3.0, 4.0), "result": "H"},
        {"odds": (2.0, 3.0, 4.0), "result": "H"},
    ]
    probs = [(0.6, 0.2, 0.2), (0.1, 0.1, 0.8)]
    # Pari 1 : EV = 0.6*2-1 = 0.2 (gagné). Pari 2 : EV(A) = 0.8*4-1 = 2.2 (perdu).
    assert flat_stake_profits(matches, probs, threshold=0.05) == [1.0, -1.0]
    assert flat_stake_profits(matches, probs, threshold=5.0) == []


def test_max_drawdown_measures_worst_fall_from_peak():
    assert max_drawdown([1, 1, -1, -1, -1, 2]) == pytest.approx(-3.0)
    assert max_drawdown([1, 2]) == 0.0
