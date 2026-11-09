"""Tests du statut temporel des matchs (aucun réseau)."""

from __future__ import annotations

from datetime import datetime, timezone

from betclic_agent.timing import (
    IN_PLAY,
    PAST,
    UNKNOWN,
    UPCOMING,
    local_time,
    match_status,
    parse_kickoff,
)

NOON = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)


def test_parse_kickoff_lit_les_sept_decimales_du_backend():
    parsed = parse_kickoff("2026-09-17T19:00:00.0000000Z")
    assert parsed == datetime(2026, 9, 17, 19, 0, tzinfo=timezone.utc)


def test_parse_kickoff_accepte_vide_ou_invalide():
    assert parse_kickoff(None) is None
    assert parse_kickoff("") is None
    assert parse_kickoff("demain") is None


def test_match_status_avant_le_coup_d_envoi():
    assert match_status("2026-09-17T19:00:00.0000000Z", NOON) == UPCOMING


def test_match_status_pendant_la_fenetre_de_jeu():
    assert match_status("2026-09-17T10:30:00.0000000Z", NOON) == IN_PLAY


def test_match_status_apres_la_fenetre_de_jeu():
    assert match_status("2026-09-17T07:00:00.0000000Z", NOON) == PAST


def test_match_status_borne_de_fin_personnalisee():
    kickoff = "2026-09-17T10:30:00.0000000Z"
    assert match_status(kickoff, NOON, duration_h=1) == PAST
    assert match_status(kickoff, NOON, duration_h=2) == IN_PLAY


def test_match_status_sans_heure():
    assert match_status(None, NOON) == UNKNOWN


def test_local_time_affiche_hh_mm_utc():
    assert local_time("2026-09-17T19:00:00.0000000Z") == "19:00"
    assert local_time(None) == "—"
