"""Loch Fyne Whiskies parser (UK overseas benchmark).

Source: https://www.lochfynewhiskies.com — Magento search is a plain
server-rendered GET:
  /catalogsearch/result/index/?q=<term>&p=<page>
Verified by live curl fetch 11 Nov 2026: 24 cards per page
(`class="item product product-item"`), 461 results for
"glenfiddich 18". Per card: the name is the `product-item-link`
anchor text; the selling price is the price-box
`<span class="price">£99.50</span>` inside the
`data-price-type="finalPrice"` wrapper — NEVER the photo anchor's
`data-price` (an out-of-stock card still carries `data-price` while
its price-box says "Sold Out" and has no `£` price); stock is
`data-dimension10="In stock"` / `"Out of stock"` on the photo
anchor. Two price-box traps, both verified on the fixture: a card
may carry a struck-through `oldPrice` span after the final price
(Jura 18: final £60.00, old £79.00), and a "from" price nests a
`<span class="price-from">from</span>` inside the price span
(Bunnahabhain 18: from £120.00) — the price regex anchors on
`finalPrice` and tolerates the nested "from" span, so neither trap
misprices a card. The size is NOT on the search page; it is on the
product page as `<span class="key-attribute size">70cl</span>`, so
the driver fetches the product page for size only for canonical
matches (bounded by the search pages scanned).

Prices are GBP, VAT included. Only in-stock prices count downstream.
"""

import asyncio
import re
from datetime import datetime, timezone
from html import unescape as html_unescape
from urllib.parse import quote_plus, urljoin

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

RETAILER = "Loch Fyne Whiskies"
COUNTRY = "UK"
CURRENCY = "GBP"
BASE_URL = "https://www.lochfynewhiskies.com"
MAX_SEARCH_PAGES = 5

_CARD_SPLIT = 'class="item product product-item"'
_NAME_RE = re.compile(
    r'class="product-item-link" href="([^"]+)"[^>]*>([^<]+)</a>')
_PRICE_RE = re.compile(
    r'data-price-type="finalPrice"[^>]*><span class="price">'
    r'(?:<span class="price-from">from</span>\s*)?£([\d,]+(?:\.\d{2})?)'
    r'</span>')
_STOCK_RE = re.compile(r'data-dimension10="([^"]+)"')
_SIZE_RE = re.compile(r'<span class="key-attribute size">([^<]+)</span>')


def search_url(query, page_no=1):
    """Loch Fyne Magento search URL for one query and page."""
    assert isinstance(query, str) and query.strip(), "query required"
    assert page_no >= 1, "page number must be >= 1"
    url = (BASE_URL + "/catalogsearch/result/index/?q="
           + quote_plus(query.strip()) + "&p=%d" % page_no)
    assert url.startswith(BASE_URL), "search URL must stay on Loch Fyne"
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

    The size is the key-attribute span (e.g. "70cl").
    """
    assert isinstance(html, str) and html, "page HTML required"
    size_text = _first(_SIZE_RE, html)
    if size_text is None:
        return None
    size_ml = parse_size_ml(size_text)
    assert size_ml is None or size_ml > 0
    return size_ml


def _parse_card(chunk, page_url, seen_at):
    """One search card -> (name, price, url, in_stock), or None.

    Size is not on the card; the caller fills it from the product
    page. A card with no final-price span (e.g. "Sold Out") is
    dropped — the photo anchor's data-price is never used.
    """
    assert isinstance(chunk, str) and chunk
    assert isinstance(page_url, str) and page_url.startswith("http")
    assert seen_at, "seen_at timestamp required"
    match = _NAME_RE.search(chunk)
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
    stock = _first(_STOCK_RE, chunk)
    in_stock = stock == "In stock"
    url = urljoin(BASE_URL, href)
    assert url.startswith(BASE_URL), "product URL must stay on Loch Fyne"
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


async def search(query, wanted=None, max_pages=MAX_SEARCH_PAGES,
                 delay_s=PAGE_DELAY_S, seen_at="",
                 search_url_override=None):
    """Search Loch Fyne Whiskies for one query (plain urllib, no
    browser). Returns (prices, health).

    Bounded: at most max_pages search pages; stops early when a page
    yields no cards. Size is resolved by a product-page fetch only
    for in-stock cards whose canonical name is in `wanted`; when
    `wanted` is None (unit tests) every parsed card is resolved.
    A card whose product page yields no size is dropped (an
    unverified size never becomes a benchmark). A bot-wall page ends
    the run as "blocked".
    """
    assert isinstance(query, str) and query.strip(), "query required"
    assert wanted is None or isinstance(wanted, (set, frozenset, list,
                                                 tuple))
    assert 1 <= max_pages <= MAX_PAGES, "max_pages out of bounds"
    assert delay_s >= 0, "delay must be non-negative"
    assert search_url_override is None or search_url_override.startswith(
        "http")
    stamp = seen_at or datetime.now(timezone.utc).isoformat()
    assert stamp, "seen_at timestamp required"
    base = search_url_override or search_url(query)
    assert base.startswith("http"), "search URL must be absolute"
    all_cards = []
    pages_scanned = 0
    tiles_seen = 0
    blocked_wall = ""
    blocked_at = 0
    for page_no in range(1, max_pages + 1):
        url = base if page_no == 1 else search_url(query, page_no)
        if search_url_override is not None and page_no > 1:
            break
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
        all_cards.extend(cards)
        assert len(all_cards) <= max_pages * MAX_TILES_PER_PAGE, \
            "card cap hit"
        if seen == 0:
            break
        if page_no >= max_pages:
            break
        await asyncio.sleep(delay_s)
    all_prices = []
    for name, price, product_url, in_stock in all_cards:
        if not in_stock:
            continue
        if wanted is not None and canonical_from_name(name) not in wanted:
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
    if blocked_wall:
        status = "blocked"
        detail = "bot wall (%s) at page %d" % (blocked_wall, blocked_at)
    elif pages_scanned == 0:
        status = "blocked"
        detail = "no search page loaded (blocked at page %d)" % blocked_at
    elif len(all_prices) == 0:
        status = "zero-items"
        detail = "pages loaded but no sized in-stock prices parsed"
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
