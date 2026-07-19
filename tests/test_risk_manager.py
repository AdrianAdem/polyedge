from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from risk.manager import RiskManager
from storage.db import Database
from storage.models import MarketInfo, SizedSignal, TradeAction


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "test.db")
    await database.connect()
    yield database
    await database.close()


def make_signal(spread: float = 0.01) -> SizedSignal:
    market = MarketInfo(
        market_id="m1",
        question="Will the Fed cut rates in June?",
        end_date=datetime.now(UTC) + timedelta(days=30),
        volume=250_000.0,
        price_yes=0.42,
        price_no=0.58,
        spread=spread,
    )
    return SizedSignal(
        market=market,
        action=TradeAction.BUY_YES,
        edge=0.16,
        confidence=0.82,
        position_size=0.0,
        reasoning="CPI below consensus.",
    )


async def test_approves_clean_signal_and_sets_size(db: Database) -> None:
    rm = RiskManager(db=db)
    approved = await rm.evaluate(make_signal(), portfolio_value=5000.0)
    assert approved is not None
    assert approved.position_size > 0


async def test_position_size_capped_at_max_risk_per_trade(db: Database) -> None:
    rm = RiskManager(db=db, max_risk_per_trade=0.005)
    approved = await rm.evaluate(make_signal(), portfolio_value=5000.0)
    assert approved is not None
    assert approved.position_size <= 5000.0 * 0.005


async def test_rejects_wide_spread(db: Database) -> None:
    rm = RiskManager(db=db, max_spread=0.05)
    assert await rm.evaluate(make_signal(spread=0.09), portfolio_value=5000.0) is None


async def test_rejects_when_max_open_positions_reached(db: Database) -> None:
    rm = RiskManager(db=db, max_open_positions=1)
    await db.log_trade("other", "Other market?", "YES", 10.0, 0.5, True)
    assert await rm.evaluate(make_signal(), portfolio_value=5000.0) is None


async def test_rejects_duplicate_position_in_same_market(db: Database) -> None:
    rm = RiskManager(db=db)
    await db.log_trade("m1", "Will the Fed cut rates in June?", "YES", 10.0, 0.42, True)
    assert await rm.evaluate(make_signal(), portfolio_value=5000.0) is None


async def test_hard_stop_blocks_after_daily_loss_limit(db: Database) -> None:
    rm = RiskManager(db=db, hard_stop_loss=0.004)
    trade_id = await db.log_trade("other", "Other market?", "YES", 100.0, 0.5, True)
    await db.close_trade(trade_id, exit_price=0.0, pnl=-50.0)
    assert await rm.evaluate(make_signal(), portfolio_value=5000.0) is None


async def test_zero_portfolio_is_blocked(db: Database) -> None:
    rm = RiskManager(db=db)
    assert await rm.evaluate(make_signal(), portfolio_value=0.0) is None
