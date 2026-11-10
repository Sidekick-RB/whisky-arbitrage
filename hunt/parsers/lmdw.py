"""LMDW parser (WooCommerce Store API).

Source: https://www.whisky.sg (La Maison du Whisky Singapore).
GET /wp-json/wc/store/v1/products?per_page=100&page=N returns a list
of products: name (French style, e.g. "TORMORE 16 ans"), permalink,
prices.price in minor units ("18000" = S$180.00, currency_minor_unit
2, currency_code SGD), is_in_stock, and attributes — the bottle size
is the "Volume" attribute term ("70cl"). A product is whisky when one
of its categories contains "whisk" (the catalogue also sells rum, e.g.
Neisson, whose categories are "Rum" only). Non-SGD prices are skipped,
never converted by guesswork.
"""

import asyncio
from datetime import datetime, timezone

from ..models import Listing, canonical_from_name
from ..report import ParserHealth
from .base import (
    MAX_LISTINGS,
    PAGE_DELAY_S,
    fetch_json,
    parse_size_ml,
)

RETAILER = "LMDW"
BASE_URL = "https://www.whisky.sg"
API_URL = BASE_URL + "/wp-json/wc/store/v1/products?per_page=100"
PER_PAGE = 100
MAX_PAGES = 12


def _volume_ml(product):
    """Bottle size in ml from the "Volume" attribute, or None."""
    assert isinstance(product, dict)
    for attribute in product.get("attributes") or []:
        if not isinstance(attribute, dict):
            continue
        if attribute.get("name") != "Volume":
            continue
        terms = attribute.get("terms") or []
        if terms and isinstance(terms[0], dict):
            return parse_size_ml(str(terms[0].get("name") or ""))
    return None


def _is_whisky(product):
    """True when a category name contains "whisk" (case-insensitive)."""
    assert isinstance(product, dict)
    for category in product.get("categories") or []:
        if not isinstance(category, dict):
            continue
        name = str(category.get("name") or "").lower()
        if "whisk" in name:
            return True
    return False


def _price_sgd(product):
    """Shelf price in SGD from the minor-unit price, or None."""
    assert isinstance(product, dict)
    prices = product.get("prices") or {}
    if not isinstance(prices, dict):
        return None
    if prices.get("currency_code") != "SGD":
        return None
    raw = prices.get("price")
    if raw is None:
        return None
    try:
        minor = int(str(raw))
        minor_unit = int(prices.get("currency_minor_unit", 2))
    except (ValueError, TypeError):
        return None
    assert minor >= 0, "price must be non-negative"
    assert 0 <= minor_unit <= 3, "minor unit out of range"
    value = minor / (10 ** minor_unit)
    # A zero price (e.g. a tasting-event product priced "0") is not
    # a price: skip it, never crash. Same rule as parse_price.
    return value if value > 0 else None


def parse_products_json(data, seen_at):
    """Parse one Store API page (a list). Returns (listings, has_next,
    products_seen, skipped). A product is skipped when it is not
    whisky, has no permalink, or has no parseable SGD price. Size
    comes from the Volume attribute; a missing size is None
    (unverified), never assumed."""
    assert isinstance(data, list), "Store API returns a list"
    assert seen_at, "seen_at timestamp required"
    listings = []
    skipped = 0
    for product in data:
        if not isinstance(product, dict):
            skipped += 1
            continue
        name = product.get("name") or ""
        url = product.get("permalink") or ""
        price = _price_sgd(product)
        if not _is_whisky(product) or not name or not url or price is None:
            skipped += 1
            continue
        size_ml = _volume_ml(product)
        listings.append(Listing(
            retailer=RETAILER,
            name=name.strip(),
            canonical=canonical_from_name(name),
            size_ml=size_ml,
            size_verified=size_ml is not None,
            price_sgd=price,
            in_stock=bool(product.get("is_in_stock")),
            traveller_only=False,
            eligibility="Singapore delivery",
            edition="",
            batch="",
            url=url,
            seen_at=seen_at,
        ))
    has_next = len(data) >= PER_PAGE
    assert len(listings) + skipped == len(data)
    return listings, has_next, len(data), skipped


async def scrape(seen_at="", max_pages=MAX_PAGES, delay_s=PAGE_DELAY_S):
    """Scrape the LMDW Store API catalogue. Returns (listings, health)."""
    assert 1 <= max_pages <= 40, "max_pages out of bounds"
    assert delay_s >= 0, "delay must be non-negative"
    stamp = seen_at or datetime.now(timezone.utc).isoformat()
    assert stamp, "seen_at timestamp required"
    all_listings = []
    pages_scanned = 0
    products_seen = 0
    skipped = 0
    failed_at = ""
    capped = False
    page_no = 1
    has_next = False
    while pages_scanned < max_pages:
        url = API_URL + "&page=%d" % page_no
        data = fetch_json(url)
        if data is None:
            failed_at = url
            break
        if not isinstance(data, list):
            failed_at = url
            break
        listings, has_next, seen, skip = parse_products_json(data, stamp)
        all_listings.extend(listings)
        assert len(all_listings) <= MAX_LISTINGS, "listing cap hit"
        pages_scanned += 1
        products_seen += seen
        skipped += skip
        if not has_next:
            break
        page_no += 1
        await asyncio.sleep(delay_s)
    if not failed_at and pages_scanned >= max_pages and has_next:
        capped = True
    if pages_scanned == 0:
        status = "error"
        detail = "no API page returned data (first failure: %s)" % failed_at
    elif failed_at:
        status = "partial"
        detail = "API failed at %s after %d pages" % (failed_at, pages_scanned)
    elif capped:
        status = "partial"
        detail = "page cap %d hit with more pages remaining" % max_pages
    elif len(all_listings) == 0:
        status = "zero-items"
        detail = "API pages loaded but no whisky products parsed"
    else:
        status = "ok"
        detail = "%d products seen, %d skipped" % (products_seen, skipped)
    health = ParserHealth(
        retailer=RETAILER, pages_scanned=pages_scanned,
        items_found=len(all_listings), status=status, detail=detail)
    assert health.items_found == len(all_listings)
    return all_listings, health
