"""Unit tests for the report layer: state diffing, schema, file IO."""

import json
import os
import tempfile
import unittest

from hunt.fx import fixed_rates
from hunt.report import (
    ParserHealth,
    build_report,
    diff_states,
    load_state,
    serialize_listing,
    write_json_file,
    write_report,
)
from hunt.models import Listing

FX = fixed_rates(1.714, 1.282, "test", "2026-11-09T00:00:00+00:00")


def _entry(retailer, canonical, price, in_stock=True,
           seen="2026-11-02T00:00:00+00:00"):
    assert price > 0, "test price must be positive"
    listing = Listing(
        retailer=retailer, name=canonical, canonical=canonical,
        size_ml=700, size_verified=True, price_sgd=price,
        in_stock=in_stock, traveller_only=False,
        eligibility="Singapore delivery", edition="", batch="",
        url="https://example.com/p/1", seen_at=seen)
    return serialize_listing(listing)


class DiffTest(unittest.TestCase):
    def test_glenfarclas21_delisting(self):
        """The 9 Nov 2026 case: Glenfarclas 21 was S$200 at Tipsy Topsy,
        then the listing disappeared entirely."""
        previous = {
            "Tipsy Topsy|glenfarclas-21yr":
                _entry("Tipsy Topsy", "glenfarclas-21yr", 200.0),
        }
        current = {}
        diff = diff_states(previous, current)
        assert len(diff["delistings"]) == 1
        assert diff["price_changes"] == []
        assert diff["stock_changes"] == []
        assert diff["new_listings"] == []
        gone = diff["delistings"][0]
        self.assertEqual(gone["retailer"], "Tipsy Topsy")
        self.assertEqual(gone["last_price_sgd"], 200.0)

    def test_price_change(self):
        previous = {"Paneco|ballantines-23yr":
                    _entry("Paneco", "ballantines-23yr", 199.95)}
        current = {"Paneco|ballantines-23yr":
                   _entry("Paneco", "ballantines-23yr", 189.95,
                          seen="2026-11-09T00:00:00+00:00")}
        diff = diff_states(previous, current)
        assert len(diff["price_changes"]) == 1
        assert diff["delistings"] == []
        change = diff["price_changes"][0]
        self.assertEqual(change["old_price_sgd"], 199.95)
        self.assertEqual(change["new_price_sgd"], 189.95)

    def test_stock_change(self):
        previous = {"iShopChangi|royal-brackla-18yr":
                    _entry("iShopChangi", "royal-brackla-18yr", 165.0)}
        current = {"iShopChangi|royal-brackla-18yr":
                   _entry("iShopChangi", "royal-brackla-18yr", 165.0,
                          in_stock=False)}
        diff = diff_states(previous, current)
        assert len(diff["stock_changes"]) == 1
        assert diff["price_changes"] == []
        change = diff["stock_changes"][0]
        self.assertTrue(change["was_in_stock"])
        self.assertFalse(change["in_stock"])

    def test_new_listing(self):
        previous = {}
        current = {"Paneco|test-bottle": _entry("Paneco", "test-bottle", 150.0)}
        diff = diff_states(previous, current)
        assert len(diff["new_listings"]) == 1
        assert diff["delistings"] == []
        self.assertEqual(diff["new_listings"][0]["price_sgd"], 150.0)

    def test_no_change_is_quiet(self):
        entry = _entry("Paneco", "ballantines-23yr", 199.95)
        diff = diff_states({"Paneco|ballantines-23yr": entry},
                           {"Paneco|ballantines-23yr": entry})
        assert diff == {"price_changes": [], "stock_changes": [],
                        "delistings": [], "new_listings": []}
        self.assertEqual(diff["price_changes"], [])


class StateCapTest(unittest.TestCase):
    def _big_state(self, count):
        assert 0 < count, "test state must be non-empty"
        state = {}
        for num in range(count):
            key = "Paneco|test-bottle-%04d" % num
            state[key] = _entry("Paneco", "test-bottle-%04d" % num,
                                150.0 + num * 0.01)
        assert len(state) == count, "test state must hold every entry"
        return state

    def test_six_thousand_entries_diff(self):
        """10 Nov 2026: the slice-6 run crashed in diff_states
        because the SG listing count passed the old 5,000 cap once
        the iShopChangi and MWA fixes landed (~5,800 entries).
        A 6,000-entry current state must diff without raising."""
        current = self._big_state(6000)
        diff = diff_states({}, current)
        assert len(diff["new_listings"]) == 6000
        assert diff["delistings"] == []

    def test_six_thousand_entries_load(self):
        """The same cap guards load_state; a 6,000-entry state file
        from the fixed run must load for the next run's diff."""
        state = self._big_state(6000)
        handle = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8")
        try:
            json.dump(state, handle)
            handle.close()
            loaded = load_state(handle.name)
            assert loaded is not None, "state file must load"
            assert len(loaded) == 6000
        finally:
            os.unlink(handle.name)


class ReportTest(unittest.TestCase):
    def _report(self):
        health = [ParserHealth(retailer="Paneco", pages_scanned=30,
                               items_found=240, status="ok",
                               detail="30 pages")]
        diff = {"price_changes": [], "stock_changes": [],
                "delistings": [], "new_listings": []}
        return build_report("hunt-20261109T000000Z",
                            "2026-11-09T00:00:00+00:00", FX, [], health,
                            diff, ["slice 1: Paneco only"])

    def test_schema_keys(self):
        report = self._report()
        assert report["schema_version"] == 1
        assert report["fx"]["gbp_to_sgd"] == 1.714
        assert report["fx"]["usd_to_sgd"] == 1.282
        assert report["parser_health"][0]["status"] == "ok"
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["run_id"], "hunt-20261109T000000Z")

    def test_write_and_reload_round_trip(self):
        report = self._report()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "report.json")
            assert write_report(report, path)
            with open(path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            assert loaded["schema_version"] == 1
            self.assertEqual(loaded["run_id"], report["run_id"])

    def test_write_json_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            assert write_json_file({"a": 1}, path)
            assert os.path.getsize(path) > 0
            self.assertTrue(os.path.exists(path))

    def test_load_state_missing_is_none(self):
        result = load_state("/tmp/does-not-exist-hunt-state.json")
        assert result is None
        self.assertIsNone(result)

    def test_load_state_corrupt_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{not json")
            result = load_state(path)
            assert result is None
            self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
