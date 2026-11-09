"""Unit tests for the Whisky World parser, using a real-data fixture.

Fixture: tests/fixtures/whiskyworld-search-ballantines.html — two
verbatim card blocks from the live /search/ballantines-23 page,
fetched 9 Nov 2026; and tests/fixtures/whiskyworld-product-size.html
— the verbatim "Bottle Size (cl)" row from the live Ballantine's 23
product page. The out-of-stock test uses a synthetic variant of the
real card with the site's own out-of-stock marker class added (the
class exists in the site stylesheet; no live out-of-stock card was
observed, so that path is unvalidated against a real card).
"""

import os
import unittest

from hunt.parsers.whiskyworld import (
    parse_product_html,
    parse_search_html,
    search_url,
)

SEARCH = os.path.join(os.path.dirname(__file__), "fixtures",
                      "whiskyworld-search-ballantines.html")
PRODUCT = os.path.join(os.path.dirname(__file__), "fixtures",
                       "whiskyworld-product-size.html")
PAGE_URL = "https://www.thewhiskyworld.com/search/ballantines-23"
SEEN_AT = "2026-11-09T00:00:00+00:00"


def _load(path):
    assert os.path.exists(path), "fixture missing: " + path
    with open(path, "r", encoding="utf-8") as handle:
        html = handle.read()
    assert html, "fixture must be non-empty"
    return html


class SearchUrlTest(unittest.TestCase):
    def test_search_url(self):
        url = search_url("Ballantine's 23")
        assert url == "https://www.thewhiskyworld.com/search/ballantine-s-23"
        self.assertTrue(url.startswith(
            "https://www.thewhiskyworld.com/search/"))


class FixtureTest(unittest.TestCase):
    def test_two_cards_first_price_is_sale(self):
        cards, seen = parse_search_html(_load(SEARCH), PAGE_URL, SEEN_AT)
        assert len(cards) == 2
        assert seen == 2
        by_name = {name: (price, url, in_stock)
                   for name, price, url, in_stock in cards}
        assert by_name["Ballantines 23 Year Old - American Oak"][0] == 139.90
        assert by_name["Ballantines 23 Year Old - American Oak"][2] is True
        assert by_name["Ballantines 23 Year Old - American Oak"][1] == (
            "https://www.thewhiskyworld.com/"
            "ballantines-23-year-old-american-oak-p8643")
        assert by_name["Ballantines 30 Year Old"][0] == 274.90
        self.assertEqual(len(cards), 2)
        self.assertEqual(
            by_name["Ballantines 23 Year Old - American Oak"][0], 139.90)

    def test_out_of_stock_marker(self):
        html = _load(SEARCH).replace(
            'class="product__details__prices"',
            'class="product__details__prices product_zero_stock"', 1)
        cards, _ = parse_search_html(html, PAGE_URL, SEEN_AT)
        assert len(cards) == 2
        assert cards[0][3] is False
        assert cards[1][3] is True
        self.assertFalse(cards[0][3])

    def test_product_page_size(self):
        size_ml = parse_product_html(_load(PRODUCT))
        assert size_ml == 700
        self.assertEqual(size_ml, 700)

    def test_product_page_no_size(self):
        assert parse_product_html("<html><body>no size here</body></html>") is None
        self.assertIsNone(parse_product_html("<html></html>"))


if __name__ == "__main__":
    unittest.main()
