"""Unit tests for the comparison engine, built from known watchlist cases.

FX sources:
- 2 Nov 2026 checks: GBP 1 = S$1.7165 (Jack Daniel's 10 note),
  USD 1 = S$1.279 (Willet global search).
- 9 Nov 2026 checks: GBP 1 = S$1.714, USD 1 = S$1.282 (weekly log).
"""

import unittest

from hunt.engine import (
    apply_promo,
    best_promo,
    cheaper_overseas,
    compare,
    per_litre,
)
from hunt.fx import fixed_rates
from hunt.models import (
    Listing,
    OverseasPrice,
    PromoCode,
    canonical_key,
)

FX_2NOV = fixed_rates(1.7165, 1.279, "test-2nov", "2026-11-02")
FX_9NOV = fixed_rates(1.714, 1.282, "test-9nov", "2026-11-09")

FLYBUY12 = PromoCode(code="12FLYBUY", kind="percent", value=12.0,
                     min_spend_sgd=150.0, cap_sgd=50.0,
                     expires="2026-11-11", scope="sitewide",
                     new_users_only=False)
WSCELEB25 = PromoCode(code="WSCELEB25", kind="percent", value=25.0,
                      min_spend_sgd=750.0, cap_sgd=300.0,
                      expires="2026-12-31", scope="lotte-wines-spirits",
                      new_users_only=False)
ISCNEW20 = PromoCode(code="ISCNEW20", kind="fixed", value=20.0,
                     min_spend_sgd=79.0, cap_sgd=None, expires=None,
                     scope="sitewide", new_users_only=True)


def make_listing(price, size_ml=700, size_verified=True, in_stock=True,
                 traveller_only=False, canonical="test-bottle",
                 name="Test Bottle 700ml", edition="",
                 retailer="Paneco"):
    """One local listing for tests."""
    assert price > 0, "test price must be positive"
    assert size_ml is None or size_ml > 0
    return Listing(
        retailer=retailer, name=name, canonical=canonical,
        size_ml=size_ml, size_verified=size_verified, price_sgd=price,
        in_stock=in_stock, traveller_only=traveller_only,
        eligibility="Singapore delivery", edition=edition, batch="",
        url="https://example.com/p/1", seen_at="2026-11-02T00:00:00+00:00")


def make_overseas(retailer, country, currency, price, size_ml,
                  in_stock=True):
    """One overseas price for tests."""
    assert price > 0, "test overseas price must be positive"
    assert size_ml > 0
    return OverseasPrice(
        retailer=retailer, country=country, currency=currency, price=price,
        size_ml=size_ml, in_stock=in_stock, url="https://example.com/p/1")


class PerLitreTest(unittest.TestCase):
    def test_per_litre_basic(self):
        result = per_litre(165.0, 700)
        assert result is not None
        self.assertAlmostEqual(result, 235.71, places=1)

    def test_per_litre_none_without_size(self):
        result = per_litre(165.0, None)
        assert result is None
        self.assertIsNone(result)


class BracklaTest(unittest.TestCase):
    """Royal Brackla 18, iShopChangi S$165: qualifier at ~30% vs UK."""

    def test_qualifies_and_uk_governs(self):
        local = make_listing(165.0, traveller_only=True,
                             canonical="royal-brackla-18yr",
                             retailer="iShopChangi")
        overseas = [
            make_overseas("Master of Malt", "UK", "GBP", 137.95, 700),
            make_overseas("ReserveBar", "US", "USD", 299.99, 750),
        ]
        result = compare(local, overseas, FX_2NOV, as_of="2026-11-02")
        assert result.verdict == "qualifier"
        assert result.overseas_best is not None
        self.assertEqual(result.verdict, "qualifier")
        self.assertEqual(result.overseas_best.retailer, "Master of Malt")
        self.assertTrue(29.0 <= result.saving_pct <= 32.0)

    def test_saving_is_not_the_us_figure(self):
        """The 54% US saving must not be reported: the UK price governs."""
        local = make_listing(165.0, traveller_only=True,
                             canonical="royal-brackla-18yr")
        overseas = [
            make_overseas("Master of Malt", "UK", "GBP", 137.95, 700),
            make_overseas("ReserveBar", "US", "USD", 299.99, 750),
        ]
        result = compare(local, overseas, FX_2NOV, as_of="2026-11-02")
        assert result.saving_pct is not None
        self.assertLess(result.saving_pct, 40.0)
        self.assertIn("traveller-only", " ".join(result.notes))


class RedbreastTest(unittest.TestCase):
    """Redbreast 21, Asher S$358: ~16% below the CHEAPER UK price.

    The earlier 22.6% figure used the dearer US price as the benchmark
    and was wrong. The engine must pick the cheaper overseas price.
    """

    def test_close_find_at_sixteen_percent(self):
        local = make_listing(358.0, canonical="redbreast-21yr",
                             retailer="Asher")
        overseas = [
            make_overseas("Master of Malt", "UK", "GBP", 248.0, 700),
            make_overseas("Wine.com", "US", "USD", 383.99, 750),
        ]
        result = compare(local, overseas, FX_2NOV, as_of="2026-11-02")
        assert result.verdict == "close-find"
        assert result.overseas_best is not None
        self.assertEqual(result.verdict, "close-find")
        self.assertEqual(result.overseas_best.retailer, "Master of Malt")
        self.assertTrue(15.0 <= result.saving_pct <= 17.0)

    def test_never_reports_the_wrong_figure(self):
        local = make_listing(358.0, canonical="redbreast-21yr")
        overseas = [
            make_overseas("Master of Malt", "UK", "GBP", 248.0, 700),
            make_overseas("Wine.com", "US", "USD", 383.99, 750),
        ]
        result = compare(local, overseas, FX_2NOV, as_of="2026-11-02")
        assert result.saving_pct is not None
        self.assertLess(result.saving_pct, 20.0)
        self.assertGreater(result.saving_pct, 14.0)


class Glenfarclas25Test(unittest.TestCase):
    """Glenfarclas 25, Cellarbration S$299: 27.6% below UK but ~6% ABOVE
    the US price, so NOT a qualifier. The UK-only view is the trap."""

    def test_not_a_qualifier_because_us_governs(self):
        local = make_listing(299.0, size_ml=750,
                             canonical="glenfarclas-25yr",
                             retailer="Cellarbration")
        overseas = [
            make_overseas("The Whisky Exchange", "UK", "GBP", 225.0, 700),
            make_overseas("Master of Malt", "UK", "GBP", 225.0, 700),
            make_overseas("Total Wine", "US", "USD", 219.99, 750),
        ]
        result = compare(local, overseas, FX_9NOV, as_of="2026-11-09")
        assert result.verdict == "not-a-deal"
        assert result.saving_pct is not None
        self.assertEqual(result.verdict, "not-a-deal")
        self.assertLess(result.saving_pct, 0.0)
        self.assertGreater(result.saving_pct, -10.0)

    def test_uk_only_saving_would_have_fooled_us(self):
        """Documents the trap: against the UK price alone it looks like
        a 27.6% qualifier. The engine must never compare against one
        overseas price when a cheaper verified one exists."""
        uk_per_litre = per_litre(225.0 * 1.714, 700)
        local_per_litre = per_litre(299.0, 750)
        assert uk_per_litre is not None and local_per_litre is not None
        uk_only = (uk_per_litre - local_per_litre) / uk_per_litre * 100.0
        self.assertTrue(27.0 <= uk_only <= 28.5)


class UnverifiedTest(unittest.TestCase):
    def test_no_overseas_price_is_unverified(self):
        local = make_listing(200.0, canonical="glenfarclas-21yr")
        result = compare(local, [], FX_2NOV, as_of="2026-11-02")
        assert result.verdict == "unverified"
        self.assertEqual(result.verdict, "unverified")
        self.assertIsNone(result.saving_pct)
        self.assertIsNone(result.overseas_per_litre_sgd)

    def test_out_of_stock_overseas_never_counts(self):
        local = make_listing(200.0, canonical="glenfarclas-21yr")
        overseas = [make_overseas("Wine.com", "US", "USD", 100.0, 750,
                                   in_stock=False)]
        result = compare(local, overseas, FX_2NOV, as_of="2026-11-02")
        assert result.verdict == "unverified"
        self.assertEqual(result.verdict, "unverified")
        self.assertIsNone(result.saving_pct)

    def test_assumed_size_is_unverified(self):
        """Macallan 18 2020 at MWA: size assumed (product page blocked),
        so it must not qualify in the engine even though the arithmetic
        would clear the bar."""
        local = make_listing(720.0, size_verified=False,
                             canonical="macallan-sherry-oak-18yr-2020-release",
                             edition="2020 Release", retailer="Malt & Wine Asia")
        overseas = [make_overseas("The Whisky Exchange", "UK", "GBP",
                                   650.0, 700)]
        result = compare(local, overseas, FX_2NOV, as_of="2026-11-02")
        assert result.verdict == "unverified"
        self.assertEqual(result.verdict, "unverified")
        self.assertIn("size assumed", " ".join(result.notes))

    def test_missing_size_is_unverified(self):
        local = make_listing(720.0, size_ml=None,
                             canonical="macallan-sherry-oak-18yr")
        overseas = [make_overseas("The Whisky Exchange", "UK", "GBP",
                                   650.0, 700)]
        result = compare(local, overseas, FX_2NOV, as_of="2026-11-02")
        assert result.verdict == "unverified"
        self.assertEqual(result.verdict, "unverified")

    def test_out_of_stock_local_is_unavailable(self):
        local = make_listing(165.0, in_stock=False,
                             canonical="royal-brackla-18yr")
        overseas = [make_overseas("Master of Malt", "UK", "GBP", 137.95, 700)]
        result = compare(local, overseas, FX_2NOV, as_of="2026-11-02")
        assert result.verdict == "unavailable"
        self.assertEqual(result.verdict, "unavailable")
        self.assertIsNone(result.saving_pct)

    def test_below_hundred_floor(self):
        local = make_listing(90.0, canonical="test-bottle")
        overseas = [make_overseas("Master of Malt", "UK", "GBP", 200.0, 700)]
        result = compare(local, overseas, FX_2NOV, as_of="2026-11-02")
        assert result.verdict == "not-a-deal"
        self.assertEqual(result.verdict, "not-a-deal")
        self.assertIn("floor", " ".join(result.notes))


class CheaperOverseasTest(unittest.TestCase):
    def test_ignores_out_of_stock_cheaper_price(self):
        overseas = [
            make_overseas("Master of Malt", "UK", "GBP", 100.0, 700,
                          in_stock=False),
            make_overseas("Wine.com", "US", "USD", 383.99, 750),
        ]
        best, per_litre_value = cheaper_overseas(overseas, FX_2NOV)
        assert best is not None and per_litre_value is not None
        self.assertEqual(best.retailer, "Wine.com")
        self.assertGreater(per_litre_value, 0)

    def test_empty_list_returns_none(self):
        best, per_litre_value = cheaper_overseas([], FX_2NOV)
        assert best is None and per_litre_value is None
        self.assertIsNone(best)
        self.assertIsNone(per_litre_value)


class PromoTest(unittest.TestCase):
    def test_brackla_12flybuy(self):
        price, applied = apply_promo(165.0, FLYBUY12, "2026-11-02", False)
        assert applied
        self.assertEqual(price, 145.20)

    def test_woodford_wsceleb25_cap(self):
        """25% of S$1,500 is S$375, capped at S$300 -> S$1,200."""
        price, applied = apply_promo(1500.0, WSCELEB25, "2026-11-02", False)
        assert applied
        self.assertEqual(price, 1200.00)

    def test_expired_code_not_applied(self):
        price, applied = apply_promo(165.0, FLYBUY12, "2026-11-12", False)
        assert not applied
        self.assertEqual(price, 165.0)

    def test_new_user_code_needs_new_user(self):
        price, applied = apply_promo(165.0, ISCNEW20, "2026-11-02", False)
        assert not applied
        self.assertEqual(price, 165.0)
        price, applied = apply_promo(165.0, ISCNEW20, "2026-11-02", True)
        assert applied
        self.assertEqual(price, 145.00)

    def test_best_promo_picks_cheapest(self):
        price, code = best_promo(1500.0, [FLYBUY12, WSCELEB25],
                                 "2026-11-02", False)
        assert code == "WSCELEB25"
        self.assertEqual(price, 1200.00)
        self.assertEqual(code, "WSCELEB25")

    def test_min_spend_blocks_code(self):
        price, applied = apply_promo(165.0, WSCELEB25, "2026-11-02", False)
        assert not applied
        self.assertEqual(price, 165.0)


class WilletTest(unittest.TestCase):
    def test_same_age_key_ignores_barrel(self):
        first = canonical_key("Willet", 7, "Barrel #2342", True)
        second = canonical_key("Willet", 7, "Barrel #6061", True)
        assert first == second
        self.assertEqual(first, second)

    def test_different_age_is_a_different_key(self):
        seven = canonical_key("Willet", 7, "", True)
        ten = canonical_key("Willet", 10, "", True)
        assert seven != ten
        self.assertNotEqual(seven, ten)

    def test_proof_difference_is_a_note_not_a_disqualifier(self):
        """Willet 7yr, SG S$378/70cl vs US $166.99/750ml: dearer locally,
        and the proof difference is carried as a note."""
        local = make_listing(378.0, canonical="willet-rye-7yr",
                             name="Willet Rye 7 Year Old 70cl")
        overseas = [make_overseas("Virginia ABC", "US", "USD", 166.99, 750)]
        note = "proof differs: US 123.4 vs local 114.6 (stated)"
        result = compare(local, overseas, FX_2NOV, as_of="2026-11-02",
                         proof_note=note)
        assert result.verdict == "not-a-deal"
        self.assertEqual(result.verdict, "not-a-deal")
        self.assertIn(note, result.notes)


if __name__ == "__main__":
    unittest.main()
