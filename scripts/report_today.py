"""Rapport lisible des matchs du jour à partir du JSON produit par ``today --json``.

Usage :
    python scripts/report_today.py                            # data/matches_today.json
    python scripts/report_today.py --competitions "Ligue Europa"
    python scripts/report_today.py --full                     # toutes les compétitions
    python scripts/report_today.py --status upcoming          # filtre : upcoming|in_play|past

Le rapport est produit à partir des données déjà collectées : aucune requête réseau.
Le statut (à venir / en cours / terminé) est estimé d'après l'heure de coup d'envoi —
le backend Betclic ne fournit aucun statut fiable.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from betclic_agent.timing import (  # noqa: E402
    IN_PLAY,
    PAST,
    UNKNOWN,
    UPCOMING,
    local_time,
    match_status,
)

# Compétitions mises en avant par défaut (affiches les plus suivies)
DEFAULT_HIGHLIGHTS = (
    "Ligue Europa",
    "Ligue des Champions",
    "Coupe Monde",
    "Russie Premier",
    "Egypte",
    "Égypte",
    "Algérie",
    "Tunisie",
    "Maroc",
    "Asian Games",
    "Afrique",
    "CAN",
    "UEFA",
)

STATUS_LABELS = {
    IN_PLAY: "en cours (estimé)",
    UPCOMING: "à venir",
    PAST: "terminés",
    UNKNOWN: "heure inconnue",
}


def load(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def print_header(data: dict, counts: dict[str, int], streamed: int) -> None:
    print(f"Matchs {data.get('sport', 'football')} du {data['day']} — marché {data['market']}")
    parts = [f"{len(data['matches'])} matchs"]
    parts += [f"{counts[status]} {STATUS_LABELS[status]}" for status in (IN_PLAY, UPCOMING, PAST) if counts[status]]
    parts.append(f"{streamed} avec diffusion Betclic")
    print("  " + " · ".join(parts))


def print_section(title: str, matches: list[dict], with_id: bool = False) -> None:
    print(f"\n=== {title} ({len(matches)}) ===")
    for m in sorted(matches, key=lambda m: (m["date"] or "", m["competition"] or "")):
        flag = "📺" if m["live_stream"] else "  "
        suffix = f"   (id={m['id']})" if with_id else ""
        print(f"  {local_time(m['date'])} {flag} {(m['competition'] or '—'):<32} {m['name']}{suffix}")


def print_by_competition(matches: list[dict]) -> None:
    grouped: dict[str, list[dict]] = collections.defaultdict(list)
    for m in matches:
        grouped[m["competition"] or "Compétition inconnue"].append(m)
    print(f"\n=== TOUTES LES COMPÉTITIONS ({len(grouped)}) ===")
    for competition in sorted(grouped):
        print(f"\n[{competition}]")
        for m in sorted(grouped[competition], key=lambda m: m["date"] or ""):
            print(f"  {local_time(m['date'])}  {m['name']}   (id={m['id']})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Rapport des matchs du jour (hors ligne).")
    parser.add_argument("--input", default="data/matches_today.json")
    parser.add_argument("--competitions", action="append", default=None,
                        help="Filtre d'affiche (répétable). Défaut : liste intégrée.")
    parser.add_argument("--full", action="store_true", help="Affiche toutes les compétitions")
    parser.add_argument("--status", choices=[UPCOMING, IN_PLAY, PAST], default=None,
                        help="N'affiche qu'un statut (le rapport complet par défaut)")
    parser.add_argument("--now", default=None,
                        help="Heure de référence ISO pour le calcul du statut (tests)")
    args = parser.parse_args()

    path = Path(args.input)
    if not path.exists():
        print(f"Fichier introuvable : {path} — lancez d'abord « today --json {path} ».", file=sys.stderr)
        return 1

    data = load(path)
    matches = data["matches"]
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    by_status: dict[str, list[dict]] = collections.defaultdict(list)
    for m in matches:
        by_status[match_status(m["date"], now)].append(m)
    counts = {status: len(by_status.get(status, [])) for status in (IN_PLAY, UPCOMING, PAST, UNKNOWN)}
    streamed = sum(1 for m in matches if m["live_stream"])

    print_header(data, counts, streamed)

    if args.status:
        print_section(STATUS_LABELS[args.status].upper(), by_status.get(args.status, []), with_id=True)
        return 0

    print_section("EN COURS (estimé)", by_status.get(IN_PLAY, []), with_id=True)
    needles = tuple(args.competitions) if args.competitions else DEFAULT_HIGHLIGHTS
    upcoming = [
        m for m in by_status.get(UPCOMING, [])
        if any(n.lower() in (m["competition"] or "").lower() for n in needles)
    ]
    print_section("AFFICHES À VENIR", upcoming, with_id=True)
    if args.full:
        print_by_competition(matches)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
