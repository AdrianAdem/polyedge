"""RSS news aggregator with fuzzy deduplication.

Polls a fixed feed set and drops near-duplicate headlines (the same story
syndicated across outlets) so repeated coverage cannot masquerade as
independent confirmation in the model prompt.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from difflib import SequenceMatcher

import feedparser
import structlog

from config.markets import CATEGORY_KEYWORDS, MarketCategory
from storage.models import NewsItem

logger = structlog.get_logger()

RSS_FEEDS: dict[str, str] = {
    "CNBC": "https://www.cnbc.com/id/20910258/device/rss/rss.html",
    "Yahoo Finance": "https://finance.yahoo.com/news/rssindex",
    "Federal Reserve": "https://www.federalreserve.gov/feeds/press_all.xml",
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "The Block": "https://www.theblock.co/rss.xml",
    "Politico": "https://rss.politico.com/politics-news.xml",
}

DEDUP_THRESHOLD = 0.7


class NewsAggregator:
    def __init__(self, poll_interval: int = 60) -> None:
        self.poll_interval = poll_interval
        self._items: list[NewsItem] = []
        self._running = False
        self._task: asyncio.Task | None = None
        self._max_items = 500

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("news_aggregator_started", feeds=len(RSS_FEEDS))

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
            try:
                await self._fetch_all()
            except Exception as e:
                logger.error("news_poll_error", error=str(e))
            await asyncio.sleep(self.poll_interval)

    async def _fetch_all(self) -> None:
        loop = asyncio.get_event_loop()
        for source, url in RSS_FEEDS.items():
            try:
                feed = await loop.run_in_executor(None, feedparser.parse, url)

                if not feed.entries:
                    # A feed that moved or died returns 0 entries without raising.
                    # Without this it degrades silently and looks like a quiet news day.
                    logger.warning(
                        "news_feed_empty",
                        source=source,
                        http_status=getattr(feed, "status", None),
                    )
                    continue

                new_count = 0
                for entry in feed.entries[:20]:
                    item = self._parse_entry(entry, source)
                    if item and not self._is_duplicate(item):
                        self._items.append(item)
                        new_count += 1

                if new_count:
                    logger.debug("news_fetched", source=source, new=new_count)

            except Exception as e:
                logger.warning("news_feed_error", source=source, error=str(e))

        if len(self._items) > self._max_items:
            self._items = self._items[-self._max_items :]

    def _parse_entry(self, entry: dict, source: str) -> NewsItem | None:
        title = entry.get("title", "").strip()
        if not title:
            return None

        published = entry.get("published_parsed") or entry.get("updated_parsed")
        if published:
            ts = datetime(*published[:6], tzinfo=UTC)
        else:
            ts = datetime.now(UTC)

        snippet = entry.get("summary", "")[:500]
        url = entry.get("link", "")

        return NewsItem(
            title=title,
            source=source,
            timestamp=ts,
            content_snippet=snippet,
            url=url,
        )

    def _is_duplicate(self, item: NewsItem) -> bool:
        for existing in self._items[-100:]:
            ratio = SequenceMatcher(None, item.title.lower(), existing.title.lower()).ratio()
            if ratio > DEDUP_THRESHOLD:
                return True
        return False

    def get_recent(self, category: str | None = None, limit: int = 20) -> list[NewsItem]:
        items = sorted(self._items, key=lambda x: x.timestamp, reverse=True)
        if not category:
            return items[:limit]

        # Match via category keywords, not the category name itself —
        # "crypto" as a literal rarely appears in Bitcoin headlines.
        try:
            keywords = CATEGORY_KEYWORDS[MarketCategory(category)]
        except (ValueError, KeyError):
            # OTHER/unknown: no keyword set, news matching is meaningless
            return []

        filtered = [
            i
            for i in items
            if any(kw in i.title.lower() or kw in i.content_snippet.lower() for kw in keywords)
        ]
        return filtered[:limit]

    def get_all_recent(self, limit: int = 30) -> list[NewsItem]:
        return sorted(self._items, key=lambda x: x.timestamp, reverse=True)[:limit]
