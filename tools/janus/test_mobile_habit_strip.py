"""JM 2026-11-10: "for janus CLI and iOS ... only show the -1n tasks that are
not done, as well as ... the total number of calories eaten today on the hcb
line." The phone clients get the terminal's habit strip as two chip lines in
/api/timeline["habits"], built from the excel-http daemon (lib/habit_strip)."""
import datetime as dt

from test_mobile import jm  # noqa: F401

HDR = ["睡觉", "cpap", "hiit", "ص", "teams", "词汇", "o314", "xk20"]
ROW = ["449.0", "0.0", "", "8.0", "1.0", "", "-3", ""]


def _stub_excel(jm, monkeypatch, *, row=315, kcal="650.0", hcb="-1183.2"):
    from neon import excel
    monkeypatch.setattr(excel, "lookup_row", lambda sheet, ds: row if sheet == "0n" else None)
    def read_range(sheet, rng):
        if sheet == "0n" and rng == "D1:AS1":
            return {"ok": True, "rows": [HDR]}
        if sheet == "0n" and rng == f"D{row}:AS{row}":
            return {"ok": True, "rows": [ROW]}
        if sheet == "0n" and rng == "AQ375:AS375":
            return {"ok": True, "rows": [["-107.0", "12.0", "-3.0"]]}
        if sheet == "hcbi" and rng == "E384:X388":
            r1 = ["-288.0", "", "-687.0", "", "4.0", "", "", "", "-2.0", "", "", ""] + [""] * 8
            r2 = ["", "3.0", "", "8.0", "", "-1.0", "", "0.0", "", "5.0", "-9.0", ""] + [""] * 8
            return {"ok": True, "rows": [r1, r2, [""] * 20, [""] * 20, [""] * 19 + [hcb]]}
        return {"ok": False}
    monkeypatch.setattr(excel, "read_range", read_range)
    def read(sheet, col, **kw):
        if sheet == "hcbi" and col == "U":
            return {"ok": True, "value": kcal}
        return {"ok": False}
    monkeypatch.setattr(excel, "read", read)


def _texts(line):
    return [c["text"] for c in line]


def test_strip_lists_only_pending_habits_then_ytd_owed_then_prayer(jm, monkeypatch):
    _stub_excel(jm, monkeypatch)
    jm._HABIT_HEADER_CACHE.clear()
    h = jm._habit_strip(dt.date.today())
    # hiit/xk20 blank -> pending; 睡觉/teams done -> gone; cpap explicit 0 -> gone;
    # 词汇 skip-listed; o314 is a YTD habit (its daily cell never shows).
    assert _texts(h["line1"]) == ["hiit", "xk20", "o314 -107", "其他人 -3", "ص 8"]
    assert "449" not in str(h) and "teams" not in str(h) and "cpap" not in str(h)
    hiit = h["line1"][0]
    assert hiit["bg"] == jm.COLORS["hcb"]       # domain fill, like the terminal


def test_strip_hcb_line_has_dozen_behind_hcb_standing_then_kcal(jm, monkeypatch):
    _stub_excel(jm, monkeypatch)
    jm._HABIT_HEADER_CACHE.clear()
    h = jm._habit_strip(dt.date.today())
    assert _texts(h["line2"]) == ["-288", "-687", "-1", "-2", "-9", "hcb -1183", "kcal 650"]
    assert h["line2"][-1]["bg"] == jm.COLORS["hcb"]


def test_timeline_payload_carries_the_strip_on_the_full_today_pass_only(jm, monkeypatch):
    _stub_excel(jm, monkeypatch)
    jm._HABIT_HEADER_CACHE.clear()
    monkeypatch.setattr(jm, "_fetch_today", lambda *a: [])
    monkeypatch.setattr(jm, "_fetch_events_today", lambda *a: [])
    full = jm.build_timeline()
    assert full["habits"]["line2"][-1]["text"] == "kcal 650"
    assert "habits" not in jm.build_timeline(lite=True)
    yesterday = dt.datetime.now(jm.TZ).date() - dt.timedelta(days=1)
    assert "habits" not in jm.build_timeline(yesterday)


def test_a_failed_read_drops_the_strip_not_the_timeline(jm, monkeypatch):
    from neon import excel
    monkeypatch.setattr(excel, "lookup_row", lambda *a, **k: None)
    monkeypatch.setattr(jm, "_fetch_today", lambda *a: [])
    monkeypatch.setattr(jm, "_fetch_events_today", lambda *a: [])
    tl = jm.build_timeline()
    assert "habits" not in tl and "rows" in tl


def test_blank_kcal_means_no_kcal_chip(jm, monkeypatch):
    _stub_excel(jm, monkeypatch, kcal="")
    jm._HABIT_HEADER_CACHE.clear()
    h = jm._habit_strip(dt.date.today())
    assert not any(c["text"].startswith("kcal") for c in h["line2"])
    assert h["line2"][-1]["text"] == "hcb -1183"
