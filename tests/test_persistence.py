"""Persistance : dédoublonnage, historique en ajout seul, matchs disparus conservés, migrations."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from betclic_agent.db.models import Market, Match, OddsSnapshot
from betclic_agent.db.repository import (
    add_odds_snapshots,
    create_schema,
    finish_run,
    make_engine,
    make_session_factory,
    start_run,
    upsert_match,
)

T0 = datetime(2026, 11, 9, 12, 0, 0)
MARKETS = {
    "résultat du match (t. rég)": [
        {"name": "Équipe A", "odds": 2.0},
        {"name": "Nul", "odds": 3.5},
        {"name": "Équipe B", "odds": 3.8},
    ]
}


@pytest.fixture
def session():
    engine = make_engine("sqlite://")
    create_schema(engine)
    with make_session_factory(engine)() as s:
        yield s


def _match(session: Session, now: datetime, competition: str = "Ligue 1") -> Match:
    return upsert_match(
        session,
        betclic_match_id=42,
        sport="football",
        market_code="CI",
        competition=competition,
        name="A - B",
        kickoff=None,
        now=now,
    )


def test_upsert_match_deduplique_par_identifiant_betclic(session):
    first = _match(session, T0)
    second = _match(session, T0 + timedelta(minutes=5))
    session.commit()
    assert first.id == second.id
    assert session.scalar(select(func.count()).select_from(Match)) == 1
    assert second.first_seen_at == T0
    assert second.last_seen_at == T0 + timedelta(minutes=5)


def test_match_absent_du_flux_n_est_pas_supprime(session):
    upsert_match(session, betclic_match_id=42, sport="football", market_code="CI",
                 competition="", name="A - B", kickoff=None, now=T0)
    upsert_match(session, betclic_match_id=43, sport="football", market_code="CI",
                 competition="", name="C - D", kickoff=None, now=T0)
    session.commit()
    # Lors de la collecte suivante, seul le match 42 est renvoyé par Betclic.
    upsert_match(session, betclic_match_id=42, sport="football", market_code="CI",
                 competition="", name="A - B", kickoff=None, now=T0 + timedelta(hours=1))
    session.commit()
    disappeared = session.scalar(select(Match).where(Match.betclic_match_id == 43))
    assert disappeared is not None
    assert disappeared.last_seen_at == T0


def test_snapshots_sont_en_ajout_seul(session):
    match = _match(session, T0)
    run1 = start_run(session, "football", "CI", now=T0)
    add_odds_snapshots(session, run1, match, MARKETS, now=T0)
    run2 = start_run(session, "football", "CI", now=T0 + timedelta(minutes=10))
    written = add_odds_snapshots(session, run2, match, MARKETS, now=T0 + timedelta(minutes=10))
    session.commit()

    assert written == 3
    assert session.scalar(select(func.count()).select_from(OddsSnapshot)) == 6
    # Une seule ligne « marché » par nom, même après deux collectes.
    assert session.scalar(select(func.count()).select_from(Market)) == 1
    run_ids = set(session.scalars(select(OddsSnapshot.run_id)).all())
    assert run_ids == {run1.id, run2.id}


def test_run_enregistre_statut_et_erreur(session):
    run = start_run(session, "football", "CI", now=T0)
    finish_run(session, run, "failed", matches_seen=0, error="x" * 800, now=T0 + timedelta(seconds=3))
    session.commit()
    assert run.status == "failed"
    assert len(run.error) == 500
    assert run.finished_at == T0 + timedelta(seconds=3)


def test_migrations_alembic_creent_le_schema(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    url = f"sqlite:///{tmp_path / 'migr.db'}"
    monkeypatch.setenv("DATABASE_URL", url)  # lu par alembic/env.py via Settings()
    command.upgrade(Config("alembic.ini"), "head")

    tables = set(inspect(make_engine(url)).get_table_names())
    assert {"matches", "markets", "odds_snapshots", "collection_runs", "match_results", "alerts_sent"} <= tables
