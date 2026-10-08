"""agent_triage: verdict parsing must fail closed (keep), and only ibx i9 /
ibx m5x2 timer starts may spawn a triage."""
import importlib.util
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import agent_triage as at

_spec = importlib.util.spec_from_file_location(
    "toggl_cli", Path.home() / "i446-monorepo/mcp/toggl_server/toggl_cli.py")
toggl_cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(toggl_cli)


def test_parse_skips_only():
    out = 'noise\n[{"i":1,"v":"keep","why":"ask"},{"i":2,"v":"skip","why":"digest"}]\n'
    assert at.parse_verdicts(out, 2) == {2: "digest"}


def test_parse_garbage_returns_none():
    assert at.parse_verdicts("", 3) is None
    assert at.parse_verdicts("I can't do that", 3) is None
    assert at.parse_verdicts('[{"i":1,"v":"skip"', 3) is None


def test_parse_ignores_out_of_range_and_bad_rows():
    out = '[{"i":0,"v":"skip"},{"i":9,"v":"skip"},{"i":"x","v":"skip"},{"i":2,"v":"SKIP"}]'
    assert at.parse_verdicts(out, 3) == {2: ""}


def test_parse_last_array_wins():
    out = 'format: [{"i": 1, "v": "skip"}]\nanswer: [{"i":1,"v":"keep"}]'
    assert at.parse_verdicts(out, 1) == {}


def test_trigger_matches_ibx_timers_only():
    with mock.patch("subprocess.Popen") as popen:
        for desc in ("ibx i9", "ibx - i9", "IBX M5X2", "ibx  -  m5x2"):
            toggl_cli._maybe_agent_triage(desc)
        assert [c.args[0][2] for c in popen.call_args_list] == ["i9", "i9", "m5x2", "m5x2"]
        popen.reset_mock()
        for desc in ("ibx s897", "ibx i9 review", "teams", "ibx"):
            toggl_cli._maybe_agent_triage(desc)
        popen.assert_not_called()
