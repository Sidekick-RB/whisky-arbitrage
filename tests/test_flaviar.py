"""Unit tests for the Flaviar parser, using a real-data fixture.

Fixture: tests/fixtures/flaviar-search-glenfiddich18.html — the live
search page for "glenfiddich 18", fetched 11 Nov 2026: 24 cards,
22 carrying a regular-price span. Glenfiddich 18 Year Old is
$145.99 regular (750ml, in stock); its member price is $114.99
(data-price="11499") and must never be used. Glenfiddich 30 Year
Old ($1,251.99, 700ml) is the one card with a VISIBLE sold-out
span. Glenfiddich 15 Solera and Glenmorangie Infinita 18 carry no
regular-price span (discounted variant only) and are dropped.
"""

import os
import unittest

from hunt.parsers.flaviar import (
    _is_in_stock,
    _keep,
    _size_ml,
    parse_search_html,
    search_url,
)

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "flaviar-search-glenfiddich18.html")
PAGE_URL = "https://flaviar.com/search?q=glenfiddich+18"
SEEN_AT = "2026-11-11T00:00:00+00:00"


def _load(path):
    assert os.path.exists(path), "fixture missing: " + path
    with open(path, "r", encoding="utf-8") as handle:
        html = handle.read()
    assert html, "fixture must be non-empty"
    return html


def _card(name, handle, price_html, sold_out_visible=False):
    """One synthetic Flaviar card chunk (split marker included)."""
    assert isinstance(name, str) and name, "name required"
    assert isinstance(handle, str) and handle, "handle required"
    assert isinstance(price_html, str), "price HTML required"
    span_class = ("card-btn-inner sold-out-message "
                  "card-visible-out-of-stock stock-unavailable")
    if not sold_out_visible:
        span_class += " d-none"
    style = '' if not sold_out_visible else ' style="display: flex;"'
    html = (
        '<div class="card-wrapper product-card-wrapper">'
        '<div data-title="%s" data-product-handle="%s"></div>'
        '%s<span class="%s"%s></span></div>'
        % (name, handle, price_html, span_class, style))
    assert "card-wrapper product-card-wrapper" in html
    return html


class SearchUrlTest(unittest.TestCase):
    def test_search_url(self):
        url = search_url("glenfiddich 18")
        assert url.startswith("https://flaviar.com/search?q=")
        assert "q=glenfiddich+18" in url
        assert "&page=" not in url, "page 1 carries no page parameter"
        self.assertIn("q=glenfiddich+18", url)

    def test_search_url_page_two(self):
        url = search_url("glenfiddich 18", 2)
        assert "&page=2" in url, "page 2 must use the page= parameter"
        self.assertIn("&page=2", url)


class SizeTest(unittest.TestCase):
    def test_title_size_ml(self):
        assert _size_ml("Glenfiddich 30 Year Old (700mL)",
                         "glenfiddich-30-year-old-700") == 700
        assert _size_ml("Glenfiddich 12 Year Old (375mL)",
                         "glenfiddich-12-year-old-375") == 375
        self.assertEqual(_size_ml("Glenfiddich 30 Year Old (700mL)",
                                  "glenfiddich-30-year-old-700"), 700)

    def test_title_size_litres(self):
        assert _size_ml("Glenfiddich 12 Year Old (1.75L)",
                         "glenfiddich-12-year-old-1750") == 1750
        assert _size_ml("Glenfiddich 12 Year Old (1L)",
                         "glenfiddich-12-year-old-1000") == 1000
        self.assertEqual(_size_ml("Glenfiddich 12 Year Old (1.75L)",
                                  "glenfiddich-12-year-old-1750"), 1750)

    def test_handle_suffix_size(self):
        assert _size_ml("Glenfiddich 18 Year Old Single Malt Scotch Whisky",
                         "glenfiddich-18-year-old-750") == 750
        assert _size_ml("Glenfiddich 12 Year Old (50mL)",
                         "glenfiddich-12-year-old-50") == 50
        self.assertEqual(_size_ml(
            "Glenfiddich 18 Year Old Single Malt Scotch Whisky",
            "glenfiddich-18-year-old-750"), 750)

    def test_no_size_is_none(self):
        assert _size_ml("Test Whisky 18 Year Old",
                         "test-whisky-18-year-old") is None
        self.assertIsNone(_size_ml("Test Whisky 18 Year Old",
                                    "test-whisky-18-year-old"))

    def test_handle_year_is_not_a_size(self):
        # A handle ending in a vintage year carries no size — it
        # must not parse as a 1998 ml bottle.
        assert _size_ml("Test Whisky 18 Year Old",
                         "test-whisky-18-year-old-1998") is None
        assert _size_ml("Test Whisky 18 Year Old",
                         "test-whisky-18-year-old-2020") is None
        self.assertIsNone(_size_ml("Test Whisky 18 Year Old",
                                    "test-whisky-18-year-old-1998"))


class KeepTest(unittest.TestCase):
    def test_wanted_none_keeps_all(self):
        assert _keep("Glenfiddich 18 Year Old Single Malt Scotch Whisky",
                     None) is True
        self.assertTrue(_keep("Glenfiddich 18 Year Old", None))

    def test_wanted_filters_by_canonical(self):
        wanted = {"glenfiddich-18yr"}
        assert _keep("Glenfiddich 18 Year Old Single Malt Scotch Whisky",
                     wanted) is True
        assert _keep("Glenfiddich 12 Year Old Single Malt Scotch Whisky",
                     wanted) is False
        self.assertFalse(_keep("Glenfiddich 12 Year Old", wanted))


class StockTest(unittest.TestCase):
    def test_hidden_span_is_in_stock(self):
        chunk = ('<span class="card-btn-inner sold-out-message '
                 'card-visible-out-of-stock d-none stock-unavailable">')
        assert _is_in_stock(chunk) is True
        self.assertTrue(_is_in_stock(chunk))

    def test_visible_span_is_out_of_stock(self):
        chunk = ('<span class="card-btn-inner sold-out-message '
                 'card-visible-out-of-stock stock-unavailable" '
                 'style="display: flex;">')
        assert _is_in_stock(chunk) is False
        self.assertFalse(_is_in_stock(chunk))


class FixtureTest(unittest.TestCase):
    def test_glenfiddich_18_parses(self):
        prices, seen = parse_search_html(_load(FIXTURE), PAGE_URL, SEEN_AT)
        assert seen == 24
        assert len(prices) == 22
        item = [p for p in prices
                if p.name == ("Glenfiddich 18 Year Old "
                              "Single Malt Scotch Whisky")][0]
        assert item.price == 145.99
        assert item.size_ml == 750
        assert item.in_stock is True
        assert item.currency == "USD"
        assert item.retailer == "Flaviar"
        assert item.url == ("https://flaviar.com/products/"
                            "glenfiddich-18-year-old-750")
        self.assertEqual(item.price, 145.99)
        self.assertEqual(item.size_ml, 750)

    def test_member_price_never_used(self):
        # data-price="11499" ($114.99) is the MEMBER price for
        # Glenfiddich 18; the regular price is $145.99. No parsed
        # price anywhere may equal the member price.
        prices, _ = parse_search_html(_load(FIXTURE), PAGE_URL, SEEN_AT)
        assert all(p.price != 114.99 for p in prices)
        item = [p for p in prices if "Glenfiddich 18 Year Old" in p.name
                and "(700mL)" not in p.name][0]
        assert item.price == 145.99
        self.assertNotIn(114.99, [p.price for p in prices])

    def test_cards_without_regular_price_are_dropped(self):
        # Glenfiddich 15 Solera and Glenmorangie Infinita 18 show a
        # discounted variant only (green regular / discounted spans),
        # no price_v3-regular price__regular span — dropped, never
        # priced from data-price.
        prices, seen = parse_search_html(_load(FIXTURE), PAGE_URL, SEEN_AT)
        assert seen == 24
        assert len(prices) == 22
        names = [p.name for p in prices]
        assert not any("Solera Reserve" in n for n in names)
        assert not any("Infinita" in n for n in names)
        self.assertEqual(len(prices), 22)

    def test_out_of_stock_glenfiddich_30_kept_unstocked(self):
        # The one card with a VISIBLE sold-out span carries a price,
        # so it is kept with in_stock False (astor convention).
        prices, _ = parse_search_html(_load(FIXTURE), PAGE_URL, SEEN_AT)
        item = [p for p in prices
                if p.name.startswith("Glenfiddich 30 Year Old "
                                     "Single Malt Scotch Whisky")][0]
        assert item.price == 1251.99
        assert item.size_ml == 700
        assert item.in_stock is False
        self.assertEqual(item.price, 1251.99)
        self.assertFalse(item.in_stock)

    def test_in_stock_count(self):
        prices, _ = parse_search_html(_load(FIXTURE), PAGE_URL, SEEN_AT)
        assert len(prices) == 22
        assert sum(1 for p in prices if p.in_stock) == 21
        assert sum(1 for p in prices if not p.in_stock) == 1
        self.assertEqual(sum(1 for p in prices if p.in_stock), 21)

    def test_disabled_button_member_only_card_still_in_stock(self):
        # Grande Couronne 26 has a disabled (member-only / allocation)
        # button but a HIDDEN sold-out span — in stock, not sold out.
        prices, _ = parse_search_html(_load(FIXTURE), PAGE_URL, SEEN_AT)
        item = [p for p in prices if "Grande Couronne" in p.name][0]
        assert item.price == 1081.99
        assert item.size_ml == 750
        assert item.in_stock is True
        self.assertEqual(item.price, 1081.99)
        self.assertTrue(item.in_stock)

    def test_title_sizes_parse(self):
        # Size from the title where the handle alone would also work,
        # and the litre/mini variants.
        prices, _ = parse_search_html(_load(FIXTURE), PAGE_URL, SEEN_AT)
        by_name = {p.name: p for p in prices}
        assert by_name["Glenfiddich 12 Year Old Single Malt Scotch "
                        "Whisky (1.75L)"].size_ml == 1750
        assert by_name["Glenfiddich 12 Year Old Single Malt Scotch "
                        "Whisky (375mL)"].size_ml == 375
        assert by_name["Glenfiddich 12 Year Old Single Malt Scotch "
                        "Whisky (1L)"].size_ml == 1000
        assert by_name["Glenfiddich 12 Year Old Single Malt Scotch "
                        "Whisky (50mL)"].size_ml == 50
        self.assertEqual(by_name["Glenfiddich 12 Year Old Single Malt "
                                 "Scotch Whisky (1.75L)"].size_ml, 1750)

    def test_no_size_card_is_dropped(self):
        # A card with a regular price but no size in its title or
        # handle is dropped, never guessed.
        html = _card(
            "Test Whisky 18 Year Old", "test-whisky-18-year-old",
            '<div class="price_v3-regular price__regular">$99.99</div>')
        prices, seen = parse_search_html(html, PAGE_URL, SEEN_AT)
        assert seen == 1
        assert len(prices) == 0
        self.assertEqual(len(prices), 0)

    def test_synthetic_card_with_handle_size_parses(self):
        html = _card(
            "Test Whisky 18 Year Old", "test-whisky-18-year-old-750",
            '<div class="price_v3-regular price__regular">$99.99</div>')
        prices, seen = parse_search_html(html, PAGE_URL, SEEN_AT)
        assert seen == 1
        assert len(prices) == 1
        assert prices[0].price == 99.99
        assert prices[0].size_ml == 750
        assert prices[0].in_stock is True
        assert prices[0].url == ("https://flaviar.com/products/"
                                 "test-whisky-18-year-old-750")
        self.assertEqual(prices[0].size_ml, 750)

    def test_synthetic_visible_sold_out_kept_unstocked(self):
        html = _card(
            "Test Whisky 18 Year Old", "test-whisky-18-year-old-750",
            '<div class="price_v3-regular price__regular">$99.99</div>',
            sold_out_visible=True)
        prices, seen = parse_search_html(html, PAGE_URL, SEEN_AT)
        assert seen == 1
        assert len(prices) == 1
        assert prices[0].in_stock is False
        self.assertFalse(prices[0].in_stock)


if __name__ == "__main__":
    unittest.main()
