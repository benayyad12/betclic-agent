"""Service du moteur : assemble modèles, marchés, corners/cartons et value bets pour l'API.

Le modèle est ajusté une fois (date du jour) puis mis en cache. Une équipe inconnue ou un
historique insuffisant lève une erreur explicite : aucune valeur n'est inventée.
"""

from __future__ import annotations

import threading
from datetime import date, timedelta

from . import secondary
from .dataset import MatchRecord, load_matches
from .errors import EngineError, UnknownTeam
from .markets import derive_markets
from .poisson import fit
from .teams import TeamIndex
from .value import Selection, evaluate

RECENT_DAYS = 365
LOW_SAMPLE = 20
SEPARATORS = (" - ", " – ", " vs ", " v ")

_lock = threading.Lock()
_state: dict | None = None


def split_match_name(name: str) -> tuple[str, str] | None:
    for sep in SEPARATORS:
        if sep in name:
            home, away = name.split(sep, 1)
            return home.strip(), away.strip()
    return None


def _state_now() -> dict:
    global _state
    with _lock:
        if _state is None:
            matches: list[MatchRecord] = load_matches()
            as_of = date.today()
            model = fit(matches, as_of)
            names = {m.home for m in matches} | {m.away for m in matches}
            recent: dict[str, int] = {}
            cutoff = as_of - timedelta(days=RECENT_DAYS)
            for m in matches:
                if m.date >= cutoff:
                    recent[m.home] = recent.get(m.home, 0) + 1
                    recent[m.away] = recent.get(m.away, 0) + 1
            stats = secondary.fit(matches, as_of)
            _state = {
                "as_of": as_of,
                "last_match": matches[-1].date,
                "model": model,
                "stats": stats,
                "index": TeamIndex(names),
                "recent": recent,
            }
        return _state


def reset() -> None:
    global _state
    with _lock:
        _state = None


def _resolve(state: dict, name: str) -> str:
    resolved = state["index"].resolve(name)
    if resolved is None:
        raise UnknownTeam(name)
    return resolved


def _risks(state: dict, home: str, away: str) -> list[str]:
    risks = []
    staleness = (state["as_of"] - state["last_match"]).days
    if staleness > 30:
        risks.append(f"données arrêtées au {state['last_match'].isoformat()} ({staleness} j) : forme récente non prise en compte")
    for team in (home, away):
        n = state["recent"].get(team, 0)
        if n < LOW_SAMPLE:
            risks.append(f"{team} : {n} match(s) sur 12 mois, estimation fragile")
    if "cards" not in state["stats"]:
        risks.append("cartons : historique insuffisant, non estimés")
    if "corners" not in state["stats"]:
        risks.append("corners : historique insuffisant, non estimés")
    risks.append("absent : possession, xG, buteurs, compositions, blessures")
    return risks


def _quality(state: dict, home: str, away: str) -> str:
    n = min(state["recent"].get(home, 0), state["recent"].get(away, 0))
    if n >= 30:
        return "élevée"
    if n >= LOW_SAMPLE:
        return "moyenne"
    return "faible"


def predict(home_name: str, away_name: str, odds: dict | None = None) -> dict:
    """Prédictions complètes d'un match. ``odds`` = cotes Betclic optionnelles {home, draw, away}."""
    if split_match_name(home_name) is not None or split_match_name(away_name) is not None:
        raise EngineError("passez les noms d'équipes séparément")
    state = _state_now()
    home = _resolve(state, home_name)
    away = _resolve(state, away_name)
    if home == away:
        raise EngineError("les deux équipes doivent être différentes")

    model = state["model"]
    lam, mu = model.expected_goals(home, away)
    markets = derive_markets(lam, mu, model.rho, model.ht_share)

    stat_out = {}
    for stat, label in (("corners", "corners"), ("cards", "cards")):
        if stat in state["stats"]:
            stat_out[label] = secondary.derive(state["stats"][stat], stat, home, away)
        else:
            stat_out[label] = None

    value = []
    if odds and all(odds.get(k) for k in ("home", "draw", "away")):
        p = markets["match_result"]
        for key, label, prob in (("home", "Victoire domicile", p["home"]),
                                 ("draw", "Match nul", p["draw"]),
                                 ("away", "Victoire extérieur", p["away"])):
            odd = float(odds[key])
            n = min(state["recent"].get(home, 0), state["recent"].get(away, 0))
            value.append(evaluate(Selection("1X2", label, prob, 1 - prob, 0.0, odd, 1 / odd, n)))

    return {
        "home": home,
        "away": away,
        "as_of": state["as_of"].isoformat(),
        "data_last_match": state["last_match"].isoformat(),
        "data_quality": _quality(state, home, away),
        "expected_goals": {"home": lam, "away": mu},
        "rho": model.rho,
        "markets": markets,
        "secondary": stat_out,
        "value": value,
        "risks": _risks(state, home, away),
        "method": "Dixon-Coles (Poisson bivarié corrigé), données antérieures à aujourd'hui uniquement",
    }


QUALITY_RANK = {"faible": 0, "moyenne": 1, "élevée": 2}


def scan(fixtures: list[dict], min_quality: str = "faible", top: int = 10) -> dict:
    """Parcourt une liste de rencontres {home, away, odds?} et ne retourne que des opportunités justifiées.

    Une sélection n'est « value » que si la borne basse de l'intervalle d'EV est positive.
    Rien n'est forcé : une liste vide est un résultat valide.
    """
    floor = QUALITY_RANK.get(min_quality, 0)
    value_bets, likely, insufficient, no_edge = [], [], [], []
    for fx in fixtures:
        label = f"{fx.get('home', '')} - {fx.get('away', '')}"
        try:
            pred = predict(fx["home"], fx["away"], fx.get("odds"))
        except UnknownTeam as exc:
            insufficient.append({"match": label, "reason": f"équipe inconnue du moteur : {exc}"})
            continue
        except (EngineError, KeyError) as exc:
            insufficient.append({"match": label, "reason": str(exc) or "données insuffisantes"})
            continue
        if QUALITY_RANK[pred["data_quality"]] < floor:
            insufficient.append({"match": label, "reason": f"qualité {pred['data_quality']} sous le minimum"})
            continue
        for sel in pred["value"]:
            entry = {"match": f"{pred['home']} - {pred['away']}", **sel}
            if sel["ev_interval"][0] > 0:
                value_bets.append(entry)
            else:
                no_edge.append(entry)
            if sel["model_probability"] >= 0.5:
                likely.append(entry)
    value_bets.sort(key=lambda e: e["rank_key"], reverse=True)
    likely.sort(key=lambda e: e["model_probability"], reverse=True)
    return {
        "best_value_bets": value_bets[:top],
        "highest_probability": likely[:top],
        "insufficient_data": insufficient,
        "no_statistical_edge": len(no_edge),
        "scanned": len(fixtures),
    }
