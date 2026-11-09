"""Shared helper for retailers whose pages are bot-wall challenges.

Master of Malt, Wine.com, Total Wine and Malt & Wine Asia all return
a challenge page (Vercel checkpoint, DataDome, PerimeterX, Cloudflare)
to curl, so no markup was ever captured and no parser exists for
them. These modules do not invent prices: they fetch the search page
in the browser, classify it, and report "blocked" with the wall name
— or "error" when the page loads without a challenge (meaning the
wall is gone and a real parser is now needed, not that the site has
no deals).
"""

import asyncio
from datetime import datetime, timezone

from ..report import ParserHealth
from .base import (
    MAX_PAGES,
    PAGE_DELAY_S,
    detect_bot_wall,
    goto_with_retries,
    new_stealth_context,
)

MAX_SEARCH_PAGES = 1


def classify_page(html):
    """("blocked", wall) when the page is a challenge page, else
    ("loaded", None). Pure function, unit-tested from the fixture."""
    assert isinstance(html, str), "page HTML must be a string"
    wall = detect_bot_wall(html) if html else None
    if wall is not None:
        return "blocked", wall
    return "loaded", None


async def search_blocked(pw, retailer, search_url, query,
                         max_pages=MAX_SEARCH_PAGES,
                         delay_s=PAGE_DELAY_S, seen_at="",
                         search_url_override=None):
    """Fetch one search page and classify it. Returns ([], health):
    "blocked" with the wall name, "blocked" when the page never
    loads, or "error" when it loads without a challenge (a real
    parser is then needed). Never returns a price."""
    assert pw is not None, "playwright handle required"
    assert retailer, "retailer must be non-empty"
    assert isinstance(search_url, str) and search_url.startswith("http")
    assert isinstance(query, str) and query.strip(), "query required"
    assert 1 <= max_pages <= MAX_PAGES, "max_pages out of bounds"
    assert delay_s >= 0, "delay must be non-negative"
    assert search_url_override is None or search_url_override.startswith(
        "http")
    stamp = seen_at or datetime.now(timezone.utc).isoformat()
    assert stamp, "seen_at timestamp required"
    base = search_url_override or search_url
    assert base.startswith("http"), "search URL must be absolute"
    browser, context = await new_stealth_context(pw)
    assert browser is not None and context is not None
    pages_scanned = 0
    blocked_wall = ""
    blocked_at = 0
    loaded = False
    try:
        page = await context.new_page()
        assert page is not None, "page creation failed"
        for page_no in range(1, max_pages + 1):
            reached = await goto_with_retries(page, base)
            if not reached:
                blocked_at = page_no
                break
            html = await page.content()
            assert isinstance(html, str) and len(html) > 0
            pages_scanned += 1
            kind, wall = classify_page(html)
            if kind == "blocked":
                blocked_wall = wall
                blocked_at = page_no
                break
            loaded = True
            if page_no >= max_pages:
                break
            await asyncio.sleep(delay_s)
    finally:
        await browser.close()
    if blocked_wall:
        status = "blocked"
        detail = "bot wall (%s) at page %d" % (blocked_wall, blocked_at)
    elif pages_scanned == 0:
        status = "blocked"
        detail = "no search page loaded (blocked at page %d)" % blocked_at
    elif loaded:
        status = "error"
        detail = ("page loaded without a challenge; no parser exists for "
                  "%s (markup unknown)" % retailer)
    else:
        status = "error"
        detail = "search page state unclear for " + retailer
    health = ParserHealth(
        retailer=retailer, pages_scanned=pages_scanned,
        items_found=0, status=status, detail=detail)
    assert health.items_found == 0
    return [], health
