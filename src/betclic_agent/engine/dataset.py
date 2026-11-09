"""Chargement du jeu historique xgabora/Club-Football-Match-Data (licence MIT).

Sources sous-jacentes : Football-Data.co.uk (résultats, tirs, corners, cartons, cotes)
et ClubElo (Elo). Une valeur vide reste ``None`` : elle n'est jamais remplacée par 0.
"""

from __future__ import annotations

import csv
import io
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

DATASET_ZIP_URL = "https://github.com/xgabora/Club-Football-Match-Data/archive/refs/heads/main.zip"
DEFAULT_PATH = Path(__file__).resolve().parents[3] / "data" / "Matches.csv"
MEMBER = "Club-Football-Match-Data-main/data/Matches.csv"


def _float(value: str | None) -> float | None:
    if value is None or value.strip() == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


@dataclass(frozen=True)
class MatchRecord:
    division: str
    date: date
    home: str
    away: str
    home_elo: float | None
    away_elo: float | None
    ft_home: int
    ft_away: int
    ht_home: int | None
    ht_away: int | None
    home_shots: float | None
    away_shots: float | None
    home_target: float | None
    away_target: float | None
    home_corners: float | None
    away_corners: float | None
    home_cards: float | None
    away_cards: float | None
    odd_home: float | None
    odd_draw: float | None
    odd_away: float | None
    over25: float | None
    under25: float | None
    handicap_size: float | None
    handicap_home: float | None
    handicap_away: float | None


def _cards(row: dict[str, str], side: str) -> float | None:
    yellow = _float(row[f"{side}Yellow"])
    red = _float(row[f"{side}Red"])
    if yellow is None and red is None:
        return None
    return (yellow or 0.0) + (red or 0.0)


def _record(row: dict[str, str]) -> MatchRecord | None:
    try:
        match_date = date.fromisoformat(row["MatchDate"])
    except (KeyError, ValueError):
        return None
    ft_h, ft_a = _float(row["FTHome"]), _float(row["FTAway"])
    if ft_h is None or ft_a is None:
        return None
    ht_h, ht_a = _float(row["HTHome"]), _float(row["HTAway"])
    return MatchRecord(
        division=row["Division"],
        date=match_date,
        home=row["HomeTeam"],
        away=row["AwayTeam"],
        home_elo=_float(row["HomeElo"]),
        away_elo=_float(row["AwayElo"]),
        ft_home=int(ft_h),
        ft_away=int(ft_a),
        ht_home=None if ht_h is None else int(ht_h),
        ht_away=None if ht_a is None else int(ht_a),
        home_shots=_float(row["HomeShots"]),
        away_shots=_float(row["AwayShots"]),
        home_target=_float(row["HomeTarget"]),
        away_target=_float(row["AwayTarget"]),
        home_corners=_float(row["HomeCorners"]),
        away_corners=_float(row["AwayCorners"]),
        home_cards=_cards(row, "Home"),
        away_cards=_cards(row, "Away"),
        odd_home=_float(row["OddHome"]),
        odd_draw=_float(row["OddDraw"]),
        odd_away=_float(row["OddAway"]),
        over25=_float(row["Over25"]),
        under25=_float(row["Under25"]),
        handicap_size=_float(row["HandiSize"]),
        handicap_home=_float(row["HandiHome"]),
        handicap_away=_float(row["HandiAway"]),
    )


def parse_matches(lines) -> list[MatchRecord]:
    """Lit les lignes CSV et renvoie les matchs joués, triés par date."""
    records = [r for r in (_record(row) for row in csv.DictReader(lines)) if r is not None]
    records.sort(key=lambda r: r.date)
    return records


def ensure_dataset(path: Path = DEFAULT_PATH, timeout: int = 60) -> Path:
    """Utilise le fichier déjà extrait, sinon le télécharge. Aucun dépôt manuel."""
    if path.exists() and path.stat().st_size > 0:
        return path
    try:
        return fetch_dataset(path, timeout=timeout)
    except Exception as exc:
        raise FileNotFoundError(f"Matches.csv introuvable et téléchargement impossible : {exc}") from exc


def load_matches(path: Path = DEFAULT_PATH) -> list[MatchRecord]:
    with open(ensure_dataset(path), newline="", encoding="utf-8") as handle:
        return parse_matches(handle)


def fetch_dataset(dest: Path = DEFAULT_PATH, timeout: int = 120) -> Path:
    """Télécharge le CSV des matchs (~15 Mo compressé). À lancer une fois, hors requête web."""
    with urllib.request.urlopen(DATASET_ZIP_URL, timeout=timeout) as response:
        archive = zipfile.ZipFile(io.BytesIO(response.read()))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(archive.read(MEMBER))
    return dest
