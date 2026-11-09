"""Notifications : affichage console formaté + envoi Telegram optionnel."""

from __future__ import annotations

import urllib.parse
import urllib.request

from .config import Settings


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
            return resp.status == 200
    except Exception:
        return False


def alert_opportunities(opportunities: list[dict], scanned: int, settings: Settings) -> bool:
    """Alerte (Telegram) sur la liste d'opportunités. Retourne True si envoyée."""
    if not opportunities:
        return False
    lines = [f"🎯 <b>Betclic {settings.market}</b> — {scanned} matchs scannés", ""]
    for item in opportunities[:10]:
        sel = ", ".join(f"{s['name']} @{s['odds']}" for s in item["selections"][:3])
        lines.append(f"• <b>{item['match']}</b> — {item['market']} ({item['margin_pct']}%)")
        lines.append(f"   {sel}")
    return send_telegram("\n".join(lines), settings)
