"""SQLite persistence layer.

Stores signals, trades, per-call API costs and daily aggregates. All access is
async via aiosqlite so database writes never block the scan loop.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import aiosqlite
import structlog

logger = structlog.get_logger()

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    market_id TEXT NOT NULL,
    market_question TEXT NOT NULL,
    category TEXT,
    haiku_relevant INTEGER,
    haiku_urgency INTEGER,
    sonnet_real_prob REAL,
    edge REAL,
    confidence REAL,
    action TEXT,
    reasoning TEXT,
    key_factors TEXT
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    market_id TEXT NOT NULL,
    market_question TEXT NOT NULL,
    side TEXT NOT NULL,
    size REAL NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL,
    pnl REAL,
    is_paper INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'open'
);

CREATE TABLE IF NOT EXISTS api_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    latency_ms REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_stats (
    date TEXT PRIMARY KEY,
    total_pnl REAL NOT NULL DEFAULT 0,
    win_rate REAL NOT NULL DEFAULT 0,
    num_trades INTEGER NOT NULL DEFAULT 0,
    api_cost REAL NOT NULL DEFAULT 0,
    max_drawdown REAL NOT NULL DEFAULT 0
);
"""


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(str(self.db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.commit()
        logger.info("database_connected", path=str(self.db_path))

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Database not connected")
        return self._db

    async def log_signal(
        self,
        market_id: str,
        market_question: str,
        category: str,
        haiku_relevant: bool,
        haiku_urgency: int,
        sonnet_real_prob: float | None,
        edge: float | None,
        confidence: float | None,
        action: str | None,
        reasoning: str | None,
        key_factors: list[str] | None,
    ) -> None:
        await self.db.execute(
            """INSERT INTO signals
               (market_id, market_question, category, haiku_relevant, haiku_urgency,
                sonnet_real_prob, edge, confidence, action, reasoning, key_factors)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                market_id,
                market_question,
                category,
                int(haiku_relevant),
                haiku_urgency,
                sonnet_real_prob,
                edge,
                confidence,
                action,
                reasoning,
                json.dumps(key_factors) if key_factors else None,
            ),
        )
        await self.db.commit()

    async def log_trade(
        self,
        market_id: str,
        market_question: str,
        side: str,
        size: float,
        entry_price: float,
        is_paper: bool = True,
    ) -> int:
        cursor = await self.db.execute(
            """INSERT INTO trades (market_id, market_question, side, size, entry_price, is_paper)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (market_id, market_question, side, size, entry_price, int(is_paper)),
        )
        await self.db.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def close_trade(self, trade_id: int, exit_price: float, pnl: float) -> None:
        await self.db.execute(
            "UPDATE trades SET exit_price = ?, pnl = ?, status = 'closed' WHERE id = ?",
            (exit_price, pnl, trade_id),
        )
        await self.db.commit()

    async def log_api_call(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        latency_ms: float,
    ) -> None:
        await self.db.execute(
            "INSERT INTO api_calls (model, input_tokens, output_tokens, cost_usd, latency_ms) VALUES (?, ?, ?, ?, ?)",
            (model, input_tokens, output_tokens, cost_usd, latency_ms),
        )
        await self.db.commit()

    async def get_open_trades(self) -> list[dict]:
        cursor = await self.db.execute("SELECT * FROM trades WHERE status = 'open'")
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_today_trades(self) -> list[dict]:
        today = date.today().isoformat()
        cursor = await self.db.execute("SELECT * FROM trades WHERE date(timestamp) = ?", (today,))
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_today_pnl(self) -> float:
        today = date.today().isoformat()
        cursor = await self.db.execute(
            "SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE date(timestamp) = ? AND pnl IS NOT NULL",
            (today,),
        )
        row = await cursor.fetchone()
        return float(row[0]) if row else 0.0

    async def get_today_api_cost(self) -> float:
        today = date.today().isoformat()
        cursor = await self.db.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM api_calls WHERE date(timestamp) = ?",
            (today,),
        )
        row = await cursor.fetchone()
        return float(row[0]) if row else 0.0

    async def update_daily_stats(self) -> None:
        today = date.today().isoformat()
        trades = await self.get_today_trades()
        closed = [t for t in trades if t["status"] == "closed"]
        wins = [t for t in closed if (t["pnl"] or 0) > 0]
        total_pnl = sum(t["pnl"] or 0 for t in closed)
        win_rate = len(wins) / len(closed) if closed else 0.0
        api_cost = await self.get_today_api_cost()

        running_pnl = 0.0
        max_dd = 0.0
        peak = 0.0
        for t in sorted(closed, key=lambda x: x["timestamp"]):
            running_pnl += t["pnl"] or 0
            peak = max(peak, running_pnl)
            dd = peak - running_pnl
            max_dd = max(max_dd, dd)

        await self.db.execute(
            """INSERT INTO daily_stats (date, total_pnl, win_rate, num_trades, api_cost, max_drawdown)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(date) DO UPDATE SET
               total_pnl=excluded.total_pnl, win_rate=excluded.win_rate,
               num_trades=excluded.num_trades, api_cost=excluded.api_cost,
               max_drawdown=excluded.max_drawdown""",
            (today, total_pnl, win_rate, len(trades), api_cost, max_dd),
        )
        await self.db.commit()

    async def get_recent_signals(self, limit: int = 50) -> list[dict]:
        cursor = await self.db.execute(
            "SELECT * FROM signals ORDER BY timestamp DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_recent_trades(self, limit: int = 50) -> list[dict]:
        cursor = await self.db.execute(
            "SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_daily_stats(self, days: int = 30) -> list[dict]:
        cursor = await self.db.execute(
            "SELECT * FROM daily_stats ORDER BY date DESC LIMIT ?", (days,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_portfolio_value(self, initial_balance: float) -> float:
        cursor = await self.db.execute(
            "SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE pnl IS NOT NULL"
        )
        row = await cursor.fetchone()
        total_pnl = float(row[0]) if row else 0.0
        return initial_balance + total_pnl
