"""Dossier statistique d'une rencontre, calculé uniquement sur les matchs antérieurs.

Elo, forme, tirs et confrontations directes décrivent l'historique. Ils ne remplacent
pas le modèle de buts et aucune valeur manquante n'est remplacée par zéro.
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import date

from .dataset import MatchRecord

RECENT = 5
SHOT_WINDOW = 10
H2H_KEEP = 6
MIN_ELO_BUCKET = 30
ELO_EDGES = (-150, -75, -25, 25, 75, 150)


def _result(gf: int, ga: int) -> str:
    if gf > ga:
        return "V"
    if gf == ga:
        return "N"
    return "D"


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def elo_bucket(gap: float) -> int:
    for index, edge in enumerate(ELO_EDGES):
        if gap < edge:
            return index
    return len(ELO_EDGES)


class DossierIndex:
    def __init__(self, teams: dict[str, dict], h2h: dict[tuple[str, str], list[dict]], elo_rates: list[dict]):
        self.teams = teams
        self.h2h = h2h
        self.elo_rates = elo_rates

    def pair(self, home: str, away: str) -> dict:
        home_row = self.teams.get(home)
        away_row = self.teams.get(away)
        meetings = self.h2h.get(tuple(sorted((home, away))), [])
        home_wins = draws = away_wins = 0
        for meeting in meetings:
            gf, ga = _goals_for(meeting, home)
            if gf > ga:
                home_wins += 1
            elif gf == ga:
                draws += 1
            else:
                away_wins += 1
        elo_gap = None
        elo_reading = None
        if home_row and away_row and home_row.get("elo") is not None and away_row.get("elo") is not None:
            elo_gap = home_row["elo"] - away_row["elo"]
            bucket = self.elo_rates[elo_bucket(elo_gap)]
            if bucket["n"] >= MIN_ELO_BUCKET:
                elo_reading = {
                    "gap": elo_gap,
                    "n": bucket["n"],
                    "home": bucket["home"] / bucket["n"],
                    "draw": bucket["draw"] / bucket["n"],
                    "away": bucket["away"] / bucket["n"],
                }
        return {
            "home": home_row,
            "away": away_row,
            "h2h": {
                "n": len(meetings),
                "home_wins": home_wins,
                "draws": draws,
                "away_wins": away_wins,
                "meetings": list(reversed(meetings)),
            },
            "elo_gap": elo_gap,
            "elo_reading": elo_reading,
        }


def _goals_for(meeting: dict, team: str) -> tuple[int, int]:
    if meeting["home"] == team:
        return meeting["gf"], meeting["ga"]
    return meeting["ga"], meeting["gf"]


def _profile(team: str, rows: deque, elo: float | None, played: int) -> dict:
    last = list(rows)[-RECENT:]
    shot_rows = [row for row in rows if row["shots"] is not None]
    target_rows = [row for row in rows if row["target"] is not None]
    return {
        "team": team,
        "matches": played,
        "elo": elo,
        "form": [_result(row["gf"], row["ga"]) for row in last],
        "points_5": sum(3 if _result(row["gf"], row["ga"]) == "V" else 1 if _result(row["gf"], row["ga"]) == "N" else 0 for row in last) if last else None,
        "gf_5": _mean([row["gf"] for row in last]),
        "ga_5": _mean([row["ga"] for row in last]),
        "shots": _mean([row["shots"] for row in shot_rows]),
        "shots_n": len(shot_rows),
        "on_target": _mean([row["target"] for row in target_rows]),
        "on_target_n": len(target_rows),
    }


def build(matches: list[MatchRecord], as_of: date) -> DossierIndex:
    """Indexe l'historique strictement antérieur à ``as_of``."""
    recent: dict[str, deque] = defaultdict(lambda: deque(maxlen=SHOT_WINDOW))
    played: dict[str, int] = defaultdict(int)
    elo: dict[str, float] = {}
    h2h: dict[tuple[str, str], deque] = defaultdict(lambda: deque(maxlen=H2H_KEEP))
    buckets = [{"home": 0, "draw": 0, "away": 0, "n": 0} for _ in range(len(ELO_EDGES) + 1)]

    for match in matches:
        if match.date >= as_of:
            continue
        played[match.home] += 1
        played[match.away] += 1
        if match.home_elo is not None:
            elo[match.home] = match.home_elo
        if match.away_elo is not None:
            elo[match.away] = match.away_elo
        if match.home_elo is not None and match.away_elo is not None:
            slot = buckets[elo_bucket(match.home_elo - match.away_elo)]
            slot["n"] += 1
            if match.ft_home > match.ft_away:
                slot["home"] += 1
            elif match.ft_home == match.ft_away:
                slot["draw"] += 1
            else:
                slot["away"] += 1
        recent[match.home].append({
            "gf": match.ft_home, "ga": match.ft_away,
            "shots": match.home_shots, "target": match.home_target,
        })
        recent[match.away].append({
            "gf": match.ft_away, "ga": match.ft_home,
            "shots": match.away_shots, "target": match.away_target,
        })
        h2h[tuple(sorted((match.home, match.away)))].append({
            "date": match.date.isoformat(),
            "home": match.home,
            "away": match.away,
            "gf": match.ft_home,
            "ga": match.ft_away,
        })

    teams = {
        team: _profile(team, rows, elo.get(team), played[team])
        for team, rows in recent.items()
    }
    return DossierIndex(teams, {key: list(rows) for key, rows in h2h.items()}, buckets)
