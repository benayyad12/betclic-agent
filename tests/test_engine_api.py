from fastapi.testclient import TestClient

from betclic_agent import api
from betclic_agent.engine import service


def _pred(home, away, odds=None, quality="élevée", p_home=0.6):
    value = [{
        "label": "Victoire domicile",
        "model_probability": p_home,
        "ev_interval": [0.05 if p_home > 0.5 else -0.2, 0.3],
        "expected_value": 0.1,
        "rank_key": 0.05 if p_home > 0.5 else -0.2,
        "betclic_odds": 2.0,
        "fair_odds": 1 / p_home,
        "confidence": 0.5,
        "data_quality": quality,
    }] if odds else []
    return {"home": home, "away": away, "data_quality": quality, "value": value}


def test_scan_separates_value_likely_and_insufficient(monkeypatch):
    def fake_predict(home, away, odds=None):
        if home == "Inconnu":
            raise service.UnknownTeam(home)
        return _pred(home, away, odds, quality="moyenne" if home == "Faible" else "élevée")

    monkeypatch.setattr(service, "predict", fake_predict)
    res = service.scan([
        {"home": "A", "away": "B", "odds": {"home": 2, "draw": 3, "away": 4}},
        {"home": "Inconnu", "away": "B"},
        {"home": "Faible", "away": "C", "odds": {"home": 2, "draw": 3, "away": 4}},
    ], min_quality="élevée")
    assert res["scanned"] == 3
    assert len(res["best_value_bets"]) == 1
    assert res["best_value_bets"][0]["match"] == "A - B"
    assert len(res["insufficient_data"]) == 2
    assert res["no_statistical_edge"] == 0


def test_scan_without_odds_returns_no_forced_pick(monkeypatch):
    monkeypatch.setattr(service, "predict", lambda h, a, odds=None: _pred(h, a))
    res = service.scan([{"home": "A", "away": "B"}])
    assert res["best_value_bets"] == []
    assert res["highest_probability"] == []


def test_match_endpoint_passes_odds_only_when_all_three_given(monkeypatch):
    seen = {}

    def fake_predict(home, away, odds=None):
        seen["odds"] = odds
        return _pred(home, away, odds)

    monkeypatch.setattr(service, "predict", fake_predict)
    client = TestClient(api.app)
    client.get("/api/engine/match", params={"home": "Lyon", "away": "Lille", "odds_home": 2, "odds_draw": 3})
    assert seen["odds"] is None
    client.get("/api/engine/match", params={"home": "Lyon", "away": "Lille", "odds_home": 2, "odds_draw": 3, "odds_away": 4})
    assert seen["odds"] == {"home": 2.0, "draw": 3.0, "away": 4.0}


def test_scan_endpoint_rejects_empty_fixtures():
    client = TestClient(api.app)
    assert client.post("/api/engine/scan", json={"fixtures": []}).status_code == 422


def test_validation_endpoint_404_without_report(monkeypatch):
    monkeypatch.setattr(api.validate, "load_report", lambda: None)
    assert TestClient(api.app).get("/api/engine/validation").status_code == 404


def test_engine_match_unexpected_error_returns_explicit_detail(monkeypatch):
    def boom(home, away, odds=None):
        raise KeyError("corners")

    monkeypatch.setattr(service, "predict", boom)
    r = TestClient(api.app, raise_server_exceptions=False).get(
        "/api/engine/match", params={"home": "Lyon", "away": "Lille"})
    assert r.status_code == 500
    assert "KeyError" in r.json()["detail"]


def test_forecast_is_point_prediction_without_interval(monkeypatch):
    def fake(home, away, odds=None):
        return {
            "home": home, "away": away,
            "markets": {
                "match_result": {"home": 0.55, "draw": 0.25, "away": 0.20},
                "top_scores": [{"home": 2, "away": 1, "p": 0.12}],
                "btts": {"yes": 0.54, "no": 0.46},
                "over_under": {"2.5": {"over": 0.51, "under": 0.49}},
            },
            "expected_goals": {"home": 1.7, "away": 1.0},
            "data_quality": "moyenne",
            "data_last_match": "2026-09-03",
        }

    monkeypatch.setattr(service, "predict", fake)
    out = service.forecast("Lyon", "Lille")
    assert out["pick_label"] == "Victoire domicile"
    assert out["score"] == "2-1"
    assert "ev_interval" not in out


def test_forecasts_batch_keeps_going_when_a_team_is_unknown(monkeypatch):
    def fake(home, away):
        if home == "Inconnu":
            raise service.UnknownTeam("Inconnu")
        return {"home": home, "away": away, "pick_label": "Match nul", "score": "1-1"}

    monkeypatch.setattr(service, "forecast", fake)
    r = TestClient(api.app).post("/api/engine/forecasts", json={"fixtures": [
        {"home": "Lyon", "away": "Lille", "id": "1"},
        {"home": "Inconnu", "away": "Lille", "id": "2"},
    ]})
    assert r.status_code == 200
    body = r.json()["forecasts"]
    assert body[0]["score"] == "1-1"
    assert "inconnu" in body[1]["error"].lower() or body[1]["error"] == "Inconnu"
    assert body[1]["id"] == "2"


def test_staking_suggest_never_places_a_bet():
    r = TestClient(api.app).post("/api/staking/suggest", json={
        "bankroll": 1000, "prob": 0.55, "odds": 2.1, "fraction": 0.25, "cap_pct": 0.02,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["placed"] is False
    assert body["stake"] > 0


def test_status_does_not_expose_secrets():
    body = TestClient(api.app).get("/api/status").json()
    assert body["automatic_betting"] is False
    blob = str(body).lower()
    assert "token" not in blob
    assert "openai" not in blob
    assert "sportmonks" not in blob


def test_clock_fr_uses_paris_timezone():
    body = TestClient(api.app).get("/api/clock", params={"market": "FR"}).json()
    assert body["timezone"] == "Europe/Paris"
    assert len(body["today"]) == 10


def test_engine_match_missing_dataset_returns_503(monkeypatch):
    def missing(home, away, odds=None):
        raise FileNotFoundError("data/Matches.csv")

    monkeypatch.setattr(service, "predict", missing)
    r = TestClient(api.app).get("/api/engine/match", params={"home": "Lyon", "away": "Lille"})
    assert r.status_code == 503
    assert "Matches.csv" in r.json()["detail"]
