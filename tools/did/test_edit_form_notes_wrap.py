#!/usr/bin/env python3
"""Regression 2026-11-08: "notes in details screen don't wrap they are just
one line so I can't read". The notes field cut every line at the pane width,
and each comment showed only its first line (agent comments open with a bare
**vN context** header, so nothing readable). Both wrap now."""
import json
from pathlib import Path

import test_edit_form as tef
from test_edit_form import _load

TAIL_NOTE = "ZEBRAWORD"
NOTES = "send the near recruiter draft from the m5x2 account after the gdm round " * 3 + TAIL_NOTE
COMMENT = "**v181 context (2026-11-07):**\n\nChild Send Near recruiter email was checked off but the draft is " \
          "still unsent and nothing is in Sent, so verify the send QUOKKAWORD"


def test_notes_wrap_into_rows_that_cover_the_text():
    ef = _load()
    chunks = ef.wrap_chunks("ab\n" + "x" * 25, 10)
    assert [seg for _, seg in chunks] == ["ab", "x" * 10, "x" * 10, "x" * 5]
    assert [st for st, _ in chunks] == [0, 3, 13, 23]
    assert [seg for _, seg in ef.wrap_chunks("", 10)] == [""]


def test_comments_show_their_whole_body_wrapped_newest_kept():
    ef = _load()
    bundle = {"task": {"labels": ["m5x2"], "due": {"string": "Oct 13"}},
              "comments": [{"posted_at": "2026-11-0%dT00:00" % d, "content": COMMENT} for d in (1, 3, 7)]}
    rows = ef.detail_lines(bundle, "Inbox", width=60)
    assert all(ef.dwidth(t) <= 60 for _, t in rows)
    assert sum("QUOKKAWORD" in t for _, t in rows) == 3 and not any("**" in t for _, t in rows)
    capped = ef.detail_lines(bundle, "Inbox", width=60, cap=4)
    assert len(capped) == 4 and capped[0][1].startswith("#Inbox") and "QUOKKAWORD" in capped[-1][1]


def test_pty_long_notes_and_comment_are_readable(tmp_path, monkeypatch):
    real_env = tef._env

    def env(tmp, content=tef.LONG):
        e = real_env(tmp, content)
        p = Path(e["XDG_STATE_HOME"]) / "jm" / "task-detail" / "t1.json"
        b = json.loads(p.read_text())
        b["task"]["description"] = NOTES
        b["comments"] = [{"posted_at": "2026-11-07T00:00", "content": COMMENT}]
        p.write_text(json.dumps(b))
        return e

    monkeypatch.setattr(tef, "_env", env)
    rc, _, _, _, screen = tef._run(tmp_path, "\x1b")
    assert rc == 2
    assert TAIL_NOTE in screen        # end of a 220-char one-line note, pane is 100 wide
    assert "QUOKKAWORD" in screen     # end of the comment body, not just its header
