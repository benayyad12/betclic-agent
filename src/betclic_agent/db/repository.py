"""Écriture et lecture de la persistance. Toutes les fonctions reçoivent une session SQLAlchemy."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base, CollectionRun, Market, Match, OddsSnapshot


def make_engine(url: str) -> Engine:
    return create_engine(url, future=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def create_schema(engine: Engine) -> None:
    """Création directe (tests et usage local). En production : ``alembic upgrade head``."""
    Base.metadata.create_all(engine)


def utcnow() -> datetime:
    """Horodatage UTC naïf, cohérent avec les colonnes ``DateTime`` sans fuseau."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def start_run(session: Session, sport: str, market_code: str, now: datetime | None = None) -> CollectionRun:
    run = CollectionRun(
        started_at=now or utcnow(), sport=sport, market_code=market_code, status="running"
    )
    session.add(run)
    session.flush()
    return run


def finish_run(
    session: Session,
    run: CollectionRun,
    status: str,
    matches_seen: int = 0,
    error: str | None = None,
    now: datetime | None = None,
) -> None:
    run.status = status
    run.matches_seen = matches_seen
    run.error = error[:500] if error else None
    run.finished_at = now or utcnow()
    session.flush()


def upsert_match(
    session: Session,
    *,
    betclic_match_id: int,
    sport: str,
    market_code: str,
    competition: str,
    name: str,
    kickoff: datetime | None,
    now: datetime,
) -> Match:
    """Crée le match s'il est inconnu, sinon met à jour ses métadonnées et ``last_seen_at``."""
    match = session.scalar(select(Match).where(Match.betclic_match_id == betclic_match_id))
    if match is None:
        match = Match(
            betclic_match_id=betclic_match_id,
            sport=sport,
            market_code=market_code,
            competition=competition,
            name=name,
            kickoff=kickoff,
            first_seen_at=now,
            last_seen_at=now,
        )
        session.add(match)
    else:
        match.competition = competition or match.competition
        match.name = name or match.name
        match.kickoff = kickoff or match.kickoff
        match.last_seen_at = now
    session.flush()
    return match


def _get_or_create_market(session: Session, match: Match, name: str, now: datetime) -> Market:
    market = session.scalar(select(Market).where(Market.match_id == match.id, Market.name == name))
    if market is None:
        market = Market(match_id=match.id, name=name, first_seen_at=now, last_seen_at=now)
        session.add(market)
    else:
        market.last_seen_at = now
    session.flush()
    return market


def add_odds_snapshots(
    session: Session,
    run: CollectionRun,
    match: Match,
    markets: dict[str, list[dict]],
    now: datetime,
) -> int:
    """Ajoute une ligne par sélection observée. Ne modifie jamais un snapshot existant."""
    written = 0
    for market_name, selections in markets.items():
        market = _get_or_create_market(session, match, market_name, now)
        for selection in selections:
            session.add(
                OddsSnapshot(
                    run_id=run.id,
                    market_id=market.id,
                    selection=selection["name"],
                    odds=float(selection["odds"]),
                    observed_at=now,
                )
            )
            written += 1
    session.flush()
    return written
