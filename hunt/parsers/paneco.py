"""Paneco parser (reference pattern for all other retailers).

Source: https://www.paneco.com.sg/t/alcohol-deals/bf-spirits
(X-mas Spirits Sale tag page, 709 products, 30 pages). The page is
server-rendered HTML with one <section class="tile "> per product and
?page=N pagination; the grid also lazy-loads through a Turbo frame, so
the driver waits for the product list and network idle before reading.
Selectors are pinned to the Nov 2026 markup; the parser health check
flags zero items, so a redesign fails loudly instead of reporting
"no deals".
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
    goto_with_retries,
    new_stealth_context,
    parse_price_sgd,
    parse_size_ml,
)

RETAILER = "Paneco"
BASE_URL = "https://www.paneco.com.sg"
SALE_URL = BASE_URL + "/t/alcohol-deals/bf-spirits"

_TILE_SPLIT = '<section class="tile '
_TITLE_RE = re.compile(r'tile__title[^>]*>([^<]+)</a>')
_LINK_RE = re.compile(r'tile__link[^>]*href="([^"]+)"')
_SCHEMA_PRICE_RE = re.compile(r'itemprop="price"\s+content="([\d.]+)"')
_VISIBLE_PRICE_RE = re.compile(r'tile__price-selling">\$?([\d,]+\.\d{2})')
_SIZE_TAG_RE = re.compile(r'bottle_volume_tag">([^<]+)<')
_AVAIL_RE = re.compile(r'itemprop="availability"\s+href="https://schema\.org/(\w+)"')
_STOCK_TEXT_RE = re.compile(r'tile__in-stock-text">([^<]+)<')
_PAGE_RE = re.compile(r"\?page=(\d+)")


def page_no_from_url(url):
    """Page number from a ?page=N URL (1 when absent)."""
    assert isinstance(url, str) and url.startswith("http")
    query = parse_qs(urlparse(url).query)
    raw = query.get("page", ["1"])
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


def _parse_tile(chunk, page_url, seen_at):
    """One product tile -> Listing, or None when the tile is unusable.

    A tile without a name, URL or price is skipped, never guessed.
    """
    assert isinstance(chunk, str) and chunk
    assert isinstance(page_url, str) and page_url.startswith("http")
    assert seen_at, "seen_at timestamp required"
    name = _first(_TITLE_RE, chunk)
    href = _first(_LINK_RE, chunk)
    if name is None or href is None:
        return None
    name = html_unescape(name.strip())
    assert name, "tile name must be non-empty after strip"
    schema_price = _first(_SCHEMA_PRICE_RE, chunk)
    if schema_price is not None:
        price = parse_price_sgd(schema_price)
    else:
        visible = _first(_VISIBLE_PRICE_RE, chunk)
        price = parse_price_sgd(visible) if visible is not None else None
    if price is None:
        return None
    size_text = _first(_SIZE_TAG_RE, chunk)
    size_ml = parse_size_ml(size_text) if size_text is not None else None
    if size_ml is None:
        # Some tiles (e.g. 1L bottles) carry no volume tag; the size is
        # printed in the displayed name instead, which is verified data.
        size_ml = parse_size_ml(name)
    availability = _first(_AVAIL_RE, chunk)
    if availability is not None:
        in_stock = availability == "InStock"
    else:
        stock_text = _first(_STOCK_TEXT_RE, chunk)
        in_stock = stock_text is not None and "in stock" in stock_text.lower()
    if "back-soon" in chunk:
        in_stock = False
    url = urljoin(BASE_URL, href)
    assert url.startswith(BASE_URL), "product URL must stay on Paneco"
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
    """Parse one Paneco sale page. Returns (listings, has_next, tiles_seen).

    has_next is derived from the highest ?page=N link on the page, so a
    redesign that drops pagination stops the loop instead of spinning.
    """
    assert isinstance(html, str) and html, "page HTML required"
    assert isinstance(page_url, str) and page_url.startswith("http")
    assert seen_at, "seen_at timestamp required"
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
    """Load one sale page and return (html, reached).

    reached False means the page never loaded (blocked / network down);
    the caller turns that into a blocked health status. The grid lazy-loads
    through a Turbo frame, so wait for the product list and network idle
    before reading content.
    """
    assert page is not None, "page required"
    assert isinstance(url, str) and url.startswith("http")
    reached = await goto_with_retries(page, url)
    assert isinstance(reached, bool), "goto must return a bool"
    if not reached:
        return "", False
    try:
        await page.wait_for_selector(".product-list-item", timeout=15000)
    except Exception:
        pass
    try:
        await page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass
    html = await page.content()
    assert isinstance(html, str) and len(html) > 0, "page content required"
    return html, True


async def scrape_sale(pw, max_pages=MAX_PAGES, delay_s=PAGE_DELAY_S,
                      seen_at="", sale_url=SALE_URL):
    """Scrape the full Paneco sale tag page. Returns (listings, health).

    Bounded: at most max_pages pages, at most MAX_LISTINGS listings.
    A blocked page ends the run as "blocked"/"partial", never as "ok"
    with a silent gap. sale_url is parameterised so tests can point the
    driver at a local fixture server.
    """
    assert pw is not None, "playwright handle required"
    assert 1 <= max_pages <= MAX_PAGES, "max_pages out of bounds"
    assert delay_s >= 0, "delay must be non-negative"
    assert isinstance(sale_url, str) and sale_url.startswith("http")
    stamp = seen_at or datetime.now(timezone.utc).isoformat()
    assert stamp, "seen_at timestamp required"
    browser, context = await new_stealth_context(pw)
    assert browser is not None and context is not None
    all_listings = []
    pages_scanned = 0
    tiles_seen = 0
    blocked_at = 0
    try:
        page = await context.new_page()
        assert page is not None, "page creation failed"
        for page_no in range(1, max_pages + 1):
            url = sale_url if page_no == 1 else sale_url + "?page=" + str(page_no)
            html, reached = await _fetch_page(page, url)
            if not reached:
                blocked_at = page_no
                break
            assert html, "empty page content"
            listings, has_next, seen = parse_page_html(html, url, stamp)
            all_listings.extend(listings)
            assert len(all_listings) <= MAX_LISTINGS, "listing cap hit"
            pages_scanned += 1
            tiles_seen += seen
            if not has_next:
                break
            await asyncio.sleep(delay_s)
    finally:
        await browser.close()
    if pages_scanned == 0:
        status = "blocked"
        detail = "no Paneco sale page loaded (blocked at page %d)" % blocked_at
    elif blocked_at:
        status = "partial"
        detail = "blocked at page %d after %d pages" % (blocked_at, pages_scanned)
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
