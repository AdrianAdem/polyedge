from datetime import UTC, datetime, timedelta

import pytest

from analysis.signal import SignalEngine
from storage.models import MarketInfo, ScanResult, SonnetSignal, TradeAction


def make_scan(spread: float = 0.01) -> ScanResult:
    market = MarketInfo(
        market_id="m1",
        question="Will the Fed cut rates in June?",
        end_date=datetime.now(UTC) + timedelta(days=30),
        volume=250_000.0,
        price_yes=0.42,
        price_no=0.58,
        spread=spread,
    )
    return ScanResult(
        market=market,
        category="macro",
        current_price=0.42,
        volume=250_000.0,
        time_to_expiry_hours=720.0,
    )


def make_signal(
    edge: float = 0.16,
    confidence: float = 0.82,
    action: TradeAction = TradeAction.BUY_YES,
) -> SonnetSignal:
    return SonnetSignal(
        real_probability=0.58,
        edge=edge,
        confidence=confidence,
        action=action,
        reasoning="CPI came in below consensus.",
        key_factors=["CPI 2.1%", "Dovish Fed commentary"],
    )


@pytest.fixture
def engine() -> SignalEngine:
    return SignalEngine(min_edge=0.10, min_confidence=0.70)


def test_accepts_signal_above_all_thresholds(engine: SignalEngine) -> None:
    result = engine.evaluate(make_scan(), make_signal())
    assert result is not None
    assert result.action == TradeAction.BUY_YES


def test_rejects_skip_action(engine: SignalEngine) -> None:
    assert engine.evaluate(make_scan(), make_signal(action=TradeAction.SKIP)) is None


def test_rejects_edge_below_threshold(engine: SignalEngine) -> None:
    assert engine.evaluate(make_scan(), make_signal(edge=0.05)) is None


def test_rejects_confidence_below_threshold(engine: SignalEngine) -> None:
    assert engine.evaluate(make_scan(), make_signal(confidence=0.5)) is None


def test_rejects_illiquid_market(engine: SignalEngine) -> None:
    assert engine.evaluate(make_scan(spread=0.09), make_signal()) is None


def test_score_is_edge_times_confidence(engine: SignalEngine) -> None:
    result = engine.evaluate(make_scan(), make_signal(edge=0.20, confidence=0.80))
    assert result is not None
    assert result.score == pytest.approx(0.16)


def test_position_size_is_left_to_risk_manager(engine: SignalEngine) -> None:
    result = engine.evaluate(make_scan(), make_signal())
    assert result is not None
    assert result.position_size == 0.0
