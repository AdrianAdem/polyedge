"""Polymarket Gamma API client.

Read-only market data over the public Gamma endpoint — no authentication and no
wallet required, because the bot never places orders while in paper mode.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import structlog

from storage.models import MarketInfo

logger = structlog.get_logger()

GAMMA_API_BASE = "https://gamma-api.polymarket.com"
MAX_OFFSET = 2000  # volume-sorted, so everything past this is below any sane threshold


class PolymarketClient:
    """Fetches open markets and resolution outcomes from the Gamma API."""

    def __init__(self, min_volume: float = 50_000) -> None:
        self.min_volume = min_volume
        self._client = httpx.AsyncClient(timeout=30.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def get_active_markets(self) -> list[MarketInfo]:
        """Return open markets above the volume floor with >24h until expiry.

        Paginates volume-descending and stops at ``MAX_OFFSET``; markets past
        that point cannot clear any usable volume threshold.
        """
        markets: list[MarketInfo] = []
        offset = 0
        limit = 100

        while offset < MAX_OFFSET:
            try:
                resp = await self._client.get(
                    f"{GAMMA_API_BASE}/markets",
                    params={
                        "closed": "false",
                        "limit": limit,
                        "offset": offset,
                        "order": "volume",
                        "ascending": "false",
                        "volume_num_min": self.min_volume,
                    },
                )
                resp.raise_for_status()
                data = resp.json()

                if not data:
                    break

                for m in data:
                    market = self._parse_market(m)
                    if market and self._passes_filter(market):
                        markets.append(market)

                if len(data) < limit:
                    break
                offset += limit

            except httpx.HTTPError as e:
                logger.error("polymarket_api_error", error=str(e), offset=offset)
                break

        logger.info("polymarket_markets_fetched", count=len(markets))
        return markets

    async def get_market(self, market_id: str) -> MarketInfo | None:
        try:
            resp = await self._client.get(f"{GAMMA_API_BASE}/markets/{market_id}")
            resp.raise_for_status()
            return self._parse_market(resp.json())
        except httpx.HTTPError as e:
            logger.error("polymarket_market_error", market_id=market_id, error=str(e))
            return None

    async def get_market_resolution(self, market_id: str) -> float | None:
        """Return final YES outcome price for a closed market, None if still open."""
        try:
            resp = await self._client.get(f"{GAMMA_API_BASE}/markets/{market_id}")
            resp.raise_for_status()
            data = resp.json()
            if not data.get("closed"):
                return None
            outcomes = data.get("outcomePrices") or data.get("outcome_prices") or []
            if isinstance(outcomes, str):
                outcomes = json.loads(outcomes)
            return float(outcomes[0]) if outcomes else None
        except (httpx.HTTPError, ValueError, IndexError) as e:
            logger.warning("polymarket_resolution_error", market_id=market_id, error=str(e))
            return None

    def _parse_market(self, data: dict) -> MarketInfo | None:
        try:
            end_str = data.get("endDate") or data.get("end_date_iso")
            if not end_str:
                return None

            end_date = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            now = datetime.now(UTC)
            if end_date <= now:
                return None

            volume = float(data.get("volume", 0) or 0)

            outcomes = data.get("outcomePrices") or data.get("outcome_prices")
            if isinstance(outcomes, str):
                outcomes = json.loads(outcomes)

            if not outcomes or len(outcomes) < 2:
                return None

            price_yes = float(outcomes[0])
            price_no = float(outcomes[1])
            spread = abs(price_yes + price_no - 1.0)

            tokens = data.get("clobTokenIds") or data.get("clob_token_ids") or []
            if isinstance(tokens, str):
                tokens = json.loads(tokens)

            return MarketInfo(
                market_id=str(data.get("id", "")),
                question=data.get("question", ""),
                end_date=end_date,
                volume=volume,
                price_yes=price_yes,
                price_no=price_no,
                spread=spread,
                token_id_yes=tokens[0] if len(tokens) > 0 else "",
                token_id_no=tokens[1] if len(tokens) > 1 else "",
            )
        except (ValueError, KeyError, IndexError) as e:
            logger.warning("polymarket_parse_error", error=str(e))
            return None

    def _passes_filter(self, market: MarketInfo) -> bool:
        now = datetime.now(UTC)
        hours_left = (market.end_date - now).total_seconds() / 3600
        return market.volume >= self.min_volume and hours_left > 24
