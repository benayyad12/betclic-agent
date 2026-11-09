"""Modèles dédiés corners, cartons et tirs (distincts du modèle de buts).

Taux par équipe = moyenne pondérée dans le temps des valeurs produites et concédées,
rétrécie vers la moyenne du jeu. Les valeurs manquantes sont exclues, jamais mises à 0.
Le total suit une loi de Poisson : approximation, signalée dans ``method``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from .dataset import MatchRecord
from .errors import UnknownTeam
from .poisson import _weight

SHRINK_K = 6.0
MIN_SAMPLES = 30
OVER_LINES = {
    "corners": (7.5, 8.5, 9.5, 10.5),
    "cards": (2.5, 3.5, 4.5),
}


@dataclass(frozen=True)
class Rate:
    for_: float
    against: float


@dataclass
class StatModel:
    as_of: date
    league: dict[str, float]
    teams: dict[str, dict[str, Rate]]

    def expected(self, stat: str, home: str, away: str) -> tuple[float, float]:
        for team in (home, away):
            if team not in self.teams:
                raise UnknownTeam(team)
        h = self.teams[home][stat]
        a = self.teams[away][stat]
        home_exp = (h.for_ + a.against) / 2
        away_exp = (a.for_ + h.against) / 2
        return home_exp, away_exp


def fit(matches: list[MatchRecord], as_of: date) -> dict[str, StatModel]:
    """Un modèle par statistique disposant d'assez de données. Une statistique absente
    n'apparaît pas dans le résultat : elle n'est jamais estimée à partir de zéros."""
    past = [m for m in matches if m.date < as_of]
    models = {}
    for stat, home_attr, away_attr in (
        ("corners", "home_corners", "away_corners"),
        ("cards", "home_cards", "away_cards"),
    ):
        rows = []
        for m in past:
            hv, av = getattr(m, home_attr), getattr(m, away_attr)
            if hv is None or av is None:
                continue
            rows.append((m, _weight(m.date, as_of), hv, av))
        if len(rows) < MIN_SAMPLES:
            continue

        league_mean = (
            sum(w * (hv + av) / 2 for _, w, hv, av in rows) / sum(w for _, w, _, _ in rows)
        )
        acc: dict[str, list[float]] = {}
        for m, w, hv, av in rows:
            for team, produced, conceded in ((m.home, hv, av), (m.away, av, hv)):
                slot = acc.setdefault(team, [0.0, 0.0, 0.0])
                slot[0] += w * produced
                slot[1] += w * conceded
                slot[2] += w
        teams = {}
        for team, (prod, conc, weight) in acc.items():
            teams[team] = {
                stat: Rate(
                    for_=(prod + SHRINK_K * league_mean) / (weight + SHRINK_K),
                    against=(conc + SHRINK_K * league_mean) / (weight + SHRINK_K),
                )
            }
        models[stat] = StatModel(as_of=as_of, league={stat: league_mean}, teams=teams)
    return models


def poisson_over(total_mean: float, line: float, max_k: int = 60) -> dict[str, float]:
    """P(total > line) et P(total < line) pour une loi de Poisson de moyenne donnée."""
    over = under = 0.0
    for k in range(max_k):
        p = math.exp(-total_mean + k * math.log(total_mean) - math.lgamma(k + 1)) if total_mean > 0 else float(k == 0)
        if k > line:
            over += p
        elif k < line:
            under += p
    return {"over": over, "under": under}


def derive(stat_model: StatModel, stat: str, home: str, away: str) -> dict:
    home_exp, away_exp = stat_model.expected(stat, home, away)
    total = home_exp + away_exp
    return {
        "expected": {"home": home_exp, "away": away_exp, "total": total},
        "over_under": {
            f"{line}": poisson_over(total, line) for line in OVER_LINES[stat]
        },
        "method": "taux par équipe rétrécis, total Poisson (approximation)",
    }
