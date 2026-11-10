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
    _scrape_sg_urllib,
    aggregate_health,
    dedupe_listings,
    match_overseas,
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
        matched, calls = match_overseas(listing, prices, {}, 10)
        assert len(matched) == 1
        assert matched[0].price == 137.95
        assert calls == 0, "exact match must not spend Groq calls"
        self.assertEqual(len(matched), 1)

    def test_no_match_no_budget(self):
        listing = _listing("TORMORE 16 ans", "tormore-16-ans")
        prices = [_overseas("Tormore 16 Year Old 70cl", 120.00)]
        matched, calls = match_overseas(listing, prices, {}, 0)
        assert matched == []
        assert calls == 0
        self.assertEqual(matched, [])

    def test_no_prices(self):
        listing = _listing("Test Bottle 700ml", "test-bottle")
        matched, calls = match_overseas(listing, [], {}, 10)
        assert matched == []
        assert calls == 0
        self.assertEqual(matched, [])


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

        def stub_clean(name):
            assert name, "clean_to_key must get a non-empty name"
            if "Jack Daniel's 10" in name:
                return "jack-daniels-10yr"
            return None

        run_module.clean_to_key = stub_clean
        try:
            matched, calls = match_overseas(listing, prices, {}, 10)
        finally:
            run_module.clean_to_key = real_clean
        assert len(matched) == 1
        assert matched[0].price == 115.00
        assert calls == 3, (
            "1 call for the listing name + 1 per in-stock overseas "
            "name; the out-of-stock Glenfarclas 25 is skipped")
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
        matched, _ = match_overseas(michter[0], prices, {}, 10)
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
