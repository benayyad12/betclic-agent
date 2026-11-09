"""Modèle logistique multinomial, appris sur les features extraites avant chaque match.

Une passe chronologique : la forme et l'Elo utilisés pour un match n'incluent pas son résultat.
Le score exact reste celui du modèle de buts, pour ne pas contredire la matrice de scores.
"""

from __future__ import annotations

import math
from collections import deque

from .dataset import MatchRecord

FEATURES = 6
EPOCHS = 2
LR = 0.08
L2 = 1e-4
RECENT = 5


def _softmax(scores: list[float]) -> list[float]:
    top = max(scores)
    exps = [math.exp(score - top) for score in scores]
    total = sum(exps) or 1.0
    return [value / total for value in exps]


def _dot(weights: list[float], features: list[float]) -> float:
    return sum(w * x for w, x in zip(weights, features))


class LogisticModel:
    def __init__(self, weights: list[list[float]], samples: int):
        self.weights = weights
        self.samples = samples

    def probabilities(self, features: list[float]) -> dict[str, float]:
        scores = [_dot(row, features) for row in self.weights]
        home, draw, away = _softmax(scores)
        return {"home": home, "draw": draw, "away": away}

    def as_dict(self) -> dict:
        return {"samples": self.samples, "features": FEATURES, "epochs": EPOCHS}


def _blank() -> dict:
    return {"elo": None, "rows": deque(maxlen=RECENT)}


def features_from(state: dict[str, dict], home: str, away: str, home_elo: float | None, away_elo: float | None) -> list[float]:
    """Features à l'instant d'un match, sans son résultat."""
    hs, aws = state.get(home) or _blank(), state.get(away) or _blank()
    elo_gap = 0.0
    elo_known = 0.0
    if home_elo is not None and away_elo is not None:
        elo_gap = (home_elo - away_elo) / 100.0
        elo_known = 1.0
    elif hs["elo"] is not None and aws["elo"] is not None:
        elo_gap = (hs["elo"] - aws["elo"]) / 100.0
        elo_known = 1.0
    h_rows, a_rows = list(hs["rows"]), list(aws["rows"])
    h_pts = sum(row[2] for row in h_rows) / 15.0
    a_pts = sum(row[2] for row in a_rows) / 15.0
    h_gf = sum(row[0] for row in h_rows) / len(h_rows) if h_rows else 0.0
    a_gf = sum(row[0] for row in a_rows) / len(a_rows) if a_rows else 0.0
    h_shots = [row[3] for row in h_rows if row[3] is not None]
    a_shots = [row[3] for row in a_rows if row[3] is not None]
    shots = ((sum(h_shots) / len(h_shots)) - (sum(a_shots) / len(a_shots))) / 10.0 if h_shots and a_shots else 0.0
    return [1.0, elo_gap, elo_known, h_pts - a_pts, h_gf - a_gf, shots]


def _update(state: dict[str, dict], match: MatchRecord) -> None:
    for team, gf, ga, elo, shots in (
        (match.home, match.ft_home, match.ft_away, match.home_elo, match.home_shots),
        (match.away, match.ft_away, match.ft_home, match.away_elo, match.away_shots),
    ):
        slot = state.get(team)
        if slot is None:
            slot = state[team] = _blank()
        points = 3 if gf > ga else 1 if gf == ga else 0
        slot["rows"].append((gf, ga, points, shots))
        if elo is not None:
            slot["elo"] = elo


def fit(matches: list[MatchRecord], as_of) -> tuple[LogisticModel, FeatureIndex]:
    """Ajuste le modèle sur les matchs strictement antérieurs à ``as_of``."""
    past = [m for m in matches if m.date < as_of]
    weights = [[0.0] * FEATURES for _ in range(3)]
    state: dict[str, dict] = {}
    samples: list[tuple[list[float], int]] = []
    for match in past:
        label = 0 if match.ft_home > match.ft_away else 1 if match.ft_home == match.ft_away else 2
        samples.append((features_from(state, match.home, match.away, match.home_elo, match.away_elo), label))
        _update(state, match)
    model = LogisticModel(weights, len(samples))
    if len(samples) >= 50:
        for _ in range(EPOCHS):
            for features, label in samples:
                probs = _softmax([_dot(row, features) for row in weights])
                for cls in range(3):
                    error = probs[cls] - (1.0 if cls == label else 0.0)
                    row = weights[cls]
                    for i, value in enumerate(features):
                        row[i] -= LR * (error * value + L2 * row[i])
    return model, FeatureIndex(state)


class FeatureIndex:
    """État des équipes après la passe d'extraction. Sert à prédire un match futur."""

    def __init__(self, state: dict[str, dict]):
        self.state = state

    def probabilities(self, model: LogisticModel, home: str, away: str) -> dict:
        probs = model.probabilities(features_from(self.state, home, away, None, None))
        pick = max(probs, key=probs.get)
        return {**probs, "pick": pick, "samples": model.samples}
