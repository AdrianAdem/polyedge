"""Quality gate between model output and risk sizing.

Turns a raw LLM verdict into a ranked, tradeable signal — or rejects it.
"""

from __future__ import annotations

import structlog

from storage.models import ScanResult, SizedSignal, SonnetSignal, TradeAction

logger = structlog.get_logger()


class SignalEngine:
    """Applies edge, confidence and liquidity thresholds to model verdicts."""

    def __init__(self, min_edge: float = 0.10, min_confidence: float = 0.70) -> None:
        self.min_edge = min_edge
        self.min_confidence = min_confidence

    def evaluate(self, scan: ScanResult, signal: SonnetSignal) -> SizedSignal | None:
        """Return a scored signal, or None if it fails any threshold.

        Score is ``|edge| * confidence`` so the risk manager can rank competing
        signals. Position size stays 0.0 here — sizing is the risk manager's job.
        """
        if signal.action == TradeAction.SKIP:
            return None

        if abs(signal.edge) < self.min_edge:
            logger.debug("signal_edge_too_low", edge=signal.edge, market=scan.market.question[:40])
            return None

        if signal.confidence < self.min_confidence:
            logger.debug(
                "signal_confidence_low",
                confidence=signal.confidence,
                market=scan.market.question[:40],
            )
            return None

        if scan.market.spread > 0.05:
            logger.debug(
                "signal_spread_too_wide",
                spread=scan.market.spread,
                market=scan.market.question[:40],
            )
            return None

        score = abs(signal.edge) * signal.confidence

        return SizedSignal(
            market=scan.market,
            action=signal.action,
            edge=signal.edge,
            confidence=signal.confidence,
            position_size=0.0,  # risk manager sets this
            reasoning=signal.reasoning,
            key_factors=signal.key_factors,
            score=score,
        )
