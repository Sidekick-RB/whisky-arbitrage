"""PIVENE parser (Shopify products.json feed).

Source: https://www.pivene.com — 1158 products in the all-products
feed (5 pages). A wine-heavy catalogue whose whisky titles are in
wine style with no whisky keyword and often no age statement, e.g.
"Yamazaki, 2022 Limited Edition NV 700ML" and "Hakushu, Story of
The Distillery 2024 NV 700ML" — these are caught by the distillery-
name rule in shopify.is_whisky_title. Verified 9 Nov 2026: Yamazaki
2022 LE S$1,080, Hakushu 18 S$1,047, Hibiki 21 S$1,047 (sold out),
Chivas Regal 18 (750ml) S$132, Springbank 12 Cask Strength S$540.
"""

from .shopify import FEED_LIMIT, parse_products_json, scrape_feed
from .base import PAGE_DELAY_S

RETAILER = "PIVENE"
BASE_URL = "https://www.pivene.com"
FEED_URLS = (BASE_URL + "/products.json?limit=%d" % FEED_LIMIT,)
MAX_PAGES = 6


def parse_feed_json(data, seen_at):
    """Parse one PIVENE products.json page (see shopify.parse_products_json)."""
    assert isinstance(data, dict), "feed data must be a dict"
    assert seen_at, "seen_at timestamp required"
    listings, has_next, seen, skipped = parse_products_json(
        data, BASE_URL, RETAILER, seen_at)
    assert len(listings) + skipped == seen
    return listings, has_next, seen, skipped


async def scrape(seen_at="", max_pages=MAX_PAGES, delay_s=PAGE_DELAY_S):
    """Scrape the PIVENE all-products feed. Returns (listings, health)."""
    assert 1 <= max_pages <= 40, "max_pages out of bounds"
    assert delay_s >= 0, "delay must be non-negative"
    listings, health = await scrape_feed(
        FEED_URLS, BASE_URL, RETAILER, max_pages, delay_s, seen_at)
    assert health.retailer == RETAILER
    return listings, health
