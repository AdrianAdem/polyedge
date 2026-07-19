"""Market categorisation by keyword matching.

Categories drive both news filtering and which context blocks get attached to a
prompt. Keyword matching is deliberate — an LLM call per market purely to label
it would cost more than the signal is worth.
"""

from __future__ import annotations

from enum import Enum


class MarketCategory(str, Enum):
    POLITICS = "politics"
    CRYPTO = "crypto"
    MACRO = "macro"
    SPORTS = "sports"
    OTHER = "other"


CATEGORY_KEYWORDS: dict[MarketCategory, list[str]] = {
    MarketCategory.POLITICS: [
        "president",
        "election",
        "senate",
        "congress",
        "trump",
        "biden",
        "democrat",
        "republican",
        "vote",
        "governor",
        "mayor",
        "impeach",
        "supreme court",
        "cabinet",
        "legislation",
        "bill",
        "executive order",
        "primary",
        "nominee",
        "inaugur",
    ],
    MarketCategory.CRYPTO: [
        "bitcoin",
        "btc",
        "ethereum",
        "eth",
        "crypto",
        "solana",
        "sol",
        "altcoin",
        "defi",
        "nft",
        "blockchain",
        "token",
        "halving",
        "stablecoin",
        "usdt",
        "usdc",
        "binance",
        "coinbase",
    ],
    MarketCategory.MACRO: [
        "fed",
        "interest rate",
        "inflation",
        "cpi",
        "gdp",
        "unemployment",
        "recession",
        "fomc",
        "treasury",
        "bond",
        "yield",
        "tariff",
        "trade war",
        "sanctions",
        "oil",
        "opec",
        "gold",
    ],
    MarketCategory.SPORTS: [
        "nba",
        "nfl",
        "mlb",
        "nhl",
        "soccer",
        "football",
        "basketball",
        "baseball",
        "tennis",
        "golf",
        "ufc",
        "boxing",
        "f1",
        "formula",
        "world cup",
        "super bowl",
        "champion",
        "playoff",
    ],
}


def categorize_market(question: str) -> MarketCategory:
    q_lower = question.lower()
    scores: dict[MarketCategory, int] = {}
    for cat, keywords in CATEGORY_KEYWORDS.items():
        scores[cat] = sum(1 for kw in keywords if kw in q_lower)

    best = max(scores, key=lambda c: scores[c])
    if scores[best] == 0:
        return MarketCategory.OTHER
    return best
