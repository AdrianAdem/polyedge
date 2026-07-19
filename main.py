"""Orchestrator for the PolyEdge scan loop.

Wires the data feeds, analysis tiers, risk checks and execution together, then
runs the scan cycle on an interval. Background feeds (Binance, news, FRED) run
as independent tasks; a failure in one degrades context without halting the loop.
"""

from __future__ import annotations

import asyncio
import signal

import structlog

from analysis.claude_analyst import ClaudeAnalyst
from analysis.scanner import MarketScanner
from analysis.signal import SignalEngine
from config.settings import load_config
from data.binance import BinanceFeed
from data.fred import FredClient
from data.news import NewsAggregator
from data.polymarket import PolymarketClient
from execution.paper import PaperTrader
from execution.telegram import TelegramBot
from risk.manager import RiskManager
from storage.db import Database
from storage.models import TradeAction

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(20),
)

logger = structlog.get_logger()


def build_news_context(news_items: list) -> str:
    if not news_items:
        return "Keine aktuellen Nachrichten verfügbar."

    lines = []
    for item in news_items[:15]:
        lines.append(f"[{item.source}] {item.title}")
        if item.content_snippet:
            lines.append(f"  {item.content_snippet[:200]}")
    return "\n".join(lines)


def build_crypto_context(binance: BinanceFeed) -> str:
    parts = []
    for sym in ["btcusdt", "ethusdt"]:
        snap = binance.get_snapshot(sym)
        if snap:
            parts.append(
                f"{snap.symbol.upper()}: ${snap.price:,.2f} "
                f"(60s: {snap.momentum_60s:+.2%}, 5m: {snap.momentum_300s:+.2%}, "
                f"vol: {snap.volatility:.4f}, dir: {snap.direction})"
            )
    return "\n".join(parts) if parts else "Keine Crypto-Daten verfügbar."


async def main() -> None:
    config = load_config()
    logger.info("polyedge_starting", paper_mode=config.trading.paper_trading)

    db = Database(config.db_path)
    await db.connect()

    poly_client = PolymarketClient(min_volume=config.scanner.min_volume)
    binance_feed = BinanceFeed()
    news_agg = NewsAggregator()
    fred_client = FredClient(api_key=config.fred.api_key)
    claude_analyst = ClaudeAnalyst(api_key=config.anthropic.api_key, db=db)
    scanner = MarketScanner(min_volume=config.scanner.min_volume)
    signal_engine = SignalEngine(
        min_edge=config.scanner.min_edge,
        min_confidence=config.scanner.min_confidence,
    )
    risk_manager = RiskManager(
        db=db,
        max_risk_per_trade=config.trading.max_risk_per_trade,
        max_daily_risk=config.trading.max_daily_risk,
        hard_stop_loss=config.trading.hard_stop_loss,
    )
    paper_trader = PaperTrader(db=db, initial_balance=config.trading.initial_balance)
    telegram = TelegramBot(
        bot_token=config.telegram.bot_token,
        chat_id=config.telegram.chat_id,
        db=db,
        initial_balance=config.trading.initial_balance,
    )

    await binance_feed.connect()
    await news_agg.start()

    if config.fred.api_key:
        await fred_client.start()

    if config.telegram.bot_token:
        await telegram.start()

    loop_running = True

    def shutdown_handler(sig, frame):
        nonlocal loop_running
        logger.info("shutdown_signal_received")
        loop_running = False

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    logger.info("polyedge_main_loop_started")

    while loop_running:
        try:
            if telegram.paused:
                logger.debug("bot_paused_skipping_scan")
                await asyncio.sleep(config.scanner.interval_seconds)
                continue

            markets = await poly_client.get_active_markets()
            scan_results = scanner.filter_markets(markets)
            # Volume-sorted; cap bounds Haiku cost per scan cycle
            scan_results = scan_results[: config.scanner.max_markets_per_scan]

            for result in scan_results:
                category_news = news_agg.get_recent(result.category)
                if not category_news:
                    # No relevant news = no information edge, skip API spend
                    continue
                cat_news_str = build_news_context(category_news)

                haiku_signal = await claude_analyst.quick_filter(result, cat_news_str)

                if not haiku_signal.relevant or haiku_signal.urgency < 3:
                    continue

                logger.info(
                    "haiku_flagged",
                    market=result.market.question[:50],
                    urgency=haiku_signal.urgency,
                    direction=haiku_signal.direction,
                )

                macro_str = str(fred_client.get_context_dict())
                crypto_str = build_crypto_context(binance_feed)

                sonnet_signal = await claude_analyst.deep_analysis(
                    scan=result,
                    news_context=cat_news_str,
                    macro_context=macro_str,
                    crypto_context=crypto_str,
                )

                if sonnet_signal.action == TradeAction.SKIP:
                    continue

                sized = signal_engine.evaluate(result, sonnet_signal)
                if not sized:
                    continue

                portfolio_value = await paper_trader.get_portfolio_value()
                approved = await risk_manager.evaluate(sized, portfolio_value)
                if not approved:
                    continue

                if config.telegram.bot_token:
                    await telegram.send_signal(approved)

                if config.trading.paper_trading:
                    await paper_trader.execute(approved)

            # Settle open paper trades against resolved markets
            open_trades = await db.get_open_trades()
            if open_trades:
                resolved: dict[str, float] = {}
                for mid in {t["market_id"] for t in open_trades}:
                    outcome = await poly_client.get_market_resolution(mid)
                    if outcome is not None:
                        resolved[mid] = outcome
                if resolved:
                    await paper_trader.check_settlements(resolved)

            await db.update_daily_stats()

            logger.info(
                "scan_cycle_complete",
                markets_scanned=len(scan_results),
                next_scan_seconds=config.scanner.interval_seconds,
            )

            await asyncio.sleep(config.scanner.interval_seconds)

        except Exception as e:
            logger.error("main_loop_error", error=str(e), exc_info=True)
            if config.telegram.bot_token:
                await telegram.send_error(str(e))
            await asyncio.sleep(60)

    logger.info("polyedge_shutting_down")
    await binance_feed.stop()
    await news_agg.stop()
    await fred_client.stop()
    if config.telegram.bot_token:
        await telegram.stop()
    await poly_client.close()
    await db.close()
    logger.info("polyedge_stopped")


if __name__ == "__main__":
    asyncio.run(main())
