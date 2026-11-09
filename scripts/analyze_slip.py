"""Analyse un panier de paris Betclic collé depuis le site (DOM HTML).

Usage :
    python scripts/analyze_slip.py data/attachments/pasted-17-09.txt [--json data/matches_today.json]

Ce que le script calcule, pour un combiné :
  - la cote totale et la probabilité implicite brute ;
  - la probabilité dé-margée (le « juste prix ») ;
  - l'espérance de gain et le coût cumulé de la marge ;
  - la probabilité de gagner le combiné, sélection par sélection.

Les matchs sont résolus depuis le JSON du jour (pas de recherche réseau) ; les cotes sont
récupérées sur l'API Betclic pour chaque sélection.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from betclic_agent.analysis import demargin  # noqa: E402
from betclic_agent.client_ci import BetclicMarketClient  # noqa: E402

CARD = re.compile(
    r'cardEventName">\s*<span class="ellipsis">\s*([^<]+?)\s*</span>'
    r'.*?cardMarketLabel">([^<]+)</div>'
    r'<div class="progressiveBettingSlip_cardMarketValue">([^<]+)</div>',
    re.S,
)


def parse_slip(path: Path) -> list[dict]:
    """Extrait les sélections du DOM collé : [(match, sélection, marché)]."""
    raw = path.read_text(encoding="utf-8", errors="ignore")
    return [
        {"match": match.strip(), "selection": label.strip(), "market": market.strip()}
        for match, label, market in CARD.findall(raw)
    ]


def resolve_matches(slip: list[dict], matches: list[dict]) -> None:
    """Associe chaque sélection à un match du jour (par nom d'équipe)."""
    for item in slip:
        parts = [p.strip().lower() for p in item["match"].split(" - ")]
        item["match_id"] = None
        for match in matches:
            name = (match["name"] or "").lower()
            if len(parts) == 2 and parts[0] in name and parts[1] in name:
                item["match_id"] = match["id"]
                break


def find_odds(client: BetclicMarketClient, item: dict) -> None:
    """Récupère la cote de la sélection et la marge de son marché."""
    if not item.get("match_id"):
        return
    try:
        markets = client.get_main_markets(item["match_id"])
    except Exception as exc:  # match terminé/suspendu ou réponse vide : on continue
        item["error"] = str(exc)[:60]
        return
    wanted_market = item["market"].lower().replace("é", "e")
    for name, selections in markets.items():
        if wanted_market[:24] not in name.lower().replace("é", "e"):
            continue
        probabilities = demargin(selections)
        for selection in selections:
            if selection["name"].strip().lower() == item["selection"].strip().lower():
                item["odds"] = selection["odds"]
                item["market_name"] = name
                item["market_size"] = len(selections)
                total = sum(1 / s["odds"] for s in selections)
                item["margin_pct"] = round((total - 1) * 100, 2)
                item["fair_prob"] = probabilities.get(selection["name"])
                return


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyse d'un panier de paris Betclic.")
    parser.add_argument("slip")
    parser.add_argument("--json", default="data/matches_today.json")
    args = parser.parse_args()

    slip = parse_slip(Path(args.slip))
    if not slip:
        print("Aucune sélection détectée dans le fichier.")
        return 1

    day = json.loads(Path(args.json).read_text(encoding="utf-8"))
    resolve_matches(slip, day["matches"])

    client = BetclicMarketClient(market=day.get("market", "CI"))
    print(f"{len(slip)} sélection(s) — résolution des cotes en cours…\n")
    for index, item in enumerate(slip, start=1):
        find_odds(client, item)
        odds = item.get("odds")
        status = f"cote {odds}" if odds else ("match non trouvé" if not item["match_id"] else "sélection introuvable")
        print(f"{index:>2}. {item['match']:<42} {item['selection']:<22} [{item['market'][:34]}] → {status}")

    priced = [item for item in slip if item.get("odds")]
    print(f"\n{len(priced)}/{len(slip)} sélections chiffrées")
    if not priced:
        return 1

    combined = 1.0
    book_prob = 1.0
    fair_prob = 1.0
    margin_cost = 1.0
    for item in priced:
        combined *= item["odds"]
        book_prob *= 1 / item["odds"]
        fair_prob *= item["fair_prob"]
        margin_cost *= 1 - item["margin_pct"] / 100

    print(f"\n=== Combiné ===")
    print(f"  cote totale            : {combined:,.2f}")
    print(f"  proba implicite (book) : {book_prob*100:.4f} %  (1 chance sur {1/book_prob:,.0f})")
    print(f"  proba dé-margée        : {fair_prob*100:.4f} %  (1 chance sur {1/fair_prob:,.0f})")
    print(f"  marge cumulée          : {(1-margin_cost)*100:.1f} %")
    print(f"  espérance de gain      : {(fair_prob*combined - 1)*100:+.1f} %")
    for stake in (100, 1000):
        print(f"  mise {stake} F → gain {(stake*combined):,.0f} F si tout passe ; "
              f"espérance {(stake*(fair_prob*combined-1)):+,.0f} F")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
