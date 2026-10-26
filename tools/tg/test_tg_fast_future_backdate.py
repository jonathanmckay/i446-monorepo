"""v169 review: tg-fast must refuse a backdate later than now (it would delete
the running entry and recreate it in the future)."""
import importlib.util
from datetime import datetime as real_dt
from pathlib import Path

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("tgf", HERE / "tg-fast.py")
tgf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tgf)


class _At0020(real_dt):
    @classmethod
    def now(cls, tz=None):
        return real_dt(2026, 10, 26, 0, 20)


def test_future_backdate_refused_without_touching_toggl(monkeypatch):
    calls = []
    monkeypatch.setattr(tgf, "datetime", _At0020)
    monkeypatch.setattr(tgf, "_run_cli", lambda *a: calls.append(a) or "")
    out = tgf.cmd_backdated("2330", "睡觉", "睡觉", [])
    assert out.startswith("✗") and calls == []


def test_past_backdate_still_runs(monkeypatch):
    calls = []
    monkeypatch.setattr(tgf, "datetime", _At0020)
    monkeypatch.setattr(tgf, "_run_cli", lambda *a: calls.append(a) or "")
    monkeypatch.setattr(tgf, "_update_cache", lambda *a, **k: None)
    tgf.cmd_backdated("0010", "睡觉", "睡觉", [])
    assert any(c[0] == "start" and "--at" in c for c in calls)
