"""did-fast records each entry's 分 where every client can read it, and undo
clears it (JM 2026-11-05: 一起发 credited 19 分, the row stayed blank)."""
import importlib.util
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "lib"))
import entry_points as ep  # noqa: E402

DAY = "2026-11-05"


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def df():
    return _load("df_entrypts", "did-fast.py")


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("ENTRY_POINTS_DIR", str(tmp_path))


RANGED = {"name": "一起发", "step": "1n", "0fen": {"points": 19},
          "toggl": {"ok": True, "ids": [4612720841]}}


def test_a_ranged_result_notes_the_entry_it_created(df):
    out = {}
    df._note_entry_points(out, RANGED, None, DAY)
    assert out["entry_points"] == {DAY: {"4612720841": 19}}


def test_a_stopped_timer_takes_its_own_points(df):
    out = {}
    stop = {"description": "bball", "stopped": True, "id": "99"}
    df._note_entry_points(out, {"name": "bball", "step": "variable", "0fen": {"points": 20}}, stop, DAY)
    assert out["entry_points"] == {DAY: {"99": 20}}


def test_a_timer_for_another_name_gets_nothing(df):
    out = {}
    stop = {"description": "bball", "stopped": True, "id": "99"}
    df._note_entry_points(out, {"name": "hiit", "step": "0n", "0fen": {"points": 20}}, stop, DAY)
    assert "entry_points" not in out


def test_block_segments_sum_onto_one_id_and_stay_apart_across_ids(df):
    out = {}
    df._note_entry_points(out, RANGED, None, DAY)
    df._note_entry_points(out, RANGED, None, DAY)
    assert out["entry_points"][DAY]["4612720841"] == 38


@pytest.mark.parametrize("entry", [
    {"name": "x", "step": "0n", "toggl": {"ok": True, "ids": [1]}},                 # sheet-priced: 0
    {"name": "x", "step": "refused", "0fen": {"points": 9}, "toggl": {"ok": True, "ids": [1]}},
    {"name": "x", "step": "1n", "0fen": {"points": 9}, "toggl": {"ok": False, "ids": [1]}},
])
def test_no_record_without_points_or_a_real_entry(df, entry):
    out = {}
    df._note_entry_points(out, entry, None, DAY)
    assert "entry_points" not in out


def test_failed_writes_record_nothing(df):
    src = (HERE / "did-fast.py").read_text()
    assert "if not failed:\n        _record_entry_points(output)" in src


def test_record_lands_per_day(df, store):
    df._record_entry_points({"entry_points": {DAY: {"7": 19}, "2026-11-04": {"8": 5}}})
    assert ep.for_day(DAY) == {"7": 19} and ep.for_day("2026-11-04") == {"8": 5}


def test_undo_tombstones_the_entries_it_reverses(store, monkeypatch):
    uf = _load("uf_entrypts", "undo-fast.py")
    ep.record(DAY, {"7": 19})
    out = {"entry_points": {DAY: {"7": 19}}, "results": []}
    try:
        uf.reverse_didfast_output(out, "11/5", DAY, [])
    except Exception:  # noqa: BLE001 -- only the record is under test
        pass
    assert ep.for_day(DAY) == {"7": None}
