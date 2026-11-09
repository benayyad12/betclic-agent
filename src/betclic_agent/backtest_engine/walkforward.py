"""Validation walk-forward : chaque test n'utilise que des données antérieures.

Les observations sont triées par date. Le k-ième pli entraîne sur tout ce qui précède
sa frontière temporelle et teste sur la tranche suivante. Aucune observation de test
n'est jamais antérieure à une observation d'entraînement (pas de look-ahead).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import TypeVar

T = TypeVar("T")


def walk_forward_splits(
    items: Sequence[T],
    time_of: Callable[[T], datetime],
    n_folds: int = 5,
    min_train: int = 50,
) -> list[tuple[list[T], list[T]]]:
    """Renvoie des paires (entraînement, test) en ordre chronologique.

    Entraînement = ``time < frontière``, test = ``frontière <= time < frontière suivante``.
    La dernière frontière dépasse la dernière observation pour l'inclure. Les plis
    dont l'entraînement ou le test est vide sont ignorés.
    """
    if n_folds < 1:
        raise ValueError("n_folds doit être >= 1")
    ordered = sorted(items, key=time_of)
    if len(ordered) < min_train + n_folds:
        return []

    start = min_train
    step = (len(ordered) - start) / n_folds
    boundaries = [time_of(ordered[int(start + k * step)]) for k in range(n_folds)]
    boundaries.append(time_of(ordered[-1]) + timedelta(microseconds=1))

    folds = []
    for k in range(n_folds):
        lo, hi = boundaries[k], boundaries[k + 1]
        train = [x for x in ordered if time_of(x) < lo]
        test = [x for x in ordered if lo <= time_of(x) < hi]
        if train and test:
            folds.append((train, test))
    return folds
