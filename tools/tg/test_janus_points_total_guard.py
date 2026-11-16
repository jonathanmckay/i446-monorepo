"""Regression test (2026-06-22): the in-progress block showed 4351分 instead of
385. Col D (=SUM(P:Y)) is read mid-recalc during did/daemon writes and returns
garbage (the rejection log captured D=-46 for ~26min, plus high spikes).
fetch_points committed today_points on EVERY read, before the block consistency
guard, so a torn total poisoned the topline — and once the current-block 分
reconstruction (Σ − locked) started depending on the total, that torn read
corrupted the live block. Fix: gate today_points on a plausibility bound and pair
fresh blocks only with a freshly-accepted total.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_totguard", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_totguard"] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeProc:
    def __init__(self, out, rc=0):
        self.returncode = rc
        self.stdout = out
        self.stderr = ""


def _run(monkeypatch, m, out, tmp_path=None):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeProc(out))
    # Regression (2026-07-30): these fixtures ARE the exact D=-46/4351/1523
    # torn reads the log exists to capture, so without this every test run
    # appended fake "rejected read" entries to the real, shared
    # /tmp/janus-points-rejected.log -- polluting live production diagnostics
    # with test artifacts indistinguishable from genuine torn reads.
    if tmp_path is not None:
        monkeypatch.setattr(m, "POINTS_REJECTED_LOG", tmp_path / "rejected.log")
    m.fetch_points()


def test_rejected_read_logs_to_configurable_path_not_hardcoded_shared_file(monkeypatch, tmp_path):
    """Bug 2026-07-30: fetch_points hardcoded open("/tmp/janus-points-rejected.log",
    "a") inline, so every test run of a torn-read fixture (this file's whole
    purpose) appended fake entries to the real, shared, production log --
    indistinguishable from genuine torn reads and actively hindering live
    diagnosis of a real "stale points" bug report. fetch_points must write to
    the module-level POINTS_REJECTED_LOG so tests can redirect it."""
    m = _load_tui()
    m.STATE.today_points = 606
    m.STATE.block_points = {}
    real_log = Path("/tmp/janus-points-rejected.log")
    before = real_log.stat().st_size if real_log.exists() else None
    fake_log = tmp_path / "rejected.log"
    _run(monkeypatch, m, "-46|=D|=D-G|=D-SUM(G,H)|=D-SUM(G,H,I)|=D|=D|=D|=D|=D", tmp_path)
    assert fake_log.exists() and "cand=-46" in fake_log.read_text(), \
        "the rejected read must be logged to the configured path"
    after = real_log.stat().st_size if real_log.exists() else None
    assert before == after, "the real shared log must be untouched by a test run"


def test_negative_torn_total_is_rejected(monkeypatch, tmp_path):
    m = _load_tui()
    m.STATE.today_points = 606
    m.STATE.block_points = {"午": 215}
    # The exact dawn torn read from the log: D=-46, every block still residual.
    _run(monkeypatch, m, "-46|=D|=D-G|=D-SUM(G,H)|=D-SUM(G,H,I)|=D|=D|=D|=D|=D", tmp_path)
    assert m.STATE.today_points == 606, "a negative torn total must not be adopted"
    assert m.STATE.block_points == {"午": 215}, "blocks kept when total is torn"


def test_high_spike_total_is_rejected(monkeypatch, tmp_path):
    m = _load_tui()
    m.STATE.today_points = 606
    m.STATE.block_points = {"巳": 3, "午": 215}
    # D spikes to 4351 (the reported bug) while blocks read normally.
    _run(monkeypatch, m, "4351||3|215|=D|=D|=D|=D|=D|=D", tmp_path)
    assert m.STATE.today_points == 606, "an implausibly high total must not be adopted"
    assert m.STATE.block_points == {"巳": 3, "午": 215}, "blocks not paired with a torn total"


def test_plausible_total_and_blocks_are_adopted(monkeypatch, tmp_path):
    m = _load_tui()
    m.STATE.today_points = 0
    m.STATE.block_points = {}
    # D | 卯 | 辰 | 巳=3 | 午=215 | 未… residual
    _run(monkeypatch, m, "606|||3|215|=D|=D|=D|=D|=D", tmp_path)
    assert m.STATE.today_points == 606
    assert m.STATE.block_points == {"巳": 3, "午": 215}


def test_spike_under_cap_caught_by_py_crosscheck(monkeypatch, tmp_path):
    """The 1523-on-a-758分-day bug: a torn total below the 2000 cap that a fixed
    bound can't catch. D must equal its own =SUM(P:Y); here it doesn't, so reject.
    Layout: D | 9×G:O | 10×P:Y (P:Y sums to 758, D claims 1523)."""
    m = _load_tui()
    m.STATE.today_points = 758
    m.STATE.block_points = {"午": 200}
    raw = "1523|=D|=D|=D|=D|=D|=D|=D|=D|=D|21|29|377|33|195|34|13|6|50|0"
    _run(monkeypatch, m, raw, tmp_path)
    assert m.STATE.today_points == 758, "D≠SUM(P:Y) is a torn read; keep last good"


def test_consistent_total_with_py_is_adopted(monkeypatch, tmp_path):
    """D == SUM(P:Y) → trustworthy, committed."""
    m = _load_tui()
    m.STATE.today_points = 0
    raw = "758|=D|=D|=D|=D|=D|=D|=D|=D|=D|21|29|377|33|195|34|13|6|50|0"
    _run(monkeypatch, m, raw, tmp_path)
    assert m.STATE.today_points == 758


def test_total_trustworthy_unit():
    m = _load_tui()
    assert m._total_trustworthy(758, 758) is True
    assert m._total_trustworthy(1523, 758) is False  # disagrees with P:Y
    assert m._total_trustworthy(-46, None) is False   # negative, no P:Y
    assert m._total_trustworthy(4351, None) is False  # over cap, no P:Y
    assert m._total_trustworthy(606, None) is True    # under cap, no P:Y
    assert m._total_trustworthy(759, 758) is True     # ±1 rounding tolerance
    # Regression (2026-07-03): D and its own P:Y range torn to the SAME spike must
    # STILL be rejected — agreement above the cap isn't proof of a real total.
    # (This set the cold-start current-block header to 5064分.)
    assert m._total_trustworthy(5064, 5064) is False  # matching torn spike over cap
    assert m._total_trustworthy(3001, 3001) is False  # just over the cap, agreeing
    assert m._total_trustworthy(3000, 3000) is True   # exactly at the cap is fine
    assert m._total_trustworthy(2173, 2173) is True   # 2026-11-15: a real day over 2000


def test_residual_block_value_read_directly_rounds_once(monkeypatch, tmp_path):
    """The current/residual block's 分 is read straight from Neon's own
    computed VALUE for that cell (not reconstructed via Σ − locked, removed
    2026-07-20 after it produced 5064分/6932分 on-screen) — a single
    int(round()) of whatever Neon itself shows, for whichever block's
    formula happens to be the live residual. Layout: D | 9 formula/literal
    strings (卯..亥) | 10 P:Y values | 9 G:O VALUES (卯..亥, same order)."""
    m = _load_tui()
    m.STATE.block_points = {}
    # 卯=6 (literal), 辰=217.5 (literal), 巳..亥 all residual formulas — only
    # 午's residual VALUE (index 3) is nonzero, the rest resolve to 0.
    raw_terms = ["6", "217.5"] + ["=D"] * 7
    py_vals = ["511"] + ["0"] * 9  # sums to today_points, passes the D==SUM(P:Y) cross-check
    gio_vals = ["0", "0", "0", "288", "0", "0", "0", "0", "0"]
    out = "|".join(["511.357142857143", *raw_terms, *py_vals, *gio_vals])
    _run(monkeypatch, m, out, tmp_path)
    assert m.STATE.today_points == 511
    assert m.STATE.block_points.get("午") == 288, "residual block value must round once, straight from Neon"
    assert m.STATE.block_points.get("卯") == 6
    assert m.STATE.block_points.get("辰") == 218  # 217.5 rounds to even


# Today's actual row at 19:35 on 2026-11-15 (from /tmp/janus-points-rejected.log).
_RAW_2173 = ("2173.071428571428||227.5|542.571428571429|220.5|602.999999999999|145.5|175"
             "|=D317-SUM(G317,H317,I317,J317,K317,L317,M317)"
             "|=D317-SUM(G317,H317,I317,J317,K317,L317,M317,N317)"
             "|85.0|169.0|90.0|668.0|579.0|12.0|169.0|202.071428571429|189.0|10.0"
             "||227.5|542.571428571429|220.5|602.999999999999|145.5|175.0|259.0|0.0")


def test_real_2173_day_is_adopted(monkeypatch, tmp_path):
    """Regression (2026-11-15, "janus is showing no points for the day or per
    block"): Σ reached 2173, above the old 2000 cap, so every read was
    rejected; the header froze at 1975 and a fresh janus showed nothing."""
    m = _load_tui()
    m.STATE.today_points = 1975
    m.STATE.block_points = {"戌": 61}
    _run(monkeypatch, m, _RAW_2173, tmp_path)
    assert m.STATE.today_points == 2173
    assert m.STATE.block_points["戌"] == 259
    assert m.STATE.block_points["未"] == 603


def test_day_over_the_cap_is_adopted_on_the_second_read(monkeypatch, tmp_path):
    """Above the cap, a total that agrees with P:Y and repeats on the next
    read is real; one sighting alone is not proof."""
    m = _load_tui()
    m.STATE.today_points = 2950
    raw = "3200|=D|=D|=D|=D|=D|=D|=D|=D|=D|21|29|2819|33|195|34|13|6|50|0"
    _run(monkeypatch, m, raw, tmp_path)
    assert m.STATE.today_points == 2950
    _run(monkeypatch, m, raw, tmp_path)
    assert m.STATE.today_points == 3200


def test_transient_over_cap_spike_is_never_adopted(monkeypatch, tmp_path):
    """The 5064/5064 torn spike (2026-07-03) appears once, between good reads."""
    m = _load_tui()
    good = "758|=D|=D|=D|=D|=D|=D|=D|=D|=D|21|29|377|33|195|34|13|6|50|0"
    spike = "5064|=D|=D|=D|=D|=D|=D|=D|=D|=D|21|29|4683|33|195|34|13|6|50|0"
    for raw in (good, spike, good, spike):
        _run(monkeypatch, m, raw, tmp_path)
        assert m.STATE.today_points == 758


def test_high_total_confirmed_unit():
    import datetime as dt
    m = _load_tui()
    d1, d2 = dt.date(2026, 11, 15), dt.date(2026, 11, 16)
    assert m._high_total_confirmed(3173, 3173, d1) is False  # first sighting
    assert m._high_total_confirmed(3173, 3173, d1) is True   # repeated
    assert m._high_total_confirmed(3223, 3223, d1) is False  # new value: wait again
    assert m._high_total_confirmed(3223, 3223, d2) is False  # other day's value
    assert m._high_total_confirmed(3223, 1975, d2) is False  # P:Y disagrees
    assert m._high_total_confirmed(3223, 3223, d2) is False  # disagreement reset it
    assert m._high_total_confirmed(3223, None, d2) is False  # no cross-check
