"""Astor Wines parser (US overseas benchmark).

Source: https://www.astorwines.com — Spirits search is a plain
server-rendered GET:
  /SpiritsSearchResult.aspx?p=<page>&search=Advanced
      &searchtype=Contains&term=<query>&cat=2
Verified by live curl fetch 10 Nov 2026: 12 `item-teaser` cards on
page 1 inside #search-results. Per card: the name is the
`<a class="item-name">` text (strip trailing spaces/dots — the
display truncates with "..."); the selling price is the
`<span class="price-value price-bottle display-2">$149.99</span>`
(the empty `price-old` span and the `price-case` span are not the
bottle price); the size is the gtag variant `'(750)'` (millilitres);
stock is the absence of an `out-of-stock-container` div (an
out-of-stock card has an empty price container and the text "This
item is currently out of stock"). Product links are relative
`item/<id>?lt=...` — the query string is dropped.

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
    parse_size_ml,
)

RETAILER = "Astor Wines"
COUNTRY = "US"
CURRENCY = "USD"
BASE_URL = "https://www.astorwines.com"
SEARCH_PATH = ("/SpiritsSearchResult.aspx?search=Advanced"
               "&searchtype=Contains&cat=2")
MAX_SEARCH_PAGES = 5

_CARD_SPLIT = 'class="item-teaser"'
_NAME_RE = re.compile(
    r'<a[^>]*class="item-name"[^>]*href="([^"]+)"[^>]*>([^<]+)</a>', re.S)
_PRICE_RE = re.compile(
    r'price-value price-bottle display-2">\s*\$([\d,]+(?:\.\d{2})?)', re.S)
_VARIANT_RE = re.compile(r"variant: '\((\d+)\)'")
_OUT_OF_STOCK = "out-of-stock-container"


def search_url(query, page_no=1):
    """Astor Spirits search URL for one query and page."""
    assert isinstance(query, str) and query.strip(), "query required"
    assert page_no >= 1, "page number must be >= 1"
    url = (BASE_URL + SEARCH_PATH + "&term=" + quote_plus(query.strip())
           + "&p=%d" % page_no)
    assert url.startswith(BASE_URL), "search URL must stay on Astor"
    return url


def _clean_name(text):
    """Anchor text stripped of trailing spaces and dots."""
    assert isinstance(text, str), "name must be a string"
    cleaned = text.strip().rstrip(".").strip()
    assert isinstance(cleaned, str)
    return cleaned


def parse_search_html(html, page_url, seen_at):
    """Parse one Astor search page. Returns (prices, tiles_seen).

    Raises ValueError("blocked:<wall>") on a bot-wall challenge page.
    A card without a bottle price or a size is dropped, never
    guessed; an out-of-stock card is kept with in_stock False only
    when it carries a price (it never does — the price container is
    empty — so in practice out-of-stock cards are dropped).
    """
    assert isinstance(html, str) and html, "page HTML required"
    assert isinstance(page_url, str) and page_url.startswith("http")
    assert seen_at, "seen_at timestamp required"
    wall = detect_bot_wall(html)
    assert wall is None or isinstance(wall, str)
    if wall is not None:
        raise ValueError("blocked:" + wall)
    chunks = html.split(_CARD_SPLIT)
    prices = []
    for chunk in chunks[1:]:
        match = _NAME_RE.search(chunk)
        if match is None:
            continue
        href = html_unescape(match.group(1))
        name = _clean_name(html_unescape(match.group(2)))
        if not name:
            continue
        price_text = _PRICE_RE.search(chunk)
        if price_text is None:
            continue
        price = parse_price(price_text.group(1))
        if price is None:
            continue
        plain = html_unescape(chunk)
        size_match = _VARIANT_RE.search(plain)
        if size_match is None:
            continue
        size_ml = parse_size_ml(size_match.group(1) + "ml")
        if size_ml is None:
            continue
        in_stock = _OUT_OF_STOCK not in chunk
        url = urljoin(BASE_URL, urlparse(href).path)
        assert url.startswith(BASE_URL), "product URL must stay on Astor"
        prices.append(OverseasPrice(
            retailer=RETAILER, country=COUNTRY, currency=CURRENCY,
            price=price, size_ml=size_ml, in_stock=in_stock,
            url=url, name=name))
        assert len(prices) <= MAX_LISTINGS, "price cap hit"
    assert len(prices) <= len(chunks) - 1, "prices cannot exceed cards"
    return prices, len(chunks) - 1


async def search(query, max_pages=MAX_SEARCH_PAGES,
                 delay_s=PAGE_DELAY_S, seen_at="",
                 search_url_override=None):
    """Search Astor Wines for one query (plain urllib, no browser).
    Returns (prices, health). Bounded: at most max_pages search
    pages; stops early when a page yields no cards. A bot-wall page
    ends the run as "blocked"."""
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
    for page_no in range(1, max_pages + 1):
        url = base if page_no == 1 else search_url(query, page_no)
        if search_url_override is not None and page_no > 1:
            break
        html = fetch_text(url)
        if html is None:
            blocked_at = page_no
            break
        try:
            found, seen = parse_search_html(html, url, stamp)
        except ValueError as exc:
            if str(exc).startswith("blocked:"):
                blocked_wall = str(exc)[len("blocked:"):]
                blocked_at = page_no
                break
            raise
        tiles_seen += seen
        pages_scanned += 1
        all_prices.extend(found)
        assert len(all_prices) <= MAX_LISTINGS, "price cap hit"
        if seen == 0:
            break
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
