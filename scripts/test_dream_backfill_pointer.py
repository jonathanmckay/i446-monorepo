"""Regression (2026-10-27): dream-launch.sh advanced the backfill pointer with
`date -j -f FMT "$D" -v-1d +FMT`; BSD date ignored -v after the operand and
wrote "Mon Oct 26 03:00:00 PDT 2026", so every run reset to yesterday (v83-v170).
The pointer must step back one day per run (or BACKFILL_DAYS_PER_RUN) and only
ever hold YYYY-MM-DD."""
import os
import re
import subprocess
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE / "dream-backfill-pointer.sh"


def _run(state, stop="2026-03-01", **env):
    e = {**os.environ, **env}
    r = subprocess.run(["bash", str(SCRIPT), str(state), stop], env=e,
                       capture_output=True, text=True, check=True)
    return r.stdout.strip(), state.read_text().strip()


def test_advances_one_day(tmp_path):
    s = tmp_path / "p"; s.write_text("2026-10-26\n")
    out, ptr = _run(s)
    assert out == "2026-10-26 2026-10-26"
    assert ptr == "2026-10-25"
    out, ptr = _run(s)                       # second night keeps walking back
    assert out == "2026-10-25 2026-10-25" and ptr == "2026-10-24"


def test_crosses_month_boundary(tmp_path):
    s = tmp_path / "p"; s.write_text("2026-10-01")
    _run(s)
    assert s.read_text().strip() == "2026-09-30"


def test_days_per_run(tmp_path):
    s = tmp_path / "p"; s.write_text("2026-10-25")
    out, ptr = _run(s, BACKFILL_DAYS_PER_RUN="3")
    assert out == "2026-10-25 2026-10-23" and ptr == "2026-10-22"


def test_days_per_run_clamped_at_stop(tmp_path):
    s = tmp_path / "p"; s.write_text("2026-03-02")
    out, ptr = _run(s, BACKFILL_DAYS_PER_RUN="5")
    assert out == "2026-03-02 2026-03-01" and ptr == "2026-02-28"
    out, ptr = _run(s)                       # past the stop: complete, no output
    assert out == "" and ptr == "2026-02-28"


def test_bad_days_per_run_falls_back_to_one(tmp_path):
    s = tmp_path / "p"; s.write_text("2026-10-25")
    out, ptr = _run(s, BACKFILL_DAYS_PER_RUN="x")
    assert ptr == "2026-10-24"


def test_corrupted_pointer_resets_to_yesterday(tmp_path):
    s = tmp_path / "p"; s.write_text("Mon Oct 26 03:00:00 PDT 2026\n")
    out, ptr = _run(s)
    y = (date.today() - timedelta(days=1)).isoformat()
    assert out == f"{y} {y}"
    assert ptr == (date.today() - timedelta(days=2)).isoformat()


def test_missing_pointer_starts_yesterday(tmp_path):
    s = tmp_path / "sub" / "p"
    out, ptr = _run(s)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", ptr)
    assert out.split()[0] == (date.today() - timedelta(days=1)).isoformat()


def test_launcher_no_longer_has_bsd_arg_order_bug():
    src = (HERE / "dream-launch.sh").read_text()
    assert not re.search(r"date -j -f [^\n]*-v-", src)
    assert "dream-backfill-pointer.sh" in src


def test_v180_seed_walks_back_and_floor_is_inclusive(tmp_path):
    # v180 (2026-11-06): pointer seeded to 2026-09-29 after the launcher wrote
    # "Thu Nov  5 03:00:01 PST 2026" again. 03-01 itself is backfilled; 02-28 is not.
    s = tmp_path / "p"; s.write_text("2026-09-29\n")
    out, ptr = _run(s)
    assert out == "2026-09-29 2026-09-29" and ptr == "2026-09-28"
    s.write_text("2026-03-01")
    out, ptr = _run(s)
    assert out == "2026-03-01 2026-03-01" and ptr == "2026-02-28"
    out, ptr = _run(s)
    assert out == "" and ptr == "2026-02-28"


def test_v180_date_string_pointer_is_never_rewritten_as_date_string(tmp_path):
    s = tmp_path / "p"; s.write_text("Thu Nov  5 03:00:01 PST 2026\n")
    _run(s)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", s.read_text().strip())
