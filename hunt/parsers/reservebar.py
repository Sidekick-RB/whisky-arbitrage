"""ReserveBar parser (US overseas benchmark).

Source: https://reservebar.com. The search page is a JS app whose
raw HTML carries no prices, so the parser does not use it. Instead:
1. Load https://reservebar.com/sitemap.xml once per run (10k+ URLs)
   and match product slugs against the query.
2. Fetch each matched product page (server-rendered) and read the
   JSON-LD Product block (offers.price, priceCurrency USD,
   availability) plus the visible size ("750 ML" in a Typography
   span — the FIRST uppercase "NNN ML" in the page; later matches
   are the same size in embedded JSON, verified 9 Nov 2026 on the
   Oban 14 page).

Slug matching: every significant query word (digits, or longer than
2 letters, excluding "year"/"old"/"the") must appear as a substring
of the slug, so "daniel" matches "jack-daniels-10-year-old-...".
The sitemap is loaded once and passed in (run.py loads it once per
run); a search call without sitemap_urls loads it itself.

Prices are USD, tax excluded. Only in-stock prices count downstream.
"""

import asyncio
import json
import re
from datetime import datetime, timezone
from html import unescape as html_unescape
from urllib.parse import urlparse

from ..models import OverseasPrice, canonical_from_name
from ..report import ParserHealth
from .base import (
    MAX_LISTINGS,
    PAGE_DELAY_S,
    fetch_text,
    parse_size_ml,
)

RETAILER = "ReserveBar"
COUNTRY = "US"
CURRENCY = "USD"
BASE_URL = "https://www.reservebar.com"
SITEMAP_URL = BASE_URL + "/sitemap.xml"

_LOC_RE = re.compile(
    r"<loc>(https://www\.reservebar\.com/products/[^<]+)</loc>")
_JSONLD_RE = re.compile(
    r'<script type="application/ld\+json">(.*?)</script>', re.S)
_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s+ML\b")
_SKIPPED_WORDS = ("year", "old", "the")
MAX_RESULTS = 5


def load_sitemap(sitemap_url=SITEMAP_URL):
    """Product-page URLs from the sitemap, or None on any failure."""
    assert isinstance(sitemap_url, str) and sitemap_url.startswith("http")
    text = fetch_text(sitemap_url)
    if text is None:
        return None
    urls = _LOC_RE.findall(text)
    assert len(urls) <= 50000, "sitemap implausibly large"
    return urls


def slug_matches(query, product_url):
    """True when every significant query word appears in the slug.

    The slug is the path segment after /products/ (the last segment
    is the GROUPING id, not the product name).
    """
    assert isinstance(query, str) and query.strip(), "query required"
    assert isinstance(product_url, str) and product_url.startswith("http")
    parts = [p for p in urlparse(product_url).path.split("/") if p]
    assert len(parts) >= 2 and parts[0] == "products", (
        "ReserveBar product URL must be /products/<slug>/GROUPING-...")
    slug = parts[1].lower()
    words = re.findall(r"[a-z0-9]+", query.lower())
    significant = [w for w in words
                   if (w.isdigit() or len(w) > 2)
                   and w not in _SKIPPED_WORDS]
    assert significant, "query must carry a significant word"
    for word in significant:
        if word not in slug:
            return False
    return True


def match_sitemap(query, product_urls, max_results=MAX_RESULTS):
    """First max_results sitemap URLs whose slug matches the query."""
    assert isinstance(query, str) and query.strip(), "query required"
    assert isinstance(product_urls, list), "sitemap URLs must be a list"
    assert 1 <= max_results <= 20, "max_results out of bounds"
    matched = []
    for url in product_urls:
        if slug_matches(query, url):
            matched.append(url)
            if len(matched) >= max_results:
                break
    assert len(matched) <= max_results
    return matched


def parse_product_html(html, page_url, seen_at):
    """One ReserveBar product page -> OverseasPrice, or None.

    Needs the JSON-LD block (name, price, currency, availability)
    and a visible size; a page missing any of them is dropped, never
    guessed.
    """
    assert isinstance(html, str) and html, "page HTML required"
    assert isinstance(page_url, str) and page_url.startswith("http")
    assert seen_at, "seen_at timestamp required"
    match = _JSONLD_RE.search(html)
    if match is None:
        return None
    try:
        data = json.loads(html_unescape(match.group(1)))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    offers = data.get("offers")
    if not isinstance(offers, dict):
        return None
    name = str(data.get("name") or "").strip()
    if not name:
        return None
    if offers.get("priceCurrency") != CURRENCY:
        return None
    try:
        price = float(str(offers.get("price")))
    except (ValueError, TypeError):
        return None
    if price <= 0:
        return None
    availability = str(offers.get("availability") or "")
    in_stock = availability.endswith("/InStock")
    size_match = _SIZE_RE.search(html)
    if size_match is None:
        return None
    size_ml = parse_size_ml(size_match.group(0))
    if size_ml is None:
        return None
    assert size_ml > 0, "parsed size must be positive"
    return OverseasPrice(
        retailer=RETAILER, country=COUNTRY, currency=CURRENCY,
        price=price, size_ml=size_ml, in_stock=in_stock, url=page_url,
        name=name)


async def search(query, sitemap_urls=None, max_results=MAX_RESULTS,
                 delay_s=PAGE_DELAY_S, seen_at="",
                 sitemap_url=SITEMAP_URL):
    """Search ReserveBar for one query via the sitemap. Returns
    (prices, health). Bounded: at most max_results product pages.
    A sitemap that never loads is "error"; no slug match is
    "zero-items". No price is ever invented."""
    assert isinstance(query, str) and query.strip(), "query required"
    assert 1 <= max_results <= 20, "max_results out of bounds"
    assert delay_s >= 0, "delay must be non-negative"
    stamp = seen_at or datetime.now(timezone.utc).isoformat()
    assert stamp, "seen_at timestamp required"
    urls = sitemap_urls
    if urls is None:
        urls = load_sitemap(sitemap_url)
    if urls is None:
        health = ParserHealth(
            retailer=RETAILER, pages_scanned=0, items_found=0,
            status="error", detail="sitemap failed to load: " + sitemap_url)
        return [], health
    matched = match_sitemap(query, urls, max_results)
    all_prices = []
    pages_scanned = 0
    for product_url in matched:
        html = fetch_text(product_url)
        pages_scanned += 1
        if html is None:
            continue
        price = parse_product_html(html, product_url, stamp)
        if price is not None:
            all_prices.append(price)
        assert len(all_prices) <= MAX_LISTINGS, "price cap hit"
        await asyncio.sleep(delay_s)
    if len(all_prices) == 0:
        status = "zero-items"
        detail = "no in-stock priced match for %r (%d slugs)" % (
            query, len(matched))
    else:
        status = "ok"
        detail = "%d slugs matched, %d priced" % (
            len(matched), len(all_prices))
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
