"""Unit tests for the data model: canonical keys and validation."""

import unittest

from hunt.models import (
    FxRates,
    Listing,
    OverseasPrice,
    PromoCode,
    canonical_from_name,
    canonical_key,
    listing_key,
)


def _listing(**overrides):
    fields = {
        "retailer": "Paneco",
        "name": "Test Bottle 700ml",
        "canonical": "test-bottle",
        "size_ml": 700,
        "size_verified": True,
        "price_sgd": 100.0,
        "in_stock": True,
        "traveller_only": False,
        "eligibility": "Singapore delivery",
        "edition": "",
        "batch": "",
        "url": "https://example.com/p/1",
        "seen_at": "2026-11-02T00:00:00+00:00",
    }
    fields.update(overrides)
    return Listing(**fields)


class CanonicalKeyTest(unittest.TestCase):
    def test_same_expression_same_key(self):
        first = canonical_key("Royal Brackla", 18, "", False)
        second = canonical_key("Royal Brackla", 18, "", False)
        assert first == second
        self.assertEqual(first, "royal-brackla-18yr")

    def test_edition_is_part_of_the_key(self):
        release_2020 = canonical_key("Macallan", 18, "2020 Release", False)
        release_2023 = canonical_key("Macallan", 18, "2023 Release", False)
        assert release_2020 != release_2023
        self.assertIn("2020-release", release_2020)
        self.assertIn("2023-release", release_2023)

    def test_nas_bottles_use_nas(self):
        key = canonical_key("Chivas Regal", None, "Ultis", False)
        assert key == "chivas-regal-nas-ultis"
        self.assertEqual(key, "chivas-regal-nas-ultis")


class CanonicalFromNameTest(unittest.TestCase):
    def test_brackla(self):
        key = canonical_from_name("Royal Brackla 18 Year Old Single Malt 700ml")
        assert key == "royal-brackla-18yr"
        self.assertEqual(key, "royal-brackla-18yr")

    def test_abv_stripped(self):
        # iShopChangi names always carry ABV ("700ML 46%"); without
        # this the key would never match an overseas name.
        key = canonical_from_name(
            "Royal Brackla 18 Year Old Single Malt Scotch Whisky 700ML 46%")
        assert key == "royal-brackla-18yr"
        assert canonical_from_name("Nikka Whiskey Tailored 700ML 43%") == (
            "nikka-tailored")
        self.assertEqual(key, "royal-brackla-18yr")

    def test_ballantines(self):
        key = canonical_from_name("Ballantine's 23 Year Old American Oak 700ml")
        assert key == "ballantines-23yr-american-oak"
        self.assertEqual(key, "ballantines-23yr-american-oak")

    def test_edition_survives(self):
        key = canonical_from_name(
            "The Macallan Sherry Oak 18 Year Old 2020 Release 700ml")
        assert key == "macallan-sherry-oak-18yr-2020-release"
        self.assertEqual(key, "macallan-sherry-oak-18yr-2020-release")

    def test_litre_size_dropped(self):
        key = canonical_from_name("Johnnie Walker Blue Label 1L")
        assert key == "johnnie-walker-blue-label"
        self.assertEqual(key, "johnnie-walker-blue-label")

    def test_gift_box_dropped(self):
        key = canonical_from_name(
            "Bushmills 16 Year Old Three Wood 700ml w/ Gift Box")
        assert key == "bushmills-16yr-three-wood"
        self.assertEqual(key, "bushmills-16yr-three-wood")

    def test_years_plural_normalised(self):
        key = canonical_from_name("Redbreast 21 Years 700ml")
        assert key == "redbreast-21yr"
        self.assertEqual(key, "redbreast-21yr")

    def test_bare_whisky_dropped(self):
        key = canonical_from_name(
            "Glenmorangie 25 Years Single Malt Whisky 750ml")
        assert key == "glenmorangie-25yr"
        self.assertEqual(key, "glenmorangie-25yr")


class ListingKeyTest(unittest.TestCase):
    def test_listing_key(self):
        key = listing_key(_listing())
        assert key == "Paneco|test-bottle|nt"
        self.assertEqual(key, "Paneco|test-bottle|nt")

    def test_listing_key_carries_mode(self):
        # Same bottle, two fulfilment modes: different keys, so both
        # survive dedupe (iShopChangi NT vs TR, KrisShop home vs flight).
        home = listing_key(_listing())
        flight = listing_key(_listing(traveller_only=True))
        assert home != flight
        assert flight == "Paneco|test-bottle|tr"
        self.assertEqual(flight, "Paneco|test-bottle|tr")


class ValidationTest(unittest.TestCase):
    def test_bad_price_rejected(self):
        with self.assertRaises(AssertionError):
            _listing(price_sgd=0.0)
        with self.assertRaises(AssertionError):
            _listing(price_sgd=-5.0)

    def test_bad_size_rejected(self):
        with self.assertRaises(AssertionError):
            _listing(size_ml=0)

    def test_relative_url_rejected(self):
        with self.assertRaises(AssertionError):
            _listing(url="/products/test")

    def test_bad_overseas_country_rejected(self):
        with self.assertRaises(AssertionError):
            OverseasPrice(retailer="X", country="SG", currency="SGD",
                          price=10.0, size_ml=700, in_stock=True,
                          url="https://example.com/p/1")

    def test_bad_fx_rejected(self):
        with self.assertRaises(AssertionError):
            FxRates(gbp_to_sgd=0.0, usd_to_sgd=1.28, source="t",
                    as_of="2026-11-02")

    def test_bad_promo_kind_rejected(self):
        with self.assertRaises(AssertionError):
            PromoCode(code="X", kind="bogus", value=10.0, min_spend_sgd=0.0,
                      cap_sgd=None, expires=None, scope="sitewide",
                      new_users_only=False)


if __name__ == "__main__":
    unittest.main()
