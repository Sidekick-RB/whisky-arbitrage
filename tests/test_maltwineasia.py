"""Unit tests for the Malt & Wine Asia parser, using a real-data fixture.

Fixture: tests/fixtures/maltwineasia-products.json — real records
trimmed from the live WooCommerce Store API, fetched 10 Nov 2026:
Macallan Sherry Oak 18 2022 Release (S$650) and 2020 Release
(S$680), a Samaroli Macallan 32, and a Château Beychevelle 2019
wine (skipped by the whisky filter). Only the fields the parser
reads are kept (name, permalink, prices, is_in_stock, attributes,
categories).
"""

import json
import os
import unittest

from hunt.parsers.maltwineasia import parse_products_json

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "maltwineasia-products.json")
SEEN_AT = "2026-11-10T00:00:00+00:00"


def _load():
    assert os.path.exists(FIXTURE), "fixture missing: " + FIXTURE
    with open(FIXTURE, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    assert isinstance(data, list) and data, "fixture must be a list"
    return data


class FixtureTest(unittest.TestCase):
    def test_known_values(self):
        listings, has_next, seen, skipped = parse_products_json(
            _load(), SEEN_AT)
        assert len(listings) == 3
        assert seen == 4
        assert skipped == 1
        assert has_next is False
        by_name = {item.name: item for item in listings}
        macallan_2020 = by_name[
            "The Macallan Sherry Oak 18 Years Old, 2020 Release"]
        assert macallan_2020.price_sgd == 680.00
        assert macallan_2020.size_ml == 700
        assert macallan_2020.size_verified is True
        assert macallan_2020.in_stock is True
        assert macallan_2020.url == (
            "https://maltwineasia.com/product/"
            "the-macallan-sherry-oak-18-years-old-2020-release/")
        assert by_name[
            "The Macallan Sherry Oak 18 Years Old, 2022 Release"
        ].price_sgd == 650.00
        self.assertEqual(macallan_2020.price_sgd, 680.00)
        self.assertEqual(macallan_2020.size_ml, 700)

    def test_wine_excluded_by_category(self):
        listings, _, _, _ = parse_products_json(_load(), SEEN_AT)
        names = [item.name for item in listings]
        assert "Château Beychevelle 2019" not in names
        self.assertNotIn("Château Beychevelle 2019", names)

    def test_zero_price_skipped(self):
        # 10 Nov 2026: the slice-4 validation run crashed on
        # AssertionError "price must be positive" — the catalogue
        # lists tasting events at price "0" (e.g. "Italian Wine
        # Tasting" on page 5). A zero price is not a price and must
        # never crash the parser.
        event = [{
            "name": "Italian Wine Tasting",
            "permalink": "https://maltwineasia.com/product/italian-wine-tasting/",
            "prices": {"price": "0", "currency_code": "SGD",
                       "currency_minor_unit": 2},
            "is_in_stock": True,
            "attributes": [{"name": "Volume",
                            "terms": [{"name": "750 ML"}]}],
            "categories": [{"name": "Wine"}],
        }]
        listings, _, seen, skipped = parse_products_json(event, SEEN_AT)
        assert len(listings) == 0
        assert seen == 1
        assert skipped == 1
        self.assertEqual(len(listings), 0)


if __name__ == "__main__":
    unittest.main()
