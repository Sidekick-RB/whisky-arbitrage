"""Unit tests for the shared Shopify products.json helper."""

import asyncio
import functools
import http.server
import os
import threading
import unittest

from hunt.parsers.base import fetch_json
from hunt.parsers.shopify import (
    is_whisky_title,
    parse_products_json,
    scrape_feed,
)

SEEN_AT = "2026-11-09T00:00:00+00:00"
BASE_URL = "https://example-shop.example"


class IsWhiskyTitleTest(unittest.TestCase):
    def test_keyword_match(self):
        assert is_whisky_title("Blanton's Original Bourbon Whiskey - 70cl")
        assert is_whisky_title("Chivas Royal Salute 21 Blended Scotch Whisky")
        assert is_whisky_title("Michter's US*1 Single Barrel Rye - 70cl")
        self.assertTrue(is_whisky_title("Monkey Shoulder Whisky 700ml"))

    def test_age_statement_match(self):
        assert is_whisky_title("Redbreast 21 Years 700ml")
        assert is_whisky_title("Glenmorangie 25 Years Single Malt Whisky 750ml")
        assert is_whisky_title("Lagavulin 16 Year Old (YO) Islay Single Malt Scotch Whisky 700ml")
        self.assertTrue(is_whisky_title("Redbreast 21 Years 700ml"))

    def test_distillery_name_match(self):
        assert is_whisky_title("Yamazaki, 2022 Limited Edition NV 700ML",
                               "Spirits")
        assert is_whisky_title("Hakushu, Story of The Distillery 2024 NV 700ML",
                               "Spirits")
        self.assertTrue(is_whisky_title("Yamazaki, 2022 Limited Edition NV 700ML",
                                        "Spirits"))

    def test_wine_vetoed(self):
        assert not is_whisky_title("Leflaive, Puligny Montrachet 2019 750ML",
                                   "White Wine")
        assert not is_whisky_title("Rothbury Estate Shiraz Cabernet 750ml",
                                   "Red Wine")
        assert not is_whisky_title("Whisky Barrel Aged Red Wine 750ml",
                                   "Red Wine")
        self.assertFalse(is_whisky_title(
            "Leflaive, Puligny Montrachet 2019 750ML", "White Wine"))

    def test_excluded_items(self):
        assert not is_whisky_title("Westward Whisky masterclass")
        assert not is_whisky_title(
            "Johnnie Walker Blue Label Festive Blend Bundle A")
        assert not is_whisky_title("Whisky Tasting Event Ticket")
        self.assertFalse(is_whisky_title("Westward Whisky masterclass"))

    def test_plain_non_whisky(self):
        assert not is_whisky_title("Glen Carlou Pinot Noir 2020 750ml",
                                   "Red Wine")
        assert not is_whisky_title("Hendrick's Gin 700ml", "Gin")
        self.assertFalse(is_whisky_title("Hendrick's Gin 700ml", "Gin"))


class ParseProductsJsonTest(unittest.TestCase):
    def _data(self):
        return {"products": [
            {"title": "Jack Daniel's 10 Year Old Tennessee Whiskey - 70cl",
             "handle": "jack-daniels-10",
             "vendor": "Jack Daniel's",
             "product_type": "Whiskey",
             "variants": [{"price": "152.00", "compare_at_price": None,
                           "available": True}]},
            {"title": "Westward Whisky masterclass",
             "handle": "westward-masterclass",
             "vendor": "Westward",
             "product_type": "Whisky",
             "variants": [{"price": "80.00", "compare_at_price": None,
                           "available": False}]},
            {"title": "Leflaive, Puligny Montrachet 2019 750ML",
             "handle": "leflaive-puligny-2019",
             "vendor": "Leflaive",
             "product_type": "White Wine",
             "variants": [{"price": "450.00", "compare_at_price": None,
                           "available": True}]},
            {"title": "No Price Whisky 700ml",
             "handle": "no-price-whisky",
             "vendor": "Test",
             "product_type": "Whisky",
             "variants": [{"price": "", "compare_at_price": None,
                           "available": True}]},
        ]}

    def test_keeps_whisky_skips_rest(self):
        listings, has_next, seen, skipped = parse_products_json(
            self._data(), BASE_URL, "TestShop", SEEN_AT)
        assert len(listings) == 1, "only the JD 10 is a whisky bottle"
        assert seen == 4
        assert skipped == 3
        assert has_next is False
        listing = listings[0]
        assert listing.price_sgd == 152.00
        assert listing.size_ml == 700
        assert listing.size_verified is True
        assert listing.in_stock is True
        assert listing.url == BASE_URL + "/products/jack-daniels-10"
        self.assertEqual(listing.price_sgd, 152.00)

    def test_full_page_has_next(self):
        data = {"products": [
            {"title": "Test Whisky %d 700ml" % n, "handle": "test-%d" % n,
             "vendor": "Test", "product_type": "Whisky",
             "variants": [{"price": "100.00", "available": True}]}
            for n in range(250)]}
        listings, has_next, seen, skipped = parse_products_json(
            data, BASE_URL, "TestShop", SEEN_AT)
        assert has_next is True
        assert len(listings) == 250
        self.assertTrue(has_next)


class FetchJsonTest(unittest.TestCase):
    def test_unreachable_returns_none(self):
        result = fetch_json("http://127.0.0.1:1/products.json")
        assert result is None
        self.assertIsNone(result)


class ScrapeFeedDriverTest(unittest.TestCase):
    def test_driver_against_local_fixture_server(self):
        fixtures_dir = os.path.join(os.path.dirname(__file__), "fixtures")
        handler = functools.partial(
            http.server.SimpleHTTPRequestHandler, directory=fixtures_dir)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        assert server.server_address[1] > 0, "server must bind a port"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            feed = ("http://127.0.0.1:%d/tlc-products.json?limit=250"
                    % port)
            listings, health = asyncio.run(scrape_feed(
                [feed], "https://theliquidcollection.com",
                "The Liquid Collection", max_pages=2, delay_s=0,
                seen_at=SEEN_AT))
        finally:
            server.shutdown()
            server.server_close()
        assert len(listings) == 7, "7 whisky bottles from the TLC fixture"
        assert health.status == "ok"
        assert health.pages_scanned == 1
        assert health.items_found == 7
        assert listings[0].price_sgd > 0
        self.assertEqual(health.status, "ok")


if __name__ == "__main__":
    unittest.main()
