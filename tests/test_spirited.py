"""Unit tests for the Spirited parser, using a real-data fixture.

Fixture: tests/fixtures/spirited-products.json — real records trimmed
from the live all-products feed (167 products), fetched 9 Nov 2026.
Spirited has no size anywhere in the tile or JSON, so every listing
has size_ml None (unverified), never an assumed size.
"""

import json
import os
import unittest

from hunt.parsers.spirited import parse_feed_json

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "spirited-products.json")
SEEN_AT = "2026-11-09T00:00:00+00:00"


def _load():
    assert os.path.exists(FIXTURE), "fixture missing: " + FIXTURE
    with open(FIXTURE, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    assert data.get("products"), "fixture must carry products"
    return data


class FixtureTest(unittest.TestCase):
    def test_known_values(self):
        listings, has_next, seen, skipped = parse_feed_json(_load(), SEEN_AT)
        assert len(listings) == 4
        assert seen == 4
        assert skipped == 0
        assert has_next is False
        by_name = {item.name: item for item in listings}
        blanton = by_name["Blanton's Original Single Barrel Bourbon Whiskey"]
        assert blanton.price_sgd == 145.00
        assert blanton.in_stock is True
        assert blanton.size_ml is None
        assert blanton.size_verified is False
        weller = by_name["W L Weller 12 Year Old Kentucky Straight Bourbon Whiskey"]
        assert weller.price_sgd == 235.00
        assert weller.in_stock is True
        self.assertEqual(blanton.price_sgd, 145.00)
        self.assertIsNone(blanton.size_ml)

    def test_sold_out_flagged(self):
        listings, _, _, _ = parse_feed_json(_load(), SEEN_AT)
        by_name = {item.name: item for item in listings}
        assert by_name["High West Campfire"].in_stock is False
        assert by_name["High West Campfire"].price_sgd == 150.00
        assert by_name["High West Double Rye"].in_stock is False
        self.assertFalse(by_name["High West Campfire"].in_stock)

    def test_zero_price_product_skipped(self):
        # Live 9 Nov 2026: Spirited's feed prices the Ardbeg Day 2024
        # event "0.00". Its title passes the whisky filter (Ardbeg +
        # "Whisky Fest" type), so it reached parse_price and the old
        # assertion crashed the first Fargate run. It must be skipped.
        data = {"products": [
            {"title": "Ardbeg Day 2024 - Ardbeg Spectacular Circus",
             "product_type": "Whisky Fest",
             "handle": "ardbeg-day-2024-ardbeg-spectacular-circus",
             "variants": [{"price": "0.00", "available": False}]},
            {"title": "Blanton's Original Single Barrel Bourbon Whiskey",
             "product_type": "Bourbon",
             "handle": "blantons-original",
             "variants": [{"price": "145.00", "available": True}]},
        ]}
        listings, has_next, seen, skipped = parse_feed_json(data, SEEN_AT)
        assert len(listings) == 1
        assert seen == 2
        assert skipped == 1
        assert has_next is False
        assert listings[0].name.startswith("Blanton's")
        assert listings[0].price_sgd == 145.00
        self.assertEqual(len(listings), 1)
        self.assertEqual(skipped, 1)


if __name__ == "__main__":
    unittest.main()
