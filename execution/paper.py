"""Paper trading simulator.

Records simulated fills at live orderbook prices and settles them against real
market resolutions, so the tracked PnL reflects genuine market outcomes rather
than a synthetic price model.
"""

from __future__ import annotations

import structlog

from storage.db import Database
from storage.models import SizedSignal, TradeAction

logger = structlog.get_logger()


class PaperTrader:
    def __init__(self, db: Database, initial_balance: float = 500.0) -> None:
        self.db = db
        self.initial_balance = initial_balance

    async def get_portfolio_value(self) -> float:
        return await self.db.get_portfolio_value(self.initial_balance)

    async def execute(self, signal: SizedSignal) -> int:
        entry_price = (
            signal.market.price_yes
            if signal.action == TradeAction.BUY_YES
            else signal.market.price_no
        )
        side = "YES" if signal.action == TradeAction.BUY_YES else "NO"

        trade_id = await self.db.log_trade(
            market_id=signal.market.market_id,
            market_question=signal.market.question,
            side=side,
            size=signal.position_size,
            entry_price=entry_price,
            is_paper=True,
        )

        logger.info(
            "paper_trade_executed",
            trade_id=trade_id,
            market=signal.market.question[:50],
            side=side,
            size=signal.position_size,
            entry_price=entry_price,
        )
        return trade_id

    async def check_settlements(self, resolved_markets: dict[str, float]) -> None:
        """Check open trades against resolved market outcomes."""
        open_trades = await self.db.get_open_trades()

        for trade in open_trades:
            market_id = trade["market_id"]
            if market_id not in resolved_markets:
                continue

            outcome = resolved_markets[market_id]
            side = trade["side"]
            entry_price = trade["entry_price"]
            size = trade["size"]
            shares = size / entry_price if entry_price > 0 else 0

            if side == "YES":
                exit_price = outcome
            else:
                exit_price = 1.0 - outcome

            pnl = shares * (exit_price - entry_price)

            await self.db.close_trade(trade["id"], exit_price, pnl)
            logger.info(
                "paper_trade_settled",
                trade_id=trade["id"],
                pnl=round(pnl, 2),
                outcome=outcome,
            )
