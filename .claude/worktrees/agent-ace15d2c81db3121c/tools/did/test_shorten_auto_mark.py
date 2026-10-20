"""Regression: the 😈 automation marker must survive Haiku shortening.

The raw task content carries 😈 ('created by a robot' — stale-contacts.py
convention), but Haiku-rewritten shorts and pre-fix sidecar/comment caches
stored bare prose, so dtd rendered '😈 Reach out to Jessica Allen …' as just
'Reach out to Jessica Allen' (user 2026-07-21). keep_auto_mark re-prefixes at
every lookup path, not only at generation."""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load():
    spec = importlib.util.spec_from_file_location("shorten_mark", HERE / "shorten.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["shorten_mark"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_keep_auto_mark_prefixes_when_dropped():
    m = _load()
    assert m.keep_auto_mark("😈 Reach out to X (overdue) [10]",
                            "Reach out to X [10]") == "😈 Reach out to X [10]"


def test_keep_auto_mark_no_double_prefix():
    m = _load()
    assert m.keep_auto_mark("😈 call mom", "😈 call mom") == "😈 call mom"


def test_keep_auto_mark_plain_tasks_untouched():
    m = _load()
    assert m.keep_auto_mark("pack (20) [15]", "pack [15]") == "pack [15]"


def test_sidecar_fast_path_repairs_bare_cached_short(monkeypatch):
    """A pre-fix sidecar entry (bare short, matching hash) must come back
    prefixed without any network regeneration."""
    m = _load()
    content = "😈 Reach out to Jessica Allen (overdue weekly: last contact 2026-07-10) [10]"
    h = m._hash(content)
    monkeypatch.setattr(m, "_load_sidecar",
                        lambda: {"T1": {"h": h, "short": "Reach out to Jessica Allen [10]"}})
    monkeypatch.setattr(m, "_save_sidecar", lambda d: None)
    out = m.shorten_tasks([{"id": "T1", "content": content}])
    assert out["T1"].startswith("😈 ")


def test_split_estimates_keeps_leading_marker():
    """resolve() checks the marker on PROSE — split_estimates must not eat it."""
    m = _load()
    prose, est = m.split_estimates("😈 Reach out to Jessica Allen (overdue) [10]")
    assert prose.startswith("😈")


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
