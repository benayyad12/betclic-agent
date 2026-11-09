"""Interface web locale de BetclicAgent : matchs du jour et analyse des cotes.

Lancement :
    python -m uvicorn betclic_agent.api:app --app-dir src --host 127.0.0.1 --port 8000
Puis ouvrir http://127.0.0.1:8000
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .client_ci import MAX_PAGES, PAGE_SIZE, BetclicMarketClient
from .config import Settings
from pydantic import BaseModel, Field

from .engine import service as engine_service
from .engine import validate
from .engine.errors import EngineError, UnknownTeam
from .markets import analyze_markets
from . import soccergpt
from .timing import IN_PLAY, PAST, UNKNOWN, UPCOMING, match_status

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="BetclicAgent")
logger = logging.getLogger(__name__)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class ScanFixture(BaseModel):
    home: str = Field(min_length=2, max_length=80)
    away: str = Field(min_length=2, max_length=80)
    odds: dict[str, float] | None = None


class ScanRequest(BaseModel):
    fixtures: list[ScanFixture] = Field(min_length=1, max_length=200)
    min_quality: Literal["faible", "moyenne", "élevée"] = "faible"
    top: int = Field(default=10, ge=1, le=50)


@app.get("/engine", include_in_schema=False)
def engine_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "engine.html")

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
MATCH_CACHE_TTL_S = 15
MATCH_STALE_MAX_S = 120

# Cache des matchs complets par (sport, marché) ; seuls les résultats sans page en échec sont mis en cache.
_cache: dict[tuple[str, str], tuple[float, dict]] = {}
_match_cache: dict[tuple[int, str], tuple[float, dict]] = {}
_refreshing: set[tuple[int, str]] = set()
PREFETCH_LIMIT = 40
# Pages de la liste du jour récupérées en parallèle (2 max) ; le pré-chargement des cotes reste séquentiel.
LIST_CONCURRENCY = 8
PREFETCH_CONCURRENCY = 8
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


async def _get_collected(
    sport: str, market: str | None, client: BetclicMarketClient, max_age: float = CACHE_TTL_S
) -> tuple[dict, bool]:
    """Renvoie (données, depuis_cache). Un verrou par clé évite deux récupérations simultanées."""
    key = (sport, client.market)

    def fresh() -> dict | None:
        entry = _cache.get(key)
        if entry and time.monotonic() - entry[0] < max_age:
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
    try:
        return await build_today(sport, market, date)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Betclic indisponible : {exc}") from exc


async def build_today(
    sport: str, market: str | None, date: str | None, max_age: float = CACHE_TTL_S
) -> dict:
    """Construit la vue d'une journée (utilisée par REST et par le flux SSE)."""
    client = _client(market)
    day = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data, from_cache = await _get_collected(sport, market, client, max_age)

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


COLLECT_INTERVAL_S = 15
SSE_KEEPALIVE_S = 20


class _Hub:
    """Un collecteur par (sport, marché, jour), partagé par tous les onglets ouverts.
    Il interroge Betclic une fois par cycle, puis ne pousse que les changements."""

    def __init__(self) -> None:
        self.subscribers: set[asyncio.Queue] = set()
        self.latest: tuple[str, dict] | None = None
        self.signature: str | None = None
        self.error: str | None = None


_hubs: dict[tuple[str, str, str], _Hub] = {}


def _publish(hub: _Hub, event: str, payload: dict) -> None:
    """Dernier message gagne : une file pleine (client lent) est vidée avant d'y poser le nouveau."""
    if event == "data":
        hub.latest = (event, payload)
    for q in hub.subscribers:
        if q.full():
            q.get_nowait()
        q.put_nowait((event, payload))


async def _run_hub(key: tuple[str, str, str], hub: _Hub) -> None:
    sport, market, day = key
    try:
        while hub.subscribers:
            try:
                payload = await build_today(sport, market, day, max_age=COLLECT_INTERVAL_S)
                signature = json.dumps(
                    {"matches": payload["matches"], "counts": payload["counts"]}, sort_keys=True
                )
                if signature != hub.signature:
                    hub.signature = signature
                    _publish(hub, "data", payload)
                else:
                    _publish(hub, "tick", {"checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
                hub.error = None
            except Exception as exc:
                message = str(exc) or exc.__class__.__name__
                if message != hub.error:
                    hub.error = message
                    _publish(hub, "error", {"detail": f"Betclic indisponible : {message}"})
            await asyncio.sleep(COLLECT_INTERVAL_S)
    finally:
        if _hubs.get(key) is hub:
            del _hubs[key]


@app.get("/api/stream/{sport}")
async def stream(
    sport: str,
    request: Request,
    date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    market: Market | None = None,
) -> StreamingResponse:
    """Flux Server-Sent Events : l'état courant part immédiatement, puis chaque changement est poussé."""
    name = (market or Settings().market).upper()
    day = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = (sport, name, day)
    hub = _hubs.get(key)
    if hub is None:
        hub = _hubs[key] = _Hub()
        task = asyncio.create_task(_run_hub(key, hub))
        _background.add(task)
        task.add_done_callback(_background.discard)

    queue: asyncio.Queue = asyncio.Queue(maxsize=1)
    hub.subscribers.add(queue)
    if hub.latest is not None:
        queue.put_nowait(hub.latest)
    elif hub.error is not None:
        queue.put_nowait(("error", {"detail": f"Betclic indisponible : {hub.error}"}))

    async def events():
        try:
            while not await request.is_disconnected():
                try:
                    event, payload = await asyncio.wait_for(queue.get(), timeout=SSE_KEEPALIVE_S)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        finally:
            hub.subscribers.discard(queue)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/predict")
async def predict_endpoint(home: str = Query(min_length=2, max_length=80),
                           away: str = Query(min_length=2, max_length=80)) -> dict:
    """Analyse GPT-4o (SoccerGPT) pour home vs away. Sortie de modèle : à valider avant usage."""
    if home.strip().lower() == away.strip().lower():
        raise HTTPException(status_code=400, detail="Les deux équipes doivent être différentes.")
    try:
        return await asyncio.to_thread(soccergpt.predict, home.strip(), away.strip())
    except soccergpt.SoccerGPTError as exc:
        status = 503 if "manquante" in str(exc) else 404 if "introuvable" in str(exc) else 502
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Analyse indisponible : {exc}") from exc


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


@app.get("/api/engine/match")
async def engine_match(home: str = Query(min_length=2, max_length=80),
                       away: str = Query(min_length=2, max_length=80),
                       odds_home: float | None = Query(None, gt=1, lt=1000),
                       odds_draw: float | None = Query(None, gt=1, lt=1000),
                       odds_away: float | None = Query(None, gt=1, lt=1000)) -> dict:
    """Prédictions du moteur Dixon-Coles pour un match, avec value bets si les cotes 1X2 sont fournies."""
    odds = None
    if odds_home and odds_draw and odds_away:
        odds = {"home": odds_home, "draw": odds_draw, "away": odds_away}
    try:
        return await asyncio.to_thread(engine_service.predict, home, away, odds)
    except UnknownTeam as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except EngineError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        logger.error("Données moteur absentes : %s", exc)
        raise HTTPException(status_code=503, detail="Données historiques absentes : placez data/Matches.csv.") from exc
    except Exception as exc:
        logger.exception("Erreur moteur pour %s vs %s", home, away)
        raise HTTPException(status_code=500, detail=f"Erreur interne du moteur ({type(exc).__name__}) : voir les logs serveur.") from exc


@app.get("/api/engine/validation")
def engine_validation() -> dict:
    """Dernier rapport de validation walk-forward (hors échantillon). Lancer scripts/validate_engine.py pour le générer."""
    report = validate.load_report()
    if report is None:
        raise HTTPException(status_code=404, detail="Rapport absent : lancez scripts/validate_engine.py.")
    return report


@app.post("/api/engine/scan")
async def engine_scan(payload: ScanRequest) -> dict:
    """Daily Center : scanne une liste de rencontres sans forcer de recommandation."""
    fixtures = [f.model_dump() for f in payload.fixtures]
    return await asyncio.to_thread(engine_service.scan, fixtures, payload.min_quality, payload.top)


async def _markets_for(match_id: int, name: str) -> dict:
    """Cache « servir vite, rafraîchir ensuite » : moins de 30 s → cache direct ; entre 30 s et
    MATCH_STALE_MAX_S → cache renvoyé immédiatement + rafraîchissement en arrière-plan.
    Ainsi le clic est instantané sans jamais attendre Betclic sur un match déjà vu."""
    key = (match_id, name)
    entry = _match_cache.get(key)
    if entry:
        age = time.monotonic() - entry[0]
        if age < MATCH_CACHE_TTL_S:
            return entry[1]
        if age < MATCH_STALE_MAX_S:
            if key not in _refreshing:
                _refreshing.add(key)
                task = asyncio.create_task(_refresh_match(key))
                _background.add(task)
                task.add_done_callback(_background.discard)
            return entry[1]
    return await _fetch_match(key)


async def _fetch_match(key: tuple[int, str]) -> dict:
    match_id, name = key
    markets = await asyncio.to_thread(lambda: _client(name).get_main_markets(match_id))
    if markets:
        _match_cache[key] = (time.monotonic(), markets)
    return markets


async def _refresh_match(key: tuple[int, str]) -> None:
    try:
        await _fetch_match(key)
    except Exception:
        pass
    finally:
        _refreshing.discard(key)



async def _prefetch_markets(match_ids: list[int], name: str) -> None:
    """Charge en arrière-plan les marchés des matchs affichés, PREFETCH_CONCURRENCY à la fois
    (2 max), espacés de REQUEST_DELAY_S. Les erreurs sont ignorées (optimisation, pas obligation)."""
    delay = Settings().request_delay_s
    sem = asyncio.Semaphore(PREFETCH_CONCURRENCY)

    async def one(match_id: int) -> None:
        async with sem:
            try:
                await _markets_for(match_id, name)
            except Exception:
                pass
            await asyncio.sleep(delay)

    await asyncio.gather(*(one(m) for m in match_ids))
