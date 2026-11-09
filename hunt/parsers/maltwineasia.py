"""Malt & Wine Asia parser (SG retailer) — BOT-WALL ONLY.

Source: https://maltwineasia.com. The live site returns a Cloudflare
challenge (403) to curl, so no markup was ever captured and no price
parser exists. This module only reports the wall; it never invents a
price. Three bottles from the watchlist are affected (Macallan Sherry
Oak 18 2020 Release S$720, Glen Scotia 25 S$580, Glenfiddich 40
S$4,888 — all unverified since 9 Nov 2026). The search URL is
unknown, so the module probes the home page. If the wall ever lifts,
a real parser must be written from fresh markup — the "error" health
status says so.
"""

from .blocked import search_blocked

RETAILER = "Malt & Wine Asia"
BASE_URL = "https://maltwineasia.com"


def search_url(query):
    """Probe URL for this retailer (home page; search URL unknown)."""
    assert isinstance(query, str) and query.strip(), "query required"
    return BASE_URL + "/"


async def search(pw, query, max_pages=1, delay_s=1.0, seen_at="",
                 search_url_override=None):
    """Check whether the MWA site is still a bot wall.
    Returns ([], health) — never a price."""
    assert pw is not None, "playwright handle required"
    assert isinstance(query, str) and query.strip(), "query required"
    base = search_url_override or search_url(query)
    assert base.startswith("http"), "search URL must be absolute"
    return await search_blocked(
        pw, RETAILER, base, query, max_pages, delay_s, seen_at)
