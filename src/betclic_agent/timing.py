"""Statut temporel d'un match.

Le backend Betclic n'expose pas de statut fiable : son indicateur « live » ne
signifie pas que le match est en cours (vérifié le 17/09/2026 — 18 matchs marqués
« live » avaient un coup d'envoi à venir, et 12 matchs dont l'heure était passée
n'étaient pas marqués). Le seul repère sûr est donc l'heure de coup d'envoi.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
