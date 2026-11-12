"""Unit tests for the Scotch <46% ABV filter (slice 17).

Marcel's rule (12 Nov 2026): no Scotch below 46% ABV in the weekly
pulls, unless aged 21 years or more, or premium/collectible. The
filter is curated knowledge, not derived data: the Listing model
carries no ABV, so brands, age pairs, edition prefixes and tokens
encode what is known to be 46% or more. Fail-open: an unknown
brand is never excluded.
"""

import unittest

from hunt.report import diff_states
from hunt.scotch import (
    age_years,
    brand_of,
    excluded_by_abv_rule,
    is_46_plus,
    is_premium,
    is_scotch,
)


class AgeParsingTest(unittest.TestCase):
    def test_hyphen_yo(self):
        self.assertEqual(age_years("macallan-18-yo-sherry-oak-cask"), 18)

    def test_hyphen_yrs(self):
        self.assertEqual(age_years("glenlivet-18-yrs"), 18)

    def test_plain_yo(self):
        self.assertEqual(age_years("caol-ila-13yo"), 13)

    def test_french_ans(self):
        self.assertEqual(age_years("bowmore-22-ans-1998"), 22)

    def test_yearold_word(self):
        self.assertEqual(age_years("glenfarclas-30yearold"), 30)

    def test_bare_year_is_not_an_age(self):
        self.assertIsNone(age_years("benriach-1999-refill-sherry"))
        self.assertIsNone(age_years("macallan-2022-classic-cut"))


class ExcludedTest(unittest.TestCase):
    """Core-range Scotch under 46% ABV: the filter drops it."""

    def test_bowmore_18(self):
        self.assertTrue(excluded_by_abv_rule("bowmore-18yr"))

    def test_glenfiddich_12(self):
        self.assertTrue(excluded_by_abv_rule("glenfiddich-12yr"))

    def test_glenlivet_18(self):
        self.assertTrue(excluded_by_abv_rule("glenlivet-18yr"))

    def test_johnnie_walker_black_label(self):
        self.assertTrue(excluded_by_abv_rule("johnnie-walker-black-label"))

    def test_talisker_10(self):
        """Talisker 10 is 45.8%."""
        self.assertTrue(excluded_by_abv_rule("talisker-10yr"))

    def test_jura_12(self):
        self.assertTrue(excluded_by_abv_rule("jura-12yr"))

    def test_compass_box_hedonism(self):
        """Hedonism is 43%; only the named Compass Box editions are 46%+."""
        self.assertTrue(excluded_by_abv_rule("compass-box-hedonism"))

    def test_signatory_standard(self):
        """A standard Signatory Vintage bottling is 43%."""
        self.assertTrue(
            excluded_by_abv_rule("signatory-vintage-caol-ila-12yr"))

    def test_port_askaig_8(self):
        """Port Askaig 8 is 45.8%; only the 15/17yr are 46%+."""
        self.assertTrue(excluded_by_abv_rule("port-askaig-8yr"))


class KeptTest(unittest.TestCase):
    """46%+ Scotch, 21+ Scotch, and premium Scotch stay in the pull."""

    def test_royal_brackla_18_on_the_line(self):
        """46% exactly: on the line, not below."""
        self.assertFalse(excluded_by_abv_rule("royal-brackla-18yr"))
        self.assertTrue(is_46_plus("royal-brackla-18yr"))

    def test_glenfarclas_21_on_the_line(self):
        """21 years exactly: on the line, not below."""
        self.assertFalse(excluded_by_abv_rule("glenfarclas-21yr"))

    def test_macallan_18_premium_age_pair(self):
        """Macallan 18 Sherry Oak is 43% but is the flagship
        collectible annual release."""
        self.assertFalse(
            excluded_by_abv_rule("macallan-18-yo-sherry-oak-cask"))
        self.assertTrue(is_premium("macallan-18-yo-sherry-oak-cask"))

    def test_macallan_time_space(self):
        self.assertFalse(excluded_by_abv_rule("macallan-time-space-mastery"))

    def test_chivas_ultis(self):
        self.assertFalse(excluded_by_abv_rule("chivas-regal-ultis"))

    def test_ardbeg_10_brand_level(self):
        self.assertFalse(excluded_by_abv_rule("ardbeg-10yr"))

    def test_springbank_10_brand_level(self):
        self.assertFalse(excluded_by_abv_rule("springbank-10yr"))

    def test_laphroaig_quarter_cask_token(self):
        self.assertFalse(excluded_by_abv_rule("laphroaig-quarter-cask"))

    def test_glenfarclas_105_token(self):
        self.assertFalse(excluded_by_abv_rule("glenfarclas-105"))

    def test_ben_nevis_vintage_pair(self):
        self.assertFalse(excluded_by_abv_rule("ben-nevis-20142024-10yo"))

    def test_old_vintage_year(self):
        """A 1946 vintage is a collectible single cask."""
        self.assertFalse(
            excluded_by_abv_rule("gordon-macphail-glenlivet-1946"))

    def test_proof_rule(self):
        """110 proof is 55% ABV."""
        self.assertFalse(
            excluded_by_abv_rule("macallan-12-110-proof-sherry-oak"))

    def test_port_askaig_15_age_pair(self):
        self.assertFalse(excluded_by_abv_rule("port-askaig-15yr"))

    def test_fettercairn_14_age_pair(self):
        """Fettercairn 14 is 46%; the 12 is 40%."""
        self.assertFalse(excluded_by_abv_rule("fettercairn-14yr-old"))

    def test_tomatin_collection_finish(self):
        """Tomatin's Italian Collection finishes are 46%."""
        self.assertFalse(excluded_by_abv_rule(
            "tomatin-12yr-amarone-cask-finish-italian-collection"))

    def test_laphroaig_px_cask_1l(self):
        """The 1L travel-retail PX Cask is 48%."""
        self.assertFalse(excluded_by_abv_rule("laphroaig-px-cask"))

    def test_glen_grant_nonchill_token(self):
        self.assertFalse(excluded_by_abv_rule(
            "glen-grant-16yearold-nonchill-filtered"))

    def test_2015_vintage_year(self):
        """A 2015 vintage is a single cask, collectible."""
        self.assertFalse(excluded_by_abv_rule(
            "dailuaine-2015-8yo-red-wine-barrique-finish"))


class Sub46OverrideTest(unittest.TestCase):
    """Known sub-46% exceptions inside 46%+ brands: the override
    corrects the brand-level keep without touching the 21+ or
    premium exceptions."""

    def test_arran_barrel_reserve(self):
        """Arran is a 46%+ brand, but The Barrel Reserve is 43%."""
        self.assertTrue(is_46_plus("arran-the-barrel-reserve"))
        self.assertTrue(excluded_by_abv_rule("arran-the-barrel-reserve"))

    def test_glencadam_american_oak(self):
        """Glencadam is a 46%+ brand, but American Oak Reserve is 40%."""
        self.assertTrue(
            is_46_plus("glencadam-american-oak-reserve-barrel-matured"))
        self.assertTrue(excluded_by_abv_rule(
            "glencadam-american-oak-reserve-barrel-matured"))


class NeverExcludedTest(unittest.TestCase):
    """Non-Scotch is never excluded, whatever the name says."""

    def test_woodford_baccarat(self):
        self.assertFalse(excluded_by_abv_rule("woodford-reserve-baccarat"))

    def test_hibiki_21(self):
        self.assertFalse(excluded_by_abv_rule("hibiki-21yr"))

    def test_jack_daniels_10(self):
        """'jack' must not be a Scotch token: Jack Daniel's is
        Tennessee whiskey."""
        self.assertFalse(excluded_by_abv_rule("jack-daniels-10yr"))
        self.assertIsNone(brand_of("jack-daniels-10yr"))
        self.assertFalse(is_scotch("jack-daniels-10yr"))


class DiffPartitionTest(unittest.TestCase):
    """diff_states with is_excluded splits delistings: a filtered
    bottle goes to excluded_by_rule, a genuinely gone bottle stays
    a delisting. Without is_excluded the shape is unchanged."""

    def _entry(self, retailer, canonical, price):
        return {
            "retailer": retailer,
            "canonical": canonical,
            "price_sgd": price,
            "in_stock": True,
            "seen_at": "2026-11-12T00:00:00+00:00",
            "traveller_only": False,
        }

    def test_partition(self):
        previous = {
            "Tipsy Topsy|bowmore-18yr":
                self._entry("Tipsy Topsy", "bowmore-18yr", 190.0),
            "Tipsy Topsy|glenfarclas-21yr":
                self._entry("Tipsy Topsy", "glenfarclas-21yr", 200.0),
        }
        diff = diff_states(previous, {}, is_excluded=excluded_by_abv_rule)
        self.assertEqual(len(diff["delistings"]), 1)
        self.assertEqual(diff["delistings"][0]["canonical"],
                         "glenfarclas-21yr")
        self.assertEqual(len(diff["excluded_by_rule"]), 1)
        self.assertEqual(diff["excluded_by_rule"][0]["canonical"],
                         "bowmore-18yr")

    def test_two_arg_shape_unchanged(self):
        previous = {
            "Tipsy Topsy|bowmore-18yr":
                self._entry("Tipsy Topsy", "bowmore-18yr", 190.0),
        }
        diff = diff_states(previous, {})
        self.assertNotIn("excluded_by_rule", diff)
        self.assertEqual(len(diff["delistings"]), 1)


if __name__ == "__main__":
    unittest.main()
