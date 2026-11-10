"""Unit tests for the iShopChangi parser, using a real-data fixture.

Fixture: tests/fixtures/ishopchangi-search-tr.json — verbatim
products from the live /bin search JSON for "royal brackla"
(Traveller mode), fetched 10 Nov 2026: Brackla 18 (S$165.00),
Brackla 12 (S$100.00), Brackla 21 (S$330.00), plus one real
from=True record. The out-of-stock, no-price and duplicate records
are synthetic copies of real records (no live out-of-stock or
priceless product appeared on page 1).

The old Playwright DOM parser and its synthetic HTML fixtures are
gone: the /bin JSON route (slice 5) replaced it after the browser
driver returned zero tiles on Fargate run after run.
"""

import asyncio
import copy
import json
import os
import unittest

import hunt.parsers.ishopchangi as isc
from hunt.parsers.ishopchangi import api_url, parse_search_json

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "ishopchangi-search-tr.json")
SEEN_AT = "2026-11-10T00:00:00+00:00"


def _load():
    assert os.path.exists(FIXTURE), "fixture missing: " + FIXTURE
    with open(FIXTURE, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    assert isinstance(data, dict) and data, "fixture must be an object"
    return data


class ApiUrlTest(unittest.TestCase):
    def test_api_urls_per_mode(self):
        nt = api_url("whisky", "nt")
        tr = api_url("whisky", "tr")
        assert nt.startswith(
            "https://www.ishopchangi.com/bin/cagcommerce/webservices/"
            "v2/cag/products/search?query=whisky:popularity:channel:ISC"
            ":isCorporateSalesProduct:false:cagCollectionPoint:"
            "HOMEDELIVERYNONTRAVELLER&currentPage=0")
        assert "cagCollectionPoint:DEPARTURE" in tr
        assert "currentPage=0" in tr
        assert api_url("royal brackla", "tr", 1).endswith("currentPage=1")
        assert "royal%20brackla" in api_url("royal brackla", "tr")
        self.assertIn("DEPARTURE", tr)


class FixtureTest(unittest.TestCase):
    def test_brackla_18_traveller(self):
        listings, has_next, seen, skipped = parse_search_json(
            _load(), "tr", SEEN_AT)
        assert seen == 7
        assert skipped == 2
        assert has_next is True
        assert len(listings) == 5
        by_code = {}
        for item in listings:
            by_code[item.url.rsplit("mp", 1)[-1]] = item
        brackla18 = by_code["00519659"]
        assert brackla18.price_sgd == 165.00
        assert brackla18.size_ml == 700
        assert brackla18.size_verified is True
        assert brackla18.in_stock is True
        assert brackla18.traveller_only is True
        assert brackla18.eligibility == "Pre-order to flight"
        assert brackla18.url == (
            "https://www.ishopchangi.com/en/product/"
            "royal-brackla-18-year-old-single-malt-scotch-whisky-"
            "700ml-46--mp00519659")
        self.assertEqual(brackla18.price_sgd, 165.00)
        self.assertEqual(brackla18.size_ml, 700)

    def test_brackla_12_one_litre(self):
        listings, _, _, _ = parse_search_json(_load(), "tr", SEEN_AT)
        by_name = {item.name: item for item in listings}
        brackla12 = by_name[
            "ROYAL BRACKLA 12 YEAR OLD 1000ML 46%"]
        assert brackla12.price_sgd == 100.00
        assert brackla12.size_ml == 1000
        self.assertEqual(brackla12.size_ml, 1000)

    def test_from_price_unverifies_size(self):
        data = _load()
        brackla18 = copy.deepcopy(data["products"][0])
        assert brackla18["code"] == "mp00519659"
        brackla18["from"] = True
        data["products"] = [brackla18]
        listings, _, _, _ = parse_search_json(data, "tr", SEEN_AT)
        assert len(listings) == 1
        assert listings[0].size_ml == 700
        assert listings[0].size_verified is False
        self.assertFalse(listings[0].size_verified)

    def test_out_of_stock_kept_with_price(self):
        listings, _, _, _ = parse_search_json(_load(), "tr", SEEN_AT)
        by_code = {item.url.rsplit("mp", 1)[-1]: item for item in listings}
        oos = by_code["00519660"]
        assert oos.price_sgd == 165.00
        assert oos.in_stock is False
        self.assertFalse(oos.in_stock)

    def test_no_price_and_duplicate_skipped(self):
        listings, _, seen, skipped = parse_search_json(
            _load(), "tr", SEEN_AT)
        assert seen == 7
        assert skipped == 2
        assert len(listings) == 5
        codes = [item.url.rsplit("mp", 1)[-1] for item in listings]
        assert "00088807" not in codes
        assert codes.count("00088806") == 1
        self.assertEqual(len(listings), 5)


class DriverTest(unittest.TestCase):
    def test_scrape_uses_json_not_browser(self):
        # The browser driver returned zero tiles on Fargate while the
        # same data answers as JSON; the driver is now urllib. This
        # test proves the wiring with a stubbed fetch_json (no
        # network, no browser).
        data = _load()
        calls = []

        def _fake_fetch(url, headers=None):
            assert url.startswith(
                "https://www.ishopchangi.com/bin/")
            assert headers and headers.get("Referer", "").startswith(
                "https://www.ishopchangi.com/en/search")
            calls.append(url)
            return data

        saved = isc.fetch_json
        isc.fetch_json = _fake_fetch
        try:
            listings, health = asyncio.run(
                isc.scrape(seen_at=SEEN_AT, max_pages=1, delay_s=0))
        finally:
            isc.fetch_json = saved
        assert len(listings) == 10
        assert health.status == "ok"
        assert health.pages_scanned == 2
        modes = {item.traveller_only for item in listings}
        assert modes == {True, False}
        assert len(calls) == 2
        self.assertEqual(len(listings), 10)
        self.assertEqual(health.status, "ok")

    def test_failed_fetch_is_blocked_not_crash(self):
        saved = isc.fetch_json
        isc.fetch_json = lambda url, headers=None: None
        try:
            listings, health = asyncio.run(
                isc.scrape(seen_at=SEEN_AT, max_pages=1, delay_s=0))
        finally:
            isc.fetch_json = saved
        assert listings == []
        assert health.status == "blocked"
        self.assertEqual(health.status, "blocked")


if __name__ == "__main__":
    unittest.main()
