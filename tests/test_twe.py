"""Unit tests for the TWE parser, using a synthetic fixture page.

Fixture: tests/fixtures/twe-search-glenfarclas.html — SYNTHETIC, built
from the old in-repo scraper's selectors (scraper.py, Mar 2026); the
live site returns a Cloudflare challenge to curl, so no live fetch was
possible. Live markup is unvalidated.
"""

import os
import unittest

from hunt.parsers.twe import (
    match_prices,
    parse_search_html,
    search_url,
)

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "twe-search-glenfarclas.html")
WALL = os.path.join(os.path.dirname(__file__), "fixtures",
                    "bot-wall-cloudflare.html")
PAGE_URL = "https://www.thewhiskyexchange.com/search?q=glenfarclas"
SEEN_AT = "2026-11-09T00:00:00+00:00"


def _load(path):
    assert os.path.exists(path), "fixture page missing: " + path
    with open(path, "r", encoding="utf-8") as handle:
        html = handle.read()
    assert html, "fixture page must be non-empty"
    return html


class SearchUrlTest(unittest.TestCase):
    def test_search_url(self):
        url = search_url("Royal Brackla 18")
        assert url.startswith("https://www.thewhiskyexchange.com/search?q=")
        assert "Royal+Brackla+18" in url or "Royal%20Brackla" in url
        self.assertIn("Royal", url)


class FixtureTest(unittest.TestCase):
    def test_page_parses_three_cards(self):
        html = _load(FIXTURE)
        prices, has_next, tiles_seen = parse_search_html(
            html, PAGE_URL, SEEN_AT)
        assert len(prices) == 3, "page must yield 3 prices"
        assert tiles_seen == 3, "page must show 3 cards"
        assert has_next, "page must have a next page"
        self.assertEqual(len(prices), 3)
        self.assertTrue(has_next)

    def test_known_price_values(self):
        html = _load(FIXTURE)
        prices, _, _ = parse_search_html(html, PAGE_URL, SEEN_AT)
        by_name = {p.name: p for p in prices}
        assert len(by_name) == 3, "names must be distinct"
        brackla = by_name["Royal Brackla 18 Year Old Palo Cortado "
                           "Sherry Cask Finish 70cl"]
        assert brackla.price == 137.95
        assert brackla.currency == "GBP"
        assert brackla.country == "UK"
        assert brackla.size_ml == 700
        assert brackla.in_stock is True
        assert brackla.url == ("https://www.thewhiskyexchange.com/p/12345/"
                                "royal-brackla-18-year-old")
        self.assertEqual(brackla.price, 137.95)
        self.assertTrue(brackla.in_stock)

    def test_out_of_stock_card_flagged(self):
        html = _load(FIXTURE)
        prices, _, _ = parse_search_html(html, PAGE_URL, SEEN_AT)
        flagged = [p for p in prices if "Glenfarclas 25" in p.name]
        assert len(flagged) == 1, "one Glenfarclas card expected"
        assert flagged[0].in_stock is False
        assert flagged[0].price == 225.00
        self.assertFalse(flagged[0].in_stock)

    def test_bot_wall_raises_blocked(self):
        html = _load(WALL)
        with self.assertRaises(ValueError) as caught:
            parse_search_html(html, PAGE_URL, SEEN_AT)
        assert str(caught.exception).startswith("blocked:cloudflare")
        self.assertIn("cloudflare", str(caught.exception))

    def test_match_prices_by_canonical(self):
        html = _load(FIXTURE)
        prices, _, _ = parse_search_html(html, PAGE_URL, SEEN_AT)
        matched = match_prices(
            prices, "royal-brackla-18yr-palo-cortado-sherry-cask-finish")
        assert len(matched) == 1, "one Brackla match expected"
        assert matched[0].price == 137.95
        assert match_prices(prices, "macallan-18yr") == []
        self.assertEqual(len(matched), 1)


if __name__ == "__main__":
    unittest.main()
