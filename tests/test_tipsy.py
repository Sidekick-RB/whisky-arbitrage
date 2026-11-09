"""Unit tests for the Tipsy Topsy parser, using a real-data fixture.

Fixture: tests/fixtures/tipsy-products.json — real records trimmed
from the live all-products feed (494 products), fetched 9 Nov 2026.
The whisky-collection feed is not used: it omits sold-out items.
"""

import json
import os
import unittest

from hunt.parsers.tipsy import parse_feed_json

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "tipsy-products.json")
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
        blue = by_name["Johnnie Walker Blue Label Blended Scotch Whisky 1000ml with Gift Box"]
        assert blue.price_sgd == 280.00
        assert blue.size_ml == 1000
        assert blue.in_stock is True
        assert by_name["Monkey Shoulder Whisky 700ml"].price_sgd == 76.00
        assert by_name["Lagavulin 16 Year Old (YO) Islay Single Malt Scotch Whisky 700ml"].price_sgd == 150.00
        self.assertEqual(blue.price_sgd, 280.00)
        self.assertEqual(blue.size_ml, 1000)

    def test_sold_out_flagged(self):
        listings, _, _, _ = parse_feed_json(_load(), SEEN_AT)
        by_name = {item.name: item for item in listings}
        glenfarclas = by_name["Glenfarclas 105 Cask Strength Highland Single Malt Whisky 700ml with Gift box"]
        assert glenfarclas.in_stock is False
        assert glenfarclas.price_sgd == 110.00
        self.assertFalse(glenfarclas.in_stock)


if __name__ == "__main__":
    unittest.main()
