"""The Whisky Barrel parser (UK overseas benchmark).

Source: https://www.thewhiskybarrel.com — Shopify storefront. The
data source is the Shopify predictive-search JSON, not the HTML
search page:
  /search/suggest.json?q=<term>&resources[type]=product
      &resources[limit]=10
Verified by live curl fetch 11 Nov 2026: HTTP 200, up to 10
products in `resources.results.products[]`, each with `available`
(bool), `price` (string, e.g. "150.61"), `title`, and `url`
(relative, with a tracking query string — the query is stripped).
The HTML search page is NOT parsed: every card carries both a
"sale" and a "sold out" badge including available products, so
badges must never be used as a stock signal, and the HTML carries
no size. The size is on the product page as
`<span class="bottle-info__label">Volume:</span> <span>70cl</span>`,
so the driver fetches the product page for size only for canonical
matches (bounded by the suggest results). The Volume gap on the
product page is real newlines and spaces (verified on the fixture),
so the regex uses `[^<]*` between the tags.

Prices are GBP, VAT included. Only in-stock prices count downstream.
"""

import re
from datetime import datetime, timezone
from urllib.parse import quote_plus, urljoin, urlparse

from ..models import OverseasPrice, canonical_from_name
from ..report import ParserHealth
from .base import (
    MAX_LISTINGS,
    MAX_PAGES,
    PAGE_DELAY_S,
    fetch_json,
    fetch_text,
    parse_price,
    parse_size_ml,
)

RETAILER = "The Whisky Barrel"
COUNTRY = "UK"
CURRENCY = "GBP"
BASE_URL = "https://www.thewhiskybarrel.com"
MAX_SEARCH_PAGES = 3
MAX_SUGGEST_RESULTS = 10

_SUGGEST_URL = (BASE_URL + "/search/suggest.json?q=%s"
                "&resources[type]=product&resources[limit]=10")
_SIZE_RE = re.compile(
    r'bottle-info__label">Volume:</span>[^<]*<span>([^<]+)</span>')


def search_url(query):
    """Whisky Barrel suggest.json URL for one query.

    The suggest endpoint offers no pagination; one call returns at
    most MAX_SUGGEST_RESULTS products.
    """
    assert isinstance(query, str) and query.strip(), "query required"
    url = _SUGGEST_URL % quote_plus(query.strip())
    assert url.startswith(BASE_URL), "suggest URL must stay on TWB"
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

    The size is the "Volume:" bottle-info row (e.g. "70cl").
    """
    assert isinstance(html, str) and html, "page HTML required"
    size_text = _first(_SIZE_RE, html)
    if size_text is None:
        return None
    size_ml = parse_size_ml(size_text)
    assert size_ml is None or size_ml > 0
    return size_ml


def _suggest_products(data):
    """Product list from a suggest.json payload, or [].

    A malformed payload yields no products, never a crash.
    """
    assert isinstance(data, dict), "suggest JSON must be an object"
    resources = data.get("resources")
    if not isinstance(resources, dict):
        return []
    results = resources.get("results")
    if not isinstance(results, dict):
        return []
    products = results.get("products")
    if not isinstance(products, list):
        return []
    assert len(products) >= 0, "product list must be countable"
    return products


def parse_suggest_json(data, seen_at):
    """Parse one suggest.json payload. Returns (cards, items_seen).

    Each card is a (name, price, url, in_stock) tuple; size is filled
    in by the driver from the product page. Stock comes only from
    the `available` flag — never from the HTML badges. A product
    without a usable title, price, or URL is dropped; an out-of-
    stock product that carries a price is kept with in_stock False.
    """
    assert isinstance(data, dict), "suggest JSON must be an object"
    assert seen_at, "seen_at timestamp required"
    products = _suggest_products(data)
    cards = []
    for prod in products[:MAX_SUGGEST_RESULTS]:
        if not isinstance(prod, dict):
            continue
        title = prod.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        name = title.strip()
        assert name, "product name must be non-empty after strip"
        price = parse_price(str(prod.get("price", "")))
        if price is None:
            continue
        href = prod.get("url")
        if not isinstance(href, str) or not href:
            continue
        url = urljoin(BASE_URL, urlparse(href).path)
        assert url.startswith(BASE_URL), "product URL must stay on TWB"
        in_stock = prod.get("available") is True
        cards.append((name, price, url, in_stock))
    assert len(cards) <= len(products), "cards cannot exceed products"
    return cards, len(products)


async def search(query, wanted=None, max_pages=MAX_SEARCH_PAGES,
                 delay_s=PAGE_DELAY_S, seen_at="",
                 search_url_override=None):
    """Search The Whisky Barrel for one query (plain urllib, no
    browser). Returns (prices, health).

    Bounded: one suggest.json fetch (the endpoint does not paginate;
    max_pages is accepted for signature parity and capped). Size is
    resolved by a product-page fetch only for in-stock cards whose
    canonical name is in `wanted`; when `wanted` is None (unit
    tests) every parsed card is resolved. A card whose product page
    yields no size is dropped (an unverified size never becomes a
    benchmark). A failed fetch ends the run as "blocked".
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
    url = search_url_override or search_url(query)
    assert url.startswith("http"), "search URL must be absolute"
    data = fetch_json(url)
    pages_scanned = 0
    items_seen = 0
    all_cards = []
    if data is not None:
        all_cards, items_seen = parse_suggest_json(data, stamp)
        pages_scanned = 1
    assert len(all_cards) <= MAX_SUGGEST_RESULTS, "card cap hit"
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
    if pages_scanned == 0:
        status = "blocked"
        detail = "no suggest page loaded"
    elif len(all_prices) == 0:
        status = "zero-items"
        detail = "suggest endpoint loaded but no sized in-stock prices parsed"
    else:
        status = "ok"
        detail = "%d products seen, %d priced" % (items_seen,
                                                   len(all_prices))
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
