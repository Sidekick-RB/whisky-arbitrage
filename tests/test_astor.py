"""Unit tests for the Astor Wines parser, using a real-data fixture.

Fixture: tests/fixtures/astor-search-glenlivet18.html — two verbatim
item-teaser cards from the live Spirits search page for "glenlivet
18", fetched 10 Nov 2026: Glenlivet 18yr ($149.99, in stock, 750ml)
and one out-of-stock card (empty price container).
"""

import os
import unittest

from hunt.parsers.astor import (
    _variant_to_ml,
    parse_search_html,
    search_url,
)

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "astor-search-glenlivet18.html")
PAGE_URL = ("https://www.astorwines.com/SpiritsSearchResult.aspx"
            "?p=1&search=Advanced&searchtype=Contains"
            "&term=glenlivet+18&cat=2")
SEEN_AT = "2026-11-10T00:00:00+00:00"


def _load(path):
    assert os.path.exists(path), "fixture missing: " + path
    with open(path, "r", encoding="utf-8") as handle:
        html = handle.read()
    assert html, "fixture must be non-empty"
    return html


class SearchUrlTest(unittest.TestCase):
    def test_search_url(self):
        url = search_url("glenlivet 18")
        assert url.startswith(
            "https://www.astorwines.com/SpiritsSearchResult.aspx")
        assert "term=glenlivet+18" in url
        assert "cat=2" in url
        assert "&p=1&Page=1" in url, "Astor paginates on Page=, not p="
        self.assertIn("cat=2", url)

    def test_search_url_page_two(self):
        url = search_url("glenlivet 18", 2)
        assert "&p=1&Page=2" in url, "page 2 must use the Page= parameter"
        self.assertIn("&p=1&Page=2", url)


class VariantToMlTest(unittest.TestCase):
    def test_millilitres(self):
        assert _variant_to_ml("750") == 750
        assert _variant_to_ml("700") == 700
        self.assertEqual(_variant_to_ml("750"), 750)

    def test_decimal_litres(self):
        assert _variant_to_ml("1.75") == 1750
        assert _variant_to_ml("1.5") == 1500
        self.assertEqual(_variant_to_ml("1.75"), 1750)

    def test_ltr_is_one_litre(self):
        assert _variant_to_ml("LTR") == 1000
        self.assertEqual(_variant_to_ml("LTR"), 1000)

    def test_unknown_variant_is_none(self):
        assert _variant_to_ml("MAGNUM") is None
        self.assertIsNone(_variant_to_ml("MAGNUM"))


class FixtureTest(unittest.TestCase):
    def test_glenlivet_18_parses(self):
        prices, seen = parse_search_html(_load(FIXTURE), PAGE_URL, SEEN_AT)
        assert seen == 2
        assert len(prices) == 1
        item = prices[0]
        assert "Glenlivet 18yr" in item.name
        assert item.price == 149.99
        assert item.size_ml == 750
        assert item.in_stock is True
        assert item.currency == "USD"
        assert item.retailer == "Astor Wines"
        assert item.url == "https://www.astorwines.com/item/03574"
        self.assertEqual(item.price, 149.99)
        self.assertEqual(item.size_ml, 750)

    def test_out_of_stock_card_has_no_price(self):
        # The out-of-stock card's price container is empty, so it is
        # dropped rather than priced — a price is never invented.
        prices, seen = parse_search_html(_load(FIXTURE), PAGE_URL, SEEN_AT)
        assert seen == 2
        assert len(prices) == 1
        self.assertEqual(len(prices), 1)

    def test_litre_variants_parse(self):
        # Astor sizes 1.75 L / 1.5 L / LTR were dropped by the old
        # digits-only variant regex; they are in-stock bottles too.
        html = (
            '<div class="item-teaser">'
            '<a class="item-name" href="/item/00001">'
            "Johnnie Walker Blue Label</a>"
            '<span class="price-value price-bottle display-2">'
            "$224.96</span>"
            "<script>variant: '(1.75)'</script></div>"
            '<div class="item-teaser">'
            '<a class="item-name" href="/item/00002">'
            "Johnnie Walker Blue Label</a>"
            '<span class="price-value price-bottle display-2">'
            "$199.99</span>"
            "<script>variant: '(LTR)'</script></div>")
        prices, seen = parse_search_html(html, PAGE_URL, SEEN_AT)
        assert seen == 2
        assert len(prices) == 2
        assert prices[0].size_ml == 1750
        assert prices[1].size_ml == 1000
        assert prices[0].in_stock is True
        assert prices[1].in_stock is True
        self.assertEqual(prices[0].size_ml, 1750)
        self.assertEqual(prices[1].size_ml, 1000)


if __name__ == "__main__":
    unittest.main()
