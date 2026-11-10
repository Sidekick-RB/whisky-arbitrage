"""iShopChangi parser (React SPA, search HTML).

Source: https://www.ishopchangi.com/en/search?query=whisky — no flight
and no sign-in needed to browse either mode (verified live 9 Nov
2026; see ~/workspace/bottles/ishopchangi-capture-2026-11-09.md).
The /bin search API only answers the site's own same-origin XHR, so
this parser reads the rendered DOM and never calls it from outside
the page.

Two passes, one per mode, each in a FRESH browser context: the
capture shows stale client-side prices leak across modes in one
session (a Non-Traveller load reached from the Traveller product page
briefly showed the Traveller price). Non-Traveller (cmode=nt) is the
home-delivery range; Traveller (cmode=tr) is the duty-free range.
The two modes return different result sets and different prices for
the same bottle, so each mode yields its own listings and the state
key carries the mode (see listing_key).

Price rule: the sale price is the price that is NOT struck through;
the struck-through "was" price is never the price. A "From S$..."
price is the cheapest variant, so it never supports a verified size.
Stock rule: a tile is out of stock only when it says so.

Fixture note: tests use SYNTHETIC HTML built from the verbatim
rendered records in the capture file. The live markup is UNVALIDATED
(the real class names are unknown; the extractor works structurally
on product links and price text). The first Fargate run validates it
live; a render failure shows up as an "error" health status, never
as a silent zero.
"""

import asyncio
import re
from datetime import datetime, timezone
from urllib.parse import quote_plus, urljoin

from ..models import Listing, canonical_from_name
from ..report import ParserHealth
from .base import (
    MAX_LISTINGS,
    MAX_PAGES,
    PAGE_DELAY_S,
    detect_bot_wall,
    goto_with_retries,
    new_stealth_context,
    parse_price_sgd,
    parse_size_ml,
)

RETAILER = "iShopChangi"
BASE_URL = "https://www.ishopchangi.com"
NT_POINTS = ("HOMEDELIVERYNONTRAVELLER%2FODD,"
             "HOMEDELIVERYNONTRAVELLER%2FREGULAR,LANDSIDE")
TR_POINTS = "HOMEDELIVERYTRAVELLER,ARRIVAL,DEPARTURE"
PAGE_SIZE = 48
MAX_MODE_PAGES = 40

_SEEN_RE = re.compile(r"You have seen (\d+) of (\d+) products")

# Structural DOM extraction: product links carry /en/product/<slug>-mp<code>;
# the tile is the nearest ancestor holding a price; the sale price is the
# first S$ amount not struck through (<s>/<del> or line-through style).
# The name is the longest line of the link text (the brand is a shorter
# line above it). No class names are assumed: the live markup is unknown.
_EXTRACT_JS = r"""() => {
  const records = [];
  const links = Array.from(document.querySelectorAll('a[href*="/en/product/"]'));
  for (const a of links) {
    const href = a.getAttribute('href') || '';
    const code = href.match(/-mp(\d+)/);
    if (!code) continue;
    const lines = (a.innerText || '').split('\n').map(s => s.trim()).filter(Boolean);
    if (!lines.length) continue;
    let name = lines[0];
    for (const line of lines) {
      if (line.length > name.length) name = line;
    }
    let tile = null;
    let el = a.parentElement;
    for (let i = 0; i < 8 && el; i++) {
      if (/S\$\s*[\d,]+\.\d{2}/.test(el.innerText || '')) { tile = el; break; }
      el = el.parentElement;
    }
    if (!tile) continue;
    const prices = [];
    const nodes = [tile].concat(Array.from(tile.querySelectorAll('*')));
    for (const node of nodes) {
      for (const child of node.childNodes) {
        if (child.nodeType !== 3) continue;
        const text = child.textContent || '';
        const re = /S\$\s*([\d,]+\.\d{2})/g;
        let m;
        while ((m = re.exec(text)) !== null) {
          let struck = false;
          try {
            struck = !!node.closest('s, del') ||
              (window.getComputedStyle(node).textDecorationLine || '').indexOf('line-through') !== -1;
          } catch (e) {
            struck = !!node.closest('s, del');
          }
          prices.push({amount: m[1], struck: struck});
        }
      }
    }
    const tileText = tile.innerText || '';
    records.push({
      name: name, href: href, code: code[1],
      prices: prices, tileText: tileText,
      outOfStock: /out of stock/i.test(tileText)
    });
  }
  return records;
}"""


def search_url(mode, query, page_no=1):
    """Whisky search URL for one mode and page."""
    assert mode in ("nt", "tr"), "mode must be nt or tr"
    assert isinstance(query, str) and query.strip(), "query required"
    assert page_no >= 1, "page number must be >= 1"
    points = NT_POINTS if mode == "nt" else TR_POINTS
    url = (BASE_URL + "/en/search?query=" + quote_plus(query.strip())
           + "&cmode=" + mode + "&cagCollectionPoint=" + points)
    if page_no > 1:
        url += "&page=" + str(page_no)
    assert url.startswith(BASE_URL), "search URL must stay on site"
    return url


def _clean_name(text):
    """Collapse whitespace in a tile name to single spaces. Also drop
    a leading repeated brand: when the link wraps brand + name on one
    line ("ROYAL BRACKLA ROYAL BRACKLA 18 Year Old ..."), the brand
    appears twice and would otherwise double in the canonical key."""
    assert isinstance(text, str), "name must be a string"
    words = text.split()
    assert isinstance(words, list), "split must return a list"
    for size in (3, 2, 1):
        if len(words) > 2 * size:
            first = [word.lower() for word in words[:size]]
            second = [word.lower() for word in words[size:2 * size]]
            if first == second:
                words = words[size:]
                break
    cleaned = " ".join(words)
    assert isinstance(cleaned, str), "cleaned name must be a string"
    return cleaned


def _sale_price(prices, tile_text):
    """Sale price from one tile's price records. Returns
    (price, is_from_variant) or (None, False). The sale price is the
    first price that is not struck through; a 'From' price is the
    cheapest variant, so it never supports a verified size."""
    assert isinstance(prices, list), "prices must be a list"
    assert isinstance(tile_text, str), "tile text must be a string"
    for record in prices:
        assert isinstance(record, dict), "price record must be a dict"
        if record.get("struck"):
            continue
        amount = str(record.get("amount") or "")
        price = parse_price_sgd("S$" + amount)
        if price is None:
            continue
        marker = r"from\s*S\$\s*" + re.escape(amount)
        is_from = re.search(marker, tile_text, re.IGNORECASE) is not None
        assert price > 0, "sale price must be positive"
        return price, is_from
    return None, False


def parse_tile_records(records, mode, seen_at):
    """Tile records from page.evaluate -> Listings. Returns
    (listings, tiles_seen, skipped). Dedupes by product code within
    the pass; a tile without a usable price is skipped, never priced."""
    assert mode in ("nt", "tr"), "mode must be nt or tr"
    assert isinstance(records, list), "records must be a list"
    assert seen_at, "seen_at timestamp required"
    seen_codes = set()
    listings = []
    skipped = 0
    for record in records:
        if not isinstance(record, dict):
            skipped += 1
            continue
        code = str(record.get("code") or "")
        href = str(record.get("href") or "")
        name = _clean_name(str(record.get("name") or ""))
        if not code or not href or not name:
            skipped += 1
            continue
        if code in seen_codes:
            skipped += 1
            continue
        seen_codes.add(code)
        price, is_from = _sale_price(record.get("prices") or [],
                                     str(record.get("tileText") or ""))
        if price is None:
            skipped += 1
            continue
        size_ml = parse_size_ml(name)
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
            in_stock=not bool(record.get("outOfStock")),
            traveller_only=(mode == "tr"),
            eligibility=("Pre-order to flight" if mode == "tr"
                         else "Singapore delivery"),
            edition="",
            batch="",
            url=url,
            seen_at=seen_at,
        ))
    assert len(listings) + skipped == len(records)
    return listings, len(records), skipped


def _has_next(html, tile_count, page_no):
    """True when more search pages follow. The 'You have seen X of Y
    products' counter is authoritative; without it, a full 48-tile
    page means more may follow."""
    assert isinstance(html, str), "html must be a string"
    assert tile_count >= 0, "tile count must be non-negative"
    assert page_no >= 1, "page number must be >= 1"
    match = _SEEN_RE.search(html)
    if match is not None:
        seen = int(match.group(1))
        total = int(match.group(2))
        assert total >= 0 and seen >= 0, "counter must be non-negative"
        return seen < total
    return tile_count >= PAGE_SIZE


async def _fetch_page(page, url, retry_empty=False):
    """Load one search page and extract tile records. Returns
    (html, records, reached): records is None when extraction failed.

    The site is a React SPA: on a slow connection the shell loads
    but no product links render inside a short wait (10 Nov 2026
    Fargate run: both modes came back with zero tiles). So the
    product-link wait is long, and when retry_empty is set (first
    page of a mode, where the SPA is still booting) an empty
    extraction waits once more and re-extracts before giving up."""
    assert page is not None, "page required"
    assert isinstance(url, str) and url.startswith("http")
    assert isinstance(retry_empty, bool), "retry flag must be bool"
    reached = await goto_with_retries(page, url)
    assert isinstance(reached, bool), "goto must return a bool"
    if not reached:
        return "", None, False
    for _attempt in range(2):
        try:
            await page.wait_for_selector(
                "a[href*='/en/product/']", timeout=45000)
            break
        except Exception:
            pass
    try:
        await page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass
    html = await page.content()
    assert isinstance(html, str) and len(html) > 0, "page content required"
    try:
        records = await page.evaluate(_EXTRACT_JS)
    except Exception:
        return html, None, True
    assert isinstance(records, list), "extract must return a list"
    if not records and retry_empty:
        try:
            await page.wait_for_selector(
                "a[href*='/en/product/']", timeout=30000)
        except Exception:
            pass
        try:
            records = await page.evaluate(_EXTRACT_JS)
        except Exception:
            return html, None, True
        assert isinstance(records, list), "re-extract must return a list"
        html = await page.content()
        assert isinstance(html, str) and len(html) > 0
    return html, records, True


async def scrape_mode(pw, mode, seen_at="", max_pages=MAX_MODE_PAGES,
                      delay_s=PAGE_DELAY_S, query="whisky",
                      search_url_override=None):
    """Scrape one mode's whisky search. Returns (listings, health).
    Each mode gets a fresh browser context: stale client-side prices
    leak across modes in one session."""
    assert pw is not None, "playwright handle required"
    assert mode in ("nt", "tr"), "mode must be nt or tr"
    assert isinstance(query, str) and query.strip(), "query required"
    assert 1 <= max_pages <= MAX_PAGES, "max_pages out of bounds"
    assert delay_s >= 0, "delay must be non-negative"
    assert search_url_override is None or search_url_override.startswith(
        "http")
    stamp = seen_at or datetime.now(timezone.utc).isoformat()
    assert stamp, "seen_at timestamp required"
    browser, context = await new_stealth_context(pw)
    assert browser is not None and context is not None
    all_listings = []
    pages_scanned = 0
    tiles_seen = 0
    skipped = 0
    failed_kind = ""
    failed_at = 0
    try:
        page = await context.new_page()
        assert page is not None, "page creation failed"
        for page_no in range(1, max_pages + 1):
            if search_url_override is not None:
                url = search_url_override
            else:
                url = search_url(mode, query, page_no)
            html, records, reached = await _fetch_page(
                page, url, retry_empty=(page_no == 1))
            if not reached:
                failed_kind = "load"
                failed_at = page_no
                break
            assert html, "empty page content"
            wall = detect_bot_wall(html)
            assert wall is None or isinstance(wall, str)
            if wall is not None:
                failed_kind = "wall:" + wall
                failed_at = page_no
                break
            if records is None:
                failed_kind = "extract"
                failed_at = page_no
                break
            listings, seen, skip = parse_tile_records(records, mode, stamp)
            all_listings.extend(listings)
            assert len(all_listings) <= MAX_LISTINGS, "listing cap hit"
            pages_scanned += 1
            tiles_seen += seen
            skipped += skip
            if search_url_override is not None:
                break
            if not _has_next(html, seen, page_no):
                break
            await asyncio.sleep(delay_s)
    finally:
        await browser.close()
    if failed_kind.startswith("wall:"):
        status = "blocked"
        detail = "bot wall (%s) at page %d" % (
            failed_kind[len("wall:"):], failed_at)
    elif pages_scanned == 0 and failed_kind == "load":
        status = "blocked"
        detail = "no search page loaded (blocked at page %d)" % failed_at
    elif pages_scanned == 0 and failed_kind == "extract":
        status = "error"
        detail = "tile extraction failed on page 1"
    elif failed_kind:
        status = "partial"
        detail = "%s failed at page %d after %d pages" % (
            failed_kind, failed_at, pages_scanned)
    elif len(all_listings) == 0:
        status = "error"
        detail = ("search page loaded but no product tiles parsed "
                  "(mode %s)" % mode)
    else:
        status = "ok"
        detail = "%s: %d tiles seen, %d skipped" % (
            mode, tiles_seen, skipped)
    health = ParserHealth(
        retailer=RETAILER, pages_scanned=pages_scanned,
        items_found=len(all_listings), status=status, detail=detail)
    assert health.items_found == len(all_listings)
    return all_listings, health


async def scrape(pw, seen_at="", max_pages=MAX_MODE_PAGES,
                 delay_s=PAGE_DELAY_S, query="whisky",
                 nt_url=None, tr_url=None):
    """Scrape both modes, NT first, each in a fresh browser context.
    Returns (listings, health): one health entry with the worst status
    of the two passes and summed pages/items."""
    assert pw is not None, "playwright handle required"
    assert isinstance(query, str) and query.strip(), "query required"
    assert 1 <= max_pages <= MAX_PAGES, "max_pages out of bounds"
    assert delay_s >= 0, "delay must be non-negative"
    assert nt_url is None or nt_url.startswith("http")
    assert tr_url is None or tr_url.startswith("http")
    stamp = seen_at or datetime.now(timezone.utc).isoformat()
    assert stamp, "seen_at timestamp required"
    listings = []
    healths = []
    for mode, override in (("nt", nt_url), ("tr", tr_url)):
        found, entry = await scrape_mode(
            pw, mode, seen_at=stamp, max_pages=max_pages,
            delay_s=delay_s, query=query, search_url_override=override)
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
