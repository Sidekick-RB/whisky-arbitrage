"""The Liquid Collection parser (Shopify products.json feed).

Source: https://theliquidcollection.com — whisky collection (690
products, 3 feed pages) plus the bourbon-american-whiskey collection
(34 products, 1 page). The global /products.json feed is NOT used:
it caps at 250 records per page and the whisky is spread across
collections. Verified 9 Nov 2026: the bourbon feed is complete (34)
and contains Jack Daniel's 10 at S$152, Blanton's Original S$125 and
Blanton's Gold S$178 — Blanton's Straight From The Barrel is absent
(delisted; do not carry the old S$198 price forward).
"""

from .shopify import FEED_LIMIT, parse_products_json, scrape_feed
from .base import PAGE_DELAY_S

RETAILER = "The Liquid Collection"
BASE_URL = "https://theliquidcollection.com"
FEED_URLS = (
    BASE_URL + "/collections/whisky/products.json?limit=%d" % FEED_LIMIT,
    BASE_URL + "/collections/bourbon-american-whiskey/products.json"
    "?limit=%d" % FEED_LIMIT,
)
MAX_PAGES = 4


def parse_feed_json(data, seen_at):
    """Parse one TLC products.json page (see shopify.parse_products_json)."""
    assert isinstance(data, dict), "feed data must be a dict"
    assert seen_at, "seen_at timestamp required"
    listings, has_next, seen, skipped = parse_products_json(
        data, BASE_URL, RETAILER, seen_at)
    assert len(listings) + skipped == seen
    return listings, has_next, seen, skipped


async def scrape(seen_at="", max_pages=MAX_PAGES, delay_s=PAGE_DELAY_S):
    """Scrape the TLC whisky + bourbon feeds. Returns (listings, health)."""
    assert 1 <= max_pages <= 40, "max_pages out of bounds"
    assert delay_s >= 0, "delay must be non-negative"
    listings, health = await scrape_feed(
        FEED_URLS, BASE_URL, RETAILER, max_pages, delay_s, seen_at)
    assert health.retailer == RETAILER
    return listings, health
