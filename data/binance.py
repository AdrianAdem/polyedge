"""Binance trade-stream feed for crypto market context.

Keeps a 30-minute rolling price window in memory and derives momentum and
realised volatility from it. Reconnects automatically; a dropped socket
degrades context quality but never stops the scan loop.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from datetime import UTC, datetime

import structlog
import websockets

from storage.models import CryptoSnapshot

logger = structlog.get_logger()

BINANCE_WS = "wss://stream.binance.com:9443/ws"
SYMBOLS = ["btcusdt", "ethusdt"]
WINDOW_SECONDS = 1800  # 30 minutes


class BinanceFeed:
    def __init__(self) -> None:
        self._prices: dict[str, deque[tuple[float, float]]] = {s: deque() for s in SYMBOLS}
        self._running = False
        self._task: asyncio.Task | None = None

    async def connect(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._ws_loop())
        logger.info("binance_feed_started", symbols=SYMBOLS)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _ws_loop(self) -> None:
        streams = "/".join(f"{s}@trade" for s in SYMBOLS)
        url = f"{BINANCE_WS}/{streams}"

        while self._running:
            try:
                async with websockets.connect(url) as ws:
                    logger.info("binance_ws_connected")
                    async for msg in ws:
                        if not self._running:
                            break
                        data = json.loads(msg)
                        symbol = data.get("s", "").lower()
                        price = float(data.get("p", 0))
                        ts = time.time()

                        if symbol in self._prices:
                            self._prices[symbol].append((ts, price))
                            self._trim(symbol)

            except (websockets.ConnectionClosed, Exception) as e:
                logger.warning("binance_ws_reconnect", error=str(e))
                await asyncio.sleep(5)

    def _trim(self, symbol: str) -> None:
        cutoff = time.time() - WINDOW_SECONDS
        q = self._prices[symbol]
        while q and q[0][0] < cutoff:
            q.popleft()

    def get_snapshot(self, symbol: str) -> CryptoSnapshot | None:
        key = symbol.lower().replace("/", "")
        q = self._prices.get(key)
        if not q or len(q) < 2:
            return None

        current_price = q[-1][1]
        now = time.time()

        momentum_60 = self._calc_momentum(q, now, 60)
        momentum_300 = self._calc_momentum(q, now, 300)
        volatility = self._calc_volatility(q, now, 300)

        direction = "up" if momentum_60 > 0 else "down" if momentum_60 < 0 else "flat"

        return CryptoSnapshot(
            symbol=symbol,
            price=current_price,
            momentum_60s=momentum_60,
            momentum_300s=momentum_300,
            volatility=volatility,
            direction=direction,
            timestamp=datetime.now(UTC),
        )

    def _calc_momentum(self, q: deque[tuple[float, float]], now: float, window: int) -> float:
        cutoff = now - window
        past_prices = [p for t, p in q if t >= cutoff]
        if len(past_prices) < 2:
            return 0.0
        return (past_prices[-1] - past_prices[0]) / past_prices[0]

    def _calc_volatility(self, q: deque[tuple[float, float]], now: float, window: int) -> float:
        cutoff = now - window
        prices = [p for t, p in q if t >= cutoff]
        if len(prices) < 10:
            return 0.0
        returns = [(prices[i] - prices[i - 1]) / prices[i - 1] for i in range(1, len(prices))]
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / len(returns)
        return variance**0.5
