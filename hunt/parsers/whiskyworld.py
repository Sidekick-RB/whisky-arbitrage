"""The Whisky World parser (UK overseas benchmark).

Source: https://www.thewhiskyworld.com/search/<terms> (path-based
search; the site normalises e.g. "ballantines23" to "ballantines-23").
Verified by live curl fetch 9 Nov 2026: search cards are
server-rendered; the title anchor sits inside
"product__details__title--branded"; the FIRST inc-VAT price in the
card is the selling price (a "Was" RRP and a "Save" amount follow it
in the same card, so the first match is the one to take); the size is
NOT on the card — it is on the product page in a "Bottle Size (cl)"
details row, so the driver fetches the product page for each card
(bounded to MAX_SIZE_FETCHES). Stock rule (assumption, documented):
a card is out of stock only when it carries the site's own
out-of-stock markers ("product_zero_stock" / "--out-of-stock",
classes present in the site stylesheet); no out-of-stock card was
observed on the live page, so the negative path is unvalidated.

The driver is plain urllib, not a browser: both the search and the
product pages are server-rendered and curl-friendly, and the 10 Nov
2026 Fargate run returned zero cards from the browser driver while
the same pages parsed fine via curl — the datacenter IP, not the
selectors, was the problem.

Prices are GBP, VAT included. Only in-stock prices count downstream.
"""

import asyncio
import re
from datetime import datetime, timezone
from html import unescape as html_unescape
from urllib.parse import urljoin

from ..models import OverseasPrice, canonical_from_name
from ..report import ParserHealth
from .base import (
    MAX_LISTINGS,
    MAX_PAGES,
    MAX_TILES_PER_PAGE,
    PAGE_DELAY_S,
    detect_bot_wall,
    fetch_text,
    parse_price,
    parse_size_ml,
)

RETAILER = "The Whisky World"
COUNTRY = "UK"
CURRENCY = "GBP"
BASE_URL = "https://www.thewhiskyworld.com"
SEARCH_URL = BASE_URL + "/search/"

_CARD_SPLIT = 'class="product__details__holder"'
_TITLE_RE = re.compile(
    r'product__details__title--branded">\s*<a[^>]*href="([^"]+)"'
    r'[^>]*title="([^"]+)"', re.S)
_PRICE_RE = re.compile(
    r'product-content__price--inc"\s*>\s*<span\s*class="GBP"\s*>\s*'
    r'£([\d,]+(?:\.\d{2})?)', re.S)
_SIZE_ROW_RE = re.compile(
    r'Bottle Size \(cl\)</td>\s*<td[^>]*>.*?<a[^>]*>([^<]+)</a>', re.S)
_OUT_OF_STOCK = ("product_zero_stock", "--out-of-stock")
MAX_SEARCH_PAGES = 1
MAX_SIZE_FETCHES = 5


def search_url(query):
    """Search URL for one query string (words joined with hyphens)."""
    assert isinstance(query, str) and query.strip(), "query required"
    terms = re.sub(r"[^a-z0-9]+", "-", query.strip().lower()).strip("-")
    assert terms, "query must carry a search term"
    url = SEARCH_URL + terms
    assert url.startswith(BASE_URL), "search URL must stay on Whisky World"
    return url


def _first(pattern, chunk):
    """First regex group match in a chunk, or None."""
    assert pattern is not None and isinstance(chunk, str)
    match = pattern.search(chunk)
    if match is None:
        return None
    result = match.group(1)
    assert isinstance(result, str)
    return result


def parse_product_html(html):
    """Bottle size in ml from a product page, or None.

    The size is the "Bottle Size (cl)" details row (e.g. "70cl").
    """
    assert isinstance(html, str) and html, "page HTML required"
    size_text = _first(_SIZE_ROW_RE, html)
    if size_text is None:
        return None
    size_ml = parse_size_ml(size_text)
    assert size_ml is None or size_ml > 0
    return size_ml


def _parse_card(chunk, page_url, seen_at):
    """One search card -> (name, price, url, in_stock), or None.

    Size is not on the card; the caller fills it from the product
    page. The FIRST inc-VAT price is the selling price.
    """
    assert isinstance(chunk, str) and chunk
    assert isinstance(page_url, str) and page_url.startswith("http")
    assert seen_at, "seen_at timestamp required"
    match = _TITLE_RE.search(chunk)
    if match is None:
        return None
    href = match.group(1)
    name = html_unescape(match.group(2).strip())
    assert name, "card name must be non-empty after strip"
    price_text = _first(_PRICE_RE, chunk)
    if price_text is None:
        return None
    price = parse_price(price_text)
    if price is None:
        return None
    lowered = chunk.lower()
    in_stock = not any(mark in lowered for mark in _OUT_OF_STOCK)
    url = urljoin(BASE_URL, href)
    assert url.startswith(BASE_URL), "product URL must stay on Whisky World"
    return name, price, url, in_stock


def parse_search_html(html, page_url, seen_at):
    """Parse one search page. Returns (cards, tiles_seen).

    Each card is a (name, price, url, in_stock) tuple; size is filled
    in by the driver from the product page. Raises
    ValueError("blocked:<wall>") on a bot-wall challenge page.
    """
    assert isinstance(html, str) and html, "page HTML required"
    assert isinstance(page_url, str) and page_url.startswith("http")
    assert seen_at, "seen_at timestamp required"
    wall = detect_bot_wall(html)
    assert wall is None or isinstance(wall, str)
    if wall is not None:
        raise ValueError("blocked:" + wall)
    chunks = html.split(_CARD_SPLIT)
    assert len(chunks) <= MAX_TILES_PER_PAGE + 1, "too many cards on page"
    cards = []
    for chunk in chunks[1:]:
        card = _parse_card(chunk, page_url, seen_at)
        if card is not None:
            cards.append(card)
    assert len(cards) <= len(chunks) - 1, "cards cannot exceed chunks"
    return cards, len(chunks) - 1


_PAGE_TITLE_RE = re.compile(r"<title[^>]*>([^<]*)</title>", re.I)


def _page_title(html):
    """Page <title> text, or None — a diagnostic for zero-card pages.

    When a search parses no cards, the title distinguishes a
    challenge page from genuinely empty results in the next run's
    health detail."""
    assert isinstance(html, str), "html must be a string"
    match = _PAGE_TITLE_RE.search(html)
    if match is None:
        return None
    title = html_unescape(match.group(1)).strip()
    assert isinstance(title, str)
    return title or None


async def search(query, max_pages=MAX_SEARCH_PAGES,
                 delay_s=PAGE_DELAY_S, seen_at="",
                 search_url_override=None):
    """Search The Whisky World for one query (plain urllib, no
    browser). Returns (prices, health).

    Bounded: at most max_pages search pages; at most MAX_SIZE_FETCHES
    product-page fetches for sizes. A card whose product page yields
    no size is dropped (an unverified size never becomes a benchmark).
    A bot-wall page ends the run as "blocked". A search page that
    loads but yields no cards records its <title> so the next run
    can tell a challenge page from empty results.
    """
    assert isinstance(query, str) and query.strip(), "query required"
    assert 1 <= max_pages <= MAX_PAGES, "max_pages out of bounds"
    assert delay_s >= 0, "delay must be non-negative"
    assert search_url_override is None or search_url_override.startswith(
        "http")
    stamp = seen_at or datetime.now(timezone.utc).isoformat()
    assert stamp, "seen_at timestamp required"
    base = search_url_override or search_url(query)
    assert base.startswith("http"), "search URL must be absolute"
    all_prices = []
    pages_scanned = 0
    tiles_seen = 0
    blocked_wall = ""
    blocked_at = 0
    first_title = None
    for page_no in range(1, max_pages + 1):
        url = base if page_no == 1 else base + "?page=" + str(page_no)
        html = fetch_text(url)
        if html is None:
            blocked_at = page_no
            break
        try:
            cards, seen = parse_search_html(html, url, stamp)
        except ValueError as exc:
            if str(exc).startswith("blocked:"):
                blocked_wall = str(exc)[len("blocked:"):]
                blocked_at = page_no
                break
            raise
        tiles_seen += seen
        pages_scanned += 1
        if not cards and first_title is None:
            first_title = _page_title(html)
        for index, (name, price, product_url, in_stock) in enumerate(
                cards):
            if index >= MAX_SIZE_FETCHES:
                break
            if not in_stock:
                continue
            product_html = fetch_text(product_url)
            if product_html is None:
                continue
            size_ml = parse_product_html(product_html)
            if size_ml is None:
                continue
            all_prices.append(OverseasPrice(
                retailer=RETAILER, country=COUNTRY, currency=CURRENCY,
                price=price, size_ml=size_ml, in_stock=in_stock,
                url=product_url, name=name))
            assert len(all_prices) <= MAX_LISTINGS, "price cap hit"
        if page_no >= max_pages:
            break
        await asyncio.sleep(delay_s)
    if blocked_wall:
        status = "blocked"
        detail = "bot wall (%s) at page %d" % (blocked_wall, blocked_at)
    elif pages_scanned == 0:
        status = "blocked"
        detail = "no search page loaded (blocked at page %d)" % blocked_at
    elif len(all_prices) == 0:
        status = "zero-items"
        detail = "pages loaded but no sized in-stock prices parsed"
        if first_title is not None:
            detail += " (first page title: %s)" % first_title[:80]
    else:
        status = "ok"
        detail = "%d cards seen, %d priced" % (tiles_seen, len(all_prices))
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
