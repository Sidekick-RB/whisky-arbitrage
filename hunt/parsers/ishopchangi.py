"""iShopChangi parser (urllib /bin JSON API).

Source: https://www.ishopchangi.com — Adobe AEM + React SPA. The
search page itself is a shell: all product data comes from
GET /bin/cagcommerce/webservices/v2/cag/products/search, which
answers plain GETs with ONE required header: Referer: any
https://www.ishopchangi.com/... page URL (verified live 10 Nov
2026 — bare GETs 301 to /en/home; with a Referer, HTTP 200 JSON).
No cookie or session token is needed, and no bot-detection code is
in the React bundle.

The mode is a facet filter in the query value, not separate URL
params:
  <term>:popularity:channel:ISC:isCorporateSalesProduct:false
      :cagCollectionPoint:<CODE>
NT code HOMEDELIVERYNONTRAVELLER (690 whisky results), TR code
DEPARTURE (422 results) — the two modes are genuinely different
catalogues. Pagination is currentPage (0-based); the page param is
ignored. Each product carries code (mp...), name (size in the
name), url, inStock, price.value (sale), originPrice.value (was),
from (a variant "From" price — never size-verified), exclusive,
manufacturer.

This replaces the Playwright DOM parser (slices 1-4): the browser
driver returned zero tiles on Fargate run after run while the same
URLs rendered fine in a real browser — the React app never renders
tiles in headless Chromium there, for an unknown browser-side
reason. The JSON route sidesteps it entirely; urllib egress from
Fargate is already proven by the other parsers. Ground truth
reproduced exactly from the JSON: Brackla 18 TR S$165.00 / was
S$218.00 (mp00519659), Brackla 12 TR S$100.00, Brackla 21 TR
S$330.00, Royal Salute 32 S$613.50.
"""

import asyncio
from datetime import datetime, timezone
from urllib.parse import quote, urljoin

from ..models import Listing, canonical_from_name
from ..report import ParserHealth
from .base import (
    MAX_LISTINGS,
    MAX_PAGES,
    PAGE_DELAY_S,
    fetch_json,
    parse_price,
    parse_size_ml,
)

RETAILER = "iShopChangi"
BASE_URL = "https://www.ishopchangi.com"
API_PATH = "/bin/cagcommerce/webservices/v2/cag/products/search"
NT_POINTS = "HOMEDELIVERYNONTRAVELLER"
TR_POINTS = "DEPARTURE"
PAGE_SIZE = 48
MAX_MODE_PAGES = 40


def api_url(query, mode, page_no=0):
    """Search JSON URL for one query, mode and 0-based page."""
    assert isinstance(query, str) and query.strip(), "query required"
    assert mode in ("nt", "tr"), "mode must be nt or tr"
    assert page_no >= 0, "page number must be non-negative"
    points = NT_POINTS if mode == "nt" else TR_POINTS
    facet = (quote(query.strip()) + ":popularity:channel:ISC"
             ":isCorporateSalesProduct:false:cagCollectionPoint:"
             + points)
    url = (BASE_URL + API_PATH + "?query=" + facet
           + "&currentPage=%d" % page_no)
    assert url.startswith(BASE_URL), "search URL must stay on site"
    return url


def _referer(mode):
    """Referer header value for one mode: the search page URL.

    The /bin endpoint answers only when the request carries a
    Referer on the site; any site page URL works."""
    assert mode in ("nt", "tr"), "mode must be nt or tr"
    url = BASE_URL + "/en/search?query=whisky&cmode=" + mode
    assert url.startswith(BASE_URL), "referer must stay on site"
    return url


def _price_sgd(product):
    """Sale price in SGD from price.value, or None.

    Only SGD prices count; a zero or missing price is not a price.
    """
    assert isinstance(product, dict)
    price = product.get("price") or {}
    if not isinstance(price, dict):
        return None
    if price.get("currencyIso") != "SGD":
        return None
    value = parse_price(str(price.get("value") or ""))
    assert value is None or value > 0
    return value


def parse_search_json(data, mode, seen_at):
    """Parse one search JSON page. Returns (listings, has_next,
    products_seen, skipped).

    A product is skipped when it has no code, name, URL or usable
    SGD price, or when its code repeats within the page. Size comes
    from the name; a "From" variant price is never size-verified.
    Out-of-stock products are kept (with in_stock False) when they
    carry a price."""
    assert isinstance(data, dict), "search JSON must be an object"
    assert mode in ("nt", "tr"), "mode must be nt or tr"
    assert seen_at, "seen_at timestamp required"
    products = data.get("products") or []
    assert isinstance(products, list), "products must be a list"
    pagination = data.get("pagination") or {}
    assert isinstance(pagination, dict), "pagination must be an object"
    listings = []
    skipped = 0
    seen_codes = set()
    for product in products:
        if not isinstance(product, dict):
            skipped += 1
            continue
        code = str(product.get("code") or "")
        name = str(product.get("name") or "").strip()
        href = str(product.get("url") or "")
        if not code or not name or not href:
            skipped += 1
            continue
        if code in seen_codes:
            skipped += 1
            continue
        seen_codes.add(code)
        price = _price_sgd(product)
        if price is None:
            skipped += 1
            continue
        size_ml = parse_size_ml(name)
        is_from = bool(product.get("from"))
        size_verified = size_ml is not None and not is_from
        url = urljoin(BASE_URL, href.split("#")[0])
        if not url.startswith(BASE_URL):
            skipped += 1
            continue
        listings.append(Listing(
            retailer=RETAILER,
            name=name,
            canonical=canonical_from_name(name),
            size_ml=size_ml,
            size_verified=size_verified,
            price_sgd=price,
            in_stock=bool(product.get("inStock")),
            traveller_only=(mode == "tr"),
            eligibility=("Pre-order to flight" if mode == "tr"
                         else "Singapore delivery"),
            edition="",
            batch="",
            url=url,
            seen_at=seen_at,
        ))
    current = int(pagination.get("currentPage") or 0)
    total = int(pagination.get("totalPages") or 0)
    has_next = total > 0 and current + 1 < total
    assert len(listings) + skipped == len(products)
    return listings, has_next, len(products), skipped


async def scrape_mode(mode, seen_at="", max_pages=MAX_MODE_PAGES,
                      delay_s=PAGE_DELAY_S, query="whisky"):
    """Scrape one mode's whisky search via the /bin JSON API.
    Returns (listings, health)."""
    assert mode in ("nt", "tr"), "mode must be nt or tr"
    assert isinstance(query, str) and query.strip(), "query required"
    assert 1 <= max_pages <= MAX_PAGES, "max_pages out of bounds"
    assert delay_s >= 0, "delay must be non-negative"
    stamp = seen_at or datetime.now(timezone.utc).isoformat()
    assert stamp, "seen_at timestamp required"
    all_listings = []
    pages_scanned = 0
    products_seen = 0
    skipped = 0
    failed_at = -1
    failed_kind = ""
    for page_no in range(max_pages):
        url = api_url(query, mode, page_no)
        data = fetch_json(url, headers={"Referer": _referer(mode)})
        if data is None:
            failed_at = page_no
            failed_kind = "load"
            break
        if not isinstance(data, dict):
            failed_at = page_no
            failed_kind = "shape"
            break
        listings, has_next, seen, skip = parse_search_json(
            data, mode, stamp)
        all_listings.extend(listings)
        assert len(all_listings) <= MAX_LISTINGS, "listing cap hit"
        pages_scanned += 1
        products_seen += seen
        skipped += skip
        if not has_next:
            break
        await asyncio.sleep(delay_s)
    if pages_scanned == 0 and failed_kind == "load":
        status = "blocked"
        detail = "no search JSON loaded (blocked at page %d)" % (
            failed_at + 1)
    elif pages_scanned == 0:
        status = "error"
        detail = "search JSON unreadable at page %d" % (failed_at + 1)
    elif failed_kind:
        status = "partial"
        detail = "%s failed at page %d after %d pages" % (
            failed_kind, failed_at + 1, pages_scanned)
    elif len(all_listings) == 0:
        status = "zero-items"
        detail = "search JSON loaded but no products parsed (mode %s)" % mode
    else:
        status = "ok"
        detail = "%s: %d products seen, %d skipped" % (
            mode, products_seen, skipped)
    health = ParserHealth(
        retailer=RETAILER, pages_scanned=pages_scanned,
        items_found=len(all_listings), status=status, detail=detail)
    assert health.items_found == len(all_listings)
    return all_listings, health


async def scrape(seen_at="", max_pages=MAX_MODE_PAGES,
                 delay_s=PAGE_DELAY_S, query="whisky"):
    """Scrape both modes, NT first, via the /bin JSON API.
    Returns (listings, health): one health entry with the worst
    status of the two passes and summed pages/items."""
    assert isinstance(query, str) and query.strip(), "query required"
    assert 1 <= max_pages <= MAX_PAGES, "max_pages out of bounds"
    assert delay_s >= 0, "delay must be non-negative"
    stamp = seen_at or datetime.now(timezone.utc).isoformat()
    assert stamp, "seen_at timestamp required"
    listings = []
    healths = []
    for mode in ("nt", "tr"):
        found, entry = await scrape_mode(
            mode, seen_at=stamp, max_pages=max_pages,
            delay_s=delay_s, query=query)
        listings.extend(found)
        healths.append(entry)
    assert len(healths) == 2, "both modes must report health"
    order = {"blocked": 0, "error": 1, "partial": 2, "zero-items": 3,
             "ok": 4}
    worst = "ok"
    pages = 0
    items = 0
    details = []
    for entry in healths:
        assert entry.retailer == RETAILER, "health must name iShopChangi"
        if order[entry.status] < order[worst]:
            worst = entry.status
        pages += entry.pages_scanned
        items += entry.items_found
        details.append(entry.detail)
    health = ParserHealth(
        retailer=RETAILER, pages_scanned=pages,
        items_found=len(listings), status=worst,
        detail="; ".join(details))
    assert health.items_found == len(listings)
    return listings, health
