"""Weekly hunt runner (slice 6: all SG retailers + overseas comparisons).

Runs every SG parser (Paneco, Cellarbration, TLC, Spirited, Tipsy
Topsy, Asher, PIVENE, LMDW, Malt & Wine Asia, KrisShop, iShopChangi),
dedupes by retailer+canonical+mode, then compares every listing
against verified in-stock overseas prices (TWE, The Whisky World,
ReserveBar, Astor Wines) via hunt/engine.py. LLM name-cleaning
(Bedrock primary, Groq fallback) runs only when an exact canonical
match fails, and only while the call budget lasts; cleaned names
persist in a name cache (S3 state/name-cache.json) so the same
bottles are not re-cleaned every week. Blocked overseas retailers
(Master of Malt, Wine.com, Total Wine) are probed once per run and
reported as blocked, never as prices. iShopChangi is scraped in both
modes (Non-Traveller and Traveller) via its /bin search JSON API; a
bottle in both modes yields two listings, one per mode. Promo codes
are not applied in this slice (codes=()); the effective price is
the shelf price. Never purchases, never adds to cart, never tests
a promo code.
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone

from .engine import compare
from .fx import fetch_rates, fixed_rates
from .models import MIN_HUNT_PRICE_SGD, canonical_from_name, listing_key
from .names import BEDROCK_MODEL_ID, MAX_GROQ_CALLS, clean_to_key
from .parsers import (
    asher,
    astor,
    cellarbration,
    dramfellows,
    flaviar,
    ishopchangi,
    krisshop,
    lmdw,
    lochfyne,
    maltwineasia,
    mashgrape,
    masterofmalt,
    paneco,
    pivene,
    reservebar,
    spirited,
    tipsy,
    tlc,
    totalwine,
    twe,
    whiskybarrel,
    whiskyworld,
    winecom,
    woodencork,
)
from .parsers.base import MAX_PAGES
from .report import (
    ParserHealth,
    best_price_per_bottle,
    build_report,
    diff_states,
    load_state,
    serialize_listing,
    write_json_file,
    write_report,
)
from .s3upload import (
    NAME_CACHE_KEY,
    STATE_KEY,
    download_file,
    report_key,
    upload_cache,
    upload_run,
)

DEFAULT_OUT = "hunt-report.json"
DEFAULT_STATE = "hunt-state.json"
DEFAULT_NAME_CACHE = "name-cache.json"
MAX_OVERSEAS_QUERIES = 200
MAX_LLM_CALLS = 2000
MAX_NAME_CACHE_ENTRIES = 5000
# One overseas search per retailer per term gets at most this long.
# Slice 10 stalled 68 minutes in the 26-50 batch: a throttled
# search has no internal bound that fires, so the run needs one.
OVERSEAS_RETAILER_TIMEOUT_S = 120
# Timeouts before a retailer is skipped for the rest of the run.
MAX_RETAILER_TIMEOUTS = 2
# Progress lines in the 150-term overseas loop. Slice 6 went ~63
# silent minutes at every-25; every-5 shows a stall within one
# batch instead of after it.
OVERSEAS_PROGRESS_EVERY = 5


def parse_args(argv):
    """CLI arguments for one weekly run."""
    assert isinstance(argv, list), "argv must be a list"
    parser = argparse.ArgumentParser(
        description="Whisky deal hunt: scrape SG retailers, compare "
                    "against overseas prices, diff, report.")
    parser.add_argument("--out", default=DEFAULT_OUT,
                        help="report JSON path")
    parser.add_argument("--state", default=DEFAULT_STATE,
                        help="state JSON path (read + written)")
    parser.add_argument("--name-cache", default=DEFAULT_NAME_CACHE,
                        help="name-cleaning cache JSON path (read + "
                             "written)")
    parser.add_argument("--fx-gbp", type=float, default=None,
                        help="override GBP->SGD rate")
    parser.add_argument("--fx-usd", type=float, default=None,
                        help="override USD->SGD rate")
    parser.add_argument("--max-pages", type=int, default=MAX_PAGES,
                        help="Paneco pagination cap")
    parser.add_argument("--paneco-url", default=None,
                        help="override Paneco sale URL (testing/staging)")
    parser.add_argument("--max-overseas-queries", type=int,
                        default=MAX_OVERSEAS_QUERIES,
                        help="distinct overseas search terms per run")
    parser.add_argument("--max-llm-calls", type=int,
                        default=MAX_LLM_CALLS,
                        help="LLM name-cleaning calls per run (Bedrock, "
                             "Groq fallback)")
    parser.add_argument("--max-groq-calls", type=int,
                        default=MAX_GROQ_CALLS,
                        help="Groq fallback calls per run (free-tier cap)")
    args = parser.parse_args(argv)
    assert args.out.endswith(".json"), "--out must end in .json"
    assert args.state.endswith(".json"), "--state must end in .json"
    assert args.name_cache.endswith(".json"), (
        "--name-cache must end in .json")
    assert 1 <= args.max_pages <= MAX_PAGES, "--max-pages out of bounds"
    assert args.paneco_url is None or args.paneco_url.startswith("http")
    assert 1 <= args.max_overseas_queries <= 200, (
        "--max-overseas-queries out of bounds")
    assert 0 <= args.max_llm_calls <= 2000, "--max-llm-calls out of bounds"
    assert 0 <= args.max_groq_calls <= MAX_GROQ_CALLS, (
        "--max-groq-calls out of bounds")
    return args


def resolve_fx(args):
    """FX for the run: CLI overrides, else a live fetch. None on failure."""
    assert args is not None, "parsed args required"
    assert hasattr(args, "fx_gbp") and hasattr(args, "fx_usd"), "fx args required"
    if args.fx_gbp is not None and args.fx_usd is not None:
        stamp = datetime.now(timezone.utc).isoformat()
        return fixed_rates(args.fx_gbp, args.fx_usd, "cli-override", stamp)
    if args.fx_gbp is not None or args.fx_usd is not None:
        return None
    return fetch_rates()


def search_term(canonical):
    """Recall-oriented overseas search term for a canonical key.

    Distillery words + the age number only (edition words are dropped
    for recall; match_prices does the precise filtering). "18yr" ->
    "18"; "ans" (LMDW French "years") and "nas" are dropped.
    """
    assert isinstance(canonical, str) and canonical, "canonical required"
    words = canonical.split("-")
    terms = []
    for word in words:
        if word.endswith("yr") and word[:-2].isdigit():
            terms.append(word[:-2])
            break
        if word in ("ans", "nas"):
            continue
        terms.append(word)
    result = " ".join(terms).strip()
    assert result, "search term must be non-empty"
    return result


def dedupe_listings(listings):
    """Dedupe by listing_key, keeping the first. Returns
    (unique, duplicates). Duplicates are recorded, never fatal."""
    assert isinstance(listings, list), "listings must be a list"
    seen = {}
    duplicates = 0
    for listing in listings:
        key = listing_key(listing)
        if key in seen:
            duplicates += 1
            continue
        seen[key] = listing
    unique = [seen[key] for key in sorted(seen)]
    assert len(unique) + duplicates == len(listings)
    return unique, duplicates


def aggregate_health(retailer, healths):
    """One health entry per retailer from many per-query entries.

    Status is the worst observed (blocked > error > partial >
    zero-items > ok); pages and items are summed.
    """
    assert retailer, "retailer must be non-empty"
    assert healths, "at least one health entry required"
    order = {"blocked": 0, "error": 1, "partial": 2, "zero-items": 3,
             "ok": 4}
    worst = "ok"
    pages = 0
    items = 0
    details = []
    for health in healths:
        assert health.retailer == retailer, "health must name the retailer"
        if order[health.status] < order[worst]:
            worst = health.status
        pages += health.pages_scanned
        items += health.items_found
        if health.status != "ok" and health.detail not in details:
            details.append(health.detail)
    detail = "%d queries, %d priced" % (len(healths), items)
    if details:
        detail += "; " + "; ".join(details[:3])
    result = ParserHealth(
        retailer=retailer, pages_scanned=pages, items_found=items,
        status=worst, detail=detail)
    assert result.items_found == items
    return result


def _match_exact(prices, canonical):
    """Overseas prices whose name canonicalises exactly to the key."""
    assert isinstance(prices, list)
    assert isinstance(canonical, str) and canonical
    matched = []
    for price in prices:
        assert price.name, "overseas price must carry a name"
        if canonical_from_name(price.name) == canonical:
            matched.append(price)
    return matched


def _twe_skip_after(entry, probe=False):
    """Whether a TWE result means skip the remaining searches.

    A "blocked" result (bot wall, or no page loaded) ends TWE for
    the run: TWE has returned zero priced items while blocked in
    every run, and each browser search costs minutes once the site
    starts throttling — the slice-8 run stalled 85 minutes in the
    101-125 batch on exactly this. A "zero-items" result on the
    probe term means the same: the probe is the most-listed
    bottle, so zero cards for it means TWE is not yielding this
    run. Any other status keeps searching, and the next run probes
    again from the first term.
    """
    assert entry is not None, "health entry required"
    assert entry.retailer, "health must name a retailer"
    if entry.status == "blocked":
        return True
    return bool(probe) and entry.status == "zero-items"


async def _search_bounded(coro, retailer,
                          timeout_s=OVERSEAS_RETAILER_TIMEOUT_S):
    """Run one overseas search bounded in time.

    Returns (prices, health, timed_out). A timeout becomes an
    "error" health entry with no prices, so the run moves on
    instead of stalling on one throttled search. Other exceptions
    still propagate — a parser crash must stay loud.
    """
    assert coro is not None, "search coroutine required"
    assert retailer, "retailer required"
    assert timeout_s > 0, "timeout must be positive"
    try:
        found, entry = await asyncio.wait_for(coro, timeout=timeout_s)
    except asyncio.TimeoutError:
        entry = ParserHealth(
            retailer=retailer, pages_scanned=0, items_found=0,
            status="error",
            detail="search timed out after %ds" % timeout_s)
        return [], entry, True
    assert entry is not None, "search must return a health entry"
    return found, entry, False


def _order_terms(terms):
    """Overseas search terms, most expensive bottle first.

    Rare expensive bottles (Baccarat, Wildmoor 40) have few listings
    and were never searched under the old most-listed-first order;
    price-first puts them in the term budget. Ties break by listing
    count, then alphabetically. Returns the ordered term list.
    """
    assert isinstance(terms, dict), "terms must be a dict"
    term_max_price = {
        term: max(item.price_sgd for item in items)
        for term, items in terms.items()}
    assert len(term_max_price) == len(terms), "one max price per term"
    ordered = sorted(
        terms, key=lambda term: (-term_max_price[term],
                                 -len(terms[term]), term))
    assert len(ordered) == len(terms), "ordering must not drop terms"
    return ordered


def _record_timeout(retailer, timed_out, timeouts, skipped,
                    limit=MAX_RETAILER_TIMEOUTS):
    """Record one search timeout. Returns True when the retailer
    should be skipped for the rest of the run: two timeouts mean
    throttled, not unlucky. The skipped set is mutated in place.
    """
    assert retailer, "retailer required"
    assert timeouts is not None and skipped is not None
    assert limit >= 1, "timeout limit must be at least 1"
    if not timed_out:
        return False
    timeouts[retailer] = timeouts.get(retailer, 0) + 1
    if timeouts[retailer] >= limit:
        skipped.add(retailer)
        return True
    return False


def match_overseas(listing, prices, llm_cache, llm_budget,
                   groq_budget=MAX_GROQ_CALLS):
    """Overseas prices for one SG listing: exact canonical match
    first; LLM cleaning (Bedrock, Groq fallback) only when no exact
    match exists and budget remains. Returns (matched, llm_calls_used,
    groq_calls_used). An LLM failure (None) leaves the listing
    unmatched — never a guessed match. The Groq fallback is capped
    separately: once groq_budget is spent, only Bedrock is tried."""
    assert listing.price_sgd > 0, "listing price must be positive"
    assert isinstance(prices, list)
    assert isinstance(llm_cache, dict)
    assert llm_budget >= 0, "LLM budget must be non-negative"
    assert groq_budget >= 0, "Groq budget must be non-negative"
    exact = _match_exact(prices, listing.canonical)
    if exact:
        return exact, 0, 0
    if llm_budget <= 0 or not prices:
        return [], 0, 0
    calls = 0
    groq_used = 0
    local_key = llm_cache.get(listing.name)
    if local_key is None:
        local_key, used_groq = clean_to_key(
            listing.name, allow_groq=(groq_used < groq_budget))
        llm_cache[listing.name] = local_key
        calls += 1
        groq_used += 1 if used_groq else 0
    if local_key is None:
        return [], calls, groq_used
    matched = []
    for price in prices:
        if not price.in_stock:
            continue
        overseas_key = llm_cache.get(price.name)
        if overseas_key is None:
            if calls >= llm_budget:
                break
            overseas_key, used_groq = clean_to_key(
                price.name, allow_groq=(groq_used < groq_budget))
            llm_cache[price.name] = overseas_key
            calls += 1
            groq_used += 1 if used_groq else 0
        if overseas_key is not None and overseas_key == local_key:
            matched.append(price)
    assert calls <= llm_budget, "LLM budget exceeded"
    assert groq_used <= groq_budget, "Groq budget exceeded"
    return matched, calls, groq_used


def _error_health(retailer, exc):
    """ParserHealth for a parser that raised: error, never a crash.

    One bad retailer must never kill the run; the failure is recorded
    in parser health and the run continues with the other retailers.
    """
    assert retailer, "retailer name required"
    assert isinstance(exc, Exception), "exc must be an exception"
    detail = "parser crashed: %s: %s" % (type(exc).__name__, str(exc)[:200])
    health = ParserHealth(
        retailer=retailer, pages_scanned=0, items_found=0,
        status="error", detail=detail)
    assert health.status == "error", "error health must say error"
    return health


def _log_parser(entry):
    """One progress line per parser, flushed immediately.

    Stdout is block-buffered in Fargate, so without flush=True
    nothing reaches CloudWatch until the run ends. A 15-minute
    crash check can only see where the run is when each parser
    logs its result as it finishes.
    """
    assert entry.retailer, "health must name a retailer"
    assert entry.status, "health status required"
    print("%s: %d listings, %s (%s)" % (
        entry.retailer, entry.items_found, entry.status,
        entry.detail[:120]), flush=True)


async def _scrape_sg_urllib(stamp):
    """SG parsers that need no browser. Returns (listings, health).

    A parser that raises is recorded as an error entry and the run
    continues with the remaining parsers.
    """
    assert stamp, "timestamp required"
    listings = []
    health = []
    for scrape, retailer in ((tlc.scrape, tlc.RETAILER),
                             (spirited.scrape, spirited.RETAILER),
                             (tipsy.scrape, tipsy.RETAILER),
                             (asher.scrape, asher.RETAILER),
                             (pivene.scrape, pivene.RETAILER),
                             (lmdw.scrape, lmdw.RETAILER),
                             (maltwineasia.scrape, maltwineasia.RETAILER),
                             (krisshop.scrape_whisky, krisshop.RETAILER),
                             (ishopchangi.scrape, ishopchangi.RETAILER)):
        try:
            found, entry = await scrape(seen_at=stamp)
        except Exception as exc:
            found, entry = [], _error_health(retailer, exc)
        assert entry.items_found == len(found)
        listings.extend(found)
        health.append(entry)
        _log_parser(entry)
    assert health, "at least one SG health entry required"
    return listings, health


def prune_name_cache(cache):
    """Persistable name cache: only successful cleanings (raw name ->
    canonical key string), newest first, capped at
    MAX_NAME_CACHE_ENTRIES. Failures (None) are never persisted —
    they are retried next run. Returns a new dict."""
    assert isinstance(cache, dict), "cache must be a dict"
    kept = [(name, key) for name, key in cache.items()
            if isinstance(name, str) and isinstance(key, str)]
    assert len(kept) <= len(cache), "prune must not grow the cache"
    if len(kept) > MAX_NAME_CACHE_ENTRIES:
        kept = kept[-MAX_NAME_CACHE_ENTRIES:]
    pruned = dict(kept)
    assert len(pruned) <= MAX_NAME_CACHE_ENTRIES, "cache cap exceeded"
    return pruned


async def run_once(fx, max_pages, paneco_url=None,
                   max_overseas_queries=MAX_OVERSEAS_QUERIES,
                   max_llm_calls=MAX_LLM_CALLS,
                   max_groq_calls=MAX_GROQ_CALLS, name_cache=None):
    """Run the full hunt. Returns (listings, health, comparisons,
    notes). name_cache is a dict the run reads and updates in place
    (raw name -> canonical key); pass None for a fresh empty cache."""
    assert fx is not None, "FX rates required"
    assert 1 <= max_pages <= MAX_PAGES, "max_pages out of bounds"
    assert 1 <= max_overseas_queries <= 200, "query cap out of bounds"
    assert 0 <= max_llm_calls <= 2000, "LLM cap out of bounds"
    assert 0 <= max_groq_calls <= MAX_GROQ_CALLS, "Groq cap out of bounds"
    assert paneco_url is None or paneco_url.startswith("http")
    assert name_cache is None or isinstance(name_cache, dict), (
        "name_cache must be a dict or None")
    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth

    stamp = datetime.now(timezone.utc).isoformat()
    as_of = stamp[:10]
    assert stamp and as_of, "timestamps required"
    notes = []
    health = []

    # --- SG scrape (urllib parsers first, then the browser ones).
    listings, sg_health = await _scrape_sg_urllib(stamp)
    health.extend(sg_health)
    async with Stealth().use_async(async_playwright()) as pw:
        assert pw is not None, "playwright handle required"
        try:
            if paneco_url is not None:
                found, entry = await paneco.scrape_sale(
                    pw, max_pages=max_pages, seen_at=stamp,
                    sale_url=paneco_url)
            else:
                found, entry = await paneco.scrape_sale(
                    pw, max_pages=max_pages, seen_at=stamp)
        except Exception as exc:
            found, entry = [], _error_health(paneco.RETAILER, exc)
        listings.extend(found)
        health.append(entry)
        _log_parser(entry)
        try:
            found, entry = await cellarbration.scrape(pw, seen_at=stamp)
        except Exception as exc:
            found, entry = [], _error_health(cellarbration.RETAILER, exc)
        listings.extend(found)
        health.append(entry)
        _log_parser(entry)
        # --- Overseas candidates: in stock, >= S$100, verified size.
        unique, duplicates = dedupe_listings(listings)
        if duplicates:
            notes.append("%d duplicate listings dropped" % duplicates)
        listings = unique
        candidates = [item for item in listings
                      if item.in_stock
                      and item.price_sgd >= MIN_HUNT_PRICE_SGD
                      and item.size_ml is not None
                      and item.size_verified]
        terms = {}
        for item in candidates:
            terms.setdefault(search_term(item.canonical), []).append(item)
        ordered_terms = _order_terms(terms)
        chosen = ordered_terms[:max_overseas_queries]
        skipped_terms = len(ordered_terms) - len(chosen)
        assert len(ordered_terms) >= len(chosen), "term cap must not grow"
        assert len(candidates) >= 0, "candidate count must be sane"
        print("Overseas: %d terms, %d candidates"
              % (len(chosen), len(candidates)), flush=True)
        if skipped_terms:
            notes.append("%d overseas search terms skipped by the %d-term "
                         "cap" % (skipped_terms, max_overseas_queries))
        # --- Overseas searches, one per distinct term.
        prices_by_term = {}
        twe_health = []
        ww_health = []
        rb_health = []
        astor_health = []
        lochfyne_health = []
        whiskybarrel_health = []
        dramfellows_health = []
        woodencork_health = []
        mashgrape_health = []
        flaviar_health = []
        sitemap_urls = reservebar.load_sitemap()
        if sitemap_urls is None:
            rb_health.append(ParserHealth(
                retailer=reservebar.RETAILER, pages_scanned=0,
                items_found=0, status="error",
                detail="sitemap failed to load"))
            notes.append("ReserveBar sitemap failed to load; no US "
                         "ReserveBar benchmarks this run.")
        twe_blocked = False
        retailer_timeouts = {}
        retailer_skipped = set()
        for term_no, term in enumerate(chosen, start=1):
            if not twe_blocked and \
                    twe.RETAILER not in retailer_skipped:
                found, entry, timed_out = await _search_bounded(
                    twe.search(pw, term, seen_at=stamp), twe.RETAILER)
                twe_health.append(entry)
                prices_by_term.setdefault(term, []).extend(found)
                if _twe_skip_after(entry, probe=(term_no == 1)):
                    twe_blocked = True
                _record_timeout(twe.RETAILER, timed_out,
                                retailer_timeouts, retailer_skipped)
            if whiskyworld.RETAILER not in retailer_skipped:
                found, entry, timed_out = await _search_bounded(
                    whiskyworld.search(term, seen_at=stamp),
                    whiskyworld.RETAILER)
                ww_health.append(entry)
                prices_by_term.setdefault(term, []).extend(found)
                _record_timeout(whiskyworld.RETAILER, timed_out,
                                retailer_timeouts, retailer_skipped)
            if astor.RETAILER not in retailer_skipped:
                found, entry, timed_out = await _search_bounded(
                    astor.search(term, seen_at=stamp), astor.RETAILER)
                astor_health.append(entry)
                prices_by_term.setdefault(term, []).extend(found)
                _record_timeout(astor.RETAILER, timed_out,
                                retailer_timeouts, retailer_skipped)
            if sitemap_urls is not None and \
                    reservebar.RETAILER not in retailer_skipped:
                found, entry, timed_out = await _search_bounded(
                    reservebar.search(term, sitemap_urls=sitemap_urls,
                                      seen_at=stamp),
                    reservebar.RETAILER)
                rb_health.append(entry)
                prices_by_term.setdefault(term, []).extend(found)
                _record_timeout(reservebar.RETAILER, timed_out,
                                retailer_timeouts, retailer_skipped)
            if lochfyne.RETAILER not in retailer_skipped:
                found, entry, timed_out = await _search_bounded(
                    lochfyne.search(
                        term,
                        wanted={item.canonical for item in terms[term]},
                        seen_at=stamp),
                    lochfyne.RETAILER)
                lochfyne_health.append(entry)
                prices_by_term.setdefault(term, []).extend(found)
                _record_timeout(lochfyne.RETAILER, timed_out,
                                retailer_timeouts, retailer_skipped)
            if whiskybarrel.RETAILER not in retailer_skipped:
                found, entry, timed_out = await _search_bounded(
                    whiskybarrel.search(
                        term,
                        wanted={item.canonical for item in terms[term]},
                        seen_at=stamp),
                    whiskybarrel.RETAILER)
                whiskybarrel_health.append(entry)
                prices_by_term.setdefault(term, []).extend(found)
                _record_timeout(whiskybarrel.RETAILER, timed_out,
                                retailer_timeouts, retailer_skipped)
            if dramfellows.RETAILER not in retailer_skipped:
                found, entry, timed_out = await _search_bounded(
                    dramfellows.search(
                        term,
                        wanted={item.canonical for item in terms[term]},
                        seen_at=stamp),
                    dramfellows.RETAILER)
                dramfellows_health.append(entry)
                prices_by_term.setdefault(term, []).extend(found)
                _record_timeout(dramfellows.RETAILER, timed_out,
                                retailer_timeouts, retailer_skipped)
            if woodencork.RETAILER not in retailer_skipped:
                found, entry, timed_out = await _search_bounded(
                    woodencork.search(
                        term,
                        wanted={item.canonical for item in terms[term]},
                        seen_at=stamp),
                    woodencork.RETAILER)
                woodencork_health.append(entry)
                prices_by_term.setdefault(term, []).extend(found)
                _record_timeout(woodencork.RETAILER, timed_out,
                                retailer_timeouts, retailer_skipped)
            if mashgrape.RETAILER not in retailer_skipped:
                found, entry, timed_out = await _search_bounded(
                    mashgrape.search(
                        term,
                        wanted={item.canonical for item in terms[term]},
                        seen_at=stamp),
                    mashgrape.RETAILER)
                mashgrape_health.append(entry)
                prices_by_term.setdefault(term, []).extend(found)
                _record_timeout(mashgrape.RETAILER, timed_out,
                                retailer_timeouts, retailer_skipped)
            if flaviar.RETAILER not in retailer_skipped:
                found, entry, timed_out = await _search_bounded(
                    flaviar.search(
                        term,
                        wanted={item.canonical for item in terms[term]},
                        seen_at=stamp),
                    flaviar.RETAILER)
                flaviar_health.append(entry)
                prices_by_term.setdefault(term, []).extend(found)
                _record_timeout(flaviar.RETAILER, timed_out,
                                retailer_timeouts, retailer_skipped)
            if term_no % OVERSEAS_PROGRESS_EVERY == 0 or \
                    term_no == len(chosen):
                # Slice 6 went ~63 silent minutes and slice 10
                # stalled at 25/150; log often so a crash check
                # can see where the run is.
                print("Overseas search: %d/%d terms"
                      % (term_no, len(chosen)), flush=True)
        if twe_blocked:
            notes.append("TWE blocked on its probe search; the other "
                         "%d TWE searches were skipped — TWE has "
                         "returned no priced items while blocked."
                         % (len(chosen) - len(twe_health)))
        for retailer in sorted(retailer_skipped):
            notes.append("%s timed out twice and was skipped for the "
                         "remaining overseas searches." % retailer)
        if twe_health:
            entry = aggregate_health(twe.RETAILER, twe_health)
            health.append(entry)
            _log_parser(entry)
        if ww_health:
            entry = aggregate_health(whiskyworld.RETAILER, ww_health)
            health.append(entry)
            _log_parser(entry)
        if astor_health:
            entry = aggregate_health(astor.RETAILER, astor_health)
            health.append(entry)
            _log_parser(entry)
        if rb_health:
            entry = aggregate_health(reservebar.RETAILER, rb_health)
            health.append(entry)
            _log_parser(entry)
        if lochfyne_health:
            entry = aggregate_health(lochfyne.RETAILER, lochfyne_health)
            health.append(entry)
            _log_parser(entry)
        if whiskybarrel_health:
            entry = aggregate_health(whiskybarrel.RETAILER,
                                     whiskybarrel_health)
            health.append(entry)
            _log_parser(entry)
        if dramfellows_health:
            entry = aggregate_health(dramfellows.RETAILER,
                                     dramfellows_health)
            health.append(entry)
            _log_parser(entry)
        if woodencork_health:
            entry = aggregate_health(woodencork.RETAILER, woodencork_health)
            health.append(entry)
            _log_parser(entry)
        if mashgrape_health:
            entry = aggregate_health(mashgrape.RETAILER, mashgrape_health)
            health.append(entry)
            _log_parser(entry)
        if flaviar_health:
            entry = aggregate_health(flaviar.RETAILER, flaviar_health)
            health.append(entry)
            _log_parser(entry)
        # Blocked overseas retailers: probe once each, record the wall.
        probe_term = chosen[0] if chosen else "whisky"
        for module in (masterofmalt, winecom, totalwine):
            _, entry = await module.search(pw, probe_term, seen_at=stamp)
            health.append(entry)
            _log_parser(entry)
        notes.append("Master of Malt, Wine.com and Total Wine are "
                     "bot-wall blocked; overseas benchmarks come from "
                     "TWE, The Whisky World, ReserveBar, Astor Wines, "
                     "Loch Fyne Whiskies, The Whisky Barrel, "
                     "DramFellows, Wooden Cork, Mash & Grape and "
                     "Flaviar.")
    # --- Comparisons for every SG listing.
    groq_fallback = bool(os.environ.get("GROQ_API_KEY"))
    notes.append("Name-cleaning via Bedrock %s; Groq fallback %s."
                 % (BEDROCK_MODEL_ID,
                    "configured" if groq_fallback else "not configured"))
    llm_cache = name_cache if name_cache is not None else {}
    cache_before = len(llm_cache)
    cache_str_before = sum(
        1 for value in llm_cache.values() if isinstance(value, str))
    print("Starting comparisons: name-cleaning via Bedrock %s"
          % BEDROCK_MODEL_ID, flush=True)
    llm_used = 0
    groq_used = 0
    comparisons = []
    for item in listings:
        overseas = []
        if (item.in_stock and item.price_sgd >= MIN_HUNT_PRICE_SGD
                and item.size_ml is not None and item.size_verified):
            term = search_term(item.canonical)
            overseas, used, used_groq = match_overseas(
                item, prices_by_term.get(term, []), llm_cache,
                max_llm_calls - llm_used,
                max_groq_calls - groq_used)
            llm_used += used
            groq_used += used_groq
        result = compare(item, overseas, fx, codes=(), as_of=as_of)
        comparisons.append({
            "retailer": item.retailer,
            "canonical": item.canonical,
            "name": item.name,
            "traveller_only": bool(item.traveller_only),
            **result.to_dict(),
        })
    # The slice-7 run logged "376 LLM calls, 357 new cache entries"
    # while the persisted cache was empty: the old line counted
    # attempts, and every cleaning had failed silently (None).
    # Count successes too, so a silent Bedrock failure is visible
    # in the next run's log instead of hiding behind the call count.
    cleaned = sum(
        1 for value in llm_cache.values() if isinstance(value, str))
    print("Name-cleaning: %d LLM calls, %d succeeded, %d new cache "
          "entries (cache now holds %d)"
          % (llm_used, cleaned - cache_str_before,
             len(llm_cache) - cache_before, len(llm_cache)),
          flush=True)
    if llm_used >= max_llm_calls and max_llm_calls > 0:
        notes.append("LLM call budget (%d) exhausted; later ambiguous "
                     "matches stayed exact-only." % max_llm_calls)
    if groq_used >= max_groq_calls and max_groq_calls > 0:
        notes.append("Groq fallback budget (%d) exhausted; later "
                     "Bedrock failures stayed unmatched." % max_groq_calls)
    notes.append("Promo codes are not applied in this slice; effective "
                 "price is the shelf price.")
    assert len(comparisons) == len(listings), (
        "one comparison per listing required")
    return listings, health, comparisons, notes


def build_state(listings):
    """Current listings keyed by listing_key() for diffing."""
    assert isinstance(listings, list), "listings must be a list"
    state = {}
    for listing in listings:
        key = listing_key(listing)
        assert key not in state, "duplicate listing key in one run"
        state[key] = serialize_listing(listing)
    assert len(state) == len(listings), "state must hold every listing"
    return state


def main(argv=None):
    """Entry point. Returns 0 on success, 2 when FX could not be
    fixed, 3 when the S3 upload failed."""
    args = parse_args(list(sys.argv[1:] if argv is None else argv))
    assert args.out.endswith(".json") and args.state.endswith(".json")
    assert args.name_cache.endswith(".json")
    fx = resolve_fx(args)
    if fx is None:
        print("ERROR: FX rates unavailable and no --fx overrides given.",
              file=sys.stderr)
        return 2
    run_id = "hunt-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_at = datetime.now(timezone.utc).isoformat()
    assert run_id.startswith("hunt-") and run_at
    bucket = os.environ.get("S3_BUCKET") or ""
    assert isinstance(bucket, str), "S3_BUCKET must be a string"
    if bucket and not os.path.exists(args.state):
        # Fargate has no local baseline: pull last week's state so the
        # diff is meaningful. A missing object just means no baseline.
        if download_file(bucket, STATE_KEY, args.state):
            print("Baseline state downloaded from s3://%s/%s"
                  % (bucket, STATE_KEY))
    if bucket and not os.path.exists(args.name_cache):
        # Same for the name-cleaning cache: without it the run
        # re-cleans every bottle it already cleaned last week.
        if download_file(bucket, NAME_CACHE_KEY, args.name_cache):
            print("Name cache downloaded from s3://%s/%s"
                  % (bucket, NAME_CACHE_KEY))
    name_cache = load_state(args.name_cache) or {}
    name_cache = prune_name_cache(name_cache)
    listings, health, comparisons, notes = asyncio.run(
        run_once(fx, args.max_pages, args.paneco_url,
                 args.max_overseas_queries, args.max_llm_calls,
                 args.max_groq_calls, name_cache=name_cache))
    state = build_state(listings)
    previous = load_state(args.state) or {}
    diff = diff_states(previous, state)
    best_prices = best_price_per_bottle(listings, comparisons)
    report = build_report(run_id, run_at, fx, comparisons, health, diff,
                          notes, best_prices=best_prices)
    assert write_report(report, args.out), "report write failed"
    assert write_json_file(state, args.state), "state write failed"
    name_cache = prune_name_cache(name_cache)
    assert write_json_file(name_cache, args.name_cache), (
        "name cache write failed")
    if bucket:
        # The Fargate filesystem is ephemeral: the report, the new
        # baseline and the name cache must reach S3 before the task
        # stops. A lost name cache only costs re-cleaning next week,
        # so its upload failure warns instead of failing the run.
        report_ok, state_ok = upload_run(
            bucket, args.out, args.state, run_id)
        if report_ok and state_ok:
            print("Uploaded s3://%s/%s and s3://%s/%s"
                  % (bucket, report_key(run_id), bucket, STATE_KEY))
        else:
            print("ERROR: S3 upload failed (report=%s state=%s); "
                  "the run's output is lost with the container."
                  % (report_ok, state_ok), file=sys.stderr)
            return 3
        if upload_cache(bucket, args.name_cache):
            print("Uploaded s3://%s/%s" % (bucket, NAME_CACHE_KEY))
        else:
            print("WARNING: name cache upload failed; next week's run "
                  "re-cleans from scratch.", file=sys.stderr)
    qualifiers = [c for c in comparisons if c["verdict"] == "qualifier"]
    close_finds = [c for c in comparisons if c["verdict"] == "close-find"]
    print("SG: %d listings across %d retailers"
          % (len(listings), len({c["retailer"] for c in comparisons})),
          flush=True)
    print("Qualifiers: %d  Close finds: %d  Unverified: %d"
          % (len(qualifiers), len(close_finds),
             len([c for c in comparisons if c["verdict"] == "unverified"])),
          flush=True)
    print("Best price per bottle: %d bottles" % len(best_prices),
          flush=True)
    print("New: %d  Price changes: %d  Stock changes: %d  Delisted: %d"
          % (len(diff["new_listings"]), len(diff["price_changes"]),
             len(diff["stock_changes"]), len(diff["delistings"])),
          flush=True)
    for entry in health:
        print("  %s: %s (%s)" % (
            entry.retailer, entry.status, entry.detail), flush=True)
    assert report["schema_version"] == 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
