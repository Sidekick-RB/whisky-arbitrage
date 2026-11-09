"""Unit tests for the PIVENE parser, using a real-data fixture.

Fixture: tests/fixtures/pivene-products.json — real records trimmed
from the live all-products feed (1158 products), fetched 9 Nov 2026.
Covers the wine-style titles with no whisky keyword and often no age
statement ("Yamazaki, 2022 Limited Edition NV 700ML"), caught by the
distillery-name rule in shopify.is_whisky_title.
"""

import json
import os
import unittest

from hunt.parsers.pivene import parse_feed_json

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "pivene-products.json")
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
        assert len(listings) == 5, "5 whisky bottles; 1 wine skipped"
        assert seen == 6
        assert skipped == 1
        assert has_next is False
        by_name = {item.name: item for item in listings}
        assert by_name["Yamazaki, 2022 Limited Edition NV 700ML"].price_sgd == 1080.00
        assert by_name["Yamazaki, 2022 Limited Edition NV 700ML"].size_ml == 700
        assert by_name["Hakushu, 18 Year Old NV 700ML"].price_sgd == 1047.00
        assert by_name["Chivas Regal, 18 Year Old NV 750ML"].price_sgd == 132.00
        assert by_name["Chivas Regal, 18 Year Old NV 750ML"].size_ml == 750
        assert by_name["Springbank, 12 Year Old Cask Strength NV 700ML"].price_sgd == 540.00
        self.assertEqual(len(listings), 5)

    def test_sold_out_and_wine(self):
        listings, _, _, _ = parse_feed_json(_load(), SEEN_AT)
        by_name = {item.name: item for item in listings}
        assert by_name["Hibiki, 21 Year Old NV 700ML"].in_stock is False
        assert by_name["Hibiki, 21 Year Old NV 700ML"].price_sgd == 1047.00
        assert not any("Leflaive" in name for name in by_name)
        self.assertFalse(by_name["Hibiki, 21 Year Old NV 700ML"].in_stock)


if __name__ == "__main__":
    unittest.main()
