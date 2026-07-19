"""Market scanner.

Narrows the full market list down to tradeable candidates by volume, time to
expiry and category, ordered by volume so the per-scan cap keeps the most
liquid markets.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

from config.markets import categorize_market
from storage.models import MarketInfo, ScanResult

logger = structlog.get_logger()


class MarketScanner:
    def __init__(self, min_volume: float = 50_000) -> None:
        self.min_volume = min_volume

    def filter_markets(self, markets: list[MarketInfo]) -> list[ScanResult]:
        results: list[ScanResult] = []
        now = datetime.now(UTC)

        for m in markets:
            hours_left = (m.end_date - now).total_seconds() / 3600
            if hours_left <= 1:
                continue

            if m.volume < self.min_volume:
                continue

            category = categorize_market(m.question)

            results.append(
                ScanResult(
                    market=m,
                    category=category.value,
                    current_price=m.price_yes,
                    volume=m.volume,
                    time_to_expiry_hours=hours_left,
                )
            )

        results.sort(key=lambda r: r.volume, reverse=True)
        logger.info("scanner_filtered", total=len(markets), passed=len(results))
        return results
