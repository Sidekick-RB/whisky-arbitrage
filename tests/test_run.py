"""Unit tests for the run.py wiring: search terms, deduping, health
aggregation, and overseas matching (exact first, Groq only on miss).
The live run (browser + network) is not unit-tested here; the
fixture-server pattern in test_paneco_live.py covers the driver path.
"""

import asyncio
import contextlib
import io
import json
import os
import unittest

from hunt.engine import compare
from hunt.fx import fixed_rates
from hunt.models import Listing, OverseasPrice
from hunt.parsers.tlc import parse_feed_json as tlc_parse
from hunt.parsers.twe import parse_search_html as twe_parse
from hunt.report import ParserHealth
from hunt.run import (
    MAX_NAME_CACHE_ENTRIES,
    _order_terms,
    _record_timeout,
    _scrape_sg_urllib,
    _search_bounded,
    _twe_skip_after,
    aggregate_health,
    dedupe_listings,
    match_overseas,
    prune_name_cache,
    search_term,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
SEEN_AT = "2026-11-09T00:00:00+00:00"
FX_9NOV = fixed_rates(1.714, 1.282, "test-9nov", "2026-11-09")


def _listing(name, canonical, price=150.0, size_ml=700):
    assert name and canonical and price > 0
    return Listing(
        retailer="Paneco", name=name, canonical=canonical,
        size_ml=size_ml, size_verified=size_ml is not None,
        price_sgd=price, in_stock=True, traveller_only=False,
        eligibility="Singapore delivery", edition="", batch="",
        url="https://example.com/p/1",
        seen_at="2026-11-09T00:00:00+00:00")


def _overseas(name, price, size_ml=700, retailer="The Whisky Exchange"):
    assert name and price > 0 and size_ml > 0
    return OverseasPrice(
        retailer=retailer, country="UK", currency="GBP", price=price,
        size_ml=size_ml, in_stock=True,
        url="https://example.com/p/2", name=name)


def _health(retailer, status, pages=1, items=1, detail=""):
    assert retailer and status
    return ParserHealth(
        retailer=retailer, pages_scanned=pages, items_found=items,
        status=status, detail=detail or status)


class SearchTermTest(unittest.TestCase):
    def test_age_and_edition(self):
        assert search_term(
            "royal-brackla-18yr-palo-cortado-sherry-cask-finish") == (
            "royal brackla 18")
        assert search_term("redbreast-21yr") == "redbreast 21"
        assert search_term("tormore-16-ans") == "tormore 16"
        self.assertEqual(search_term("redbreast-21yr"), "redbreast 21")

    def test_nas_uses_full_name(self):
        assert search_term("johnnie-walker-blue-label") == (
            "johnnie walker blue label")
        assert search_term("chivas-regal-ultis") == "chivas regal ultis"
        self.assertEqual(
            search_term("johnnie-walker-blue-label"),
            "johnnie walker blue label")


class DedupeTest(unittest.TestCase):
    def test_duplicates_dropped(self):
        first = _listing("Test Bottle 700ml", "test-bottle")
        second = _listing("Test Bottle 700ml", "test-bottle", price=160.0)
        other = _listing("Other Bottle 700ml", "other-bottle")
        unique, duplicates = dedupe_listings([first, second, other])
        assert len(unique) == 2
        assert duplicates == 1
        assert unique[0].price_sgd == 150.0, "first listing wins"
        self.assertEqual(duplicates, 1)


class AggregateHealthTest(unittest.TestCase):
    def test_worst_status_wins(self):
        healths = [
            _health("The Whisky Exchange", "ok", 1, 3),
            _health("The Whisky Exchange", "blocked", 0, 0, "cloudflare"),
            _health("The Whisky Exchange", "ok", 1, 2),
        ]
        result = aggregate_health("The Whisky Exchange", healths)
        assert result.status == "blocked"
        assert result.pages_scanned == 2
        assert result.items_found == 5
        assert "cloudflare" in result.detail
        self.assertEqual(result.status, "blocked")

    def test_all_ok(self):
        healths = [_health("ReserveBar", "ok", 1, 2),
                   _health("ReserveBar", "ok", 1, 1)]
        result = aggregate_health("ReserveBar", healths)
        assert result.status == "ok"
        assert result.items_found == 3
        self.assertEqual(result.status, "ok")


class TweSkipTest(unittest.TestCase):
    def test_blocked_probe_skips_the_rest(self):
        """10 Nov 2026: the slice-8 run stalled 85 minutes in the
        101-125 overseas batch on TWE browser searches while the
        site throttled the container. A blocked probe ends TWE
        for the run."""
        entry = _health("The Whisky Exchange", "blocked", 0, 0,
                        "no TWE search page loaded (blocked at page 1)")
        assert _twe_skip_after(entry) is True
        self.assertTrue(_twe_skip_after(entry))

    def test_other_statuses_keep_searching(self):
        for status in ("ok", "zero-items", "partial", "error"):
            entry = _health("The Whisky Exchange", status, 1, 2)
            assert _twe_skip_after(entry) is False, status
        self.assertFalse(_twe_skip_after(
            _health("The Whisky Exchange", "ok", 1, 2)))

    def test_zero_items_on_probe_skips_too(self):
        """A zero-items probe means TWE is not yielding this run:
        the probe term is the most-listed bottle, so zero cards
        for it is a soft block, not an absent bottle."""
        entry = _health("The Whisky Exchange", "zero-items", 1, 0)
        assert _twe_skip_after(entry, probe=True) is True
        self.assertTrue(_twe_skip_after(entry, probe=True))

    def test_zero_items_off_probe_keeps_searching(self):
        """A later term may genuinely be absent from TWE; only
        the probe may end the run on zero-items."""
        entry = _health("The Whisky Exchange", "zero-items", 1, 0)
        assert _twe_skip_after(entry, probe=False) is False
        self.assertFalse(_twe_skip_after(entry, probe=False))


async def _fast_search():
    return [_overseas("Glenlivet 18", 149.99)], _health(
        "Astor Wines", "ok", 1, 1)


async def _slow_search():
    await asyncio.sleep(0.5)
    return [], _health("Astor Wines", "ok", 0, 0)


async def _failing_search():
    raise ValueError("parser crashed")


class SearchBoundedTest(unittest.TestCase):
    def test_fast_search_passes_through(self):
        found, entry, timed_out = asyncio.run(_search_bounded(
            _fast_search(), "Astor Wines", timeout_s=5))
        assert len(found) == 1 and entry.status == "ok"
        assert timed_out is False
        self.assertFalse(timed_out)

    def test_slow_search_times_out(self):
        """10 Nov 2026: slice 10 stalled 68 minutes in the 26-50
        overseas batch on a throttled search with no bound."""
        found, entry, timed_out = asyncio.run(_search_bounded(
            _slow_search(), "Astor Wines", timeout_s=0.05))
        assert found == [] and entry.status == "error"
        assert "timed out" in entry.detail
        assert timed_out is True
        self.assertTrue(timed_out)

    def test_other_errors_still_propagate(self):
        """Only the timeout is converted; a parser crash must
        stay loud so the run fails visibly."""
        with self.assertRaises(ValueError):
            asyncio.run(_search_bounded(
                _failing_search(), "Astor Wines", timeout_s=5))


class RecordTimeoutTest(unittest.TestCase):
    def test_two_timeouts_skip_the_retailer(self):
        timeouts = {}
        skipped = set()
        assert _record_timeout("Astor Wines", True, timeouts,
                               skipped) is False
        assert _record_timeout("Astor Wines", True, timeouts,
                               skipped) is True
        assert "Astor Wines" in skipped
        self.assertIn("Astor Wines", skipped)

    def test_no_timeout_records_nothing(self):
        timeouts = {}
        skipped = set()
        assert _record_timeout("Astor Wines", False, timeouts,
                               skipped) is False
        assert timeouts == {} and skipped == set()
        self.assertEqual(timeouts, {})


class MatchOverseasTest(unittest.TestCase):
    def test_exact_match_first(self):
        listing = _listing(
            "Royal Brackla 18 Year Old Palo Cortado Sherry Cask Finish 70cl",
            "royal-brackla-18yr-palo-cortado-sherry-cask-finish")
        prices = [
            _overseas("Royal Brackla 18 Year Old Palo Cortado Sherry Cask "
                      "Finish 70cl", 137.95),
            _overseas("Glenfarclas 25 Year Old 70cl", 225.00),
        ]
        matched, calls, groq = match_overseas(listing, prices, {}, 10)
        assert len(matched) == 1
        assert matched[0].price == 137.95
        assert calls == 0, "exact match must not spend Groq calls"
        self.assertEqual(len(matched), 1)

    def test_no_match_no_budget(self):
        listing = _listing("TORMORE 16 ans", "tormore-16-ans")
        prices = [_overseas("Tormore 16 Year Old 70cl", 120.00)]
        matched, calls, groq = match_overseas(listing, prices, {}, 0)
        assert matched == []
        assert calls == 0
        self.assertEqual(matched, [])

    def test_no_prices(self):
        listing = _listing("Test Bottle 700ml", "test-bottle")
        matched, calls, groq = match_overseas(listing, [], {}, 10)
        assert matched == []
        assert calls == 0
        self.assertEqual(matched, [])

    def test_prefilled_cache_spends_no_calls(self):
        # The persistent name cache: both names were cleaned in an
        # earlier run, so no LLM call may happen at all — clean_to_key
        # is replaced with a raiser to prove it.
        listing = _listing("Jack Daniel's 10 Year Old 70cl",
                           "jack-daniels-10yr")
        prices = [_overseas(
            "Jack Daniel's 10 Year Old Batch 5 70cl", 115.00)]
        cache = {
            "Jack Daniel's 10 Year Old 70cl": "jack-daniels-10yr",
            "Jack Daniel's 10 Year Old Batch 5 70cl": "jack-daniels-10yr",
        }
        import hunt.run as run_module
        real_clean = run_module.clean_to_key

        def _raise(name):
            raise AssertionError("LLM must not be called for cached names")

        run_module.clean_to_key = _raise
        try:
            matched, calls, groq = match_overseas(listing, prices, cache, 10)
        finally:
            run_module.clean_to_key = real_clean
        assert len(matched) == 1
        assert calls == 0
        assert groq == 0, "cached names must not spend Groq calls"
        self.assertEqual(len(matched), 1)

    def test_groq_budget_caps_fallback(self):
        # The stub always reports Groq use; with a groq_budget of 1
        # only the first cleaning may use Groq — later cleanings run
        # Bedrock-only and stay unmatched when Bedrock is down.
        listing = _listing("Test Bottle 18 Year Old 70cl",
                           "test-bottle-18yr")
        prices = [_overseas("Test Bottle 18 Yr. Old 70cl", 120.00),
                  _overseas("Other Bottle 18 Yr. Old 70cl", 130.00)]
        seen = []
        import hunt.run as run_module
        real_clean = run_module.clean_to_key

        def stub_clean(name, allow_groq=True):
            seen.append(allow_groq)
            if not allow_groq:
                return None, False
            return "test-bottle-18yr", True

        run_module.clean_to_key = stub_clean
        try:
            matched, calls, groq = match_overseas(
                listing, prices, {}, 10, groq_budget=1)
        finally:
            run_module.clean_to_key = real_clean
        assert groq == 1, "only one Groq fallback call allowed"
        assert seen == [True, False, False], (
            "Groq disabled after the budget is spent")
        assert calls == 3, "all three names still attempted via Bedrock"
        self.assertEqual(groq, 1)


class OrderTermsTest(unittest.TestCase):
    def test_most_expensive_first(self):
        terms = {
            "common bottle": [
                _listing("Common 70cl", "common", price=120.0),
                _listing("Common 70cl", "common", price=120.0)],
            "rare bottle": [_listing("Rare 70cl", "rare", price=1500.0)],
            "mid bottle": [_listing("Mid 70cl", "mid", price=300.0)],
        }
        ordered = _order_terms(terms)
        assert ordered == ["rare bottle", "mid bottle", "common bottle"]
        self.assertEqual(ordered[0], "rare bottle")

    def test_ties_break_by_count_then_name(self):
        terms = {
            "b bottle": [_listing("B 70cl", "b", price=200.0)],
            "a bottle": [_listing("A 70cl", "a", price=200.0),
                         _listing("A 70cl", "a", price=200.0)],
            "c bottle": [_listing("C 70cl", "c", price=200.0)],
        }
        ordered = _order_terms(terms)
        assert ordered == ["a bottle", "b bottle", "c bottle"]
        self.assertEqual(ordered, ["a bottle", "b bottle", "c bottle"])


class PruneCacheTest(unittest.TestCase):
    def test_failures_are_never_persisted(self):
        cache = {
            "Royal Brackla 18": "royal-brackla-18yr",
            "Uncleanable Bottle": None,
            "Bad Key": 42,
        }
        pruned = prune_name_cache(cache)
        assert pruned == {"Royal Brackla 18": "royal-brackla-18yr"}
        self.assertEqual(len(pruned), 1)

    def test_cap_keeps_newest(self):
        cache = {"bottle-%04d" % i: "key-%04d" % i
                 for i in range(MAX_NAME_CACHE_ENTRIES + 10)}
        pruned = prune_name_cache(cache)
        assert len(pruned) == MAX_NAME_CACHE_ENTRIES
        assert "bottle-0000" not in pruned
        assert "bottle-%04d" % (MAX_NAME_CACHE_ENTRIES + 9) in pruned
        self.assertEqual(len(pruned), MAX_NAME_CACHE_ENTRIES)


class PipelineTest(unittest.TestCase):
    """TLC listing + TWE benchmark through search_term, match and the
    engine — the exact path run_once takes, with fixtures only."""

    def _load_json(self, name):
        path = os.path.join(FIXTURES, name)
        assert os.path.exists(path), "fixture missing: " + path
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def _load_text(self, name):
        path = os.path.join(FIXTURES, name)
        assert os.path.exists(path), "fixture missing: " + path
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()

    def test_jd10_tlc_is_close_find(self):
        listings, _, _, _ = tlc_parse(self._load_json("tlc-products.json"),
                                      SEEN_AT)
        prices, _, _ = twe_parse(
            self._load_text("twe-search-glenfarclas.html"),
            "https://www.thewhiskyexchange.com/search?q=glenfarclas",
            SEEN_AT)
        jd10 = [item for item in listings
                if "Jack Daniel's 10" in item.name]
        assert len(jd10) == 1, "one JD 10 in the TLC fixture"
        listing = jd10[0]
        assert listing.price_sgd == 152.00
        term = search_term(listing.canonical)
        assert term == "jack daniels 10"
        # The TWE name carries "Batch 5", so the exact canonicals
        # differ (jack-daniels-10yr vs jack-daniels-10yr-batch-5).
        # Groq name-cleaning is what bridges that gap; the live API
        # contract is tested in test_names.py, so here a stub stands
        # in for clean_to_key and proves the call is made and used.
        import hunt.run as run_module
        real_clean = run_module.clean_to_key

        def stub_clean(name, allow_groq=True):
            assert name, "clean_to_key must get a non-empty name"
            if "Jack Daniel's 10" in name:
                return "jack-daniels-10yr", False
            return None, False

        run_module.clean_to_key = stub_clean
        try:
            matched, calls, groq = match_overseas(listing, prices, {}, 10)
        finally:
            run_module.clean_to_key = real_clean
        assert len(matched) == 1
        assert matched[0].price == 115.00
        assert calls == 3, (
            "1 call for the listing name + 1 per in-stock overseas "
            "name; the out-of-stock Glenfarclas 25 is skipped")
        assert groq == 0, "stub never uses the Groq fallback"
        result = compare(listing, matched, FX_9NOV, codes=(),
                         as_of="2026-11-09")
        assert result.verdict == "close-find"
        assert 22.0 < result.saving_pct < 24.0
        self.assertEqual(result.verdict, "close-find")

    def test_no_benchmark_is_unverified(self):
        listings, _, _, _ = tlc_parse(self._load_json("tlc-products.json"),
                                      SEEN_AT)
        prices, _, _ = twe_parse(
            self._load_text("twe-search-glenfarclas.html"),
            "https://www.thewhiskyexchange.com/search?q=glenfarclas",
            SEEN_AT)
        michter = [item for item in listings
                   if "Michter's" in item.name]
        assert len(michter) == 1
        matched, _, _ = match_overseas(michter[0], prices, {}, 10)
        assert matched == []
        result = compare(michter[0], matched, FX_9NOV, codes=(),
                         as_of="2026-11-09")
        assert result.verdict == "unverified"
        self.assertEqual(result.verdict, "unverified")


class ScrapeIsolationTest(unittest.TestCase):
    def test_crashing_parser_recorded_not_fatal(self):
        # 9 Nov 2026: a Spirited zero-price variant crashed the whole
        # first Fargate run. One parser raising must now be recorded
        # as an error entry while the other parsers still run.
        from hunt.parsers import (
            asher, ishopchangi, krisshop, lmdw, maltwineasia, pivene,
            spirited, tipsy, tlc)

        def _ok(retailer):
            async def _scrape(seen_at=""):
                assert seen_at
                return [], ParserHealth(
                    retailer=retailer, pages_scanned=1, items_found=0,
                    status="ok", detail="ok")
            return _scrape

        async def _boom(seen_at=""):
            assert seen_at
            raise RuntimeError("boom")

        saved = [
            (tlc, "scrape", tlc.scrape),
            (spirited, "scrape", spirited.scrape),
            (tipsy, "scrape", tipsy.scrape),
            (asher, "scrape", asher.scrape),
            (pivene, "scrape", pivene.scrape),
            (lmdw, "scrape", lmdw.scrape),
            (maltwineasia, "scrape", maltwineasia.scrape),
            (krisshop, "scrape_whisky", krisshop.scrape_whisky),
            (ishopchangi, "scrape", ishopchangi.scrape),
        ]
        tlc.scrape = _ok(tlc.RETAILER)
        spirited.scrape = _boom
        tipsy.scrape = _ok(tipsy.RETAILER)
        asher.scrape = _ok(asher.RETAILER)
        pivene.scrape = _ok(pivene.RETAILER)
        lmdw.scrape = _ok(lmdw.RETAILER)
        maltwineasia.scrape = _ok(maltwineasia.RETAILER)
        krisshop.scrape_whisky = _ok(krisshop.RETAILER)
        ishopchangi.scrape = _ok(ishopchangi.RETAILER)
        try:
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                listings, health = asyncio.run(_scrape_sg_urllib(SEEN_AT))
        finally:
            for module, name, fn in saved:
                setattr(module, name, fn)
        assert listings == []
        assert len(health) == 9
        by_retailer = {entry.retailer: entry for entry in health}
        assert by_retailer["Spirited"].status == "error"
        assert "RuntimeError" in by_retailer["Spirited"].detail
        assert by_retailer["Spirited"].items_found == 0
        for retailer in ("The Liquid Collection", "Tipsy Topsy", "Asher",
                         "PIVENE", "LMDW", "Malt & Wine Asia", "KrisShop",
                         "iShopChangi"):
            assert by_retailer[retailer].status == "ok"
        # Progress logging: one flushed line per parser, so a 15-minute
        # CloudWatch check can see where a run is — including the
        # crashed one.
        lines = captured.getvalue().strip().split("\n")
        assert len(lines) == 9
        assert any(line.startswith("Spirited: 0 listings, error")
                   for line in lines)
        assert any(line.startswith("The Liquid Collection: 0 listings, ok")
                   for line in lines)
        self.assertEqual(len(health), 9)
        self.assertEqual(by_retailer["Spirited"].status, "error")
        self.assertEqual(len(lines), 9)


if __name__ == "__main__":
    unittest.main()
