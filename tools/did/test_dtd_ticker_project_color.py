#!/usr/bin/env python3
"""Feature (2026-07-24): dtd's footer timer line renders in the running
entry's PROJECT color, matching the palette used for list rows and janus.

Wiring: dtd.sh's start script appends the resolved project code as the timer
file's 4th field (`desc\tstart\tid\tproject`), so a dtd-started timer colors
instantly; externally-started timers are colored on the Toggl poll via
PROJECT_MAP (id → code). fzf footers process ANSI even without --ansi.
"""
import ast
import importlib.util
import re
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DTD = (HERE / "dtd.sh").read_text()
TICKER = HERE / "dtd-ticker.py"


def _load_ticker():
    spec = importlib.util.spec_from_file_location("dtd_ticker", TICKER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Wiring ────────────────────────────────────────────────────────────────────

def test_start_script_writes_project_as_fourth_field():
    assert (r"""printf '%s\t%s\t%s\t%s\n' "\$clean" "\$(date +%s)" "\$1" """
            r'''"\$project" > "\$TIMER"''') in DTD, (
        "start script must record the resolved project code in the timer file")


def test_read_timer_file_returns_fourth_field(tmp_path):
    mod = _load_ticker()
    f = tmp_path / "dtd.timer"
    f.write_text(f"AoS\t{time.time()}\tTASKID\txk88\n")
    start, desc, mtime, proj = mod._read_timer_file(f)
    assert proj == "xk88"


# ── Coloring ─────────────────────────────────────────────────────────────────

def test_project_ansi_truecolor_and_unknown():
    mod = _load_ticker()
    assert mod.project_ansi("i9") == "\033[38;2;41;121;255m"   # #2979ff
    assert mod.project_ansi("") == ""
    assert mod.project_ansi("nope") == ""


def test_footer_body_is_wrapped_in_project_color():
    src = TICKER.read_text()
    m = re.search(r"if start is not None:\n(.*?)else:", src, re.S)
    body = m.group(1)
    assert "project_ansi(proj)" in body, "running footer must resolve the color"
    assert r"\033[0m" in body, "color must be reset after the line"


def test_poll_path_resolves_code_from_project_id():
    src = TICKER.read_text()
    assert "ID_TO_CODE.get(cur.get(" in src, (
        "externally-started timers must color via PROJECT_MAP id → code")
    assert 'start, desc, proj = None, "", ""' in src, (
        "idle reconcile must clear the color with the timer")


# ── Palette sync: janus.py is the color SOURCE ───────────────────────────────

def _extract_colors(path: Path) -> dict:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and node.targets
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "PROJECT_COLORS"):
            return ast.literal_eval(node.value)
    raise AssertionError(f"PROJECT_COLORS not found in {path}")


def test_ticker_palette_matches_janus_source():
    janus = _extract_colors(HERE.parent / "tg" / "janus.py")
    ticker = _extract_colors(TICKER)
    assert ticker == janus, (
        "dtd-ticker.py's PROJECT_COLORS copy drifted from janus.py (the "
        "declared color SOURCE) — sync them")


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
