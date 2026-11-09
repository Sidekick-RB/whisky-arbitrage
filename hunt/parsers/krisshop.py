"""KrisShop parser (Adobe Commerce GraphQL).

Source: https://kscommerce.krisshop.com/graphql — the endpoint is
declared in the KrisShop homepage markup (data-graphql-url). The
site itself is a JS shell with zero product data in server HTML, so
the GraphQL API is the only headless source. Verified live 9 Nov
2026: the products search returns the traveller (flight) prices —
Woodford Reserve Baccarat final S$1,590.40 (regular S$1,988),
Wildmoor 40 S$968, Chivas Regal Ultis 1L S$270.90 — all matching the
watchlist. The home-delivery variant (Wildmoor 40 S$1,210) is NOT
returned by this endpoint's default view; it stays browser-verified.

Fulfilment comes from targetsegment first (verified live 9 Nov 2026):
"12516" is the traveller/duty-free segment, "12515" is the (DP)
home-delivery segment, and travelexclusive 1 marks traveller-only
items. When targetsegment is absent, fall back to the SKU suffix:
"CF-model" SKUs are pre-order to flight (traveller_only); "(DP)"
names / "DP" SKUs are Singapore-delivery pre-orders. The SKU rule
alone misclassifies plain-SKU traveller items (e.g. Woodford Reserve
Distillers Select 1L, K366655, S$52 at segment 12516), so the
segment always wins when present. The PDP URL pattern is
/en/product/<sku>/<url_key> (PDP markers appear in the shell
response for that shape; unverified against a rendered page).

Size comes from the name only ("700ML", "1L", "0.7L"); names without
a size (e.g. "WILDMOOR 40 BLACK MOUNTAIN", "JURA SINGLE MALT
SCOTCH WHISKY 18 YEARS OLD") yield size_ml None (unverified), never
an assumed size. total_count can exceed len(items) (Magento counts
matches, items returns only visible ones); pagination uses
page_info.total_pages.
"""

import asyncio
import json
import urllib.request
from datetime import datetime, timezone

from ..models import Listing, canonical_from_name
from ..report import ParserHealth
from .base import (
    MAX_LISTINGS,
    PAGE_DELAY_S,
    USER_AGENT,
    parse_size_ml,
)
from .shopify import is_whisky_title

RETAILER = "KrisShop"
BASE_URL = "https://www.krisshop.com"
ENDPOINT = "https://kscommerce.krisshop.com/graphql"
PAGE_SIZE = 100
MAX_PAGES = 10

_QUERY = (
    "query($search: String, $pageSize: Int, $currentPage: Int) { "
    "products(search: $search, pageSize: $pageSize, "
    "currentPage: $currentPage) { total_count "
    "items { sku name url_key stock_status targetsegment "
    "travelexclusive "
    "price_range { minimum_price { "
    "regular_price { value currency } "
    "final_price { value currency } } } } "
    "page_info { total_pages current_page } } }"
)
_NON_BOTTLE = (
    "decanter", "glass", "stones", "gift set", "masterclass",
    "bundle", "tasting", "voucher", "candle", "coaster", "flask",
    "ice bucket",
)


def _is_bottle(name):
    """True when the title looks like a single whisky bottle, not
    barware, a set, or an event."""
    assert isinstance(name, str) and name.strip()
    text = name.lower()
    if any(word in text for word in _NON_BOTTLE):
        return False
    return is_whisky_title(name, "Whisky")


def _post_graphql(search, page_size, current_page):
    """POST one products search. Returns the decoded JSON dict, or
    None on any failure (HTTP error, non-JSON body, timeout)."""
    assert isinstance(search, str) and search.strip()
    assert 1 <= page_size <= 100, "page size out of bounds"
    assert current_page >= 1, "page number must be >= 1"
    payload = json.dumps({
        "query": _QUERY,
        "variables": {
            "search": search,
            "pageSize": page_size,
            "currentPage": current_page,
        },
    }).encode("utf-8")
    request = urllib.request.Request(
        ENDPOINT, data=payload,
        headers={"User-Agent": USER_AGENT,
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            assert response.status == 200, "GraphQL must be HTTP 200"
            body = response.read()
    except Exception:
        return None
    assert body, "GraphQL body must be non-empty"
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def parse_response(data, seen_at):
    """Parse one GraphQL products response. Returns (listings,
    total_pages, items_seen, skipped).

    Price is final_price (the price to pay); the currency must be
    SGD. A product is skipped when it is not a bottle, has no SKU /
    url_key / final price, or the currency is not SGD.
    """
    assert isinstance(data, dict), "GraphQL data must be a dict"
    assert seen_at, "seen_at timestamp required"
    products = data.get("data", {}).get("products", {})
    assert isinstance(products, dict), "products block required"
    items = products.get("items") or []
    assert isinstance(items, list), "items must be a list"
    page_info = products.get("page_info") or {}
    total_pages = page_info.get("total_pages") or 1
    assert total_pages >= 1, "total pages must be >= 1"
    listings = []
    skipped = 0
    for item in items:
        if not isinstance(item, dict):
            skipped += 1
            continue
        name = (item.get("name") or "").strip()
        sku = (item.get("sku") or "").strip()
        url_key = (item.get("url_key") or "").strip()
        price_block = (item.get("price_range") or {}).get(
            "minimum_price", {}) or {}
        final = price_block.get("final_price") or {}
        if (not _is_bottle(name) or not sku or not url_key
                or not isinstance(final, dict)):
            skipped += 1
            continue
        if final.get("currency") != "SGD":
            skipped += 1
            continue
        try:
            price = float(final.get("value"))
        except (ValueError, TypeError):
            skipped += 1
            continue
        if price <= 0:
            skipped += 1
            continue
        is_dp = name.startswith("(DP)") or sku.endswith("DP")
        clean_name = name[4:].strip() if name.startswith("(DP)") else name
        assert clean_name, "name must be non-empty after DP strip"
        segment = item.get("targetsegment")
        segment = str(segment) if segment is not None else ""
        assert isinstance(segment, str), "segment must be a string"
        if segment == "12516":
            traveller_only = True
        elif segment == "12515":
            traveller_only = False
        else:
            traveller_only = sku.endswith("CF-model")
        if is_dp:
            eligibility = "Singapore delivery (pre-order)"
        elif traveller_only:
            eligibility = "Pre-order to flight"
        else:
            eligibility = "Singapore delivery"
        size_ml = parse_size_ml(clean_name)
        listings.append(Listing(
            retailer=RETAILER,
            name=clean_name,
            canonical=canonical_from_name(clean_name),
            size_ml=size_ml,
            size_verified=size_ml is not None,
            price_sgd=price,
            in_stock=(item.get("stock_status") == "IN_STOCK"),
            traveller_only=traveller_only,
            eligibility=eligibility,
            edition="",
            batch="",
            url=BASE_URL + "/en/product/" + sku + "/" + url_key,
            seen_at=seen_at,
        ))
    assert len(listings) + skipped == len(items)
    return listings, total_pages, len(items), skipped


async def scrape_whisky(seen_at="", max_pages=MAX_PAGES,
                        delay_s=PAGE_DELAY_S, page_size=PAGE_SIZE):
    """Scrape the full KrisShop whisky search ("whisky", paginated).
    Returns (listings, health). Bounded: at most max_pages pages, at
    most MAX_LISTINGS listings. A failed page ends the run as
    "partial"/"error", never as "ok" with a silent gap."""
    assert 1 <= max_pages <= 40, "max_pages out of bounds"
    assert 1 <= page_size <= 100, "page size out of bounds"
    assert delay_s >= 0, "delay must be non-negative"
    stamp = seen_at or datetime.now(timezone.utc).isoformat()
    assert stamp, "seen_at timestamp required"
    all_listings = []
    pages_scanned = 0
    items_seen = 0
    skipped = 0
    failed_at = 0
    total_pages = 1
    for page_no in range(1, max_pages + 1):
        data = _post_graphql("whisky", page_size, page_no)
        if data is None or "errors" in data:
            failed_at = page_no
            break
        listings, total_pages, seen, skip = parse_response(data, stamp)
        all_listings.extend(listings)
        assert len(all_listings) <= MAX_LISTINGS, "listing cap hit"
        pages_scanned += 1
        items_seen += seen
        skipped += skip
        if page_no >= total_pages:
            break
        await asyncio.sleep(delay_s)
    if pages_scanned == 0:
        status = "error"
        detail = "no GraphQL page returned data (failed at page %d)" % failed_at
    elif failed_at:
        status = "partial"
        detail = "GraphQL failed at page %d after %d pages" % (
            failed_at, pages_scanned)
    elif len(all_listings) == 0:
        status = "zero-items"
        detail = "pages loaded but no whisky bottles parsed"
    else:
        status = "ok"
        detail = "%d items seen, %d skipped" % (items_seen, skipped)
    health = ParserHealth(
        retailer=RETAILER, pages_scanned=pages_scanned,
        items_found=len(all_listings), status=status, detail=detail)
    assert health.items_found == len(all_listings)
    return all_listings, health


async def search(query, seen_at="", max_results=10, delay_s=PAGE_DELAY_S):
    """Search KrisShop for one query (single GraphQL page). Returns
    (listings, health). Used by run.py for targeted lookups; the
    weekly scan uses scrape_whisky instead."""
    assert isinstance(query, str) and query.strip(), "query required"
    assert 1 <= max_results <= 100, "max_results out of bounds"
    assert delay_s >= 0, "delay must be non-negative"
    stamp = seen_at or datetime.now(timezone.utc).isoformat()
    assert stamp, "seen_at timestamp required"
    data = _post_graphql(query, max_results, 1)
    if data is None or "errors" in data:
        health = ParserHealth(
            retailer=RETAILER, pages_scanned=0, items_found=0,
            status="error", detail="GraphQL search failed for %r" % query)
        return [], health
    listings, _, seen, skipped = parse_response(data, stamp)
    if len(listings) == 0:
        status = "zero-items"
        detail = "no whisky bottles for %r (%d seen)" % (query, seen)
    else:
        status = "ok"
        detail = "%d items seen, %d skipped" % (seen, skipped)
    health = ParserHealth(
        retailer=RETAILER, pages_scanned=1,
        items_found=len(listings), status=status, detail=detail)
    assert health.items_found == len(listings)
    return listings, health
