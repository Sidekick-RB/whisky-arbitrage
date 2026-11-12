"""Weekly report: JSON schema, state diffing, and file IO.

Report schema (version 1):
  run_id, run_at, fx {gbp_to_sgd, usd_to_sgd, source, as_of},
  comparisons [{retailer, canonical, name, verdict, ...}],
  price_changes [{key, retailer, canonical, old_price, new_price}],
  stock_changes [{key, retailer, canonical, was_in_stock, in_stock}],
  delistings [{key, retailer, canonical, last_price_sgd, last_seen}],
  excluded_by_rule [{key, retailer, canonical, last_price_sgd, last_seen}],
  new_listings [{key, retailer, canonical, price_sgd}],
  parser_health [{retailer, pages_scanned, items_found, status, detail}],
  notes [str]

State files are dicts keyed by listing_key() holding serialized listings.
A missing or corrupt previous state is not an error: the run simply has
no diff baseline.
"""

import json
import os
from dataclasses import dataclass

from .models import MIN_HUNT_PRICE_SGD, listing_key

REPORT_SCHEMA_VERSION = 1
# Raised 5,000 -> 10,000 on 10 Nov 2026: the slice-6 validation run
# crashed in diff_states before writing any report because the SG
# listing count passed 5,000 once the iShopChangi (1,112 listings)
# and Malt & Wine Asia fixes landed (~5,800 entries). The cap is
# still a fixed bound; a 10,000-entry state file is ~4 MB in S3.
MAX_STATE_ENTRIES = 10000
MAX_REPORT_NOTES = 50


@dataclass(frozen=True)
class ParserHealth:
    """One retailer's scrape health for a run. Zero items is a flag, never
    silently treated as "no deals". Statuses: ok | blocked | zero-items |
    partial | error."""

    retailer: str
    pages_scanned: int
    items_found: int
    status: str
    detail: str

    def __post_init__(self):
        assert self.retailer, "retailer must be non-empty"
        assert self.pages_scanned >= 0, "pages scanned must be >= 0"
        assert self.items_found >= 0, "items found must be >= 0"
        assert self.status in ("ok", "blocked", "zero-items", "partial",
                               "error"), "unknown health status"
        assert isinstance(self.detail, str)

    def to_dict(self):
        """JSON-safe form."""
        assert self.retailer, "retailer must be non-empty"
        result = {
            "retailer": self.retailer,
            "pages_scanned": self.pages_scanned,
            "items_found": self.items_found,
            "status": self.status,
            "detail": self.detail,
        }
        assert len(result) == 5, "health dict must carry five fields"
        return result


def _entry_key(entry):
    """State key for a serialized listing entry. Same format as
    listing_key(): retailer + canonical + fulfilment mode."""
    assert isinstance(entry, dict), "state entry must be a dict"
    assert entry.get("retailer"), "entry must carry a retailer"
    assert entry.get("canonical"), "entry must carry a canonical key"
    mode = "tr" if entry.get("traveller_only") else "nt"
    key = entry["retailer"] + "|" + entry["canonical"] + "|" + mode
    assert "|" in key
    return key


def _changes_for_key(key, prev, curr):
    """Price/stock changes for one listing present in both states.

    Returns (price_change_or_None, stock_change_or_None). Values are
    compared with !=, so a missing field counts as a change only when
    it differs from the other side.
    """
    assert isinstance(key, str) and key, "change key required"
    assert isinstance(prev, dict) and isinstance(curr, dict)
    assert prev.get("retailer") == curr.get("retailer"), "same retailer"
    price_change = None
    stock_change = None
    if curr.get("price_sgd") != prev.get("price_sgd"):
        price_change = {
            "key": key,
            "retailer": curr.get("retailer", ""),
            "canonical": curr.get("canonical", ""),
            "old_price_sgd": prev.get("price_sgd"),
            "new_price_sgd": curr.get("price_sgd"),
        }
    if curr.get("in_stock") != prev.get("in_stock"):
        stock_change = {
            "key": key,
            "retailer": curr.get("retailer", ""),
            "canonical": curr.get("canonical", ""),
            "was_in_stock": prev.get("in_stock"),
            "in_stock": curr.get("in_stock"),
        }
    assert price_change is None or "new_price_sgd" in price_change
    assert stock_change is None or "in_stock" in stock_change
    return price_change, stock_change


def diff_states(previous, current, is_excluded=None):
    """Diff two state dicts. Returns price/stock changes, delistings, news.

    previous/current map listing_key -> serialized listing dict with at
    least price_sgd, in_stock and seen_at. Keys present before but absent
    now are delistings (e.g. Glenfarclas 21, 9 Nov 2026), UNLESS
    is_excluded is given and returns True for that canonical: those go
    into a separate "excluded_by_rule" section, so a filter change does
    not read as a mass delisting. The extra section only appears when
    is_excluded is passed, so the two-argument call keeps its exact
    four-section shape.
    """
    assert isinstance(previous, dict), "previous state must be a dict"
    assert isinstance(current, dict), "current state must be a dict"
    assert len(previous) <= MAX_STATE_ENTRIES
    assert len(current) <= MAX_STATE_ENTRIES
    assert is_excluded is None or callable(is_excluded), (
        "is_excluded must be a callable or None")
    price_changes = []
    stock_changes = []
    delistings = []
    excluded_by_rule = []
    new_listings = []
    for key in sorted(previous):
        prev = previous[key]
        assert isinstance(prev, dict), "previous entry must be a dict"
        if key not in current:
            entry = {
                "key": key,
                "retailer": prev.get("retailer", ""),
                "canonical": prev.get("canonical", ""),
                "last_price_sgd": prev.get("price_sgd"),
                "last_seen": prev.get("seen_at", ""),
            }
            if is_excluded is not None and is_excluded(
                    prev.get("canonical", "")):
                excluded_by_rule.append(entry)
            else:
                delistings.append(entry)
            continue
        curr = current[key]
        assert isinstance(curr, dict), "current entry must be a dict"
        price_change, stock_change = _changes_for_key(key, prev, curr)
        if price_change is not None:
            price_changes.append(price_change)
        if stock_change is not None:
            stock_changes.append(stock_change)
    for key in sorted(current):
        if key not in previous:
            curr = current[key]
            new_listings.append({
                "key": key,
                "retailer": curr.get("retailer", ""),
                "canonical": curr.get("canonical", ""),
                "price_sgd": curr.get("price_sgd"),
            })
    result = {
        "price_changes": price_changes,
        "stock_changes": stock_changes,
        "delistings": delistings,
        "new_listings": new_listings,
    }
    if is_excluded is not None:
        result["excluded_by_rule"] = excluded_by_rule
    assert len(result) in (4, 5), "diff must carry four or five sections"
    return result


def best_price_per_bottle(listings, comparisons):
    """One row per canonical bottle: its cheapest in-scope listing.

    In scope is the hunt's scope: in stock, S$100 or more, verified
    size. Traveller and non-traveller modes of the same bottle at
    one retailer are separate listings and both compete, so the
    cheapest mode wins on its own price. The row carries the winning
    listing's verdict and saving, so the rollup reads as "cheapest
    price, and whether it is a deal". Rows sort by saving (best
    first, unverified last), then canonical. One row per canonical,
    never more.
    """
    assert isinstance(listings, list), "listings must be a list"
    assert isinstance(comparisons, list), "comparisons must be a list"
    verdicts = {}
    for entry in comparisons:
        assert entry.get("retailer"), "comparison must name a retailer"
        assert entry.get("canonical"), "comparison must name a bottle"
        key = (entry["retailer"], entry["canonical"],
               bool(entry.get("traveller_only", False)))
        verdicts[key] = (entry.get("verdict"),
                         entry.get("saving_pct"))
    best = {}
    for listing in listings:
        assert listing.price_sgd > 0, "listing price must be positive"
        if not listing.in_stock:
            continue
        if listing.price_sgd < MIN_HUNT_PRICE_SGD:
            continue
        if listing.size_ml is None or not listing.size_verified:
            continue
        key = (listing.retailer, listing.canonical,
               bool(listing.traveller_only))
        verdict, saving = verdicts.get(key, (None, None))
        row = {
            "canonical": listing.canonical,
            "price_sgd": listing.price_sgd,
            "retailer": listing.retailer,
            "traveller_only": bool(listing.traveller_only),
            "url": listing.url,
            "verdict": verdict,
            "saving_pct": saving,
        }
        current = best.get(listing.canonical)
        if current is None or (row["price_sgd"], row["retailer"],
                               row["traveller_only"]) < (
                current["price_sgd"], current["retailer"],
                current["traveller_only"]):
            best[listing.canonical] = row
    rows = list(best.values())
    assert len(rows) == len(best), "one row per bottle required"
    assert all(row["price_sgd"] >= MIN_HUNT_PRICE_SGD for row in rows)
    rows.sort(key=lambda row: (
        row["saving_pct"] is None,
        -(row["saving_pct"] or 0.0),
        row["canonical"]))
    return rows


def build_report(run_id, run_at, fx, comparisons, health, diff, notes,
                 best_prices=None):
    """Assemble the weekly report dict (schema version 1).

    best_prices is the optional per-bottle cheapest-row rollup from
    best_price_per_bottle(); an additive field, so the schema
    version stays 1 and older readers ignore it.
    """
    assert run_id, "run_id must be non-empty"
    assert run_at, "run_at must be non-empty"
    assert fx.gbp_to_sgd > 0 and fx.usd_to_sgd > 0
    assert isinstance(comparisons, list), "comparisons must be a list"
    assert isinstance(health, list), "health must be a list"
    assert isinstance(diff, dict), "diff must be a dict"
    assert len(notes) <= MAX_REPORT_NOTES, "too many report notes"
    assert best_prices is None or isinstance(best_prices, list), (
        "best prices must be a list or None")
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "run_id": run_id,
        "run_at": run_at,
        "fx": {
            "gbp_to_sgd": fx.gbp_to_sgd,
            "usd_to_sgd": fx.usd_to_sgd,
            "source": fx.source,
            "as_of": fx.as_of,
        },
        "comparisons": comparisons,
        "best_prices": list(best_prices) if best_prices else [],
        "price_changes": diff.get("price_changes", []),
        "stock_changes": diff.get("stock_changes", []),
        "delistings": diff.get("delistings", []),
        "excluded_by_rule": diff.get("excluded_by_rule", []),
        "new_listings": diff.get("new_listings", []),
        "parser_health": [entry.to_dict() for entry in health],
        "notes": list(notes),
    }
    assert report["schema_version"] == REPORT_SCHEMA_VERSION
    assert report["fx"]["gbp_to_sgd"] > 0
    return report


def write_json_file(data, path):
    """Write a dict as indented JSON. Returns True; verifies the file."""
    assert isinstance(data, dict), "data must be a dict"
    assert path.endswith(".json"), "path must end in .json"
    text = json.dumps(data, indent=2, sort_keys=True)
    assert text, "serialized JSON must be non-empty"
    with open(path, "w", encoding="utf-8") as handle:
        written = handle.write(text)
    assert written == len(text), "short write on JSON file"
    assert os.path.exists(path), "file must exist after write"
    assert os.path.getsize(path) > 0, "file must be non-empty"
    return True


def write_report(report, path):
    """Write the report as JSON. Returns True; verifies the file landed."""
    assert isinstance(report, dict), "report must be a dict"
    assert path.endswith(".json"), "report path must end in .json"
    assert report.get("schema_version") == REPORT_SCHEMA_VERSION
    assert write_json_file(report, path), "report write failed"
    return True


def load_state(path):
    """Load a previous state file. Returns a dict, or None when the file
    is missing or unparseable (a run without a baseline is still valid)."""
    assert isinstance(path, str) and path, "state path required"
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    assert isinstance(data, dict) or data is None
    if not isinstance(data, dict):
        return None
    assert len(data) <= MAX_STATE_ENTRIES, "state file too large"
    return data


def serialize_listing(listing):
    """One Listing as a JSON-safe dict for state files."""
    assert listing.retailer, "retailer must be non-empty"
    assert listing.canonical, "canonical must be non-empty"
    result = {
        "retailer": listing.retailer,
        "name": listing.name,
        "canonical": listing.canonical,
        "size_ml": listing.size_ml,
        "size_verified": listing.size_verified,
        "price_sgd": listing.price_sgd,
        "in_stock": listing.in_stock,
        "traveller_only": listing.traveller_only,
        "eligibility": listing.eligibility,
        "edition": listing.edition,
        "batch": listing.batch,
        "url": listing.url,
        "seen_at": listing.seen_at,
    }
    assert _entry_key(result) == listing_key(listing)
    return result
