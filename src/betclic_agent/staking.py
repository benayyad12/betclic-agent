"""Gestion de mise : flat betting, Kelly fractionné plafonné, simulation de risque de ruine.

Toutes les mises sont exprimées dans la même unité que ``bankroll``. Une espérance
nulle ou négative donne une mise nulle : on ne mise jamais contre son propre modèle.
"""

from __future__ import annotations

import random
import statistics


def flat_stake(bankroll: float, unit_pct: float = 0.01) -> float:
    """Mise fixe : ``unit_pct`` de la bankroll, quel que soit l'avantage estimé."""
    if bankroll <= 0:
        return 0.0
    return bankroll * unit_pct


def kelly_fraction(prob: float, odds: float) -> float:
    """Fraction de bankroll optimale selon Kelly : (p × cote − 1) / (cote − 1).

    Renvoie 0 si l'espérance est nulle ou négative.
    """
    if odds <= 1.0 or not 0.0 <= prob <= 1.0:
        raise ValueError("cote > 1 et probabilité dans [0, 1] requises")
    full = (prob * odds - 1.0) / (odds - 1.0)
    return max(0.0, full)


def fractional_kelly_stake(
    bankroll: float,
    prob: float,
    odds: float,
    fraction: float = 0.25,
    cap_pct: float = 0.02,
) -> float:
    """Kelly multiplié par ``fraction`` (ex. 0.25 = quart de Kelly), plafonné à ``cap_pct``.

    La fraction réduit la variance liée à une estimation imparfaite de ``prob`` ;
    le plafond limite la perte maximale sur un seul pari.
    """
    if bankroll <= 0:
        return 0.0
    if not 0.0 < fraction <= 1.0:
        raise ValueError("fraction de Kelly dans ]0, 1]")
    if not 0.0 < cap_pct <= 1.0:
        raise ValueError("plafond dans ]0, 1]")
    share = min(fraction * kelly_fraction(prob, odds), cap_pct)
    return bankroll * share


def ruin_simulation(
    prob: float,
    odds: float,
    n_bets: int,
    start_bankroll: float = 100.0,
    fraction: float = 0.25,
    cap_pct: float = 0.02,
    ruin_level_pct: float = 0.5,
    n_sims: int = 5000,
    seed: int = 0,
) -> dict[str, float]:
    """Monte Carlo : probabilité de tomber sous ``ruin_level_pct`` de la bankroll initiale.

    Hypothèse : chaque pari a la probabilité ``prob`` réelle et une mise Kelly fractionnée
    recalculée sur la bankroll courante. Si le modèle surestime ``prob``, la simulation
    est trop optimiste : c'est une limite du modèle, pas une garantie.
    """
    if n_bets < 1 or n_sims < 1:
        raise ValueError("n_bets et n_sims doivent être >= 1")
    rng = random.Random(seed)
    floor = start_bankroll * ruin_level_pct
    ruined = 0
    finals = []
    for _ in range(n_sims):
        bankroll = start_bankroll
        for _ in range(n_bets):
            stake = fractional_kelly_stake(bankroll, prob, odds, fraction, cap_pct)
            if stake > 0:
                bankroll += stake * (odds - 1.0) if rng.random() < prob else -stake
            if bankroll < floor:
                ruined += 1
                break
        finals.append(bankroll)
    return {
        "ruin_probability": ruined / n_sims,
        "median_final": statistics.median(finals),
        "n_sims": n_sims,
    }
