"""Unit tests for the DramFellows parser, using real-data fixtures.

Fixtures: tests/fixtures/dramfellows-search-glenfiddich18.html — 30
verbatim grid-product cards from the live /search?q=glenfiddich+18
page, fetched 11 Nov 2026 (Glenfiddich 18 at $139.99, was $179.99,
in stock; 0/30 sold out); dramfellows-product-glenfiddich18.js —
single "Default Title" variant, price 13999, available True, tags
include 'glenfiddich 750ml'; dramfellows-product-blantons.js —
4 variants ("750ml" $87.99, "3 Pack 750mL" $309.99, "6 Pack 750mL",
"12 Pack 750mL").
"""

import asyncio
import copy
import json
import os
import unittest

import hunt.parsers.dramfellows as df
from hunt.parsers.dramfellows import parse_search_html, search_url
from hunt.parsers.shopify_js import product_js_url, resolve_product_js

SEARCH = os.path.join(os.path.dirname(__file__), "fixtures",
                      "dramfellows-search-glenfiddich18.html")
GF18_JS = os.path.join(os.path.dirname(__file__), "fixtures",
                       "dramfellows-product-glenfiddich18.js")
BLANTONS_JS = os.path.join(os.path.dirname(__file__), "fixtures",
                           "dramfellows-product-blantons.js")
PAGE_URL = "https://dramfellows.com/search?q=glenfiddich+18"
SEEN_AT = "2026-11-11T00:00:00+00:00"
GF18_URL = ("https://dramfellows.com/products/"
            "glenfiddich-18-year-single-malt-scotch-whisky")


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
        assert url == "https://dramfellows.com/search?q=glenfiddich+18"
        self.assertEqual(
            url, "https://dramfellows.com/search?q=glenfiddich+18")

    def test_search_url_page_two_rejected(self):
        # Pagination is not a clean continuation — page 2 repeats
        # page 1 — so only page 1 is a valid search URL.
        with self.assertRaises(AssertionError):
            search_url("glenfiddich 18", 2)


class ResolveJsTest(unittest.TestCase):
    def test_single_variant_glenfiddich_18(self):
        # Size comes from the tags ('glenfiddich 750ml'): the single
        # variant is "Default Title" and there is no Size option.
        resolved = resolve_product_js(_load_json(GF18_JS))
        assert resolved == (139.99, 750, True)
        self.assertEqual(resolved, (139.99, 750, True))

    def test_multi_variant_blantons_prefers_bare_size(self):
        # The first bare-size variant ("750ml", $87.99) wins over the
        # "3 Pack 750mL" / "6 Pack" / "12 Pack" variants.
        resolved = resolve_product_js(_load_json(BLANTONS_JS))
        assert resolved == (87.99, 750, True)
        self.assertEqual(resolved, (87.99, 750, True))

    def test_unavailable_variant_is_none(self):
        data = _load_json(BLANTONS_JS)
        data["variants"][0]["available"] = False
        assert resolve_product_js(data) is None
        self.assertIsNone(resolve_product_js(data))

    def test_missing_size_is_none(self):
        data = {"variants": [{"title": "Default Title", "price": 9999,
                              "available": True}],
                "options": [{"name": "Title", "values": ["Default Title"]}],
                "tags": ["bourbon"],
                "description": "A fine bourbon with no size stated."}
        assert resolve_product_js(data) is None
        self.assertIsNone(resolve_product_js(data))

    def test_description_fallback(self):
        # With the size tags stripped, the description's "750mL
        # bottle" still resolves the size.
        data = _load_json(GF18_JS)
        data["tags"] = []
        resolved = resolve_product_js(data)
        assert resolved == (139.99, 750, True)
        self.assertEqual(resolved, (139.99, 750, True))

    def test_non_dict_and_empty_variants_are_none(self):
        assert resolve_product_js(None) is None
        assert resolve_product_js({"variants": []}) is None
        assert resolve_product_js("not a dict") is None
        self.assertIsNone(resolve_product_js(None))

    def test_zero_price_variant_is_none(self):
        # A zero price is not a price (base.parse_price convention):
        # the item is dropped, never a crash.
        data = {"variants": [{"title": "750ml", "price": 0,
                              "available": True}],
                "options": [{"name": "Size", "values": ["750ml"]}],
                "tags": [], "description": ""}
        assert resolve_product_js(data) is None
        self.assertIsNone(resolve_product_js(data))

    def test_pack_only_variants_are_none(self):
        # No single-bottle variant: the pack price ($309.99 for 3
        # bottles) must never pair with a single-bottle 750 ml size.
        data = {"variants": [{"title": "3 Pack 750mL", "price": 30999,
                              "available": True},
                             {"title": "6 Pack 750mL", "price": 60999,
                              "available": True}],
                "options": [{"name": "Size",
                             "values": ["3 Pack 750mL", "6 Pack 750mL"]}],
                "tags": [], "description": ""}
        assert resolve_product_js(data) is None
        self.assertIsNone(resolve_product_js(data))

    def test_product_js_url_strips_query(self):
        url = product_js_url(
            "https://dramfellows.com",
            GF18_URL + "?_pos=1&_sid=d4930083d&_ss=r")
        assert url == GF18_URL + ".js"
        self.assertEqual(url, GF18_URL + ".js")


class FixtureTest(unittest.TestCase):
    def test_thirty_cards_parse(self):
        cards, seen = parse_search_html(_load(SEARCH), PAGE_URL, SEEN_AT)
        assert seen == 30
        assert len(cards) == 30
        self.assertEqual(len(cards), 30)
        self.assertEqual(seen, 30)

    def test_glenfiddich_18_card(self):
        cards, _ = parse_search_html(_load(SEARCH), PAGE_URL, SEEN_AT)
        by_name = {name: (price, url, in_stock)
                   for name, price, url, in_stock in cards}
        item = by_name["Glenfiddich 18 Year Single Malt Scotch Whisky"]
        assert item[0] == 139.99
        assert item[1] == GF18_URL
        assert item[2] is True
        self.assertEqual(item[0], 139.99)
        self.assertEqual(item[1], GF18_URL)

    def test_blantons_from_price(self):
        # Multi-variant card reads "from $87.99": the anchored
        # current-price regex takes the $87.99 from-price, never the
        # $179.99 compare-at price.
        cards, _ = parse_search_html(_load(SEARCH), PAGE_URL, SEEN_AT)
        by_name = {name: (price, url, in_stock)
                   for name, price, url, in_stock in cards}
        item = by_name["Blanton's Original Single Barrel Bourbon 750ml"]
        assert item[0] == 87.99
        assert item[2] is True
        self.assertEqual(item[0], 87.99)

    def test_no_sold_out_cards_on_page_one(self):
        cards, _ = parse_search_html(_load(SEARCH), PAGE_URL, SEEN_AT)
        assert all(in_stock for _, _, _, in_stock in cards)
        self.assertTrue(all(in_stock for _, _, _, in_stock in cards))

    def test_out_of_stock_marker(self):
        # Synthetic: the sold-out tag ("Coming Soon") on the GF18
        # card flips only that card to out of stock.
        html = _load(SEARCH).replace(
            '<div class="grid-product__title">Glenfiddich 18 Year '
            'Single Malt Scotch Whisky</div>',
            '<div class="grid-product__tag grid-product__tag--sold-out">'
            'Coming Soon</div>'
            '<div class="grid-product__title">Glenfiddich 18 Year '
            'Single Malt Scotch Whisky</div>', 1)
        cards, seen = parse_search_html(html, PAGE_URL, SEEN_AT)
        assert seen == 30
        by_name = {name: (price, url, in_stock)
                   for name, price, url, in_stock in cards}
        item = by_name["Glenfiddich 18 Year Single Malt Scotch Whisky"]
        assert item[2] is False
        assert item[0] == 139.99
        self.assertFalse(item[2])


class DriverTest(unittest.TestCase):
    def _run_search(self, wanted, gf18_price=None):
        search_html = _load(SEARCH)
        gf18 = _load_json(GF18_JS)
        if gf18_price is not None:
            gf18 = copy.deepcopy(gf18)
            gf18["price"] = gf18_price
            gf18["variants"][0]["price"] = gf18_price
        blantons = _load_json(BLANTONS_JS)
        generic = {"variants": [{"title": "750ml", "price": 2999,
                                 "available": True}],
                   "options": [], "tags": [], "description": ""}

        def _fake_fetch(url):
            assert url.startswith("https://dramfellows.com")
            if "/search?" in url:
                return search_html
            raise AssertionError("unexpected fetch: " + url)

        def _fake_js(url):
            assert url.startswith("https://dramfellows.com/products/")
            assert url.endswith(".js"), ".js URL must end in .js"
            if "blantons-single-barrel-bourbon" in url:
                return blantons
            if "glenfiddich-18-year-single-malt-scotch-whisky" in url:
                return gf18
            return generic

        saved_text = df.fetch_text
        saved_js = df.fetch_product_js
        df.fetch_text = _fake_fetch
        df.fetch_product_js = _fake_js
        try:
            prices, health = asyncio.run(
                df.search("glenfiddich 18", wanted=wanted,
                          seen_at=SEEN_AT))
        finally:
            df.fetch_text = saved_text
            df.fetch_product_js = saved_js
        return prices, health

    def test_wanted_glenfiddich_18_resolves(self):
        prices, health = self._run_search({"glenfiddich-18yr"})
        assert len(prices) == 1
        assert health.status == "ok"
        item = prices[0]
        assert item.price == 139.99
        assert item.size_ml == 750
        assert item.in_stock is True
        assert item.retailer == "DramFellows"
        assert item.currency == "USD"
        assert item.url == GF18_URL
        self.assertEqual(item.price, 139.99)
        self.assertEqual(item.size_ml, 750)

    def test_js_price_wins_over_card_price(self):
        # The .js variant price is authoritative: with the .js price
        # changed to $129.99, the result is $129.99, not the card's
        # $139.99.
        prices, _ = self._run_search({"glenfiddich-18yr"}, gf18_price=12999)
        assert len(prices) == 1
        assert prices[0].price == 129.99
        self.assertEqual(prices[0].price, 129.99)

    def test_wanted_none_resolves_all_in_stock(self):
        prices, health = self._run_search(None)
        assert len(prices) == 30
        assert health.status == "ok"
        assert all(item.size_ml == 750 for item in prices)
        assert all(item.in_stock is True for item in prices)
        self.assertEqual(len(prices), 30)

    def test_out_of_stock_card_is_dropped(self):
        html = _load(SEARCH).replace(
            '<div class="grid-product__title">Glenfiddich 18 Year '
            'Single Malt Scotch Whisky</div>',
            '<div class="grid-product__tag grid-product__tag--sold-out">'
            'Coming Soon</div>'
            '<div class="grid-product__title">Glenfiddich 18 Year '
            'Single Malt Scotch Whisky</div>', 1)
        saved_text = df.fetch_text
        saved_js = df.fetch_product_js
        df.fetch_text = lambda url: html
        df.fetch_product_js = lambda url: _load_json(GF18_JS)
        try:
            prices, health = asyncio.run(
                df.search("glenfiddich 18", wanted={"glenfiddich-18yr"},
                          seen_at=SEEN_AT))
        finally:
            df.fetch_text = saved_text
            df.fetch_product_js = saved_js
        assert prices == []
        assert health.status == "zero-items"
        self.assertEqual(health.status, "zero-items")

    def test_failed_fetch_is_blocked_not_crash(self):
        saved_text = df.fetch_text
        df.fetch_text = lambda url: None
        try:
            prices, health = asyncio.run(
                df.search("glenfiddich 18", seen_at=SEEN_AT))
        finally:
            df.fetch_text = saved_text
        assert prices == []
        assert health.status == "blocked"
        self.assertEqual(health.status, "blocked")


if __name__ == "__main__":
    unittest.main()
