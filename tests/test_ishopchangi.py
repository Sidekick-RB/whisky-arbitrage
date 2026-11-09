"""iShopChangi parser tests.

Fixtures: tests/fixtures/ishopchangi-search-{nt,tr}.html — SYNTHETIC
HTML built from the verbatim rendered records in
~/workspace/bottles/ishopchangi-capture-2026-11-09.md (Brackla 18 TR
S$165/was S$218, Brackla 12 TR S$100 / NT S$144, Brackla 21 TR S$330 /
NT S$532, Goalong Vigorous S$87, Goalong Brandy Cask From S$127, an
out-of-stock tile). Only the Brackla 18 product code (mp00519659) is
verbatim; the other mp codes are fixture filler, and the Glenallachie
17YO S$240.00 price is filler (only its Out of Stock badge is real).
The live markup is UNVALIDATED — the extractor works structurally on
product links and price text, not on class names — so the first
Fargate run validates it live.

Two levels, like test_paneco_live.py:
- RecordTest + LocalServerTest (always run): pure record parsing, and
  the real Playwright driver against the fixtures over a local HTTP
  server. Read-only.
- LiveTest (skipped unless HUNT_LIVE=1): hits the real iShopChangi
  search. Needs direct internet.
"""

import asyncio
import functools
import http.server
import os
import threading
import unittest

from playwright.async_api import async_playwright
from playwright_stealth import Stealth

from hunt.models import listing_key
from hunt.parsers.ishopchangi import (
    parse_tile_records,
    scrape,
    scrape_mode,
    search_url,
)

LIVE = os.environ.get("HUNT_LIVE") == "1"
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
SEEN_AT = "2026-11-09T00:00:00+00:00"


def _record(code, name, prices, href=None, out_of_stock=False):
    """One tile record as page.evaluate returns it."""
    assert code and name and isinstance(prices, list)
    return {
        "name": name,
        "href": href or ("/en/product/x-" + code
                         + ".html?p_collection=X&cmode=nt"),
        "code": code,
        "prices": prices,
        "tileText": name + " S$0.00",
        "outOfStock": out_of_stock,
    }


def _price(amount, struck=False):
    assert amount, "amount required"
    return {"amount": amount, "struck": struck}


class UrlTest(unittest.TestCase):
    def test_search_urls_per_mode(self):
        nt = search_url("nt", "whisky")
        assert nt.startswith(
            "https://www.ishopchangi.com/en/search?query=whisky")
        assert "cmode=nt" in nt
        assert "HOMEDELIVERYNONTRAVELLER" in nt
        assert "&page=" not in nt
        tr = search_url("tr", "whisky", 2)
        assert "cmode=tr" in tr
        assert "HOMEDELIVERYTRAVELLER,ARRIVAL,DEPARTURE" in tr
        assert tr.endswith("&page=2")
        self.assertIn("cmode=nt", nt)
        self.assertIn("cmode=tr", tr)


class RecordTest(unittest.TestCase):
    def test_brackla_18_traveller(self):
        records = [_record(
            "mp00519659",
            "ROYAL BRACKLA 18 Year Old Single Malt Scotch Whisky 700ML 46%",
            [_price("218.00", struck=True), _price("165.00")])]
        listings, seen, skipped = parse_tile_records(
            records, "tr", SEEN_AT)
        assert len(listings) == 1 and seen == 1 and skipped == 0
        item = listings[0]
        assert item.price_sgd == 165.00
        assert item.size_ml == 700
        assert item.size_verified is True
        assert item.traveller_only is True
        assert item.eligibility == "Pre-order to flight"
        assert item.canonical == "royal-brackla-18yr"
        assert "mp00519659" in item.url
        self.assertEqual(item.price_sgd, 165.00)
        self.assertTrue(item.traveller_only)

    def test_brackla_12_two_modes_two_listings(self):
        nt_records = [_record(
            "mp00519660",
            "ROYAL BRACKLA 12 Year Old Single Malt Scotch Whisky 1000ML 46%",
            [_price("166.00", struck=True), _price("144.00")])]
        tr_records = [_record(
            "mp00519660",
            "ROYAL BRACKLA 12 Year Old Single Malt Scotch Whisky 1000ML 46%",
            [_price("119.00", struck=True), _price("100.00")])]
        nt, _, _ = parse_tile_records(nt_records, "nt", SEEN_AT)
        tr, _, _ = parse_tile_records(tr_records, "tr", SEEN_AT)
        assert len(nt) == 1 and len(tr) == 1
        assert nt[0].price_sgd == 144.00
        assert nt[0].traveller_only is False
        assert nt[0].size_ml == 1000
        assert tr[0].price_sgd == 100.00
        assert tr[0].traveller_only is True
        assert nt[0].canonical == tr[0].canonical == "royal-brackla-12yr"
        # The mode suffix is what keeps both listings alive in dedupe.
        assert listing_key(nt[0]) != listing_key(tr[0])
        assert listing_key(nt[0]).endswith("|nt")
        assert listing_key(tr[0]).endswith("|tr")
        self.assertEqual(tr[0].price_sgd, 100.00)
        self.assertFalse(nt[0].traveller_only)

    def test_was_price_ignored(self):
        records = [_record(
            "mp1", "Test Bottle 700ML 46%",
            [_price("218.00", struck=True), _price("165.00")])]
        listings, _, _ = parse_tile_records(records, "tr", SEEN_AT)
        assert listings[0].price_sgd == 165.00
        self.assertEqual(listings[0].price_sgd, 165.00)

    def test_from_price_unverifies_size(self):
        record = _record(
            "mp2", "Test Bottle 700ML 46%",
            [_price("138.00", struck=True), _price("127.00")])
        record["tileText"] = "Test Bottle From S$127.00 S$138.00"
        listings, _, _ = parse_tile_records([record], "nt", SEEN_AT)
        assert len(listings) == 1
        assert listings[0].price_sgd == 127.00
        assert listings[0].size_ml == 700
        assert listings[0].size_verified is False
        self.assertFalse(listings[0].size_verified)

    def test_repeated_brand_stripped(self):
        # Link text with brand + name on one line: the brand must not
        # double in the canonical key.
        records = [_record(
            "mp6",
            "ROYAL BRACKLA ROYAL BRACKLA 18 Year Old Single Malt "
            "Scotch Whisky 700ML 46%",
            [_price("165.00")])]
        listings, _, _ = parse_tile_records(records, "tr", SEEN_AT)
        assert len(listings) == 1
        assert listings[0].canonical == "royal-brackla-18yr"
        self.assertEqual(listings[0].canonical, "royal-brackla-18yr")

    def test_out_of_stock_kept_with_price(self):
        records = [_record(
            "mp3", "Test Bottle 700ML 46%", [_price("240.00")],
            out_of_stock=True)]
        listings, _, _ = parse_tile_records(records, "tr", SEEN_AT)
        assert len(listings) == 1
        assert listings[0].in_stock is False
        assert listings[0].price_sgd == 240.00
        self.assertFalse(listings[0].in_stock)

    def test_no_price_skipped(self):
        records = [_record("mp4", "Test Bottle 700ML 46%", [])]
        listings, seen, skipped = parse_tile_records(records, "nt", SEEN_AT)
        assert listings == [] and seen == 1 and skipped == 1
        self.assertEqual(len(listings), 0)

    def test_duplicate_code_deduped(self):
        records = [
            _record("mp5", "Test Bottle 700ML 46%", [_price("100.00")]),
            _record("mp5", "Test Bottle 700ML 46%", [_price("100.00")]),
        ]
        listings, seen, skipped = parse_tile_records(records, "nt", SEEN_AT)
        assert len(listings) == 1 and seen == 2 and skipped == 1
        self.assertEqual(len(listings), 1)


class _FixtureHandler(http.server.SimpleHTTPRequestHandler):
    """Serve the fixture directory; silence the access log."""

    def log_message(self, *args):
        assert args, "log call must carry arguments"


class LocalServerTest(unittest.TestCase):
    def test_both_modes_against_local_fixture_server(self):
        handler = functools.partial(
            _FixtureHandler, directory=FIXTURES)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        assert server.server_address[1] > 0, "server must bind a port"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            nt_url = ("http://127.0.0.1:%d/ishopchangi-search-nt.html"
                      % port)
            tr_url = ("http://127.0.0.1:%d/ishopchangi-search-tr.html"
                      % port)

            async def run():
                async with Stealth().use_async(async_playwright()) as pw:
                    assert pw is not None, "playwright handle required"
                    return await scrape(pw, seen_at=SEEN_AT,
                                        nt_url=nt_url, tr_url=tr_url)

            listings, health = asyncio.run(run())
            assert health.retailer == "iShopChangi"
            assert health.status == "ok"
            assert health.pages_scanned == 2
            assert len(listings) == 10, "4 NT + 6 TR listings expected"
            assert health.items_found == 10
            by_key = {listing_key(item): item for item in listings}
            brackla_18 = by_key["iShopChangi|royal-brackla-18yr|tr"]
            assert brackla_18.price_sgd == 165.00
            assert brackla_18.traveller_only is True
            brackla_12_nt = by_key["iShopChangi|royal-brackla-12yr|nt"]
            brackla_12_tr = by_key["iShopChangi|royal-brackla-12yr|tr"]
            assert brackla_12_nt.price_sgd == 144.00
            assert brackla_12_tr.price_sgd == 100.00
            brackla_21_nt = by_key["iShopChangi|royal-brackla-21yr|nt"]
            brackla_21_tr = by_key["iShopChangi|royal-brackla-21yr|tr"]
            assert brackla_21_nt.price_sgd == 532.00
            assert brackla_21_tr.price_sgd == 330.00
            brandy = [item for item in listings
                      if "Brandy Cask" in item.name][0]
            assert brandy.price_sgd == 127.00
            assert brandy.size_verified is False
            glenallachie = [item for item in listings
                            if "GLENALLACHIE" in item.name][0]
            assert glenallachie.in_stock is False
            nikka = [item for item in listings
                     if "Nikka Whiskey Tailored" in item.name][0]
            assert nikka.price_sgd == 185.00
            assert nikka.size_ml == 700
            self.assertEqual(len(listings), 10)
            self.assertEqual(brackla_18.price_sgd, 165.00)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            assert not thread.is_alive(), "server thread must stop"


@unittest.skipUnless(LIVE, "live test: set HUNT_LIVE=1 (needs direct net)")
class LiveTest(unittest.TestCase):
    def test_live_traveller_brackla_18(self):
        async def run():
            async with Stealth().use_async(async_playwright()) as pw:
                assert pw is not None, "playwright handle required"
                return await scrape_mode(
                    pw, "tr", query="Royal Brackla 18", max_pages=2)

        listings, health = asyncio.run(run())
        assert len(listings) > 0, "live TR search must find listings"
        assert health.items_found == len(listings)
        assert health.status == "ok"
        brackla = [item for item in listings
                   if item.canonical == "royal-brackla-18yr"]
        assert brackla, "Brackla 18 must appear in TR search"
        assert brackla[0].price_sgd == 165.00
        assert brackla[0].traveller_only is True
        print("live TR: %d listings, Brackla 18 S$%.2f"
              % (len(listings), brackla[0].price_sgd))

    def test_live_nontraveller_whisky_page(self):
        async def run():
            async with Stealth().use_async(async_playwright()) as pw:
                assert pw is not None, "playwright handle required"
                return await scrape_mode(pw, "nt", max_pages=1)

        listings, health = asyncio.run(run())
        assert len(listings) > 0, "live NT search must find listings"
        assert health.items_found == len(listings)
        assert health.status == "ok"
        assert all(item.traveller_only is False for item in listings)
        assert all(item.price_sgd > 0 for item in listings)
        assert all(item.url.startswith(
            "https://www.ishopchangi.com/en/product/")
            for item in listings)
        print("live NT: %d listings on page 1, first: %s S$%.2f"
              % (len(listings), listings[0].name, listings[0].price_sgd))


if __name__ == "__main__":
    unittest.main()
