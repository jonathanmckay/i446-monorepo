"""Tests for domain-fast.py — dtd ctrl-g domain (project) label swap."""
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load():
    spec = importlib.util.spec_from_file_location("domain_fast", HERE / "domain-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["domain_fast"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_swap_domain_replaces_existing_and_preserves_bookkeeping():
    mod = _load()
    # Existing domain (i9) replaced; section + goal labels preserved in order.
    assert mod.swap_domain(["i9", "0neon", "#0g"], "m5x2") == ["0neon", "#0g", "m5x2"]


def test_swap_domain_appends_when_no_existing_domain():
    mod = _load()
    assert mod.swap_domain(["posthoc"], "xk87") == ["posthoc", "xk87"]
    assert mod.swap_domain([], "hcm") == ["hcm"]


def test_swap_domain_drops_multiple_stale_domains():
    mod = _load()
    # Defensive: if a task somehow carries two domain labels, both go.
    assert mod.swap_domain(["i9", "m5x2", "0neon"], "hcb") == ["0neon", "hcb"]


def test_main_updates_todoist_and_patches_cache(tmp_path, monkeypatch):
    mod = _load()
    cache = {"0neon": [{"id": "42", "content": "call dad", "labels": ["i9", "0neon"]}]}
    cf = tmp_path / "cache.json"
    cf.write_text(json.dumps(cache))

    calls = []
    mod._df._api = lambda method, path, body=None, **k: calls.append((method, path, body))

    monkeypatch.setattr(sys, "argv", ["domain-fast.py", "call dad", "m5x2", str(cf)])
    assert mod.main() == 0

    # Todoist got a labels replacement on the right task.
    assert calls and calls[0][0] == "POST" and calls[0][1] == "/tasks/42"
    assert set(calls[0][2]["labels"]) == {"0neon", "m5x2"}
    # Cache patched so the row recolors on reload.
    patched = json.loads(cf.read_text())
    assert patched["0neon"][0]["labels"] == ["0neon", "m5x2"]


def test_main_rejects_unknown_domain(tmp_path, monkeypatch):
    mod = _load()
    cf = tmp_path / "cache.json"
    cf.write_text(json.dumps({"0neon": [{"id": "1", "content": "x", "labels": ["i9"]}]}))
    called = []
    mod._df._api = lambda *a, **k: called.append(a)
    monkeypatch.setattr(sys, "argv", ["domain-fast.py", "x", "notadomain", str(cf)])
    assert mod.main() == 1
    assert not called, "must not hit Todoist for an invalid domain"


def test_main_no_match_does_not_call_todoist(tmp_path, monkeypatch):
    mod = _load()
    cf = tmp_path / "cache.json"
    cf.write_text(json.dumps({"0neon": [{"id": "1", "content": "real task", "labels": ["i9"]}]}))
    called = []
    mod._df._api = lambda *a, **k: called.append(a)
    monkeypatch.setattr(sys, "argv", ["domain-fast.py", "ghost task", "m5x2", str(cf)])
    assert mod.main() == 1
    assert not called
