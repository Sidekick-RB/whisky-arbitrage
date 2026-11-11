"""Unit tests for the Loch Fyne Whiskies parser, using real-data
fixtures.

Fixtures: tests/fixtures/lochfyne-search-glenfiddich18.html — the
verbatim Magento search page for "glenfiddich 18", fetched 11 Nov
2026: 24 cards, all "In stock", Glenfiddich 18 Year Old at £99.50;
and tests/fixtures/lochfyne-product-glenfiddich18.html — the
verbatim product page with `<span class="key-attribute size">70cl
</span>`. The out-of-stock test uses a synthetic card built from
the real card shape (photo anchor with data-price, price-box
"Sold Out", data-dimension10="Out of stock") — no live
out-of-stock card was saved, so that path is unvalidated against a
real card.
"""

import asyncio
import os
import unittest

import hunt.parsers.lochfyne as lf
from hunt.parsers.lochfyne import (
    parse_product_html,
    parse_search_html,
    search_url,
)

SEARCH = os.path.join(os.path.dirname(__file__), "fixtures",
                      "lochfyne-search-glenfiddich18.html")
PRODUCT = os.path.join(os.path.dirname(__file__), "fixtures",
                       "lochfyne-product-glenfiddich18.html")
PAGE_URL = ("https://www.lochfynewhiskies.com/catalogsearch/result/"
            "index/?q=glenfiddich+18&p=1")
SEEN_AT = "2026-11-11T00:00:00+00:00"


def _load(path):
    assert os.path.exists(path), "fixture missing: " + path
    with open(path, "r", encoding="utf-8") as handle:
        html = handle.read()
    assert html, "fixture must be non-empty"
    return html


class SearchUrlTest(unittest.TestCase):
    def test_search_url(self):
        url = search_url("glenfiddich 18")
        assert url.startswith(
            "https://www.lochfynewhiskies.com/catalogsearch/result/index/")
        assert "q=glenfiddich+18" in url
        assert "&p=1" in url
        self.assertIn("q=glenfiddich+18", url)

    def test_search_url_page_two(self):
        url = search_url("glenfiddich 18", 2)
        assert "&p=2" in url, "page 2 must use the p= parameter"
        self.assertIn("&p=2", url)


class FixtureTest(unittest.TestCase):
    def test_twenty_four_cards_all_in_stock(self):
        cards, seen = parse_search_html(_load(SEARCH), PAGE_URL, SEEN_AT)
        assert seen == 24
        assert len(cards) == 24
        assert all(in_stock for _, _, _, in_stock in cards)
        self.assertEqual(len(cards), 24)

    def test_glenfiddich_18_benchmark(self):
        cards, _ = parse_search_html(_load(SEARCH), PAGE_URL, SEEN_AT)
        by_name = {name: (price, url, in_stock)
                   for name, price, url, in_stock in cards}
        assert by_name["Glenfiddich 18 Year Old"][0] == 99.50
        assert by_name["Glenfiddich 18 Year Old"][1] == (
            "https://www.lochfynewhiskies.com/"
            "glenfiddich-18-year-old-small-batch")
        assert by_name["Glenfiddich 18 Year Old"][2] is True
        self.assertEqual(by_name["Glenfiddich 18 Year Old"][0], 99.50)

    def test_final_price_not_old_price(self):
        # Jura 18 carries a struck-through oldPrice £79.00 after its
        # final price £60.00; Glen Moray 18 an oldPrice £94.00 after
        # its final £79.99. The selling price is the finalPrice.
        cards, _ = parse_search_html(_load(SEARCH), PAGE_URL, SEEN_AT)
        by_name = {name: price for name, price, _, _ in cards}
        assert by_name["Jura 18 Year Old"] == 60.00
        assert by_name["Glen Moray 18 Year Old"] == 79.99
        self.assertEqual(by_name["Jura 18 Year Old"], 60.00)

    def test_from_price_card_parses(self):
        # Bunnahabhain 18 has a "from" price: a nested price-from
        # span inside the price span. It is a real in-stock price.
        cards, _ = parse_search_html(_load(SEARCH), PAGE_URL, SEEN_AT)
        by_name = {name: price for name, price, _, _ in cards}
        assert by_name["Bunnahabhain 18 Year Old"] == 120.00
        self.assertEqual(by_name["Bunnahabhain 18 Year Old"], 120.00)

    def test_out_of_stock_card_is_dropped(self):
        # The OOS card still carries data-price="964" on its photo
        # anchor, but its price-box says "Sold Out" with no £ price
        # — the anchor price must never become a listing.
        html = (
            '<div class="item product product-item">'
            '<a href="https://www.lochfynewhiskies.com/'
            'the-macallan-reflexion" class="product photo '
            'product-item-photo" data-price="964" '
            'data-dimension10="Out of stock">'
            '<h3 class="product name product-item-name">'
            '<a class="product-item-link" href="https://'
            'www.lochfynewhiskies.com/the-macallan-reflexion">'
            'The Macallan Reflexion</a></h3>'
            '<div class="price-box price-final_price">Sold Out</div>'
            '</div>')
        cards, seen = parse_search_html(html, PAGE_URL, SEEN_AT)
        assert seen == 1
        assert len(cards) == 0
        self.assertEqual(len(cards), 0)

    def test_out_of_stock_flag_on_priced_card(self):
        # An out-of-stock card that does carry a price-box price is
        # kept with in_stock False (the size driver skips it).
        html = (
            '<div class="item product product-item">'
            '<a href="https://www.lochfynewhiskies.com/'
            'glenfiddich-18-year-old-small-batch" class="product '
            'photo product-item-photo" data-price="99.5" '
            'data-dimension10="Out of stock">'
            '<h3 class="product name product-item-name">'
            '<a class="product-item-link" href="https://'
            'www.lochfynewhiskies.com/glenfiddich-18-year-old-'
            'small-batch">Glenfiddich 18 Year Old</a></h3>'
            '<div class="price-box price-final_price">'
            '<span class="price-container"><span '
            'data-price-type="finalPrice"><span class="price">'
            '£99.50</span></span></span></div>'
            '</div>')
        cards, seen = parse_search_html(html, PAGE_URL, SEEN_AT)
        assert seen == 1
        assert len(cards) == 1
        assert cards[0][3] is False
        self.assertFalse(cards[0][3])

    def test_product_page_size(self):
        size_ml = parse_product_html(_load(PRODUCT))
        assert size_ml == 700
        self.assertEqual(size_ml, 700)

    def test_product_page_no_size(self):
        assert parse_product_html("<html><body>no size here</body></html>") is None
        self.assertIsNone(parse_product_html("<html></html>"))


class UrllibDriverTest(unittest.TestCase):
    def test_search_resolves_size_for_all_when_wanted_is_none(self):
        search_html = _load(SEARCH)
        product_html = _load(PRODUCT)
        calls = []

        def _fake_fetch(url):
            assert url.startswith("https://www.lochfynewhiskies.com")
            calls.append(url)
            if "/catalogsearch/" in url:
                # Page 1 carries the 24 cards; page 2 is empty, so
                # the driver stops after 2 pages scanned.
                if "&p=1" in url:
                    return search_html
                return "<html><body>no results</body></html>"
            return product_html

        saved = lf.fetch_text
        lf.fetch_text = _fake_fetch
        try:
            prices, health = asyncio.run(
                lf.search("glenfiddich 18", seen_at=SEEN_AT))
        finally:
            lf.fetch_text = saved
        assert len(prices) == 24
        assert health.status == "ok"
        assert all(item.size_ml == 700 for item in prices)
        assert all(item.currency == "GBP" for item in prices)
        assert all(item.in_stock for item in prices)
        by_name = {item.name: item for item in prices}
        assert by_name["Glenfiddich 18 Year Old"].price == 99.50
        assert health.pages_scanned == 2
        assert len(calls) == 26
        self.assertEqual(len(prices), 24)
        self.assertEqual(health.status, "ok")

    def test_search_resolves_size_only_for_wanted(self):
        # Only the wanted canonical key gets a product-page fetch;
        # the other 23 cards are dropped without a fetch.
        search_html = _load(SEARCH)
        product_html = _load(PRODUCT)
        calls = []

        def _fake_fetch(url):
            assert url.startswith("https://www.lochfynewhiskies.com")
            calls.append(url)
            if "/catalogsearch/" in url:
                # Page 1 carries the 24 cards; page 2 is empty, so
                # the driver stops after 2 pages scanned.
                if "&p=1" in url:
                    return search_html
                return "<html><body>no results</body></html>"
            return product_html

        saved = lf.fetch_text
        lf.fetch_text = _fake_fetch
        try:
            prices, health = asyncio.run(
                lf.search("glenfiddich 18", wanted={"glenfiddich-18yr"},
                          seen_at=SEEN_AT))
        finally:
            lf.fetch_text = saved
        assert len(prices) == 1
        assert health.status == "ok"
        assert prices[0].name == "Glenfiddich 18 Year Old"
        assert prices[0].price == 99.50
        assert prices[0].size_ml == 700
        assert len(calls) == 3
        self.assertEqual(len(prices), 1)
        self.assertEqual(len(calls), 3)

    def test_failed_fetch_is_blocked_not_crash(self):
        saved = lf.fetch_text
        lf.fetch_text = lambda url: None
        try:
            prices, health = asyncio.run(
                lf.search("glenfiddich 18", seen_at=SEEN_AT))
        finally:
            lf.fetch_text = saved
        assert prices == []
        assert health.status == "blocked"
        self.assertEqual(health.status, "blocked")


if __name__ == "__main__":
    unittest.main()
