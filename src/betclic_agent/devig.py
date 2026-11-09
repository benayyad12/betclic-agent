"""Dé-margement d'un marché : trois méthodes pour retirer la marge du bookmaker.

Entrée : probabilités implicites brutes ``q_i = 1 / cote_i`` (somme S = 1 + marge).
Sortie : probabilités ``p_i`` dont la somme vaut 1.

- ``proportional`` : ``p_i = q_i / S``. Répartit la marge uniformément.
- ``power``        : ``p_i = q_i ** k`` avec k tel que ``sum p_i = 1``. Pénalise davantage
  les issues à forte cote (longshots) que les favoris.
- ``shin``         : modèle de Shin (1993) avec z = part d'initiés, résolu par bissection.
  Suppose qu'une partie des paris provient d'initiés ; méthode prudente sur les longshots.

Si S <= 1 (pas de marge, ou marge négative), ``power`` et ``shin`` renvoient la méthode
proportionnelle : il n'y a rien à retirer de façon fiable.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

_TOL = 1e-12
_MAX_ITER = 200


def _check(q: Sequence[float]) -> float:
    if not q:
        raise ValueError("aucune issue")
    if any(x < 0 or x >= 1.0 for x in q):
        raise ValueError("probabilité brute hors de [0, 1[")
    total = sum(q)
    if total <= 0:
        raise ValueError("somme des probabilités nulle")
    return total


def proportional(q: Sequence[float]) -> list[float]:
    total = _check(q)
    return [x / total for x in q]


def _bisect(f: Callable[[float], float], lo: float, hi: float) -> float:
    """Racine de f sur [lo, hi] avec f(lo) >= 0 >= f(hi)."""
    for _ in range(_MAX_ITER):
        mid = (lo + hi) / 2
        if f(mid) > 0:
            lo = mid
        else:
            hi = mid
        if hi - lo < _TOL:
            break
    return (lo + hi) / 2


def power(q: Sequence[float]) -> list[float]:
    total = _check(q)
    if total <= 1.0:
        return proportional(q)
    nonzero = sum(1 for x in q if x > 0)
    if nonzero < 2:
        return proportional(q)

    def excess(k: float) -> float:
        return sum(x**k for x in q) - 1.0

    # excess(0) = nb_issues_non_nulles - 1 > 0 ; excess décroît avec k.
    hi = 1.0
    while excess(hi) > 0:
        hi *= 2.0
    k = _bisect(excess, 0.0, hi)
    return [x**k for x in q]


def shin(q: Sequence[float]) -> list[float]:
    total = _check(q)
    if total <= 1.0:
        return proportional(q)

    def probs(z: float) -> list[float]:
        return [
            (((z * z + 4 * (1 - z) * x * x / total) ** 0.5) - z) / (2 * (1 - z))
            for x in q
        ]

    def excess(z: float) -> float:
        return sum(probs(z)) - 1.0

    z = _bisect(excess, 0.0, 1.0 - 1e-9)
    return probs(z)


METHODS: dict[str, Callable[[Sequence[float]], list[float]]] = {
    "proportional": proportional,
    "power": power,
    "shin": shin,
}
