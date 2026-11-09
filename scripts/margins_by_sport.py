"""Compare les marges Betclic CI entre sports, uniquement sur des marchés exhaustifs.

Deux modes :

    # 1. Recenser les marchés réellement exposés par sport (noms + nombre d'issues)
    python scripts/margins_by_sport.py --census --sample 2

    # 2. Mesurer les marges des marchés exhaustifs reconnus
    python scripts/margins_by_sport.py --sample 3 --json data/margins_by_sport.json

Règle de fiabilité (voir docs/ARCHITECTURE.md) : une marge n'a de sens que si les issues
sont **mutuellement exclusives et exhaustives**. Elle n'est donc calculée que pour les
marchés dont le NOM et le NOMBRE DE SÉLECTIONS correspondent à une structure connue
(``EXHAUSTIVE_BY_SPORT``). Un marché à deux sélections n'est PAS forcément exhaustif :
le football seul en donnait quatre avec des marges de -90 % sans ce garde-fou.
"""

from __future__ import annotations

import argparse
import collections
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from betclic_agent.client_ci import BetclicMarketClient  # noqa: E402
from betclic_agent.config import Settings  # noqa: E402
from betclic_agent.markets import market_margin  # noqa: E402

DEFAULT_SPORTS = (
    "football",
    "tennis",
    "basketball",
    "rugby",
    "handball",
    "hockey",
    "volleyball",
    "baseball",
    "mma",
)

# Marchés exhaustifs reconnus, par sport : (motif de nom, nombre d'issues attendu).
# À compléter depuis la sortie de `--census` — jamais par supposition.
EXHAUSTIVE_BY_SPORT: dict[str, tuple[tuple[str, int], ...]] = {
    "football": (("résultat du match", 3), ("les 2 équipes marquent", 2)),
    "tennis": (("vainqueur du match", 2),),
    "basketball": (("vainqueur du match", 2), ("résultat du match", 2)),
    "rugby": (("vainqueur du match", 2), ("résultat du match", 3)),
    "handball": (("vainqueur du match", 2), ("résultat du match", 3)),
    "hockey": (("vainqueur du match", 2), ("résultat du match", 3)),
    "volleyball": (("vainqueur du match", 2),),
    "baseball": (("vainqueur du match", 2),),
    "mma": (("vainqueur du combat", 2), ("vainqueur du match", 2)),
}


def is_reliable(sport: str, market_name: str, selections: list[dict]) -> bool:
    """Vrai si la marge de ce marché est interprétable (nom + nombre d'issues connus)."""
    lowered = market_name.lower()
    return any(
        pattern in lowered and len(selections) == expected
        for pattern, expected in EXHAUSTIVE_BY_SPORT.get(sport, ())
    )


def collect(client: BetclicMarketClient, sport: str, sample: int) -> list[tuple[dict, dict]]:
    """Retourne [(match, {marché: sélections})] pour un échantillon du sport."""
    matches = client.collect_matches(sport, limit=sample)
    return [(match, client.get_main_markets(match["id"])) for match in matches]


def census(client: BetclicMarketClient, sport: str, sample: int) -> dict:
    """Recense les marchés observés (noms, issues) sans calculer de marge."""
    observed: collections.Counter = collections.Counter()
    for _match, markets in collect(client, sport, sample):
        for name, selections in markets.items():
            observed[(name, len(selections))] += 1
    return {"sport": sport, "markets": observed.most_common()}


def measure(client: BetclicMarketClient, sport: str, sample: int) -> dict:
    """Calcule les marges des seuls marchés exhaustifs reconnus."""
    margins: list[float] = []
    market_names: collections.Counter = collections.Counter()
    rejected = collections.Counter()
    details: list[dict] = []

    for match, markets in collect(client, sport, sample):
        for name, selections in markets.items():
            if not is_reliable(sport, name, selections):
                rejected[(name, len(selections))] += 1
                continue
            margin = market_margin(selections, expected_outcomes=len(selections))
            if margin is None:
                continue
            margins.append(margin)
            market_names[name] += 1
            details.append({"match": match["name"], "market": name, "margin_pct": margin})

    return {
        "sport": sport,
        "matches_sampled": sample,
        "reliable_markets": len(margins),
        "median_margin": round(statistics.median(margins), 2) if margins else None,
        "mean_margin": round(statistics.fmean(margins), 2) if margins else None,
        "min_margin": min(margins) if margins else None,
        "max_margin": max(margins) if margins else None,
        "market_names": market_names.most_common(5),
        "rejected_markets": rejected.most_common(5),
        "details": details,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Marges Betclic CI par sport (marchés exhaustifs).")
    parser.add_argument("--sample", type=int, default=3, help="Matchs analysés par sport")
    parser.add_argument("--sports", nargs="*", default=list(DEFAULT_SPORTS))
    parser.add_argument("--market", default="CI")
    parser.add_argument("--census", action="store_true", help="Recenser les marchés au lieu de mesurer")
    parser.add_argument("--json", default=None)
    args = parser.parse_args()

    settings = Settings()
    settings.market = args.market.upper()
    client = BetclicMarketClient(market=settings.market, settings=settings)

    if args.census:
        print(f"Census des marchés — marché {client.market}, {args.sample} match(s) par sport\n")
        for sport in args.sports:
            try:
                result = census(client, sport, args.sample)
            except Exception as exc:  # un sport indisponible ne doit pas arrêter le census
                print(f"### {sport} : erreur {exc}")
                continue
            print(f"### {sport} ({len(result['markets'])} marchés distincts)")
            for (name, issues), count in result["markets"]:
                reliable = "✅" if is_reliable(sport, name, [{}] * issues) else "  "
                print(f"   {reliable} {name} — {issues} issue(s) ×{count}")
            print()
        return 0

    print(f"Marché {client.market} — {args.sample} match(s) par sport")
    print("(marges calculées uniquement sur les marchés exhaustifs reconnus)\n")

    results = []
    for sport in args.sports:
        try:
            result = measure(client, sport, args.sample)
        except Exception as exc:
            print(f"{sport:<12} erreur : {exc}")
            continue
        results.append(result)
        if result["reliable_markets"]:
            print(f"{sport:<12} {result['reliable_markets']} marché(s) fiable(s)"
                  f" · marge médiane {result['median_margin']}%"
                  f" [{result['min_margin']} – {result['max_margin']}]")
        else:
            print(f"{sport:<12} aucun marché exhaustif reconnu — lancer --census pour compléter la table")
        time.sleep(settings.request_delay_s)

    ranked = sorted((r for r in results if r["median_margin"] is not None), key=lambda r: r["median_margin"])
    if ranked:
        print("\nClassement (marge médiane croissante = sport le plus généreux pour le parieur) :")
        for rank, r in enumerate(ranked, start=1):
            print(f"  {rank}. {r['sport']:<12} {r['median_margin']}%  (moyenne {r['mean_margin']}%,"
                  f" {r['reliable_markets']} marchés)")
    else:
        print("\nAucune marge exploitable : compléter EXHAUSTIVE_BY_SPORT depuis le census.")

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nExport JSON : {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
