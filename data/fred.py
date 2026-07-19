"""FRED macroeconomic data client.

Pulls CPI, Fed funds rate, unemployment and GDP growth on an hourly cycle and
exposes them as prompt context. Series are fetched in a thread since fredapi is
synchronous.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import structlog

from storage.models import MacroData

logger = structlog.get_logger()

SERIES_IDS = {
    "cpi": "CPIAUCSL",
    "fed_funds_rate": "FEDFUNDS",
    "unemployment": "UNRATE",
    "gdp_growth": "A191RL1Q225SBEA",
}


class FredClient:
    def __init__(self, api_key: str, poll_interval: int = 3600) -> None:
        self.api_key = api_key
        self.poll_interval = poll_interval
        self._data = MacroData()
        self._running = False
        self._task: asyncio.Task | None = None

    @property
    def data(self) -> MacroData:
        return self._data

    async def start(self) -> None:
        self._running = True
        await self._fetch()
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("fred_client_started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _poll_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self.poll_interval)
            try:
                await self._fetch()
            except Exception as e:
                logger.error("fred_poll_error", error=str(e))

    async def _fetch(self) -> None:
        loop = asyncio.get_event_loop()
        try:
            from fredapi import Fred

            fred = Fred(api_key=self.api_key)

            for field_name, series_id in SERIES_IDS.items():
                try:
                    series = await loop.run_in_executor(
                        None,
                        lambda sid=series_id: fred.get_series(sid, observation_start="2024-01-01"),
                    )
                    if series is not None and not series.empty:
                        setattr(self._data, field_name, float(series.iloc[-1]))
                except Exception as e:
                    logger.warning("fred_series_error", series=series_id, error=str(e))

            self._data.last_updated = datetime.now(UTC)
            logger.info(
                "fred_data_updated",
                cpi=self._data.cpi,
                fed_rate=self._data.fed_funds_rate,
                unemployment=self._data.unemployment,
                gdp=self._data.gdp_growth,
            )
        except ImportError:
            logger.error("fredapi_not_installed")
        except Exception as e:
            logger.error("fred_fetch_error", error=str(e))

    def get_context_dict(self) -> dict:
        return {
            "cpi": self._data.cpi,
            "fed_funds_rate": self._data.fed_funds_rate,
            "unemployment_rate": self._data.unemployment,
            "gdp_growth": self._data.gdp_growth,
            "last_updated": self._data.last_updated.isoformat()
            if self._data.last_updated
            else None,
        }
