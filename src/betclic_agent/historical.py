"""Données historiques de matchs (licence MIT, xgabora/Club-Football-Match-Data).

Le jeu de données contient résultats, cotes 1X2 pré-match, Elo ClubElo avant match
et forme. Il ne contient ni xG ni cotes Betclic : il sert uniquement de référence
pour le backtest des modèles.
"""

from __future__ import annotations

import csv
import io
import urllib.request
import zipfile
from pathlib import Path
from typing import Iterable

DATASET_ZIP_URL = "https://github.com/xgabora/Club-Football-Match-Data/archive/refs/heads/main.zip"
MATCHES_MEMBER = "Club-Football-Match-Data-main/data/Matches.csv"
DEFAULT_DIR = Path("data")


def download_matches(dest_dir: Path = DEFAULT_DIR, timeout: int = 120) -> Path:
    """Télécharge le CSV des matchs dans ``dest_dir`` (ignoré par git) et renvoie son chemin."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / "Matches.csv"
    if target.exists():
        return target
    with urllib.request.urlopen(DATASET_ZIP_URL, timeout=timeout) as response:
        archive = zipfile.ZipFile(io.BytesIO(response.read()))
    target.write_bytes(archive.read(MATCHES_MEMBER))
    return target


def _float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_matches(lines: Iterable[str]) -> list[dict]:
    """Lit les lignes CSV et ne garde que les matchs exploitables.

    Un match est retenu s'il a un résultat (H/D/A), les trois cotes 1X2 et les deux Elo.
    Les autres lignes sont écartées plutôt que complétées.
    """
    matches = []
    for row in csv.DictReader(lines):
        odds = (_float(row["OddHome"]), _float(row["OddDraw"]), _float(row["OddAway"]))
        elos = (_float(row["HomeElo"]), _float(row["AwayElo"]))
        result = row["FTResult"]
        if result not in ("H", "D", "A") or None in odds or None in elos:
            continue
        matches.append(
            {
                "date": row["MatchDate"],
                "division": row["Division"],
                "home": row["HomeTeam"],
                "away": row["AwayTeam"],
                "home_elo": elos[0],
                "away_elo": elos[1],
                "odds": odds,
                "result": result,
            }
        )
    return sorted(matches, key=lambda m: m["date"])


def load_matches(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as handle:
        return parse_matches(handle)
