#!/usr/bin/env python3
"""Regressions from 2026-10-22:

1. "dtd more info page mangles links. I want it to show the full link in
   notes, and have a hyperlink that will open the link in the correct chrome
   context in a separate window if I click on it either from detail or main
   view." The details screen (edit-form.py) showed the raw [(link)](https://…)
   in the title and both name fields, cut off at the pane width, and its link
   rows were plain text. Now: links are cut out of the name (and restored on
   save), shown whole (wrapped) under notes, and drawn as OSC 8 hyperlinks to
   hammerspoon://dtdlink (dtd_link.click_url) which opens the task's Chrome
   profile with --new-window.
2. "dtd startup shows a long scrolling thing": the dead-launch sweep ran
   `rm -f` on /tmp/dtd-*.resolved DIRECTORIES, printing one "is a directory"
   line per dead launch (49 on Straylight). Plus "make startup faster": the
   age/block cache refresh (~2.2 s) runs in the background now.
3. ctrl-c / ctrl-q from the details page must land on the list: ctrl-q was
   unbound (fzf's default = abort, rc 130, no back router), and intr was
   restored before fzf re-entered raw mode.
"""
import importlib.util
import json
import os
import pty
import re
import select
import struct
import sys
import time
from pathlib import Path

import fcntl
import termios

HERE = Path(__file__).resolve().parent
DTD = (HERE / "dtd.sh").read_text()
sys.path.insert(0, str(HERE))
import dtd_link  # noqa: E402

SP = "https://microsoft.sharepoint.com/:x:/t/X/cQrx?isSPOFile=1&web=1&clickparams=eyJBcHBOYW1lIjoiVGVhbXMifQ"


def _ef():
    spec = importlib.util.spec_from_file_location("edit_form_links", HERE / "edit-form.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


# ── dtd_link ─────────────────────────────────────────────────────────────────

def test_click_url_routes_profile_and_round_trips():
    from urllib.parse import parse_qs, urlparse
    t = dtd_link.click_url(SP, ["m5x2"])
    q = parse_qs(urlparse(t).query)
    assert t.startswith("hammerspoon://dtdlink?")
    assert q["p"] == ["Profile 11"] and q["u"] == [SP]
    assert parse_qs(urlparse(dtd_link.click_url(SP, ["i9"])).query)["p"] == ["Profile 1"]
    assert parse_qs(urlparse(dtd_link.click_url(SP, [])).query)["p"] == ["Profile 5"]


def test_urls_keep_balanced_parens_and_drop_trailing_punctuation():
    assert dtd_link.urls_in("see https://a.com/Doc(1).docx, then") == ["https://a.com/Doc(1).docx"]
    assert dtd_link.urls_in("(see https://b.com/x)") == ["https://b.com/x"]
    assert dtd_link.urls_in("[(link)](https://c.com/p?q=1) and https://c.com/p?q=1") == ["https://c.com/p?q=1"]


def test_new_window_in_open_cmd():
    assert "--new-window" in dtd_link.open_cmd("https://x.com", "Profile 5")


def test_hammerspoon_handler_only_accepts_known_profiles_and_http():
    lua = (Path.home() / ".hammerspoon" / "dtd-link.lua")
    if not lua.exists():
        return  # Ix / CI: no Hammerspoon config
    src = lua.read_text()
    assert 'hs.urlevent.bind("dtdlink"' in src
    for prof, _ in [*dtd_link.PROFILES.values(), dtd_link.DEFAULT_PROFILE]:
        assert f'["{prof}"] = true' in src
    assert '^https?://' in src and "--new-window" in src


# ── edit-form: name without links, restored on save ─────────────────────────

def test_prefill_cuts_links_out_of_the_name():
    ef = _ef()
    p = ef.prefill({"id": "t", "content": f"go through Data DRI [(link)]({SP}) (30) [30]", "labels": ["i9"]}, "")
    assert p["name"] == "go through Data DRI"
    assert p["name_links"] == [f"[(link)]({SP})"]


def test_result_untouched_name_keeps_original_edited_name_keeps_links():
    ef = _ef()
    base = {"name_pre": "read the memo", "name_full": f"read [(link)]({SP}) the memo",
            "name_links": [f"[(link)]({SP})"], "desc_loaded": False}
    assert ef.result({**base, "name": "read the memo"})["name"] == base["name_full"]
    assert ef.result({**base, "name": "read the new memo"})["name"] == f"read the new memo [(link)]({SP})"
    assert ef.result({**base, "name": ""})["name"] == ""        # blank = keep, as before


def test_title_row_shows_link_not_markdown():
    ef = _ef()
    assert ef.show_links(f"pay [(link)]({SP}) (5)") == "pay 🔗 (5)"
    assert ef.show_links("read https://x.com/a now") == "read 🔗 now"


# ── edit-form: pty render ────────────────────────────────────────────────────

def _render(tmp_path: Path, cols: int, content: str, labels, desc: str = "") -> str:
    state = tmp_path / "state"
    (state / "jm" / "task-detail").mkdir(parents=True)
    (state / "jm" / "task-detail" / "t1.json").write_text(json.dumps({
        "task": {"id": "t1", "content": content, "description": desc, "labels": labels}, "comments": []}))
    (tmp_path / "cache.json").write_text(json.dumps({"today": [{"id": "t1", "content": content, "labels": labels}]}))
    env = dict(os.environ, TERM="xterm-256color", XDG_STATE_HOME=str(state),
               LANG="en_US.UTF-8", LC_ALL="en_US.UTF-8")
    pid, fd = pty.fork()
    if pid == 0:
        os.execvpe(sys.executable, [sys.executable, str(HERE / "edit-form.py"), "--id", "t1",
                                    str(tmp_path / "cache.json"), "--out", str(tmp_path / "o.json")], env)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, cols, 0, 0))
    buf = b""

    def pump(t):
        nonlocal buf
        end = time.time() + t
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], 0.05)
            if r:
                try:
                    buf += os.read(fd, 65536)
                except OSError:
                    return False
        return True
    pump(1.5)
    os.write(fd, b"\x1b")
    pump(1.0)                 # keep draining: a full pty buffer blocks the form's exit
    if os.waitpid(pid, os.WNOHANG) == (0, 0):
        os.kill(pid, 9)
        os.waitpid(pid, 0)
    return buf.decode("utf-8", "replace")


def test_detail_screen_shows_the_whole_link_as_a_profile_hyperlink(tmp_path):
    raw = _render(tmp_path, 51, f"go through Data DRI [(link)]({SP}) (30) [30]", ["m5x2"])
    targets = set(re.findall(r"\x1b\]8;[^;]*;([^\x1b]+)\x1b\\", raw))
    assert targets == {dtd_link.click_url(SP, ["m5x2"])}
    # every piece of the URL is on screen: the hyperlinked segments rejoin to it
    segs = re.findall(r"\x1b\]8;[^;]*;[^\x1b]+\x1b\\([^\x1b]*)\x1b\]8;;", raw)
    assert "".join(segs) == SP
    assert "[(link)]" not in raw          # no raw markdown in title or name rows


def test_notes_links_are_listed_too(tmp_path):
    raw = _render(tmp_path, 100, "plain task (5) [3]", ["xk87"], desc="doc: https://d.com/x(1).pdf")
    assert dtd_link.click_url("https://d.com/x(1).pdf", ["xk87"]) in raw


# ── dtd.sh: startup + ctrl-c/ctrl-q ──────────────────────────────────────────

def test_startup_sweep_removes_resolved_dirs_quietly():
    sweep = DTD[DTD.index("for _f in /tmp/dtd-<->-*(Nmh+1)"):]
    sweep = sweep[:sweep.index("done")]
    assert 'rm -rf "$_f" 2>/dev/null' in sweep
    assert 'rm -rf "$DTD_RESOLVED_DIR"' in DTD          # and the session's own at exit


def test_age_and_block_staleness_refresh_in_background():
    for guard in ('"$cache_age" -gt "$DTD_CACHE_MAX_AGE" ]]; then', '"$stale_block" == "1" ]]; then'):
        blk = DTD[DTD.index(guard):]
        blk = blk[:blk.index("\nfi\n")]
        assert "_dtd_refresh_bg" in blk and "--refresh-cache" not in blk
    assert 'last_m=${DTD_SNAP_M:-' in DTD                # watcher baseline = the snapshot's mtime


def test_ctrl_q_goes_through_the_back_router():
    assert '--bind "ctrl-q:transform($DTD_BACK {q})"' in DTD
