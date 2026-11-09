"""Unit tests for the TLC parser, using a real-data fixture.

Fixture: tests/fixtures/tlc-products.json — real records trimmed from
the live bourbon-american-whiskey feed (34 products, complete) and
the whisky feed, fetched 9 Nov 2026. Only the fields the parser
reads are kept (title, handle, vendor, product_type, tags, variants).
"""

import json
import os
import unittest

from hunt.parsers.tlc import parse_feed_json

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "tlc-products.json")
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
        assert len(listings) == 7, "7 whisky bottles, 1 masterclass skipped"
        assert seen == 8
        assert skipped == 1
        assert has_next is False
        by_name = {item.name: item for item in listings}
        jd10 = by_name["Jack Daniel's 10 Year Old Tennessee Whiskey - 70cl"]
        assert jd10.price_sgd == 152.00
        assert jd10.size_ml == 700
        assert jd10.in_stock is True
        assert jd10.url == ("https://theliquidcollection.com/products/"
                            "jack-daniels-10-year-old-tennessee-whiskey-70cl")
        assert by_name["Blanton's Original Bourbon Whisky – 70cl"].price_sgd == 125.00
        assert by_name["Blanton's Gold Edition - 70cl"].price_sgd == 178.00
        assert by_name["Chivas Royal Salute 21 Blended Scotch Whisky – 70cl"].price_sgd == 165.00
        assert by_name["Yamazaki 12 Year Old Single Malt Japanese Whisky - 70cl"].price_sgd == 239.00
        self.assertEqual(jd10.price_sgd, 152.00)
        self.assertEqual(len(listings), 7)

    def test_masterclass_excluded(self):
        listings, _, _, _ = parse_feed_json(_load(), SEEN_AT)
        names = [item.name for item in listings]
        assert not any("masterclass" in name.lower() for name in names)
        assert not any("Straight From The Barrel" in name for name in names)
        self.assertNotIn("Westward Whisky masterclass", names)


if __name__ == "__main__":
    unittest.main()
