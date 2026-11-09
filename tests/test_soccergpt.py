"""Analyse SoccerGPT : formatage des stats, extraction de la prédiction, cache. Sans réseau."""

from __future__ import annotations

import pytest

from betclic_agent import soccergpt
from betclic_agent.soccergpt import Settings, SoccerGPTError


def _settings(**over) -> Settings:
    base = dict(sportmonks_token="tok", openai_key="sk", openai_model="gpt-4o",
                league="Euro Qualification", season="2024", competition="2024 European Championship")
    return Settings(**{**base, **over})


def test_stat_lines_formats_known_values_and_omits_missing():
    statistic = {"details": [
        {"type_id": 52, "value": {"all": {"count": 12, "average": 2.4}}},  # présent : formaté
        {"type_id": 214, "value": {"all": {"count": 5}}},                 # percentage absent : omis
        {"type_id": 45, "value": {"average": 61}},                          # possession : formaté
    ]}
    assert soccergpt.stat_lines(statistic) == [
        "The team scored 12 goals, with 2.4 goals per game.",
        "The team's ball possession was 61%",
    ]


def test_parse_prediction_extracts_winner_and_score_or_none():
    text = "Analysis...\nWinner: Spain\nFinal Score: Spain: 2, Italy: 1"
    assert soccergpt.parse_prediction(text) == {"winner": "Spain", "final_score": "Spain: 2, Italy: 1"}
    assert soccergpt.parse_prediction("no structure here") == {"winner": None, "final_score": None}


def test_predict_requires_configured_keys():
    with pytest.raises(SoccerGPTError, match="SPORTMONKS_API_TOKEN"):
        soccergpt.predict("Spain", "Italy", _settings(sportmonks_token=""))


def test_predict_builds_prompt_calls_model_and_caches(monkeypatch):
    soccergpt._cache.clear()
    calls = {"model": 0}

    class FakeApi:
        def __init__(self, token, session=None):
            pass

        def find_team(self, name):
            return {"id": len(name), "name": name}

        def get(self, path, include=None):
            return {"statistics": [{"season_id": 1, "details": [{"type_id": 52, "value": {"all": {"count": 3, "average": 1.0}}}]}]}

        def season(self, sid):
            return {"name": "2024", "league_id": 9}

        def league(self, lid):
            return {"name": "Euro Qualification"}

    def fake_model(settings, system, user):
        calls["model"] += 1
        assert "Spain vs. Italy" in user and "The team scored 3 goals" in user
        assert "2024 European Championship" in system
        return "Winner: Spain\nFinal Score: Spain: 2, Italy: 1"

    monkeypatch.setattr(soccergpt, "Sportmonks", FakeApi)
    monkeypatch.setattr(soccergpt, "ask_model", fake_model)

    first = soccergpt.predict("Spain", "Italy", _settings())
    second = soccergpt.predict("Spain", "Italy", _settings())
    assert first["winner"] == "Spain" and first["final_score"] == "Spain: 2, Italy: 1"
    assert first["from_cache"] is False and second["from_cache"] is True
    assert calls["model"] == 1
