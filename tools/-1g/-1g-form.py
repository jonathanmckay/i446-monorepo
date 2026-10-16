#!/usr/bin/env python3
"""-1g block-goal form: three optional lines, saved straight into the current
2-hour block without a Claude subprocess.

Opened by dtd's Enter on the `😈 -1g` ritual card (2026-10-16: "hitting enter
on that sort of task from dtd will open the form in that cmd line"). Each line
takes the same shorthand as /-1g: `@code` picks the domain, `{N}` the bonus
points (default {10}), `(N)` minutes; a `[N]`-only line is a plain todo, not a
block goal (the /-1g rule). Enter/Tab on the last line, Esc or ^S save; ^Q/^C
discard. All three blank = nothing written.

Save = the /-1g skill's steps, locally:
  1. append the goals under the current 地支 block of build-order.md ON IX
     (single writer, locked; off-ix callers delegate over ssh, falling back
     to an unlocked local write that Syncthing carries, same as did-fast)
  2. create the Todoist tasks (project 0g, labels #-1g + domain, p1, today)
  3. did-fast --refresh-cache (foreground), then --ritual -1g (closes the card)

Usage:
  python3 -1g-form.py                 # the form
  python3 -1g-form.py --from-json f   # {"goals": ["...", "..."]} instead of the form
  python3 -1g-form.py --dry-run       # print what would be written, write nothing
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

LIB = Path.home() / "i446-monorepo" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))
import neon_blocks as nb  # noqa: E402

DID_FAST = Path.home() / "i446-monorepo" / "tools" / "did" / "did-fast.py"
GOALS_PROJECT = "6XfvCQ3p8Gq6fhGR"   # Todoist project "0g"
GOAL_LABEL = "#-1g"
FIELDS = ["goal 1", "goal 2", "goal 3"]


def _local_hour() -> int:
    try:
        import daytime
        return daytime.local_now().hour
    except Exception:  # noqa: BLE001
        import datetime as dt
        return dt.datetime.now().hour


def _on_ix() -> bool:
    import socket
    host = socket.gethostname().lower()
    return host in ("ix", "ix.local") or host.startswith("ix.") or "mac-mini" in host


def plan(lines: list[str], block: str) -> dict:
    """Pure: the three typed lines -> what gets written where.
    {"goals": [{"text", "domain"}], "todos": [{"text", "domain"}]}"""
    goals, todos = [], []
    for line in lines:
        for item in nb.parse_goals_text(line or ""):
            text, dom = nb.split_goal_domain(item, block)
            if not text:
                continue
            if nb.is_plain_todo(text):
                todos.append({"text": text, "domain": dom})
            else:
                goals.append({"text": nb.ensure_goal_points(text), "domain": dom})
    return {"goals": goals, "todos": todos}


def _append_on_ix(block: str, goals: list[str]) -> tuple[list[str], str]:
    """(goals actually added, 'ix' | 'local' | 'local-fallback')."""
    if _on_ix():
        return nb.append_block_goals(block, goals), "ix"
    py = (
        "import sys, json; sys.path.insert(0, '/Users/mckay/i446-monorepo/lib')\n"
        "import neon_blocks as nb\n"
        f"print(json.dumps(nb.append_block_goals({block!r}, {goals!r})))\n"
    )
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                            "ix", "python3", "-"], input=py, capture_output=True,
                           text=True, timeout=20)
        if r.returncode == 0:
            return json.loads(r.stdout.strip().splitlines()[-1]), "ix"
    except Exception:  # noqa: BLE001
        pass
    return nb.append_block_goals(block, goals), "local-fallback"


def save(lines: list[str], *, block: str | None = None, dry_run: bool = False,
         out=sys.stdout) -> int:
    block = block or nb.current_block(_local_hour())
    p = plan(lines, block)
    if not p["goals"] and not p["todos"]:
        print("-1g: nothing entered, nothing written", file=out)
        return 0
    if dry_run:
        print(json.dumps({"block": block, **p}, ensure_ascii=False, indent=1), file=out)
        return 0
    import todoist
    msgs = []
    if p["goals"]:
        added, where = _append_on_ix(block, [g["text"] for g in p["goals"]])
        msgs.append(f"build-order {block}: +{len(added)} ({where})")
        for g in p["goals"]:
            try:
                todoist.create_task(g["text"], labels=[GOAL_LABEL, g["domain"]],
                                    due_string="today", priority=4, project_id=GOALS_PROJECT)
            except Exception as e:  # noqa: BLE001
                msgs.append(f"todoist FAILED {g['text']!r}: {e}")
    for t in p["todos"]:
        try:
            todoist.create_task(t["text"], labels=[t["domain"]], due_string="today")
        except Exception as e:  # noqa: BLE001
            msgs.append(f"todoist FAILED {t['text']!r}: {e}")
    for args in (["--refresh-cache"], ["--ritual", "-1g"]) if p["goals"] else (["--refresh-cache"],):
        subprocess.run([sys.executable, str(DID_FAST), *args],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
    print(f"-1g → {block}: {len(p['goals'])} goals, {len(p['todos'])} todos · " + " · ".join(msgs),
          file=out)
    return 1 if any("FAILED" in m for m in msgs) else 0


def run_form(block: str) -> list[str] | None:
    from prompt_toolkit import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import HSplit, Layout, VSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.styles import Style
    from prompt_toolkit.widgets import Frame, TextArea

    areas = [TextArea(multiline=False, style="class:input", height=1) for _ in FIELDS]
    rows = [VSplit([Window(FormattedTextControl(f" {lbl:<8}"), width=10, style="class:label"), a])
            for lbl, a in zip(FIELDS, areas)]
    hint = Window(FormattedTextControl(
        " @code domain · {N} bonus (default {10}) · (N) min · [N]-only = plain todo"),
        height=1, style="class:label")
    status = Window(FormattedTextControl(
        " Enter/Tab next · Enter on last, Esc or ^S save · ^Q cancel"), height=1, style="class:status")
    root = HSplit([Frame(HSplit([hint, *rows]), title=f"-1g · {block} goals"), status])
    last = len(areas) - 1

    def _idx(app):
        cc = app.layout.current_control
        return next((i for i, a in enumerate(areas) if a.control is cc), None)

    kb = KeyBindings()

    @kb.add("enter", eager=True)
    @kb.add("tab", eager=True)
    def _(e):
        if _idx(e.app) == last:
            e.app.exit(result="submit")
        else:
            e.app.layout.focus_next()

    @kb.add("s-tab", eager=True)
    def _(e):
        e.app.layout.focus_previous()

    @kb.add("escape", eager=True)
    @kb.add("c-s")
    def _(e):
        e.app.exit(result="submit")

    @kb.add("c-q")
    @kb.add("c-c")
    def _(e):
        e.app.exit(result=None)

    style = Style([("label", "fg:#7c7c7c"), ("input", "bg:#1b1b1b fg:#cfcfcf"),
                   ("status", "bg:#111111 fg:#00e676"), ("frame.border", "fg:#333333")])
    app = Application(layout=Layout(root, focused_element=areas[0]), key_bindings=kb,
                      full_screen=True, style=style, mouse_support=True)
    if app.run() != "submit":
        return None
    return [a.text.strip() for a in areas]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-json", help='{"goals": [...]} instead of the form')
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--block", help="地支 block (default: current)")
    a = ap.parse_args()
    block = a.block or nb.current_block(_local_hour())
    if a.from_json:
        lines = json.loads(Path(a.from_json).read_text())["goals"]
    else:
        lines = run_form(block)
        if lines is None:
            print("-1g: cancelled")
            return 0
    return save(lines, block=block, dry_run=a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
