"""Tests for the paper execution path.

This module records the fills and settles them against real market outcomes, so
its arithmetic decides every reported PnL number. It had no coverage.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from execution.paper import PaperTrader
from storage.db import Database
from storage.models import MarketInfo, SizedSignal, TradeAction


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "test.db")
    await database.connect()
    yield database
    await database.close()


def make_signal(
    action: TradeAction = TradeAction.BUY_YES,
    price_yes: float = 0.40,
    price_no: float = 0.60,
    size: float = 10.0,
) -> SizedSignal:
    market = MarketInfo(
        market_id="m1",
        question="Will the Fed cut rates in June?",
        end_date=datetime.now(UTC) + timedelta(days=30),
        volume=250_000.0,
        price_yes=price_yes,
        price_no=price_no,
        spread=0.01,
    )
    return SizedSignal(
        market=market,
        action=action,
        edge=0.15,
        confidence=0.8,
        position_size=size,
        reasoning="test",
    )


async def test_buy_yes_fills_at_the_yes_price(db: Database) -> None:
    trader = PaperTrader(db)
    await trader.execute(make_signal(TradeAction.BUY_YES, price_yes=0.40))

    (trade,) = await db.get_open_trades()
    assert trade["side"] == "YES"
    assert trade["entry_price"] == pytest.approx(0.40)


async def test_buy_no_fills_at_the_no_price(db: Database) -> None:
    trader = PaperTrader(db)
    await trader.execute(make_signal(TradeAction.BUY_NO, price_no=0.60))

    (trade,) = await db.get_open_trades()
    assert trade["side"] == "NO"
    assert trade["entry_price"] == pytest.approx(0.60)


async def test_a_won_yes_position_pays_the_remaining_distance_to_one(db: Database) -> None:
    trader = PaperTrader(db)
    await trader.execute(make_signal(TradeAction.BUY_YES, price_yes=0.40, size=10.0))

    await trader.check_settlements({"m1": 1.0})

    # 10 USD at 0.40 buys 25 shares; each settles 0.60 above entry.
    assert await trader.get_portfolio_value() == pytest.approx(515.0)
    assert await db.get_open_trades() == []


async def test_a_lost_yes_position_gives_back_the_stake(db: Database) -> None:
    trader = PaperTrader(db)
    await trader.execute(make_signal(TradeAction.BUY_YES, price_yes=0.40, size=10.0))

    await trader.check_settlements({"m1": 0.0})

    assert await trader.get_portfolio_value() == pytest.approx(490.0)


async def test_a_no_position_settles_against_the_inverted_outcome(db: Database) -> None:
    trader = PaperTrader(db)
    await trader.execute(make_signal(TradeAction.BUY_NO, price_no=0.60, size=12.0))

    # The market resolves NO, so the YES outcome is 0.0 and NO pays out at 1.0.
    await trader.check_settlements({"m1": 0.0})

    # 12 USD at 0.60 buys 20 shares; each settles 0.40 above entry.
    assert await trader.get_portfolio_value() == pytest.approx(508.0)


async def test_an_unresolved_market_leaves_the_trade_open(db: Database) -> None:
    trader = PaperTrader(db)
    await trader.execute(make_signal())

    await trader.check_settlements({"some-other-market": 1.0})

    assert len(await db.get_open_trades()) == 1
    assert await trader.get_portfolio_value() == pytest.approx(500.0)


async def test_a_zero_entry_price_settles_flat_instead_of_dividing_by_zero(db: Database) -> None:
    """A degenerate quote must not take the process down mid-settlement."""
    trader = PaperTrader(db)
    await trader.execute(make_signal(TradeAction.BUY_YES, price_yes=0.0, size=10.0))

    await trader.check_settlements({"m1": 1.0})

    assert await db.get_open_trades() == []
    assert await trader.get_portfolio_value() == pytest.approx(500.0)
