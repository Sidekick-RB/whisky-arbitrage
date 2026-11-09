"""Shared Shopify products.json parsing for the SG retailers.

TLC, Spirited, Tipsy Topsy, Asher and PIVENE are all Shopify stores
whose public products.json feed returns name / handle / variants[0]
(price string, available bool, compare_at_price). The feed is the
primary source: the HTML tiles on these stores carry no reliable stock
signal (TLC removes unavailable items from the grid; Spirited renders
sold-out badges in every tile) and Spirited has no size anywhere in
the tile or JSON.

Two traps are handled here:
* Wine-heavy catalogues (Asher 4000+ products, PIVENE 1158) — a
  keyword-only filter misses whisky titled in wine style, e.g.
  PIVENE's "Yamazaki, 2022 Limited Edition NV 700ML" (no whisky
  keyword, no age statement). The filter therefore also accepts an
  age statement ("12 Year Old", "21 Years") or a known distillery /
  brand name, unless the product_type is a known non-whisky type.
* Non-bottle items (masterclasses, bundles) are excluded by title.
"""

import asyncio
import re
from datetime import datetime, timezone

from ..models import Listing, canonical_from_name
from ..report import ParserHealth
from .base import (
    MAX_LISTINGS,
    PAGE_DELAY_S,
    fetch_json,
    parse_price,
    parse_size_ml,
)

FEED_LIMIT = 250

_WHISKY_KEYWORDS = (
    "whisky", "whiskey", "bourbon", "scotch", "single malt",
    "blended malt", "rye", "tennessee",
)
_EXCLUDED_KEYWORDS = (
    "masterclass", "tasting", "bundle", "gift card", "voucher", "event",
)
_NON_WHISKY_TYPES = (
    "red wine", "white wine", "sparkling", "sake", "fortified wine",
    "dessert wine", "rose", "rosé", "beer", "cider", "accessories",
    "white", "gin", "vodka", "rum", "tequila", "mezcal", "liqueur",
    "brandy", "cognac", "armagnac", "calvados", "grappa", "pisco",
    "cachaca", "baijiu", "soju", "champagne", "prosecco", "port",
    "sherry",
)
_AGE_RE = re.compile(r"\b\d{1,2}\s*(?:years|year old|yo)\b", re.IGNORECASE)

# Distillery / brand names, used only for titles with no whisky keyword
# and no age statement (PIVENE wine-style titles). Whole-word matches;
# bare "glen" is deliberately absent (it appears in wine names).
_DISTILLERY_NAMES = (
    "macallan", "glenfiddich", "glenlivet", "glenmorangie", "glenfarclas",
    "glendronach", "glen grant", "glen scotia", "glen keith",
    "glenallachie", "glengoyne", "glenrothes", "glen moray", "glen spey",
    "glen elgin", "glen ord", "glenlossie", "glen garioch", "glenkinchie",
    "glenturret", "glenburgie", "glencadam", "glendullan", "glen garioch",
    "aberlour", "balvenie", "dalmore", "dalwhinnie", "talisker", "oban",
    "lagavulin", "laphroaig", "ardbeg", "bowmore", "bruichladdich",
    "caol ila", "bunnahabhain", "kilchoman", "springbank", "kilkerran",
    "longrow", "hazelburn", "highland park", "clynelish", "brora",
    "port ellen", "mortlach", "royal brackla", "royal salute", "chivas",
    "ballantine", "ballantines", "johnnie walker", "singleton", "jura",
    "deanston", "edradour", "benriach", "benrinnes", "ben nevis",
    "tomatin", "tomintoul", "aberfeldy", "ancnoc", "ardmore",
    "ardnamurchan", "auchentoshan", "balblair", "benromach",
    "blair athol", "cardhu", "craigellachie", "dufftown", "fettercairn",
    "inchgower", "linkwood", "loch lomond", "macduff", "mannochmore",
    "miltonduff", "old pulteney", "pulteney", "speyburn", "strathisla",
    "tamdhu", "tamnavulin", "teaninich", "toberwory", "tobermory",
    "ledaig", "wolfburn", "nc'nean", "ncnean", "waterford",
    "yamazaki", "hakushu", "hibiki", "nikka", "yoichi", "miyagikyo",
    "chichibu", "karuizawa", "hanyu", "akashi", "mars", "komagatake",
    "fuji", "sakurao", "togouchi",
    "redbreast", "jameson", "midleton", "green spot", "yellow spot",
    "red spot", "blue spot", "powers", "teeling", "writers tears",
    "bushmills", "tullamore", "kilbeggan", "connemara", "tyrconnell",
    "glendalough",
    "blanton", "buffalo trace", "woodford", "maker's mark",
    "wild turkey", "willett", "michter", "whistlepig", "jack daniel",
    "jim beam", "evan williams", "four roses", "eagle rare", "weller",
    "pappy", "stagg", "elijah craig", "knob creek", "basil hayden",
    "booker's", "baker's", "old forester", "bulleit", "rittenhouse",
    "sazerac", "1792", "russell's reserve", "henry mckenna",
    "heaven hill", "dickel", "balcones", "westland", "westward",
    "stranahan's", "high west", "smooth ambler", "barrell",
    "garrison brothers", "old grand-dad", "very old barton", "larceny",
    "bernheim", "peerless", "rabbit hole", "angels envy",
    "angel's envy", "blood oath", "jefferson's", "jefferson",
    "van winkle", "william larue weller", "thomas h handy",
    "george t stagg", "e.h. taylor", "e h taylor", "hudson",
    "tuthilltown", "hillrock", "widow jane", "kings county",
    "lost lantern", "ironroot", "andalusia", "still austin",
    "milam & greene", "treaty oak", "yellow rose",
    "kavalan", "amrut", "paul john", "rampur", "solan",
    "crown royal", "canadian club", "lot 40", "gooderham",
    "pike creek", "forty creek", "alberta premium", "collingwood",
    "wiser's", "black velvet", "pendleton",
)
_DISTILLERY_RES = tuple(
    re.compile(r"\b" + re.escape(name) + r"\b", re.IGNORECASE)
    for name in _DISTILLERY_NAMES
)


def is_whisky_title(title, product_type=""):
    """True when a Shopify product title looks like a whisky bottle.

    Keyword, age-statement, product_type and distillery-name rules,
    in that order. A known non-whisky product_type vetoes everything
    except the excluded-keyword check (e.g. a "Whisky Barrel Aged"
    red wine is wine, not whisky).
    """
    assert isinstance(title, str) and title.strip()
    assert isinstance(product_type, str)
    text = title.lower()
    if any(word in text for word in _EXCLUDED_KEYWORDS):
        return False
    ptype = product_type.lower()
    if ptype in _NON_WHISKY_TYPES:
        return False
    if any(word in text for word in _WHISKY_KEYWORDS):
        return True
    if "whisk" in ptype:
        return True
    if _AGE_RE.search(title) is not None:
        return True
    for pattern in _DISTILLERY_RES:
        if pattern.search(title) is not None:
            return True
    return False


def parse_products_json(data, base_url, retailer, seen_at):
    """Parse one products.json page. Returns (listings, has_next,
    products_seen, skipped).

    has_next is True only when the page is full (FEED_LIMIT products),
    so a short page ends the loop. A product is skipped (never guessed)
    when it is not a whisky title, has no handle, no first variant,
    or no parseable price. Size comes from the title; a title with no
    size yields size_ml None (unverified), never an assumed size.
    """
    assert isinstance(data, dict), "feed data must be a dict"
    assert isinstance(base_url, str) and base_url.startswith("http")
    assert retailer, "retailer must be non-empty"
    assert seen_at, "seen_at timestamp required"
    products = data.get("products")
    assert isinstance(products, list), "feed must carry a products list"
    listings = []
    skipped = 0
    for product in products:
        if not isinstance(product, dict):
            skipped += 1
            continue
        title = product.get("title") or ""
        ptype = product.get("product_type") or ""
        handle = product.get("handle") or ""
        variants = product.get("variants") or []
        if not is_whisky_title(title, ptype) or not handle or not variants:
            skipped += 1
            continue
        variant = variants[0]
        if not isinstance(variant, dict):
            skipped += 1
            continue
        price = parse_price(str(variant.get("price") or ""))
        if price is None:
            skipped += 1
            continue
        size_ml = parse_size_ml(title)
        listings.append(Listing(
            retailer=retailer,
            name=title.strip(),
            canonical=canonical_from_name(title),
            size_ml=size_ml,
            size_verified=size_ml is not None,
            price_sgd=price,
            in_stock=bool(variant.get("available")),
            traveller_only=False,
            eligibility="Singapore delivery",
            edition="",
            batch="",
            url=base_url + "/products/" + handle,
            seen_at=seen_at,
        ))
    has_next = len(products) >= FEED_LIMIT
    assert len(listings) + skipped == len(products)
    return listings, has_next, len(products), skipped


async def scrape_feed(feed_urls, base_url, retailer, max_pages,
                      delay_s=PAGE_DELAY_S, seen_at=""):
    """Scrape one or more products.json feeds (paginated).

    Bounded: at most max_pages page fetches across all feeds, at most
    MAX_LISTINGS listings. Stops early on a short page. Returns
    (listings, health); a feed that never returns data is "error",
    a page cap hit while pages remain is "partial".
    """
    assert feed_urls, "at least one feed URL required"
    assert isinstance(base_url, str) and base_url.startswith("http")
    assert retailer, "retailer must be non-empty"
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
    for feed_url in feed_urls:
        assert feed_url.startswith("http"), "feed URL must be absolute"
        page_no = 1
        has_next = False
        while pages_scanned < max_pages:
            url = feed_url + ("&page=%d" % page_no if "?" in feed_url
                              else "?page=%d" % page_no)
            data = fetch_json(url)
            if data is None:
                failed_at = url
                break
            listings, has_next, seen, skip = parse_products_json(
                data, base_url, retailer, stamp)
            all_listings.extend(listings)
            assert len(all_listings) <= MAX_LISTINGS, "listing cap hit"
            pages_scanned += 1
            products_seen += seen
            skipped += skip
            if not has_next:
                break
            page_no += 1
            await asyncio.sleep(delay_s)
        if failed_at:
            break
        if pages_scanned >= max_pages and has_next:
            capped = True
            break
    if pages_scanned == 0:
        status = "error"
        detail = "no feed page returned data (first failure: %s)" % failed_at
    elif failed_at:
        status = "partial"
        detail = "feed failed at %s after %d pages" % (failed_at, pages_scanned)
    elif capped:
        status = "partial"
        detail = "page cap %d hit with more pages remaining" % max_pages
    elif len(all_listings) == 0:
        status = "zero-items"
        detail = "feeds loaded but no whisky products parsed"
    else:
        status = "ok"
        detail = "%d products seen, %d skipped" % (products_seen, skipped)
    health = ParserHealth(
        retailer=retailer, pages_scanned=pages_scanned,
        items_found=len(all_listings), status=status, detail=detail)
    assert health.items_found == len(all_listings)
    return all_listings, health
