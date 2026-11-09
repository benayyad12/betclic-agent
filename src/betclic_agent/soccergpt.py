"""Analyse de match par GPT-4o, portée depuis SoccerGPT (https://github.com/chrisby/SoccerGPT,
licence Apache-2.0) : statistiques d'équipe Sportmonks → prompt → prédiction du score.

Clés lues dans `.env` uniquement : SPORTMONKS_API_TOKEN, OPENAI_API_KEY.
Périmètre v1 : statistiques d'équipe (pas les joueurs ni les coachs) pour limiter le nombre
de requêtes. Une prédiction est une sortie de modèle, pas une probabilité calibrée : à valider
par le backtest avant tout usage.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from string import Formatter

import requests

SPORTMONKS_BASE = "https://api.sportmonks.com/v3/football"
TIMEOUT_S = 15
PREDICTION_TTL_S = 3600

# Gabarits des statistiques d'équipe (type_id Sportmonks → phrase), repris de SoccerGPT/helper.py.
TEAM_STATS = {
    51: "On average, the team caused {average} offsides per game.",
    84: "On average, the team received {average} yellow cards per game.",
    56: "On average, the team performed {average} fouls per game.",
    47: "The team scored {scored} penalties, and missed {missed}.",
    216: "The team lost {count} game(s). This is {percentage}% of all games played.",
    88: "The team conceded {count} goals in total and {average} goals per game.",
    78: "The team performed {count} tackles and {average} tackles per game.",
    52: "The team scored {count} goals, with {average} goals per game.",
    215: "{count} of the team's game(s) resulted in a draw. This is {percentage}% of all games played.",
    45: "The team's ball possession was {average}%",
    214: "The team won {count} game(s). This is {percentage}% of all games played.",
    194: "In {percentage}% of all games, their opponent did not score a single goal (clean sheet).",
    34: "On average, the team took {average} corners per game.",
    44: "On average, the team performed {average} dangerous attacks per game.",
    9681: "{conversion_rate_pct}% of all shots were successfully converted into a goal.",
    1677: "Out of all {total} shots, {on_target} were on target, {off_target} were off target, {inside_box} were shot inside of the box, {outside_box} outside the box, and {blocked} were blocked.",
    9682: "{pct_shots_on_target}% of all shots were on the target.",
    43: "On average, the team performed {average} attacks per game.",
    213: "{0-15}% of all conceded goals were conceded in the first 15 minutes, {15-30}% between minute 15 and 30, {30-45}% between minute 30 and 45, and {45-60}% were conceded in the last 15 minutes.",
    196: "{0-15}% of all scored goals were scored in the first 15 minutes, {15-30}% between minute 15 and 30, {30-45}% between minute 30 and 45, and {45-60}% were scored in the last 15 minutes.",
    191: "In {over_0_5}% of the games, the team scored at least 1 goal, in {over_1_5}% of the games they scored at least 2 goals, in {over_2_5}% of the games they scored at least 3 goals, in {over_3_5}% of the games they scored at least 4 goals, in {over_4_5}% of the games they scored at least 5 goals, in {over_5_5}% of the games they scored at least 6 goals.",
    188: "The team played a total of {count} matches.",
    9676: "On average, the team earned {average_points_per_game} points per game.",
}
# Statistiques dont la valeur est sous value["all"] (les autres sont directement sous value).
_ALL_BUCKET = {216, 88, 52, 214, 194, 215}

SYSTEM_PROMPT = """\
You are a soccer expert with the latest knowledge about all European teams.
Your task is to predict the result of a {competition} game.

The user will provide you with the game information and details such as coach name, \
the team's performance during the qualification tournament, \
and statistics about the team.

Before responding with the final score, you must analyze the provided information and justify the predicted score.
Please take all information about the teams into account.

Finally, respond in the following format:
Winner: TEAM_NAME
Final Score: TEAM_1: SCORE, TEAM_2: SCORE
"""


class SoccerGPTError(Exception):
    """Erreur lisible côté API (configuration manquante, équipe introuvable, réponse inexploitable)."""


@dataclass(frozen=True)
class Settings:
    sportmonks_token: str
    openai_key: str
    openai_model: str
    league: str
    season: str
    competition: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            sportmonks_token=os.getenv("SPORTMONKS_API_TOKEN", ""),
            openai_key=os.getenv("OPENAI_API_KEY", ""),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-4o"),
            league=os.getenv("SOCCERGPT_LEAGUE", "Euro Qualification"),
            season=os.getenv("SOCCERGPT_SEASON", "2024"),
            competition=os.getenv("SOCCERGPT_COMPETITION", "2024 European Championship"),
        )

    def require(self) -> None:
        missing = [n for n, v in (("SPORTMONKS_API_TOKEN", self.sportmonks_token),
                                  ("OPENAI_API_KEY", self.openai_key)) if not v]
        if missing:
            raise SoccerGPTError(f"Configuration manquante dans .env : {', '.join(missing)}")


class Sportmonks:
    """Client minimal. Le jeton part dans l'en-tête Authorization, jamais dans l'URL (logs)."""

    def __init__(self, token: str, session: requests.Session | None = None) -> None:
        self._token = token
        self._session = session or requests.Session()
        self._seasons: dict[int, dict | None] = {}
        self._leagues: dict[int, dict | None] = {}

    def get(self, path: str, include: str | None = None) -> dict | None:
        params = {"include": include} if include else None
        r = self._session.get(
            f"{SPORTMONKS_BASE}/{path}",
            headers={"Authorization": self._token},
            params=params,
            timeout=TIMEOUT_S,
        )
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise SoccerGPTError(f"Sportmonks a répondu {r.status_code} sur {path}")
        return r.json().get("data")

    def find_team(self, name: str) -> dict | None:
        """Meilleure correspondance par nom exact (insensible à la casse), sinon la première."""
        data = self.get(f"teams/search/{requests.utils.quote(name)}") or []
        if not data:
            return None
        exact = [t for t in data if t.get("name", "").lower() == name.lower()]
        return (exact or data)[0]

    def season(self, season_id: int) -> dict | None:
        if season_id not in self._seasons:
            self._seasons[season_id] = self.get(f"seasons/{season_id}")
        return self._seasons[season_id]

    def league(self, league_id: int) -> dict | None:
        if league_id not in self._leagues:
            self._leagues[league_id] = self.get(f"leagues/{league_id}")
        return self._leagues[league_id]


def stat_lines(statistic: dict) -> list[str]:
    """Phrases lisibles pour une statistique d'équipe (valeurs absentes → ligne omise, rien d'inventé)."""
    lines = []
    details = {d["type_id"]: d for d in statistic.get("details", [])}
    for type_id, template in TEAM_STATS.items():
        detail = details.get(type_id)
        if detail is None:
            continue
        value = detail.get("value")
        if type_id in _ALL_BUCKET:
            value = (value or {}).get("all")
        elif type_id == 191:
            value = {k: (v or {}).get("team") for k, v in (value or {}).items()}
        keys = [k for _, k, _, _ in Formatter().parse(template) if k]
        try:
            kwargs = {k: value[k] for k in keys}
        except (KeyError, TypeError):
            continue  # champ manquant : on omet plutôt que d'inventer
        if any(v is None for v in kwargs.values()):
            continue
        if type_id in (213, 196, 191):
            kwargs = {k: (v or {}).get("percentage") if isinstance(v, dict) else v for k, v in kwargs.items()}
            if any(v is None for v in kwargs.values()):
                continue
        lines.append(template.format(**kwargs))
    return lines


def team_qualification_stats(api: Sportmonks, team: dict, league_name: str, season_name: str) -> list[str]:
    """Statistiques de l'équipe sur la compétition et la saison demandées (comme SoccerGPT)."""
    full = api.get(f"teams/{team['id']}", include="statistics.details") or {}
    lines: list[str] = []
    for statistic in full.get("statistics", []):
        season = api.season(statistic["season_id"])
        if not season or str(season.get("name")) != season_name:
            continue
        league = api.league(season["league_id"]) or {}
        if league.get("name") != league_name:
            continue
        lines.extend(stat_lines(statistic))
    return lines


def build_match_prompt(home: str, away: str, home_lines: list[str], away_lines: list[str]) -> str:
    parts = [f"The match for which you have to predict the final score is {home} vs. {away}."]
    for name, lines in ((home, home_lines), (away, away_lines)):
        parts.append(f'\n\nHere are some statistics about the team "{name}".')
        if not lines:
            parts.append("\n - No statistics available for this team in the configured competition.")
        for line in lines:
            parts.append(f"\n - {line}")
    parts.append("\n\n Now predict the outcome of the game following the instructions from above.")
    return "".join(parts)


_WINNER = re.compile(r"Winner:\s*(.+)", re.IGNORECASE)
_SCORE = re.compile(r"Final Score:\s*(.+)", re.IGNORECASE)


def parse_prediction(text: str) -> dict:
    """Extrait le vainqueur et le score. Les champs absents restent None (pas de valeur inventée)."""
    winner = _WINNER.search(text)
    score = _SCORE.search(text)
    return {
        "winner": winner.group(1).strip() if winner else None,
        "final_score": score.group(1).strip() if score else None,
    }


def ask_model(settings: Settings, system: str, user: str) -> str:
    from openai import OpenAI  # import paresseux : le reste du projet n'en dépend pas

    completion = OpenAI(api_key=settings.openai_key, timeout=TIMEOUT_S * 4).chat.completions.create(
        model=settings.openai_model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.2,
    )
    return completion.choices[0].message.content or ""


_cache: dict[tuple[str, str], tuple[float, dict]] = {}


def predict(home: str, away: str, settings: Settings | None = None) -> dict:
    """Prédiction pour home vs away. Résultats mis en cache une heure (coût API et latence)."""
    settings = settings or Settings.from_env()
    settings.require()
    key = (home.lower(), away.lower())
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < PREDICTION_TTL_S:
        return {**hit[1], "from_cache": True}

    api = Sportmonks(settings.sportmonks_token)
    teams = {}
    for label, name in (("home", home), ("away", away)):
        team = api.find_team(name)
        if team is None:
            raise SoccerGPTError(f"Équipe introuvable chez Sportmonks : {name}")
        teams[label] = team
    home_lines = team_qualification_stats(api, teams["home"], settings.league, settings.season)
    away_lines = team_qualification_stats(api, teams["away"], settings.league, settings.season)

    system = SYSTEM_PROMPT.format(competition=settings.competition)
    user = build_match_prompt(home, away, home_lines, away_lines)
    raw = ask_model(settings, system, user)
    result = {
        "home": home,
        "away": away,
        "model": settings.openai_model,
        "stats_lines": {"home": len(home_lines), "away": len(away_lines)},
        **parse_prediction(raw),
        "analysis": raw,
        "from_cache": False,
    }
    _cache[key] = (time.monotonic(), result)
    return result
