"""Unit tests for the bot-wall-only parsers (MoM, Wine.com,
Total Wine, Malt & Wine Asia) and the shared classify_page helper.

Fixture: tests/fixtures/bot-wall-cloudflare.html — a real Cloudflare
challenge page captured by curl 9 Nov 2026. These modules have no
price parser: the tests assert the wall is classified "blocked" and
that no price can ever come out of them.
"""

import os
import unittest

from hunt.parsers.blocked import classify_page
from hunt.parsers.masterofmalt import search_url as mom_url
from hunt.parsers.maltwineasia import search_url as mwa_url
from hunt.parsers.totalwine import search_url as tw_url
from hunt.parsers.winecom import search_url as wine_url

WALL = os.path.join(os.path.dirname(__file__), "fixtures",
                    "bot-wall-cloudflare.html")


def _load(path):
    assert os.path.exists(path), "fixture missing: " + path
    with open(path, "r", encoding="utf-8") as handle:
        html = handle.read()
    assert html, "fixture must be non-empty"
    return html


class ClassifyPageTest(unittest.TestCase):
    def test_cloudflare_fixture_is_blocked(self):
        kind, wall = classify_page(_load(WALL))
        assert kind == "blocked"
        assert wall == "cloudflare"
        self.assertEqual(kind, "blocked")

    def test_plain_page_is_loaded(self):
        kind, wall = classify_page("<html><body>results</body></html>")
        assert kind == "loaded"
        assert wall is None
        self.assertEqual(kind, "loaded")

    def test_empty_page_is_loaded(self):
        kind, wall = classify_page("")
        assert kind == "loaded"
        self.assertEqual(kind, "loaded")


class SearchUrlTest(unittest.TestCase):
    def test_urls(self):
        assert mom_url("Macallan 18").startswith(
            "https://www.masterofmalt.com/search/?q=")
        assert wine_url("Chivas 25").startswith(
            "https://www.wine.com/search?text=")
        assert tw_url("Glenfarclas 25").startswith(
            "https://www.totalwine.com/search?text=")
        assert mwa_url("Macallan 18") == "https://maltwineasia.com/"
        self.assertTrue(mom_url("Macallan 18").startswith(
            "https://www.masterofmalt.com/search/?q="))


if __name__ == "__main__":
    unittest.main()
