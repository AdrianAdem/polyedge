"""Domain models shared across the pipeline.

Plain dataclasses rather than Pydantic models: these are internal values that
never cross a trust boundary, so validation would only add overhead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TradeAction(str, Enum):
    BUY_YES = "BUY_YES"
    BUY_NO = "BUY_NO"
    SKIP = "SKIP"


@dataclass
class NewsItem:
    title: str
    source: str
    timestamp: datetime
    content_snippet: str
    url: str = ""


@dataclass
class MarketInfo:
    market_id: str
    question: str
    end_date: datetime
    volume: float
    price_yes: float
    price_no: float
    spread: float
    token_id_yes: str = ""
    token_id_no: str = ""


@dataclass
class ScanResult:
    market: MarketInfo
    category: str
    current_price: float
    volume: float
    time_to_expiry_hours: float


@dataclass
class HaikuSignal:
    relevant: bool
    direction: str
    urgency: int


@dataclass
class SonnetSignal:
    real_probability: float
    edge: float
    confidence: float
    action: TradeAction
    reasoning: str
    key_factors: list[str] = field(default_factory=list)


@dataclass
class SizedSignal:
    market: MarketInfo
    action: TradeAction
    edge: float
    confidence: float
    position_size: float
    reasoning: str
    key_factors: list[str] = field(default_factory=list)
    score: float = 0.0


@dataclass
class CryptoSnapshot:
    symbol: str
    price: float
    momentum_60s: float
    momentum_300s: float
    volatility: float
    direction: str
    timestamp: datetime = field(default_factory=_utcnow)


@dataclass
class MacroData:
    cpi: float | None = None
    fed_funds_rate: float | None = None
    unemployment: float | None = None
    gdp_growth: float | None = None
    last_updated: datetime = field(default_factory=_utcnow)
