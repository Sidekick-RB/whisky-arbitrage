"""Tipsy Topsy parser (Shopify products.json feed).

Source: https://tipsytopsy.com.sg — 494 products in the all-products
feed (2 pages). The /collections/whisky feed (102 products) is NOT
used: it omits sold-out items — the Glenfarclas 105 Cask Strength
(S$110, sold out) is in the all-feed but absent from the whisky
collection. Verified 9 Nov 2026: Johnnie Walker Blue Label 1L S$280
in stock, Lagavulin 16 S$150, Glenfarclas 21 absent (delisted).
"""

from .shopify import FEED_LIMIT, parse_products_json, scrape_feed
from .base import PAGE_DELAY_S

RETAILER = "Tipsy Topsy"
BASE_URL = "https://tipsytopsy.com.sg"
FEED_URLS = (BASE_URL + "/products.json?limit=%d" % FEED_LIMIT,)
MAX_PAGES = 3


def parse_feed_json(data, seen_at):
    """Parse one Tipsy Topsy products.json page (see shopify.parse_products_json)."""
    assert isinstance(data, dict), "feed data must be a dict"
    assert seen_at, "seen_at timestamp required"
    listings, has_next, seen, skipped = parse_products_json(
        data, BASE_URL, RETAILER, seen_at)
    assert len(listings) + skipped == seen
    return listings, has_next, seen, skipped


async def scrape(seen_at="", max_pages=MAX_PAGES, delay_s=PAGE_DELAY_S):
    """Scrape the Tipsy Topsy all-products feed. Returns (listings, health)."""
    assert 1 <= max_pages <= 40, "max_pages out of bounds"
    assert delay_s >= 0, "delay must be non-negative"
    listings, health = await scrape_feed(
        FEED_URLS, BASE_URL, RETAILER, max_pages, delay_s, seen_at)
    assert health.retailer == RETAILER
    return listings, health
