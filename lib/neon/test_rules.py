"""lib/neon/rules.py — rule engine v0 (Neon off Excel step 3)."""
from __future__ import annotations

import pytest

from neon import ledger, rules


def _e(**kw):
    base = {"ts": "2026-10-18T08:30:00", "kind": "append", "sheet": "0分", "col": "R", "row": 289,
            "date": "10/18", "value": "+15", "before": "='0n'!BK292 + 0", "after": "='0n'!BK292 + 0+15",
            "after_value": "85.0", "before_value": "70.0", "src": "did x", "chain": "ok"}
    base.update(kw)
    return base


def _on(col, value, ts="2026-10-18T07:00:00", kind="write"):
    av = "" if value is None else str(value)
    return _e(ts=ts, kind=kind, sheet="0n", col=col, row=292, value=value if value is not None else "",
              before="", after=av, after_value=av, before_value="")


def _snap(col, value, formula, ts="2026-10-18T23:55:00"):
    return _e(ts=ts, kind="snapshot", sheet="0分", col=col, value=None, before=formula, after=formula,
              after_value=str(value), before_value=None, src="snapshot")


@pytest.fixture()
def db(tmp_path):
    c = ledger.connect(tmp_path / "neon.db")
    yield c
    c.close()


# ── tokenizer ────────────────────────────────────────────────────────────────

def test_split_terms_respects_parens_and_quotes():
    f = "='0n'!BF292 + 0-20+'1n+'!C5-45 + 20 + 15 + 20"
    assert rules.split_terms(f) == ["'0n'!BF292", "+0", "-20", "+'1n+'!C5", "-45", "+20", "+15", "+20"]
    f2 = "=IF(AND('0n'!T292<>0,'0n'!T292<>\"\"),9,0) + 80 + 30 + 20"
    t = rules.classify_terms(f2)
    assert t["literals"] == 130 and len(t["funcs"]) == 1 and t["flow_refs"] == []


def test_classify_terms_buckets():
    t = rules.classify_terms("='0n'!BF292 + 0-20+'1n+'!C5-45 + 20 + 15 + 20")
    assert t["literals"] == -10 and t["flow_refs"] == ["'0n'!BF292"] and t["week_refs"] == ["+'1n+'!C5"]
    assert rules.classify_terms("=13+13 + 10 + 10 + 13 + 13")["literals"] == 72
    assert rules.classify_terms("=60 + 100")["literals"] == 160
    assert rules.classify_terms("")["literals"] == 0
    o = rules.classify_terms("=hcbi!AA293+hcbi!Y293 + 20")
    assert o["literals"] == 20 and o["other_refs"] == ["hcbi!AA293", "+hcbi!Y293"]


# ── marked() semantics ───────────────────────────────────────────────────────

def test_marked_follows_not_isblank():
    assert rules.marked(None) is None                                  # never observed
    assert rules.marked({"value": 1.0, "text": None}) is True
    assert rules.marked({"value": 0.0, "text": None}) is True          # explicit 0 counts (Excel quirk)
    assert rules.marked({"value": None, "text": ""}) is False          # blank
    assert rules.marked({"value": None, "text": "x"}) is True


# ── derive_day ───────────────────────────────────────────────────────────────

def test_derive_i9_flow_plus_credits_matches_snapshot(db, monkeypatch):
    monkeypatch.setattr(rules, "load_weights", lambda conn=None: {"U": 10, "V": 8, "W": 20, "X": 20, "Y": 7, "Z": 5})
    for col, v in (("U", 1), ("V", None), ("W", 1), ("X", 0), ("Y", None), ("Z", None)):
        ledger.record(_on(col, v), db)                  # U, W marked; X explicit 0 (counts); V/Y/Z blank
    ledger.record(_e(value="+15", after="='0n'!BK292 + 0+15", after_value="65"), db)
    ledger.record(_snap("R", 65, "='0n'!BK292 + 0+15"), db)
    day = rules.derive_day("2026-10-18", db)
    r = day["cols"]["R"]
    assert r["components"] == {"literals": 15.0, "flow": 50.0}        # 10+20+20
    assert r["derived"] == 65 and r["observed"] == 65 and r["status"] == "ok"


def test_unknown_input_makes_partial_not_mismatch(db, monkeypatch):
    monkeypatch.setattr(rules, "load_weights", lambda conn=None: {"U": 10, "V": 8, "W": 20, "X": 20, "Y": 7, "Z": 5})
    ledger.record(_on("U", 1), db)                                     # only U ever observed
    ledger.record(_snap("R", 70, "='0n'!BK292 + 0"), db)
    r = rules.derive_day("2026-10-18", db)["cols"]["R"]
    assert r["status"] == "partial" and r["derived"] == 10
    assert set(r["unknown"]) == {"0n!V", "0n!W", "0n!X", "0n!Y", "0n!Z"}


def test_mismatch_when_all_inputs_known_but_excel_differs(db, monkeypatch):
    monkeypatch.setattr(rules, "load_weights", lambda conn=None: {"AA": 3, "AB": 15, "AC": 15})
    for col in ("AA", "AB", "AC"):
        ledger.record(_on(col, 1), db)
    ledger.record(_snap("S", 543, "='0n'!BH292 + 35+ 7*45"), db)      # Excel has 7*45 we can't parse as literal
    r = rules.derive_day("2026-10-18", db)["cols"]["S"]
    assert r["status"] == "mismatch" and r["components"]["flow"] == 33


def test_q_bonus_requires_nonzero_0g_mark(db, monkeypatch):
    monkeypatch.setattr(rules, "load_weights", lambda conn=None: {})
    ledger.record(_on("T", 0), db)                                      # explicit 0 → no bonus
    ledger.record(_snap("Q", 130, "=IF(AND('0n'!T292<>0,'0n'!T292<>\"\"),9,0) + 80 + 30 + 20"), db)
    q = rules.derive_day("2026-10-18", db)["cols"]["Q"]
    assert q["components"]["bonus"] == 0 and q["derived"] == 130 and q["status"] == "ok"
    ledger.record(_on("T", 1, ts="2026-10-18T08:00:00"), db)
    q = rules.derive_day("2026-10-18", db)["cols"]["Q"]
    assert q["components"]["bonus"] == 9 and q["derived"] == 139


def test_p_is_last_set_plus_later_appends(db, monkeypatch):
    monkeypatch.setattr(rules, "load_weights", lambda conn=None: {})
    ledger.record(_e(ts="2026-10-18T06:00:00", kind="write", col="P", value="=13+13", after="=13+13",
                     after_value="26", before_value="0", src="ritual 辰 -1n"), db)
    ledger.record(_e(ts="2026-10-18T07:00:00", col="P", value="+10", after="=13+13+10", after_value="36",
                     before_value="26", src="manual +10 hcbp"), db)
    ledger.record(_e(ts="2026-10-18T08:00:00", kind="write", col="P", value="=13+13+13", after="=13+13+13",
                     after_value="39", before_value="36", src="block-turnover -1n reconcile"), db)   # wipes the +10
    ledger.record(_snap("P", 39, "=13+13+13"), db)
    p = rules.derive_day("2026-10-18", db)["cols"]["P"]
    assert p["components"] == {"last_set": 39.0, "appends_after": 0.0} and p["status"] == "ok"


def test_w_is_observe_only_and_sigma_uses_observation(db, monkeypatch):
    monkeypatch.setattr(rules, "load_weights", lambda conn=None: {})
    ledger.record(_snap("W", 20.93, "=hcbi!AA293+hcbi!Y293 + 20"), db)
    ledger.record(_snap("Y", 160, "=60 + 100"), db)
    ledger.record(_snap("D", 180.93, "=SUM(P289:Y289)"), db)
    day = rules.derive_day("2026-10-18", db)
    assert day["cols"]["W"]["status"] == "observe-only"
    assert day["cols"]["Y"]["derived"] == 160 and day["cols"]["Y"]["status"] == "ok"
    assert day["cols"]["D"]["status"] == "partial"                     # P has no set yet → unknown part


def test_summarize_and_blocks(db, monkeypatch):
    monkeypatch.setattr(rules, "load_weights", lambda conn=None: {})
    ledger.record(_e(kind="write", col="G", value="71", after="71", after_value="71", before_value="71",
                     src="block-turnover lock G"), db)
    ledger.record(_snap("Y", 160, "=60 + 100"), db)
    ledger.record(_e(), db)                                            # +15 i9 credit at 08:30 → 巳
    day = rules.derive_day("2026-10-18", db)
    assert day["blocks"]["卯"] == {"lock": 71.0, "observed": 71.0}
    s = rules.summarize(day)
    assert "Y" in s["ok"] and "W" not in s["mismatch"]
    assert rules.event_blocks("2026-10-18", db) == {"巳": 15.0}         # the fixture credit at 08:30


def test_products_in_formula_tail_count_as_literals():
    t = rules.classify_terms("='0n'!BH292 + 35+ 7*45 + 45 + 20 + 75 + 20")
    assert t["literals"] == 510 and t["n_literals"] == 6
    assert rules._eval_arith("7*45") == 315 and rules._eval_arith("-3*0.5") == -1.5
    assert rules._eval_arith("'0n'!BK") is None and rules._eval_arith("7*x") is None


def test_week_refs_resolve_from_1n_row5_snapshot(db, monkeypatch):
    monkeypatch.setattr(rules, "load_weights", lambda conn=None: {})
    ledger.record(_e(ts="2026-10-18T03:50:00", kind="baseline", sheet="1n+", col="C", row=5, date=None,
                     value=None, before=None, after="15", after_value="15.0", src="snapshot 1n+ row5"), db)
    ledger.record(_snap("T", 5, "='0n'!BF292 + 0-10+'1n+'!C5"), db)
    for col in rules.FLOW_RANGES["T"]:
        ledger.record(_on(col, None), db)                               # all blank → flow 0
    t = rules.derive_day("2026-10-18", db)["cols"]["T"]
    assert t["components"]["week_refs"] == 15 and t["derived"] == 5 and t["status"] == "ok"
    ledger.record(_snap("S", 5, "='0n'!BH292 +'1n+'!ZZ5"), db)        # unobserved ref stays unknown
    s_ = rules.derive_day("2026-10-18", db)["cols"]["S"]
    assert "+'1n+'!ZZ5" in s_["unknown"] and s_["status"] == "partial"


# ── strict (ledger-as-source) check ──────────────────────────────────────────

def _base(col, formula, value):
    return _e(ts="2026-10-18T03:50:00", kind="baseline", col=col, value=None, before=None,
              after=formula, after_value=str(value), before_value=None, src="baseline")


def _reconcile(col, value, formula, sheet="0分", ts="2026-10-18T23:55:00"):
    return _e(ts=ts, kind="reconcile", sheet=sheet, col=col, value=None, before=formula,
              after=formula, after_value=str(value), before_value=None, src="reconcile")


def test_strict_credits_come_from_events_not_the_excel_formula(db, monkeypatch):
    monkeypatch.setattr(rules, "load_weights", lambda conn=None: {})
    ledger.record(_base("Y", "=0", 0), db)
    ledger.record(_e(col="Y", value="+30", before="=0", after="=0+30", after_value="30"), db)
    ledger.record(_reconcile("Y", 210, "=0+30 + 180"), db)          # +180 typed into Excel
    assert rules.derive_day("2026-10-18", db)["cols"]["Y"]["status"] == "ok"   # loose absorbs it
    chk = rules.strict_check("2026-10-18", db)
    (miss,) = [m for m in chk["misses"] if m["col"] == "Y"]
    assert miss["derived"] == 30 and miss["observed"] == 210
    assert any("+180 in Excel formula" in w for w in miss["why"])


def test_strict_set_resets_base_so_daemon_undo_reconciles(db, monkeypatch):
    monkeypatch.setattr(rules, "load_weights", lambda conn=None: {})
    ledger.record(_base("Y", "=0", 0), db)
    ledger.record(_e(ts="2026-10-18T09:00:00", col="Y", value="+2028", before="=0", after="=0+2028",
                     after_value="2028"), db)
    ledger.record(_e(ts="2026-10-18T10:00:00", kind="write", col="Y", value="=0+20", before="=0+2028",
                     after="=0+20", after_value="20", src="undo-fast"), db)
    ledger.record(_snap("Y", 20, "=0+20"), db)
    assert all(m["col"] != "Y" for m in rules.strict_check("2026-10-18", db)["misses"])


def test_strict_ignores_0n_reconcile_and_names_the_cell(db, monkeypatch):
    monkeypatch.setattr(rules, "load_weights", lambda conn=None: {})
    ledger.record(_base("X", "='0n'!AZ292", 0), db)
    ledger.record(_reconcile("AJ", 45, "=20+25", sheet="0n"), db)    # kid minutes typed into 0n
    ledger.record(_snap("X", 45, "='0n'!AZ292"), db)
    assert rules.derive_day("2026-10-18", db)["cols"]["X"]["derived"] == 45
    (miss,) = [m for m in rules.strict_check("2026-10-18", db)["misses"] if m["col"] == "X"]
    assert miss["derived"] == 0 and any(w.startswith("0n!AJ") for w in miss["why"])


def test_strict_evaluates_same_row_sum_cells():
    on = {"N": {"value": 1.0, "text": None, "formula": "1"},
          "AX": {"value": 0.0, "text": None, "formula": "=N300+O300 + 0"}}
    assert rules._eval_row_formulas(on)["AX"]["value"] == 1.0


def test_streak_counts_trailing_passes():
    days = {"2026-10-27": {"pass": True}, "2026-10-28": {"pass": False},
            "2026-10-29": {"pass": True}, "2026-10-30": {"pass": True}}
    assert rules.streak(days) == 2
