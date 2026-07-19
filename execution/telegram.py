"""Telegram alerting and control interface.

Delivers signals and exposes /status, /history, /pause, /resume and /costs.
Pause state is held here and honoured by the main loop.
"""

from __future__ import annotations

import structlog
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes

from storage.db import Database
from storage.models import SizedSignal

logger = structlog.get_logger()


class TelegramBot:
    def __init__(self, bot_token: str, chat_id: str, db: Database, initial_balance: float) -> None:
        self.bot = Bot(token=bot_token)
        self.chat_id = chat_id
        self.db = db
        self.initial_balance = initial_balance
        self._app: Application | None = None
        self._paused = False

    @property
    def paused(self) -> bool:
        return self._paused

    async def start(self) -> None:
        self._app = Application.builder().token(self.bot.token).build()
        self._app.add_handler(CommandHandler("status", self._cmd_status))
        self._app.add_handler(CommandHandler("history", self._cmd_history))
        self._app.add_handler(CommandHandler("pause", self._cmd_pause))
        self._app.add_handler(CommandHandler("resume", self._cmd_resume))
        self._app.add_handler(CommandHandler("costs", self._cmd_costs))

        await self._app.initialize()
        await self._app.start()
        if self._app.updater:
            await self._app.updater.start_polling()

        logger.info("telegram_bot_started")

    async def stop(self) -> None:
        if self._app:
            if self._app.updater:
                await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()

    async def send_signal(self, signal: SizedSignal) -> None:
        action_emoji = "\U0001f3af"
        factors = "\n".join(f"  - {f}" for f in signal.key_factors)

        msg = (
            f"{action_emoji} SIGNAL: {signal.action.value}\n"
            f'Market: "{signal.market.question}"\n'
            f"Current Price: ${signal.market.price_yes:.2f} ({signal.market.price_yes * 100:.0f}%)\n"
            f"Edge: {signal.edge * 100:.1f}%\n"
            f"Confidence: {signal.confidence:.2f}\n"
            f"Score: {signal.score:.3f}\n"
            f"Suggested Size: ${signal.position_size:.2f}\n"
            f"Key Factors:\n{factors}\n\n"
            f"Reasoning: {signal.reasoning}"
        )
        await self._send(msg)

    async def send_error(self, error: str) -> None:
        await self._send(f"⚠️ ERROR:\n{error[:1000]}")

    async def _send(self, text: str) -> None:
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                parse_mode=None,
            )
        except Exception as e:
            logger.error("telegram_send_error", error=str(e))

    async def _cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        portfolio = await self.db.get_portfolio_value(self.initial_balance)
        today_pnl = await self.db.get_today_pnl()
        open_trades = await self.db.get_open_trades()
        status = "PAUSED" if self._paused else "RUNNING"

        msg = (
            f"\U0001f4ca Portfolio Status\n"
            f"Status: {status}\n"
            f"Portfolio: ${portfolio:.2f}\n"
            f"Today PnL: ${today_pnl:+.2f}\n"
            f"Open Positions: {len(open_trades)}\n"
        )

        for t in open_trades:
            msg += f"  - {t['side']} {t['market_question'][:40]}... @ ${t['entry_price']:.2f}\n"

        if update.message:
            await update.message.reply_text(msg)

    async def _cmd_history(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        trades = await self.db.get_recent_trades(10)
        if not trades:
            if update.message:
                await update.message.reply_text("No trades yet.")
            return

        msg = "\U0001f4c8 Recent Trades\n"
        for t in trades:
            pnl_str = f"${t['pnl']:+.2f}" if t["pnl"] is not None else "open"
            msg += f"  {t['side']} {t['market_question'][:35]}... | {pnl_str}\n"

        if update.message:
            await update.message.reply_text(msg)

    async def _cmd_pause(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        self._paused = True
        if update.message:
            await update.message.reply_text("⏸ Bot paused. Use /resume to continue.")

    async def _cmd_resume(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        self._paused = False
        if update.message:
            await update.message.reply_text("▶ Bot resumed.")

    async def _cmd_costs(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        today_cost = await self.db.get_today_api_cost()
        if update.message:
            await update.message.reply_text(f"\U0001f4b0 API Cost Today: ${today_cost:.4f}")
