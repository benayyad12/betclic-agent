"""Smoke test : vérifie que la chaîne complète fonctionne (collecte → analyse).

Usage :
    python scripts/smoke_test.py            # marché CI (défaut)
    python scripts/smoke_test.py --market FR
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from betclic_agent.client_ci import BetclicMarketClient  # noqa: E402
from betclic_agent.config import Settings  # noqa: E402
from betclic_agent.markets import analyze_markets, find_low_margin_markets  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", default="CI")
    parser.add_argument("--sport", default="football")
    args = parser.parse_args()

    settings = Settings()
    settings.market = args.market.upper()
    client = BetclicMarketClient(market=settings.market, settings=settings)

    print(f"1. Connexion Betclic — marché {client.market} ({client.origin})")
    matches = client.collect_matches(args.sport, limit=5)
    if not matches:
        print("   ❌ Aucun match retourné")
        return 1
    print(f"   ✅ {len(matches)} matchs récupérés")
    for m in matches[:3]:
        print(f"      - {m['name']} [{m['competition']}] diffusion={m['live_stream']}")

    print("\n2. Récupération des marchés du premier match")
    first = matches[0]
    markets = client.get_main_markets(first["id"])
    print(f"   ✅ {len(markets)} marchés pour « {first['name']} »")

    print("\n3. Analyse des marges (fiables = marchés exhaustifs seulement)")
    analyzed = analyze_markets(markets)
    reliable = [m for m in analyzed if m["reliable"]]
    print(f"   {len(reliable)} marché(s) fiable(s) sur {len(analyzed)}")
    for item in reliable[:3]:
        margin = f"{item['margin_pct']}%" if item["margin_pct"] is not None else "n/a"
        print(f"   ✅ {item['market']} : marge {margin} ({item['outcomes']} issues)")
    for item in [m for m in analyzed if not m["reliable"]][:2]:
        margin = f"{item['margin_pct']}%" if item["margin_pct"] is not None else "n/a"
        print(f"   ⚠️ {item['market']} : marge {margin} — non interprétable (issues non exhaustives)")

    print("\n4. Détection (seuils configurés, marchés fiables uniquement)")
    found = find_low_margin_markets(analyzed, settings.min_margin_pct, settings.max_margin_pct)
    print(f"   ✅ {len(found)} marché(s) dans la fourchette {settings.min_margin_pct}-{settings.max_margin_pct}%")

    print("\n✅ Smoke test OK — la chaîne collecte → analyse → détection fonctionne.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
