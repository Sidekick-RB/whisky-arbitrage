"""Cellarbration parser (Magento).

Source: https://cellarbration.com.sg/whisky.html (Magento category page,
?p=N pagination). The live site returns a Cloudflare challenge to
curl, so no live fetch was possible; the fixture is SYNTHETIC, built
from standard Magento 2 Luma category markup (product items carry
class "product-item", the name in ".product-item-link", the selling
price in a "price" span). Live markup is UNVALIDATED and the driver
reports "blocked" when the fetched page is a challenge page.

Price rule (Luma ordering assumption): a discounted tile renders the
old price before the special price, so the LAST "price" span in the
tile is the selling price. Stock rule (assumption): a tile is out of
stock only when it says so; otherwise it counts as in stock.
"""

import asyncio
import re
from datetime import datetime, timezone
from html import unescape as html_unescape
from urllib.parse import urljoin, urlparse, parse_qs

from ..models import Listing, canonical_from_name
from ..report import ParserHealth
from .base import (
    MAX_LISTINGS,
    MAX_PAGES,
    MAX_TILES_PER_PAGE,
    PAGE_DELAY_S,
    detect_bot_wall,
    goto_with_retries,
    new_stealth_context,
    parse_price_sgd,
    parse_size_ml,
)

RETAILER = "Cellarbration"
BASE_URL = "https://cellarbration.com.sg"
LIST_URL = BASE_URL + "/whisky.html"

_TILE_SPLIT = '<li class="item product product-item'
_NAME_RE = re.compile(
    r'<[a-z0-9]+[^>]*class="[^"]*product-item-link[^"]*"[^>]*>([^<]+)<')
_LINK_RE = re.compile(r'href="([^"]+\.html[^"]*)"')
_PRICE_RE = re.compile(r'class="price"[^>]*>([^<]+)<')
_PAGE_RE = re.compile(r"[?&]p=(\d+)")
_OUT_OF_STOCK = ("out of stock", "unavailable")


def page_no_from_url(url):
    """Page number from a ?p=N URL (1 when absent)."""
    assert isinstance(url, str) and url.startswith("http")
    query = parse_qs(urlparse(url).query)
    raw = query.get("p", ["1"])
    assert len(raw) == 1, "page parameter must appear once"
    number = int(raw[0])
    assert number >= 1, "page number must be >= 1"
    return number


def _first(pattern, chunk):
    """First regex group match in a chunk, or None."""
    assert pattern is not None and isinstance(chunk, str)
    match = pattern.search(chunk)
    if match is None:
        return None
    result = match.group(1)
    assert isinstance(result, str)
    return result


def _selling_price(chunk):
    """Selling price: the last "price" span (Luma renders old first)."""
    assert isinstance(chunk, str) and chunk
    matches = _PRICE_RE.findall(chunk)
    assert matches is not None, "findall must return a list"
    if not matches:
        return None
    price = parse_price_sgd(matches[-1])
    assert price is None or price > 0
    return price


def _parse_tile(chunk, page_url, seen_at):
    """One product tile -> Listing, or None when the tile is unusable."""
    assert isinstance(chunk, str) and chunk
    assert isinstance(page_url, str) and page_url.startswith("http")
    assert seen_at, "seen_at timestamp required"
    name = _first(_NAME_RE, chunk)
    if name is None:
        return None
    name = html_unescape(name.strip())
    assert name, "tile name must be non-empty after strip"
    href = _first(_LINK_RE, chunk)
    if href is None:
        return None
    price = _selling_price(chunk)
    if price is None:
        return None
    size_ml = parse_size_ml(name)
    lowered = chunk.lower()
    in_stock = not any(mark in lowered for mark in _OUT_OF_STOCK)
    assert isinstance(in_stock, bool)
    url = urljoin(BASE_URL, html_unescape(href))
    assert url.startswith(BASE_URL), "product URL must stay on site"
    return Listing(
        retailer=RETAILER,
        name=name,
        canonical=canonical_from_name(name),
        size_ml=size_ml,
        size_verified=size_ml is not None,
        price_sgd=price,
        in_stock=in_stock,
        traveller_only=False,
        eligibility="Singapore delivery",
        edition="",
        batch="",
        url=url,
        seen_at=seen_at,
    )


def parse_page_html(html, page_url, seen_at):
    """Parse one Cellarbration category page.

    Returns (listings, has_next, tiles_seen). Raises ValueError
    ("blocked:<wall>") when the page is a bot-wall challenge page.
    """
    assert isinstance(html, str) and html, "page HTML required"
    assert isinstance(page_url, str) and page_url.startswith("http")
    assert seen_at, "seen_at timestamp required"
    wall = detect_bot_wall(html)
    assert wall is None or isinstance(wall, str)
    if wall is not None:
        raise ValueError("blocked:" + wall)
    page_no = page_no_from_url(page_url)
    chunks = html.split(_TILE_SPLIT)
    assert len(chunks) <= MAX_TILES_PER_PAGE + 1, "too many tiles on page"
    listings = []
    tiles_seen = 0
    for chunk in chunks[1:]:
        tiles_seen += 1
        listing = _parse_tile(chunk, page_url, seen_at)
        if listing is not None:
            listings.append(listing)
    assert len(listings) <= tiles_seen, "listings cannot exceed tiles"
    page_numbers = [int(n) for n in _PAGE_RE.findall(html)]
    max_page = max(page_numbers) if page_numbers else 1
    has_next = page_no < max_page and len(listings) > 0
    assert isinstance(has_next, bool)
    return listings, has_next, tiles_seen


async def _fetch_page(page, url):
    """Load one category page and return (html, reached)."""
    assert page is not None, "page required"
    assert isinstance(url, str) and url.startswith("http")
    reached = await goto_with_retries(page, url)
    assert isinstance(reached, bool), "goto must return a bool"
    if not reached:
        return "", False
    try:
        await page.wait_for_selector(".product-item", timeout=15000)
    except Exception:
        pass
    try:
        await page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass
    html = await page.content()
    assert isinstance(html, str) and len(html) > 0, "page content required"
    return html, True


async def scrape(pw, max_pages=MAX_PAGES, delay_s=PAGE_DELAY_S,
                 seen_at="", list_url=LIST_URL):
    """Scrape the full Cellarbration whisky category.

    Returns (listings, health). Bounded: at most max_pages pages, at
    most MAX_LISTINGS listings. A bot-wall page ends the run as
    "blocked", never as "ok" with a silent gap.
    """
    assert pw is not None, "playwright handle required"
    assert 1 <= max_pages <= MAX_PAGES, "max_pages out of bounds"
    assert delay_s >= 0, "delay must be non-negative"
    assert isinstance(list_url, str) and list_url.startswith("http")
    stamp = seen_at or datetime.now(timezone.utc).isoformat()
    assert stamp, "seen_at timestamp required"
    browser, context = await new_stealth_context(pw)
    assert browser is not None and context is not None
    all_listings = []
    pages_scanned = 0
    tiles_seen = 0
    blocked_wall = ""
    blocked_at = 0
    try:
        page = await context.new_page()
        assert page is not None, "page creation failed"
        for page_no in range(1, max_pages + 1):
            url = list_url if page_no == 1 else list_url + "?p=" + str(page_no)
            html, reached = await _fetch_page(page, url)
            if not reached:
                blocked_at = page_no
                break
            assert html, "empty page content"
            try:
                listings, has_next, seen = parse_page_html(
                    html, url, stamp)
            except ValueError as exc:
                if str(exc).startswith("blocked:"):
                    blocked_wall = str(exc)[len("blocked:"):]
                    blocked_at = page_no
                    break
                raise
            all_listings.extend(listings)
            assert len(all_listings) <= MAX_LISTINGS, "listing cap hit"
            pages_scanned += 1
            tiles_seen += seen
            if not has_next:
                break
            await asyncio.sleep(delay_s)
    finally:
        await browser.close()
    if blocked_wall:
        status = "blocked"
        detail = "bot wall (%s) at page %d" % (blocked_wall, blocked_at)
    elif pages_scanned == 0:
        status = "blocked"
        detail = "no page loaded (blocked at page %d)" % blocked_at
    elif len(all_listings) == 0:
        status = "zero-items"
        detail = "pages loaded but no product tiles parsed"
    else:
        status = "ok"
        detail = "%d tiles seen, %d skipped" % (
            tiles_seen, tiles_seen - len(all_listings))
    health = ParserHealth(
        retailer=RETAILER, pages_scanned=pages_scanned,
        items_found=len(all_listings), status=status, detail=detail)
    assert health.items_found == len(all_listings)
    return all_listings, health
