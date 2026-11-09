"""Statut temporel d'un match.

Le backend Betclic n'expose pas de statut fiable : son indicateur « live » ne
signifie pas que le match est en cours (vérifié le 17/09/2026 — 18 matchs marqués
« live » avaient un coup d'envoi à venir, et 12 matchs dont l'heure était passée
n'étaient pas marqués). Le seul repère sûr est donc l'heure de coup d'envoi.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# Un match dure ~2 h ; on garde une marge (prolongations, arrêts de jeu).
DEFAULT_DURATION_H = 3

UPCOMING = "upcoming"
IN_PLAY = "in_play"
PAST = "past"
UNKNOWN = "unknown"


def parse_kickoff(value: str | None) -> datetime | None:
    """Convertit une date Betclic (``2026-09-17T19:00:00.0000000Z``) en datetime UTC.

    Le backend renvoie 7 décimales de seconde : illisible pour ``fromisoformat``,
    d'où la lecture explicite des 19 premiers caractères.
    """
    if not value:
        return None
    try:
        return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def match_status(
    kickoff_iso: str | None,
    now: datetime | None = None,
    duration_h: int = DEFAULT_DURATION_H,
) -> str:
    """Statut estimé : ``upcoming``, ``in_play``, ``past`` ou ``unknown``.

    Estimation assumée à partir de l'heure de coup d'envoi (aucun statut fiable
    côté backend) : un match reporté apparaît donc comme ``in_play``/``past``.
    """
    kickoff = parse_kickoff(kickoff_iso)
    if kickoff is None:
        return UNKNOWN
    moment = now or datetime.now(timezone.utc)
    if moment < kickoff:
        return UPCOMING
    if moment < kickoff + timedelta(hours=duration_h):
        return IN_PLAY
    return PAST


def local_time(kickoff_iso: str | None) -> str:
    """Heure de coup d'envoi ``HH:MM`` (UTC = heure locale ivoirienne)."""
    kickoff = parse_kickoff(kickoff_iso)
    return kickoff.strftime("%H:%M") if kickoff else "—"


# Le calendrier affiché doit être celui du marché, pas l'UTC brut.
# Un match à 00:30 à Paris est encore la veille en UTC : sans ce décalage,
# « demain » sur betclic.fr ne correspond pas à la liste.
MARKET_ZONES = {
    "FR": "Europe/Paris",
    "CI": "Africa/Abidjan",
    "SN": "Africa/Dakar",
}


def market_zone(market: str) -> ZoneInfo:
    return ZoneInfo(MARKET_ZONES.get((market or "CI").upper(), "Africa/Abidjan"))


def market_today(market: str, now: datetime | None = None) -> str:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(market_zone(market)).date().isoformat()


def kickoff_local_day(kickoff_iso: str | None, market: str) -> str:
    """Jour civil du coup d'envoi dans le fuseau du marché. Vide si la date est illisible."""
    kickoff = parse_kickoff(kickoff_iso)
    if kickoff is None:
        return ""
    return kickoff.astimezone(market_zone(market)).date().isoformat()
