import math
from datetime import date

import pytest

from betclic_agent.engine.errors import UnknownTeam
from betclic_agent.engine.secondary import derive, fit as fit_stats, poisson_over
from betclic_agent.engine.teams import TeamIndex
from betclic_agent.engine.value import Selection, evaluate, expected_value, rank
from betclic_agent.engine.dataset import MatchRecord


def _sel(win, lose, odds, n=80, push=0.0):
    implied = 1 / odds if odds else None
    return Selection("1X2", "Domicile", win, lose, push, odds, implied, n)


def test_expected_value_matches_formula():
    p, odds = 0.55, 2.0
    assert math.isclose(expected_value(p, 1 - p, odds), p * odds - 1)


def test_push_returns_stake_in_expected_value():
    assert math.isclose(expected_value(0.4, 0.3, 2.0), 0.4 * 1.0 - 0.3)


def test_evaluate_without_odds_stays_unavailable():
    out = evaluate(_sel(0.5, 0.5, None))
    assert out["available"] is False


def test_rank_excludes_negative_and_unsure_edges():
    good = evaluate(_sel(0.6, 0.4, 2.2, n=200))
    bad = evaluate(_sel(0.4, 0.6, 2.0, n=200))
    thin = evaluate(_sel(0.9, 0.1, 1.5, n=3))
    picks = rank([good, bad, thin])
    assert good in picks
    assert bad not in picks
    assert thin not in picks


def test_rank_returns_empty_list_when_nothing_qualifies():
    assert rank([evaluate(_sel(0.3, 0.7, 2.0, n=100))]) == []


def test_poisson_over_under_sum_to_one():
    out = poisson_over(9.0, 8.5)
    assert math.isclose(out["over"] + out["under"], 1.0, abs_tol=1e-9)


def _stat_match(day, home, away, hc, ac):
    return MatchRecord(
        division="E0", date=day, home=home, away=away, home_elo=None, away_elo=None,
        ft_home=1, ft_away=0, ht_home=None, ht_away=None,
        home_shots=None, away_shots=None, home_target=None, away_target=None,
        home_corners=hc, away_corners=ac, home_cards=None, away_cards=None,
        odd_home=None, odd_draw=None, odd_away=None, over25=None, under25=None,
        handicap_size=None, handicap_home=None, handicap_away=None,
    )


def test_stat_model_omits_statistic_without_data_instead_of_zeros():
    rows = [_stat_match(date(2025, 1, 1), "A", "B", None, None)] * 40
    assert "corners" not in fit_stats(rows, date(2026, 1, 1))


def test_stat_model_ignores_future_matches():
    rows = [_stat_match(date(2024, 1, d % 28 + 1), "A", "B", 6, 4) for d in range(40)]
    rows.append(_stat_match(date(2027, 1, 1), "A", "B", 99, 99))
    model = fit_stats(rows, date(2026, 1, 1))["corners"]
    assert model.league["corners"] < 20


def test_stat_derive_unknown_team_raises():
    rows = [_stat_match(date(2024, 1, d % 28 + 1), "A", "B", 6, 4) for d in range(40)]
    model = fit_stats(rows, date(2026, 1, 1))["corners"]
    with pytest.raises(UnknownTeam):
        derive(model, "corners", "A", "Z")


def test_alias_clubs_francais_betclic():
    from betclic_agent.engine.teams import TeamIndex

    idx = TeamIndex(["Marseille", "Lyon", "Rennes", "Lens", "Lille", "Brest", "Nice", "Reims"])
    assert idx.resolve("Olympique de Marseille") == "Marseille"
    assert idx.resolve("OM") == "Marseille"
    assert idx.resolve("Olympique Lyonnais") == "Lyon"
    assert idx.resolve("Stade Rennais") == "Rennes"
    assert idx.resolve("LOSC") == "Lille"
    assert idx.resolve("RC Lens") == "Lens"


def test_team_index_alias_resolves_known_pair():
    idx = TeamIndex(["Man City", "Paris SG"])
    assert idx.resolve("Manchester City") == "Man City"
    assert idx.resolve("Paris Saint-Germain") == "Paris SG"
