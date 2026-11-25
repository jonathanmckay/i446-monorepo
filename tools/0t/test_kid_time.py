"""kid_time.plan / attribute: the day-close fill of 0n xk20/xk22/xk26/xk88
(JM 2026-11-24). Regression for 2026-11-18: a backfilled 41m "中文 #xk20"
never reached xk20, which read 1."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "lib"))
_SPEC = importlib.util.spec_from_file_location("kid_time", Path(__file__).parent / "kid_time.py")
kt = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(kt)

CODES = {1: "xk87", 2: "hcbp", 3: "xk88"}
SESS = {"math", "english", "中文"}
ALIAS = {"熙熙": "xk26"}.get


def e(i, mins, pid=1, tags=(), desc="中文"):
    return {"id": i, "duration": mins * 60, "project_id": pid, "tags": list(tags), "description": desc}


def plan(entries, logged=(), current=None):
    return kt.plan(entries, set(map(str, logged)), current or {}, CODES.get, ALIAS, SESS)


def test_backfilled_session_fills_the_gap_over_a_card_fallback():
    p = plan([e(1, 41, tags=["xk20"]), e(2, 30, tags=["xk22"])], current={"xk20": 1, "xk22": 25})
    assert p["credits"] == {"xk20": 40, "xk22": 5}
    assert p["counted"] == {"1": 41, "2": 30}


def test_rerun_adds_nothing():
    p = plan([e(1, 41, tags=["xk20"])], logged=[1], current={"xk20": 41})
    assert p["credits"] == {} and p["counted"] == {}


def test_entry_a_client_logged_is_never_paid_again_even_if_cell_is_low():
    # janus logged entry 1 (minutes went wherever it sent them); only entry 2 is new
    p = plan([e(1, 41, tags=["xk20"]), e(2, 20, tags=["xk20"])], logged=[1], current={"xk20": 0})
    assert p["credits"] == {"xk20": 20}


def test_never_lowers_or_exceeds_the_toggl_total():
    # live credit with a bonus (35 for a 30m session) plus a new 41m entry: tops up to 71
    p = plan([e(1, 30, tags=["xk20"]), e(2, 41, tags=["xk20"])], logged=[1], current={"xk20": 35})
    assert p["credits"] == {"xk20": 36}
    p = plan([e(1, 30, tags=["xk20"])], current={"xk20": 50})
    assert p["credits"] == {} and p["counted"] == {"1": 30}   # still marked, so janus refuses it


def test_independent_time_on_another_project_is_half():
    p = plan([e(1, 20, pid=2, tags=["xk26"], desc="hiit")])
    assert p["credits"] == {"xk26": 10}


def test_xk88_project_and_nickname_and_running_timer():
    p = plan([e(1, 48, pid=3, desc="lx call"), e(2, 60, desc="熙熙"),
              {"id": 3, "duration": -1, "project_id": 3, "tags": [], "description": "x"}])
    assert p["credits"] == {"xk88": 48, "xk26": 60}


def test_both_kids_tagged_each_get_the_minutes():
    p = plan([e(1, 30, tags=["xk20", "xk22"])])
    assert p["credits"] == {"xk20": 30, "xk22": 30} and p["counted"] == {"1": 60}


def test_untagged_teaching_is_reported_not_credited_unless_already_logged():
    p = plan([e(1, 93), e(2, 102, desc="math"), e(3, 30, desc="family time"), e(4, 15, desc="中文"),
              {"id": 5, "duration": 20, "project_id": 1, "tags": [], "description": "中文"}],   # a 20s stub
             logged=[4])
    assert p["credits"] == {}
    assert [u["id"] for u in p["untagged"]] == [1, 2]


def test_secondary_kid_tag_rate_shared_with_the_live_clients():
    import janus_done
    assert janus_done.secondary_minutes("xk26", 20, "hcbp") == 10
    assert janus_done.secondary_minutes("xk22", 30, "xk20") == 30   # second kid in a joint session
    assert janus_done.secondary_minutes("其他人", 61, "hcb") == 61
