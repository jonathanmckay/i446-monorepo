#!/usr/bin/env python3
"""xk887 — weekly family/marriage review.

A full-screen PAGINATED form (prompt_toolkit), same interaction grammar as
0s.py / 1s-survey.py, covering FOUR sheets of xk887.xlsx — one page per
sheet/person: xk88 (marriage/social), xk20 (Theo), xk22 (Ren), xk26 (Rori).
Each page is written to Excel the moment it's submitted (last-field Enter/Tab
or ^S), so later pages can't lose earlier ones. S-Tab from a page's first
field goes back; blanks never clobber, so re-submitting a page is safe.

Rows are keyed by col A's M.W label (Sunday-anchored: M = sunday.month,
W = which Sunday of M), the same convention as Neon's 1分+1s tab. UNLIKE
1分+1s, these sheets do NOT pre-populate future weeks — col A is a formula
chain (`=prev+0.1`, rolling to `=prev+1` at month end) that hardens into a
literal once a row is filled, and the row after the last filled one is
genuinely empty. So a week that doesn't exist yet is APPENDED (new row,
label written as a literal), not just looked up.

xk26's col B (age in weeks) has the same harden-to-literal pattern. If left
blank on a newly appended row, it auto-continues from the previous row's
value + 1 (matching the sheet's own established convention); on an existing
row it's left alone like any other blank.

Usage:
  python3 xk887-survey.py                 # review the last completed Sun-Sat week
  python3 xk887-survey.py 2026-07-15      # review the week containing that date
  python3 xk887-survey.py --from-json F   # non-interactive write from JSON
  python3 xk887-survey.py --print-script --from-json F   # print AppleScript only
"""
from __future__ import annotations

import argparse
import calendar
import datetime as _dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path.home() / "i446-monorepo/lib"))
from review_form_writer import BackgroundWriter, run_bounded  # noqa: E402
import dtd_hide  # noqa: E402  optimistic dtd row-hide for the cards --detach closes
import form_detach  # noqa: E402  dtd status line + detached child (--detach)

IX_OSA = Path.home() / ".claude/skills/_lib/ix-osa.sh"
WORKBOOK = "xk887.xlsx"
# ix's OneDrive mount uses the Library/CloudStorage naming convention, not a
# plain ~/OneDrive symlink (confirmed via mdfind on ix, 2026-08-04) — same
# pattern as tools/2s/2s-fast.py's SCORECARD_PATH.
WORKBOOK_PATH = Path.home() / "Library/CloudStorage/OneDrive-Personal/vault-excel" / WORKBOOK
# Recovery dump for answers that fail to write to Excel — a stable cache dir,
# not /tmp (macOS periodic cleanup can reap /tmp; hand-typed answers with no
# other durability shouldn't live there). See write_answers_safely().
RECOVERY_DIR = Path.home() / ".cache/xk887-recovery"
# --detach: the saver child's stdout/stderr (its progress goes to dtd's status line).
LOG = Path.home() / ".cache/xk887-survey.log"

# Each sheet: (sheet name, display title, fields).
# Fields: (key, label, column, kind).  kind: text | textml | num | num_auto
# num_auto = numeric; if blank AND the row is newly appended, auto-continue
# from the previous row's same column + 1 (xk26's age-in-weeks only).
SHEETS = [
    {
        "sheet": "xk88",
        "title": "xk88 · Marriage / Social",
        "fields": [
            ("good",         "Good",                                  "B", "textml"),
            ("regrettable",  "Regrettable",                           "C", "textml"),
            ("focus",        "Focus",                                 "D", "textml"),
            ("notes",        "Notes",                                 "E", "textml"),
            ("upcoming",     "Upcoming",                               "G", "textml"),
            ("did_notes",    "Did / Notes",                           "H", "textml"),
            ("kind",         "What did I do that was kind",           "J", "textml"),
            ("husband",      "Best husband this week?",               "K", "textml"),
        ],
    },
    {
        "sheet": "xk20",
        "title": "xk20 · Theo",
        "fields": [
            ("is",           "He is",                                 "B", "textml"),
            ("new",          "New / Curriculum",                      "C", "textml"),
            ("concerned",    "Concerned/Opp",                         "D", "textml"),
            ("well",         "Well",                                  "E", "textml"),
            ("better",       "Better",                                "F", "textml"),
            ("notes",        "Notes",                                 "G", "textml"),
            ("goals",        "Goals",                                 "H", "textml"),
            ("chores",       "Chores (unprompted / reminded / skipped)", "I", "text"),
        ],
    },
    {
        "sheet": "xk22",
        "title": "xk22 · Ren",
        "fields": [
            ("is",           "She is",                                "B", "textml"),
            ("new",          "New",                                   "C", "textml"),
            ("concerned",    "Concerned",                             "D", "textml"),
            ("well",         "Well",                                  "E", "textml"),
            ("better",       "Better",                                "F", "textml"),
            ("notes",        "Notes",                                 "G", "textml"),
            ("goals",        "Goals",                                 "H", "textml"),
            ("chores",       "Chores (unprompted / reminded / skipped)", "I", "text"),
        ],
    },
    {
        "sheet": "xk26",
        "title": "xk26 · Rori",
        "fields": [
            ("age",          "Age (weeks)",                           "B", "num_auto"),
            ("new",          "New",                                   "C", "textml"),
            ("concerned",    "Concerned",                             "D", "textml"),
            ("well",         "Well",                                  "E", "textml"),
            ("better",       "Better",                                "F", "textml"),
            ("notes",        "Notes",                                 "G", "textml"),
            ("goals",        "Goals",                                 "H", "textml"),
        ],
    },
]


_WEEK_LABEL_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})$")


def sunday_for_week_label(label: str, year: int | None = None) -> _dt.date:
    """Reverse of week_row_label: 'M.W' -> that week's Sunday. The label has
    no year component (same as everywhere else this convention is used --
    1分+1s, 0n's 1n+ sheet), so this assumes the current year unless one is
    given. Raises ValueError if W doesn't exist in that month (e.g. '2.6')."""
    m = _WEEK_LABEL_RE.match(label)
    if not m:
        raise ValueError("not a week label: %r" % label)
    month, week = int(m.group(1)), int(m.group(2))
    year = year or _dt.date.today().year
    days_in_month = calendar.monthrange(year, month)[1]
    for day in range(1, days_in_month + 1):
        d = _dt.date(year, month, day)
        if d.weekday() == 6 and (day - 1) // 7 + 1 == week:  # Sunday
            return d
    raise ValueError("no week %s in %d-%02d" % (label, year, month))


def week_range(arg: str | None, today: _dt.date | None = None) -> tuple[_dt.date, _dt.date]:
    """Sun-Sat range of the review week. No arg -> the most recent COMPLETED
    week; an 'M.W' arg (e.g. '7.4') -> that week directly, the same label
    col A is keyed by; any other arg -> an ISO date, the week containing it.
    (Same convention as 1s.)"""
    today = today or _dt.date.today()
    if arg and _WEEK_LABEL_RE.match(arg):
        sunday = sunday_for_week_label(arg)
    elif arg:
        d = _dt.date.fromisoformat(arg)
        sunday = d - _dt.timedelta(days=(d.weekday() + 1) % 7)
    else:
        this_sunday = today - _dt.timedelta(days=(today.weekday() + 1) % 7)
        sunday = this_sunday - _dt.timedelta(days=7)
    return sunday, sunday + _dt.timedelta(days=6)


def week_row_label(sunday: _dt.date) -> str:
    """Col A's M.W label for the week starting at `sunday`, e.g. 2026-07-12 -> '7.2'."""
    return "%d.%d" % (sunday.month, (sunday.day - 1) // 7 + 1)


def _is_num(s: str) -> bool:
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


def _as_applescript(s: str) -> str:
    parts = s.split("\n")
    esc = [p.replace("\\", "\\\\").replace('"', '\\"') for p in parts]
    return " & linefeed & ".join('"%s"' % e for e in esc) if esc else '""'


def field_key(sheet: str, key: str) -> str:
    return "%s_%s" % (sheet, key)


# ---------------------------------------------------------------------------
# Write to xk887.xlsx
# ---------------------------------------------------------------------------

def build_applescript(answers: dict, sunday: _dt.date, sheets=None) -> str:
    """One tell block, one sheet loop per config: the review week's row is
    only ever the sheet's current tail row (col A's M.W label there, read as
    string value NOT value -- the label column is a formula chain with
    float-precision display artifacts, same lesson as 0s.py's date
    matching). The M.W label has no year component, so it is NOT safe to
    scan the whole column for a string match -- that can re-hit an old
    year's row with the same label well before the real tail. If the tail's
    label isn't this week's, append a new row after it. Only non-empty
    answers are written, so blanks never clobber existing cells -- except
    xk26's age, which auto-continues from the previous row on a newly
    appended row only. `sheets` restricts the write to a subset of SHEETS
    (the paginated form writes one sheet per page)."""
    label = week_row_label(sunday)
    L = [
        'tell application "Microsoft Excel"',
        # xk887.xlsx isn't part of any always-open daily-driver Excel
        # session (unlike Neon分v12.2.xlsx) -- it drifts closed between
        # infrequent /xk887 runs. Referencing `workbook "%s"` while it's
        # closed throws, uncaught, all the way up through main() -- confirmed
        # live 2026-08-04: the workbook was closed, the write for the first
        # page of a session crashed the whole process (and reaped its cmux
        # pane) right after the user submitted it, silently losing what
        # they'd typed. `wbNames does not contain` is a plain query, not an
        # error-handler around the reference -- it can't misfire on some
        # OTHER AppleScript error (hung Excel, wrong name) the way a bare
        # try/on-error around the reference would. Same pattern as
        # tools/2s/2s-fast.py's write_scorecard().
        '  set wbNames to (name of every workbook)' ,
        '  if wbNames does not contain "%s" then open POSIX file "%s"' % (WORKBOOK, WORKBOOK_PATH),
        '  set wb to workbook "%s"' % WORKBOOK,
        '  set totalWrote to 0',
        '  set report to ""',
    ]
    for cfg in (sheets if sheets is not None else SHEETS):
        sheet = cfg["sheet"]
        v = sheet  # AppleScript variable prefix; sheet names are valid identifiers
        L += [
            '  set ws to worksheet "%s" of wb' % sheet,
            '  set lastRow_%s to 1' % v,
            # ONE range read, then an in-memory scan (2026-10-17): the per-row
            # `string value of range ("A" & r)` loop was 1,000 Apple events per
            # sheet; the same loop in 0s measured 154 s on ix against 4.6 s for
            # the single read, which is why these writes blew their 60 s budget.
            '  set avals_%s to string value of range "A2:A1000" of ws' % v,
            '  repeat with i from 1 to count of avals_%s' % v,
            '    set av to item i of avals_%s' % v,
            '    if class of av is list then set av to item 1 of av',
            '    if av is not "" then set lastRow_%s to i + 1' % v,
            '  end repeat',
            '  set tailLabel_%s to ""' % v,
            '  try',
            '    set tailLabel_%s to (string value of range ("A" & lastRow_%s) of ws)' % (v, v),
            '  end try',
            # Only the tail row can legitimately be "this week" -- the M.W
            # label has no year component, so scanning the whole sheet for
            # a string match risks re-hitting an old year's row with the
            # same label (e.g. a prior year's "7.3") well before the real
            # tail, silently overwriting stale history instead of appending.
            '  set isNew_%s to (tailLabel_%s is not "%s")' % (v, v, label),
            '  if isNew_%s then' % v,
            '    set weekRow_%s to lastRow_%s + 1' % (v, v),
            '    set value of range ("A" & weekRow_%s) of ws to %s' % (v, label),
            '  else',
            '    set weekRow_%s to lastRow_%s' % (v, v),
            '  end if',
        ]
        for key, _label, col, kind in cfg["fields"]:
            val = (answers.get(field_key(sheet, key)) or "").strip()
            if kind == "num_auto" and not val:
                L += [
                    '  if isNew_%s then' % v,
                    '    try',
                    '      set prevVal_%s to (value of range ("%s" & lastRow_%s) of ws)' % (v, col, v),
                    '      set value of range ("%s" & weekRow_%s) of ws to (prevVal_%s + 1)' % (col, v, v),
                    '      set totalWrote to totalWrote + 1',
                    '    end try',
                    '  end if',
                ]
                continue
            if not val:
                continue
            if kind in ("num", "num_auto"):
                if not _is_num(val):
                    # Never put text into a numeric column (xk26 age feeds
                    # formulas): drop it and say so instead of writing it.
                    print("  ⚠ %s.%s: %r is not a number, not written" % (sheet, key, val),
                          file=sys.stderr)
                    continue
                L.append('  set value of range ("%s" & weekRow_%s) of ws to %s' % (col, v, float(val)))
            else:
                L.append('  set value of range ("%s" & weekRow_%s) of ws to %s' % (col, v, _as_applescript(val)))
            L.append('  set totalWrote to totalWrote + 1')
        L.append('  set report to report & "%s:" & weekRow_%s & " "' % (sheet, v))
    L += [
        '  save wb',
        '  return "OK: wrote=" & totalWrote & " " & report',
        'end tell',
    ]
    return "\n".join(L)


def write_answers(answers: dict, sunday: _dt.date, sheets=None) -> str:
    script = build_applescript(answers, sunday, sheets=sheets)
    proc = run_bounded([str(IX_OSA)], input=script, timeout=60)
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 or out.startswith("ERROR"):
        raise RuntimeError(out or proc.stderr.strip() or "ix-osa failed")
    return out


def dump_recovery(answers: dict, sunday: _dt.date, sheet: str | None = None) -> Path:
    """Persist hand-typed answers that failed to reach Excel -- the only
    copy of that data anywhere once the form's own process is gone. Named
    by week + sheet + timestamp so repeated failures never clobber each
    other. Replay via `--from-json <path>`."""
    RECOVERY_DIR.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = "-%s" % sheet if sheet else ""
    path = RECOVERY_DIR / ("%s%s-%s.json" % (week_row_label(sunday), suffix, ts))
    path.write_text(json.dumps(answers, ensure_ascii=False, indent=2))
    return path


# ---------------------------------------------------------------------------
# Background writer (2026-08-11): each page's write_answers() call is a
# blocking ix-osa round trip (Excel + AppleScript on ix, sometimes 15s+ when
# the daemon is under load) that used to run between run_page() calls — the
# user stared at "writing N fields..." before the NEXT page's form could even
# render ("the save function takes way too long, should be non-blocking
# while I go on to the next field"). Writes now queue onto BackgroundWriter
# (lib/review_form_writer.py, shared with 0s.py/1s-survey.py) and
# run_paginated() moves on to the next page immediately; the shared writer
# processes queued calls ONE AT A TIME (never two AppleScript writes to the
# same open workbook concurrently) and results are reported only after the
# whole interactive flow ends, when the terminal is no longer owned by a
# prompt_toolkit full-screen Application (a background thread printing while
# one is active would corrupt the display).
# ---------------------------------------------------------------------------
_writer = BackgroundWriter(recovery_dir=RECOVERY_DIR)


def queue_write(answers: dict, sunday: _dt.date, cfg: dict) -> None:
    """Non-blocking: snapshot `answers` (later pages mutate the live dict)
    and hand it to the shared background worker. Re-reads RECOVERY_DIR each
    call (rather than trusting whatever _writer was constructed with) so
    tests that monkeypatch it after module load still take effect."""
    _writer.recovery_dir = RECOVERY_DIR
    _writer.queue(_write_page, answers, sunday, cfg,
                  tag=cfg["sheet"], recovery_payload=answers)


# Sheets whose most recent in-process write landed. A page card (PAGE_CARDS)
# is closed only behind a CONFIRMED write of its page: the writer thread runs
# the next job even after a failed one, so queue order alone proves nothing.
_written_ok: set[str] = set()


def _write_page(answers: dict, sunday: _dt.date, cfg: dict) -> str:
    _written_ok.discard(cfg["sheet"])
    result = write_answers(answers, sunday, sheets=[cfg])
    _written_ok.add(cfg["sheet"])
    return result


def filled_count(answers: dict, cfg: dict) -> int:
    return sum(1 for key, _l, _c, _k in cfg["fields"]
               if (answers.get(field_key(cfg["sheet"], key)) or "").strip())


# ---------------------------------------------------------------------------
# Finishing the survey closes the week's family cards (user request
# 2026-09-29): "if I finish the /xk887 survey, call dtd to finish 1 xk88 and
# 1 xk87 if they are on the list". "On the list" = the card is in dtd's own
# task cache (1neon tier) and due today or earlier — a card already advanced
# to next week (or the separate "1 xk87 wknd") is left alone. Each close is
# the same run.py path dtd's done action uses, queued onto the same
# background writer as the Neon writes so it can't interleave with them.
# ---------------------------------------------------------------------------

TASK_QUEUE = Path.home() / ".local/state/jm/task-queue.json"
RUN_PY = Path.home() / "i446-monorepo/tools/did/run.py"
DID_FAST = Path.home() / "i446-monorepo/tools/did/did-fast.py"
FINISH_CARDS = ("1 xk88", "1 xk87")
# Saving a PAGE closes its own card at once (user request 2026-11-24: "when I
# save the xk88 survey, mark that task as done"): the xk88 page is the whole
# of `1 xk88`, so it need not wait for the three kid pages. `1 xk87` still
# closes only when every page was submitted. Guards: the page must have at
# least one answer (Esc / ^→ past a blank page closes nothing) and its Excel
# write must have landed. --no-mark still closes nothing.
PAGE_CARDS = {"xk88": ("1 xk88",)}
# card -> confirmed closed by THIS process (inline path). A card queued twice
# (page save, then survey finish, or a re-submitted page) runs run.py once.
_closed: dict[str, bool] = {}
_ANNOT_RE = re.compile(r"\s*[\[\(\{][^\]\)\}]*[\]\)\}]")


def _card_name(content: str) -> str:
    return _ANNOT_RE.sub("", content or "").strip().lower()


def finish_card_tasks(queue: dict, today: _dt.date) -> list[tuple[str, str]]:
    """(card name, Todoist id) for every FINISH_CARDS task that is open in
    the 1neon tier of dtd's task cache and due on or before `today` (a
    missing due counts as due). Exact card name after stripping
    (N)/[N]/{N}, so "1 xk87 wknd" never matches "1 xk87". A closed weekly
    card stays in the cache with a LATER due, which is why due is checked
    and mere presence is not. FINISH_CARDS order."""
    found: list[tuple[str, str]] = []
    for t in queue.get("1neon", []) or []:
        name = _card_name(t.get("content", ""))
        if name not in FINISH_CARDS:
            continue
        due = (t.get("due") or "")[:10]
        if due:
            try:
                if _dt.date.fromisoformat(due) > today:
                    continue
            except ValueError:
                pass
        found.append((name, str(t.get("id") or "")))
    return sorted(found, key=lambda x: FINISH_CARDS.index(x[0]))


def open_finish_cards(queue: dict, today: _dt.date) -> list[str]:
    """The FINISH_CARDS names from finish_card_tasks, deduplicated, in
    FINISH_CARDS order."""
    found = {name for name, _tid in finish_card_tasks(queue, today)}
    return [c for c in FINISH_CARDS if c in found]


def queue_finish_marks(today: _dt.date | None = None, only=None, skip=(),
                       needs: str | None = None) -> list[str]:
    """Queue one run.py close per open card (see open_finish_cards) plus a
    dtd cache refresh, on the shared writer. Returns the cards queued. A
    missing/unreadable cache means nothing to close, never an error.
    `only` / `skip` narrow the cards (a page's own card; cards the detached
    saver already owns). `needs` names a sheet whose write must have landed
    by the time the close runs."""
    try:
        queue = json.loads(TASK_QUEUE.read_text())
    except (OSError, ValueError):
        return []
    cards = [c for c in open_finish_cards(queue, today or _dt.date.today())
             if (only is None or c in only) and c not in skip]
    for card in cards:
        def _mark(card=card):
            if _closed.get(card):
                return "%s: already closed by this survey" % card
            if needs and needs not in _written_ok:
                return "%s: NOT closed, the %s write failed" % (card, needs)
            ok, line = close_card(card)
            _closed[card] = ok
            return line
        _writer.queue(_mark, tag="did-" + card)
    if cards:
        def _refresh():
            run_bounded(["/usr/bin/python3", str(DID_FAST), "--refresh-cache"], timeout=60)
            return "dtd cache refreshed"
        _writer.queue(_refresh, tag="dtd-refresh")
    return cards


def drain_writes() -> bool:
    """Block until every queued write has finished, then report results —
    called once, after the interactive multi-page flow ends (normal
    completion OR cancel), never between pages. Must run before the process
    exits: an abrupt exit while ix-osa is mid-write is the corruption risk
    queue_write's serialization elsewhere guards against. Returns True iff
    every queued write succeeded, so the caller can still exit non-zero on a
    background failure even though it was discovered after moving on."""
    return _writer.drain()


# ---------------------------------------------------------------------------
# --detach (dtd, 2026-11-17): "move saving the 1xk887 survey ... to the
# background so that the UI is more snappy, and then we can have the bottom
# notification letting me know when it is saved." Page-to-page was already
# non-blocking; the wait was the END of run_paginated, where drain_writes()
# held dtd's screen until four ix-osa round trips and two card closes were
# done. With --detach the form never writes anything itself:
#
#   form     rewrites one spool file (atomically) each time a page is
#            submitted, and once more when it finishes or is cancelled.
#   saver    ONE detached child (this script, --save-spool), started at the
#            first submitted page. It follows the spool: writes each
#            submitted page as it appears (so pages 1-3 are usually in Excel
#            before page 4 is typed), then, once the form says "finished",
#            closes the week's cards, and reports every step in dtd's status
#            line (lib/form_detach.py). It outlives the form, so a closed
#            tab mid-survey still saves the pages that were submitted.
#
# One saver = one writer, in page order, exactly like the BackgroundWriter
# thread it replaces. Any failed sheet is dumped to RECOVERY_DIR as before.
# ---------------------------------------------------------------------------

def spool_path(sunday: _dt.date) -> Path:
    return RECOVERY_DIR / ("pending-%s-%d.json" % (week_row_label(sunday), os.getpid()))


def write_spool(path: Path, data: dict) -> None:
    """Atomic: the saver polls this file and must never read half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    os.replace(tmp, path)


def read_spool(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("pages"), dict) else None


def answers_from_file(path: Path) -> tuple[dict, list | None, str | None]:
    """(--from-json) A flat answers dict writes every sheet, as always. A
    saver spool (an interrupted --detach run can leave one behind) replays
    only the pages that were actually submitted, into the week it recorded.
    Returns (answers, sheets or None for all, ISO Sunday or None)."""
    data = json.loads(path.read_text())
    if isinstance(data, dict) and isinstance(data.get("pages"), dict) \
            and isinstance(data.get("answers"), dict):
        done = data["pages"]
        return (data["answers"], [cfg for cfg in SHEETS if cfg["sheet"] in done],
                data.get("week"))
    return data, None, None


class DetachedSaver:
    """The form's side of --detach. page() and end() only touch the spool
    file; they never wait on Excel. `ok` False means the hand-off failed
    (child could not start, spool unwritable) and the caller must fall back
    to the in-process writer for whatever page() refused."""

    def __init__(self, sunday: _dt.date, mark: bool = True):
        self.sunday = sunday
        self.mark = mark
        self.path = spool_path(sunday)
        self.pages: dict[str, int] = {}
        # sheet -> [[card, Todoist id]] the saver closes once that page is
        # written (PAGE_CARDS); their dtd rows are hidden when handed over.
        self.page_close: dict[str, list[list[str]]] = {}
        self.answers: dict = {}
        self.seq = 0
        self.proc = None
        self.ok = True

    def _write(self, state: str, close: list | None = None) -> None:
        write_spool(self.path, {
            "week": self.sunday.isoformat(), "parent": os.getpid(), "state": state,
            "pages": self.pages, "close": close or [],
            "page_close": self.page_close, "answers": self.answers})

    def early_cards(self) -> list[str]:
        return [name for cards in self.page_close.values() for name, _tid in cards]

    def _page_cards(self, answers: dict, cfg: dict) -> list[list[str]]:
        """The open cards this page closes by itself, if it is being handed
        over for the first time with at least one answer."""
        sheet = cfg["sheet"]
        if not self.mark or sheet not in PAGE_CARDS or sheet in self.page_close \
                or not filled_count(answers, cfg):
            return []
        try:
            queue = json.loads(TASK_QUEUE.read_text())
        except (OSError, ValueError):
            return []
        return [[name, tid] for name, tid in finish_card_tasks(queue, _dt.date.today())
                if name in PAGE_CARDS[sheet]]

    def page(self, answers: dict, cfg: dict) -> bool:
        if not self.ok:
            return False
        prev = (dict(self.pages), self.answers, self.seq)
        early = self._page_cards(answers, cfg)
        try:
            if early:
                # Hidden BEFORE the spool write / spawn (dtd_hide's rule), so
                # the saver's unhide can never run ahead of this hide.
                self.page_close[cfg["sheet"]] = early
                for _name, tid in early:
                    if tid:
                        dtd_hide.hide_card(tid)
            self.seq += 1
            self.pages[cfg["sheet"]] = self.seq   # a re-submitted page gets a newer seq
            self.answers = dict(answers)
            self._write("open")
            if self.proc is None:
                self.proc = form_detach.spawn_detached(
                    [sys.executable, str(Path(__file__).resolve()),
                     "--save-spool", str(self.path)], LOG)
        except OSError:
            self.pages, self.answers, self.seq = prev
            self.ok = False
            if early:   # never handed over: the saver will not close these
                self.page_close.pop(cfg["sheet"], None)
                for _name, tid in early:
                    if tid:
                        dtd_hide.unhide_card(tid)
            if self.proc is None:
                try:
                    self.path.unlink()
                except OSError:
                    pass
            else:
                try:   # the saver still owns the earlier pages: let it finish them
                    self._write("cancelled")
                except OSError:
                    pass
            return False
        return True

    def end(self, state: str, mark: bool = False, today: _dt.date | None = None) -> list[str]:
        """Tell the saver the form is over. state 'finished' + mark closes
        the week's cards; their dtd rows are hidden HERE, before this
        process exits, so the list that comes back never shows them
        (lib/dtd_hide.py). Returns the card names the saver will close."""
        if self.proc is None or not self.ok:
            return []
        close: list[tuple[str, str]] = []
        if state == "finished" and mark:
            try:
                queue = json.loads(TASK_QUEUE.read_text())
            except (OSError, ValueError):
                queue = {}
            early = set(self.early_cards())   # the saver already owns these
            close = [c for c in finish_card_tasks(queue, today or _dt.date.today())
                     if c[0] not in early]
            for _name, tid in close:
                if tid:
                    dtd_hide.hide_card(tid)
        # Header BEFORE the final spool write: the saver's own messages must
        # always come after this one, never be overwritten by it.
        form_detach.hdr("⏳ xk887 week %s: saving in the background …"
                        % week_row_label(self.sunday), poke=False)
        try:
            self._write(state, close=[list(c) for c in close])
        except OSError:
            # The saver sees this process exit and saves what it has, without
            # closing cards: put their rows back.
            for _name, tid in close:
                if tid:
                    dtd_hide.unhide_card(tid)
            return []
        return [name for name, _tid in close]


def close_card(card: str) -> tuple[bool, str]:
    """Close one weekly card through run.py (dtd's own done path). True only
    on a CONFIRMED close (or a card run.py found already closed today)."""
    try:
        r = run_bounded(["/usr/bin/python3", str(RUN_PY), card], timeout=120)
    except Exception as e:  # noqa: BLE001  timeout, missing interpreter
        return False, "%s: %s" % (card, type(e).__name__)
    out = (r.stdout or "") + (r.stderr or "")
    tail = out.strip().splitlines()
    # run_0n says "already closed"; run_1n (these weekly cards) says "already
    # done this week" when the card has rolled past the week: closed either way.
    ok = r.returncode == 0 and any(
        s in out for s in ("todoist closed", "already closed", "already done this week"))
    return ok, "%s: %s" % (card, tail[-1].strip() if tail else "exit %d" % r.returncode)


def run_saver(path: Path, poll: float = 0.3, settle: float = 0.5) -> int:
    """The detached child (--save-spool). Follows the spool until the form
    has ended (or died) and every submitted page is written."""
    written: dict[str, int] = {}
    fails: dict[str, Path] = {}
    saved: list[str] = []
    results: dict[str, bool] = {}   # card -> closed; the one record of every close tried
    spool = None
    misses = 0

    def _close(name: str, tid: str, label: str, parent: int) -> None:
        form_detach.hdr("⏳ xk887 week %s: saved, closing %s …" % (label, name),
                        poke=not form_detach.pid_alive(parent))
        ok, line = close_card(name)
        print("xk887 saver → %s" % line, flush=True)
        results[name] = ok
        if not ok and tid:
            dtd_hide.unhide_card(tid)   # still open in Todoist: show the row again
    while True:
        cur = read_spool(path)
        if cur is None:
            misses += 1
            if misses > 40:   # ~12s of an unreadable spool: nothing more to follow
                break
            time.sleep(poll)
            continue
        misses, spool = 0, cur
        sunday = _dt.date.fromisoformat(spool["week"])
        label = week_row_label(sunday)
        parent_gone = not form_detach.pid_alive(spool.get("parent") or 0)
        ended = spool.get("state") != "open" or parent_gone
        pending = [cfg for cfg in SHEETS
                   if spool["pages"].get(cfg["sheet"], 0) > written.get(cfg["sheet"], 0)]
        if not pending:
            if ended:
                break
            time.sleep(poll)
            continue
        cfg = pending[0]
        sheet = cfg["sheet"]
        seq = spool["pages"][sheet]
        # fzf is inside execute() while the form is up: write the text, but
        # only ask for a repaint once the form is gone.
        form_detach.hdr("⏳ xk887 week %s: saving %s (%d of %d) …"
                        % (label, sheet, len(set(saved) | {sheet}), len(spool["pages"])),
                        poke=parent_gone)
        try:
            print("xk887 saver → %s: %s" % (sheet, write_answers(spool["answers"], sunday, sheets=[cfg])),
                  flush=True)
            fails.pop(sheet, None)
            if sheet not in saved:
                saved.append(sheet)
        except Exception as e:  # noqa: BLE001
            fails[sheet] = dump_recovery(spool["answers"], sunday, sheet)
            print("xk887 saver ✗ %s FAILED: %s (answers in %s)" % (sheet, e, fails[sheet]), flush=True)
        written[sheet] = seq
        # This page's own card (PAGE_CARDS): closed as soon as the page is in
        # Excel, whatever happens to the later pages. Never on a failed write.
        for name, tid in (spool.get("page_close") or {}).get(sheet) or []:
            if results.get(name):
                continue
            if sheet in fails:
                results[name] = False
                if tid:
                    dtd_hide.unhide_card(tid)
            else:
                _close(name, tid, label, spool.get("parent") or 0)
    if spool is None:
        print("xk887 saver ✗ spool %s unreadable, nothing saved" % path, flush=True)
        form_detach.hdr("✗ xk887: save FAILED, spool unreadable (%s)" % path)
        return 1
    if spool.get("state") == "finished":
        # one more try for a page card whose close failed after a good write
        for sheet, cards in (spool.get("page_close") or {}).items():
            for name, tid in cards:
                if sheet not in fails and results.get(name) is False:
                    _close(name, tid, label, spool.get("parent") or 0)
        for name, tid in spool.get("close") or []:
            if not results.get(name):
                _close(name, tid, label, spool.get("parent") or 0)
    if results:
        try:
            run_bounded(["/usr/bin/python3", str(DID_FAST), "--refresh-cache"], timeout=60)
        except Exception:  # noqa: BLE001
            pass
    closed = [n for n, ok in results.items() if ok]
    not_closed = [n for n, ok in results.items() if not ok]
    if fails:
        msg = "✗ xk887 week %s: FAILED %s, answers in %s" % (
            label, ", ".join(fails), ", ".join(str(p) for p in fails.values()))
        if saved:
            msg += " · saved " + ", ".join(s for s in saved if s not in fails)
    else:
        msg = "✓ xk887 week %s saved (%s)" % (label, ", ".join(saved) or "nothing to write")
    if closed:
        msg += " · closed " + ", ".join(closed)
    if not_closed:
        msg += " · NOT closed: " + ", ".join(not_closed)
    print("xk887 saver → " + msg, flush=True)
    # The final line must land after dtd's wrapper has handed the screen back
    # to fzf, or the repaint request is dropped: announce() waits for the form.
    form_detach.announce(msg, parent=spool.get("parent") or 0, settle=settle)
    try:
        path.unlink()   # every failed sheet has its own recovery dump
    except OSError:
        pass
    return 1 if (fails or not_closed) else 0


# ---------------------------------------------------------------------------
# Full-screen form (prompt_toolkit) -- same interaction grammar as 0s.py/1s
# ---------------------------------------------------------------------------

def run_page(cfg: dict, sunday: _dt.date, saturday: _dt.date,
             page_no: int, total: int, initial: dict) -> tuple[str | None, dict]:
    """One full-screen page for one sheet/person. Returns (action, answers):
    action 'submit' (write this page, go forward), 'back' (previous page,
    nothing written), or None (cancelled). Fields sit on adjacent lines with
    no blank rows between them — multiline boxes start at one line and grow
    with their content instead of reserving empty height."""
    from prompt_toolkit import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import HSplit, Layout, ScrollablePane, VSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.dimension import Dimension
    from prompt_toolkit.styles import Style
    from prompt_toolkit.widgets import Frame, TextArea

    areas = {}
    ordered_keys = []
    rows = []
    for key, label, _col, kind in cfg["fields"]:
        fkey = field_key(cfg["sheet"], key)
        ml = kind == "textml"
        area = TextArea(multiline=ml,
                        height=Dimension(min=1, max=6) if ml else 1,
                        dont_extend_height=True,
                        wrap_lines=True, style="class:input", scrollbar=False,
                        text=initial.get(fkey, ""))
        areas[fkey] = (area, kind, label)
        ordered_keys.append(fkey)
        tag = " #" if kind in ("num", "num_auto") else ""
        lbl = Window(FormattedTextControl(label + tag), width=21,  # was 27; JM 2026-11-10: narrower prompt, wider answer
                    style="class:label", dont_extend_width=True, wrap_lines=True)
        rows.append(VSplit([lbl, Window(width=1, char=" "), area]))

    msg = {"text": ""}
    nav = "Enter/Tab next · last field saves page" + (" →" if page_no < total else " + finish")
    status = Window(FormattedTextControl(
        lambda: msg["text"] or nav + " · S-Tab back · ^→/^← page · ^S save page · ^Q cancel"),
        height=1, style="class:status")

    root = HSplit([Frame(ScrollablePane(HSplit(rows, padding=0)),
                         title="xk887 %d/%d · %s · week %s (%s–%s)" % (
                             page_no, total, cfg["title"], week_row_label(sunday),
                             sunday.isoformat(), saturday.isoformat())),
                   status])

    ordered = [(k, areas[k][0], areas[k][1]) for k in ordered_keys]
    last_idx = len(ordered) - 1

    def _focused_idx(app):
        cc = app.layout.current_control
        for i, (_k, a, _kind) in enumerate(ordered):
            if a.control is cc:
                return i
        return None

    def _submit(app):
        app.exit(result="submit")

    kb = KeyBindings()

    @kb.add("tab", eager=True)
    def _(e):
        if _focused_idx(e.app) == last_idx:
            _submit(e.app)
        else:
            e.app.layout.focus_next()

    @kb.add("s-tab", eager=True)
    def _(e):
        if _focused_idx(e.app) == 0 and page_no > 1:
            e.app.exit(result="back")
        else:
            e.app.layout.focus_previous()

    @kb.add("enter", eager=True)
    def _(e):
        idx = _focused_idx(e.app)
        if idx is None:
            return
        kind = ordered[idx][2]
        if kind == "textml":
            e.current_buffer.insert_text("\n")
        elif idx == last_idx:
            _submit(e.app)
        else:
            e.app.layout.focus_next()

    @kb.add("c-s")
    def _(e):
        _submit(e.app)

    # Free page navigation (2026-08-04) -- previously the only way off a page
    # was Tab/Enter at the LAST field (submit, forward) or S-Tab at the
    # FIRST field (back) -- so revisiting an earlier or later page meant
    # tabbing through every field in between. These work from any field on
    # any page. Forward still validates + writes (same as Tab/Enter at the
    # last field); back still writes nothing (blanks never clobber, so a
    # later re-submit of a revisited page is always safe).
    @kb.add("c-right")
    @kb.add("c-pagedown")
    def _(e):
        _submit(e.app)

    @kb.add("c-left")
    @kb.add("c-pageup")
    def _(e):
        if page_no > 1:
            e.app.exit(result="back")

    # Esc = save this page and leave the survey (2026-10-16: opened inline
    # from dtd's Enter, Esc must land back on the dtd list). Pages already
    # queued stay queued; the week's cards are closed only when every page
    # was submitted.
    @kb.add("escape")   # not eager: Opt+Backspace (ESC DEL) must reach backward-kill-word
    def _(e):
        e.app.exit(result="finish")

    @kb.add("c-q")
    @kb.add("c-c")
    def _(e):
        e.app.exit(result=None)

    style = Style([
        ("label", "fg:#7c7c7c"),
        ("input", "bg:#1b1b1b fg:#cfcfcf"),
        ("section", "fg:#5f875f bold"),
        ("status", "bg:#111111 fg:#00e676"),
        ("frame.border", "fg:#333333"),
    ])

    app = Application(layout=Layout(root, focused_element=areas[ordered_keys[0]][0]),
                      key_bindings=kb, full_screen=True, style=style,
                      mouse_support=True)
    app.timeoutlen = 0.05   # lone Esc fires after 50ms instead of the 1s chord wait
    action = app.run()
    return action, {k: a.text.strip() for k, (a, _kind, _lbl) in areas.items()}


def run_paginated(sunday: _dt.date, saturday: _dt.date, no_mark: bool = False,
                  detach: bool = False) -> int:
    """One page per sheet/person; each page is QUEUED to Excel the moment it
    is submitted and the NEXT page renders immediately — the write itself
    runs in the background (see queue_write/drain_writes) so a crash or
    cancel on page 3 never loses pages 1-2, and typing never blocks on the
    ix-osa round trip. S-Tab from a page's first field goes back
    (already-queued pages can be edited and re-submitted; blanks never
    clobber, so re-writes are safe). Every queued write is waited on and
    reported exactly once, at the very end (drain_writes) — never between
    pages, since a background thread printing while the next page's
    full-screen Application is running would corrupt the display.

    detach=True (dtd): pages go to a DetachedSaver instead, nothing is
    waited on, and the function returns the moment the form ends; the saver
    child reports in dtd's status line. A page the saver cannot take falls
    back to the in-process writer above."""
    answers: dict = {}
    total = len(SHEETS)
    queued = []
    saver = DetachedSaver(sunday, mark=not no_mark) if detach else None
    inline = False   # True once any page went to the in-process writer
    early: list[str] = []   # page cards queued for an in-process close
    i = 0
    while i < total:
        cfg = SHEETS[i]
        action, page = run_page(cfg, sunday, saturday, i + 1, total, answers)
        answers.update(page)
        if action is None:
            handed = bool(saver and saver.proc is not None and saver.ok)
            if saver:
                saver.end("cancelled")
            if inline or not saver:
                drain_writes()
            print("xk887 cancelled on page %d/%d%s." % (
                i + 1, total,
                "; queued before cancel: " + ", ".join(queued) if queued else ""))
            # Pages handed to the saver are being written: that is not a
            # failed form, and a non-zero exit makes dtd's wrapper hold the
            # screen and overwrite the saver's status line.
            return 0 if (handed and not inline) else 1
        if action == "back":
            i -= 1
            continue
        filled = filled_count(answers, cfg)
        print("xk887 → %s: queued %d fields …" % (cfg["sheet"], filled), flush=True)
        if not (saver and saver.page(answers, cfg)):
            queue_write(answers, sunday, cfg)
            inline = True
            # The page's own card closes behind its write (PAGE_CARDS), unless
            # the saver was already handed that card on an earlier submit.
            if filled and not no_mark and cfg["sheet"] in PAGE_CARDS:
                early += [c for c in queue_finish_marks(
                    only=PAGE_CARDS[cfg["sheet"]], needs=cfg["sheet"],
                    skip=saver.early_cards() if saver else ()) if c not in early]
        if cfg["sheet"] not in queued:
            queued.append(cfg["sheet"])
        i += 1
        if action == "finish":
            break
    # Every page submitted = survey finished → close the week's family cards
    # (queued behind the Neon writes; see queue_finish_marks). An Esc before
    # the last page saves what was filled and leaves the cards open, except a
    # saved page's own card (PAGE_CARDS: `1 xk88` once the xk88 page is in).
    if saver and saver.ok and saver.proc is not None and not inline:
        marked = saver.early_cards() + saver.end("finished", mark=not (no_mark or i < total))
        print("xk887 → week %s: saving in the background (%s)%s · log %s" % (
            week_row_label(sunday), ", ".join(queued),
            "; closing " + ", ".join(marked) if marked else "", LOG))
        return 0
    if saver:
        saver.end("cancelled")   # it keeps the pages it has; marks run below
    # A card the saver owns is never also closed here (two run.py at once
    # would advance and credit it twice); one queued above no-ops if it closed.
    marked = [] if (no_mark or i < total) else queue_finish_marks(
        skip=saver.early_cards() if saver else ())
    marked = early + [c for c in marked if c not in early]
    all_ok = drain_writes()
    print("xk887 → week %s done (%s)%s" % (
        week_row_label(sunday), ", ".join(queued),
        "; closed " + ", ".join(marked) if marked else ""))
    return 0 if all_ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("date", nargs="?",
                    help="'M.W' week label (e.g. 7.4) or any ISO date in the review week "
                         "(default: last completed week)")
    ap.add_argument("--from-json", help="write answers from a JSON file instead of the form")
    ap.add_argument("--print-script", action="store_true", help="print AppleScript, do not write")
    ap.add_argument("--no-mark", action="store_true",
                    help="do not close the open '1 xk88' / '1 xk87' cards (reruns)")
    ap.add_argument("--detach", action="store_true",
                    help="save in a detached child and exit the moment the form ends (dtd)")
    ap.add_argument("--save-spool", help=argparse.SUPPRESS)   # the --detach child
    args = ap.parse_args()

    if args.save_spool:
        return run_saver(Path(args.save_spool))

    sunday, saturday = week_range(args.date)

    if args.from_json:
        answers, only, spool_week = answers_from_file(Path(args.from_json))
        if spool_week and not args.date:
            sunday, saturday = week_range(spool_week)
        if args.print_script:
            print(build_applescript(answers, sunday, sheets=only))
            return 0
        filled = sum(1 for cfg in (only or SHEETS) for key, _l, _c, _kind in cfg["fields"]
                     if (answers.get(field_key(cfg["sheet"], key)) or "").strip())
        print("xk887 → writing %d fields to xk887.xlsx week %s …" % (filled, week_row_label(sunday)),
              flush=True)
        try:
            result = write_answers(answers, sunday, sheets=only)
        except Exception as e:  # noqa: BLE001
            print("xk887 ✗ WRITE FAILED: %s" % e, flush=True)
            print("xk887 → input JSON is already durable at %s -- retry once fixed" % args.from_json,
                  flush=True)
            return 1
        print("xk887 → %s (%d fields) · %s" % (week_row_label(sunday), filled, result))
        return 0

    # Interactive: one page per person, written as each page is submitted.
    return run_paginated(sunday, saturday, no_mark=args.no_mark, detach=args.detach)


if __name__ == "__main__":
    sys.exit(main())
