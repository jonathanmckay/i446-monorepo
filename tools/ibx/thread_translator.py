#!/usr/bin/env python3
"""thread_translator — auto-translate a group text between English and Chinese.

JM asked (2026-10-05): in the "3494" thread, whenever anyone posts in English
or Chinese, reply with the translation into the other language, marked 🤖 on
both sides so nobody mistakes it for JM writing. This is a standing approval
for exactly this: translations only, only in the threads listed in THREADS.

Runs on ix as launchd agent com.jm.thread-translator (KeepAlive, polls
chat.db every POLL_SECS). Translation uses `claude -p --model haiku`, which
authenticates through the login keychain, so it works under launchd in the
GUI session but not over ssh.

Skips: the bot's own 🤖 messages (no loops), tapbacks/attachments, messages
already in both languages (e.g. /send's bilingual output), and anything with
no letters. Starts from the newest message on first run (no backlog), and caps
sends per hour so a bug can't flood the family thread.

Usage: thread_translator.py [--once] [--dry-run]
"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path

THREADS = {
    "3494": {"glossary": {"阿珊": "Kelly"}},
}
BOT = "🤖"
POLL_SECS = 5
MAX_PER_HOUR = 30
CLAUDE = "/opt/homebrew/bin/claude"
CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"
STATE = Path.home() / ".local" / "state" / "jm" / "thread-translator.json"
LOG = Path.home() / "Library" / "Logs" / "thread-translator.log"

CJK = re.compile(r"[㐀-鿿豈-﫿]")
LATIN = re.compile(r"[A-Za-z]")


def log(msg: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# --- message text -------------------------------------------------------
def attributed_text(blob: bytes | None) -> str:
    """Plain text from chat.db's attributedBody (TypedStream NSAttributedString):
    the first '+'-marked UTF-8 string that isn't a class name."""
    if not blob:
        return ""
    i = 0
    while i < len(blob) - 2:
        if blob[i] == 0x2B:
            lb = blob[i + 1]
            if 0 < lb < 0x80:
                start, length = i + 2, lb
            elif lb == 0x81 and i + 4 <= len(blob):
                length = int.from_bytes(blob[i + 2:i + 4], "little")
                start = i + 4
            else:
                i += 1
                continue
            try:
                s = blob[start:start + length].decode("utf-8")
            except UnicodeDecodeError:
                i += 1
                continue
            if len(s) >= 1 and not s.startswith("NS") and s not in ("streamtyped",):
                return s
        i += 1
    return ""


def target_language(text: str) -> str | None:
    """'en' for Chinese text, 'zh' for English text, None to skip (bot's own,
    already bilingual, or no letters)."""
    t = text.strip()
    if not t or BOT in t:
        return None
    cjk, latin = len(CJK.findall(t)), len(LATIN.findall(t))
    if cjk == 0 and latin == 0:
        return None
    # Both languages substantially present: already bilingual (e.g. /send).
    if cjk >= 2 and latin >= 6:
        return None
    return "en" if cjk > latin else "zh"


# --- translation --------------------------------------------------------
def translate(text: str, target: str, glossary: dict[str, str]) -> str | None:
    lang = "natural English" if target == "en" else "natural Simplified Chinese"
    names = "; ".join(f"{zh} = {en}" for zh, en in glossary.items())
    prompt = (
        f"Translate this family group-chat message into {lang}, the way a native "
        "speaker would text it. Keep names, times, and numbers exact. "
        + (f"Name glossary (use the target-language form): {names}. " if names else "")
        + "Output ONLY the translation, no quotes, no notes.\n\n" + text
    )
    try:
        r = subprocess.run([CLAUDE, "-p", "--model", "haiku", prompt],
                           capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.TimeoutExpired) as e:
        log(f"translate failed: {e}")
        return None
    out = r.stdout.strip().strip('"').strip()
    if r.returncode != 0 or not out or "Not logged in" in out:
        log(f"translate failed rc={r.returncode}: {(r.stderr or out)[:200]}")
        return None
    if len(out) > 4 * len(text) + 40:
        log("translate rejected: output implausibly long")
        return None
    return out


def send(chat_guid: str, text: str) -> bool:
    safe_text = text.replace("\\", "\\\\").replace('"', '\\"')
    safe_id = chat_guid.replace("\\", "\\\\").replace('"', '\\"')
    script = (f'tell application "Messages"\n'
              f'    set theChat to (first chat whose id is "{safe_id}")\n'
              f'    send "{safe_text}" to theChat\n'
              f'end tell')
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if r.returncode != 0:
        log(f"send failed: {r.stderr.strip()[:200]}")
    return r.returncode == 0


# --- chat.db ------------------------------------------------------------
def chat_for(db: sqlite3.Connection, name: str):
    """(chat ROWID, guid) of the most recently active chat named exactly `name`."""
    return db.execute(
        "SELECT c.ROWID, c.guid FROM chat c LEFT JOIN chat_message_join j ON j.chat_id = c.ROWID "
        "LEFT JOIN message m ON m.ROWID = j.message_id WHERE c.display_name = ? "
        "GROUP BY c.ROWID ORDER BY MAX(m.date) DESC LIMIT 1", (name,)).fetchone()


def new_messages(db: sqlite3.Connection, chat_rowid: int, after: int):
    return db.execute(
        "SELECT m.ROWID, m.text, m.attributedBody, m.associated_message_type, "
        "m.cache_has_attachments FROM message m JOIN chat_message_join j "
        "ON j.message_id = m.ROWID WHERE j.chat_id = ? AND m.ROWID > ? ORDER BY m.ROWID",
        (chat_rowid, after)).fetchall()


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(STATE)


SNAP_DIR = Path("/tmp/thread-translator-db")


def open_db() -> sqlite3.Connection:
    """Read a snapshot copy, like imsg_watcher: under launchd, sqlite opening
    chat.db in place is refused ("authorization denied") even though copying
    it works. The -wal/-shm files come along so the newest messages count."""
    import shutil
    SNAP_DIR.mkdir(exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        src = CHAT_DB.with_name(CHAT_DB.name + suffix)
        dst = SNAP_DIR / (CHAT_DB.name + suffix)
        if src.exists():
            shutil.copy2(src, dst)
        elif dst.exists():
            dst.unlink()
    return sqlite3.connect(str(SNAP_DIR / CHAT_DB.name))


def poll_once(state: dict, sent_times: deque, dry_run: bool) -> None:
    db = open_db()
    try:
        for name, cfg in THREADS.items():
            row = chat_for(db, name)
            if not row:
                continue
            chat_rowid, guid = row
            key = f"{name}:{guid}"
            if key not in state:  # first sight: start from now, no backlog
                last = db.execute("SELECT COALESCE(MAX(message_id), 0) FROM chat_message_join "
                                  "WHERE chat_id = ?", (chat_rowid,)).fetchone()[0]
                state[key] = last
                save_state(state)
                log(f"watching {name!r} ({guid}) from message {last}")
                continue
            for rowid, text, body, assoc, has_att in new_messages(db, chat_rowid, state[key]):
                state[key] = rowid
                save_state(state)
                if assoc:  # tapback / reaction
                    continue
                msg = (text or attributed_text(body) or "").replace("￼", "").strip()
                tgt = target_language(msg)
                if not tgt:
                    continue
                now = time.time()
                while sent_times and now - sent_times[0] > 3600:
                    sent_times.popleft()
                if len(sent_times) >= MAX_PER_HOUR:
                    log(f"rate cap hit ({MAX_PER_HOUR}/h); skipping message {rowid}")
                    continue
                out = translate(msg, tgt, cfg.get("glossary", {}))
                if not out:
                    continue
                reply = f"{BOT} {out} {BOT}"
                if dry_run:
                    log(f"[dry-run] {name} #{rowid}: {msg!r} -> {reply!r}")
                    continue
                if send(guid, reply):
                    sent_times.append(now)
                    log(f"{name} #{rowid} -> {tgt}: {len(msg)} chars translated")
    finally:
        db.close()


def main(argv: list[str]) -> int:
    once, dry = "--once" in argv, "--dry-run" in argv
    state, sent_times = load_state(), deque()
    log(f"thread_translator start (once={once}, dry_run={dry})")
    while True:
        try:
            poll_once(state, sent_times, dry)
        except Exception as e:
            log(f"poll error: {type(e).__name__}: {e}")
        if once:
            return 0
        time.sleep(POLL_SECS)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
