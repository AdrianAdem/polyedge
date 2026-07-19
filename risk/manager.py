"""Position sizing and hard risk limits.

Last gate before a signal becomes a trade. Every rejection is logged so the
reason a signal died is always recoverable from the log stream.
"""

from __future__ import annotations

import structlog

from risk.kelly import kelly_size
from storage.db import Database
from storage.models import SizedSignal, TradeAction

logger = structlog.get_logger()


class RiskManager:
    """Enforces per-trade, daily and portfolio-level exposure limits.

    Limits are checked in cost order — cheapest rejections first — so a
    hard-stopped day never reaches the sizing math.
    """

    def __init__(
        self,
        db: Database,
        max_risk_per_trade: float = 0.005,
        max_daily_risk: float = 0.02,
        hard_stop_loss: float = 0.004,
        max_open_positions: int = 5,
        max_spread: float = 0.05,
        min_hours_to_expiry: float = 1.0,
    ) -> None:
        self.db = db
        self.max_risk_per_trade = max_risk_per_trade
        self.max_daily_risk = max_daily_risk
        self.hard_stop_loss = hard_stop_loss
        self.max_open_positions = max_open_positions
        self.max_spread = max_spread
        self.min_hours_to_expiry = min_hours_to_expiry

    async def evaluate(self, signal: SizedSignal, portfolio_value: float) -> SizedSignal | None:
        """Return the signal with ``position_size`` set, or None if any limit blocks it.

        Size is quarter-Kelly capped at ``max_risk_per_trade`` of the portfolio.
        Positions below $1 are dropped as not worth the spread.
        """
        if await self._is_hard_stopped(portfolio_value):
            logger.warning("risk_hard_stop_active")
            return None

        if await self._exceeds_daily_risk(portfolio_value):
            logger.warning("risk_daily_limit_reached")
            return None

        open_trades = await self.db.get_open_trades()
        if len(open_trades) >= self.max_open_positions:
            logger.warning("risk_max_positions", open=len(open_trades))
            return None

        already_in = any(t["market_id"] == signal.market.market_id for t in open_trades)
        if already_in:
            logger.debug("risk_already_positioned", market=signal.market.market_id)
            return None

        if signal.market.spread > self.max_spread:
            logger.debug("risk_spread_too_wide", spread=signal.market.spread)
            return None

        entry_price = (
            signal.market.price_yes
            if signal.action == TradeAction.BUY_YES
            else signal.market.price_no
        )
        odds = (1.0 / entry_price) - 1.0 if entry_price > 0 else 0.0

        raw_size = kelly_size(
            edge=abs(signal.edge),
            odds=odds,
            bankroll=portfolio_value,
        )

        max_size = portfolio_value * self.max_risk_per_trade
        position_size = min(raw_size, max_size)

        if position_size < 1.0:
            logger.debug("risk_size_too_small", size=position_size)
            return None

        signal.position_size = round(position_size, 2)

        logger.info(
            "risk_approved",
            market=signal.market.question[:40],
            action=signal.action.value,
            size=signal.position_size,
            edge=signal.edge,
        )
        return signal

    async def _is_hard_stopped(self, portfolio_value: float) -> bool:
        today_pnl = await self.db.get_today_pnl()
        loss_pct = abs(today_pnl) / portfolio_value if portfolio_value > 0 else 0
        return today_pnl < 0 and loss_pct >= self.hard_stop_loss

    async def _exceeds_daily_risk(self, portfolio_value: float) -> bool:
        if portfolio_value <= 0:
            return True

        today_trades = await self.db.get_today_trades()
        open_trades = await self.db.get_open_trades()
        # Today's open trades appear in both queries — count them once
        open_ids = {t["id"] for t in open_trades}
        closed_today = sum(t["size"] for t in today_trades if t["id"] not in open_ids)
        total_exposure = sum(t["size"] for t in open_trades) + closed_today
        return total_exposure / portfolio_value >= self.max_daily_risk
