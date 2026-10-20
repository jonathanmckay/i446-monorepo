#!/usr/bin/env python3
"""1 f693 — weekly people stoplight + "what do I owe each person" todos.

The weekly ritual (g245/5e1/1-neon-meta.md › Specific Rituals › f693):
stoplight every person on the org diagram, then add a todo for what *I* need
to do to make each of them more successful. This script is the survey half,
runnable straight from dtd (Enter on the `1 f693` card) or via `/1f693`:

  1. opens the drawio org diagram in Chrome (m5c7.com profile — m5x2 context)
  2. full-screen prompt_toolkit form: 6 slots of  <initials>  <todo>
     (a todo may carry its own `(N)` minutes / `[N]` value; defaults (15) [10])
  3. on save: one Todoist task per filled slot, due today, labels i9 + f693,
     content `f693 <INIT>: <todo> (N) [N]`
  4. appends a dated entry to vault/h335/f693/f693.md › ## Current Org with a
     screenshot placeholder, a pending stoplight line, and the todos
  5. marks the `1 f693` habit done (did run.py → 1n+ points), refreshes dtd
  6. opens the doc in Obsidian (paste the screenshot) and launches a cc tab
     with `/1f693 stoplight`, which reads the pasted image and writes the table

Usage:
  1f693.py                       interactive (what dtd runs)
  1f693.py --from-json f.json    non-interactive: [{"initials": "AB", "task": "..."}]
  1f693.py --dry-run             print the tasks + entry, write nothing
  1f693.py --no-browser --no-mark --no-launch   skip side effects
  1f693.py --open-diagram        just open the diagram and exit
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DIAGRAM_URL = ("https://app.diagrams.net/#G13lL3iRagUQucleRmp966RMG8R2iMRoUd"
               "%23%7B%22pageId%22%3A%22Fk-glKlrqk2ZiSOgV627%22%7D")
CHROME_PROFILE = "Profile 11"          # m5c7.com — the m5x2 context (no m5x2.com profile exists)
VAULT = Path.home() / "vault"
F693_DOC = VAULT / "h335/f693/f693.md"
ANCHOR = "## Current Org"
HABIT = "1 f693"
LABELS = ["i9", "f693"]
DEFAULT_MIN, DEFAULT_VAL = 15, 10
SLOTS = 6
DID_RUN = Path.home() / "i446-monorepo/tools/did/run.py"
DID_FAST = Path.home() / "i446-monorepo/tools/did/did-fast.py"
TODOIST_BASE = "https://api.todoist.com/api/v1"


# ── pure helpers (tested) ───────────────────────────────────────────────────
def parse_task_spec(text: str) -> tuple[str, int, int]:
    """'call X (20) [15]' → ('call X', 20, 15); missing → defaults."""
    minutes, value = DEFAULT_MIN, DEFAULT_VAL
    m = re.search(r"\((\d+)\)", text)
    if m:
        minutes = int(m.group(1)); text = text.replace(m.group(0), "")
    v = re.search(r"\[(\d+)\]", text)
    if v:
        value = int(v.group(1)); text = text.replace(v.group(0), "")
    return re.sub(r"\s{2,}", " ", text).strip(), minutes, value


def task_content(initials: str, task: str) -> str:
    desc, minutes, value = parse_task_spec(task)
    ini = initials.strip().upper()
    head = f"f693 {ini}: {desc}" if ini else f"f693 {desc}"
    return f"{head} ({minutes}) [{value}]"


def clean_slots(slots: list[dict]) -> list[dict]:
    """Drop blank rows; keep order; normalise whitespace."""
    out = []
    for s in slots:
        task = (s.get("task") or "").strip()
        if not task:
            continue
        out.append({"initials": (s.get("initials") or "").strip().upper(), "task": task})
    return out


def entry_date(d: _dt.date | None = None) -> str:
    d = d or _dt.date.today()
    return f"{d.month}.{d.day:02d}"


def render_entry(slots: list[dict], d: _dt.date | None = None) -> str:
    lines = [entry_date(d),
             "<!-- paste the org screenshot here (⌘V in Obsidian), then run /1f693 stoplight -->",
             "",
             "**Stoplight:** _pending — run `/1f693 stoplight` after pasting the screenshot_",
             "",
             "**Next steps:**"]
    for s in clean_slots(slots):
        lines.append(f"* {task_content(s['initials'], s['task'])}")
    if len(lines) == 6:
        lines.append("* _(none recorded)_")
    return "\n".join(lines) + "\n"


def insert_entry(doc: str, entry: str) -> str:
    """Newest entry goes right under the ## Current Org heading."""
    idx = doc.find(ANCHOR)
    if idx < 0:
        return doc.rstrip("\n") + "\n\n" + ANCHOR + "\n\n" + entry
    end = doc.find("\n", idx)
    end = len(doc) if end < 0 else end + 1
    return doc[:end] + "\n" + entry + "\n" + doc[end:]


# ── side effects ────────────────────────────────────────────────────────────
def _todoist_token() -> str:
    """Reuse did-fast's token (single source; never duplicate the secret)."""
    m = re.search(r'TODOIST_TOKEN\s*=\s*"([^"]+)"', DID_FAST.read_text())
    if not m:
        raise RuntimeError("TODOIST_TOKEN not found in did-fast.py")
    return m.group(1)


def create_task(content: str, token: str) -> str:
    body = json.dumps({"content": content, "due_string": "today", "labels": LABELS}).encode()
    req = urllib.request.Request(f"{TODOIST_BASE}/tasks", data=body, method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())["id"]


def open_diagram() -> None:
    subprocess.Popen(["open", "-na", "Google Chrome", "--args",
                      f"--profile-directory={CHROME_PROFILE}", DIAGRAM_URL],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def open_doc_in_obsidian() -> None:
    rel = str(F693_DOC.relative_to(VAULT))
    subprocess.Popen(["open", "obsidian://open?path=" + urllib.parse.quote(rel, safe="")],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def launch_stoplight_tab() -> str:
    """New cmux tab running `cc "/1f693 stoplight"` so the Claude half is
    waiting when the screenshot has been pasted."""
    try:
        out = subprocess.run(["cmux", "new-surface", "--type", "terminal"], capture_output=True,
                             text=True, timeout=10).stdout
        m = re.search(r"surface:(\d+)", out)
        if not m:
            return "cmux: no surface id"
        subprocess.run(["cmux", "respawn-pane", "--surface", f"surface:{m.group(1)}",
                        "--command", 'cc "/1f693 stoplight"'], capture_output=True, timeout=10)
        return "cc tab opened: /1f693 stoplight"
    except Exception as e:  # noqa: BLE001
        return f"cmux unavailable ({e}); run: cc \"/1f693 stoplight\""


def mark_done() -> str:
    proc = subprocess.run(["/usr/bin/python3", str(DID_RUN), HABIT], capture_output=True, text=True, timeout=120)
    msg = (proc.stdout or proc.stderr or "").strip().splitlines()
    try:
        subprocess.run(["/usr/bin/python3", str(DID_FAST), "--refresh-cache"], capture_output=True, timeout=60)
    except Exception:  # noqa: BLE001
        pass
    return msg[-1] if msg else "(no output from did run.py)"


def report(line: str) -> None:
    print(line, flush=True)
    hdr = os.environ.get("DTD_HDR_FILE")
    if hdr:
        try:
            Path(hdr).write_text(line + "\n")
        except Exception:  # noqa: BLE001
            pass


# ── form ────────────────────────────────────────────────────────────────────
def run_form() -> list[dict] | None:
    from prompt_toolkit import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import HSplit, Layout, VSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.styles import Style
    from prompt_toolkit.widgets import Frame, TextArea

    areas: list[tuple[TextArea, TextArea]] = []
    rows = [VSplit([Window(FormattedTextControl("  #"), width=4, style="class:label"),
                    Window(FormattedTextControl("who "), width=7, style="class:label"),
                    Window(FormattedTextControl("what I need to do so they succeed   (N) [N] optional"),
                           style="class:label")])]
    for i in range(SLOTS):
        who = TextArea(multiline=False, height=1, style="class:input")
        what = TextArea(multiline=False, height=1, wrap_lines=True, style="class:input")
        areas.append((who, what))
        rows.append(VSplit([Window(FormattedTextControl(f"  {i + 1}"), width=4, style="class:label"),
                            who, Window(width=1, char=" "), what]))
        for a in (who, what):
            a.window.width = None
        who.window.width = 6

    msg = {"text": ""}
    status = Window(FormattedTextControl(
        lambda: msg["text"] or "Tab next · S-Tab back · Enter next · Esc/^S save → Todoist + f693 doc · ^Q cancel"),
        height=1, style="class:status")
    root = HSplit([Frame(HSplit(rows, padding=0), title=f"1 f693 · stoplight todos · {entry_date()}"), status])

    order = [a for pair in areas for a in pair]

    def _idx(app):
        cc = app.layout.current_control
        for i, a in enumerate(order):
            if a.control is cc:
                return i
        return None

    kb = KeyBindings()

    @kb.add("tab", eager=True)
    @kb.add("enter", eager=True)
    def _(e):
        i = _idx(e.app)
        if i is None or i == len(order) - 1:
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
    app = Application(layout=Layout(root, focused_element=order[0]), key_bindings=kb,
                      full_screen=True, style=style, mouse_support=True)
    if app.run() != "submit":
        return None
    return [{"initials": who.text, "task": what.text} for who, what in areas]


# ── main ────────────────────────────────────────────────────────────────────
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-json", help="slots JSON file instead of the form")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--no-mark", action="store_true")
    ap.add_argument("--no-launch", action="store_true", help="don't open Obsidian / the cc stoplight tab")
    ap.add_argument("--open-diagram", action="store_true")
    a = ap.parse_args(argv)

    if a.open_diagram:
        open_diagram(); return 0
    if not a.no_browser and not a.dry_run:
        open_diagram()

    if a.from_json:
        slots = json.loads(Path(a.from_json).read_text())
    else:
        slots = run_form()
        if slots is None:
            report("1 f693 cancelled"); return 0
    slots = clean_slots(slots)
    entry = render_entry(slots)

    if a.dry_run:
        for s in slots:
            print("todoist:", task_content(s["initials"], s["task"]))
        print("--- f693.md entry ---"); print(entry, end="")
        return 0

    lines = []
    if slots:
        token = _todoist_token()
        ok = 0
        for s in slots:
            content = task_content(s["initials"], s["task"])
            try:
                create_task(content, token); ok += 1
            except (urllib.error.URLError, OSError, KeyError) as e:
                lines.append(f"✗ todoist: {content} ({e})")
        lines.append(f"+{ok} todoist")
    doc = F693_DOC.read_text()
    F693_DOC.write_text(insert_entry(doc, entry))
    lines.append("f693.md ✓")
    if not a.no_mark:
        lines.append(mark_done())
    if not a.no_launch:
        open_doc_in_obsidian()
        lines.append(launch_stoplight_tab())
    report("1 f693 · " + " · ".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
