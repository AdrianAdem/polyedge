from config.markets import MarketCategory, categorize_market


def test_categorizes_politics() -> None:
    assert categorize_market("Will Trump win the 2028 election?") == MarketCategory.POLITICS


def test_categorizes_crypto() -> None:
    assert categorize_market("Will Bitcoin reach $100k in May?") == MarketCategory.CRYPTO


def test_categorizes_macro() -> None:
    assert categorize_market("Will the Fed cut interest rates in June?") == MarketCategory.MACRO


def test_categorizes_sports() -> None:
    assert categorize_market("Will the Lakers win the NBA championship?") == MarketCategory.SPORTS


def test_unmatched_question_falls_back_to_other() -> None:
    assert categorize_market("Will it rain tomorrow?") == MarketCategory.OTHER


def test_matching_is_case_insensitive() -> None:
    assert categorize_market("WILL BITCOIN CRASH?") == MarketCategory.CRYPTO
