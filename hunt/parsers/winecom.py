"""Wine.com parser (US overseas benchmark) — BOT-WALL ONLY.

Source: https://www.wine.com. The live site returns a DataDome
challenge (403, "Please enable JS and disable any ad blocker") to
curl, so no markup was ever captured and no price parser exists.
This module only reports the wall; it never invents a price. The
search URL is assumed (unverified): /search?text=<term>. If the wall
ever lifts, a real parser must be written from fresh markup — the
"error" health status says so.
"""

from urllib.parse import quote_plus

from .blocked import search_blocked

RETAILER = "Wine.com"
COUNTRY = "US"
CURRENCY = "USD"
BASE_URL = "https://www.wine.com"
SEARCH_URL = BASE_URL + "/search?text="


def search_url(query):
    """Search URL for one query string (assumed format, unverified)."""
    assert isinstance(query, str) and query.strip(), "query required"
    url = SEARCH_URL + quote_plus(query.strip())
    assert url.startswith(BASE_URL), "search URL must stay on Wine.com"
    return url


async def search(pw, query, max_pages=1, delay_s=1.0, seen_at="",
                 search_url_override=None):
    """Check whether the Wine.com search page is still a bot wall.
    Returns ([], health) — never a price."""
    assert pw is not None, "playwright handle required"
    assert isinstance(query, str) and query.strip(), "query required"
    base = search_url_override or search_url(query)
    assert base.startswith("http"), "search URL must be absolute"
    return await search_blocked(
        pw, RETAILER, base, query, max_pages, delay_s, seen_at)
