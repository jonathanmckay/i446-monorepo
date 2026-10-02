"""xk20 / xk22 as /did domains (user request 2026-10-02: "make did take xk20
and xk22 as domains (0n AJ and AK respectively)").

A Todoist card labelled xk20/xk22, or an item with "@xk20"/"@xk22", logs its
MINUTES into the kid's own 0n column (resolved by header, AJ/AK today),
cumulatively — the same write "/did xk20 20" makes. 0n!AZ sums those columns
and 0分!X is ='0n'!AZ, so the minutes already become xk points at a point
per minute: the card's [N] must NOT also be appended to 0分 (double count).
The synthesized write never marks the habit "xk20" done and closes nothing.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent
SRC = (HERE / "did-fast.py").read_text()
TREE = ast.parse(SRC)
HEADERS = {"0n": {"xk20": 36, "xk22": 37, "问学": 35}}


def _load():
    spec = importlib.util.spec_from_file_location("did_fast_kid_domain", HERE / "did-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["did_fast_kid_domain"] = mod
    spec.loader.exec_module(mod)
    return mod


def _main_body() -> str:
    fn = next(n for n in ast.walk(TREE) if isinstance(n, ast.FunctionDef) and n.name == "main")
    return ast.get_source_segment(SRC, fn)


def _card(m, name, content, labels, fen_points=0, **item_kw):
    item = m.ParsedItem(raw=name, name=name, target_date="10/2", **item_kw)
    return m.RouteResult(item=item, step="todoist",
                         todoist_task={"id": "1", "content": content, "labels": labels},
                         fen_col=m._project_fen_col(labels), fen_points=fen_points)


def test_xk20_xk22_labels_are_0n_routes_not_0fen_columns():
    m = _load()
    assert m.LABEL_TO_0FEN["xk20"] == "0n:xk20" and m.LABEL_TO_0FEN["xk22"] == "0n:xk22"
    assert m._0n_domain_header("0n:xk20") == "xk20"
    assert m._0n_domain_header("X") is None and m._0n_domain_header(None) is None
    assert m._project_fen_col(["0neon", "xk20"]) == "0n:xk20"
    assert m._project_fen_col(["xk22"]) == "0n:xk22"
    # the kid columns accumulate across same-day sessions
    assert {"xk20", "xk22"} <= m.CUMULATIVE_0N


def test_card_estimate_becomes_minutes_in_the_kids_0n_column():
    m = _load()
    r = _card(m, "story", "(20) [10] story @xk20", ["0neon", "xk20"], fen_points=10)
    synth = m.synth_kid_time_writes([r], None, HEADERS)
    assert len(synth) == 1
    k = synth[0]
    assert k.step == "0n" and k.col_num == 36 and k.write_value == 20
    assert k.item.name == "xk20" and k.kid_time_for == "story"
    assert k.todoist_task is None and k.fen_points == 0


def test_stopped_timer_for_the_task_beats_the_estimate():
    m = _load()
    r = _card(m, "story", "(20) [10] story", ["xk22"], fen_points=10)
    synth = m.synth_kid_time_writes([r], {"description": "Story", "minutes": 33}, HEADERS)
    assert synth[0].col_num == 37 and synth[0].write_value == 33
    # a timer for some OTHER task is ignored
    synth = m.synth_kid_time_writes([r], {"description": "walk", "minutes": 33}, HEADERS)
    assert synth[0].write_value == 20


def test_time_range_beats_everything_and_points_are_the_last_fallback():
    m = _load()
    r = _card(m, "story", "(20) [10] story", ["xk20"], fen_points=10, time_range=("1800", "1845"))
    assert m.synth_kid_time_writes([r], {"description": "story", "minutes": 33}, HEADERS)[0].write_value == 45
    r = _card(m, "story", "[10] story", ["xk20"], fen_points=10)
    assert m.synth_kid_time_writes([r], None, HEADERS)[0].write_value == 10
    r = _card(m, "story", "story", ["xk20"])
    assert m.synth_kid_time_writes([r], None, HEADERS)[0].write_value == 1


def test_adhoc_at_xk20_item_also_writes_minutes():
    m = _load()
    item = m.ParsedItem(raw="story @xk20", name="story", target_date="10/2",
                        project_override="xk20", time_range=("0900", "0930"))
    r = m.RouteResult(item=item, step="variable", fen_col=m.LABEL_TO_0FEN["xk20"], fen_points=30)
    synth = m.synth_kid_time_writes([r], None, HEADERS)
    assert synth[0].col_num == 36 and synth[0].write_value == 30


def test_non_kid_results_and_unknown_header_produce_no_write(capsys):
    m = _load()
    r = _card(m, "chores", "(20) [10] chores", ["xk88"], fen_points=10)
    assert m.synth_kid_time_writes([r], None, HEADERS) == []
    r = _card(m, "story", "(20) story", ["xk20"])
    assert m.synth_kid_time_writes([r], None, {"0n": {"问学": 35}}) == []
    assert "no 0n column" in capsys.readouterr().err


def test_main_gates_the_0fen_append_off_and_joins_the_0n_batch():
    body = _main_body()
    assert "fast.extend(synth_kid_time_writes(fast, toggl_stop, headers))" in body
    assert body.index("apply_timer_minutes(fast, toggl_stop)") < body.index("synth_kid_time_writes(") \
        < body.index("# 4. Batch 0₦ writes"), "kid minutes must join the step-4 0n batch after timer backfill"
    start = body.index("fen_appends = []")
    fen_loop = body[start:body.index("fen_result = None", start)]
    assert "is_0n_domain" in fen_loop and "not is_0n_domain" in fen_loop, \
        "the 0分 points append must be gated off for 0n-routed domains (double count via 0n!AZ)"
    assert 'if not r.kid_time_for]' in body, "synthesized writes must not mark xk20 itself complete"
    rec = body[body.index('entry["kid_time_for"] = r.kid_time_for'):]
    assert "not _0n_domain_header(r.fen_col)" in rec[:600], 'no "0fen" entry for a 0n-routed domain'


def test_synthesized_write_is_cumulative_in_the_0n_script():
    m = _load()
    r = _card(m, "story", "(20) story", ["xk20"])
    k = m.synth_kid_time_writes([r], None, HEADERS)[0]
    script = m.build_0n_script([k], "10/2")
    assert script and "36" in script
    # the read-old-add-new branch every CUMULATIVE_0N habit gets, not a plain overwrite
    assert "+" in script and "20" in script


def test_dtd_and_domain_fast_accept_the_new_domains():
    dtd = (HERE / "dtd.sh").read_text()
    assert "'xk20':" in dtd and "'xk22':" in dtd, "dtd COLORS must know xk20/xk22"
    assert "xk87|xk88|xk20|xk22|" in dtd, "dtd domainsearch case list must accept xk20/xk22"
    assert "'xk20','xk22'" in dtd, "dtd label_arg list must pass @xk20/@xk22 through"
    spec = importlib.util.spec_from_file_location("domain_fast_kid", HERE / "domain-fast.py")
    df = importlib.util.module_from_spec(spec); sys.modules["domain_fast_kid"] = df
    spec.loader.exec_module(df)
    assert {"xk20", "xk22"} <= df.DOMAINS
