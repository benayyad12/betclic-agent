"""Compare les cotes d'un même match entre deux marchés Betclic (CI vs FR).

Utilité : un « boost » sur une cote se voit en comparant au prix de référence. Si le même
match, servi par le même backend, propose une cote nettement meilleure sur un marché que
sur l'autre, l'écart est un signal (boost côté régulateur, ou erreur de prix).

Usage : python compare_markets.py <match_id>
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from betclic_agent.client_ci import BetclicMarketClient  # noqa: E402
from betclic_agent.markets import market_margin  # noqa: E402

MARKETS = ("CI", "FR", "SN")


def fetch(market: str, match_id: int) -> dict[str, list[dict]]:
    client = BetclicMarketClient(market=market)
    try:
        return client.get_main_markets(match_id)
    except Exception as exc:
        return {"__erreur__": [{"name": str(exc)[:90], "odds": 0.0}]}


def main() -> int:
    match_id = int(sys.argv[1])
    per_market = {market: fetch(market, match_id) for market in MARKETS}

    names = set()
    for markets in per_market.values():
        names.update(markets)

    for name in sorted(names):
        rows = []
        for market in MARKETS:
            selections = per_market[market].get(name)
            if not selections:
                rows.append(f"{market}: —")
                continue
            margin = market_margin(selections)
            odds = "/".join(f"{s['odds']}" for s in selections[:3])
            rows.append(f"{market}: {odds} (marge {margin}%)")
        print(f"### {name}")
        for row in rows:
            print(f"    {row}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
