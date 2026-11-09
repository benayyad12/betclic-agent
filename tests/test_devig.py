"""Propriétés des méthodes de dé-margement : somme = 1, monotonie, identité sans marge."""

import random

import pytest

from betclic_agent.analysis import demargin
from betclic_agent.devig import METHODS, power, proportional, shin

BOOKS = [
    [1 / 2.0, 1 / 3.5, 1 / 3.8],  # 1X2 classique
    [1 / 1.05, 1 / 12, 1 / 30, 1 / 50],  # favori + longshots
    [1 / 1.9, 1 / 1.95],  # 2 issues
]


def _random_books(n=200, seed=7):
    rng = random.Random(seed)
    for _ in range(n):
        k = rng.randint(2, 6)
        raw = [rng.uniform(0.02, 0.95) for _ in range(k)]
        scale = rng.uniform(1.02, 1.30)  # marge 2 % à 30 %
        q = [x * scale / sum(raw) for x in raw]
        if all(x < 1.0 for x in q):  # une cote décimale > 1 donne q < 1
            yield q


@pytest.mark.parametrize("method", METHODS.values())
@pytest.mark.parametrize("q", BOOKS)
def test_somme_egale_a_un(method, q):
    assert sum(method(q)) == pytest.approx(1.0, abs=1e-9)


@pytest.mark.parametrize("method", METHODS.values())
def test_somme_et_bornes_sur_marches_aleatoires(method):
    for q in _random_books():
        p = method(q)
        assert sum(p) == pytest.approx(1.0, abs=1e-9)
        assert all(0.0 <= x <= 1.0 for x in p)


@pytest.mark.parametrize("method", METHODS.values())
def test_monotonie_une_cote_plus_haute_donne_une_proba_plus_basse(method):
    for q in _random_books():
        p = method(q)
        order = sorted(range(len(q)), key=lambda i: -q[i])  # du favori au longshot
        ps = [p[i] for i in order]
        assert all(a >= b - 1e-12 for a, b in zip(ps, ps[1:]))


@pytest.mark.parametrize("method", METHODS.values())
def test_sans_marge_la_methode_rend_les_probabilites_telles_quelles(method):
    q = [0.5, 0.3, 0.2]
    assert method(q) == pytest.approx(q, abs=1e-9)


def test_power_penalise_davantage_les_longshots_que_le_proportionnel():
    q = BOOKS[1]
    assert power(q)[-1] < proportional(q)[-1]


def test_shin_longshots_moins_retouches_que_le_proportionnel():
    q = BOOKS[1]
    assert shin(q)[-1] < proportional(q)[-1]


def test_demargin_reste_proportionnel():
    probs = demargin([{"name": "A", "odds": 2.0}, {"name": "B", "odds": 2.0}])
    assert probs == {"A": 0.5, "B": 0.5}


@pytest.mark.parametrize("method", METHODS.values())
def test_entree_invalide_leve_une_erreur(method):
    with pytest.raises(ValueError):
        method([])
    with pytest.raises(ValueError):
        method([0.0, 0.0])
    with pytest.raises(ValueError):
        method([1.0, 0.5])
