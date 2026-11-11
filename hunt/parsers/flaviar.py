"""Flaviar parser (US overseas benchmark).

Source: https://flaviar.com — search is a plain server-rendered GET:
  /search?q=<query>[&page=<page>]
Verified by live curl fetch 11 Nov 2026: 24
`card-wrapper product-card-wrapper` cards on page 1 of a
"glenfiddich 18" search (48 results, page 2 disjoint), prices
visible without signing in. Per card: the name is the
`data-title` attribute; the REGULAR price is the
`price_v3-regular price__regular` div (e.g. $145.99 for
Glenfiddich 18); the size is in the title ("(700mL)") or the
handle suffix ("glenfiddich-18-year-old-750" -> 750 ml);
stock is the visibility of the `card-visible-out-of-stock`
span — hidden (`d-none`) means in stock, visible (no `d-none`,
inline `style="display: flex;"`) means out of stock.

Traps, all verified on the fixture:
- NEVER use the card's `data-price` attribute: it is the MEMBER
  price (data-price="11499" = $114.99 for Glenfiddich 18, while
  the regular price is $145.99).
- Only 22 of the 24 cards carry a `price_v3-regular
  price__regular` span. The other two (Glenfiddich 15 Solera,
  Glenmorangie Infinita 18) show a discounted variant instead
  (`price_v3-regular--green` / `price_v3-discounted`) and are
  DROPPED, never priced from `data-price` or `data-regular`.
- A disabled add-to-cart button alone is NOT out of stock: the
  Grande Couronne 26 card has a disabled (member-only /
  allocation) button but a hidden sold-out span, so it is in
  stock. Only a VISIBLE sold-out span marks a card out of stock.
- An out-of-stock card that carries a price is KEPT with
  in_stock False (Glenfiddich 30, $1,251.99); only in-stock
  prices count downstream.

Marketplace caveat: Flaviar is a marketplace — the Glenfiddich 18
product has 212 retailer variants, and the card price is the
default retailer's price, which may be geo-dependent (Flaviar
routes fulfilment by shipping state). The card price is used as
the public list price as-is: variants are never averaged and the
member price is never used. No per-product `.js` fetch is made:
it is 117 KB for one product and carries no size info.

The age-gate overlay (`#age-verification-overlay`) exists in the
HTML but is hidden by default (`d-none`); it does not block curl
content. Prices are USD, tax excluded.
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
    PAGE_DELAY_S,
    detect_bot_wall,
    fetch_text,
    parse_price,
    parse_size_ml,
)

RETAILER = "Flaviar"
COUNTRY = "US"
CURRENCY = "USD"
BASE_URL = "https://flaviar.com"
MAX_SEARCH_PAGES = 3

_CARD_SPLIT = "card-wrapper product-card-wrapper"
_NAME_RE = re.compile(r'data-title="([^"]+)"')
_HANDLE_RE = re.compile(r'data-product-handle="([^"]+)"')
_PRICE_RE = re.compile(
    r'price_v3-regular price__regular[^>]*>\$([\d,]+\.\d{2})')
_TITLE_SIZE_RE = re.compile(r"\((\d+(?:\.\d+)?)\s?(mL|L|cl)\)")
_HANDLE_SIZE_RE = re.compile(r"-(\d+)$")
# Plausible bottle sizes for a handle suffix. A handle may also end
# in a vintage year ("-1998"); a year is not a size, so a suffix
# outside this set means "no size", never a 1998 ml bottle.
_HANDLE_SIZES = frozenset(
    {50, 100, 200, 350, 375, 500, 700, 750, 1000, 1500, 1750, 2000,
     3000})
_SOLD_OUT_SPAN_RE = re.compile(
    r'<span[^>]*card-visible-out-of-stock[^>]*>')


def search_url(query, page_no=1):
    """Flaviar search URL for one query and page."""
    assert isinstance(query, str) and query.strip(), "query required"
    assert page_no >= 1, "page number must be >= 1"
    url = BASE_URL + "/search?q=" + quote_plus(query.strip())
    if page_no > 1:
        url += "&page=%d" % page_no
    assert url.startswith(BASE_URL), "search URL must stay on Flaviar"
    return url


def _size_ml(name, handle):
    """Bottle size in ml from the title or the handle suffix.

    Title first ("(700mL)", "(1.75L)", "(1L)"), then the handle
    suffix ("-750", "-1750") — but only when the suffix is a
    plausible bottle size (see _HANDLE_SIZES): a handle ending in
    a vintage year ("-1998") carries no size. Returns None when
    neither carries a size; the size is never guessed.
    """
    assert isinstance(name, str) and name, "name required"
    assert isinstance(handle, str) and handle, "handle required"
    title_match = _TITLE_SIZE_RE.search(name)
    if title_match is not None:
        size = parse_size_ml(
            title_match.group(1) + title_match.group(2))
        assert size is None or size > 0, "size must be positive"
        return size
    suffix_match = _HANDLE_SIZE_RE.search(handle)
    if suffix_match is None:
        return None
    size = parse_size_ml(suffix_match.group(1) + "ml")
    assert size is None or size > 0, "size must be positive"
    if size not in _HANDLE_SIZES:
        return None
    return size


def _is_in_stock(chunk):
    """True unless the sold-out span is VISIBLE in the card chunk.

    The span is present in every card; `d-none` in its tag means
    hidden (in stock), no `d-none` means visible (out of stock).
    A disabled button is not consulted: member-only cards have one
    while still in stock.
    """
    assert isinstance(chunk, str) and chunk, "card chunk required"
    match = _SOLD_OUT_SPAN_RE.search(chunk)
    assert match is None or match.group(0), "span match must be text"
    if match is None:
        return True
    visible = "d-none" not in match.group(0)
    assert isinstance(visible, bool), "visibility must be a bool"
    return not visible


def _keep(name, wanted):
    """True when the name's canonical key is in the wanted set.

    When wanted is None every name is kept.
    """
    assert isinstance(name, str) and name, "name required"
    assert wanted is None or isinstance(wanted, (set, frozenset))
    if wanted is None:
        return True
    key = canonical_from_name(name)
    assert key, "canonical key must be non-empty"
    return key in wanted


def parse_search_html(html, page_url, seen_at):
    """Parse one Flaviar search page. Returns (prices, tiles_seen).

    Raises ValueError("blocked:<wall>") on a bot-wall challenge page.
    A card without a regular-price span or without a size in its
    title/handle is dropped, never guessed; an out-of-stock card is
    kept with in_stock False only when it carries a price.
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
        name_match = _NAME_RE.search(chunk)
        if name_match is None:
            continue
        name = html_unescape(name_match.group(1)).strip()
        if not name:
            continue
        handle_match = _HANDLE_RE.search(chunk)
        if handle_match is None:
            continue
        handle = handle_match.group(1).strip()
        if not handle:
            continue
        price_match = _PRICE_RE.search(chunk)
        if price_match is None:
            continue
        price = parse_price(price_match.group(1))
        if price is None:
            continue
        size_ml = _size_ml(name, handle)
        if size_ml is None:
            continue
        in_stock = _is_in_stock(chunk)
        url = urljoin(BASE_URL, "/products/" + handle)
        assert url.startswith(BASE_URL), "product URL must stay on Flaviar"
        prices.append(OverseasPrice(
            retailer=RETAILER, country=COUNTRY, currency=CURRENCY,
            price=price, size_ml=size_ml, in_stock=in_stock,
            url=url, name=name))
        assert len(prices) <= MAX_LISTINGS, "price cap hit"
    assert len(prices) <= len(chunks) - 1, "prices cannot exceed cards"
    return prices, len(chunks) - 1


async def search(query, wanted=None, max_pages=MAX_SEARCH_PAGES,
                 delay_s=PAGE_DELAY_S, seen_at="",
                 search_url_override=None):
    """Search Flaviar for one query (plain urllib, no browser).
    Returns (prices, health). Bounded: at most max_pages search
    pages; stops early when a page yields no cards. When `wanted`
    is given, only prices whose canonical name is in the set are
    kept. A bot-wall page ends the run as "blocked"."""
    assert isinstance(query, str) and query.strip(), "query required"
    assert wanted is None or isinstance(wanted, (set, frozenset))
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
        for item in found:
            assert item.name, "parsed price must carry a name"
            if _keep(item.name, wanted):
                all_prices.append(item)
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
