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
