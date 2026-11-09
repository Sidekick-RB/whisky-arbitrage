"""Weekly hunt runner (slice 3: all SG retailers + overseas comparisons).

Runs every SG parser (Paneco, Cellarbration, TLC, Spirited, Tipsy
Topsy, Asher, PIVENE, LMDW, KrisShop, iShopChangi), dedupes by
retailer+canonical+mode, then compares every listing against verified
in-stock overseas prices (TWE, The Whisky World, ReserveBar) via
hunt/engine.py. Groq name-cleaning runs only when an exact canonical
match fails, and only while the call budget lasts. Blocked overseas
retailers (Master of Malt, Wine.com, Total Wine) and Malt & Wine Asia
are probed once per run and reported as blocked, never as prices.
iShopChangi is scraped in both modes (Non-Traveller and Traveller),
each in a fresh browser context; a bottle in both modes yields two
listings, one per mode. Promo codes are not applied in this slice
(codes=()); the effective price is the shelf price.
Never purchases, never adds to cart, never tests a promo code.
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone

from .engine import compare
from .fx import fetch_rates, fixed_rates
from .models import MIN_HUNT_PRICE_SGD, canonical_from_name, listing_key
from .names import clean_to_key
from .parsers import (
    asher,
    cellarbration,
    ishopchangi,
    krisshop,
    lmdw,
    maltwineasia,
    masterofmalt,
    paneco,
    pivene,
    reservebar,
    spirited,
    tipsy,
    tlc,
    totalwine,
    twe,
    whiskyworld,
    winecom,
)
from .parsers.base import MAX_PAGES
from .report import (
    ParserHealth,
    build_report,
    diff_states,
    load_state,
    serialize_listing,
    write_json_file,
    write_report,
)
from .s3upload import STATE_KEY, download_file, report_key, upload_run

DEFAULT_OUT = "hunt-report.json"
DEFAULT_STATE = "hunt-state.json"
MAX_OVERSEAS_QUERIES = 60
MAX_GROQ_CALLS = 100


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
    parser.add_argument("--max-groq-calls", type=int,
                        default=MAX_GROQ_CALLS,
                        help="Groq name-cleaning calls per run")
    args = parser.parse_args(argv)
    assert args.out.endswith(".json"), "--out must end in .json"
    assert args.state.endswith(".json"), "--state must end in .json"
    assert 1 <= args.max_pages <= MAX_PAGES, "--max-pages out of bounds"
    assert args.paneco_url is None or args.paneco_url.startswith("http")
    assert 1 <= args.max_overseas_queries <= 200, (
        "--max-overseas-queries out of bounds")
    assert 0 <= args.max_groq_calls <= 500, "--max-groq-calls out of bounds"
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


def match_overseas(listing, prices, groq_cache, groq_budget):
    """Overseas prices for one SG listing: exact canonical match
    first; Groq cleaning only when no exact match exists and budget
    remains. Returns (matched, groq_calls_used). A Groq failure
    (None) leaves the listing unmatched — never a guessed match."""
    assert listing.price_sgd > 0, "listing price must be positive"
    assert isinstance(prices, list)
    assert isinstance(groq_cache, dict)
    assert groq_budget >= 0, "Groq budget must be non-negative"
    exact = _match_exact(prices, listing.canonical)
    if exact:
        return exact, 0
    if groq_budget <= 0 or not prices:
        return [], 0
    calls = 0
    local_key = groq_cache.get(listing.name)
    if local_key is None:
        local_key = clean_to_key(listing.name)
        groq_cache[listing.name] = local_key
        calls += 1
    if local_key is None:
        return [], calls
    matched = []
    for price in prices:
        if not price.in_stock:
            continue
        overseas_key = groq_cache.get(price.name)
        if overseas_key is None:
            if calls >= groq_budget:
                break
            overseas_key = clean_to_key(price.name)
            groq_cache[price.name] = overseas_key
            calls += 1
        if overseas_key is not None and overseas_key == local_key:
            matched.append(price)
    assert calls <= groq_budget, "Groq budget exceeded"
    return matched, calls


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
                             (krisshop.scrape_whisky, krisshop.RETAILER)):
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


async def run_once(fx, max_pages, paneco_url=None,
                   max_overseas_queries=MAX_OVERSEAS_QUERIES,
                   max_groq_calls=MAX_GROQ_CALLS):
    """Run the full hunt. Returns (listings, health, comparisons, notes)."""
    assert fx is not None, "FX rates required"
    assert 1 <= max_pages <= MAX_PAGES, "max_pages out of bounds"
    assert 1 <= max_overseas_queries <= 200, "query cap out of bounds"
    assert 0 <= max_groq_calls <= 500, "Groq cap out of bounds"
    assert paneco_url is None or paneco_url.startswith("http")
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
        try:
            found, entry = await ishopchangi.scrape(pw, seen_at=stamp)
        except Exception as exc:
            found, entry = [], _error_health(ishopchangi.RETAILER, exc)
        listings.extend(found)
        health.append(entry)
        _log_parser(entry)
        # Blocked SG retailer: probe once, record the wall.
        try:
            _, entry = await maltwineasia.search(
                pw, "whisky", seen_at=stamp)
        except Exception as exc:
            entry = _error_health(maltwineasia.RETAILER, exc)
        health.append(entry)
        _log_parser(entry)
        notes.append("Malt & Wine Asia is bot-wall blocked; its three "
                     "watchlist bottles (Macallan 18 2020, Glen Scotia 25, "
                     "Glenfiddich 40) stay unverified.")
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
        ordered_terms = sorted(
            terms, key=lambda term: (-len(terms[term]), term))
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
        sitemap_urls = reservebar.load_sitemap()
        if sitemap_urls is None:
            rb_health.append(ParserHealth(
                retailer=reservebar.RETAILER, pages_scanned=0,
                items_found=0, status="error",
                detail="sitemap failed to load"))
            notes.append("ReserveBar sitemap failed to load; no US "
                         "ReserveBar benchmarks this run.")
        for term in chosen:
            found, entry = await twe.search(pw, term, seen_at=stamp)
            twe_health.append(entry)
            prices_by_term.setdefault(term, []).extend(found)
            found, entry = await whiskyworld.search(pw, term, seen_at=stamp)
            ww_health.append(entry)
            prices_by_term.setdefault(term, []).extend(found)
            if sitemap_urls is not None:
                found, entry = await reservebar.search(
                    term, sitemap_urls=sitemap_urls, seen_at=stamp)
                rb_health.append(entry)
                prices_by_term.setdefault(term, []).extend(found)
        if twe_health:
            entry = aggregate_health(twe.RETAILER, twe_health)
            health.append(entry)
            _log_parser(entry)
        if ww_health:
            entry = aggregate_health(whiskyworld.RETAILER, ww_health)
            health.append(entry)
            _log_parser(entry)
        if rb_health:
            entry = aggregate_health(reservebar.RETAILER, rb_health)
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
                     "TWE, The Whisky World and ReserveBar only.")
    # --- Comparisons for every SG listing.
    groq_available = bool(os.environ.get("GROQ_API_KEY"))
    if not groq_available:
        notes.append("GROQ_API_KEY is not set; ambiguous matches stay "
                     "unverified (exact canonical matches only).")
    groq_cache = {}
    groq_used = 0
    comparisons = []
    for item in listings:
        overseas = []
        if (item.in_stock and item.price_sgd >= MIN_HUNT_PRICE_SGD
                and item.size_ml is not None and item.size_verified):
            term = search_term(item.canonical)
            overseas, used = match_overseas(
                item, prices_by_term.get(term, []), groq_cache,
                max_groq_calls - groq_used)
            groq_used += used
        result = compare(item, overseas, fx, codes=(), as_of=as_of)
        comparisons.append({
            "retailer": item.retailer,
            "canonical": item.canonical,
            "name": item.name,
            **result.to_dict(),
        })
    if groq_used >= max_groq_calls and max_groq_calls > 0:
        notes.append("Groq call budget (%d) exhausted; later ambiguous "
                     "matches stayed exact-only." % max_groq_calls)
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
    listings, health, comparisons, notes = asyncio.run(
        run_once(fx, args.max_pages, args.paneco_url,
                 args.max_overseas_queries, args.max_groq_calls))
    state = build_state(listings)
    previous = load_state(args.state) or {}
    diff = diff_states(previous, state)
    report = build_report(run_id, run_at, fx, comparisons, health, diff,
                          notes)
    assert write_report(report, args.out), "report write failed"
    assert write_json_file(state, args.state), "state write failed"
    if bucket:
        # The Fargate filesystem is ephemeral: the report and the new
        # baseline must reach S3 before the task stops.
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
    qualifiers = [c for c in comparisons if c["verdict"] == "qualifier"]
    close_finds = [c for c in comparisons if c["verdict"] == "close-find"]
    print("SG: %d listings across %d retailers"
          % (len(listings), len({c["retailer"] for c in comparisons})),
          flush=True)
    print("Qualifiers: %d  Close finds: %d  Unverified: %d"
          % (len(qualifiers), len(close_finds),
             len([c for c in comparisons if c["verdict"] == "unverified"])),
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
