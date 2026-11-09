"""Tests de l'API : cache des analyses de match et réutilisation du client (sans réseau)."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from betclic_agent import api  # noqa: E402


class FakeClient:
    calls = 0

    def get_main_markets(self, match_id: int) -> dict:
        FakeClient.calls += 1
        return {"Résultat du match (t. rég)": [{"name": "A", "odds": 2.0}, {"name": "N", "odds": 3.5}, {"name": "B", "odds": 4.0}]}


@pytest.fixture
def fake(monkeypatch):
    FakeClient.calls = 0
    api._match_cache.clear()
    monkeypatch.setattr(api, "_client", lambda market: FakeClient())
    return TestClient(api.app)


def test_analyze_second_click_served_from_cache(fake):
    first = fake.get("/api/analyze/42?market=CI")
    second = fake.get("/api/analyze/42?market=CI")
    assert first.status_code == second.status_code == 200
    assert second.json() == first.json()
    assert FakeClient.calls == 1


def test_analyze_unknown_match_is_not_cached(fake, monkeypatch):
    class Empty(FakeClient):
        def get_main_markets(self, match_id):
            Empty.calls += 1
            return {}

    Empty.calls = 0
    monkeypatch.setattr(api, "_client", lambda market: Empty())
    assert fake.get("/api/analyze/7?market=CI").status_code == 404
    assert fake.get("/api/analyze/7?market=CI").status_code == 404
    assert Empty.calls == 2


def test_prefetch_fills_cache_so_click_is_instant(monkeypatch):
    import asyncio

    FakeClient.calls = 0
    api._match_cache.clear()
    monkeypatch.setattr(api, "_client", lambda market: FakeClient())
    asyncio.run(api._prefetch_markets([1, 2, 3], "CI"))
    assert FakeClient.calls == 3
    asyncio.run(api._markets_for(2, "CI"))
    assert FakeClient.calls == 3
