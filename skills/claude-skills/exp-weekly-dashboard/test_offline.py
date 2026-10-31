#!/usr/bin/env python3
"""Offline self-test: no ExP, no az, no network.

    python3 test_offline.py

Covers the week window, next-step naming, outcome classification, the
pagination guard, token caching, and a full mocked generate -> render run
in a temp directory (the checked-in weekly_data.json/html are untouched).
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent


def _load_generator():
    spec = importlib.util.spec_from_file_location("gen", HERE / "generate_live_weekly_data.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_renderer(tmp: Path):
    """Import the renderer against a copy so module-level DATA load is isolated."""
    spec = importlib.util.spec_from_file_location("ren", tmp / "build_weekly_cards.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gen = _load_generator()


class PureFunctions(unittest.TestCase):
    def test_week_window_is_prior_mon_sun(self):
        self.assertEqual(gen.compute_week_window(date(2026, 10, 31)), ("2026-10-19", "2026-10-25"))
        self.assertEqual(gen.compute_week_window(date(2026, 11, 2)), ("2026-10-26", "2026-11-01"))

    def test_next_step_name_suffixes(self):
        stopped = {"stoppedAt": "2026-10-20T00:00:00Z"}
        steps = [
            {"displayName": "Enable for all", "startedAt": "2026-10-21T00:00:00Z", "analysisType": "DataCollection", "splits": "100%"},
            {"displayName": "Later", "startedAt": "2026-10-25T00:00:00Z", "analysisType": "AB", "splits": "50/50"},
        ]
        self.assertEqual(gen.next_step_name(stopped, steps), "Enable for all (100% DC)")
        self.assertIsNone(gen.next_step_name(stopped, []))

    def test_format_splits_keeps_equal_arms(self):
        m = lambda *v: {"variantTrafficMaps": [{"trafficExposurePercentage": x} for x in v]}
        self.assertEqual(gen.format_splits(m(50, 50)), "50/50")
        self.assertEqual(gen.format_splits(m(33.3, 33.3, 33.4)), "33/33/33")
        self.assertEqual(gen.format_splits(m(100)), "100%")
        self.assertEqual(gen.format_splits({"analysisType": "DataCollection"}), "100%")

    def test_dedupe(self):
        started, stopped = gen.dedupe_buckets([{"stepId": "a"}, {"stepId": "b"}], [{"stepId": "b"}])
        self.assertEqual([s["stepId"] for s in started], ["a"])


class Pagination(unittest.TestCase):
    def test_stops_when_server_ignores_skip(self):
        page = {"value": [{"id": f"s{i}"} for i in range(10)]}
        with mock.patch.object(gen, "call_tool", return_value=page) as call:
            out = gen.query_steps("x")
        self.assertEqual(len(out), 10)
        self.assertEqual(call.call_count, 2)

    def test_normal_pagination(self):
        pages = [{"value": [{"id": "a"}, {"id": "b"}]}, {"value": [{"id": "c"}]}, {"value": []}]
        with mock.patch.object(gen, "call_tool", side_effect=pages):
            self.assertEqual([s["id"] for s in gen.query_steps("x")], ["a", "b", "c"])


class TokenCache(unittest.TestCase):
    def test_az_called_once(self):
        gen._TOKEN_CACHE.clear()
        with mock.patch.object(gen, "_fetch_access_token", return_value="tok") as fetch:
            for _ in range(5):
                self.assertEqual(gen.get_access_token(), "tok")
        self.assertEqual(fetch.call_count, 1)
        gen._TOKEN_CACHE.clear()


class Outcomes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        for name in ("build_weekly_cards.py", "xbox_logo.png"):
            shutil.copy(HERE / name, cls.tmp / name)
        shutil.copy(HERE / "examples" / "weekly_data.example.json", cls.tmp / "weekly_data.json")
        cls.ren = _load_renderer(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _o(self, nxt, analysis="AB", splits="50/50"):
        return self.ren._compute_outcome({"nextStepName": nxt, "analysisType": analysis, "splits": splits,
                                          "experimentId": "e", "stepId": "s", "startedAt": "2026-01-01"})

    def test_table(self):
        self.assertEqual(self._o("Step 2 (50/50 AB)"), "Promoted")
        self.assertEqual(self._o("Switch to 50% (AB)"), "Promoted")
        self.assertEqual(self._o("Switch to 100% (DC)"), "Rolled out")
        self.assertEqual(self._o("Step-3 (DC)"), "Rolled out")
        # Regression: substring "ab" in "Enable"/"Disable" used to read as Promoted.
        self.assertEqual(self._o("Enable for all (100% DC)"), "Rolled out")
        self.assertEqual(self._o("Disable legacy (DC)"), "Rolled out")
        self.assertEqual(self._o("", analysis="DataCollection", splits="100%"), "Completed")
        self.assertEqual(self._o(""), "Stopped")


class EndToEndMocked(unittest.TestCase):
    """generate (mocked MCP) -> weekly_data.json -> render HTML, in a temp dir."""

    def test_pipeline(self):
        step_started = {"id": "st1", "experimentId": "e1", "displayName": "Step 1", "startedAt": "2026-10-20T10:00:00Z",
                        "stoppedAt": None, "analysisType": "AB",
                        "variantTrafficMaps": [{"trafficExposurePercentage": 50}, {"trafficExposurePercentage": 50}]}
        step_stopped = {"id": "sp1", "experimentId": "e1", "displayName": "Step 0", "startedAt": "2026-10-01T10:00:00Z",
                        "stoppedAt": "2026-10-20T09:00:00Z", "analysisType": "AB",
                        "variantTrafficMaps": [{"trafficExposurePercentage": 50}, {"trafficExposurePercentage": 50}]}
        step_active = {"id": "ac1", "experimentId": "e1", "displayName": "Old", "startedAt": "2026-09-01T10:00:00Z",
                       "stoppedAt": None, "analysisType": "AB", "variantTrafficMaps": []}
        experiment = {"id": "e1", "featureId": "f1", "displayName": "Exp One", "audienceFilterExpression": "",
                      "experimentSteps": [step_stopped, step_started]}
        feature = {"id": "f1", "displayName": "Feature One", "description": "d", "experimentationGroup": "xbox~playxbox"}

        def fake_call(name, args):
            if name == "query_analyses":
                return {"value": []}
            if "entityId" in args:
                return {"value": [experiment if args["type"] == "Experiment" else feature]}
            if args["skip"]:
                return {"value": []}
            f = args["odataFilter"]
            if f.startswith("StartedAt ge"):
                return {"value": [step_started]}
            if f.startswith("StoppedAt ge"):
                return {"value": [step_stopped]}
            return {"value": [step_active]}

        with mock.patch.object(gen, "call_tool", side_effect=fake_call):
            data = gen.build_weekly_data("2026-10-19", "2026-10-25")
        self.assertEqual(data["buckets"]["stopped"][0]["nextStepName"], "Step 1 (50/50 AB)")
        self.assertEqual(data["buckets"]["started"][0]["splits"], "50/50")

        tmp = Path(tempfile.mkdtemp())
        try:
            for name in ("build_weekly_cards.py", "xbox_logo.png"):
                shutil.copy(HERE / name, tmp / name)
            (tmp / "weekly_data.json").write_text(json.dumps(data), encoding="utf-8")
            r = subprocess.run([sys.executable, str(tmp / "build_weekly_cards.py")], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            html = (tmp / "weekly_dashboard.html").read_text(encoding="utf-8")
            self.assertIn("Feature One", html)
            self.assertIn("Promoted", html)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
