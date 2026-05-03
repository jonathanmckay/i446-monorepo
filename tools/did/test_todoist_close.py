#!/usr/bin/env python3
"""Regression tests for Todoist close logic in did-fast.py.

Pure unit tests with mocked HTTP — no real Todoist calls.

Run: python3 test_todoist_close.py
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch, MagicMock

# Load did-fast.py (hyphenated filename).
_HERE = Path(__file__).parent
_SPEC = importlib.util.spec_from_file_location("did_fast", _HERE / "did-fast.py")
df = importlib.util.module_from_spec(_SPEC)
sys.modules["did_fast"] = df  # required so dataclass.__module__ lookup works
_SPEC.loader.exec_module(df)


def _mk_response(status: int, body: bytes = b""):
    """Build a context-manager-compatible mock response."""
    resp = MagicMock()
    resp.__enter__ = lambda self: self
    resp.__exit__ = lambda *a: False
    resp.status = status
    resp.read.return_value = body
    return resp


def _mk_http_error(code: int, body: bytes = b"err"):
    """Build a urllib HTTPError with a readable body."""
    return urllib.error.HTTPError(
        url="x", code=code, msg="err", hdrs=None,
        fp=io.BytesIO(body),
    )


class CloseTodoistTask(unittest.TestCase):
    def test_success_then_verify_archived_404(self):
        """Close returns 204; verify GET 404 → ok (archived)."""
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append((req.get_method(), req.full_url))
            if req.get_method() == "POST":
                return _mk_response(204)
            # GET verify
            raise _mk_http_error(404)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            tid, ok, err = df.close_todoist_task("T1")

        self.assertEqual(tid, "T1")
        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0], "POST")
        self.assertEqual(calls[1][0], "GET")

    def test_success_then_verify_checked_true(self):
        """Close returns 204; GET shows checked=true → ok."""
        body = json.dumps({"id": "T2", "checked": True, "due": None}).encode()

        def fake_urlopen(req, timeout=None):
            if req.get_method() == "POST":
                return _mk_response(204)
            return _mk_response(200, body)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            tid, ok, err = df.close_todoist_task("T2")

        self.assertTrue(ok)
        self.assertIsNone(err)

    def test_recurring_task_accepted_without_checked(self):
        """Recurring tasks reschedule (checked stays false). Should still pass."""
        body = json.dumps({
            "id": "T3", "checked": False,
            "due": {"date": "2026-05-04", "is_recurring": True},
        }).encode()

        def fake_urlopen(req, timeout=None):
            if req.get_method() == "POST":
                return _mk_response(204)
            return _mk_response(200, body)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            tid, ok, err = df.close_todoist_task("T3")

        self.assertTrue(ok)
        self.assertIsNone(err)

    def test_verify_failed_still_open(self):
        """Close returns 204 but task still shows checked=false (non-recurring)."""
        body = json.dumps({"id": "T4", "checked": False, "due": None}).encode()

        def fake_urlopen(req, timeout=None):
            if req.get_method() == "POST":
                return _mk_response(204)
            return _mk_response(200, body)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            tid, ok, err = df.close_todoist_task("T4")

        self.assertFalse(ok)
        self.assertIsNotNone(err)
        self.assertIn("verify_failed", err)

    def test_retry_on_5xx(self):
        """First POST gets 500, retry POST gets 204; verify 404 → ok with one retry."""
        attempts = {"post": 0, "get": 0}

        def fake_urlopen(req, timeout=None):
            if req.get_method() == "POST":
                attempts["post"] += 1
                if attempts["post"] == 1:
                    raise _mk_http_error(500, b"server err")
                return _mk_response(204)
            attempts["get"] += 1
            raise _mk_http_error(404)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            tid, ok, err = df.close_todoist_task("T5")

        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertEqual(attempts["post"], 2, "should have retried once")

    def test_no_retry_on_401(self):
        """401 must not retry — it's a real auth failure."""
        attempts = {"post": 0}

        def fake_urlopen(req, timeout=None):
            attempts["post"] += 1
            raise _mk_http_error(401, b'{"error":"unauthorized"}')

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            tid, ok, err = df.close_todoist_task("T6")

        self.assertFalse(ok)
        self.assertIn("401", err)
        self.assertEqual(attempts["post"], 1, "must NOT retry on 4xx")

    def test_no_retry_on_404(self):
        """404 from close should also not retry."""
        attempts = {"post": 0}

        def fake_urlopen(req, timeout=None):
            attempts["post"] += 1
            raise _mk_http_error(404)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            tid, ok, err = df.close_todoist_task("T7")

        self.assertFalse(ok)
        self.assertEqual(attempts["post"], 1)

    def test_close_todoist_tasks_dict_shape(self):
        """The batch helper must return dict[id -> (bool, error|None)]."""
        body = json.dumps({"id": "X", "checked": True, "due": None}).encode()

        def fake_urlopen(req, timeout=None):
            if req.get_method() == "POST":
                return _mk_response(204)
            return _mk_response(200, body)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            results = df.close_todoist_tasks(["A", "B"])

        self.assertEqual(set(results.keys()), {"A", "B"})
        for tid, val in results.items():
            self.assertIsInstance(val, tuple)
            self.assertEqual(len(val), 2)
            ok, err = val
            self.assertTrue(ok)
            self.assertIsNone(err)

    def test_empty_batch(self):
        self.assertEqual(df.close_todoist_tasks([]), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
