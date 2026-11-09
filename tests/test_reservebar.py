"""Unit tests for the ReserveBar parser, using real-data fixtures.

Fixture: tests/fixtures/reservebar-product-oban14.html — the verbatim
JSON-LD block plus the verbatim "750 ML" size span from the live
Oban 14 product page, fetched 9 Nov 2026; and
tests/fixtures/reservebar-sitemap-excerpt.xml — a small excerpt of
the live sitemap (real product URLs) for the slug matcher. The
sitemap test serves the excerpt over a local HTTP server (same
pattern as test_paneco_live.py).
"""

import functools
import http.server
import os
import threading
import unittest

from hunt.parsers.reservebar import (
    load_sitemap,
    match_sitemap,
    parse_product_html,
    slug_matches,
)

PRODUCT = os.path.join(os.path.dirname(__file__), "fixtures",
                       "reservebar-product-oban14.html")
SITEMAP = os.path.join(os.path.dirname(__file__), "fixtures",
                       "reservebar-sitemap-excerpt.xml")
PAGE_URL = ("https://reservebar.com/products/"
            "oban-14-years-old-single-malt-whisky/GROUPING-34985")
SEEN_AT = "2026-11-09T00:00:00+00:00"


def _load(path):
    assert os.path.exists(path), "fixture missing: " + path
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    assert text, "fixture must be non-empty"
    return text


class SlugMatchTest(unittest.TestCase):
    def test_matches(self):
        assert slug_matches("Oban 14", PAGE_URL)
        assert slug_matches("Jack Daniel's 10",
                           "https://www.reservebar.com/products/"
                           "jack-daniels-10-year-old-tennessee-whiskey/"
                           "GROUPING-1")
        assert slug_matches("Royal Brackla 18",
                           "https://www.reservebar.com/products/"
                           "royal-brackla-18-year-old/GROUPING-2")
        self.assertTrue(slug_matches("Oban 14", PAGE_URL))

    def test_rejects(self):
        assert not slug_matches("Macallan 18", PAGE_URL)
        assert not slug_matches("Oban 12", PAGE_URL)
        self.assertFalse(slug_matches("Macallan 18", PAGE_URL))


class FixtureTest(unittest.TestCase):
    def test_product_page_parses(self):
        price = parse_product_html(_load(PRODUCT), PAGE_URL, SEEN_AT)
        assert price is not None
        assert price.name == "OBAN 14 YEARS OLD SINGLE MALT WHISKY"
        assert price.price == 68.98
        assert price.currency == "USD"
        assert price.country == "US"
        assert price.size_ml == 750
        assert price.in_stock is True
        assert price.url == PAGE_URL
        self.assertEqual(price.price, 68.98)
        self.assertEqual(price.size_ml, 750)

    def test_missing_jsonld_returns_none(self):
        assert parse_product_html("<html><body>no data</body></html>",
                                  PAGE_URL, SEEN_AT) is None
        self.assertIsNone(parse_product_html(
            "<html></html>", PAGE_URL, SEEN_AT))

    def test_sitemap_excerpt(self):
        fixtures_dir = os.path.join(os.path.dirname(__file__), "fixtures")
        handler = functools.partial(
            http.server.SimpleHTTPRequestHandler, directory=fixtures_dir)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        assert server.server_address[1] > 0, "server must bind a port"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            url = ("http://127.0.0.1:%d/reservebar-sitemap-excerpt.xml"
                   % port)
            urls = load_sitemap(url)
        finally:
            server.shutdown()
            server.server_close()
        assert urls is not None
        assert len(urls) == 4, "4 product URLs, collections URL excluded"
        assert all("/products/" in url for url in urls)
        assert urls[0].endswith("/GROUPING-34985")
        self.assertEqual(len(urls), 4)


class MatchSitemapTest(unittest.TestCase):
    def test_match_from_list(self):
        urls = [
            "https://www.reservebar.com/products/oban-14-years-old-single-malt-whisky/GROUPING-34985",
            "https://www.reservebar.com/products/the-macallan-fine-oak-15-years-old/GROUPING-804848",
            "https://www.reservebar.com/products/the-macallan-classic-cut-2022/GROUPING-1961657",
        ]
        matched = match_sitemap("Macallan 15", urls)
        assert len(matched) == 1
        assert "fine-oak-15" in matched[0]
        assert match_sitemap("Oban 14", urls) == [urls[0]]
        assert match_sitemap("Glenfarclas 25", urls) == []
        self.assertEqual(len(matched), 1)


if __name__ == "__main__":
    unittest.main()
