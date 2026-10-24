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

--daily flips the form to 0g mode (2026-10-18: "0g should also go to a form
like -1g does from dtd"): same three lines, but goals land flush-left under
`## 0₲` (before `### 以后的目标`), a lone `[N]` is CONVERTED to `{N}` instead of
marking a plain todo (/0g rule, bug 2026-07-11: daily-goal points belong in the
0g column), default is {15}, and `@code` stays in the text. Save = the /0g
skill's steps, locally:
  1. append under ## 0₲ of build-order.md ON IX (same single-writer rule)
  2. scripts/0g_log.py — the set-time archive that survives the 04:00 reset
  3. scripts/0g-sync.py sync — the canonical MD→Todoist path (it owns the
     state file, labels and adopt/dedup; creating tasks here directly would
     let its cron double-create one completed before the next sync)
  4. did-fast "0g" (marks the 0₦ habit, closes the card) + --refresh-cache

Usage:
  python3 -1g-form.py                 # the -1g form
  python3 -1g-form.py --daily         # the 0g form
  python3 -1g-form.py --from-json f   # {"goals": ["...", "..."]} instead of the form
  python3 -1g-form.py --dry-run       # print what would be written, write nothing
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

LIB = Path.home() / "i446-monorepo" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))
import neon_blocks as nb  # noqa: E402

DID_FAST = Path.home() / "i446-monorepo" / "tools" / "did" / "did-fast.py"
ZEROG_SYNC = Path.home() / "i446-monorepo" / "scripts" / "0g-sync.py"
ZEROG_LOG = Path.home() / "i446-monorepo" / "scripts" / "0g_log.py"
TODOIST_TOKEN_FILE = Path.home() / ".config" / "todoist" / "token"
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


_BRACE_RE = re.compile(r"\{\d+\}")
_SQUARE_RE = re.compile(r"\[(\d+)\]")


def plan_daily(lines: list[str]) -> list[str]:
    """Pure: the typed lines -> 0₲ goal texts. Every line is a goal — no
    plain-todo split: /0g converts a lone [N] to {N} because daily-goal points
    belong in the 0g column (bug 2026-07-11). @code stays in the text;
    0g-sync's own label detection reads it from the MD line."""
    goals = []
    for line in lines:
        for item in nb.parse_goals_text(line or ""):
            if not _BRACE_RE.search(item):
                item = _SQUARE_RE.sub(r"{\1}", item)
            goals.append(nb.ensure_goal_points(item, default=15))
    return goals


RECOVERY_DIR = Path.home() / ".cache" / "1g-form-recovery"


def _append_on_ix(block: str, goals: list[str], daily: bool = False) -> list[str]:
    """Append under `block` (or, daily, under ## 0₲) on ix's build-order (the
    single writer) and return the goals actually added. Off-ix, delegate over
    ssh; if ix is unreachable raise -- NEVER fall back to a local write: ix's
    goal-less copy would sync back over it the moment --ritual's own ssh stamp
    succeeds (the 2026-08-11 clobber). The caller dumps the goals to
    RECOVERY_DIR instead."""
    call = (f"nb.append_daily_goals({goals!r})" if daily
            else f"nb.append_block_goals({block!r}, {goals!r})")
    if _on_ix():
        return nb.append_daily_goals(goals) if daily else nb.append_block_goals(block, goals)
    py = (
        "import sys, json; sys.path.insert(0, '/Users/mckay/i446-monorepo/lib')\n"
        "import neon_blocks as nb\n"
        f"print(json.dumps({call}))\n"
    )
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                        "ix", "python3", "-"], input=py, capture_output=True,
                       text=True, timeout=20)
    if r.returncode != 0:
        raise RuntimeError(f"ssh ix failed: {(r.stderr or '').strip()[-200:]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def _dump_recovery(block: str, lines: list[str]) -> Path:
    RECOVERY_DIR.mkdir(parents=True, exist_ok=True)
    import datetime as dt
    p = RECOVERY_DIR / f"{dt.datetime.now().isoformat(timespec='seconds')}.json"
    p.write_text(json.dumps({"block": block, "goals": lines}, ensure_ascii=False, indent=1))
    return p


LOG = Path.home() / ".cache" / "1g-form.log"


def _hdr(msg: str) -> None:
    """Show `msg` in dtd's header line when launched from dtd (DTD_HDR_FILE)."""
    hdr = os.environ.get("DTD_HDR_FILE")
    if hdr:
        try:
            Path(hdr).write_text(msg + "\n")
        except OSError:
            pass


def detach(lines: list[str], block: str, daily: bool = False) -> None:
    """Run save() in a detached child and return at once, so dtd's list is
    back on screen immediately instead of after ~5s of Todoist + Excel +
    cache refresh (user report 2026-10-17: "exiting the -1g dialog and
    getting back to dtd was a bit too slow"). The child reports into dtd's
    header via DTD_HDR_FILE and logs to LOG; dtd's cache watcher hides the
    card once the refresh lands."""
    tmp = Path(tempfile.mkstemp(prefix="1g-form-", suffix=".json")[1])
    tmp.write_text(json.dumps({"goals": lines}, ensure_ascii=False))
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "ab") as log:
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--from-json", str(tmp),
                          "--block", block, "--unlink-json"] + (["--daily"] if daily else []),
                         stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
    n = sum(1 for l in lines if l.strip())
    _hdr(f"⏳ 0g: saving {n} goal(s)…" if daily else f"⏳ -1g → {block}: saving {n} goal(s)…")


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
    added: list[str] = []
    if p["goals"]:
        try:
            added = _append_on_ix(block, [g["text"] for g in p["goals"]])
            msgs.append(f"build-order {block}: +{len(added)}")
        except Exception as e:  # noqa: BLE001 -- ssh down, block header missing, ...
            rec = _dump_recovery(block, lines)
            msg = f"✗ -1g FAILED: build-order write ({e}); goals saved to {rec}"
            print(msg, file=out)
            _hdr(msg)
            return 1
        # Todoist only for goals the build-order didn't already hold, so an
        # Esc reflex / resubmit never duplicates tasks.
        for g in p["goals"]:
            if g["text"] not in added:
                continue
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
    # --ritual -1g closes the card AND refreshes the task cache itself (one
    # blocking refresh, not two); todos-only just needs the refresh.
    args = ["--ritual", "-1g"] if p["goals"] else ["--refresh-cache"]
    r = subprocess.run([sys.executable, str(DID_FAST), *args], capture_output=True, text=True,
                       timeout=120)
    if r.returncode != 0:
        msgs.append(f"did-fast {' '.join(args)} FAILED rc={r.returncode}: "
                    f"{(r.stderr or r.stdout).strip()[-160:]}")
    failed = any("FAILED" in m for m in msgs)
    summary = f"-1g → {block}: {len(added)} goals, {len(p['todos'])} todos · " + " · ".join(msgs)
    print(summary, file=out)
    _hdr(("✗ " if failed else "✓ ") + summary)
    return 1 if failed else 0


def _zerog_sync() -> subprocess.CompletedProcess:
    """Run 0g-sync where the goals were just written. It reads build-order.md
    from disk, and the goals were appended on IX; run here (Straylight) it read
    the local copy before Syncthing delivered them, logged "Found 0 unchecked
    tasks in 0₲", created no Todoist tasks, and still reported "todoist
    synced" (bug 2026-10-24: 0g goals never appeared in dtd). Off ix it runs
    over ssh against ix's copy; on ix, locally. Same token handling as the
    cron: TODOIST_API_KEY from the env, else lib/todoist's token file."""
    if _on_ix():
        env = dict(os.environ)
        if "TODOIST_API_KEY" not in env:
            try:
                env["TODOIST_API_KEY"] = TODOIST_TOKEN_FILE.read_text().strip()
            except OSError:
                pass
        return subprocess.run([sys.executable, str(ZEROG_SYNC), "sync"],
                              capture_output=True, text=True, timeout=120, env=env)
    remote = ('TODOIST_API_KEY="${TODOIST_API_KEY:-$(cat ~/.config/todoist/token)}" '
              'python3 ~/i446-monorepo/scripts/0g-sync.py sync')
    return subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "ix", remote],
                          capture_output=True, text=True, timeout=150)


def save_daily(lines: list[str], *, dry_run: bool = False, out=sys.stdout) -> int:
    goals = plan_daily(lines)
    if not goals:
        print("0g: nothing entered, nothing written", file=out)
        return 0
    if dry_run:
        print(json.dumps({"goals": goals}, ensure_ascii=False, indent=1), file=out)
        return 0
    msgs = []
    try:
        added = _append_on_ix("", goals, daily=True)
        msgs.append(f"build-order 0₲: +{len(added)}")
    except Exception as e:  # noqa: BLE001 -- ssh down, section missing, ...
        rec = _dump_recovery("0₲", lines)
        msg = f"✗ 0g FAILED: build-order write ({e}); goals saved to {rec}"
        print(msg, file=out)
        _hdr(msg)
        return 1
    # Set-time archive: goals must reach 0g-log.md the moment they're set so
    # they survive the 04:00 reset / 0₲→以后的目标 move (regression 2026-06-26).
    if added:
        r = subprocess.run([sys.executable, str(ZEROG_LOG), *added],
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            msgs.append(f"0g-log FAILED: {(r.stderr or r.stdout).strip()[-120:]}")
    # Todoist via 0g-sync, the canonical MD→Todoist path: it owns the state
    # file, label detection and adopt/dedup. Creating tasks here directly
    # would let its cron re-create any goal completed before the next sync
    # adopts it. The cron passes TODOIST_API_KEY explicitly; hand it the same
    # token lib/todoist uses when the env/Keychain doesn't have one.
    r = _zerog_sync()
    if r.returncode != 0:
        msgs.append(f"0g-sync FAILED rc={r.returncode}: {(r.stderr or r.stdout).strip()[-160:]}")
    else:
        msgs.append("todoist synced")
    # Plain "0g" marks the 0₦ habit and closes the card but (unlike --ritual)
    # does not refresh the task cache itself, so refresh separately.
    for args in (["0g"], ["--refresh-cache"]):
        r = subprocess.run([sys.executable, str(DID_FAST), *args],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            msgs.append(f"did-fast {' '.join(args)} FAILED rc={r.returncode}: "
                        f"{(r.stderr or r.stdout).strip()[-160:]}")
    failed = any("FAILED" in m for m in msgs)
    summary = f"0g: {len(added)} goals · " + " · ".join(msgs)
    print(summary, file=out)
    _hdr(("✗ " if failed else "✓ ") + summary)
    return 1 if failed else 0


def run_form(block: str, daily: bool = False) -> list[str] | None:
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
        " @code domain · {N} pts (default {15}) · (N) min · [N] becomes {N}" if daily else
        " @code domain · {N} bonus (default {10}) · (N) min · [N]-only = plain todo"),
        height=1, style="class:label")
    status = Window(FormattedTextControl(
        " Enter/Tab next · Enter on last, Esc or ^S save · ^Q cancel"), height=1, style="class:status")
    title = "0g · daily goals" if daily else f"-1g · {block} goals"
    root = HSplit([Frame(HSplit([hint, *rows]), title=title), status])
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
    ap.add_argument("--daily", action="store_true",
                    help="0g mode: goals go under ## 0₲ instead of the current block")
    ap.add_argument("--detach", action="store_true",
                    help="after the form, save in a detached child and exit at once (dtd)")
    ap.add_argument("--unlink-json", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args()
    block = a.block or nb.current_block(_local_hour())
    if a.from_json:
        lines = json.loads(Path(a.from_json).read_text())["goals"]
        if a.unlink_json:
            Path(a.from_json).unlink(missing_ok=True)
    else:
        lines = run_form(block, daily=a.daily)
        if lines is None:
            print(("0g" if a.daily else "-1g") + ": cancelled")
            return 0
        if a.detach and not a.dry_run and any(l.strip() for l in lines):
            detach(lines, block, daily=a.daily)
            return 0
    if a.daily:
        return save_daily(lines, dry_run=a.dry_run)
    return save(lines, block=block, dry_run=a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
