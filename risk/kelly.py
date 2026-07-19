from __future__ import annotations


def kelly_size(
    edge: float,
    odds: float,
    bankroll: float,
    fraction: float = 0.25,
) -> float:
    """
    Quarter-Kelly for conservative sizing.
    edge: estimated advantage (e.g. 0.15 = 15%)
    odds: payout on win (binary markets: (1/price) - 1)
    bankroll: current portfolio value
    fraction: Kelly fraction (0.25 = Quarter Kelly)
    """
    if odds <= 0 or edge <= 0:
        return 0.0

    kelly = edge / odds
    return max(0.0, bankroll * kelly * fraction)
