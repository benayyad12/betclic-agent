"""Tests de l'analyse fine (dé-margement, agrégation du score exact)."""

from __future__ import annotations

import pytest

from betclic_agent.analysis import demargin, parse_score, score_aggregates


def test_demargin_normalise_a_un():
    probs = demargin([{"name": "A", "odds": 3.0}, {"name": "N", "odds": 3.58}, {"name": "B", "odds": 2.12}])
    assert sum(probs.values()) == pytest.approx(1.0)
    assert probs["B"] > probs["A"] > probs["N"]


def test_demargin_cote_invalide_ne_casse_pas():
    probs = demargin([{"name": "A", "odds": 1.0}, {"name": "B", "odds": 2.0}])
    assert probs == {"A": 0.0, "B": 1.0}


def test_parse_score_accepte_les_espaces():
    assert parse_score("1 - 0") == (1, 0)
    assert parse_score("2-1") == (2, 1)


def test_parse_score_rejette_autre():
    assert parse_score("Autre") is None
    assert parse_score("1 - 0 (a.p.)") is None


def test_score_aggregates_repartit_les_issues():
    selections = [
        {"name": "1 - 0", "odds": 10.0},
        {"name": "0 - 0", "odds": 10.0},
        {"name": "0 - 1", "odds": 10.0},
        {"name": "1 - 1", "odds": 10.0},
        {"name": "2 - 1", "odds": 10.0},
        {"name": "Autre", "odds": 10.0},  # ignoré : pas un score
    ]
    aggregates = score_aggregates(selections)
    assert aggregates is not None
    assert aggregates["home"] == pytest.approx(2 / 5)
    assert aggregates["draw"] == pytest.approx(2 / 5)
    assert aggregates["away"] == pytest.approx(1 / 5)
    assert aggregates["btts_yes"] == pytest.approx(2 / 5)  # 1-1 et 2-1
    assert aggregates["over_2_5"] == pytest.approx(1 / 5)  # seul 2-1 dépasse 2,5 buts
    assert len(aggregates["scores"]) == 5


def test_score_aggregates_sans_score_retourne_none():
    assert score_aggregates([{"name": "Autre", "odds": 12.0}]) is None
