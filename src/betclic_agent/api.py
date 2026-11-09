"""Interface web locale de BetclicAgent : matchs du jour et analyse des cotes.

Lancement :
    python -m uvicorn betclic_agent.api:app --app-dir src --host 127.0.0.1 --port 8000
Puis ouvrir http://127.0.0.1:8000
"""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .client_ci import MAX_PAGES, PAGE_SIZE, BetclicMarketClient
from .config import Settings
from .markets import analyze_markets
from .timing import IN_PLAY, PAST, UNKNOWN, UPCOMING, match_status

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="BetclicAgent")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

Market = Literal["CI", "FR", "SN"]


def _client(market: str | None) -> BetclicMarketClient:
    """Un client par thread et par marché : la session HTTP (connexions keep-alive) est réutilisée
    au lieu d'être recréée à chaque requête. Les threads ne partagent pas de session."""
    name = (market or Settings().market).upper()
    clients = getattr(_local, "clients", None)
    if clients is None:
        clients = _local.clients = {}
    if name not in clients:
        clients[name] = BetclicMarketClient(market=name, settings=Settings())
    return clients[name]


_local = threading.local()
PAGE_RETRIES = 2
CACHE_TTL_S = 60
MATCH_CACHE_TTL_S = 30

# Cache des matchs complets par (sport, marché) ; seuls les résultats sans page en échec sont mis en cache.
_cache: dict[tuple[str, str], tuple[float, dict]] = {}
_match_cache: dict[tuple[int, str], tuple[float, dict]] = {}
PREFETCH_LIMIT = 40
# Pages de la liste du jour récupérées en parallèle (2 max) ; le pré-chargement des cotes reste séquentiel.
LIST_CONCURRENCY = 2
_background: set[asyncio.Task] = set()

# Pool dédié : assez de threads pour que toutes les pages d'une journée partent en même temps
# (le pool par défaut est limité à CPU+4 threads).
_pool = ThreadPoolExecutor(max_workers=MAX_PAGES, thread_name_prefix="betclic-page")


def _run_page(sport: str, market: str | None, offset: int, size: int = PAGE_SIZE) -> asyncio.Future:
    return asyncio.get_running_loop().run_in_executor(_pool, _fetch_page, sport, market, offset, size)
_locks: dict[tuple[str, str], asyncio.Lock] = {}


PROBE_SIZE = 200


def _fetch_page(sport: str, market: str | None, offset: int, size: int = PAGE_SIZE) -> dict:
    """Appel bloquant exécuté dans un thread, avec des nouvelles tentatives espacées."""
    last_exc: Exception | None = None
    for attempt in range(PAGE_RETRIES + 1):
        try:
            return _client(market).get_matches_page(sport, offset=offset, size=size)
        except Exception as exc:
            last_exc = exc
            if attempt < PAGE_RETRIES:
                time.sleep(0.5 * (attempt + 1))
    raise last_exc  # type: ignore[misc]


async def _collect_concurrent(sport: str, market: str | None, client: BetclicMarketClient) -> dict:
    """Récupère les pages en parallèle. La 1re requête demande une grande page ;
    si le serveur la plafonne, on pagine avec la taille réellement renvoyée."""
    first = await asyncio.get_running_loop().run_in_executor(
        _pool, _fetch_page, sport, market, 0, PROBE_SIZE
    )
    total = first.get("total") or 0
    step = len(first.get("matches", [])) or PAGE_SIZE
    max_matches = MAX_PAGES * PAGE_SIZE
    last_offset = min(total, max_matches) if total else max_matches
    offsets = list(range(step, last_offset, step))
    sem = asyncio.Semaphore(LIST_CONCURRENCY)

    async def bounded(o: int):
        async with sem:
            return await _run_page(sport, market, o, step)

    results = await asyncio.gather(*(bounded(o) for o in offsets), return_exceptions=True)
    pages = [first] + [r for r in results if not isinstance(r, BaseException)]
    failed = sum(isinstance(r, BaseException) for r in results)

    seen: set = set()
    collected: list[dict] = []
    for page in pages:
        for m in page.get("matches", []):
            if m.id not in seen:
                seen.add(m.id)
                collected.append(client._to_dict(m, sport))
    return {
        "matches": collected,
        "pages": len(pages) + failed,
        "failed_pages": failed,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


async def _get_collected(sport: str, market: str | None, client: BetclicMarketClient) -> tuple[dict, bool]:
    """Renvoie (données, depuis_cache). Un verrou par clé évite deux récupérations simultanées."""
    key = (sport, client.market)

    def fresh() -> dict | None:
        entry = _cache.get(key)
        if entry and time.monotonic() - entry[0] < CACHE_TTL_S:
            return entry[1]
        return None

    if (hit := fresh()) is not None:
        return hit, True
    async with _locks.setdefault(key, asyncio.Lock()):
        if (hit := fresh()) is not None:
            return hit, True
        data = await _collect_concurrent(sport, market, client)
        if data["failed_pages"] == 0:
            _cache[key] = (time.monotonic(), data)
        return data, False


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/sports")
def sports(market: Market | None = None) -> dict:
    client = _client(market)
    return {"market": client.market, "sports": client.get_sports_names()}


@app.get("/api/today/{sport}")
async def today(
    sport: str,
    date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    market: Market | None = None,
) -> dict:
    """Matchs du jour (UTC = heure locale en CI), groupés par statut estimé. Pages récupérées en parallèle."""
    client = _client(market)
    day = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        data, from_cache = await _get_collected(sport, market, client)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Betclic indisponible : {exc}") from exc

    matches = client.sort_by_time(client.matches_on([dict(m) for m in data["matches"]], day))
    now = datetime.now(timezone.utc)
    for match in matches:
        match["status"] = match_status(match["date"], now)

    # Pré-chargement des cotes des matchs encore ouverts (plafonné) : la réponse part sans attendre.
    open_ids = [m["id"] for m in matches if m["status"] in (UPCOMING, IN_PLAY)][:PREFETCH_LIMIT]
    if open_ids:
        task = asyncio.create_task(_prefetch_markets(open_ids, client.market))
        _background.add(task)
        task.add_done_callback(_background.discard)

    return {
        "market": client.market,
        "sport": sport,
        "day": day,
        "matches": matches,
        "source": {
            "fetched_at": data["fetched_at"],
            "from_cache": from_cache,
            "pages": data["pages"],
            "failed_pages": data["failed_pages"],
            "complete": data["failed_pages"] == 0,
        },
        "counts": {
            "in_play": sum(m["status"] == IN_PLAY for m in matches),
            "upcoming": sum(m["status"] == UPCOMING for m in matches),
            "past": sum(m["status"] == PAST for m in matches),
            "unknown": sum(m["status"] == UNKNOWN for m in matches),
        },
    }


@app.get("/api/analyze/{match_id}")
async def analyze(match_id: int, market: Market | None = None) -> dict:
    """Marchés d'un match (cache 30 s par match et marché)."""
    name = (market or Settings().market).upper()
    try:
        markets = await _markets_for(match_id, name)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Betclic indisponible : {exc}") from exc
    if not markets:
        raise HTTPException(status_code=404, detail="Aucun marché pour ce match.")
    return {
        "match_id": match_id,
        "market": name,
        "markets": analyze_markets(markets),
    }


async def _markets_for(match_id: int, name: str) -> dict:
    """Renvoie les marchés depuis le cache, sinon les récupère (et les met en cache)."""
    key = (match_id, name)
    entry = _match_cache.get(key)
    if entry and time.monotonic() - entry[0] < MATCH_CACHE_TTL_S:
        return entry[1]
    markets = await asyncio.to_thread(lambda: _client(name).get_main_markets(match_id))
    if markets:
        _match_cache[key] = (time.monotonic(), markets)
    return markets


async def _prefetch_markets(match_ids: list[int], name: str) -> None:
    """Charge en arrière-plan les marchés des matchs affichés, un par un, espacés de
    REQUEST_DELAY_S : une seule requête à la fois vers Betclic. Les erreurs sont ignorées
    (optimisation, pas obligation)."""
    delay = Settings().request_delay_s
    for match_id in match_ids:
        try:
            await _markets_for(match_id, name)
        except Exception:
            pass
        await asyncio.sleep(delay)
