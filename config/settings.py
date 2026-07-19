"""Configuration loaded from environment variables.

Every secret is read from the environment; nothing is hardcoded. See
.env.example for the full set of supported variables.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _env_float(key: str, default: float = 0.0) -> float:
    return float(os.getenv(key, str(default)))


def _env_int(key: str, default: int = 0) -> int:
    return int(os.getenv(key, str(default)))


def _env_bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).lower() in ("true", "1", "yes")


class PolymarketConfig(BaseModel):
    api_key: str = Field(default_factory=lambda: _env("POLYMARKET_API_KEY"))
    secret: str = Field(default_factory=lambda: _env("POLYMARKET_SECRET"))
    passphrase: str = Field(default_factory=lambda: _env("POLYMARKET_PASSPHRASE"))
    private_key: str = Field(default_factory=lambda: _env("POLYGON_PRIVATE_KEY"))


class BinanceConfig(BaseModel):
    api_key: str = Field(default_factory=lambda: _env("BINANCE_API_KEY"))
    secret: str = Field(default_factory=lambda: _env("BINANCE_SECRET"))


class AnthropicConfig(BaseModel):
    api_key: str = Field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))


class FredConfig(BaseModel):
    api_key: str = Field(default_factory=lambda: _env("FRED_API_KEY"))


class TelegramConfig(BaseModel):
    bot_token: str = Field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    chat_id: str = Field(default_factory=lambda: _env("TELEGRAM_CHAT_ID"))


class TradingConfig(BaseModel):
    paper_trading: bool = Field(default_factory=lambda: _env_bool("PAPER_TRADING", True))
    initial_balance: float = Field(default_factory=lambda: _env_float("INITIAL_BALANCE", 500))
    max_risk_per_trade: float = Field(
        default_factory=lambda: _env_float("MAX_RISK_PER_TRADE", 0.005)
    )
    max_daily_risk: float = Field(default_factory=lambda: _env_float("MAX_DAILY_RISK", 0.02))
    hard_stop_loss: float = Field(default_factory=lambda: _env_float("HARD_STOP_LOSS", 0.004))


class ScannerConfig(BaseModel):
    interval_seconds: int = Field(default_factory=lambda: _env_int("SCAN_INTERVAL_SECONDS", 300))
    min_volume: float = Field(default_factory=lambda: _env_float("MIN_MARKET_VOLUME", 50000))
    min_edge: float = Field(default_factory=lambda: _env_float("MIN_EDGE_THRESHOLD", 0.10))
    min_confidence: float = Field(
        default_factory=lambda: _env_float("MIN_CONFIDENCE_THRESHOLD", 0.70)
    )
    # Cost cap: Haiku runs per market per scan, so this bounds API spend directly
    max_markets_per_scan: int = Field(default_factory=lambda: _env_int("MAX_MARKETS_PER_SCAN", 40))


class Settings(BaseModel):
    polymarket: PolymarketConfig = Field(default_factory=PolymarketConfig)
    binance: BinanceConfig = Field(default_factory=BinanceConfig)
    anthropic: AnthropicConfig = Field(default_factory=AnthropicConfig)
    fred: FredConfig = Field(default_factory=FredConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    trading: TradingConfig = Field(default_factory=TradingConfig)
    scanner: ScannerConfig = Field(default_factory=ScannerConfig)
    db_path: Path = Field(default_factory=lambda: Path(_env("DB_PATH", "polyedge.db")))


def load_config() -> Settings:
    return Settings()
