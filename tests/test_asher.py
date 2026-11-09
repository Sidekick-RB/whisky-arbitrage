"""Unit tests for the Asher parser, using a real-data fixture.

Fixture: tests/fixtures/asher-products.json — real records trimmed
from the live all-products feed (4000+ products), fetched 9 Nov
2026. Covers the no-keyword title ("Redbreast 21 Years 700ml",
caught by the age rule), the bundle exclusion and the wine veto.
"""

import json
import os
import unittest

from hunt.parsers.asher import parse_feed_json

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "asher-products.json")
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
        assert len(listings) == 6, "6 bottles; bundle + wine skipped"
        assert seen == 8
        assert skipped == 2
        assert has_next is False
        by_name = {item.name: item for item in listings}
        assert by_name["Chivas Regal 25 Years Blended Whisky 700ml"].price_sgd == 309.00
        assert by_name["Redbreast 21 Years 700ml"].price_sgd == 358.00
        assert by_name["Redbreast 21 Years 700ml"].canonical == "redbreast-21yr"
        assert by_name["Hibiki 30 Years Blended Whisky 700ml"].price_sgd == 5888.00
        assert by_name["Glenmorangie 25 Years Single Malt Whisky 750ml"].price_sgd == 699.00
        assert by_name["Glenmorangie 25 Years Single Malt Whisky 750ml"].size_ml == 750
        assert by_name["Singleton 15 Years Single Malt Whisky 700ml"].price_sgd == 85.00
        assert by_name["Born Irish Whisky 80 Proof 700ml"].price_sgd == 75.00
        self.assertEqual(len(listings), 6)

    def test_bundle_and_wine_excluded(self):
        listings, _, _, _ = parse_feed_json(_load(), SEEN_AT)
        names = [item.name for item in listings]
        assert not any("Bundle" in name for name in names)
        assert not any("Rothbury" in name for name in names)
        self.assertNotIn("Rothbury Estate Premium Selection Shiraz Cabernet RV 750ml", names)


if __name__ == "__main__":
    unittest.main()
