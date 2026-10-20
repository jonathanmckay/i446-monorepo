"""New /tg mode (2026-08-18 user request): a bare trailing number under 100,
with no unit ("/tg ibx 15"), means "I've been doing this for the last N
minutes" -- a completed entry from (now - N) to now.

The skill's own doc already claimed "2h or 90m -> duration (create entry
ending now)" was supported, but tg-fast.py never actually implemented any
duration parsing at all -- a pre-existing doc/code gap discovered while
building this. This wires the bare-number form the user asked for through
the same cmd_create_range/trim_range path every other range-based entry
uses, so it correctly trims/stops whatever's currently running.
"""
import importlib.util
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

HERE = Path(__file__).parent
SRC = HERE / "tg-fast.py"
TZ = ZoneInfo("America/Los_Angeles")


def _load():
    spec = importlib.util.spec_from_file_location("tg_fast_bare_min", SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tg_fast_bare_min"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def mod():
    return _load()


class _FakeToggl:
    def __init__(self):
        self.calls = []

    def trim_range(self, start_dt, end_dt, exclude_ids=None):
        self.calls.append((start_dt, end_dt))
        return []


def _install_fakes(mod, monkeypatch):
    fake = _FakeToggl()
    monkeypatch.setattr(mod, "_toggl_api", lambda: fake)
    created = []

    def fake_run_cli(*args):
        created.append(args)
        return f"Created: x [id:{len(created)}]"
    monkeypatch.setattr(mod, "_run_cli", fake_run_cli)
    return fake, created


def test_bare_number_creates_range_from_n_minutes_ago_to_now(mod, monkeypatch):
    fake, created = _install_fakes(mod, monkeypatch)

    before = datetime.now(TZ)
    out = mod._process_entry("ibx 15")
    after = datetime.now(TZ)

    assert "Created" in out
    assert len(fake.calls) == 1
    start_dt, end_dt = fake.calls[0]
    # end_dt is "now" truncated to the minute (cmd_create_range only accepts
    # HH:MM, same granularity as every other range-creation path in this
    # file) -- allow up to a minute of slack either side for that truncation.
    assert before - timedelta(minutes=1) <= end_dt <= after + timedelta(minutes=1)
    assert (end_dt - start_dt) == timedelta(minutes=15)

    assert created, "must actually call the CLI to create the entry"
    cli_args = created[0]
    assert cli_args[0] == "create"
    assert cli_args[1] == "ibx"


def test_bare_number_respects_at_project_override(mod, monkeypatch):
    fake, created = _install_fakes(mod, monkeypatch)
    mod._process_entry("random thing 20 @hcmc")
    assert created
    assert "hcmc" in created[0], f"@ override must still apply: {created[0]!r}"


def test_ninety_nine_is_the_max_bare_duration(mod, monkeypatch):
    fake, created = _install_fakes(mod, monkeypatch)
    mod._process_entry("ibx 99")
    assert len(fake.calls) == 1
    start_dt, end_dt = fake.calls[0]
    assert (end_dt - start_dt) == timedelta(minutes=99)


def test_three_digit_trailing_number_is_not_treated_as_bare_duration(mod, monkeypatch):
    """100+ (3 digits) must NOT be swept into this path -- it isn't "under
    100" per the user's own spec, and 4-digit numbers are the pre-existing
    HHMM backdate syntax, so anything in between must fall through untouched."""
    fake, created = _install_fakes(mod, monkeypatch)
    mod._process_entry("ibx 100")
    assert not fake.calls, "a 3-digit trailing number must not trigger the bare-duration path"


def test_four_digit_trailing_number_still_backdates_not_bare_duration(mod, monkeypatch):
    """Regression guard: '1823 o314' (4-digit HHMM backdate) must keep
    working exactly as before -- the new 1-2 digit bare-duration regex must
    never intercept it."""
    fake, created = _install_fakes(mod, monkeypatch)
    stopped = []
    monkeypatch.setattr(mod, "cmd_backdated",
                        lambda backtime, desc, project, tags: stopped.append(
                            (backtime, desc, project, tags)) or "backdated ok")
    out = mod._process_entry("1823 o314")
    assert out == "backdated ok"
    assert stopped == [("1823", "o314", "hcm", [])]
    assert not fake.calls, "the 4-digit backdate path must not also trigger a range create"


def test_bare_number_with_no_description_falls_through(mod, monkeypatch):
    """A lone number with nothing else ('/tg 15') is ambiguous -- there's no
    activity to log -- so it must fall through to the default start-timer
    path instead of silently creating a nameless entry."""
    fake, created = _install_fakes(mod, monkeypatch)
    started = []
    monkeypatch.setattr(mod, "cmd_start",
                        lambda desc, project, tags: started.append((desc, project, tags)) or "started ok")
    out = mod._process_entry("15")
    assert out == "started ok"
    assert not fake.calls
