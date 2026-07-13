#!/usr/bin/env python3
"""Regression: starting a -1neon ritual timer maps to the SAME project as the
row color.

Bug (2026-07-13): -1n cards rendered the right colors (from RITUAL_DOMAIN) but
starting their timer used tg-fast on the raw `😈 -1g` content — which can't
resolve a 😈-prefixed name, and whose shortcodes disagree with the ritual map
(tg-fast: -1ibx→m5x2; ritual/color: -1ibx→i9; سمش unknown to tg-fast). So timers
landed on the wrong/no project.

Fix: DTD_START strips 😈 and resolves the project from the SAME RITUAL_DOMAIN map
as the color, falling back to tg-fast for non-ritual tasks.
"""
import ast
import re
from pathlib import Path

DTD = (Path(__file__).resolve().parent / "dtd.sh").read_text()


def test_color_and_timer_ritual_maps_agree():
    maps = [ast.literal_eval(m) for m in re.findall(r"RITUAL_DOMAIN = (\{[^}]*\})", DTD)]
    assert len(maps) == 2, f"expected a color map and a timer map, found {len(maps)}"
    assert maps[0] == maps[1], f"color vs timer ritual→domain mismatch: {maps}"
    # sanity: the user-chosen mappings the report is about
    assert maps[0]["-1ibx"] == "i9" and maps[0]["سمش"] == "hcm"


def _start_script() -> str:
    i = DTD.index('cat > "$DTD_START" << STARTEOF')
    j = DTD.index("\nSTARTEOF", i)
    return DTD[i:j]


def test_timer_resolves_ritual_project_from_map():
    s = _start_script()
    assert "RITUAL_DOMAIN" in s, "DTD_START must resolve ritual projects from the map"
    assert "replace('😈','')" in s, "DTD_START must strip the 😈 marker"


def test_timer_falls_back_to_tg_fast_for_non_rituals():
    s = _start_script()
    # non-ritual (empty project) → tg-fast, guarded so it doesn't override a
    # ritual project.
    assert '[ -z "\\$project" ] && project=\\$(python3 "\\$TG_FAST" --resolve' in s


def test_start_payload_has_no_shell_meta():
    # Same class as the 2026-07-13 backtick bug: the embedded python -c must not
    # carry backticks / $(...) that zsh would run.
    s = _start_script()
    # extract the python -c "..." body
    k = s.index('python3 -c "')
    body = s[k + len('python3 -c "'):s.index('"', s.index('print(proj)', k))]
    assert "`" not in body and "$(" not in body


if __name__ == "__main__":
    import sys, pytest
    sys.exit(pytest.main([__file__, "-v"]))
