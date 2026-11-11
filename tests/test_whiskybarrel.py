"""Unit tests for The Whisky Barrel parser, using real-data fixtures.

Fixtures: tests/fixtures/whiskybarrel-suggest-glenfiddich18.json —
the verbatim Shopify suggest.json for "glenfiddich 18", fetched 11
Nov 2026: 5 products, all available; and
tests/fixtures/whiskybarrel-product-glenallachie18.html — the
verbatim Glenallachie 18 product page with the "Volume: 70cl"
bottle-info row. Note: no plain Glenfiddich 18 Year Old is in the
suggest fixture — results are other 18yos — so the fixture tests
assert on Glenallachie 18 (£150.61) and Glen Moray 18 (£99.95).
"""

import asyncio
import json
import os
import unittest

import hunt.parsers.whiskybarrel as wb
from hunt.parsers.whiskybarrel import (
    parse_product_html,
    parse_suggest_json,
    search_url,
)

SUGGEST = os.path.join(os.path.dirname(__file__), "fixtures",
                       "whiskybarrel-suggest-glenfiddich18.json")
PRODUCT = os.path.join(os.path.dirname(__file__), "fixtures",
                       "whiskybarrel-product-glenallachie18.html")
SEEN_AT = "2026-11-11T00:00:00+00:00"


def _load_json(path):
    assert os.path.exists(path), "fixture missing: " + path
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    assert isinstance(data, dict), "fixture must be a JSON object"
    return data


def _load(path):
    assert os.path.exists(path), "fixture missing: " + path
    with open(path, "r", encoding="utf-8") as handle:
        html = handle.read()
    assert html, "fixture must be non-empty"
    return html


class SearchUrlTest(unittest.TestCase):
    def test_search_url(self):
        url = search_url("glenfiddich 18")
        assert url == (
            "https://www.thewhiskybarrel.com/search/suggest.json"
            "?q=glenfiddich+18&resources[type]=product"
            "&resources[limit]=10")
        self.assertTrue(url.startswith(
            "https://www.thewhiskybarrel.com/search/suggest.json"))


class FixtureTest(unittest.TestCase):
    def test_five_products_all_available(self):
        cards, seen = parse_suggest_json(_load_json(SUGGEST), SEEN_AT)
        assert seen == 5
        assert len(cards) == 5
        assert all(in_stock for _, _, _, in_stock in cards)
        self.assertEqual(len(cards), 5)

    def test_glenallachie_18_benchmark(self):
        cards, _ = parse_suggest_json(_load_json(SUGGEST), SEEN_AT)
        by_name = {name: (price, url, in_stock)
                   for name, price, url, in_stock in cards}
        assert by_name["Glenallachie 18 Year Old"][0] == 150.61
        assert by_name["Glenallachie 18 Year Old"][1] == (
            "https://www.thewhiskybarrel.com/products/"
            "glenallachie-18-year-old-malt")
        assert by_name["Glenallachie 18 Year Old"][2] is True
        self.assertEqual(by_name["Glenallachie 18 Year Old"][0], 150.61)

    def test_glen_moray_18_price(self):
        cards, _ = parse_suggest_json(_load_json(SUGGEST), SEEN_AT)
        by_name = {name: price for name, price, _, _ in cards}
        assert by_name["Glen Moray 18 Year Old 2007 Fragrant Drops"] == 99.95
        self.assertEqual(
            by_name["Glen Moray 18 Year Old 2007 Fragrant Drops"], 99.95)

    def test_out_of_stock_flag_and_zero_price(self):
        # available:false with a price is kept with in_stock False;
        # a £0.00 price is not a price and is dropped.
        data = {"resources": {"results": {"products": [
            {"available": False, "handle": "glenallachie-18-year-old",
             "price": "150.61", "title": "Glenallachie 18 Year Old",
             "url": "/products/glenallachie-18-year-old-malt"},
            {"available": True, "handle": "free-sample",
             "price": "0.00", "title": "Free Sample",
             "url": "/products/free-sample"},
            {"available": True, "handle": "no-title",
             "price": "10.00", "title": " ",
             "url": "/products/no-title"},
        ]}}}
        cards, seen = parse_suggest_json(data, SEEN_AT)
        assert seen == 3
        assert len(cards) == 1
        assert cards[0][0] == "Glenallachie 18 Year Old"
        assert cards[0][1] == 150.61
        assert cards[0][3] is False
        self.assertFalse(cards[0][3])

    def test_malformed_payload_is_empty_not_crash(self):
        cards, seen = parse_suggest_json({}, SEEN_AT)
        assert cards == []
        assert seen == 0
        cards, seen = parse_suggest_json({"resources": {}}, SEEN_AT)
        assert cards == []
        assert seen == 0
        self.assertEqual(cards, [])

    def test_product_page_size(self):
        size_ml = parse_product_html(_load(PRODUCT))
        assert size_ml == 700
        self.assertEqual(size_ml, 700)

    def test_product_page_no_size(self):
        assert parse_product_html("<html><body>no size here</body></html>") is None
        self.assertIsNone(parse_product_html("<html></html>"))


class UrllibDriverTest(unittest.TestCase):
    def test_search_resolves_size_for_all_when_wanted_is_none(self):
        suggest = _load_json(SUGGEST)
        product_html = _load(PRODUCT)
        calls = []

        def _fake_json(url):
            assert url.startswith("https://www.thewhiskybarrel.com")
            calls.append(("json", url))
            return suggest

        def _fake_text(url):
            assert url.startswith("https://www.thewhiskybarrel.com")
            calls.append(("text", url))
            return product_html

        saved_json = wb.fetch_json
        saved_text = wb.fetch_text
        wb.fetch_json = _fake_json
        wb.fetch_text = _fake_text
        try:
            prices, health = asyncio.run(
                wb.search("glenfiddich 18", seen_at=SEEN_AT))
        finally:
            wb.fetch_json = saved_json
            wb.fetch_text = saved_text
        assert len(prices) == 5
        assert health.status == "ok"
        assert all(item.size_ml == 700 for item in prices)
        assert all(item.currency == "GBP" for item in prices)
        assert all(item.in_stock for item in prices)
        by_name = {item.name: item for item in prices}
        assert by_name["Glenallachie 18 Year Old"].price == 150.61
        assert len(calls) == 6
        self.assertEqual(len(prices), 5)
        self.assertEqual(health.status, "ok")

    def test_search_resolves_size_only_for_wanted(self):
        # Only the wanted canonical key gets a product-page fetch;
        # the other 4 products are dropped without a fetch.
        suggest = _load_json(SUGGEST)
        product_html = _load(PRODUCT)
        calls = []

        def _fake_json(url):
            assert url.startswith("https://www.thewhiskybarrel.com")
            calls.append(("json", url))
            return suggest

        def _fake_text(url):
            assert url.startswith("https://www.thewhiskybarrel.com")
            calls.append(("text", url))
            return product_html

        saved_json = wb.fetch_json
        saved_text = wb.fetch_text
        wb.fetch_json = _fake_json
        wb.fetch_text = _fake_text
        try:
            prices, health = asyncio.run(
                wb.search("glenfiddich 18", wanted={"glenallachie-18yr"},
                          seen_at=SEEN_AT))
        finally:
            wb.fetch_json = saved_json
            wb.fetch_text = saved_text
        assert len(prices) == 1
        assert health.status == "ok"
        assert prices[0].name == "Glenallachie 18 Year Old"
        assert prices[0].price == 150.61
        assert prices[0].size_ml == 700
        assert len(calls) == 2
        self.assertEqual(len(prices), 1)
        self.assertEqual(len(calls), 2)

    def test_failed_fetch_is_blocked_not_crash(self):
        saved = wb.fetch_json
        wb.fetch_json = lambda url: None
        try:
            prices, health = asyncio.run(
                wb.search("glenfiddich 18", seen_at=SEEN_AT))
        finally:
            wb.fetch_json = saved
        assert prices == []
        assert health.status == "blocked"
        self.assertEqual(health.status, "blocked")


if __name__ == "__main__":
    unittest.main()
