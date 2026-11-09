"""Unit tests for the Paneco parser, using a saved fixture page.

Fixture: tests/fixtures/paneco-sale-p1.html, the live X-mas Spirits Sale
page 1 fetched 9 Nov 2026 (8 products, 30 pages of pagination).
"""

import os
import unittest

from hunt.parsers.base import parse_price_sgd, parse_size_ml
from hunt.parsers.paneco import (
    BASE_URL,
    SALE_URL,
    page_no_from_url,
    parse_page_html,
)

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "paneco-sale-p1.html")


def _load_fixture():
    assert os.path.exists(FIXTURE), "fixture page missing"
    with open(FIXTURE, "r", encoding="utf-8") as handle:
        html = handle.read()
    assert html, "fixture page must be non-empty"
    return html


class ParseHelpersTest(unittest.TestCase):
    def test_parse_price(self):
        assert parse_price_sgd("$44.95") == 44.95
        assert parse_price_sgd("S$1,234.56") == 1234.56
        assert parse_price_sgd("44.95") == 44.95
        self.assertEqual(parse_price_sgd("$44.95"), 44.95)
        self.assertEqual(parse_price_sgd("S$1,234.56"), 1234.56)

    def test_parse_price_none(self):
        assert parse_price_sgd("free") is None
        assert parse_price_sgd("") is None
        # 9 Nov 2026: Spirited prices event tickets "0.00" — a zero
        # price is not a price, and must never crash the run.
        assert parse_price_sgd("0.00") is None
        assert parse_price_sgd("$0") is None
        assert parse_price_sgd("0") is None
        self.assertIsNone(parse_price_sgd("free"))
        self.assertIsNone(parse_price_sgd(""))
        self.assertIsNone(parse_price_sgd("0.00"))

    def test_parse_size(self):
        assert parse_size_ml("700ml") == 700
        assert parse_size_ml("1L") == 1000
        assert parse_size_ml("750ml") == 750
        assert parse_size_ml("70cl") == 700
        assert parse_size_ml("50cl") == 500
        assert parse_size_ml("1.5L") == 1500
        self.assertEqual(parse_size_ml("700ml"), 700)
        self.assertEqual(parse_size_ml("1L"), 1000)

    def test_parse_size_none(self):
        assert parse_size_ml("nope") is None
        assert parse_size_ml("") is None
        self.assertIsNone(parse_size_ml("nope"))

    def test_page_no(self):
        assert page_no_from_url(SALE_URL) == 1
        assert page_no_from_url(SALE_URL + "?page=3") == 3
        self.assertEqual(page_no_from_url(SALE_URL), 1)
        self.assertEqual(page_no_from_url(SALE_URL + "?page=3"), 3)


class FixtureTest(unittest.TestCase):
    def test_page_one_parses_eight_products(self):
        html = _load_fixture()
        listings, has_next, tiles_seen = parse_page_html(
            html, SALE_URL, "2026-11-09T00:00:00+00:00")
        assert len(listings) == 8, "page 1 must yield 8 listings"
        assert tiles_seen == 8, "page 1 must show 8 tiles"
        assert has_next, "page 1 must have a next page"
        self.assertEqual(len(listings), 8)
        self.assertTrue(has_next)

    def test_known_listing_values(self):
        html = _load_fixture()
        listings, _, _ = parse_page_html(
            html, SALE_URL, "2026-11-09T00:00:00+00:00")
        by_name = {item.name: item for item in listings}
        assert len(by_name) == 8, "listing names must be unique on a page"
        baileys = by_name.get("Baileys Strawberries & Cream 700ml")
        assert baileys is not None, "Baileys listing must be parsed"
        assert baileys.price_sgd == 44.95
        assert baileys.size_ml == 700
        assert baileys.size_verified
        assert baileys.in_stock
        assert baileys.retailer == "Paneco"
        assert baileys.canonical == "baileys-strawberries-cream"
        assert baileys.url.startswith(BASE_URL + "/products/")
        self.assertEqual(baileys.price_sgd, 44.95)
        self.assertEqual(baileys.canonical, "baileys-strawberries-cream")

    def test_every_listing_is_well_formed(self):
        html = _load_fixture()
        listings, _, _ = parse_page_html(
            html, SALE_URL, "2026-11-09T00:00:00+00:00")
        for item in listings:
            assert item.price_sgd > 0, "price must be positive"
            assert item.size_ml in (700, 750, 1000), "unexpected size"
            assert item.url.startswith(BASE_URL), "URL must stay on Paneco"
            assert item.canonical, "canonical must be non-empty"
        assert len(listings) == 8
        self.assertEqual(len(listings), 8)

    def test_gift_box_bottle_parsed(self):
        html = _load_fixture()
        listings, _, _ = parse_page_html(
            html, SALE_URL, "2026-11-09T00:00:00+00:00")
        names = [item.name for item in listings]
        assert any("Bushmills 16" in name for name in names)
        assert any("Glen Moray 18" in name for name in names)
        self.assertTrue(any("Bushmills 16" in name for name in names))


class EmptyPageTest(unittest.TestCase):
    def test_empty_html_rejected(self):
        with self.assertRaises(AssertionError):
            parse_page_html("", SALE_URL, "2026-11-09T00:00:00+00:00")

    def test_no_tiles_no_next(self):
        listings, has_next, tiles_seen = parse_page_html(
            "<html><body>no products here</body></html>",
            SALE_URL, "2026-11-09T00:00:00+00:00")
        assert listings == [] and tiles_seen == 0
        assert not has_next
        self.assertEqual(listings, [])
        self.assertFalse(has_next)


if __name__ == "__main__":
    unittest.main()
