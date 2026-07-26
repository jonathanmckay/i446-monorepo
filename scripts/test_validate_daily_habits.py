"""Regression: validate-daily-habits detects daily habits missing from Todoist
and builds correct recreate payloads.

Bug context: recurring daily Todoist tasks sometimes fail to regenerate for the
new day and silently vanish, with no way to notice short of checking Excel. This
validator compares the canonical manifest against live Todoist; these tests pin
the matching (no false positives/negatives) and the recreate spec.
"""

import importlib.util
import json
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "vdh", Path(__file__).resolve().parent / "validate-daily-habits.py")
vdh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vdh)

MANIFEST = {
    "habits": {
        "0g": {"match": "0g", "content": "0g (4) [8]", "due_string": "every day",
               "labels": ["0neon", "g245"], "priority": 4, "project_id": "P1"},
        "ibx-s897": {"match": "ibx s897", "content": "ibx s897 [6] (15)",
                     "due_string": "every day", "labels": ["0neon", "s897"],
                     "priority": 2, "project_id": "P2"},
        "早餐": {"match": "早餐", "content": "早餐 (15) [5]", "due_string": "every day",
                "labels": ["0neon", "hcb"], "priority": 3, "project_id": "P3"},
    }
}


def test_all_present_no_missing():
    present = ["0g (4) [8]", "ibx s897 [6] (15)", "早餐 (15) [5]", "unrelated task"]
    assert vdh.compute_missing(MANIFEST, present) == []


def test_detects_missing_habit():
    # 0g failed to regenerate — absent from the live set
    present = ["ibx s897 [6] (15)", "早餐 (15) [5]"]
    assert vdh.compute_missing(MANIFEST, present) == ["0g"]


def test_matches_despite_estimate_token_differences():
    # Live content has different (N)/[N] ordering than the manifest's stored
    # content — must still match on the bare name, not report missing.
    present = ["0g [8] (4)", "ibx s897", "早餐 [5] (15)"]
    assert vdh.compute_missing(MANIFEST, present) == []


def test_short_name_not_falsely_matched_as_substring():
    # '0g' (len 2) must NOT match '0gym' via substring; only exact bare match
    # counts for short names, so a stray task can't mask a missing habit.
    present = ["0gym workout (30)", "ibx s897 [6]", "早餐 (15)"]
    assert "0g" in vdh.compute_missing(MANIFEST, present)


def test_recreate_payload_carries_full_spec_with_clean_name():
    # No 😈 in the content (2026-07-26: "😈 1st hci" missed the 0n header on
    # completion, mis-routing the habit's write). Provenance lives in the
    # description instead.
    body = vdh.recreate_payload(MANIFEST["habits"]["0g"])
    assert body["content"] == "0g (4) [8]"
    assert "😈" not in body["content"]
    assert body["description"].startswith("auto-recreated by validate-daily-habits")
    assert body["due_string"] == "every day"        # preserves recurrence
    assert body["labels"] == ["0neon", "g245"]
    assert body["priority"] == 4
    assert body["project_id"] == "P1"


def test_bare_strips_auto_marker_and_estimates():
    assert vdh.bare("ibx s897 [6] (15)") == "ibx s897"
    assert vdh.bare("charge [3] (15)") == "charge"
    assert vdh.bare("0g (4) [8]") == "0g"
    assert vdh.bare("😈 0g (4) [8]") == "0g"          # auto-marker stripped


def test_auto_recreated_habit_not_reflagged_as_missing():
    # A habit we recreated last run carries 😈; this run must see it as present,
    # not missing — otherwise it gets recreated again every day (duplicates).
    present = ["😈 0g (4) [8]", "ibx s897 [6] (15)", "早餐 (15) [5]"]
    assert vdh.compute_missing(MANIFEST, present) == []


def test_2n_wires_daily_habits_card():
    """The /inbound (-2n) flow must run the once-daily check and surface a card
    only when something was recreated/errored. Guards against the wiring being
    dropped in a future -2n refactor."""
    src = (Path.home() / "i446-monorepo/tools/ibx/-2n.py").read_text()
    assert "daily_habits_due_today" in src, "once-a-day guard missing from -2n"
    assert "run_daily_habits_check" in src, "habits check call missing from -2n"
    assert 'cards_needed.append("habits")' in src, "habits card not added to queue"
    assert "validate-daily-habits.py" in src, "-2n must invoke the validator"


def test_live_manifest_is_valid_and_nonempty():
    """The shipped manifest must parse and have habits with the fields the
    validator needs (guards against a corrupt/empty manifest silently disabling
    the check)."""
    mpath = Path(__file__).resolve().parent.parent / "config" / "daily-todoist-manifest.json"
    m = json.loads(mpath.read_text())
    assert m["habits"], "manifest has no habits"
    for key, h in m["habits"].items():
        assert h.get("match"), f"{key} missing 'match'"
        assert h.get("content"), f"{key} missing 'content'"
        assert h.get("due_string"), f"{key} missing 'due_string' (recurrence)"


def test_na_today_reads_dtd_deleted_names(tmp_path, monkeypatch):
    """A habit deleted from dtd (= N/A for today) lands in the day's NA file;
    na_today() returns its bare name so --fix won't resurrect the card the
    same day. Missing file → empty set (the common case)."""
    import datetime
    monkeypatch.setenv("HOME", str(tmp_path))
    assert vdh.na_today() == set()
    na_dir = tmp_path / ".cache/jm"
    na_dir.mkdir(parents=True)
    today = datetime.date.today().isoformat()
    (na_dir / f"habits-na-{today}.json").write_text(
        json.dumps(["cpap", "ibx s897"]))
    assert vdh.na_today() == {"cpap", "ibx s897"}
