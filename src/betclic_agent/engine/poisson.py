"""Modèle de Poisson bivarié à la Dixon-Coles (Dixon & Coles, 1997).

Chaque équipe a une force offensive et défensive. Les buts suivent des lois de Poisson
indépendantes, corrigées pour les scores faibles (0-0, 1-0, 0-1, 1-1) par le paramètre rho.
Les paramètres sont estimés par points fixes (maximum de vraisemblance du modèle
multiplicatif), avec une pondération temporelle et un rétrécissement vers 1.
Aucune donnée postérieure à ``as_of`` n'est utilisée.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from .dataset import MatchRecord
from .errors import InsufficientData, UnknownTeam

MAX_GOALS = 10
HALF_LIFE_DAYS = 548
LOOKBACK_DAYS = 8 * 365
SHRINK_K = 4.0
MIN_TEAM_MATCHES = 8
ITERATIONS = 60


def poisson_pmf(k: int, lam: float) -> float:
    return math.exp(-lam + k * math.log(lam) - math.lgamma(k + 1)) if lam > 0 else float(k == 0)


def tau(i: int, j: int, lam: float, mu: float, rho: float) -> float:
    if i == 0 and j == 0:
        return 1.0 - lam * mu * rho
    if i == 0 and j == 1:
        return 1.0 + lam * rho
    if i == 1 and j == 0:
        return 1.0 + mu * rho
    if i == 1 and j == 1:
        return 1.0 - rho
    return 1.0


def score_matrix(lam: float, mu: float, rho: float = 0.0, max_goals: int = MAX_GOALS):
    """Matrice P[i][j] = P(buts domicile = i, buts extérieur = j), normalisée."""
    home = [poisson_pmf(i, lam) for i in range(max_goals + 1)]
    away = [poisson_pmf(j, mu) for j in range(max_goals + 1)]
    grid = [[home[i] * away[j] * tau(i, j, lam, mu, rho) for j in range(max_goals + 1)]
            for i in range(max_goals + 1)]
    total = sum(sum(row) for row in grid)
    return [[cell / total for cell in row] for row in grid]


@dataclass(frozen=True)
class TeamRating:
    attack: float
    defence: float
    matches: float


@dataclass
class DixonColesModel:
    as_of: date
    base_home: float
    base_away: float
    rho: float
    ratings: dict[str, TeamRating]
    ht_share: float
    home_advantage: float = field(default=1.0)

    def expected_goals(self, home: str, away: str) -> tuple[float, float]:
        for team in (home, away):
            if team not in self.ratings:
                raise UnknownTeam(team)
        h, a = self.ratings[home], self.ratings[away]
        lam = self.base_home * h.attack * a.defence
        mu = self.base_away * a.attack * h.defence
        return lam, mu


def _weight(match_date: date, as_of: date) -> float:
    age = (as_of - match_date).days
    return 0.5 ** (age / HALF_LIFE_DAYS)


def fit(matches: list[MatchRecord], as_of: date, min_team_matches: int = MIN_TEAM_MATCHES) -> DixonColesModel:
    """Estime le modèle sur les seuls matchs strictement antérieurs à ``as_of``."""
    past = [m for m in matches if m.date < as_of and (as_of - m.date).days <= LOOKBACK_DAYS]
    if len(past) < 50:
        raise InsufficientData(f"{len(past)} matchs antérieurs : 50 minimum")

    rows = [(m, _weight(m.date, as_of)) for m in past]
    wsum = sum(w for _, w in rows)
    base_home = sum(w * m.ft_home for m, w in rows) / wsum
    base_away = sum(w * m.ft_away for m, w in rows) / wsum

    teams: dict[str, float] = {}
    for m, w in rows:
        teams[m.home] = teams.get(m.home, 0.0) + w
        teams[m.away] = teams.get(m.away, 0.0) + w
    under_sampled = [t for t, count in teams.items() if count < min_team_matches]
    attack = {t: 1.0 for t in teams}
    defence = {t: 1.0 for t in teams}

    for _ in range(ITERATIONS):
        scored = {t: 0.0 for t in teams}
        scored_exp = {t: 0.0 for t in teams}
        conceded = {t: 0.0 for t in teams}
        conceded_exp = {t: 0.0 for t in teams}
        for m, w in rows:
            scored[m.home] += w * m.ft_home
            scored_exp[m.home] += w * base_home * defence[m.away]
            scored[m.away] += w * m.ft_away
            scored_exp[m.away] += w * base_away * defence[m.home]
            conceded[m.away] += w * m.ft_home
            conceded_exp[m.away] += w * base_home * attack[m.home]
            conceded[m.home] += w * m.ft_away
            conceded_exp[m.home] += w * base_away * attack[m.away]
        for t in teams:
            attack[t] = (scored[t] + SHRINK_K) / (scored_exp[t] + SHRINK_K)
            defence[t] = (conceded[t] + SHRINK_K) / (conceded_exp[t] + SHRINK_K)
        mean_a = sum(attack.values()) / len(attack)
        mean_d = sum(defence.values()) / len(defence)
        for t in teams:
            attack[t] /= mean_a
            defence[t] /= mean_d

    rho = _estimate_rho(rows, base_home, base_away, attack, defence)
    ratings = {t: TeamRating(attack[t], defence[t], teams[t]) for t in teams}
    if under_sampled:
        for t in under_sampled:
            ratings.pop(t, None)

    ht_total = [m for m, _ in rows if m.ht_home is not None and m.ht_away is not None]
    ft_goals = sum(m.ft_home + m.ft_away for m in ht_total)
    ht_goals = sum(m.ht_home + m.ht_away for m in ht_total)
    ht_share = ht_goals / ft_goals if ft_goals else 0.45

    return DixonColesModel(
        as_of=as_of,
        base_home=base_home,
        base_away=base_away,
        rho=rho,
        ratings=ratings,
        ht_share=ht_share,
    )


def _estimate_rho(rows, base_home, base_away, attack, defence) -> float:
    """Recherche sur grille de rho qui maximise la vraisemblance des 4 scores faibles."""
    best_rho, best_ll = 0.0, -math.inf
    for step in range(-20, 21):
        rho = step / 100.0
        ll = 0.0
        for m, w in rows:
            if m.ft_home > 1 or m.ft_away > 1:
                continue
            lam = base_home * attack[m.home] * defence[m.away]
            mu = base_away * attack[m.away] * defence[m.home]
            factor = tau(m.ft_home, m.ft_away, lam, mu, rho)
            if factor <= 0:
                ll = -math.inf
                break
            ll += w * math.log(factor)
        if ll > best_ll:
            best_rho, best_ll = rho, ll
    return best_rho
