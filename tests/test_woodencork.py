"""Unit tests for the Wooden Cork parser, using real-data fixtures.

Fixtures: tests/fixtures/woodencork-search-glenfiddich18.html — 40
verbatim product-grid-item cards from the live
/search?q=glenfiddich+18 page, fetched 11 Nov 2026 (Glenfiddich 18
at $124.99, in stock; 10/40 cards sold out);
woodencork-product-glenfiddich18.js — single "Default Title"
variant, price 12499, available True, tags include 'Size_750ml'.
"""

import asyncio
import json
import os
import unittest

import hunt.parsers.woodencork as wc
from hunt.parsers.shopify_js import resolve_product_js
from hunt.parsers.woodencork import parse_search_html, search_url

SEARCH = os.path.join(os.path.dirname(__file__), "fixtures",
                      "woodencork-search-glenfiddich18.html")
GF18_JS = os.path.join(os.path.dirname(__file__), "fixtures",
                       "woodencork-product-glenfiddich18.js")
PAGE_URL = "https://woodencork.com/search?q=glenfiddich+18"
SEEN_AT = "2026-11-11T00:00:00+00:00"
GF18_URL = "https://woodencork.com/products/glenfiddich-18-year"


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
        assert url == "https://woodencork.com/search?q=glenfiddich+18"
        self.assertEqual(url, "https://woodencork.com/search?q=glenfiddich+18")

    def test_search_url_page_two(self):
        url = search_url("glenfiddich 18", 2)
        assert url == ("https://woodencork.com/search?q=glenfiddich+18"
                       "&page=2")
        self.assertIn("&page=2", url)


class FixtureTest(unittest.TestCase):
    def test_forty_cards_parse(self):
        cards, seen = parse_search_html(_load(SEARCH), PAGE_URL, SEEN_AT)
        assert seen == 40
        assert len(cards) == 40
        self.assertEqual(len(cards), 40)
        self.assertEqual(seen, 40)

    def test_glenfiddich_18_card(self):
        cards, _ = parse_search_html(_load(SEARCH), PAGE_URL, SEEN_AT)
        by_name = {name: (price, url, in_stock)
                   for name, price, url, in_stock in cards}
        item = by_name["Glenfiddich 18 Year"]
        assert item[0] == 124.99
        assert item[1] == GF18_URL
        assert item[2] is True
        self.assertEqual(item[0], 124.99)
        self.assertEqual(item[1], GF18_URL)

    def test_ten_sold_out_cards(self):
        cards, _ = parse_search_html(_load(SEARCH), PAGE_URL, SEEN_AT)
        out = [name for name, _, _, in_stock in cards if not in_stock]
        assert len(out) == 10
        # Out-of-stock cards still carry a price on this site — the
        # tag, not the price, is the stock signal.
        assert ("Glenfiddich 12 Year Old, 15 Year Old, 18 Year Old "
                "Single Malt Scotch Whisky Combo Pack") in out
        assert "Glenfiddich IPA Cask" in out
        self.assertEqual(len(out), 10)

    def test_out_of_stock_card_keeps_its_price(self):
        cards, _ = parse_search_html(_load(SEARCH), PAGE_URL, SEEN_AT)
        by_name = {name: (price, url, in_stock)
                   for name, price, url, in_stock in cards}
        item = by_name["Glenfiddich IPA Cask"]
        assert item[2] is False
        assert item[0] is not None and item[0] > 0
        self.assertFalse(item[2])

    def test_glenfiddich_18_js_resolves(self):
        # Size comes from the 'Size_750ml' tag: the single variant
        # is "Default Title" and there is no Size option.
        resolved = resolve_product_js(_load_json(GF18_JS))
        assert resolved == (124.99, 750, True)
        self.assertEqual(resolved, (124.99, 750, True))


class DriverTest(unittest.TestCase):
    def _run_search(self, wanted):
        search_html = _load(SEARCH)
        gf18 = _load_json(GF18_JS)
        generic = {"variants": [{"title": "750ml", "price": 2999,
                                 "available": True}],
                   "options": [], "tags": [], "description": ""}

        def _fake_fetch(url):
            assert url.startswith("https://woodencork.com")
            # Page 1 is the fixture; page 2+ is empty on the live
            # site, so the driver must stop there.
            if "&page=" in url:
                return "<html><body>no results</body></html>"
            return search_html

        def _fake_js(url):
            assert url.startswith("https://woodencork.com/products/")
            assert url.endswith(".js"), ".js URL must end in .js"
            if "glenfiddich-18-year" in url:
                return gf18
            return generic

        saved_text = wc.fetch_text
        saved_js = wc.fetch_product_js
        wc.fetch_text = _fake_fetch
        wc.fetch_product_js = _fake_js
        try:
            prices, health = asyncio.run(
                wc.search("glenfiddich 18", wanted=wanted,
                          seen_at=SEEN_AT))
        finally:
            wc.fetch_text = saved_text
            wc.fetch_product_js = saved_js
        return prices, health

    def test_wanted_glenfiddich_18_resolves(self):
        prices, health = self._run_search({"glenfiddich-18yr"})
        assert len(prices) == 1
        assert health.status == "ok"
        item = prices[0]
        assert item.price == 124.99
        assert item.size_ml == 750
        assert item.in_stock is True
        assert item.retailer == "Wooden Cork"
        assert item.currency == "USD"
        assert item.url == GF18_URL
        self.assertEqual(item.price, 124.99)
        self.assertEqual(item.size_ml, 750)

    def test_wanted_none_resolves_all_in_stock(self):
        # 40 cards minus the 10 sold-out ones.
        prices, health = self._run_search(None)
        assert len(prices) == 30
        assert health.status == "ok"
        assert all(item.size_ml == 750 for item in prices)
        assert all(item.in_stock is True for item in prices)
        self.assertEqual(len(prices), 30)

    def test_sold_out_wanted_item_is_dropped(self):
        # The Glenfiddich IPA Cask is sold out on the fixture page,
        # so a search wanting only it yields no prices.
        prices, health = self._run_search({"glenfiddich-ipa-cask"})
        assert prices == []
        assert health.status == "zero-items"
        self.assertEqual(health.status, "zero-items")

    def test_failed_fetch_is_blocked_not_crash(self):
        saved_text = wc.fetch_text
        wc.fetch_text = lambda url: None
        try:
            prices, health = asyncio.run(
                wc.search("glenfiddich 18", seen_at=SEEN_AT))
        finally:
            wc.fetch_text = saved_text
        assert prices == []
        assert health.status == "blocked"
        self.assertEqual(health.status, "blocked")


if __name__ == "__main__":
    unittest.main()
