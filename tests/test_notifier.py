"""Notifier : échappement HTML, journalisation des échecs, déduplication des alertes."""

import logging
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs

import pytest

from betclic_agent import notifier
from betclic_agent.config import Settings
from betclic_agent.db.repository import create_schema, make_engine, make_session_factory
from betclic_agent.db.models import AlertSent
from sqlalchemy import select


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "SECRET_TOKEN")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    return Settings()


@pytest.fixture
def sessions(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'n.db'}")
    create_schema(engine)
    return make_session_factory(engine)


def _item(match="A <b>vs</b> B", margin=4.2):
    return {
        "match": match,
        "market": "Résultat du match",
        "margin_pct": margin,
        "selections": [{"name": "A", "odds": 2.1}, {"name": "<i>N</i>", "odds": 3.4}],
    }


def _fake_response(status=200):
    resp = MagicMock(status=status)
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def test_message_html_echappe_les_valeurs(settings):
    with patch("urllib.request.urlopen", return_value=_fake_response()) as urlopen:
        notifier.alert_opportunities([_item()], scanned=1, settings=settings)
    text = parse_qs(urlopen.call_args.kwargs["data"].decode())["text"][0]
    assert "&lt;b&gt;vs&lt;/b&gt;" in text
    assert "<i>N</i>" not in text
    assert "&lt;i&gt;N&lt;/i&gt;" in text


def test_echec_telegram_est_journalise_sans_token(settings, caplog):
    with patch("urllib.request.urlopen", side_effect=OSError("boom")):
        with caplog.at_level(logging.ERROR, logger="betclic_agent.notifier"):
            ok = notifier.send_telegram("hello", settings)
    assert ok is False
    assert "Envoi Telegram échoué" in caplog.text
    assert "SECRET_TOKEN" not in caplog.text


def test_alerte_deja_envoyee_n_est_pas_renvoyee(settings, sessions):
    with patch("urllib.request.urlopen", return_value=_fake_response()) as urlopen:
        assert notifier.alert_opportunities([_item()], 1, settings, sessions) is True
        assert notifier.alert_opportunities([_item()], 1, settings, sessions) is False
    assert urlopen.call_count == 1
    with sessions() as s:
        assert len(s.scalars(select(AlertSent)).all()) == 1


def test_echec_telegram_n_enregistre_pas_l_alerte(settings, sessions):
    with patch("urllib.request.urlopen", return_value=_fake_response(status=500)):
        assert notifier.alert_opportunities([_item()], 1, settings, sessions) is False
    with sessions() as s:
        assert s.scalars(select(AlertSent)).all() == []
