from __future__ import annotations

from storage.models import SizedSignal


class LiveTrader:
    """Placeholder for live execution. Activated after 2+ weeks profitable paper trading."""

    async def execute(self, signal: SizedSignal) -> None:
        raise NotImplementedError(
            "Live trading disabled. Enable after 2+ weeks profitable paper trading."
        )
