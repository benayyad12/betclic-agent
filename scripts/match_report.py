"""Dossier de match : cotes, marges, probabilités dé-margées et cohérence interne.

Usage :
    python scripts/match_report.py <match_id> [--market CI]

Ce que le rapport répond :
  - quelle marge Betclic prélève sur chaque marché (donc quel coût il faut battre) ;
  - quelles probabilités le marché implique (1X2, BTTS) ;
  - si le bookmaker se contredit entre marché principal et score exact (incohérence
    interne exploitable) — ou s'il est cohérent, auquel cas il n'y a pas d'angle gratuit.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from betclic_agent.analysis import demargin, score_aggregates  # noqa: E402
from betclic_agent.client_ci import BetclicMarketClient  # noqa: E402
from betclic_agent.markets import market_margin  # noqa: E402

# Au-delà de ce seuil, le marché est trop chargé pour être jouable sérieusement.
HEAVY_MARGIN_PCT = 15.0


def main_odds(markets: dict[str, list[dict]]) -> dict[str, dict]:
    """Repère les marchés principaux interprétables (1X2 et BTTS)."""
    found = {}
    for name, selections in markets.items():
        lowered = name.lower()
        if "résultat du match" in lowered and len(selections) == 3 and "extra" not in lowered:
            found["1X2"] = {"name": name, "selections": selections}
        elif "les 2 équipes marquent" in lowered and len(selections) == 2:
            found["BTTS"] = {"name": name, "selections": selections}
    return found


def order_1x2(selections: list[dict]) -> tuple[str, str, str]:
    """Retourne (domicile, nul, extérieur) en vérifiant l'ordre renvoyé par le bookmaker.

    Betclic renvoie l'ordre standard dom/nul/ext ; on le vérifie plutôt que de le supposer,
    et on retombe sur un ordre explicite si le libellé du nul n'est pas au milieu.
    """
    names = [s["name"] for s in selections]
    draw_index = next((i for i, n in enumerate(names) if "nul" in n.lower()), None)
    if draw_index == 1:
        return names[0], names[1], names[2]
    if draw_index is None:
        return names[0], "Nul", names[-1]
    ordered = [n for n in names if n != names[draw_index]]
    return ordered[0], names[draw_index], ordered[-1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Dossier de match (cotes, marges, cohérence).")
    parser.add_argument("match_id", type=int)
    parser.add_argument("--market", default="CI")
    args = parser.parse_args()

    client = BetclicMarketClient(market=args.market.upper())
    markets = client.get_main_markets(args.match_id)
    if not markets:
        print("Aucun marché retourné.")
        return 1

    print(f"=== Match {args.match_id} — marché {client.market} : {len(markets)} marchés ===\n")

    principals = main_odds(markets)
    implied: dict[str, dict[str, float]] = {}
    for key, item in principals.items():
        margin = market_margin(item["selections"])
        probabilities = demargin(item["selections"])
        implied[key] = probabilities
        print(f"[{key}] {item['name']} — marge {margin:.2f}% (il faut battre le marché de {margin:.1f} %)")
        for selection in item["selections"]:
            print(f"    {selection['name']:<16} cote {selection['odds']:<6} "
                  f"→ {probabilities[selection['name']] * 100:.1f} %")
        print()

    exact = next((s for name, s in markets.items() if "score exact" in name.lower()), None)
    if exact:
        aggregates = score_aggregates(exact)
        if aggregates:
            margin = market_margin(exact)
            print(f"[Score exact] {len(exact)} issues, marge {margin:.2f}% — agrégats dé-margés :")
            print(f"    1 : {aggregates['home'] * 100:.1f} %   X : {aggregates['draw'] * 100:.1f} %   "
                  f"2 : {aggregates['away'] * 100:.1f} %")
            print(f"    BTTS oui : {aggregates['btts_yes'] * 100:.1f} %   +2,5 buts : {aggregates['over_2_5'] * 100:.1f} %")
            print("    top scores : " + ", ".join(
                f"{k} {v * 100:.1f} %" for k, v in sorted(aggregates["scores"].items(), key=lambda kv: -kv[1])[:6]
            ))
            if "1X2" in implied:
                home, draw, away = order_1x2(principals["1X2"]["selections"])
                pairs = (
                    ("1", implied["1X2"][home], aggregates["home"]),
                    ("X", implied["1X2"][draw], aggregates["draw"]),
                    ("2", implied["1X2"][away], aggregates["away"]),
                )
                gaps = " | ".join(f"{label} {exact_p * 100:.1f} % vs {score_p * 100:.1f} %" for label, exact_p, score_p in pairs)
                worst = max(abs(exact_p - score_p) for _label, exact_p, score_p in pairs) * 100
                verdict = "cohérent" if worst < 3 else "INCOHÉRENCE à examiner"
                print(f"\n    Contrôle 1X2 vs score exact : {gaps} → écart max {worst:.1f} pt : {verdict}")
            print()

    heavy = sorted(
        ((name, market_margin(sels), len(sels)) for name, sels in markets.items() if market_margin(sels) is not None),
        key=lambda item: item[1],
        reverse=True,
    )
    print("Marchés les plus chargés (à éviter) :")
    for name, margin, issues in heavy[:5]:
        print(f"    {margin:>8.2f} %  ({issues} issues)  {name}")
    print(f"\nRappel : au-delà de {HEAVY_MARGIN_PCT:.0f} % de marge, aucun pari sérieux n'est défendable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
