"""Tests du moteur de backtest : chaque métrique est vérifiée sur un cas à résultat connu."""

from datetime import datetime, timedelta

import pytest

from betclic_agent.backtest_engine.metrics import (
    Bet,
    bootstrap_roi_ci,
    brier_score,
    calibration_curve,
    closing_line_value,
    log_loss,
    max_drawdown,
    roi,
    yield_per_bet,
)
from betclic_agent.backtest_engine.walkforward import walk_forward_splits

T0 = datetime(2026, 1, 1)


def _bet(i, odds, won, **kw):
    return Bet(placed_at=T0 + timedelta(days=i), model_prob=0.5, odds=odds, won=won, **kw)


def test_brier_prevision_constante_0_5():
    assert brier_score([0.5, 0.5], [1, 0]) == pytest.approx(0.25)


def test_brier_prevision_parfaite_vaut_zero():
    assert brier_score([1.0, 0.0, 1.0], [1, 0, 1]) == 0.0


def test_log_loss_prevision_constante_0_5_vaut_ln2():
    assert log_loss([0.5] * 4, [1, 0, 1, 0]) == pytest.approx(0.6931471805599453)


def test_log_loss_borne_les_probabilites_extremes():
    # sans bornage, log(0) provoquerait une erreur ; une erreur franche reste très pénalisée
    assert log_loss([0.0], [1]) > 30


def test_calibration_curve_detecte_un_modele_bien_calibre():
    probs = [0.8] * 10
    outcomes = [1] * 8 + [0] * 2
    (bucket,) = calibration_curve(probs, outcomes)
    assert bucket["predicted"] == pytest.approx(0.8)
    assert bucket["observed"] == pytest.approx(0.8)
    assert bucket["n"] == 10


def test_calibration_curve_ignore_les_tranches_vides():
    curve = calibration_curve([0.1, 0.9], [0, 1], bins=10)
    assert len(curve) == 2


def test_roi_et_yield_sur_cas_connu():
    # cote 3 gagnante (+2), cote 2 perdante (-1) : profit +1 sur 2 unités misées
    bets = [_bet(0, 3.0, True), _bet(1, 2.0, False)]
    assert roi(bets) == pytest.approx(0.5)
    assert yield_per_bet(bets) == pytest.approx(0.5)


def test_roi_respecte_les_mises_variables():
    bets = [_bet(0, 2.0, True, stake=3.0), _bet(1, 2.0, False, stake=1.0)]
    # profit = 3 - 1 = 2 sur 4 misés
    assert roi(bets) == pytest.approx(0.5)


def test_roi_sans_mise_renvoie_none():
    assert roi([]) is None


def test_max_drawdown_cas_connu():
    # profits +1, -2, +1, -1, +3 → cumul 1, -1, 0, -1, 2 → creux -1 depuis le pic 1 = 2
    bets = [
        _bet(0, 2.0, True),  # +1
        _bet(1, 2.0, False, stake=2.0),  # -2
        _bet(2, 2.0, True),  # +1
        _bet(3, 2.0, False),  # -1
        _bet(4, 4.0, True),  # +3
    ]
    assert max_drawdown(bets) == pytest.approx(2.0)


def test_max_drawdown_sans_perte_vaut_zero():
    assert max_drawdown([_bet(0, 2.0, True), _bet(1, 2.0, True)]) == 0.0


def test_clv_moyenne_cote_prise_contre_cloture():
    bets = [_bet(0, 2.2, True, closing_odds=2.0), _bet(1, 1.8, False)]
    clv = closing_line_value(bets)
    assert clv["n"] == 1  # le pari sans cote de clôture est exclu, pas inventé
    assert clv["mean"] == pytest.approx(0.1)


def test_clv_sans_cloture_renvoie_none():
    assert closing_line_value([_bet(0, 2.0, True)]) is None


def test_intervalle_bootstrap_degenere_si_tous_les_paris_identiques():
    bets = [_bet(i, 2.0, True) for i in range(20)]  # ROI = +100 % à chaque rééchantillon
    assert bootstrap_roi_ci(bets, n_boot=200) == (pytest.approx(1.0), pytest.approx(1.0))


def test_intervalle_bootstrap_encadre_le_roi_observe():
    bets = [_bet(i, 2.0, i % 2 == 0) for i in range(100)]  # ROI observé = 0
    low, high = bootstrap_roi_ci(bets, n_boot=500, seed=1)
    assert low < 0.0 < high


def test_walk_forward_jamais_de_look_ahead():
    items = list(range(200))
    folds = walk_forward_splits(
        items,
        time_of=lambda i: T0 + timedelta(days=i),
        n_folds=4,
        min_train=50,
    )
    assert len(folds) == 4
    for train, test in folds:
        assert max(train) < min(test)


def test_walk_forward_trop_peu_de_donnees_renvoie_vide():
    assert walk_forward_splits(list(range(10)), time_of=lambda i: T0 + timedelta(days=i)) == []
