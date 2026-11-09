"""Interface web locale de BetclicAgent : matchs du jour et analyse des cotes.

Lancement :
    python -m uvicorn betclic_agent.api:app --app-dir src --host 127.0.0.1 --port 8000
Puis ouvrir http://127.0.0.1:8000
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .client_ci import MAX_PAGES, PAGE_SIZE, BetclicMarketClient
from .config import Settings
from pydantic import BaseModel, Field

from .engine import service as engine_service
from .engine import validate
from .engine.errors import EngineError, UnknownTeam
from .markets import analyze_markets
from . import soccergpt, staking
from .timing import (
    IN_PLAY,
    PAST,
    UNKNOWN,
    UPCOMING,
    kickoff_local_day,
    market_today,
    market_zone,
    match_status,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
LIST_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "list_cache"
logger = logging.getLogger(__name__)


def _warm_engine() -> None:
    try:
        engine_service.warm()
    except Exception:
        logger.info("préchargement moteur reporté", exc_info=True)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    threading.Thread(target=_warm_engine, name="engine-warm", daemon=True).start()
    yield


app = FastAPI(title="BetclicAgent", lifespan=_lifespan)
app.add_middleware(GZipMiddleware, minimum_size=800)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class ScanFixture(BaseModel):
    home: str = Field(min_length=2, max_length=80)
    away: str = Field(min_length=2, max_length=80)
    odds: dict[str, float] | None = None


class ScanRequest(BaseModel):
    fixtures: list[ScanFixture] = Field(min_length=1, max_length=200)
    min_quality: Literal["faible", "moyenne", "élevée"] = "faible"
    top: int = Field(default=10, ge=1, le=50)


class ForecastFixture(BaseModel):
    home: str = Field(min_length=2, max_length=80)
    away: str = Field(min_length=2, max_length=80)
    id: str | None = Field(default=None, max_length=40)


class ForecastBatch(BaseModel):
    fixtures: list[ForecastFixture] = Field(min_length=1, max_length=120)


class StakeRequest(BaseModel):
    bankroll: float = Field(gt=0, le=1_000_000_000)
    prob: float = Field(ge=0, le=1)
    odds: float = Field(gt=1, lt=1000)
    fraction: float = Field(default=0.25, gt=0, le=1)
    cap_pct: float = Field(default=0.02, gt=0, le=0.25)


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
LIST_FRESH_S = 60
MATCH_CACHE_TTL_S = 15
MATCH_STALE_MAX_S = 120

# Cache des matchs complets par (sport, marché). Un résultat partiel reste servable, mais périmé.
_cache: dict[tuple[str, str], tuple[float, dict]] = {}
_list_tasks: dict[tuple[str, str], asyncio.Task] = {}
_match_cache: dict[tuple[int, str], tuple[float, dict]] = {}
_refreshing: set[tuple[int, str]] = set()
PREFETCH_LIMIT = 0
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


def _list_key(sport: str, market: str) -> tuple[str, str]:
    return (sport, market.upper())


def _read_list_disk(key: tuple[str, str]) -> dict | None:
    path = LIST_CACHE_DIR / f"{key[0]}-{key[1]}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("matches"), list):
        return None
    return data


def _write_list_disk(key: tuple[str, str], data: dict) -> None:
    try:
        LIST_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = LIST_CACHE_DIR / f"{key[0]}-{key[1]}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        logger.info("cache disque indisponible pour %s", key)


def _store_list(key: tuple[str, str], data: dict) -> None:
    fresh = data.get("failed_pages") == 0
    stamp = time.monotonic() if fresh else time.monotonic() - CACHE_TTL_S - 1
    _cache[key] = (stamp, data)
    if data.get("matches") or fresh:
        _write_list_disk(key, data)


def _peek_collected(sport: str, market: str) -> dict | None:
    """Dernière liste connue, mémoire puis disque. Ne contacte jamais Betclic."""
    key = _list_key(sport, market)
    entry = _cache.get(key)
    if entry:
        return entry[1]
    disk = _read_list_disk(key)
    if disk is None:
        return None
    _cache[key] = (time.monotonic() - CACHE_TTL_S - 1, disk)
    return disk


def _list_age(sport: str, market: str) -> float | None:
    entry = _cache.get(_list_key(sport, market))
    if entry is None:
        return None
    return time.monotonic() - entry[0]


def _annotate_forecasts(sport: str, data: dict) -> dict:
    """Ajoute la prédiction déjà calculée. N'attend pas le chargement du modèle."""
    if sport != "football" or not engine_service.is_warm():
        return data
    matches = []
    for match in data.get("matches", []):
        item = dict(match)
        if not item.get("forecast"):
            parts = engine_service.split_match_name(item.get("name") or "")
            if parts:
                try:
                    item["forecast"] = engine_service.forecast(*parts)
                except (UnknownTeam, EngineError) as exc:
                    item["forecast"] = {"error": str(exc)}
        matches.append(item)
    return {**data, "matches": matches}


def _short_upstream(exc: Exception) -> str:
    text = str(exc) or exc.__class__.__name__
    if any(token in text for token in ("begmedia", "NameResolution", "Failed to resolve", "timed out", "Timeout")):
        return "Betclic est injoignable. Les dernières données restent affichées."
    return text if len(text) <= 180 else text[:180] + "…"


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
        try:
            data = await _collect_concurrent(sport, market, client)
        except Exception:
            entry = _cache.get(key)
            if entry:
                return entry[1], True
            raise
        data = _annotate_forecasts(sport, data)
        if data["matches"] or data["failed_pages"] == 0:
            _store_list(key, data)
        return data, False


def _schedule_list_refresh(sport: str, market: str) -> None:
    """Une seule collecte en vol par sport et marché. La requête HTTP n'attend pas."""
    key = _list_key(sport, market)
    task = _list_tasks.get(key)
    if task and not task.done():
        return

    async def run() -> None:
        try:
            await _get_collected(sport, market, _client(market), max_age=LIST_FRESH_S)
        except Exception:
            logger.info("rafraîchissement liste %s ignoré", key, exc_info=True)
        finally:
            current = _list_tasks.get(key)
            if current is asyncio.current_task():
                _list_tasks.pop(key, None)

    task = asyncio.create_task(run())
    _list_tasks[key] = task
    _background.add(task)
    task.add_done_callback(_background.discard)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/sports")
def sports(market: Market | None = None) -> dict:
    client = _client(market)
    return {"market": client.market, "sports": client.get_sports_names()}


@app.get("/api/clock")
def clock(market: Market | None = None) -> dict:
    """Aujourd'hui dans le fuseau du marché. Sert à aligner « demain » sur le calendrier Betclic."""
    name = (market or Settings().market).upper()
    return {"market": name, "today": market_today(name), "timezone": str(market_zone(name))}


@app.get("/api/today/{sport}")
async def today(
    sport: str,
    date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    market: Market | None = None,
) -> dict:
    """Matchs du jour. Répond tout de suite avec le dernier état, puis rafraîchit en arrière-plan."""
    try:
        return await build_today(sport, market, date, wait=False)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=_short_upstream(exc)) from exc


def _empty_today(client: BetclicMarketClient, sport: str, day: str) -> dict:
    return {
        "market": client.market,
        "sport": sport,
        "day": day,
        "timezone": str(market_zone(client.market)),
        "local_today": market_today(client.market),
        "matches": [],
        "pending": True,
        "source": {
            "fetched_at": None,
            "from_cache": False,
            "pages": 0,
            "failed_pages": 0,
            "complete": False,
        },
        "counts": {"in_play": 0, "upcoming": 0, "past": 0, "unknown": 0},
    }


def _shape_today(
    client: BetclicMarketClient, sport: str, day: str, data: dict, from_cache: bool
) -> dict:
    copied = [dict(m) for m in data["matches"]]
    matches = client.sort_by_time(
        [m for m in copied if kickoff_local_day(m.get("date"), client.market) == day]
    )
    now = datetime.now(timezone.utc)
    for match in matches:
        match["status"] = match_status(match["date"], now)
    return {
        "market": client.market,
        "sport": sport,
        "day": day,
        "timezone": str(market_zone(client.market)),
        "local_today": market_today(client.market),
        "matches": matches,
        "pending": False,
        "source": {
            "fetched_at": data.get("fetched_at"),
            "from_cache": from_cache,
            "pages": data.get("pages") or 0,
            "failed_pages": data.get("failed_pages") or 0,
            "complete": data.get("failed_pages") == 0,
        },
        "counts": {
            "in_play": sum(m["status"] == IN_PLAY for m in matches),
            "upcoming": sum(m["status"] == UPCOMING for m in matches),
            "past": sum(m["status"] == PAST for m in matches),
            "unknown": sum(m["status"] == UNKNOWN for m in matches),
        },
    }


async def build_today(
    sport: str, market: str | None, date: str | None, max_age: float = CACHE_TTL_S, wait: bool = True
) -> dict:
    """Construit la vue d'une journée.

    ``wait=False`` (chemin HTTP) ne contacte pas Betclic : cache mémoire, sinon disque,
    sinon une coquille ``pending``. La collecte part en tâche de fond.
    """
    client = _client(market)
    day = date or market_today(client.market)
    if not wait:
        hit = _peek_collected(sport, client.market)
        age = _list_age(sport, client.market)
        if hit is None or age is None or age >= LIST_FRESH_S:
            _schedule_list_refresh(sport, client.market)
        if hit is None:
            return _empty_today(client, sport, day)
        return _shape_today(client, sport, day, hit, from_cache=True)

    data, from_cache = await _get_collected(sport, market, client, max_age)
    # Pré-chargement des cotes des matchs encore ouverts : la réponse du flux n'attend pas ce détail.
    shaped = _shape_today(client, sport, day, data, from_cache)
    open_ids = [m["id"] for m in shaped["matches"] if m["status"] in (UPCOMING, IN_PLAY)][:PREFETCH_LIMIT]
    if open_ids:
        task = asyncio.create_task(_prefetch_markets(open_ids, client.market))
        _background.add(task)
        task.add_done_callback(_background.discard)
    return shaped


COLLECT_INTERVAL_S = 60
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
                message = _short_upstream(exc)
                if message != hub.error:
                    hub.error = message
                    _publish(hub, "error", {"detail": message})
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
    day = date or market_today(name)
    key = (sport, name, day)
    hub = _hubs.get(key)
    if hub is None:
        hub = _hubs[key] = _Hub()
        hit = _peek_collected(sport, name)
        if hit:
            payload = _shape_today(_client(name), sport, day, hit, from_cache=True)
            hub.signature = json.dumps(
                {"matches": payload["matches"], "counts": payload["counts"]}, sort_keys=True
            )
            hub.latest = ("data", payload)
        task = asyncio.create_task(_run_hub(key, hub))
        _background.add(task)
        task.add_done_callback(_background.discard)

    queue: asyncio.Queue = asyncio.Queue(maxsize=1)
    hub.subscribers.add(queue)
    if hub.latest is not None:
        queue.put_nowait(hub.latest)
    elif hub.error is not None:
        queue.put_nowait(("error", {"detail": hub.error}))

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
    except Exception as exc:
        _raise_engine(exc, home, away)


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


def _raise_engine(exc: Exception, home: str, away: str) -> None:
    if isinstance(exc, UnknownTeam):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, EngineError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(exc, FileNotFoundError):
        logger.error("Données moteur absentes : %s", exc)
        raise HTTPException(status_code=503, detail="Données historiques absentes : l'extracteur n'a pas pu obtenir Matches.csv.") from exc
    logger.exception("Erreur moteur pour %s vs %s", home, away)
    raise HTTPException(
        status_code=500,
        detail=f"Erreur interne du moteur ({type(exc).__name__}) : voir les logs serveur.",
    ) from exc


def _forecast_one(home: str, away: str, match_id: str | None = None) -> dict:
    try:
        out = engine_service.forecast(home, away)
    except (UnknownTeam, EngineError) as exc:
        out = {"home": home, "away": away, "error": str(exc)}
    if match_id:
        out["id"] = match_id
    return out


@app.get("/api/engine/forecast")
async def engine_forecast(home: str = Query(min_length=2, max_length=80),
                          away: str = Query(min_length=2, max_length=80)) -> dict:
    """Prédiction ponctuelle : issue, score et buts. Aucun intervalle n'est renvoyé."""
    try:
        return await asyncio.to_thread(engine_service.forecast, home, away)
    except Exception as exc:
        _raise_engine(exc, home, away)


@app.post("/api/engine/forecasts")
async def engine_forecasts(payload: ForecastBatch) -> dict:
    """Prédictions ponctuelles d'une liste de matchs. Une équipe inconnue n'interrompt pas le lot."""
    def run() -> dict:
        return {"forecasts": [_forecast_one(fx.home, fx.away, fx.id) for fx in payload.fixtures]}

    try:
        return await asyncio.to_thread(run)
    except FileNotFoundError as exc:
        _raise_engine(exc, "", "")


@app.get("/api/engine/teams")
async def engine_teams(q: str = Query(min_length=2, max_length=40)) -> dict:
    """Suggestions de noms reconnus par le moteur."""
    try:
        return {"teams": await asyncio.to_thread(engine_service.search_teams, q)}
    except FileNotFoundError as exc:
        _raise_engine(exc, q, "")


@app.post("/api/staking/suggest")
def staking_suggest(payload: StakeRequest) -> dict:
    """Mise suggérée. Aucun pari n'est envoyé."""
    full = staking.kelly_fraction(payload.prob, payload.odds)
    stake = staking.fractional_kelly_stake(
        payload.bankroll, payload.prob, payload.odds, payload.fraction, payload.cap_pct,
    )
    return {
        "stake": stake,
        "flat_stake": staking.flat_stake(payload.bankroll),
        "kelly_full": full,
        "fraction": payload.fraction,
        "cap_pct": payload.cap_pct,
        "placed": False,
    }


@app.get("/api/status")
def app_status() -> dict:
    """État local, sans secret."""
    settings = Settings()
    root = Path(__file__).resolve().parents[2]
    return {
        "market": settings.market,
        "min_margin_pct": settings.min_margin_pct,
        "max_margin_pct": settings.max_margin_pct,
        "telegram_configured": bool(settings.telegram_token and settings.telegram_chat_id),
        "soccergpt_configured": bool(os.getenv("SPORTMONKS_API_TOKEN") and os.getenv("OPENAI_API_KEY")),
        "dataset_present": (root / "data" / "Matches.csv").exists(),
        "validation_present": (root / "data" / "validation_report.json").exists(),
        "automatic_betting": False,
    }


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
