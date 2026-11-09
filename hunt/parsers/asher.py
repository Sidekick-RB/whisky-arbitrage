"""Asher parser (Shopify products.json feed).

Source: https://asher.sg — a wine-heavy catalogue of 4000+ products
in the all-products feed (16+ pages of 250). The known bottles are
scattered deep: Hibiki 30 + Glenmorangie 25 on page 12, Redbreast 21
on page 13, Chivas Regal 25 on page 15. The whisky collection handle
(/collections/whisky) does not exist (0 products). Verified 9 Nov
2026: Chivas 25 S$309, Redbreast 21 S$358, Hibiki 30 S$5,888,
Glenmorangie 25 (750ml) S$699, Singleton 15 S$85. The page cap is
generous (24 pages = 6000 products); a cap hit is reported as
"partial", never silently complete.
"""

from .shopify import FEED_LIMIT, parse_products_json, scrape_feed
from .base import PAGE_DELAY_S

RETAILER = "Asher"
BASE_URL = "https://asher.sg"
FEED_URLS = (BASE_URL + "/products.json?limit=%d" % FEED_LIMIT,)
MAX_PAGES = 24


def parse_feed_json(data, seen_at):
    """Parse one Asher products.json page (see shopify.parse_products_json)."""
    assert isinstance(data, dict), "feed data must be a dict"
    assert seen_at, "seen_at timestamp required"
    listings, has_next, seen, skipped = parse_products_json(
        data, BASE_URL, RETAILER, seen_at)
    assert len(listings) + skipped == seen
    return listings, has_next, seen, skipped


async def scrape(seen_at="", max_pages=MAX_PAGES, delay_s=PAGE_DELAY_S):
    """Scrape the Asher all-products feed. Returns (listings, health)."""
    assert 1 <= max_pages <= 40, "max_pages out of bounds"
    assert delay_s >= 0, "delay must be non-negative"
    listings, health = await scrape_feed(
        FEED_URLS, BASE_URL, RETAILER, max_pages, delay_s, seen_at)
    assert health.retailer == RETAILER
    return listings, health
