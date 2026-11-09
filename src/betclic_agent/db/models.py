"""Schéma de persistance : matchs, marchés, historique des cotes et runs de collecte.

Principes :
- ``odds_snapshots`` est en ajout seul : chaque observation est conservée, jamais écrasée.
- Un match qui disparaît du flux Betclic n'est jamais supprimé (``last_seen_at`` reste figé).
- Les dates sont stockées en UTC.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class CollectionRun(Base):
    __tablename__ = "collection_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sport: Mapped[str] = mapped_column(String(64))
    market_code: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16), default="running")  # running | ok | failed
    matches_seen: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(primary_key=True)
    betclic_match_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    sport: Mapped[str] = mapped_column(String(64))
    market_code: Mapped[str] = mapped_column(String(8))
    competition: Mapped[str] = mapped_column(String(200), default="")
    name: Mapped[str] = mapped_column(String(300))
    kickoff: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime)

    markets: Mapped[list[Market]] = relationship(back_populates="match")
    result: Mapped[MatchResult | None] = relationship(back_populates="match", uselist=False)


class Market(Base):
    __tablename__ = "markets"
    __table_args__ = (UniqueConstraint("match_id", "name", name="uq_market_per_match"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), index=True)
    name: Mapped[str] = mapped_column(String(300))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime)

    match: Mapped[Match] = relationship(back_populates="markets")


class OddsSnapshot(Base):
    __tablename__ = "odds_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("collection_runs.id"), index=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), index=True)
    selection: Mapped[str] = mapped_column(String(300))
    odds: Mapped[float] = mapped_column(Float)
    observed_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class MatchResult(Base):
    __tablename__ = "match_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), unique=True)
    home_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime)

    match: Mapped[Match] = relationship(back_populates="result")


class AlertSent(Base):
    __tablename__ = "alerts_sent"

    id: Mapped[int] = mapped_column(primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(128), unique=True)
    channel: Mapped[str] = mapped_column(String(16))
    sent_at: Mapped[datetime] = mapped_column(DateTime)
