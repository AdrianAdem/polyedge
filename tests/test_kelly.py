from risk.kelly import kelly_size


def test_positive_edge_returns_fraction_of_bankroll() -> None:
    # price 0.42 -> odds 1.381, edge 0.15, quarter Kelly on 500
    size = kelly_size(edge=0.15, odds=1.381, bankroll=500.0, fraction=0.25)
    assert 13.0 < size < 14.0


def test_zero_edge_returns_zero() -> None:
    assert kelly_size(edge=0.0, odds=1.5, bankroll=500.0) == 0.0


def test_negative_edge_returns_zero() -> None:
    assert kelly_size(edge=-0.2, odds=1.5, bankroll=500.0) == 0.0


def test_zero_odds_returns_zero_instead_of_dividing_by_zero() -> None:
    assert kelly_size(edge=0.15, odds=0.0, bankroll=500.0) == 0.0


def test_smaller_fraction_gives_smaller_position() -> None:
    quarter = kelly_size(edge=0.15, odds=1.381, bankroll=500.0, fraction=0.25)
    half = kelly_size(edge=0.15, odds=1.381, bankroll=500.0, fraction=0.5)
    assert half > quarter
