"""Interface en ligne de commande de BetclicAgent.

Commandes :
    sports                     Liste les sports disponibles
    matches <sport>            Liste les matchs à venir et en direct
    today [sport]              Matchs du jour (pagination complète, groupés par compétition)
    analyze <match_id>         Analyse les marchés d'un match (marges)
    scan [sport]               Veille complète : scan + détection d'opportunités
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .client_ci import MAX_PAGES, BetclicMarketClient
from .config import Settings
from .markets import analyze_markets, find_opportunities
from .notifier import alert_opportunities, print_report
from .timing import IN_PLAY, PAST, UNKNOWN, UPCOMING, local_time, match_status


def cmd_sports(client: BetclicMarketClient, _args) -> int:
    print("Sports disponibles :")
    for name in client.get_sports_names():
        print(f"  - {name}")
    return 0


def cmd_matches(client: BetclicMarketClient, args) -> int:
    matches = client.collect_matches(args.sport, limit=args.limit)
    print(f"\n{len(matches)} match(s) {args.sport} — marché {client.market}\n")
    for m in matches:
        flag = "📺" if m["live_stream"] else "  "
        print(f"{flag} {m['name']} [{m['competition']}] — {m['date'][:16]}")
        print(f"     id={m['id']}")
    return 0


def cmd_today(client: BetclicMarketClient, args) -> int:
    day = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"\nCollecte des matchs {args.sport} (marché {client.market})…")
    every = client.collect_all_matches(
        args.sport,
        max_pages=args.max_pages or MAX_PAGES,
        until_day=day,
        on_page=lambda n, total, size: print(f"  page {n} : {total} matchs cumulés"),
    )
    matches = client.sort_by_time(client.matches_on(every, day))

    now = datetime.now(timezone.utc)
    buckets: dict[str, list[dict]] = {IN_PLAY: [], UPCOMING: [], PAST: [], UNKNOWN: []}
    for match in matches:
        buckets[match_status(match["date"], now)].append(match)
    streamed = sum(1 for m in matches if m["live_stream"])

    print(f"\n{len(matches)} match(s) {args.sport} le {day} (UTC = heure locale) — marché {client.market}")
    print(f"   {len(buckets[IN_PLAY])} en cours (estimé) | {len(buckets[UPCOMING])} à venir"
          f" | {len(buckets[PAST])} terminés | {streamed} avec diffusion Betclic")
    print("   ⓘ statut estimé d'après l'heure de coup d'envoi : le backend ne fournit pas de statut fiable\n")

    sections = (
        ("EN COURS (estimé)", buckets[IN_PLAY]),
        ("À VENIR", buckets[UPCOMING]),
        ("TERMINÉS", buckets[PAST]),
        ("HEURE INDISPONIBLE", buckets[UNKNOWN]),
    )
    for title, items in sections:
        if not items:
            continue
        print(f"=== {title} ({len(items)}) ===")
        by_competition: dict[str, list[dict]] = {}
        for match in items:
            by_competition.setdefault(match["competition"] or "Compétition inconnue", []).append(match)
        for competition in sorted(by_competition):
            print(f"[{competition}]")
            for match in by_competition[competition]:
                flag = "📺" if match["live_stream"] else "  "
                print(f"  {local_time(match['date'])} {flag} {match['name']}   (id={match['id']})")
        print()

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(
            json.dumps(
                {"day": day, "market": client.market, "sport": args.sport, "matches": matches},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"Export JSON : {args.json}")
    return 0


def cmd_analyze(client: BetclicMarketClient, args) -> int:
    markets = client.get_main_markets(args.match_id)
    if not markets:
        print("Aucun marché retourné pour ce match.")
        return 1
    analyzed = analyze_markets(markets)
    print(f"\nAnalyse du match {args.match_id} — marché {client.market}\n")
    for item in analyzed:
        margin = f"{item['margin_pct']}%" if item["margin_pct"] is not None else "n/a"
        flag = "✅" if item["reliable"] else "⚠️ "
        print(f"  {flag} {item['market']}  ({item['outcomes']} issues, marge {margin})")
        for sel in item["selections"][:5]:
            print(f"      {sel['name']}: {sel['odds']}")
    print("\n✅ = marge interprétable (marché exhaustif) | ⚠️ = non interprétable")
    return 0


def cmd_scan(client: BetclicMarketClient, args) -> int:
    settings = args.settings
    matches = client.collect_matches(args.sport, limit=args.limit)
    opportunities: list[dict] = []
    for idx, match in enumerate(matches, start=1):
        try:
            markets = client.get_main_markets(match["id"])
        except Exception as exc:  # un match indisponible ne doit pas arrêter le scan
            print(f"  [{idx}/{len(matches)}] {match['name']} — erreur: {exc}")
            continue
        analyzed = analyze_markets(markets)
        for item in find_opportunities(analyzed, settings.min_margin_pct, settings.max_margin_pct):
            opportunities.append({**item, "match": match["name"]})
        print(f"  [{idx}/{len(matches)}] {match['name']} — {len(analyzed)} marchés")
        time.sleep(settings.request_delay_s)

    opportunities.sort(key=lambda o: o["margin_pct"])
    print_report(opportunities, len(matches), settings.market)
    if opportunities:
        sent = alert_opportunities(opportunities, len(matches), settings)
        print(f"   → alerte Telegram : {'envoyée' if sent else 'non configurée/échouée'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="betclic-agent",
        description="Veille des cotes Betclic (collecte et analyse — jamais de placement de pari).",
    )
    parser.add_argument("--market", default=None, help="Marché ciblé : CI (défaut), FR, SN")
    sub = parser.add_subparsers(dest="command", required=True)

    p_sports = sub.add_parser("sports", help="Liste les sports disponibles")
    p_sports.set_defaults(func=cmd_sports)

    p_matches = sub.add_parser("matches", help="Liste les matchs d'un sport")
    p_matches.add_argument("sport", nargs="?", default="football")
    p_matches.add_argument("--limit", type=int, default=20)
    p_matches.set_defaults(func=cmd_matches)

    p_today = sub.add_parser("today", help="Matchs du jour (collecte complète par pagination)")
    p_today.add_argument("sport", nargs="?", default="football")
    p_today.add_argument("--date", default=None, help="Jour ciblé AAAA-MM-JJ (défaut : aujourd'hui, UTC)")
    p_today.add_argument("--max-pages", type=int, default=None, help="Plafond de pages (40 matchs/page)")
    p_today.add_argument("--json", default=None, help="Exporte la liste dans un fichier JSON")
    p_today.set_defaults(func=cmd_today)

    p_analyze = sub.add_parser("analyze", help="Analyse les marchés d'un match")
    p_analyze.add_argument("match_id", type=int)
    p_analyze.set_defaults(func=cmd_analyze)

    p_scan = sub.add_parser("scan", help="Veille complète avec détection d'opportunités")
    p_scan.add_argument("sport", nargs="?", default="football")
    p_scan.add_argument("--limit", type=int, default=10)
    p_scan.set_defaults(func=cmd_scan)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings()
    if args.market:
        settings.market = args.market.upper()
    args.settings = settings

    client = BetclicMarketClient(market=settings.market, settings=settings)
    print(f"BetclicAgent — marché {client.market} ({client.origin})")
    try:
        return args.func(client, args)
    except KeyboardInterrupt:
        print("\nInterrompu.")
        return 130
    except Exception as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
