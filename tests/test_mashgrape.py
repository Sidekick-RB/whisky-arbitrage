"""Unit tests for the Mash & Grape parser, using real-data fixtures.

Fixtures: tests/fixtures/mashgrape-search-glenfiddich18.html — 13
real BC Smart Filter cards from the live /search?q=glenfiddich+18
page, fetched 11 Nov 2026 (the split string occurs 14 times, but
the 14th is a client-side {{itemTitle}} template, not a card;
Glenfiddich 12 at $ 49.99 in stock; Glenfiddich 18 at $ 116.99
sold out; 8/13 cards sold out);
mashgrape-product-glenfiddich18.js — single "750ml" variant,
price 11699, available False, structured Size option ['750ml'].
"""

import asyncio
import json
import os
import unittest

import hunt.parsers.mashgrape as mg
from hunt.parsers.mashgrape import parse_search_html, search_url
from hunt.parsers.shopify_js import resolve_product_js

SEARCH = os.path.join(os.path.dirname(__file__), "fixtures",
                      "mashgrape-search-glenfiddich18.html")
GF18_JS = os.path.join(os.path.dirname(__file__), "fixtures",
                       "mashgrape-product-glenfiddich18.js")
PAGE_URL = "https://www.mashandgrape.com/search?q=glenfiddich+18"
SEEN_AT = "2026-11-11T00:00:00+00:00"
GF18_URL = ("https://www.mashandgrape.com/products/"
            "glenfiddichscotchsinglemalt18yearold")
GF12_URL = ("https://www.mashandgrape.com/products/"
            "glenfiddichscotchsinglemalt12yearold")


def _load(path):
    assert os.path.exists(path), "fixture missing: " + path
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    assert text, "fixture must be non-empty"
    return text


def _load_json(path):
    data = json.loads(_load(path))
    assert isinstance(data, dict), "fixture must be a JSON object"
    return data


class SearchUrlTest(unittest.TestCase):
    def test_search_url(self):
        url = search_url("glenfiddich 18")
        assert url == "https://www.mashandgrape.com/search?q=glenfiddich+18"
        self.assertEqual(
            url, "https://www.mashandgrape.com/search?q=glenfiddich+18")

    def test_search_url_page_two_rejected(self):
        # Single page, no pagination links observed.
        with self.assertRaises(AssertionError):
            search_url("glenfiddich 18", 2)


class FixtureTest(unittest.TestCase):
    def test_thirteen_cards_parse(self):
        # 14 split occurrences, but the 14th is the {{itemTitle}}
        # template — only 13 real cards.
        cards, seen = parse_search_html(_load(SEARCH), PAGE_URL, SEEN_AT)
        assert seen == 13
        assert len(cards) == 13
        self.assertEqual(len(cards), 13)
        self.assertEqual(seen, 13)

    def test_space_after_dollar_parses(self):
        # Prices are "$ 49.99" with a space after $ — a \$[\d]
        # regex would miss every product price.
        cards, _ = parse_search_html(_load(SEARCH), PAGE_URL, SEEN_AT)
        by_name = {name: (price, url, in_stock)
                   for name, price, url, in_stock in cards}
        item = by_name["Glenfiddich Scotch Single Malt 12 Year Old"]
        assert item[0] == 49.99
        assert item[1] == GF12_URL
        assert item[2] is True
        self.assertEqual(item[0], 49.99)

    def test_glenfiddich_18_is_sold_out(self):
        cards, _ = parse_search_html(_load(SEARCH), PAGE_URL, SEEN_AT)
        by_name = {name: (price, url, in_stock)
                   for name, price, url, in_stock in cards}
        item = by_name["Glenfiddich Scotch Single Malt 18 Year Old"]
        assert item[0] == 116.99
        assert item[1] == GF18_URL
        assert item[2] is False
        self.assertFalse(item[2])

    def test_eight_sold_out_cards(self):
        cards, _ = parse_search_html(_load(SEARCH), PAGE_URL, SEEN_AT)
        out = [name for name, _, _, in_stock in cards if not in_stock]
        assert len(out) == 8
        assert "Glenfiddich Scotch Single Malt 15 Year Old Solera Reserve" in out
        self.assertEqual(len(out), 8)

    def test_glenfiddich_18_js_is_unavailable(self):
        # The .js payload confirms the card marker: available False,
        # so resolve_product_js drops it even though a size and a
        # price are present.
        assert resolve_product_js(_load_json(GF18_JS)) is None
        self.assertIsNone(resolve_product_js(_load_json(GF18_JS)))

    def test_size_option_resolves(self):
        # Synthetic: a "Default Title" variant with a structured
        # Size option — the option's first value is the size.
        data = {"variants": [{"title": "Default Title", "price": 7000,
                              "available": True}],
                "options": [{"name": "Size", "values": ["700ml"]}],
                "tags": [], "description": ""}
        resolved = resolve_product_js(data)
        assert resolved == (70.00, 700, True)
        self.assertEqual(resolved, (70.00, 700, True))


class DriverTest(unittest.TestCase):
    def _run_search(self, wanted, twelve_available=True):
        search_html = _load(SEARCH)
        gf18 = _load_json(GF18_JS)
        twelve = {"variants": [{"title": "750ml", "price": 4999,
                                "available": twelve_available}],
                  "options": [{"name": "Size", "values": ["750ml"]}],
                  "tags": [], "description": ""}
        generic = {"variants": [{"title": "750ml", "price": 2999,
                                 "available": True}],
                   "options": [], "tags": [], "description": ""}

        def _fake_fetch(url):
            assert url.startswith("https://www.mashandgrape.com")
            return search_html

        def _fake_js(url):
            assert url.startswith("https://www.mashandgrape.com/products/")
            assert url.endswith(".js"), ".js URL must end in .js"
            if "glenfiddichscotchsinglemalt18yearold" in url:
                return gf18
            if "glenfiddichscotchsinglemalt12yearold" in url:
                return twelve
            return generic

        saved_text = mg.fetch_text
        saved_js = mg.fetch_product_js
        mg.fetch_text = _fake_fetch
        mg.fetch_product_js = _fake_js
        try:
            prices, health = asyncio.run(
                mg.search("glenfiddich 18", wanted=wanted,
                          seen_at=SEEN_AT))
        finally:
            mg.fetch_text = saved_text
            mg.fetch_product_js = saved_js
        return prices, health

    def test_glenfiddich_18_is_dropped(self):
        # Sold out on the card AND available False in .js: GF18
        # must never become a price.
        prices, health = self._run_search({"glenfiddich-18yr"})
        assert prices == []
        assert health.status == "zero-items"
        self.assertEqual(health.status, "zero-items")

    def test_wanted_glenfiddich_12_resolves(self):
        prices, health = self._run_search({"glenfiddich-12yr"})
        assert len(prices) == 1
        assert health.status == "ok"
        item = prices[0]
        assert item.price == 49.99
        assert item.size_ml == 750
        assert item.in_stock is True
        assert item.retailer == "Mash & Grape"
        assert item.currency == "USD"
        assert item.url == GF12_URL
        self.assertEqual(item.price, 49.99)
        self.assertEqual(item.size_ml, 750)

    def test_js_unavailable_drops_in_stock_card(self):
        # The card says in stock, but the .js variant is
        # unavailable: the .js availability check still drops it.
        prices, health = self._run_search({"glenfiddich-12yr"},
                                          twelve_available=False)
        assert prices == []
        assert health.status == "zero-items"
        self.assertEqual(health.status, "zero-items")

    def test_wanted_none_resolves_all_in_stock(self):
        # 13 cards minus the 8 sold-out ones.
        prices, health = self._run_search(None)
        assert len(prices) == 5
        assert health.status == "ok"
        assert all(item.size_ml == 750 for item in prices)
        assert all(item.in_stock is True for item in prices)
        self.assertEqual(len(prices), 5)

    def test_failed_fetch_is_blocked_not_crash(self):
        saved_text = mg.fetch_text
        mg.fetch_text = lambda url: None
        try:
            prices, health = asyncio.run(
                mg.search("glenfiddich 18", seen_at=SEEN_AT))
        finally:
            mg.fetch_text = saved_text
        assert prices == []
        assert health.status == "blocked"
        self.assertEqual(health.status, "blocked")


if __name__ == "__main__":
    unittest.main()
