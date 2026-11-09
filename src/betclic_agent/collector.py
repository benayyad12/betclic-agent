"""Collecteur unique : une requête à la fois, délai configurable, persistance validée.

Pas de parallélisme vers Betclic. Chaque réponse passe par ``validation`` avant
écriture ; un échec est journalisé (``logging``) puis le run continue avec le match suivant.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime

from sqlalchemy.orm import sessionmaker

from .client_ci import BetclicMarketClient
from .db.models import CollectionRun
from .db.repository import add_odds_snapshots, finish_run, start_run, upsert_match, utcnow
from .timing import parse_kickoff
from .validation import ValidationError, validate_markets, validate_match_ids

log = logging.getLogger(__name__)


def collect_once(
    client: BetclicMarketClient,
    session_factory: sessionmaker,
    sport: str = "football",
    limit: int = 40,
    delay_s: float = 1.5,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Exécute une collecte complète et retourne le bilan du run."""
    with session_factory() as session:
        run = start_run(session, sport, client.market)
        session.commit()
        run_id = run.id

    try:
        matches = validate_match_ids(client.collect_matches(sport, limit=limit))
    except Exception as exc:
        log.error("collecte des matchs échouée : %s", exc)
        with session_factory() as session:
            finish_run(session, session.get(CollectionRun, run_id), "failed", error=str(exc))
            session.commit()
        return {"run_id": run_id, "status": "failed", "matches_seen": 0, "snapshots": 0, "rejected": 0}

    snapshots = 0
    rejected = 0
    stored = 0
    for match in matches:
        now = utcnow()
        try:
            markets = validate_markets(match["id"], client.get_main_markets(match["id"]))
        except ValidationError as exc:
            log.warning("réponse refusée : %s", exc)
            rejected += 1
        except Exception as exc:
            log.error("marchés indisponibles pour %s : %s", match["id"], exc)
            rejected += 1
        else:
            with session_factory() as session:
                run = session.get(CollectionRun, run_id)
                db_match = upsert_match(
                    session,
                    betclic_match_id=match["id"],
                    sport=sport,
                    market_code=client.market,
                    competition=match.get("competition") or "",
                    name=match["name"],
                    kickoff=_kickoff(match.get("date")),
                    now=now,
                )
                snapshots += add_odds_snapshots(session, run, db_match, markets, now)
                session.commit()
            stored += 1
        sleep(delay_s)

    status = "ok" if stored or not rejected else "failed"
    with session_factory() as session:
        finish_run(session, session.get(CollectionRun, run_id), status, matches_seen=stored)
        session.commit()
    return {
        "run_id": run_id,
        "status": status,
        "matches_seen": stored,
        "snapshots": snapshots,
        "rejected": rejected,
    }


def _kickoff(value: str | None) -> datetime | None:
    parsed = parse_kickoff(value)
    return parsed.replace(tzinfo=None) if parsed else None
