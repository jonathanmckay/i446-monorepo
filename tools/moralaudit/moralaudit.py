#!/usr/bin/env python3
"""moralaudit — quarterly moral audit.

A full-screen form (prompt_toolkit) covering every (row, column) cell of the
moral audit grid. Each cell gets a free-text reflection plus a 1-7 rating (or
n/a); the rating drives that cell's fill color in the rendered output. On
submit, writes a dated note to the vault with an inline-styled HTML table
(renders natively in Obsidian) and opens it.

Rows (two groups):
  Stewardship    — resource types you control, judged as one fused
                   competence+morality question (not split — see 2026-08-13
                   design conversation: splitting them lets technical skill
                   launder a bad outcome, e.g. "ran it efficiently" standing
                   in for "should have run it at all"):
    capital        Money — deployed productively, or sitting idle?
    org            People/culture you have AUTHORITY over
    property       Physical space/property you control
    relationships  People you have NO authority over (reciprocity, not control)
    time           Your own hours — allocation across what's possible
    zi             自 — attention/energy quality (NOT time allocation itself)

  Externalities    — effects on third parties never party to the relationship,
                     orthogonal to Stewardship (you can steward well and still
                     spray externalities onto people outside it):
    ext_individual
    ext_group

Columns: i9 (Microsoft/work), m5x2 (McKay Capital), 个 (personal/self).
Not every cell need apply — n/a is a first-class answer, not a gap.

Usage:
  python3 moralaudit.py                    # interactive full-screen form
  python3 moralaudit.py --from-json F      # non-interactive: write from JSON
  python3 moralaudit.py --print-html --from-json F   # print HTML, no write
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

VAULT_DIR = Path.home() / "vault" / "hcmp" / "moral-audit"
RECOVERY_DIR = Path.home() / ".cache" / "moralaudit-recovery"

ROWS = [
    ("capital", "Capital", "stewardship"),
    ("org", "Org / People", "stewardship"),
    ("property", "Property", "stewardship"),
    ("relationships", "Relationships", "stewardship"),
    ("time", "Time", "stewardship"),
    ("zi", "自 (Self)", "stewardship"),
    ("ext_individual", "Externalities — Individual", "externalities"),
    ("ext_group", "Externalities — Group", "externalities"),
]
SECTION_LABEL = {"stewardship": "Stewardship", "externalities": "Externalities"}

COLS = [
    ("i9", "i9"),
    ("m5x2", "m5x2"),
    ("ge", "个"),
]

# Build the flat field list in row-major order: for each row, for each col,
# a text cell then its rating cell. Keys: "<row>__<col>__t" / "<row>__<col>__r"
FIELDS = []
for rkey, rlabel, _section in ROWS:
    for ckey, clabel in COLS:
        FIELDS.append((f"{rkey}__{ckey}__t", f"{rlabel} · {clabel}", "textml"))
        FIELDS.append((f"{rkey}__{ckey}__r", f"{rlabel} · {clabel} (1-7/n/a)", "rating"))


def _valid_rating(s: str) -> bool:
    s = s.strip().lower()
    if s in ("", "n/a", "na"):
        return True
    try:
        n = int(s)
    except ValueError:
        return False
    return 1 <= n <= 7


def _rating_color(s: str) -> tuple[str, str]:
    """(background, text) hex colors for a rating string. n/a/blank -> dark."""
    s = s.strip().lower()
    if s in ("", "n/a", "na"):
        return "#161616", "#5a5a5a"
    n = max(1, min(7, int(s)))
    lo = (92, 44, 44)     # 1: muted red
    mid = (58, 58, 58)    # 4: neutral gray
    hi = (56, 142, 60)    # 7: green
    if n <= 4:
        t = (n - 1) / 3.0
        a, b = lo, mid
    else:
        t = (n - 4) / 3.0
        a, b = mid, hi
    rgb = tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))
    bg = "#%02x%02x%02x" % rgb
    # Light text on dark backgrounds throughout (all these are dark-ish).
    return bg, "#e8e8e8"


def _quarter_label(d: _dt.date) -> str:
    q = (d.month - 1) // 3 + 1
    return f"{d.year}-Q{q}"


def build_html(answers: dict, target: _dt.date) -> str:
    rows_html = []
    header_cells = "".join(
        f'<th style="padding:8px 12px;text-align:left;color:#e8e8e8;'
        f'background:#111;border:1px solid #333;">{clabel}</th>'
        for _ck, clabel in COLS
    )
    rows_html.append(
        f'<tr><th style="padding:8px 12px;background:#111;border:1px solid #333;"></th>{header_cells}</tr>'
    )

    last_section = None
    for rkey, rlabel, section in ROWS:
        if section != last_section:
            span = len(COLS) + 1
            rows_html.append(
                f'<tr><td colspan="{span}" style="padding:6px 12px;background:#000;'
                f'color:#e8e8e8;font-weight:bold;border:1px solid #333;">{SECTION_LABEL[section]}</td></tr>'
            )
            last_section = section
        cells = [
            f'<td style="padding:8px 12px;font-weight:600;color:#e8e8e8;'
            f'background:#1b1b1b;border:1px solid #333;white-space:nowrap;">{rlabel}</td>'
        ]
        for ckey, _clabel in COLS:
            text = (answers.get(f"{rkey}__{ckey}__t") or "").strip()
            rating = answers.get(f"{rkey}__{ckey}__r") or ""
            bg, fg = _rating_color(rating)
            body = text.replace("\n", "<br>") if text else ""
            cells.append(
                f'<td style="padding:10px 12px;background:{bg};color:{fg};'
                f'border:1px solid #333;vertical-align:top;min-width:180px;">{body}</td>'
            )
        rows_html.append(f"<tr>{''.join(cells)}</tr>")

    table = (
        '<table style="border-collapse:collapse;width:100%;font-family:sans-serif;font-size:14px;">'
        + "".join(rows_html)
        + "</table>"
    )
    return table


def build_note(answers: dict, target: _dt.date) -> str:
    table = build_html(answers, target)
    fm = "\n".join([
        "---",
        f'title: "Moral Audit {_quarter_label(target)}"',
        f"date: {target.isoformat()}",
        "type: moral-audit",
        "tags: [hcmp]",
        "source: moralaudit",
        "---",
        "",
    ])
    return fm + table + "\n"


def target_path(target: _dt.date) -> Path:
    VAULT_DIR.mkdir(parents=True, exist_ok=True)
    return VAULT_DIR / f"{target.isoformat()}-moral-audit.md"


def write_note(answers: dict, target: _dt.date) -> Path:
    path = target_path(target)
    path.write_text(build_note(answers, target))
    return path


def _dump_recovery(answers: dict, target: _dt.date) -> Path:
    RECOVERY_DIR.mkdir(parents=True, exist_ok=True)
    p = RECOVERY_DIR / f"{_dt.datetime.now().isoformat(timespec='seconds')}.json"
    p.write_text(json.dumps({"target": target.isoformat(), "answers": answers}, ensure_ascii=False, indent=2))
    return p


def open_in_obsidian(path: Path) -> None:
    vault_root = Path.home() / "vault"
    rel = path.relative_to(vault_root)
    uri = "obsidian://open?path=" + urllib.parse.quote(str(path))
    try:
        subprocess.run(["open", uri], check=False, timeout=10)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Full-screen form (prompt_toolkit) — same skeleton as 0s.py/1s.py
# ---------------------------------------------------------------------------
def run_form(target: _dt.date) -> dict | None:
    from prompt_toolkit import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import HSplit, Layout, ScrollablePane, VSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.styles import Style
    from prompt_toolkit.widgets import Frame, TextArea

    areas = {}
    rows = []
    last_section = None
    for rkey, rlabel, section in ROWS:
        if section != last_section:
            rows.append(Window(FormattedTextControl(SECTION_LABEL[section]),
                                height=1, style="class:section"))
            last_section = section
        for ckey, clabel in COLS:
            tkey, rkey_ = f"{rkey}__{ckey}__t", f"{rkey}__{ckey}__r"
            tlabel = f"{rlabel} · {clabel}"
            t_area = TextArea(multiline=True, height=1, wrap_lines=True,
                              style="class:input", scrollbar=True)
            r_area = TextArea(multiline=False, height=1, width=4,
                              style="class:input")
            areas[tkey] = (t_area, "textml", tlabel)
            areas[rkey_] = (r_area, "rating", f"{tlabel} (1-7/n/a)")
            lbl = Window(FormattedTextControl(tlabel), width=30, style="class:label",
                        dont_extend_width=True)
            rows.append(VSplit([
                lbl, Window(width=1, char=" "),
                t_area, Window(width=1, char=" "),
                r_area,
            ], height=1))

    msg = {"text": ""}
    status = Window(FormattedTextControl(
        lambda: msg["text"] or "Enter/Tab next · Enter or Tab on last field saves · S-Tab back · ^S save · ^Q cancel"),
        height=1, style="class:status")

    body = HSplit(rows, padding=0)
    root = HSplit([Frame(ScrollablePane(body), title="moralaudit · %s" % _quarter_label(target)), status])

    ordered = [(k, areas[k][0], areas[k][1]) for k, *_ in FIELDS]
    last_idx = len(ordered) - 1

    def _focused_idx(app):
        cc = app.layout.current_control
        for i, (_k, a, _kind) in enumerate(ordered):
            if a.control is cc:
                return i
        return None

    def _submit(app):
        bad = [lbl for _k, (a, kind, lbl) in areas.items()
               if kind == "rating" and not _valid_rating(a.text)]
        if bad:
            msg["text"] = "Rating must be 1-7 or n/a: " + ", ".join(bad[:3])
            return
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

    @kb.add("c-q")
    @kb.add("c-c")
    def _(e):
        e.app.exit(result=None)

    style = Style([
        ("label", "fg:#7c7c7c"),
        ("input", "bg:#1b1b1b fg:#cfcfcf"),
        ("status", "bg:#111111 fg:#00e676"),
        ("section", "bg:#000000 fg:#e8e8e8 bold"),
        ("frame.border", "fg:#333333"),
    ])

    app = Application(layout=Layout(root, focused_element=areas[FIELDS[0][0]][0]),
                      key_bindings=kb, full_screen=True, style=style, mouse_support=True)
    if app.run() != "submit":
        return None
    return {k: a.text.strip() for k, (a, _kind, _lbl) in areas.items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("date", nargs="?", help="YYYY-MM-DD (default today)")
    ap.add_argument("--from-json", help="write answers from a JSON file instead of the form")
    ap.add_argument("--print-html", action="store_true", help="print the HTML table, do not write")
    args = ap.parse_args()

    target = _dt.date.fromisoformat(args.date) if args.date else _dt.date.today()

    if args.from_json:
        answers = json.loads(Path(args.from_json).read_text())
    else:
        answers = run_form(target)
        if answers is None:
            print("moralaudit cancelled.")
            return 1

    if args.print_html:
        print(build_html(answers, target))
        return 0

    try:
        path = write_note(answers, target)
    except Exception as e:
        rec = _dump_recovery(answers, target)
        print(f"moralaudit → write failed ({e}); answers saved to {rec}", file=sys.stderr)
        return 1

    filled = sum(1 for k, _l, kind in FIELDS if kind == "textml" and (answers.get(k) or "").strip())
    print(f"moralaudit → {filled} cells filled, wrote {path}")
    open_in_obsidian(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
