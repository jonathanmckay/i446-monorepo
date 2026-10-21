#!/usr/bin/env python3
"""dtd ctrl-p (split) lives on the ctrl-o/ctrl-g edit screen, which since
2026-10-21 ("I don't like the new ctrl+o / ctrl+p page") is a curses form
(edit-form.py): points/time, haiku name, full name, notes, project, then the
"done so far" block (pts done · min done · done as). ctrl-p from the list
opens it focused on "pts done"; edit-fast applies the ordinary edits from the
form's JSON, then prints one `SPLIT pts=… done_name=… done_min=…` line that
the edit script evals and hands to the split script as arguments. The one-
line `✓ <pts>` prompt and split-form.py (curses, 2026-10-04) are gone.

Structural checks on dtd.sh's generated scripts; the form itself has its own
pty tests in test_edit_form.py.
"""
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
DTD = (HERE / "dtd.sh").read_text()


def _edit() -> str:
    m = re.search(r'cat > "\$DTD_EDIT" << EDITEOF\n(.*?)\nEDITEOF\n', DTD, re.S)
    assert m
    return m.group(1)


def _split() -> str:
    m = re.search(r"cat > \"\$DTD_SPLIT\" << 'SPLITEOF'\n(.*?)\nSPLITEOF", DTD, re.S)
    assert m
    return m.group(1)


def test_ctrl_p_opens_the_edit_screen_focused_on_the_breakoff():
    bind = next(l for l in DTD.splitlines() if '--bind "ctrl-p:' in l)
    assert "DTD_SPLIT_SEED=1 $DTD_EDIT {2}" in bind, bind
    assert "$DTD_SPLIT" not in bind
    assert not (HERE / "split-form.py").exists()
    assert "DTD_SPLIT_PROMPT" not in DTD
    body = _edit()
    assert "\\${DTD_SPLIT_SEED:+--focus split}" in body


def test_edit_screen_runs_one_form_then_edit_fast_json_then_the_split_script():
    body = _edit()
    assert (HERE / "edit-form.py").exists()
    assert 'python3 "\\$EDIT_FORM" --id "\\$1" "$DTD_CACHE_FILE" --out "\\$_out" --timing "\\$TIMING"' in body
    # one interpreter before the form: no dtd_resolve / --current / task-detail subprocesses
    pre = body[:body.index('python3 "\\$EDIT_FORM"')]
    assert "python3" not in pre and "DTD_RESOLVE" not in pre and "DTD_DETAIL" not in pre
    assert "vared" not in body and "_dtd_split_seed" not in body
    assert '--fields --id "\\$1" "$DTD_CACHE_FILE" --json "\\$_out"' in body
    assert "grep '^SPLIT ' | tail -n 1" in body
    assert '"/tmp/dtd-$DTD_ID.split.sh" "\\$1" "\\$pts" "\\$done_name" "" "" "\\$done_min"' in body
    # cancel = rc 2 and no result file; the header says so and nothing is written
    assert 'if (( _rc == 2 )); then echo "edit cancelled" > "\\$HDR"' in body
    assert body.index('[[ ! -s "\\$_out" ]]') < body.index('python3 "\\$EDIT_FAST"')
    # header: the split script owns it on the split path, the edit summary otherwise
    assert body.index("rm -f") < body.index("_split_line=")
    assert 'else\n  echo "\\${out:-✗ edit failed}" > "\\$HDR"' in body
    # timing stamps so dtd-timing-report can show ctrl-p → paint
    for stage in ("edit-start", "edit-end"):
        assert f"\\t{stage}\\t" in body, stage


def test_split_script_takes_minutes_done_and_emits_tags_independently():
    body = _split()
    assert ('pts_today="${2:-}"; done_desc="${3:-}"; remaining_desc="${4:-}"; '
            'remaining_override="${5:-}"; minutes_done="${6:-}"') in body
    assert "split-form" not in body.replace("# form (split-form.py, 2026-10-04)", "")
    assert "vared" not in body and "read -r pts_today" not in body
    assert '"${remaining_override:-}" "${minutes_done:-}"' in body
    assert "minutes_done = sys.argv[13].strip() if len(sys.argv) > 13 else ''" in body
    assert "remaining_dur = str(max(0, int(duration) - int(minutes_done)))" in body
    assert "posthoc_content = f'{today_label} ({done_dur or pts_today}) {open_b}{pts_today}{close_b}'" in body
    assert "new_content = ' '.join([remaining_desc or clean] + _tags)" in body
    assert "if remaining_dur and remaining_dur != '0':" in body
    assert "split cancelled" in body


def test_legend_names_the_breakoff():
    assert "ctrl-p: ✂done-so-far" in DTD
