import math
from datetime import date

import pytest

from betclic_agent.engine.dataset import parse_matches
from betclic_agent.engine.markets import asian_handicap, derive_markets
from betclic_agent.engine.poisson import score_matrix
from betclic_agent.engine.teams import TeamIndex, normalize
from betclic_agent.engine.errors import InsufficientData
from betclic_agent.engine.poisson import fit

CASES = [(1.4, 1.1, -0.05), (2.6, 0.9, -0.1), (0.8, 0.7, 0.0)]


@pytest.mark.parametrize("lam, mu, rho", CASES)
def test_score_matrix_sums_to_one(lam, mu, rho):
    total = sum(sum(row) for row in score_matrix(lam, mu, rho))
    assert math.isclose(total, 1.0, abs_tol=1e-9)


@pytest.mark.parametrize("lam, mu, rho", CASES)
def test_markets_are_mutually_consistent(lam, mu, rho):
    m = derive_markets(lam, mu, rho)
    r = m["match_result"]
    assert math.isclose(r["home"] + r["draw"] + r["away"], 1.0, abs_tol=1e-9)
    assert math.isclose(m["double_chance"]["1X"], r["home"] + r["draw"], abs_tol=1e-9)
    assert math.isclose(m["draw_no_bet"]["home"] + m["draw_no_bet"]["away"], 1.0, abs_tol=1e-9)
    assert math.isclose(m["over_under"]["2.5"]["over"] + m["over_under"]["2.5"]["under"], 1.0, abs_tol=1e-9)
    assert math.isclose(m["team_goals"]["home"]["expected"], lam, rel_tol=1e-3)
    assert math.isclose(m["team_goals"]["away"]["expected"], mu, rel_tol=1e-3)
    zero_line = asian_handicap(score_matrix(lam, mu, rho), 0.0)
    assert math.isclose(zero_line["win"], r["home"], abs_tol=1e-9)
    assert math.isclose(zero_line["lose"], r["away"], abs_tol=1e-9)


def test_quarter_handicap_splits_between_two_lines():
    matrix = score_matrix(1.3, 1.0, -0.05)
    quarter = asian_handicap(matrix, -0.25)
    low = asian_handicap(matrix, -0.5)
    high = asian_handicap(matrix, 0.0)
    assert math.isclose(quarter["win"], (low["win"] + high["win"]) / 2, abs_tol=1e-9)
    assert math.isclose(quarter["push"], (low["push"] + high["push"]) / 2, abs_tol=1e-9)


def test_top_scores_sorted_and_most_probable_first():
    scores = derive_markets(1.5, 1.2, -0.05)["top_scores"]
    assert len(scores) == 5
    probs = [s["p"] for s in scores]
    assert probs == sorted(probs, reverse=True)


CSV = (
    "Division,MatchDate,HomeTeam,AwayTeam,FTHome,FTAway,HTHome,HTAway,HomeYellow,HomeRed,AwayYellow,AwayRed,"
    "HomeElo,AwayElo,HomeShots,AwayShots,HomeTarget,AwayTarget,HomeCorners,AwayCorners,OddHome,OddDraw,OddAway,"
    "Over25,Under25,HandiSize,HandiHome,HandiAway\n"
    "E0,2020-08-01,Arsenal,Chelsea,2,1,1,0,1,0,2,,1800,1750,,,,,,,2.1,3.4,3.6,,,,,\n"
    "E0,2020-07-01,Team A,Team B,,,,,,,,,,,,,,,,,,,,,,,,\n"
)


def test_parse_skips_unplayed_and_keeps_missing_as_none():
    records = parse_matches(CSV.splitlines(keepends=True))
    assert len(records) == 1
    rec = records[0]
    assert (rec.ft_home, rec.ft_away) == (2, 1)
    assert rec.home_shots is None
    assert rec.home_cards == 1.0
    assert rec.away_cards == 2.0
    assert rec.odd_home == 2.1


def test_team_normalisation_strips_accents_and_club_noise():
    assert normalize("Atlético Madrid FC") == "atletico madrid"


def test_team_index_resolves_close_names_and_refuses_unknown():
    idx = TeamIndex(["Real Madrid", "Bayern Munich"])
    assert idx.resolve("Real Madrid CF") == "Real Madrid"
    assert idx.resolve("Nowhere FC") is None


def test_fit_refuses_to_guess_without_history():
    with pytest.raises(InsufficientData):
        fit([], date(2026, 1, 1))
