"""Unit tests for the LMDW parser, using a real-data fixture.

Fixture: tests/fixtures/lmdw-products.json — real records trimmed
from the live WooCommerce Store API (per_page=10), fetched 9 Nov
2026. Only the fields the parser reads are kept (name, permalink,
prices, is_in_stock, attributes, categories).
"""

import json
import os
import unittest

from hunt.parsers.lmdw import parse_products_json

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "lmdw-products.json")
SEEN_AT = "2026-11-09T00:00:00+00:00"


def _load():
    assert os.path.exists(FIXTURE), "fixture missing: " + FIXTURE
    with open(FIXTURE, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    assert isinstance(data, list) and data, "fixture must be a list"
    return data


class FixtureTest(unittest.TestCase):
    def test_known_values(self):
        listings, has_next, seen, skipped = parse_products_json(_load(), SEEN_AT)
        assert len(listings) == 3
        assert seen == 3
        assert skipped == 0
        assert has_next is False
        by_name = {item.name: item for item in listings}
        tormore = by_name["TORMORE 16 ans"]
        assert tormore.price_sgd == 180.00
        assert tormore.size_ml == 700
        assert tormore.size_verified is True
        assert tormore.in_stock is True
        assert tormore.url == "https://www.whisky.sg/product/tormore-16-ans/"
        assert by_name["CAOL ILA 12 ans 2011 Elixir Distillers"].price_sgd == 185.00
        self.assertEqual(tormore.price_sgd, 180.00)
        self.assertEqual(tormore.size_ml, 700)

    def test_out_of_stock_flagged(self):
        listings, _, _, _ = parse_products_json(_load(), SEEN_AT)
        by_name = {item.name: item for item in listings}
        port_ellen = by_name["PORT ELLEN 14 ans 1983 Coopers Choice Lot8224 The Vintage Malt Whisky Co Ltd"]
        assert port_ellen.in_stock is False
        assert port_ellen.price_sgd == 1730.00
        self.assertFalse(port_ellen.in_stock)

    def test_rum_excluded_by_category(self):
        rum = [{
            "name": "NEISSON Profil Equilibre Rum 70cl",
            "permalink": "https://www.whisky.sg/product/neisson-rum/",
            "prices": {"price": "13800", "currency_code": "SGD",
                       "currency_minor_unit": 2},
            "is_in_stock": True,
            "attributes": [{"name": "Volume",
                            "terms": [{"name": "70cl"}]}],
            "categories": [{"name": "Rum"}],
        }]
        listings, _, seen, skipped = parse_products_json(rum, SEEN_AT)
        assert len(listings) == 0
        assert seen == 1
        assert skipped == 1
        self.assertEqual(len(listings), 0)

    def test_non_sgd_skipped(self):
        other = [{
            "name": "TORMORE 16 ans",
            "permalink": "https://www.whisky.sg/product/tormore-16-ans/",
            "prices": {"price": "18000", "currency_code": "EUR",
                       "currency_minor_unit": 2},
            "is_in_stock": True,
            "attributes": [{"name": "Volume",
                            "terms": [{"name": "70cl"}]}],
            "categories": [{"name": "Scotch Whisky"}],
        }]
        listings, _, _, _ = parse_products_json(other, SEEN_AT)
        assert len(listings) == 0
        self.assertEqual(len(listings), 0)

    def test_zero_volume_is_unverified_size(self):
        # 10 Nov 2026: eight LMDW gift sets/merch items carry Volume
        # "0cl" (e.g. TEELING BLACK HIPFLASK IN GIFTBOX). The old
        # parse_size_ml assertion crashed the whole parser on them;
        # a zero size now yields an unverified size, never a crash.
        merch = [{
            "name": "TEELING BLACK HIPFLASK IN GIFTBOX",
            "permalink": "https://www.whisky.sg/product/teeling-hipflask/",
            "prices": {"price": "4500", "currency_code": "SGD",
                       "currency_minor_unit": 2},
            "is_in_stock": True,
            "attributes": [{"name": "Volume",
                            "terms": [{"name": "0cl"}]}],
            "categories": [{"name": "Irish Whiskey"}],
        }]
        listings, _, _, _ = parse_products_json(merch, SEEN_AT)
        assert len(listings) == 1
        assert listings[0].size_ml is None
        assert listings[0].size_verified is False
        self.assertIsNone(listings[0].size_ml)

    def test_zero_price_skipped(self):
        # 10 Nov 2026: the same "must be positive" assertion shape
        # crashed the Malt & Wine Asia parser on a zero-price tasting
        # event. A zero price is not a price and must never crash.
        event = [{
            "name": "LMDW Tasting Event",
            "permalink": "https://www.whisky.sg/product/tasting-event/",
            "prices": {"price": "0", "currency_code": "SGD",
                       "currency_minor_unit": 2},
            "is_in_stock": True,
            "attributes": [{"name": "Volume",
                            "terms": [{"name": "70cl"}]}],
            "categories": [{"name": "Scotch Whisky"}],
        }]
        listings, _, seen, skipped = parse_products_json(event, SEEN_AT)
        assert len(listings) == 0
        assert seen == 1
        assert skipped == 1
        self.assertEqual(len(listings), 0)


if __name__ == "__main__":
    unittest.main()
