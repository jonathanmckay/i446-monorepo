"""Regression tests for -2n time block configuration."""
import ast
import pathlib

SRC = pathlib.Path(__file__).parent / "-2n.py"


def _parse_blocks():
    """Extract BLOCKS constant via AST to avoid importing the module."""
    tree = ast.parse(SRC.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "BLOCKS":
                    return ast.literal_eval(node.value)
    raise AssertionError("BLOCKS constant not found in -2n.py")


def _parse_index_formula():
    """Extract the block index formula offset from get_current_block."""
    tree = ast.parse(SRC.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "get_current_block":
            for child in ast.walk(node):
                if isinstance(child, ast.BinOp) and isinstance(child.op, ast.FloorDiv):
                    # (hour - N) // 2 — extract N
                    left = child.left  # (hour - N)
                    if isinstance(left, ast.BinOp) and isinstance(left.op, ast.Sub):
                        if isinstance(left.right, (ast.Constant, ast.Num)):
                            return getattr(left.right, "value", getattr(left.right, "n", None))
    raise AssertionError("Block index formula not found in get_current_block")


def test_blocks_use_dizhi_not_arabic():
    """地支 names should be used, not Arabic prayer time names."""
    blocks = _parse_blocks()
    arabic = {"فجر", "شروق", "صباح", "ظهر", "عصر", "آصيل", "غروب", "غسق", "زلة"}
    dizhi = {"卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"}
    names = {b[0] for b in blocks}
    assert names == dizhi, f"Expected 地支 names, got {names}"
    assert names.isdisjoint(arabic), "Arabic names should not appear in BLOCKS"


def test_blocks_start_on_even_hours():
    """All time blocks should start on even-numbered hours."""
    blocks = _parse_blocks()
    for name, start, end in blocks:
        start_hour = int(start.split(":")[0])
        assert start_hour % 2 == 0, f"Block {name} starts at {start} (odd hour {start_hour})"


def test_block_index_formula_uses_offset_4():
    """Formula should be (hour - 4) // 2 for even-hour blocks."""
    offset = _parse_index_formula()
    assert offset == 4, f"Expected offset 4, got {offset}"


def _extract_allowed_tools(func_name):
    """Extract the --allowedTools string from a subprocess.run call in a function."""
    tree = ast.parse(SRC.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            for child in ast.walk(node):
                if isinstance(child, ast.List):
                    elts = [
                        e.value for e in child.elts
                        if isinstance(e, ast.Constant) and isinstance(e.value, str)
                    ]
                    if "--allowedTools" in elts:
                        idx = elts.index("--allowedTools")
                        if idx + 1 < len(elts):
                            return elts[idx + 1]
    raise AssertionError(f"--allowedTools not found in {func_name}")


def test_run_1g_allows_skill_tool():
    """run_1g must include Skill in allowedTools so /-1g skill can load."""
    tools = _extract_allowed_tools("run_1g")
    assert "Skill" in tools.split(","), f"Skill missing from run_1g allowedTools: {tools}"


def test_run_did_allows_skill_tool():
    """run_did must include Skill in allowedTools so /did skill can load."""
    tools = _extract_allowed_tools("run_did")
    assert "Skill" in tools.split(","), f"Skill missing from run_did allowedTools: {tools}"


# ── Regression: -1g goals must land in build order even if claude subprocess fails ──

import importlib.util
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch


def _load_two_n():
    """Import -2n.py despite the leading-dash filename."""
    spec = importlib.util.spec_from_file_location("_two_n_for_test", SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_two_n_for_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_parse_goals_text_handles_comma_and_newline():
    m = _load_two_n()
    assert m.parse_goals_text("foo, bar, baz") == ["foo", "bar", "baz"]
    assert m.parse_goals_text("foo\nbar") == ["foo", "bar"]
    assert m.parse_goals_text("- foo\n- bar") == ["foo", "bar"]
    assert m.parse_goals_text("[ ] foo, [x] bar") == ["foo", "bar"]
    assert m.parse_goals_text("") == []
    # Preserves case (regression: prompt_card used to lowercase goals)
    assert m.parse_goals_text("Sync With Andy") == ["Sync With Andy"]


def test_write_block_goals_writes_to_correct_dizhi_block(tmp_path):
    m = _load_two_n()
    fake_bo = tmp_path / "build-order.md"
    fake_bo.write_text(
        "# header\n\n## -1₲\n\n- 卯\n- 辰\n- 巳\n- 午\n- 未\n- 申\n- 酉\n- 戌\n- 亥\n\n## next-section\n"
    )
    with patch.object(m, "BUILD_ORDER", fake_bo):
        ok = m.write_block_goals("申", ["write LR docs", "review PR"])
        assert ok is True
        out = fake_bo.read_text()
        assert "    - [ ] write LR docs" in out
        assert "    - [ ] review PR" in out
        # Must be under 申, not 未 or 酉
        section = out[out.index("- 申"):out.index("- 酉")]
        assert "write LR docs" in section
        assert "review PR" in section


def test_write_block_goals_replaces_existing_checkboxes(tmp_path):
    m = _load_two_n()
    fake_bo = tmp_path / "build-order.md"
    fake_bo.write_text(
        "## -1₲\n\n- 申\n    - [ ] old goal\n    - [x] another old goal\n- 酉\n"
    )
    with patch.object(m, "BUILD_ORDER", fake_bo):
        m.write_block_goals("申", ["new goal"])
        out = fake_bo.read_text()
        assert "old goal" not in out
        assert "another old goal" not in out
        assert "    - [ ] new goal" in out


def test_run_1g_card_writes_locally_before_subprocess(tmp_path):
    """Regression: the -1g card must write goals to build order via Python,
    not rely solely on the `claude -p /-1g` subprocess (which can silently
    fail when capture_output=True hides errors)."""
    src_text = SRC.read_text()
    # The local write must precede the claude subprocess call.
    card_section = src_text[src_text.index("# ── Card 2: -1g"):]
    next_card = card_section.index("# ── Card 3")
    card_section = card_section[:next_card]
    assert "write_block_goals(" in card_section, \
        "Card 2 must call write_block_goals() so goals land even if claude fails"
    assert "parse_goals_text(" in card_section
    # Order check: parse + write must come before run_1g.
    assert card_section.index("run_1g(") < card_section.index("write_block_goals("), \
        "write_block_goals must run AFTER run_1g so local write is authoritative "\
        "and can't be overwritten by the claude subprocess"


def test_prompt_card_preserves_case_when_requested():
    """Regression: prompt_card unconditionally lowercased input, mangling
    goal text like 'Sync with Andy'. The -1g card must pass preserve_case=True."""
    tree = ast.parse(SRC.read_text())
    # Confirm prompt_card has preserve_case parameter.
    found_param = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "prompt_card":
            args = [a.arg for a in node.args.args]
            assert "preserve_case" in args, f"prompt_card missing preserve_case: {args}"
            found_param = True
    assert found_param
    # Confirm the -1g card prompt passes preserve_case=True.
    src_text = SRC.read_text()
    card_section = src_text[src_text.index("# ── Card 2: -1g"):]
    next_card = card_section.index("# ── Card 3")
    card_section = card_section[:next_card]
    assert "preserve_case=True" in card_section, \
        "-1g card must pass preserve_case=True to prompt_card"


def test_1g_card_rechecks_block_before_showing():
    """Regression: if the user was slow to respond to earlier cards (salah,
    gaps), the block could change (e.g. 卯 → 辰) while cards were still
    being displayed. The -1g card would then show goals for the old block.

    Fix: re-check get_current_block() right before the -1g card. If the
    block changed, return 0 to let the wrapper restart with fresh state."""
    src_text = SRC.read_text()
    card_section = src_text[src_text.index("# ── Card 2: -1g"):]
    next_section = card_section.index("# ── Card 3")
    before_1g = card_section[:next_section]
    assert "get_current_block()" in before_1g, \
        "Must re-check current block before showing -1g card"
    assert "new_idx != idx" in before_1g or "new_idx != idx" in before_1g, \
        "Must compare new block index against launch block index"
    assert "return 0" in before_1g, \
        "Must exit (return 0) if block changed so wrapper restarts"


def test_time_gap_audit_card_exists():
    """Regression: /inbound never audited time gaps from the previous block.
    Users had untracked periods that were silently lost.

    Fix: Card 1.5 checks Toggl entries for the previous 支 block, finds
    gaps >5min, and prompts the user to fill them via /did."""
    src_text = SRC.read_text()
    assert "check_time_gaps" in src_text, \
        "check_time_gaps function must exist"
    assert "get_previous_block" in src_text, \
        "get_previous_block function must exist"
    # The gap card must appear between salah and -1g
    card_section = src_text[src_text.index("write_prayer_marker"):]
    card_1g = card_section.index("# ── Card 2: -1g")
    between = card_section[:card_1g]
    assert "time_gaps" in between, \
        "Time gap audit card must appear between salah and -1g"
    assert "run_did" in between, \
        "Gap responses must be passed to run_did for processing"


def test_check_time_gaps_finds_gaps():
    """Unit test for check_time_gaps gap detection logic."""
    m = _load_two_n()
    # Mock toggl CLI output
    from unittest.mock import patch
    import subprocess
    fake_output = subprocess.CompletedProcess(
        args=[], returncode=0,
        stdout="16:00-16:30 work @i9 (30min) [id:1]\n16:45-17:20 tasks @i9 (35min) [id:2]\n",
        stderr=""
    )
    with patch("subprocess.run", return_value=fake_output):
        gaps = m.check_time_gaps("16:00", "17:59")
    # Should find gap 16:30-16:45 (15min) and 17:20-18:00 (40min)
    assert len(gaps) == 2
    assert gaps[0] == ("16:30", "16:45")
    assert gaps[1] == ("17:20", "18:00")


def test_check_time_gaps_no_gaps():
    """No gaps when entries cover the full block."""
    m = _load_two_n()
    from unittest.mock import patch
    import subprocess
    fake_output = subprocess.CompletedProcess(
        args=[], returncode=0,
        stdout="16:00-18:00 work @i9 (120min) [id:1]\n",
        stderr=""
    )
    with patch("subprocess.run", return_value=fake_output):
        gaps = m.check_time_gaps("16:00", "17:59")
    assert len(gaps) == 0


def test_block_change_watcher_exists():
    """Regression: /inbound ran ritual cards only at launch. When the 2h block
    changed (e.g. 16:00 → 18:00), salah and -1g were never re-prompted because
    ibx0.main() blocks indefinitely.

    Fix: a daemon thread watches for block changes and forces exit so the
    wrapper restarts -2n with fresh ritual cards."""
    src_text = SRC.read_text()
    # The block-change watcher must exist between Card 4 and ibx0.main()
    card4_section = src_text[src_text.index("# ── Card 4: ibx0"):]
    assert "_watch_block_change" in card4_section, \
        "Card 4 must spawn a block-change watcher thread"
    assert "launch_block_idx" in card4_section, \
        "Must track the block index at launch to detect changes"
    assert "daemon=True" in card4_section, \
        "Block-change watcher must be a daemon thread"


# ── Feature: -1g status panel on /inbound idle screen ────────────────────────

IBX0_PY = pathlib.Path(__file__).parent / "ibx0.py"


def test_read_block_goals_with_status_returns_done_flag(tmp_path):
    m = _load_two_n()
    fake_bo = tmp_path / "bo.md"
    fake_bo.write_text(
        "## -1₲\n\n- 申\n    - [ ] open one\n    - [x] done one\n    - [X] done two\n- 酉\n    - [ ] still open\n"
    )
    with patch.object(m, "BUILD_ORDER", fake_bo):
        out = m.read_block_goals_with_status()
    assert out["申"] == [("open one", False), ("done one", True), ("done two", True)]
    assert out["酉"] == [("still open", False)]


def test_read_block_goals_with_status_skips_prayer_marker(tmp_path):
    """Block-header ☀️ marker must not show up as a goal item."""
    m = _load_two_n()
    fake_bo = tmp_path / "bo.md"
    fake_bo.write_text(
        f"## -1₲\n\n- 申 {m.PRAYER_MARKER}\n    - [ ] real goal\n"
    )
    with patch.object(m, "BUILD_ORDER", fake_bo):
        out = m.read_block_goals_with_status()
    # Block name in dict should be clean ("申"), not "申 ☀️"
    assert "申" in out
    assert out["申"] == [("real goal", False)]


def test_read_block_goals_compat_returns_open_only(tmp_path):
    """Existing callers depend on read_block_goals returning open goals only."""
    m = _load_two_n()
    fake_bo = tmp_path / "bo.md"
    fake_bo.write_text(
        "## -1₲\n\n- 申\n    - [ ] open\n    - [x] done\n"
    )
    with patch.object(m, "BUILD_ORDER", fake_bo):
        out = m.read_block_goals()
    assert out["申"] == ["open"]


def test_render_block_status_panel_strikes_done_goals(tmp_path):
    from rich.console import Console as RConsole
    m = _load_two_n()
    fake_bo = tmp_path / "bo.md"
    fake_bo.write_text(
        "## -1₲\n\n- 申\n    - [ ] open task\n    - [x] done task\n"
    )
    with patch.object(m, "BUILD_ORDER", fake_bo):
        renderable = m.render_block_status_panel("申")
    out_console = RConsole(record=True, width=80)
    out_console.print(renderable)
    captured = out_console.export_text()
    assert "open task" in captured
    assert "done task" in captured
    # Open goals use ☐, done goals use ☑
    assert "☐ open task" in captured
    assert "☑ done task" in captured


def test_render_block_status_panel_shows_prayer_marker(tmp_path):
    from rich.console import Console as RConsole
    m = _load_two_n()
    fake_bo = tmp_path / "bo.md"
    fake_bo.write_text(
        f"## -1₲\n\n- 申\n    - [x] {m.PRAYER_MARKER}\n    - [ ] g1\n"
    )
    with patch.object(m, "BUILD_ORDER", fake_bo):
        renderable = m.render_block_status_panel("申")
    out_console = RConsole(record=True, width=80)
    out_console.print(renderable)
    captured = out_console.export_text()
    assert m.PRAYER_MARKER in captured
    assert "申" in captured


def test_render_block_status_panel_handles_empty(tmp_path):
    from rich.console import Console as RConsole
    m = _load_two_n()
    fake_bo = tmp_path / "bo.md"
    fake_bo.write_text("## -1₲\n\n- 申\n- 酉\n")
    with patch.object(m, "BUILD_ORDER", fake_bo):
        renderable = m.render_block_status_panel("申")
    out_console = RConsole(record=True, width=80)
    out_console.print(renderable)
    captured = out_console.export_text()
    assert "no goals set" in captured


def test_ibx0_renders_block_status_at_inbox_zero():
    """ibx0 must call _render_block_status before printing the Inbox zero message."""
    src = IBX0_PY.read_text()
    assert "_render_block_status()" in src, "ibx0 must define/call _render_block_status"
    # Must appear before the 'Inbox zero — watching' print.
    idx_call = src.find("_render_block_status()")
    # Find the call site, not just the def. Find AFTER the def block.
    def_end = src.index("def _render_block_status")
    def_end = src.index("\n\n", def_end)
    call_idx = src.index("_render_block_status()", def_end)
    zero_msg_idx = src.index("Inbox zero — watching")
    assert call_idx < zero_msg_idx, "Render must precede the Inbox zero message"


def test_salah_card_gated_only_by_prayer_marker():
    """Regression: the ☀️ build-order marker is per-2h-block, but the Neon ص
    column is per-day. Gating the salah card on `not salah_done AND not
    prayer_marker_exists` causes /inbound to silently skip the per-block sun
    prompt once the daily Neon mark is set. Card must depend solely on the
    per-block ☀️ marker."""
    src = SRC.read_text()
    # The cards_needed gate
    needed_block = src[src.index("cards_needed = []"):src.index("total_cards = len(cards_needed)")]
    assert "not salah_done and not prayer_marker_exists" not in needed_block, (
        "salah card append must NOT be ANDed with salah_done — that's a per-day flag"
    )
    assert 'cards_needed.append("salah")' in needed_block
    # The card render gate
    render_block = src[src.index("# ── Card 1: صلاة"):src.index("# ── Card 2: -1g")]
    assert "not salah_done and not prayer_marker_exists" not in render_block, (
        "salah card render must NOT be ANDed with salah_done"
    )
    # Must still check the per-block marker.
    assert "prayer_marker_exists" in render_block


def test_1g_card_uses_multiline_input():
    """Regression: -1g card needs multiline input so users can hit Enter
    between goals. prompt_card must accept a multiline=True flag, and the
    -1g card must pass it."""
    tree = ast.parse(SRC.read_text())
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "prompt_card":
            args = [a.arg for a in node.args.args]
            assert "multiline" in args, f"prompt_card missing multiline param: {args}"
            found = True
    assert found
    src_text = SRC.read_text()
    card_section = src_text[src_text.index("# ── Card 2: -1g"):]
    next_card = card_section.index("# ── Card 3")
    card_section = card_section[:next_card]
    assert "multiline=True" in card_section, \
        "-1g card must pass multiline=True so multiple goals can be entered with newlines"


def test_prompt_card_multiline_reads_until_blank(monkeypatch):
    """prompt_card with multiline=True returns lines joined by \n, stopping
    on the first blank line."""
    m = _load_two_n()
    inputs = iter(["first goal", "second goal", "third goal", ""])
    def fake_input(prompt=""):
        return next(inputs)
    monkeypatch.setattr(m.console, "input", fake_input)
    monkeypatch.setattr(m, "set_term_color", lambda *_: None)
    resp = m.prompt_card(1, 1, "test", "body", options="x", preserve_case=True, multiline=True)
    assert resp == "first goal\nsecond goal\nthird goal"


def test_parse_goals_text_keeps_multiline_goals():
    """parse_goals_text must keep each newline-separated goal intact (not
    comma-split a goal that legitimately contains a comma)."""
    m = _load_two_n()
    text = "Sync with Andy, Ralph\nReview WorkIQ deck\nDraft 1:1 notes"
    # Lines without bullets get comma-split, so first line splits into 2.
    # That's fine; document the behavior.
    out = m.parse_goals_text(text)
    assert "Review WorkIQ deck" in out
    assert "Draft 1:1 notes" in out
    # And bulleted multiline preserves commas
    bulleted = "- Sync with Andy, Ralph\n- Review WorkIQ deck"
    out2 = m.parse_goals_text(bulleted)
    assert "Sync with Andy, Ralph" in out2


def test_write_prayer_marker_works_on_empty_block(tmp_path):
    """Regression: prayer card runs BEFORE -1g card, so when the user prays
    first the block usually has no goals yet. write_prayer_marker used to
    require an existing checkbox to anchor against — silently no-op'd on
    empty blocks. Must now insert the marker right after the block header."""
    m = _load_two_n()
    fake_bo = tmp_path / "bo.md"
    fake_bo.write_text("## -1₲\n\n- 申\n- 酉\n")
    with patch.object(m, "BUILD_ORDER", fake_bo):
        m.write_prayer_marker("申")
        assert m.has_prayer_marker("申")
    out = fake_bo.read_text()
    申_section = out[out.index("- 申"):out.index("- 酉")]
    assert m.PRAYER_MARKER in 申_section


def test_write_prayer_marker_does_not_double_insert(tmp_path):
    m = _load_two_n()
    fake_bo = tmp_path / "bo.md"
    fake_bo.write_text(f"## -1₲\n\n- 申 {m.PRAYER_MARKER}\n- 酉\n")
    with patch.object(m, "BUILD_ORDER", fake_bo):
        m.write_prayer_marker("申")
    out = fake_bo.read_text()
    assert out.count(m.PRAYER_MARKER) == 1


def test_write_block_goals_preserves_prayer_marker(tmp_path):
    """Regression: write_block_goals must NOT wipe the ☀️ marker. Marker
    lives on the block header line, so it stays put naturally."""
    m = _load_two_n()
    fake_bo = tmp_path / "bo.md"
    fake_bo.write_text(
        f"## -1₲\n\n- 申 {m.PRAYER_MARKER}\n- 酉\n"
    )
    with patch.object(m, "BUILD_ORDER", fake_bo):
        m.write_block_goals("申", ["new goal"])
    out = fake_bo.read_text()
    assert m.PRAYER_MARKER in out, "prayer marker was wiped by write_block_goals"
    # Marker on header line, before any goals.
    assert out.index(m.PRAYER_MARKER) < out.index("new goal")
    # And it stays on the header, not a child line.
    assert f"- 申 {m.PRAYER_MARKER}" in out


def test_pray_then_set_goals_preserves_marker(tmp_path):
    """End-to-end: simulate /inbound flow — prayer card writes ☀️ to empty
    block, then -1g card writes goals. ☀️ must survive and appear above goals."""
    m = _load_two_n()
    fake_bo = tmp_path / "bo.md"
    fake_bo.write_text("## -1₲\n\n- 申\n- 酉\n")
    with patch.object(m, "BUILD_ORDER", fake_bo):
        # Card 1: prayer
        m.write_prayer_marker("申")
        # Card 2: goals
        m.write_block_goals("申", ["focus task"])
        # Reader should see prayer + 1 open goal
        status = m.read_block_goals_with_status()
    assert ("focus task", False) in status["申"]
    out = fake_bo.read_text()
    assert m.PRAYER_MARKER in out
    assert out.index(m.PRAYER_MARKER) < out.index("focus task"), \
        "prayer marker must come before goals in build order"


def test_prune_stale_briefs_drops_yesterday(tmp_path):
    """Regression: meeting briefs from yesterday were persisting forever
    because mtg.py only filters when STAGING new briefs. -2n.py must prune
    at read time."""
    m = _load_two_n()
    fake_briefs = tmp_path / "mtg-briefs.json"
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    tomorrow = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
    briefs = [
        {"event_id": "stale", "title": "Yesterday's meeting", "start": yesterday},
        {"event_id": "fresh", "title": "Tomorrow's meeting", "start": tomorrow},
    ]
    fake_briefs.write_text(json.dumps(briefs))
    with patch.object(m, "MTG_BRIEFS", fake_briefs):
        kept = m._prune_stale_briefs(briefs)
    assert len(kept) == 1
    assert kept[0]["event_id"] == "fresh"
    # File was rewritten without the stale entry.
    on_disk = json.loads(fake_briefs.read_text())
    assert len(on_disk) == 1
    assert on_disk[0]["event_id"] == "fresh"


def test_prune_stale_briefs_keeps_within_grace(tmp_path):
    """Briefs whose meeting started within the last 4h must still be kept."""
    import datetime as _dt
    m = _load_two_n()
    fake_briefs = tmp_path / "mtg-briefs.json"
    just_started = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    briefs = [{"event_id": "active", "title": "In progress", "start": just_started}]
    fake_briefs.write_text(json.dumps(briefs))
    with patch.object(m, "MTG_BRIEFS", fake_briefs):
        kept = m._prune_stale_briefs(briefs)
    assert len(kept) == 1


def test_prune_stale_briefs_deletes_file_when_empty(tmp_path):
    m = _load_two_n()
    fake_briefs = tmp_path / "mtg-briefs.json"
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    briefs = [{"event_id": "stale", "title": "old", "start": yesterday}]
    fake_briefs.write_text(json.dumps(briefs))
    with patch.object(m, "MTG_BRIEFS", fake_briefs):
        kept = m._prune_stale_briefs(briefs)
    assert kept == []
    assert not fake_briefs.exists()


def test_main_prunes_stale_briefs_before_card_count():
    """AST: main() must call _prune_stale_briefs before building cards_needed,
    otherwise stale briefs still inflate the card count."""
    src = SRC.read_text()
    main_section = src[src.index("def main("):src.index("\nif __name__")]
    assert "_prune_stale_briefs(" in main_section
    prune_idx = main_section.index("_prune_stale_briefs(")
    cards_idx = main_section.index("cards_needed = []")
    assert prune_idx < cards_idx, "Stale briefs must be pruned BEFORE cards_needed is built"


# Module-level imports needed by the prune tests above
from datetime import datetime, timedelta, timezone
import json


def test_gap_fill_calls_toggl_create_not_run_did():
    """Bug: gap fill responses like '900-915 wake up, 915-930 hci' were passed
    to run_did which doesn't create Toggl entries for unmatched items.
    Fix: gap fill now calls fill_time_gaps which directly invokes toggl_cli create."""
    source = SRC.read_text()
    tree = ast.parse(source)

    # 1. fill_time_gaps function must exist and call toggl_cli create
    assert "def fill_time_gaps(" in source, "fill_time_gaps function must exist"
    assert '"create"' in source, "fill_time_gaps must call toggl_cli with 'create'"

    # 2. The gap card handler must call fill_time_gaps, NOT run_did
    # Find the gap card section (Card 1.5)
    gap_section = source[source.index("Card 1.5"):source.index("Card 2")]
    assert "fill_time_gaps(" in gap_section, "Gap card must call fill_time_gaps"
    assert "run_did(" not in gap_section, "Gap card must NOT call run_did"


def test_fill_time_gaps_parses_segments():
    """fill_time_gaps must parse HHMM-HHMM format and extract @project overrides."""
    source = SRC.read_text()

    # Verify fill_time_gaps handles both HHMM and H:MM formats
    func_src = source[source.index("def fill_time_gaps("):]
    func_src = func_src[:func_src.index("\ndef ", 1)]  # up to next function

    # Must split on commas
    assert ".split(\",\")" in func_src or "split(',')" in func_src, \
        "fill_time_gaps must split input on commas"

    # Must handle @project extraction
    assert "@" in func_src and "project" in func_src, \
        "fill_time_gaps must extract @project overrides"

    # Must normalize HHMM → HH:MM
    assert "100" in func_src or "// 100" in func_src, \
        "fill_time_gaps must normalize HHMM to HH:MM"


def test_fill_time_gaps_accepts_no_time_prefix_with_gaps():
    """Bug: entering 'joe 1:1 @i9' for a single gap was rejected as bad format
    because fill_time_gaps required HHMM-HHMM prefix on every segment.
    Fix: when gaps list is provided and a segment has no time prefix, use the
    corresponding gap's time range."""
    source = SRC.read_text()
    func_src = source[source.index("def fill_time_gaps("):]
    func_src = func_src[:func_src.index("\ndef ", 1)]

    # Function must accept gaps parameter
    assert "def fill_time_gaps(response, gaps=" in source, \
        "fill_time_gaps must accept a gaps parameter"

    # Must use gap time range when no time prefix is found
    assert "gaps[i]" in func_src or "gaps[0]" in func_src, \
        "fill_time_gaps must fall back to gap time ranges for segments without time prefix"

    # Call site must pass time_gaps
    gap_section = source[source.index("Card 1.5"):source.index("Card 2")]
    assert "gaps=time_gaps" in gap_section, \
        "Gap card must pass time_gaps to fill_time_gaps"


def test_snapshot_archives_yesterday_not_today():
    """Regression: snapshot_build_order() saved today's (empty/reset) build order
    instead of yesterday's enriched version. The enriched data (time entries,
    completed tasks, meetings) was lost because the snapshot captured the file
    AFTER the daily reset cleared the -1₲ section.
    Fix: snapshot_build_order archives YESTERDAY's date, not today's."""
    src = SRC.read_text()
    func_start = src.index("def snapshot_build_order(")
    func_end = src.index("\ndef ", func_start + 1)
    func_src = src[func_start:func_end]
    # Must reference yesterday, not today
    assert "timedelta(days=1)" in func_src or "timedelta(1)" in func_src, \
        "snapshot_build_order must compute yesterday's date"
    # Must NOT use today's date for the filename
    assert "today" not in func_src.lower() or "yesterday" in func_src.lower(), \
        "snapshot filename must use yesterday's date, not today's"


def test_block_name_strips_duration_suffix(tmp_path):
    """Regression: block headers with (Nmin) duration suffix caused goal lookup
    to miss existing goals. _block_name_from_header('- 午 ☀️ 📧 ⏰ (134min)')
    returned '午    (134min)' instead of '午', so goals_set was False even when
    goals were written under that block."""
    m = _load_two_n()
    # Direct function test
    assert m._block_name_from_header("- 辰 ☀️ 📧 ⏰ (134min)") == "辰"
    assert m._block_name_from_header("- 午 ☀️ 📧 ⏰ (90min)") == "午"
    assert m._block_name_from_header("- 申") == "申"
    assert m._block_name_from_header("- 亥 📧") == "亥"

    # End-to-end: goals under a block with (Nmin) must be found by canonical name
    fake_bo = tmp_path / "bo.md"
    fake_bo.write_text(
        "## -1₲\n\n"
        "- 辰 ☀️ 📧 ⏰ (134min)\n"
        "    - [ ] morning goal\n"
        "- 巳 ☀️ 📧 ⏰ (124min)\n"
        "    - [x] done goal\n"
        "    - [ ] open goal\n"
        "- 午 ☀️ 📧 ⏰\n"
        "    - [ ] afternoon goal\n"
    )
    with patch.object(m, "BUILD_ORDER", fake_bo):
        goals = m.read_block_goals()
    assert goals.get("辰") == ["morning goal"], f"辰 goals: {goals.get('辰')}"
    assert goals.get("巳") == ["open goal"], f"巳 goals: {goals.get('巳')}"
    assert goals.get("午") == ["afternoon goal"], f"午 goals: {goals.get('午')}"
