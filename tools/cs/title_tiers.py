#!/usr/bin/env python3
"""title_tiers — one session, three title lengths, every surface shows the same work.

A cmux tab has room for about two characters (2 ASCII, 1 CJK, or 1 emoji); the
tmux title line fits a phone-width label; Claude's own statusline fits the full
task name. The three tiers come from the dtd task the session holds (/d or
auto-claim) and fall back to the session's title when it holds none, so a
claimed session reads the same in dtd, on its tab, and in its statusline.

  init   1-2 chars, Haiku-chosen once per distinct text, cached in
         ~/.local/state/jm/initials.json {sha1(text)[:8]: init}. Its own file:
         shorten.py rewrites task-shortnames.json wholesale from a stale copy,
         and tasks under PROSE_CAP never get an entry there.
  short  the task's Haiku short name (task-shortnames.json, estimates stripped)
         or the session's (session-shortnames.json), else the full text trimmed
         at a word boundary to session_short.CAP.
  full   the task content with (N) [N] {N} stripped, or the session title.

`apply <sid>` pushes the tiers to the tab: inside tmux as the session's @tab
("<init> <short>") and @init (the initial alone) options; dotfiles/tmux.conf has
set-titles-string '#{?#{@init},#{@init},#{?#{@tab},#{@tab},#S}}', so the title
tmux forwards to the outer terminal, which cmux prints as the tab text, is just
the initial (2026-11-14: the two-tier text ate the whole tab). Inside a local
cmux via rename-tab; else OSC 0. With --generate it also asks Haiku for a missing
initial and records it on the claim (<tid>.json 'init') so dtd can lead the
row with the same glyph (that file travels to the other hosts with the claim).

An unclaimed thread leads with ❔ instead of an initial, so the strip shows
which tabs are not linked to a dtd task.

Usage:
  title_tiers.py tiers <sid>            JSON {init, short, full, source}
  title_tiers.py apply <sid> [--generate] [--target <tmux session>]
  title_tiers.py apply-all [--generate] every live claude in tmux here (cs tmux-map)
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path.home() / "i446-monorepo" / "lib"))
import state_paths as _sp  # noqa: E402

STATE = _sp.STATE_DIR
INITIALS = STATE / "initials.json"
SESSION_TITLES = STATE / "session-titles"
SESSION_SHORTS = STATE / "session-shortnames.json"
TASK_SHORTS = _sp.TASK_SHORTNAMES
CAP = 28
MODEL = "claude-haiku-4-5-20251001"
# A thread with no dtd task behind it leads with this instead of a Haiku initial,
# so the tab strip shows at a glance which sessions are not linked to work.
UNCLAIMED_INIT = "❔"
FREE_INIT = "~~"   # the task this session held was closed; nothing new started yet (2026-11-14)
_DID = Path.home() / "i446-monorepo" / "tools" / "did"
_EST = re.compile(r"\(\(?\d+\)?\)|\[\d*G?\]|\{\d+\}")
_LABEL = re.compile(r"(?:^|\s)@\S+")


def _mod(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _claims():
    return _mod("agent_claims_for_tiers", _DID / "agent_claims.py")


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


def _json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def prose(content: str) -> str:
    """Task content without estimates, @labels and the 😈 automation mark."""
    s = _LABEL.sub(" ", _EST.sub("", content)).replace("😈", "")
    return re.sub(r"\s+", " ", s).strip(" -–—:")


def trim(text: str, cap: int = CAP) -> str:
    """Cut at a word boundary, never mid-word; a cut that lands exactly on a
    space keeps the whole word before it."""
    if len(text) <= cap:
        return text
    if text[cap] == " ":
        return text[:cap].rstrip(" -–—:,")
    cut = text[:cap]
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > 0 else cut).rstrip(" -–—:,")


# --- initials ---------------------------------------------------------------
def cells(s: str) -> int:
    """Terminal columns a string takes: wide (CJK, emoji) glyphs count 2."""
    n = 0
    for ch in s:
        if unicodedata.combining(ch) or ch in "️‍":
            continue
        n += 2 if unicodedata.east_asian_width(ch) in ("W", "F") or ord(ch) >= 0x1F000 else 1
    return n


def valid_initial(s: str) -> bool:
    s = s.strip()
    return bool(s) and " " not in s and 1 <= cells(s) <= 2


def _haiku_initial(text: str) -> str | None:
    """One Haiku call, no retries, 6 s cap: the caller is a detached background job."""
    try:
        import anthropic
        key = _mod("dtd_shorten", _DID / "shorten.py")._api_key()
        client = anthropic.Anthropic(api_key=key, max_retries=0, timeout=6) if key \
            else anthropic.Anthropic(max_retries=0, timeout=6)
        resp = client.messages.create(
            model=MODEL, max_tokens=12,
            messages=[{"role": "user", "content": (
                "Give a tab initial for this task: EITHER two ASCII letters/digits "
                "(like 'Xb' for an Xbox deck, 'tx' for taxes), OR one Chinese character, "
                "OR one emoji. It must identify the task at a glance among a dozen tabs. "
                "Prefer a domain code's letters when the task carries one (i9, m5x2, dtd). "
                "Return only the initial, nothing else.\n\nTask: " + text[:200])}],
        )
        cand = resp.content[0].text.strip().strip("'\"`")
        return cand if valid_initial(cand) else None
    except Exception:
        return None


def initial_for(text: str, generate: bool = False, compute=_haiku_initial) -> str | None:
    """Cached initial for `text`; with generate, compute and cache a missing one."""
    if not text:
        return None
    h = _hash(text)
    data = _json(INITIALS)
    hit = data.get(h)
    if hit or not generate:
        return hit or None
    init = compute(text)
    if not init:
        return None
    data = _json(INITIALS)   # re-read: another apply may have written meanwhile
    data[h] = init
    try:
        INITIALS.parent.mkdir(parents=True, exist_ok=True)
        tmp = INITIALS.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=0))
        tmp.replace(INITIALS)
    except OSError:
        pass
    return init


# --- tiers --------------------------------------------------------------------
def is_free(sid: str) -> bool:
    """/fin (or a dtd close) finished this session's task and nothing has been
    typed since: agent_claims.set_free left by-session/<sid>.free, which the
    next ordinary prompt or claim removes."""
    if not sid:
        return False
    ac = _claims()
    return (ac.CLAIMS_DIR / "by-session" / f"{sid}.free").exists()


def claim_record(sid: str) -> dict | None:
    """The claim record of the first task this session holds, from the local
    store or a mirrored remote/<host>/ one (Straylight's cs reads Ix's claims there)."""
    if not sid:
        return None
    ac = _claims()
    for d in ac._dirs(ac.CLAIMS_DIR):
        tids = ac.held(sid, d)
        if not tids:
            continue
        rec = _json(d / f"{tids[0]}.json")
        if rec:
            rec.setdefault("task_id", tids[0])
            rec["_dir"] = str(d)
            return rec
    return None


def tiers(sid: str, generate: bool = False, title: str = "") -> dict:
    """`title` is a fallback for a session with no recorded title yet (apply-all
    passes the one cs read from the transcript)."""
    rec = claim_record(sid)
    if rec:
        full = prose(rec.get("task", "")) or rec.get("task_id", "")
        ts = _json(TASK_SHORTS).get(str(rec.get("task_id")), {})
        short = prose(ts.get("short", "")) if ts.get("short") else ""
        init = rec.get("init") or initial_for(full, generate)
        return {"init": init, "short": short or trim(full), "full": full,
                "source": "task", "task_id": rec.get("task_id"), "_dir": rec.get("_dir")}
    try:
        full = (SESSION_TITLES / sid).read_text().strip()
    except OSError:
        full = ""
    full = full or title
    if is_free(sid):
        return {"init": FREE_INIT, "short": "", "full": full, "source": "free"}
    ss = _json(SESSION_SHORTS).get(sid, {})
    short = ss.get("short") if ss.get("hash") == _hash(full) else ""
    return {"init": UNCLAIMED_INIT if full else None, "short": short or trim(full),
            "full": full, "source": "session"}


def tab_text(t: dict) -> str:
    return " ".join(x for x in (t.get("init"), t.get("short")) if x).strip()


# --- apply --------------------------------------------------------------------
def _tmux_tab(text: str, target: str | None = None) -> bool:
    pane = target or os.environ.get("TMUX_PANE")
    if not pane or (not target and not os.environ.get("TMUX")):
        return False
    try:
        # A pane target with no -p/-w sets the option on the session holding
        # the pane: one cc tmux session holds one claude, so that is the tab.
        # apply-all passes the tmux session name instead.
        subprocess.run(["tmux", "set-option", "-t", pane, "@tab", text],
                       capture_output=True, timeout=3)
        # 2026-11-14 (JM): the cmux tab is too narrow for "<init> <short>", so
        # the title tmux forwards is the initial alone (@init); @tab keeps the
        # two-tier text for anything wider. set-titles-string prefers @init.
        init = text.split(" ", 1)[0] if text else ""
        if init:
            subprocess.run(["tmux", "set-option", "-t", pane, "@init", init],
                           capture_output=True, timeout=3)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def _cmux_tab(text: str) -> bool:
    surf = os.environ.get("CMUX_PANEL_ID") or os.environ.get("CMUX_SURFACE_ID")
    if not surf:
        return False
    import shutil
    if not shutil.which("cmux"):
        return False
    try:
        subprocess.run(["cmux", "rename-tab", "--surface", surf, "--", text], capture_output=True,
                       timeout=3, env={**os.environ, "CMUX_QUIET": "1"})
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def _osc_tab(text: str) -> bool:
    try:
        with open("/dev/tty", "w") as tty:
            tty.write(f"\033]0;{text}\007")
        return True
    except OSError:
        return False


def _record_init(t: dict) -> None:
    """Record a task initial on the claim so dtd leads the row with it; the
    claim file travels to the other hosts, initials.json does not."""
    if not (t.get("source") == "task" and t.get("init") and t.get("_dir")):
        return
    p = Path(t["_dir"]) / f"{t['task_id']}.json"
    rec = _json(p)
    if rec and not rec.get("init"):
        rec["init"] = t["init"]
        try:
            _claims()._write(p, json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass


def apply_all(generate: bool = False) -> list[dict]:
    """Every live claude in a tmux session on this host (cs tmux-map): compute
    its tiers and set that tmux session's @tab. One-shot catch-up for sessions
    started before the hooks did this; the hooks keep them current afterwards."""
    try:
        out = subprocess.run(["python3", str(Path(__file__).resolve().parent / "cs.py"), "tmux-map"],
                             capture_output=True, text=True, timeout=20).stdout
        rows = json.loads(out or "[]")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        rows = []
    done = []
    for r in rows:
        sid, name = r.get("sid") or "", r.get("tmux") or ""
        if not sid or not name:
            continue
        t = tiers(sid, generate, title=r.get("title") or "")
        _record_init(t)
        text = tab_text(t)
        t["tmux"] = name
        t["applied"] = bool(text) and _tmux_tab(text, target=name)
        t.pop("_dir", None)
        done.append(t)
    return done


def apply(sid: str, generate: bool = False, target: str | None = None) -> dict:
    """`target`: a tmux session name when the caller is not inside that session
    (did-fast finishing another thread's tab)."""
    t = tiers(sid, generate)
    _record_init(t)
    text = tab_text(t)
    if text:
        t["applied"] = _tmux_tab(text, target) if target else (_tmux_tab(text) or _cmux_tab(text) or _osc_tab(text))
    return t


def main(argv: list[str]) -> int:
    gen = "--generate" in argv
    target = None
    if "--target" in argv:
        i = argv.index("--target")
        target = argv[i + 1] if i + 1 < len(argv) else None
        argv = argv[:i] + argv[i + 2:]
    if argv[:1] == ["apply-all"]:
        rows = apply_all(gen)
        for t in rows:
            print(f"{t['tmux']:<10} {'ok ' if t.get('applied') else 'no '} {tab_text(t)}")
        return 0
    if len(argv) < 2 or argv[0] not in ("tiers", "apply"):
        print(__doc__, file=sys.stderr)
        return 2
    sid = argv[1]
    out = apply(sid, gen, target) if argv[0] == "apply" else tiers(sid, gen)
    out.pop("_dir", None)
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
