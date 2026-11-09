"""Unit tests for the KrisShop parser, using real-data fixtures.

Fixtures: tests/fixtures/krisshop-search-*.json — real GraphQL
responses from https://kscommerce.krisshop.com/graphql, fetched
9 Nov 2026 (whisky search page 1, wildmoor search, woodford search,
segment search). Prices are the traveller (flight) prices; the
home-delivery variant is not returned by this endpoint.
"""

import json
import os
import unittest

from hunt.parsers.krisshop import parse_response

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
SEEN_AT = "2026-11-09T00:00:00+00:00"


def _load(name):
    path = os.path.join(FIXTURES, name)
    assert os.path.exists(path), "fixture missing: " + path
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    assert data.get("data"), "fixture must carry data"
    return data


class FixtureTest(unittest.TestCase):
    def test_woodford_baccarat(self):
        listings, total_pages, seen, skipped = parse_response(
            _load("krisshop-search-woodford.json"), SEEN_AT)
        assert len(listings) == 1
        assert seen == 1
        assert skipped == 0
        assert total_pages == 1
        baccarat = listings[0]
        assert baccarat.price_sgd == 1590.40
        assert baccarat.size_ml == 700
        assert baccarat.in_stock is True
        assert baccarat.traveller_only is True
        assert baccarat.eligibility == "Pre-order to flight"
        assert baccarat.url == ("https://www.krisshop.com/en/product/"
                                "180753CF-model/180753cf-model")
        self.assertEqual(baccarat.price_sgd, 1590.40)
        self.assertTrue(baccarat.traveller_only)

    def test_wildmoor_40(self):
        listings, total_pages, seen, skipped = parse_response(
            _load("krisshop-search-wildmoor.json"), SEEN_AT)
        assert len(listings) == 3
        assert seen == 3
        assert skipped == 0
        by_name = {item.name: item for item in listings}
        wildmoor = by_name["WILDMOOR 40 BLACK MOUNTAIN"]
        assert wildmoor.price_sgd == 968.00
        assert wildmoor.size_ml is None
        assert wildmoor.size_verified is False
        assert wildmoor.traveller_only is True
        assert by_name["WILDMOOR 23 WAKING FOREST"].price_sgd == 204.00
        assert by_name["WILDMOOR 30 TROPICAL COAST"].price_sgd == 417.60
        self.assertEqual(wildmoor.price_sgd, 968.00)
        self.assertIsNone(wildmoor.size_ml)

    def test_segment_traveller_flag(self):
        listings, total_pages, seen, skipped = parse_response(
            _load("krisshop-search-segment.json"), SEEN_AT)
        assert seen == 5
        assert skipped == 0
        assert len(listings) == 5
        by_sku = {item.url.split("/")[5]: item for item in listings}
        select = by_sku["K366655"]
        assert select.price_sgd == 52.00
        assert select.size_ml == 1000
        assert select.traveller_only is True
        assert select.eligibility == "Pre-order to flight"
        double_oaked = by_sku["K359313"]
        assert double_oaked.price_sgd == 89.10
        assert double_oaked.traveller_only is True
        assert double_oaked.eligibility == "Pre-order to flight"
        dp_select = by_sku["K366655DP"]
        assert dp_select.price_sgd == 102.00
        assert dp_select.traveller_only is False
        assert dp_select.eligibility == "Singapore delivery (pre-order)"
        self.assertTrue(select.traveller_only)
        self.assertFalse(dp_select.traveller_only)

    def test_whisky_page_filters(self):
        listings, total_pages, seen, skipped = parse_response(
            _load("krisshop-search-whisky-p1.json"), SEEN_AT)
        assert seen == 5
        assert skipped == 1, "the decanter is the only skip"
        assert len(listings) == 4
        assert total_pages == 125
        by_name = {item.name: item for item in listings}
        assert by_name["SHINOBU 15YO PURE MALT WHISKY 0.7L DRAGON EDITION"].price_sgd == 215.20
        assert by_name["SHINOBU 15YO PURE MALT WHISKY 0.7L DRAGON EDITION"].size_ml == 700
        assert by_name["BALLANTINE'S FINEST BLENDED SCOTCH WHISKY 1L"].price_sgd == 30.00
        assert by_name["BALLANTINE'S FINEST BLENDED SCOTCH WHISKY 1L"].size_ml == 1000
        assert by_name["JURA SINGLE MALT SCOTCH WHISKY 18 YEARS OLD"].size_ml is None
        bunnahabhain = by_name["BUNNAHABHAIN 25YO WHISKY 0.7L"]
        assert bunnahabhain.price_sgd == 891.00
        assert bunnahabhain.traveller_only is False
        assert bunnahabhain.eligibility == "Singapore delivery (pre-order)"
        assert not any("DECANTER" in name for name in by_name)
        self.assertEqual(len(listings), 4)


if __name__ == "__main__":
    unittest.main()
