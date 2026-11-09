"""Tests unitaires de l'analyse des marchés (marges, fiabilité, détection)."""

from __future__ import annotations

import pytest

from betclic_agent.markets import (
    analyze_markets,
    find_opportunities,
    implied_probability,
    is_exhaustive_market,
    market_margin,
)


def test_implied_probability():
    assert implied_probability(2.0) == 0.5
    assert implied_probability(1.25) == 0.8
    assert implied_probability(1.0) == 0.0  # cote invalide
    assert implied_probability(0.5) == 0.0


def test_market_margin_market_complet():
    # 1X2 avec ~4,8 % de marge : 1/2.55 + 1/3.05 + 1/3.05 ≈ 1,0479
    selections = [
        {"name": "Domicile", "odds": 2.55},
        {"name": "Nul", "odds": 3.05},
        {"name": "Extérieur", "odds": 3.05},
    ]
    margin = market_margin(selections)
    assert margin is not None
    assert 4.0 <= margin <= 5.5


def test_market_margin_sans_marge():
    # Probabilités qui somment exactement à 1 → marge 0
    selections = [{"name": "A", "odds": 2.0}, {"name": "B", "odds": 2.0}]
    assert market_margin(selections) == 0.0


def test_market_margin_vide_ou_attendu_incorrect():
    assert market_margin([]) is None
    selections = [{"name": "A", "odds": 2.0}, {"name": "B", "odds": 2.0}]
    assert market_margin(selections, expected_outcomes=3) is None


@pytest.mark.parametrize(
    "name,count,expected",
    [
        ("Résultat du match (t. rég)", 3, True),
        ("Résultat du match (t. rég)", 2, False),  # structure 1X2 incomplète
        ("Les 2 équipes marquent (t. rég)", 2, True),
        ("Les 2 équipes marquent (t. rég)", 3, False),
        ("Double chance (t. rég)", 3, False),  # issues qui se recouvrent
        ("Nombre total de buts (t. rég)", 10, False),  # plusieurs paires Over/Under
        ("L'équipe possède 2 buts d'avance ou gagne (t. rég)", 2, False),  # marché partiel
        ("Qui marquera le but 2 ?", 3, False),
    ],
)
def test_is_exhaustive_market(name, count, expected):
    selections = [{"name": f"S{i}", "odds": 2.0} for i in range(count)]
    assert is_exhaustive_market(name, selections) is expected


def test_analyze_markets_trie_et_marque_fiabilite():
    markets = {
        "Résultat du match (t. rég)": [
            {"name": "A", "odds": 2.0},
            {"name": "Nul", "odds": 3.5},
            {"name": "B", "odds": 4.0},
        ],
        "L'équipe possède 2 buts d'avance ou gagne": [
            {"name": "A", "odds": 1.18},
            {"name": "B", "odds": 8.75},
        ],
    }
    analyzed = analyze_markets(markets)
    by_name = {item["market"]: item for item in analyzed}
    assert by_name["Résultat du match (t. rég)"]["reliable"] is True
    assert by_name["L'équipe possède 2 buts d'avance ou gagne"]["reliable"] is False
    # tri par marge croissante
    assert analyzed[0]["margin_pct"] <= analyzed[1]["margin_pct"]


def test_find_opportunities_ignore_les_marches_non_fiables():
    analyzed = [
        {
            "market": "Résultat du match (t. rég)",
            "margin_pct": 7.0,
            "outcomes": 3,
            "reliable": True,
            "selections": [],
        },
        {
            # marge dans la fourchette MAIS marché non fiable → doit être ignoré
            "market": "L'équipe possède 2 buts d'avance",
            "margin_pct": 7.0,
            "outcomes": 2,
            "reliable": False,
            "selections": [],
        },
        {
            "market": "Les 2 équipes marquent (t. rég)",
            "margin_pct": 25.0,  # hors fourchette → ignoré
            "outcomes": 2,
            "reliable": True,
            "selections": [],
        },
    ]
    found = find_opportunities(analyzed, min_margin_pct=6.0, max_margin_pct=12.0)
    assert len(found) == 1
    assert found[0]["market"] == "Résultat du match (t. rég)"
