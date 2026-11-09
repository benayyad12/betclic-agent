"""Notifications : affichage console formaté + envoi Telegram optionnel."""

from __future__ import annotations

import hashlib
import html
import logging
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .db.models import AlertSent

log = logging.getLogger(__name__)


def format_opportunity(item: dict) -> str:
    """Formate une opportunité détectée pour l'affichage/l'alerte."""
    sel = ", ".join(f"{s['name']} @{s['odds']}" for s in item["selections"][:4])
    return (
        f"[{item['match']}] {item['market']} — marge {item['margin_pct']}%\n"
        f"    {sel}"
    )


def print_report(opportunities: list[dict], scanned: int, market: str) -> None:
    """Affiche le rapport de veille dans la console."""
    print(f"\n{'=' * 70}")
    print(f"VEILLE BETCLIC {market} — {scanned} matchs analysés")
    print("=" * 70)
    if not opportunities:
        print("Aucune opportunité dans les seuils configurés.")
        return
    print(f"{len(opportunities)} marché(s) dans la fourchette cible :\n")
    for item in opportunities:
        print(format_opportunity(item))
        print()


def send_telegram(message: str, settings: Settings) -> bool:
    """Envoie une alerte Telegram si le token et le chat_id sont configurés."""
    if not settings.telegram_token or not settings.telegram_chat_id:
        return False
    url = f"https://api.telegram.org/bot{settings.telegram_token}/sendMessage"
    payload = urllib.parse.urlencode(
        {"chat_id": settings.telegram_chat_id, "text": message, "parse_mode": "HTML"}
    ).encode()
    try:
        with urllib.request.urlopen(url, data=payload, timeout=15) as resp:
            ok = resp.status == 200
    except Exception as exc:
        # Ne jamais afficher l'URL : elle contient le token du bot.
        log.error("Envoi Telegram échoué : %s", type(exc).__name__)
        return False
    if not ok:
        log.error("Telegram a répondu HTTP %s", resp.status)
    return ok


def alert_key(item: dict) -> str:
    """Clé stable d'une alerte : même match, même marché, même marge => même alerte."""
    raw = f"{item['match']}|{item['market']}|{item['margin_pct']}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _already_sent(session: Session, key: str) -> bool:
    return session.scalar(select(AlertSent.id).where(AlertSent.dedupe_key == key)) is not None


def alert_opportunities(
    opportunities: list[dict],
    scanned: int,
    settings: Settings,
    session_factory: sessionmaker[Session] | None = None,
) -> bool:
    """Alerte Telegram sur les opportunités non encore alertées. Retourne True si envoyée.

    Avec ``session_factory``, chaque alerte envoyée est enregistrée dans ``alerts_sent`` et
    les alertes déjà enregistrées sont ignorées. Les valeurs venant des bookmakers sont
    échappées pour le mode HTML de Telegram.
    """
    fresh = opportunities
    if session_factory is not None:
        with session_factory() as session:
            fresh = [i for i in opportunities if not _already_sent(session, alert_key(i))]
    if not fresh:
        return False

    e = html.escape
    lines = [f"🎯 <b>Betclic {e(settings.market)}</b> — {scanned} matchs scannés", ""]
    for item in fresh[:10]:
        sel = ", ".join(f"{e(str(s['name']))} @{e(str(s['odds']))}" for s in item["selections"][:3])
        lines.append(f"• <b>{e(item['match'])}</b> — {e(item['market'])} ({e(str(item['margin_pct']))}%)")
        lines.append(f"   {sel}")
    sent = send_telegram("\n".join(lines), settings)

    if sent and session_factory is not None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with session_factory() as session:
            for item in fresh[:10]:
                session.add(AlertSent(dedupe_key=alert_key(item), channel="telegram", sent_at=now))
            session.commit()
    return sent
