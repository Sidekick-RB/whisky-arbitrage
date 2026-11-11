"""Mash & Grape parser (US overseas benchmark).

Source: https://www.mashandgrape.com — Shopify storefront. Search
is a plain server-rendered GET: /search?q=<query>, a single page
of BC Smart Filter cards. Verified against a live fixture 11 Nov
2026: cards split on `bc-sf-filter-product-item-inner` — 14
occurrences, but the 14th is a client-side `{{itemTitle}}` template,
so only 13 real cards; the name is the
`bc-sf-filter-product-item-title` text; the price is the
`bc-sf-filter-product-item-regular-price` text, which carries a
space after the dollar sign (`$ 49.99`); the out-of-stock marker
is the `soldout bc-sf-filter-label` label (8 of the 13 cards in
the fixture). There are no pagination links: MAX_SEARCH_PAGES = 1.

Size comes from the per-product `.js` endpoint (product URL path
+ ".js", query stripped), resolved via hunt.parsers.shopify_js —
the GF18 payload carries a structured Size option (['750ml']).
The .js variant's price WINS over the card price, and its
`available` flag confirms stock: the GF18 .js says available
False, so GF18 is dropped as out of stock.

Prices are USD, tax excluded. Only in-stock prices count downstream.
"""

import asyncio
import re
from datetime import datetime, timezone
from html import unescape as html_unescape
from urllib.parse import quote_plus, urljoin, urlparse

from ..models import OverseasPrice, canonical_from_name
from ..report import ParserHealth
from .base import (
    MAX_LISTINGS,
    MAX_PAGES,
    PAGE_DELAY_S,
    detect_bot_wall,
    fetch_text,
    parse_price,
)
from .shopify_js import fetch_product_js, product_js_url, resolve_product_js

RETAILER = "Mash & Grape"
COUNTRY = "US"
CURRENCY = "USD"
BASE_URL = "https://www.mashandgrape.com"
MAX_SEARCH_PAGES = 1

_CARD_SPLIT = "bc-sf-filter-product-item-inner"
_NAME_RE = re.compile(r'bc-sf-filter-product-item-title">([^<]+)</a>')
_PRICE_RE = re.compile(
    r'bc-sf-filter-product-item-regular-price">\$\s?([\d,]+\.\d{2})')
_LINK_RE = re.compile(r'href="(/products/[^"]+)"')
_OUT_OF_STOCK = "soldout bc-sf-filter-label"


def search_url(query, page_no=1):
    """Mash & Grape search URL for one query (single page)."""
    assert isinstance(query, str) and query.strip(), "query required"
    assert page_no == 1, "Mash & Grape search fits on one page"
    url = BASE_URL + "/search?q=" + quote_plus(query.strip())
    assert url.startswith(BASE_URL), "search URL must stay on Mash & Grape"
    return url


def _parse_card(chunk):
    """One search card -> (name, card price, url, in_stock), or None.

    The client-side `{{itemTitle}}` template chunk is not a card.
    """
    assert isinstance(chunk, str) and chunk, "card chunk required"
    name_match = _NAME_RE.search(chunk)
    if name_match is None:
        return None
    name = html_unescape(name_match.group(1)).strip()
    if not name or "{{" in name:
        return None
    price_match = _PRICE_RE.search(chunk)
    if price_match is None:
        return None
    price = parse_price(price_match.group(1))
    if price is None:
        return None
    link_match = _LINK_RE.search(chunk)
    if link_match is None:
        return None
    path = urlparse(html_unescape(link_match.group(1))).path
    url = urljoin(BASE_URL, path)
    assert url.startswith(BASE_URL), "product URL must stay on Mash & Grape"
    in_stock = _OUT_OF_STOCK not in chunk
    assert isinstance(in_stock, bool)
    return name, price, url, in_stock


def parse_search_html(html, page_url, seen_at):
    """Parse one Mash & Grape search page. Returns (cards, tiles_seen).

    Each card is a (name, card price, url, in_stock) tuple; the size
    and the authoritative price are filled in by the driver from the
    product .js endpoint. tiles_seen counts real cards only — the
    `{{itemTitle}}` template chunk is excluded. Raises
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
    cards = []
    tiles_seen = 0
    for chunk in chunks[1:]:
        name_match = _NAME_RE.search(chunk)
        if name_match is not None:
            title = html_unescape(name_match.group(1)).strip()
            if title and "{{" not in title:
                tiles_seen += 1
        card = _parse_card(chunk)
        if card is not None:
            cards.append(card)
    assert len(cards) <= tiles_seen, "cards cannot exceed tiles"
    return cards, tiles_seen


async def search(query, wanted=None, max_pages=MAX_SEARCH_PAGES,
                 delay_s=PAGE_DELAY_S, seen_at="",
                 search_url_override=None):
    """Search Mash & Grape for one query (plain urllib, no browser).
    Returns (prices, health). Bounded: at most max_pages search
    pages (capped at MAX_SEARCH_PAGES = 1). Size is resolved via the
    product .js endpoint only for items whose canonical name is in
    `wanted`; when `wanted` is None, every in-stock card is resolved.
    A card whose .js payload has no size, or whose chosen variant is
    unavailable, is dropped. A bot-wall page ends the run as
    "blocked"."""
    assert isinstance(query, str) and query.strip(), "query required"
    assert wanted is None or hasattr(wanted, "__contains__")
    assert 1 <= max_pages <= MAX_PAGES, "max_pages out of bounds"
    assert delay_s >= 0, "delay must be non-negative"
    assert search_url_override is None or search_url_override.startswith(
        "http")
    stamp = seen_at or datetime.now(timezone.utc).isoformat()
    assert stamp, "seen_at timestamp required"
    base = search_url_override or search_url(query)
    assert base.startswith("http"), "search URL must be absolute"
    page_limit = min(max_pages, MAX_SEARCH_PAGES)
    assert 1 <= page_limit <= MAX_SEARCH_PAGES, "page limit out of bounds"
    all_prices = []
    pages_scanned = 0
    tiles_seen = 0
    blocked_wall = ""
    blocked_at = 0
    for page_no in range(1, page_limit + 1):
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
        for name, _card_price, product_url, in_stock in cards:
            if not in_stock:
                continue
            if wanted is not None and canonical_from_name(name) not in wanted:
                continue
            data = fetch_product_js(product_js_url(BASE_URL, product_url))
            if data is None:
                continue
            resolved = resolve_product_js(data)
            if resolved is None:
                continue
            price, size_ml, available = resolved
            assert available is True, "resolved product must be available"
            all_prices.append(OverseasPrice(
                retailer=RETAILER, country=COUNTRY, currency=CURRENCY,
                price=price, size_ml=size_ml, in_stock=True,
                url=product_url, name=name))
            assert len(all_prices) <= MAX_LISTINGS, "price cap hit"
        if seen == 0:
            break
        if page_no >= page_limit:
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
        detail = "pages loaded but no priced in-stock items parsed"
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
