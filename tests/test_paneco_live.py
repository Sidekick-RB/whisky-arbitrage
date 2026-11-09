"""Paneco driver tests.

Two levels:
- LocalServerTest (always runs): serves the saved fixture page over a
  local HTTP server (localhost bypasses any egress proxy) and drives the
  real Playwright code path end to end: goto, wait, content, parse,
  health. Read-only.
- PanecoLiveTest (skipped unless HUNT_LIVE=1): hits the real Paneco
  sale page. Needs direct internet: headless Chromium cannot egress
  through an authenticated proxy that drops unauthenticated CONNECTs,
  so this test fails in sandboxed environments with such a proxy even
  though the same code works with direct internet (e.g. AWS Fargate).
"""

import asyncio
import functools
import http.server
import os
import threading
import unittest

from playwright.async_api import async_playwright
from playwright_stealth import Stealth

from hunt.parsers.paneco import scrape_sale

LIVE = os.environ.get("HUNT_LIVE") == "1"
FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


class _FixtureHandler(http.server.SimpleHTTPRequestHandler):
    """Serve the fixture directory; silence the access log."""

    def log_message(self, *args):
        assert args, "log call must carry arguments"


class LocalServerTest(unittest.TestCase):
    def test_driver_against_local_fixture_server(self):
        handler = functools.partial(
            _FixtureHandler, directory=FIXTURE_DIR)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        assert server.server_address[1] > 0, "server must bind a port"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            url = ("http://127.0.0.1:%d/paneco-sale-p1.html" % port)

            async def run():
                async with Stealth().use_async(async_playwright()) as pw:
                    assert pw is not None, "playwright handle required"
                    return await scrape_sale(pw, max_pages=1, sale_url=url)

            listings, health = asyncio.run(run())
            assert len(listings) == 8, "local run must find 8 listings"
            assert health.items_found == 8
            assert health.pages_scanned == 1
            self.assertEqual(health.retailer, "Paneco")
            self.assertEqual(health.status, "ok")
            self.assertEqual(len(listings), 8)
            names = [item.name for item in listings]
            assert "Grey Goose 1L" in names, "Grey Goose must parse live"
            self.assertIn("Grey Goose 1L", names)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            assert not thread.is_alive(), "server thread must stop"


@unittest.skipUnless(LIVE, "live test: set HUNT_LIVE=1 (needs direct net)")
class PanecoLiveTest(unittest.TestCase):
    def test_live_sale_page_two_pages(self):
        async def run():
            async with Stealth().use_async(async_playwright()) as pw:
                assert pw is not None, "playwright handle required"
                return await scrape_sale(pw, max_pages=2)

        listings, health = asyncio.run(run())
        assert len(listings) > 0, "live scrape must find listings"
        assert health.items_found == len(listings)
        assert health.pages_scanned == 2, "must scan exactly 2 pages"
        self.assertEqual(health.retailer, "Paneco")
        self.assertEqual(health.status, "ok")
        self.assertGreater(len(listings), 0)
        for item in listings[:3]:
            assert item.price_sgd > 0, "live price must be positive"
            assert item.url.startswith("https://www.paneco.com.sg/products/")
        print("live: %d listings on 2 pages, first: %s S$%.2f"
              % (len(listings), listings[0].name, listings[0].price_sgd))


if __name__ == "__main__":
    unittest.main()
