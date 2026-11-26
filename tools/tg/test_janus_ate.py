"""/ate typed into janus's input box (JM 2026-11-26)."""
import importlib.util
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import janus_ate as ja  # noqa: E402


def _r(stdout="", code=0, stderr=""):
    return types.SimpleNamespace(stdout=stdout, stderr=stderr, returncode=code)


def test_parse_follows_the_skill_grammar():
    assert ja.parse("/ate raspberries, 80, 1 (berries 3)") == {
        "name": "raspberries", "kcal": "80", "protein": "1", "groups": "berries 3", "branch": None}
    assert ja.parse("/ate 2 eggs and toast") == {
        "name": "2 eggs and toast", "kcal": None, "protein": None, "groups": None, "branch": None}
    assert ja.parse("/ate 卯 leftover oatmeal, 300, 1")["branch"] == "卯"
    s = ja.parse("/ate tofu scramble, help, 22 (help)")
    assert (s["kcal"], s["protein"], s["groups"]) == (None, "22", None)
    s = ja.parse("/ate 面条+回锅肉, tea, 2 包子, 950, 39 ()")          # commas in the name, and no groups
    assert (s["name"], s["kcal"], s["protein"], s["groups"]) == ("面条+回锅肉, tea, 2 包子", "950", "39", "")
    assert ja.parse("/ate salad, 60+40")["kcal"] == "60+40"
    assert ja.parse("/早餐 skyr, 150, 20")["branch"] == "辰"
    for other in ("ate lunch", "/ate", "/ate   ", "lunch 1200-1230", "/d ate", "/ate , 300"):
        assert ja.parse(other) is None


def test_a_full_line_writes_once_without_asking_a_model():
    calls = []
    def run(cmd, **kw):
        calls.append(cmd)
        return _r("ate mocha (390 kcal, 12g protein) → hcbi 卯 band\n")
    out = ja.run_ate(ja.parse("/ate 卯 mocha, 390, 12 (water 1)"), date="11/25", run=run)
    assert out.startswith("ate mocha") and "est." not in out
    assert len(calls) == 1 and calls[0][1:] == [ja.ATE, "--name", "mocha", "--kcal", "390", "--protein", "12",
                                               "--groups", "water 1", "--branch", "卯", "--date", "11/25"]


def test_missing_fields_are_estimated_and_typed_ones_kept():
    calls = []
    def run(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == "claude":
            return _r('Here:\n{"kcal": 512.4, "protein": 30, "groups": {"greens": 1, "candy": 2, "beans": 0}}\n')
        return _r("ate salad → hcbi\n")
    out = ja.run_ate(ja.parse("/ate chicken caesar salad, 450"), run=run)
    assert out == "ate salad → hcbi · est. protein/groups"
    assert "Known kcal: 450" in calls[0][2] and "chicken caesar salad" in calls[0][2]
    assert calls[1][3:] == ["chicken caesar salad", "--kcal", "450", "--protein", "30", "--groups", "greens 1"]


def test_a_failed_estimate_writes_nothing():
    calls = []
    def run(cmd, **kw):
        calls.append(cmd)
        return _r("I cannot do that", code=0)
    assert ja.run_ate(ja.parse("/ate mystery stew"), run=run).startswith("err: ate: estimate failed: nothing written")
    assert len(calls) == 1
    def gone(cmd, **kw):
        raise FileNotFoundError("claude")
    assert "no `claude` on this Mac" in ja.run_ate(ja.parse("/ate mystery stew"), run=gone)
    def bad_write(cmd, **kw):
        return _r(code=1, stderr="ERROR: batch write failed: daemon down\n")
    assert ja.run_ate(ja.parse("/ate x, 1, 1 ()"), run=bad_write) == "err: ERROR: batch write failed: daemon down"


def test_janus_routes_ate_before_the_comma_split():
    src = (HERE / "janus.py").read_text()
    enter = src[src.index("    ate = janus_ate.parse(text)") - 100:]
    assert enter.index("janus_ate.parse(text)") < enter.index("_fix_project_codes(text)") < enter.index('text.split(",")')
