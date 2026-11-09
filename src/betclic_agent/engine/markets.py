"""Dérivation de tous les marchés à partir d'une seule matrice de scores.

Toutes les probabilités (1X2, double chance, handicap, BTTS, over/under, buts par équipe,
clean sheets, scores exacts) viennent de la même matrice P[i][j] : elles sont donc
cohérentes entre elles par construction. La mi-temps utilise une part fixe des buts
attendus (approximation signalée dans la sortie).
"""

from __future__ import annotations

from .poisson import MAX_GOALS, score_matrix

OVER_UNDER_LINES = (0.5, 1.5, 2.5, 3.5, 4.5)
ASIAN_LINES = (-1.5, -1.25, -1.0, -0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5)


def _win_draw_loss(matrix) -> tuple[float, float, float]:
    home = draw = away = 0.0
    for i, row in enumerate(matrix):
        for j, p in enumerate(row):
            if i > j:
                home += p
            elif i == j:
                draw += p
            else:
                away += p
    return home, draw, away


def _asian_single(matrix, line: float) -> tuple[float, float, float]:
    """Handicap sur une ligne entière ou demie : (gagne, push, perd) pour l'équipe domicile."""
    win = push = lose = 0.0
    for i, row in enumerate(matrix):
        for j, p in enumerate(row):
            margin = i - j + line
            if margin > 0:
                win += p
            elif margin == 0:
                push += p
            else:
                lose += p
    return win, push, lose


def asian_handicap(matrix, line: float) -> dict:
    """Ligne quart (ex. -0.25) = moitié sur les deux lignes voisines, comme dans les books."""
    if abs((line * 4) % 2) == 1:
        lower, upper = line - 0.25, line + 0.25
        w1, p1, l1 = _asian_single(matrix, lower)
        w2, p2, l2 = _asian_single(matrix, upper)
        win, push, lose = (w1 + w2) / 2, (p1 + p2) / 2, (l1 + l2) / 2
    else:
        win, push, lose = _asian_single(matrix, line)
    return {"line": line, "win": win, "push": push, "lose": lose}


def _goal_marginals(matrix) -> tuple[list[float], list[float]]:
    size = len(matrix)
    home = [sum(matrix[i][j] for j in range(size)) for i in range(size)]
    away = [sum(matrix[i][j] for i in range(size)) for j in range(size)]
    return home, away


def derive_markets(lam: float, mu: float, rho: float, ht_share: float = 0.44) -> dict:
    matrix = score_matrix(lam, mu, rho, MAX_GOALS)
    home_p, draw_p, away_p = _win_draw_loss(matrix)
    total = {}
    for i, row in enumerate(matrix):
        for j, p in enumerate(row):
            total[i + j] = total.get(i + j, 0.0) + p

    btts_yes = sum(p for i, row in enumerate(matrix) for j, p in enumerate(row) if i > 0 and j > 0)
    home_goals, away_goals = _goal_marginals(matrix)

    scores = sorted(
        ((i, j, p) for i, row in enumerate(matrix) for j, p in enumerate(row)),
        key=lambda t: t[2],
        reverse=True,
    )[:5]

    ht_lam, ht_mu = lam * ht_share, mu * ht_share
    ht_matrix = score_matrix(ht_lam, ht_mu, 0.0, MAX_GOALS)
    ht_home, ht_draw, ht_away = _win_draw_loss(ht_matrix)
    ht_total = {}
    for i, row in enumerate(ht_matrix):
        for j, p in enumerate(row):
            ht_total[i + j] = ht_total.get(i + j, 0.0) + p

    return {
        "expected_goals": {"home": lam, "away": mu},
        "match_result": {"home": home_p, "draw": draw_p, "away": away_p},
        "double_chance": {"1X": home_p + draw_p, "X2": draw_p + away_p, "12": home_p + away_p},
        "draw_no_bet": {
            "home": home_p / (home_p + away_p) if home_p + away_p else None,
            "away": away_p / (home_p + away_p) if home_p + away_p else None,
        },
        "asian_handicap": [asian_handicap(matrix, line) for line in ASIAN_LINES],
        "btts": {"yes": btts_yes, "no": 1.0 - btts_yes},
        "over_under": {
            f"{line}": {"over": sum(p for k, p in total.items() if k > line),
                        "under": sum(p for k, p in total.items() if k < line)}
            for line in OVER_UNDER_LINES
        },
        "team_goals": {
            "home": {"expected": sum(k * p for k, p in enumerate(home_goals)),
                     "clean_sheet": sum(row[0] for row in matrix),
                     "scoring": 1.0 - home_goals[0]},
            "away": {"expected": sum(k * p for k, p in enumerate(away_goals)),
                     "clean_sheet": sum(matrix[0]),
                     "scoring": 1.0 - away_goals[0]},
        },
        "top_scores": [{"home": i, "away": j, "p": p} for i, j, p in scores],
        "half_time": {
            "result": {"home": ht_home, "draw": ht_draw, "away": ht_away},
            "over_under": {
                f"{line}": {"over": sum(p for k, p in ht_total.items() if k > line),
                            "under": sum(p for k, p in ht_total.items() if k < line)}
                for line in (0.5, 1.5)
            },
            "method": "approximation: buts de mi-temps Poisson indépendants, part fixe du total",
        },
    }
