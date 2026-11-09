"""Tests de staking : formules de Kelly et plafonds sur des cas à résultat connu."""

import pytest

from betclic_agent.staking import (
    flat_stake,
    fractional_kelly_stake,
    kelly_fraction,
    ruin_simulation,
)


def test_flat_stake_fraction_fixe_de_la_bankroll():
    assert flat_stake(1000.0, unit_pct=0.02) == pytest.approx(20.0)


def test_kelly_cas_connu():
    # p = 0.5, cote 2.2 : EV = 0.1, f* = 0.1 / 1.2
    assert kelly_fraction(0.5, 2.2) == pytest.approx(0.1 / 1.2)


def test_kelly_esperance_negative_donne_zero():
    assert kelly_fraction(0.4, 2.0) == 0.0


def test_kelly_fractionne_sans_plafond_atteint():
    stake = fractional_kelly_stake(1000.0, 0.5, 2.2, fraction=0.25, cap_pct=0.5)
    assert stake == pytest.approx(1000.0 * 0.25 * (0.1 / 1.2))


def test_plafond_limite_la_mise():
    # quart de Kelly ≈ 2.08 % ; plafond 1 % doit s'appliquer
    stake = fractional_kelly_stake(1000.0, 0.5, 2.2, fraction=0.25, cap_pct=0.01)
    assert stake == pytest.approx(10.0)


def test_pas_de_mise_sans_avantage():
    assert fractional_kelly_stake(1000.0, 0.4, 2.0) == 0.0


@pytest.mark.parametrize("kwargs", [{"fraction": 0.0}, {"fraction": 1.5}, {"cap_pct": 0.0}])
def test_parametres_hors_bornes_refuses(kwargs):
    with pytest.raises(ValueError):
        fractional_kelly_stake(1000.0, 0.5, 2.2, **kwargs)


def test_monte_carlo_sans_avantage_ne_mise_jamais():
    result = ruin_simulation(prob=0.4, odds=2.0, n_bets=200, n_sims=200, seed=3)
    assert result["ruin_probability"] == 0.0
    assert result["median_final"] == pytest.approx(100.0)


def test_monte_carlo_certitude_de_gagner_ne_ruine_jamais():
    result = ruin_simulation(prob=1.0, odds=2.0, n_bets=100, n_sims=100, seed=1)
    assert result["ruin_probability"] == 0.0
    assert result["median_final"] > 100.0


def test_monte_carlo_est_reproductible():
    a = ruin_simulation(prob=0.55, odds=2.0, n_bets=50, n_sims=300, seed=42)
    b = ruin_simulation(prob=0.55, odds=2.0, n_bets=50, n_sims=300, seed=42)
    assert a == b
