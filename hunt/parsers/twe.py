"""The Whisky Exchange parser (UK overseas benchmark).

Source: https://www.thewhiskyexchange.com/search?q=<term>
Search-result markup is grounded in the old in-repo scraper
(scraper.py, Mar 2026): cards carry class "product-card", the name in
".product-card__name" and the price in ".product-card__price". The
fixture is SYNTHETIC (the live site returns a Cloudflare challenge to
curl, so no live fetch was possible); the live markup is unvalidated
and the driver reports "blocked" when the fetched page is a challenge
page instead of results. Stock rule (assumption, see fixture): a card
is out of stock only when it says so; otherwise it counts as in stock.

Prices are GBP, VAT included. Only in-stock prices count downstream.
"""

import asyncio
import re
from datetime import datetime, timezone
from html import unescape as html_unescape
from urllib.parse import quote_plus, urljoin, urlparse, parse_qs

from ..models import OverseasPrice, canonical_from_name
from ..report import ParserHealth
from .base import (
    MAX_LISTINGS,
    MAX_PAGES,
    MAX_TILES_PER_PAGE,
    PAGE_DELAY_S,
    detect_bot_wall,
    detect_currency,
    goto_with_retries,
    new_stealth_context,
    parse_price,
    parse_size_ml,
)

RETAILER = "The Whisky Exchange"
COUNTRY = "UK"
CURRENCY = "GBP"
BASE_URL = "https://www.thewhiskyexchange.com"
SEARCH_URL = BASE_URL + "/search?q="

_NAME_RE = re.compile(
    r'<[a-z0-9]+[^>]*class="[^"]*product-card__name[^"]*"[^>]*>([^<]+)<')
_LINK_RE = re.compile(r'href="(/p/[^"]+)"')
_PRICE_RE = re.compile(
    r'<[a-z0-9]+[^>]*class="[^"]*product-card__price[^"]*"[^>]*>([^<]+)<')
_PAGE_RE = re.compile(r"[?&](?:amp;)?page=(\d+)")
_OUT_OF_STOCK = ("out of stock", "sold out", "no longer available")
MAX_SEARCH_PAGES = 3


def search_url(query):
    """Search URL for one query string."""
    assert isinstance(query, str) and query.strip(), "query required"
    url = SEARCH_URL + quote_plus(query.strip())
    assert url.startswith(BASE_URL), "search URL must stay on TWE"
    return url


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


def _parse_card(chunk, page_url, seen_at):
    """One product card -> OverseasPrice, or None when unusable."""
    assert isinstance(chunk, str) and chunk
    assert isinstance(page_url, str) and page_url.startswith("http")
    assert seen_at, "seen_at timestamp required"
    name = _first(_NAME_RE, chunk)
    if name is None:
        return None
    name = html_unescape(name.strip())
    assert name, "card name must be non-empty after strip"
    price_text = _first(_PRICE_RE, chunk)
    if price_text is None:
        return None
    price = parse_price(price_text)
    if price is None:
        return None
    currency = detect_currency(price_text) or CURRENCY
    if currency != CURRENCY:
        return None
    size_ml = parse_size_ml(name)
    if size_ml is None:
        return None
    href = _first(_LINK_RE, chunk)
    if href is None:
        return None
    lowered = chunk.lower()
    in_stock = not any(mark in lowered for mark in _OUT_OF_STOCK)
    assert isinstance(in_stock, bool)
    url = urljoin(BASE_URL, href)
    assert url.startswith(BASE_URL), "product URL must stay on TWE"
    return OverseasPrice(
        retailer=RETAILER, country=COUNTRY, currency=currency,
        price=price, size_ml=size_ml, in_stock=in_stock, url=url,
        name=name)


def parse_search_html(html, page_url, seen_at):
    """Parse one TWE search page. Returns (prices, has_next, tiles_seen).

    Raises ValueError("blocked:<wall>") when the page is a bot-wall
    challenge page rather than search results.
    """
    assert isinstance(html, str) and html, "page HTML required"
    assert isinstance(page_url, str) and page_url.startswith("http")
    assert seen_at, "seen_at timestamp required"
    wall = detect_bot_wall(html)
    assert wall is None or isinstance(wall, str)
    if wall is not None:
        raise ValueError("blocked:" + wall)
    page_no = page_no_from_url(page_url)
    starts = [m.start() for m in _NAME_RE.finditer(html)]
    assert len(starts) <= MAX_TILES_PER_PAGE, "too many cards on page"
    prices = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(html)
        assert end > start, "card chunk must be non-empty"
        price = _parse_card(html[start:end], page_url, seen_at)
        if price is not None:
            prices.append(price)
    assert len(prices) <= len(starts), "prices cannot exceed cards"
    page_numbers = [int(n) for n in _PAGE_RE.findall(html)]
    max_page = max(page_numbers) if page_numbers else 1
    has_next = page_no < max_page and len(prices) > 0
    assert isinstance(has_next, bool)
    return prices, has_next, len(starts)


async def _fetch_page(page, url):
    """Load one search page and return (html, reached)."""
    assert page is not None, "page required"
    assert isinstance(url, str) and url.startswith("http")
    reached = await goto_with_retries(page, url)
    assert isinstance(reached, bool), "goto must return a bool"
    if not reached:
        return "", False
    try:
        await page.wait_for_selector(".product-card", timeout=15000)
    except Exception:
        pass
    html = await page.content()
    assert isinstance(html, str) and len(html) > 0, "page content required"
    return html, True


async def search(pw, query, max_pages=MAX_SEARCH_PAGES,
                 delay_s=PAGE_DELAY_S, seen_at="",
                 search_url_override=None):
    """Search TWE for one query. Returns (prices, health).

    Bounded: at most max_pages pages. A bot-wall page ends the run as
    "blocked", never as "ok" with a silent gap.
    """
    assert pw is not None, "playwright handle required"
    assert isinstance(query, str) and query.strip(), "query required"
    assert 1 <= max_pages <= MAX_PAGES, "max_pages out of bounds"
    assert delay_s >= 0, "delay must be non-negative"
    assert search_url_override is None or search_url_override.startswith(
        "http")
    stamp = seen_at or datetime.now(timezone.utc).isoformat()
    assert stamp, "seen_at timestamp required"
    base = search_url_override or search_url(query)
    assert base.startswith("http"), "search URL must be absolute"
    browser, context = await new_stealth_context(pw)
    assert browser is not None and context is not None
    all_prices = []
    pages_scanned = 0
    tiles_seen = 0
    blocked_wall = ""
    blocked_at = 0
    try:
        page = await context.new_page()
        assert page is not None, "page creation failed"
        for page_no in range(1, max_pages + 1):
            url = base if page_no == 1 else base + "&page=" + str(page_no)
            html, reached = await _fetch_page(page, url)
            if not reached:
                blocked_at = page_no
                break
            assert html, "empty page content"
            try:
                prices, has_next, seen = parse_search_html(
                    html, url, stamp)
            except ValueError as exc:
                if str(exc).startswith("blocked:"):
                    blocked_wall = str(exc)[len("blocked:"):]
                    blocked_at = page_no
                    break
                raise
            all_prices.extend(prices)
            assert len(all_prices) <= MAX_LISTINGS, "listing cap hit"
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
        detail = "no TWE search page loaded (blocked at page %d)" % blocked_at
    elif len(all_prices) == 0:
        status = "zero-items"
        detail = "pages loaded but no product cards parsed"
    else:
        status = "ok"
        detail = "%d cards seen, %d parsed" % (
            tiles_seen, len(all_prices))
    health = ParserHealth(
        retailer=RETAILER, pages_scanned=pages_scanned,
        items_found=len(all_prices), status=status, detail=detail)
    assert health.items_found == len(all_prices)
    return all_prices, health


def match_prices(prices, canonical):
    """Overseas prices whose listing name matches a canonical key."""
    assert isinstance(prices, (list, tuple)), "prices must be a list"
    assert isinstance(canonical, str) and canonical, "key required"
    matched = []
    for price in prices:
        assert price.name, "overseas price must carry a name"
        if canonical_from_name(price.name) == canonical:
            matched.append(price)
    assert len(matched) <= len(prices), "matches cannot exceed prices"
    return matched
