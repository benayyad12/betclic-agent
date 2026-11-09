"""Configuration du projet — chargée depuis .env avec des valeurs par défaut sûres."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # dotenv optionnel : les variables d'environnement suffisent
    load_dotenv = None

if load_dotenv:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass
class Settings:
    """Paramètres de l'agent (tous surchargeables par .env)."""

    # Marché ciblé : CI = Côte d'Ivoire (betclic.ci), FR = France (betclic.fr)
    market: str = field(default_factory=lambda: os.getenv("BETCLIC_MARKET", "CI").upper())

    # Limite de requêtes : rester raisonnable (usage personnel, pas de hammering)
    request_delay_s: float = field(default_factory=lambda: _env_float("REQUEST_DELAY_S", 1.5))
    timeout_connect: int = field(default_factory=lambda: _env_int("TIMEOUT_CONNECT", 5))
    timeout_read: int = field(default_factory=lambda: _env_int("TIMEOUT_READ", 15))

    # Seuils de détection
    min_margin_pct: float = field(default_factory=lambda: _env_float("MIN_MARGIN_PCT", 6.0))
    max_margin_pct: float = field(default_factory=lambda: _env_float("MAX_MARGIN_PCT", 12.0))

    # Alertes
    telegram_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))

    @property
    def origin(self) -> str:
        return {
            "CI": "https://www.betclic.ci",
            "FR": "https://www.betclic.fr",
            "SN": "https://www.betclic.sn",
        }.get(self.market, "https://www.betclic.ci")

    def __repr__(self) -> str:  # évite d'exposer les secrets dans les logs
        return f"Settings(market={self.market}, delay={self.request_delay_s}s, seuils={self.min_margin_pct}-{self.max_margin_pct}%)"
