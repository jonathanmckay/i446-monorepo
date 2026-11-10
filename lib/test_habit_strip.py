"""lib/habit_strip is the phone-side twin of tools/tg/janus.py's
fetch_habits_today loop (2026-11-10). This feeds both the same 0n row and
asserts they agree, so the terminal and the phone can't drift apart on what
counts as pending, done, or a prayer."""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import habit_strip as hs  # noqa: E402

ROW = [("睡觉", "449.0"), ("cpap", "0.0"), ("hiit", ""), ("ص", "8.0"), ("teams", "1.0"),
       ("词汇", ""), ("o314", "-3"), ("xk20", ""), ("n color", "x"), ("odd", "n/a"), ("", "1")]


def test_parse_rules():
    out = hs.parse_habit_row([n for n, _ in ROW], [v for _, v in ROW])
    assert out["pending"] == ["hiit", "xk20", "odd"]      # blank or unparseable
    assert out["done"] == [("睡觉", 449.0), ("teams", 1.0)]
    assert out["prayer"] == 8.0
    assert hs.parse_habit_row(["ص", "hiit"], ["", ""])["prayer"] == 0.0


def test_parity_with_the_terminal_loop(monkeypatch):
    spec = importlib.util.spec_from_file_location("janus_parity", HERE.parent / "tools/tg/janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_parity"] = mod
    spec.loader.exec_module(mod)

    class P:
        returncode = 0
        stderr = ""
        stdout = "|".join(f"{n}\t{v}" for n, v in ROW) + "||||" + "||kcal=650.0"
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: P())
    mod.fetch_habits_today()
    ours = hs.parse_habit_row([n for n, _ in ROW], [v for _, v in ROW])
    assert [n for n, v in mod.STATE.habits_today if v is None] == ours["pending"]
    assert [(n, v) for n, v in mod.STATE.habits_today if v is not None] == ours["done"]
    assert mod.STATE.prayer_count == ours["prayer"]
    assert mod.STATE.hcbi_kcal == hs.kcal_int("650.0") == 650


def test_kcal_and_dozen_helpers():
    assert hs.kcal_int("650.0") == 650 and hs.kcal_int("") is None and hs.kcal_int(None) is None
    r1 = ["-288.0", "", "-687.0", "", "4.0", "", "", "", "-2.0", "", ""]
    r2 = ["", "3.0", "", "8.0", "", "-1.0", "", "0.0", "", "5.0", "-9.0"]
    assert hs.dozen_behind(r1, r2) == [("bn", -288.0), ("fr", -687.0), ("vg", -1.0), ("nt", -2.0), ("wtr", -9.0)]


def test_strip_chips_order_and_fills():
    parsed = {"pending": ["hiit", "2nd hci"], "done": [("teams", 1.0)], "prayer": 3.0}
    colors = {"hcb": "#f81d78", "hci": "#63ede0", "hcm": "#aa00ff"}
    out = hs.strip_chips(parsed, colors=colors, ytd={"o314": -107.0, "冥想": 12.0},
                         dozen=[("bn", -288.0)], hcb_total=-1183.2, kcal=650)
    assert [c["text"] for c in out["line1"]] == ["hiit", "2nd hci", "o314 -107", "ص 3"]
    assert out["line1"][0]["bg"] == "#f81d78" and out["line1"][1]["bg"] == "#63ede0"
    assert [c["text"] for c in out["line2"]] == ["-288", "hcb -1183", "kcal 650"]
