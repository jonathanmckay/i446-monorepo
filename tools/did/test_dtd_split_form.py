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
    assert ('"/tmp/dtd-$DTD_ID.split.sh" "\\$1" "\\$pts" "\\$done_name" "" "\\$rem_pts" "\\$done_min" '
            '"\\$rem_min" "\\$done_short" "\\$rem_short" "\\$done_bonus" "\\$rem_bonus" "\\$rem_due" "\\$keep"') in body
    assert 'done_bonus=""; rem_bonus=""; rem_due=""; keep="1"' in body
    # the save is backgrounded (haiku regeneration must not block the list) and
    # pokes fzf's port to reload once the cache is patched
    assert "} </dev/null >> \"/tmp/dtd-edit.log\" 2>&1 &!" in body
    assert body.index('echo "⏳ saving…"') < body.index("trap '' HUP") < body.index('python3 "\\$EDIT_FAST"')
    assert '--data "reload(\\$_reload)+transform-header($DTD_HDRGEN)"' in body
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
            'remaining_override="${5:-}"; minutes_done="${6:-}"; remaining_min_override="${7:-}"; '
            'done_short="${8:-}"; rem_short="${9:-}"') in body
    assert "'--set-short', '--id', tid" in body and "dtd-short:{_h}:{done_short}" in body
    assert "if len(sys.argv) > 14 and sys.argv[14].strip().isdigit():" in body
    assert "split-form" not in body.replace("# form (split-form.py, 2026-10-04)", "")
    assert "vared" not in body and "read -r pts_today" not in body
    assert ('"$task_id" "${bonus_today:-}" "${bonus_total:-}" "${remaining_override:-}" "${minutes_done:-}" '
            '"${remaining_min_override:-}" "${done_short:-}" "${rem_short:-}" "${rem_bonus_override:-}" '
            '"${rem_due:-}" "${keep_rem:-1}"') in body
    assert "minutes_done = sys.argv[13].strip() if len(sys.argv) > 13 else ''" in body
    assert "remaining_dur = str(max(0, int(duration) - int(minutes_done)))" in body
    assert "posthoc_content = ' '.join([today_label] + _done_tags)" in body
    assert "new_content = ' '.join([remaining_desc or clean] + _tags)" in body
    assert "if remaining_dur and remaining_dur != '0':" in body
    assert "split cancelled" in body


def test_cancelling_ctrl_p_keeps_the_search_query():
    """Bug 2026-10-22: "cancelling out of the split leads to the task
    disappearing". ctrl-p ended in clear-query, so Esc out of the breakoff
    form wiped the search that had found the task and the list snapped back
    to the full 70 rows with the task far off-screen."""
    bind = next(l for l in DTD.splitlines() if '--bind "ctrl-p:' in l)
    assert "+clear-query" not in bind, bind     # never unconditional
    assert "transform([[ -e /tmp/dtd-$DTD_ID.editsaved ]] && echo clear-query)" in bind
    body = _edit()
    # marker reset on entry, set only on the save path (after the cancel exit)
    assert 'rm -f "\\$_out" "/tmp/dtd-$DTD_ID.editsaved"' in body
    assert body.index('edit cancelled') < body.index(': > "/tmp/dtd-$DTD_ID.editsaved"')


def test_split_remainder_stays_on_today_by_default():
    """Bug 2026-10-22: "I split a task and the remainder isn't showing up in
    dtd". A blank do-date deferred the remainder to tomorrow, and the split
    also hid the task's name for the session, so the remainder could not
    show today. Blank now means today, and the name is hidden only when the
    task leaves today (closed, or a later do-date typed)."""
    body = _split()
    assert "rem_date = date.today().isoformat()" in body
    assert "timedelta(days=1)" not in body
    assert "_body['due_date'] = rem_date" in body
    assert "is_recurring" in body       # v169: never strip a repeat rule
    guard = "if not keep_rem or rem_date > date.today().isoformat():\n    with open(removed_file, 'a') as f: f.write(clean.lower()"
    assert guard in body
    assert body.count("with open(removed_file, 'a')") == 1


def test_split_ties_the_done_name_to_the_done_record_id():
    """Bug 2026-10-22 (same report): even on today, the remainder stayed
    hidden. The done credit (did-fast --points-only) records its name in
    completed-today with no id, and dtd hides a name-only completion by NAME,
    so the remainder, which shares the name, vanished. The split now maps
    that name to the completed done record's id, and id-backed names hide by
    id only (test_dtd_done_id_hide)."""
    body = _split()
    credit = body.index("didfast_bonus = _credit(")
    tie = body.index("_mc.append_names([safe_name.strip()], ids={safe_name.strip(): posthoc['id']})")
    assert credit < tie
    assert "if posthoc and keep_rem and (didfast_out or didfast_bonus):" in body
    assert "mark-completed.py" in body[credit:tie]


def test_legend_names_the_breakoff():
    assert "ctrl-p: ✂done-so-far" in DTD


def test_split_script_handles_bonus_do_date_and_partial_credit():
    """-1g goals (2026-10-22): [N] and {N} are independent (each has its own
    done/remaining and did-fast write, {N} never with a domain label); the
    remainder defers to the typed do-date; keep=0 closes the task and undo
    reopens it."""
    body = _split()
    assert '[[ "$pts_today" =~ ^[0-9]+$ || "$bonus_today" =~ ^[0-9]+$ ]] || { echo "split cancelled"' in body
    assert "bonus_total=$(echo \"$task\" | grep -oE '\\{[0-9]+\\}'" in body
    assert "open_b" not in body and "$bracket" not in body
    assert "remaining_bonus = max(0, bonus_total - bonus_today) if bonus_total > 0 else 0" in body
    assert "_tags.append(f'{{{remaining_bonus}}}')" in body
    assert "if re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', rem_due):\n    rem_date = rem_due" in body
    assert "else:\n    api('POST', f'/tasks/{tid}/close')" in body
    assert "didfast_out = _credit(f'{safe_name} [{pts_today}] {label_arg}') if pts_today > 0 else None" in body
    assert "didfast_bonus = _credit(f'{safe_name} {{{bonus_today}}}') if bonus_today > 0 else None" in body
    assert "'didfast_bonus': didfast_bonus," in body and "'closed': not keep_rem," in body
    # the python -c body is double-quoted in zsh: a bare double quote would end it
    py = body[body.index('python3 -c "') + len('python3 -c "'):body.index('" "$clean"')]
    assert '"' not in py.replace('\\"', "")
    undo = (HERE / "undo-fast.py").read_text()
    assert 'if tid and record.get("closed"):' in undo and '_api("POST", f"/tasks/{tid}/reopen")' in undo
    assert 'for key in ("didfast", "didfast_bonus"):' in undo
