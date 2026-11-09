"""Weekly hunt runner (slice 1: Paneco only).

Runs the Paneco sale parser live, diffs the result against the previous
state file, and writes a JSON report plus the new state file. Overseas
comparisons stay empty until the overseas parsers land in the next slice;
the report notes say so explicitly. Never purchases, never adds to cart,
never tests a promo code.
"""

import argparse
import asyncio
import sys
from datetime import datetime, timezone

from .fx import fetch_rates, fixed_rates
from .models import listing_key
from .parsers.base import MAX_PAGES
from .parsers.paneco import RETAILER as PANECO
from .parsers.paneco import scrape_sale
from .report import (
    build_report,
    diff_states,
    load_state,
    serialize_listing,
    write_json_file,
    write_report,
)

DEFAULT_OUT = "hunt-report.json"
DEFAULT_STATE = "hunt-state.json"


def parse_args(argv):
    """CLI arguments for one weekly run."""
    assert isinstance(argv, list), "argv must be a list"
    parser = argparse.ArgumentParser(
        description="Whisky deal hunt: scrape Paneco, diff, report.")
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
    args = parser.parse_args(argv)
    assert args.out.endswith(".json"), "--out must end in .json"
    assert args.state.endswith(".json"), "--state must end in .json"
    assert 1 <= args.max_pages <= MAX_PAGES, "--max-pages out of bounds"
    assert args.paneco_url is None or args.paneco_url.startswith("http")
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


async def run_once(fx, max_pages, paneco_url=None):
    """Run the Paneco scrape. Returns (listings, health)."""
    assert fx is not None, "FX rates required"
    assert 1 <= max_pages <= MAX_PAGES, "max_pages out of bounds"
    assert paneco_url is None or paneco_url.startswith("http")
    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth

    stamp = datetime.now(timezone.utc).isoformat()
    assert stamp, "timestamp required"
    async with Stealth().use_async(async_playwright()) as pw:
        assert pw is not None, "playwright handle required"
        if paneco_url is not None:
            listings, health = await scrape_sale(
                pw, max_pages=max_pages, seen_at=stamp, sale_url=paneco_url)
        else:
            listings, health = await scrape_sale(
                pw, max_pages=max_pages, seen_at=stamp)
    assert health.retailer == PANECO, "health must name Paneco"
    assert len(listings) == health.items_found
    return listings, health


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
    """Entry point. Returns 0 on success, 2 when FX could not be fixed."""
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
    listings, health = asyncio.run(
        run_once(fx, args.max_pages, args.paneco_url))
    state = build_state(listings)
    previous = load_state(args.state) or {}
    diff = diff_states(previous, state)
    notes = ["slice 1: Paneco only; overseas comparisons not yet wired"]
    report = build_report(run_id, run_at, fx, [], [health], diff, notes)
    assert write_report(report, args.out), "report write failed"
    assert write_json_file(state, args.state), "state write failed"
    print("Paneco: %d listings, %d pages, status %s"
          % (len(listings), health.pages_scanned, health.status))
    print("New: %d  Price changes: %d  Stock changes: %d  Delisted: %d"
          % (len(diff["new_listings"]), len(diff["price_changes"]),
             len(diff["stock_changes"]), len(diff["delistings"])))
    assert report["schema_version"] == 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
