"""Interface web locale de BetclicAgent : matchs du jour et analyse des cotes.

Lancement :
    python -m uvicorn betclic_agent.api:app --app-dir src --host 127.0.0.1 --port 8000
Puis ouvrir http://127.0.0.1:8000
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .client_ci import MAX_PAGES, BetclicMarketClient
from .config import Settings
from .markets import analyze_markets
from .timing import IN_PLAY, PAST, UNKNOWN, UPCOMING, match_status

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="BetclicAgent")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

Market = Literal["CI", "FR", "SN"]


def _client(market: str | None) -> BetclicMarketClient:
    settings = Settings()
    return BetclicMarketClient(market=market or settings.market, settings=settings)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/sports")
def sports(market: Market | None = None) -> dict:
    client = _client(market)
    return {"market": client.market, "sports": client.get_sports_names()}


@app.get("/api/today/{sport}")
def today(
    sport: str,
    date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    market: Market | None = None,
) -> dict:
    """Matchs du jour (UTC = heure locale en CI), groupés par statut estimé."""
    client = _client(market)
    day = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        collected = client.collect_all_matches(
            sport, max_pages=MAX_PAGES, until_day=day
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Betclic indisponible : {exc}") from exc

    matches = client.sort_by_time(client.matches_on(collected, day))
    now = datetime.now(timezone.utc)
    for match in matches:
        match["status"] = match_status(match["date"], now)

    return {
        "market": client.market,
        "sport": sport,
        "day": day,
        "matches": matches,
        "counts": {
            "in_play": sum(m["status"] == IN_PLAY for m in matches),
            "upcoming": sum(m["status"] == UPCOMING for m in matches),
            "past": sum(m["status"] == PAST for m in matches),
            "unknown": sum(m["status"] == UNKNOWN for m in matches),
        },
    }


@app.get("/api/analyze/{match_id}")
def analyze(match_id: int, market: Market | None = None) -> dict:
    client = _client(market)
    try:
        markets = client.get_main_markets(match_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Betclic indisponible : {exc}") from exc
    if not markets:
        raise HTTPException(status_code=404, detail="Aucun marché pour ce match.")
    return {
        "match_id": match_id,
        "market": client.market,
        "markets": analyze_markets(markets),
    }
