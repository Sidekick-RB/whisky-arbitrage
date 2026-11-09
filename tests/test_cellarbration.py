"""Unit tests for the Cellarbration parser, using a synthetic fixture.

Fixture: tests/fixtures/cellarbration-whisky-p1.html — SYNTHETIC
Magento Luma markup; prices/stock are real knowns from the watchlist.
The live site returns a Cloudflare challenge to curl, so no live
fetch was possible. Live markup is unvalidated.
"""

import os
import unittest

from hunt.parsers.cellarbration import LIST_URL, parse_page_html

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "cellarbration-whisky-p1.html")
WALL = os.path.join(os.path.dirname(__file__), "fixtures",
                    "bot-wall-cloudflare.html")
SEEN_AT = "2026-11-09T00:00:00+00:00"


def _load(path):
    assert os.path.exists(path), "fixture page missing: " + path
    with open(path, "r", encoding="utf-8") as handle:
        html = handle.read()
    assert html, "fixture page must be non-empty"
    return html


class FixtureTest(unittest.TestCase):
    def test_page_parses_three_tiles(self):
        html = _load(FIXTURE)
        listings, has_next, tiles_seen = parse_page_html(
            html, LIST_URL, SEEN_AT)
        assert len(listings) == 3, "page must yield 3 listings"
        assert tiles_seen == 3, "page must show 3 tiles"
        assert has_next, "page must have a next page"
        self.assertEqual(len(listings), 3)
        self.assertTrue(has_next)

    def test_special_price_beats_regular_price(self):
        html = _load(FIXTURE)
        listings, _, _ = parse_page_html(html, LIST_URL, SEEN_AT)
        jack = [item for item in listings
                if "Jack Daniel" in item.name]
        assert len(jack) == 1, "one Jack Daniel's tile expected"
        assert jack[0].price_sgd == 129.00, "special price must win"
        assert jack[0].size_ml == 700
        assert jack[0].in_stock is True
        assert jack[0].url == ("https://cellarbration.com.sg/jack-daniels-"
                                "10yo-tennessee-whiskey-6-0-7l.html")
        self.assertEqual(jack[0].price_sgd, 129.00)

    def test_out_of_stock_tile_flagged(self):
        html = _load(FIXTURE)
        listings, _, _ = parse_page_html(html, LIST_URL, SEEN_AT)
        brackla = [item for item in listings if "Brackla" in item.name]
        assert len(brackla) == 1, "one Brackla tile expected"
        assert brackla[0].in_stock is False
        assert brackla[0].price_sgd == 279.00
        self.assertFalse(brackla[0].in_stock)

    def test_bot_wall_raises_blocked(self):
        html = _load(WALL)
        with self.assertRaises(ValueError) as caught:
            parse_page_html(html, LIST_URL, SEEN_AT)
        assert str(caught.exception).startswith("blocked:cloudflare")
        self.assertIn("cloudflare", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
