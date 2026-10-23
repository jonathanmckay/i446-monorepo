#!/usr/bin/env python3
"""dtd_forms — survey forms on the phone (2026-10-22).

Terminal dtd's Enter on a survey card (0l, 1s, 1 xk88/xk87, moral audit,
1 hcme, 1 i9, 1 hcb, 1 f693, 😈 -1g, 0g) opens a prompt_toolkit form instead
of starting a timer (dtd.sh's PICKENTER `case "$_clean"`). This module gives
dtd.py the same mapping plus a field spec per form for GET /api/form, and a
runner for POST /api/form that writes through each form's OWN writer code:

  * the script's non-interactive CLI where one exists (0s/1s/-1g --from-json,
    1hcme --from-text, 1f693 --from-json), or
  * the script imported as a module and its writer functions called in the
    order its interactive main() calls them (1 i9, 1 hcb, xk887 -- whose
    --from-json path skips the card-closing step its paginated form does).

Field labels come from each script's own FIELDS/SHEETS/ROWS literals, read
with ast (never imported into the Flask process), so a relabelled question
shows up on the phone without touching this file.

The runner always runs in a SUBPROCESS (`python3 dtd_forms.py run <form>
<values.json> <result.json>`): the form modules start writer threads, ssh to
Ix and hold Excel round-trips that must never run inside the web server.

Closing the card mirrors terminal dtd exactly: 1s is run --no-mark there
("alt-enter stays the completion gesture", b84df4fe) and moralaudit never
closes it, so on the phone those two cards still need a swipe-done; 0s closes
0l only when "points checked" is 1, as in the terminal form.
"""
from __future__ import annotations

import ast
import datetime as _dt
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
LIB = TOOLS.parent / "lib"
SCRIPTS = {
    "0s": TOOLS / "0s/0s.py",
    "1s": TOOLS / "1s/1s-survey.py",
    "xk887": TOOLS / "xk887/xk887-survey.py",
    "moralaudit": TOOLS / "moralaudit/moralaudit.py",
    "1hcme": TOOLS / "1hcme/1hcme.py",
    "1i9": TOOLS / "1i9/1i9.py",
    "1hcb": TOOLS / "1hcb/1hcb.py",
    "1f693": TOOLS / "f693/1f693.py",
    "-1g": TOOLS / "-1g/-1g-form.py",
    "0g": TOOLS / "-1g/-1g-form.py",
}

# dtd.sh's PICKENTER case, card (cleaned) -> form.
CARD_FORMS = {
    "0l": "0s",
    "1s": "1s",
    "1 xk88": "xk887", "1 xk87": "xk887", "1 xk87 wknd": "xk887",
    "moral audit": "moralaudit", "moralaudit": "moralaudit",
    "1 hcme": "1hcme",
    "1 i9": "1i9",
    "1 hcb": "1hcb",
    "1 f693": "1f693",
    "-1g": "-1g",
    "0g": "0g",
}


def card_key(content: str) -> str:
    """dtd.sh's jq: 😈 out, (N) [N] [x/m] {N} out, squeeze, trim, lowercase,
    then a dated copy's " M.D" suffix out ("1 xk88 10.12" -> "1 xk88")."""
    c = (content or "").replace("😈", "")
    for pat in (r" *\([0-9]*\)", r" *\[[0-9]*\]", r" *\[[0-9.+]*/m\]", r" *\{[0-9]*\}"):
        c = re.sub(pat, "", c)
    c = re.sub(r"  +", " ", c).strip().lower()
    return re.sub(r" [0-9]{1,2}\.[0-9]{1,2}$", "", c)


def form_for(content: str) -> str:
    return CARD_FORMS.get(card_key(content), "")


# ---------------------------------------------------------------------------
# Field specs (ast literals from the scripts themselves)
# ---------------------------------------------------------------------------
def _literals(path: Path, *names: str) -> dict:
    """Module-level NAME = <literal> assignments, evaluated without importing."""
    out = {}
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id in names:
            try:
                out[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                pass
    return out


def _f(key, label, type_="text", **kw) -> dict:
    d = {"key": key, "label": label, "type": type_}
    d.update({k: v for k, v in kw.items() if v is not None})
    return d


_KIND = {"text": "text", "textml": "multiline", "num": "int", "num_auto": "int"}


def _mdy(d: _dt.date) -> str:
    return "%d/%d/%s" % (d.month, d.day, d.strftime("%y"))


def spec(form: str, today: _dt.date | None = None) -> dict | None:
    """{"form", "title", "fields": [...]} for GET /api/form, or None."""
    today = today or _dt.date.today()
    path = SCRIPTS.get(form)
    if not path or not path.exists():
        return None
    if form == "0s":
        lit = _literals(path, "FIELDS")
        fields = []
        for key, label, _col, _tgt, kind in lit["FIELDS"]:
            if key == "points_checked":
                fields.append(_f(key, label, "choice", options=["1", "0"],
                                 help="1 marks 0l done (did-fast), as in the terminal form"))
            else:
                fields.append(_f(key, label, _KIND.get(kind, "text"),
                                 help="number (decimals ok)" if kind == "num" else None))
        return {"form": form, "title": "0s · reviewing %s" % _mdy(today - _dt.timedelta(days=1)),
                "fields": fields}
    if form == "1s":
        lit = _literals(path, "FIELDS")
        fields = [_f(key, label, _KIND.get(kind, "text"),
                     help="day digits only (1-7 = Su..Sa, e.g. 2,5) expand to those days' 0s answers")
                  for key, label, _col, kind, _ck in lit["FIELDS"]]
        return {"form": form, "title": "1s · weekly review (last completed week)",
                "fields": fields}
    if form == "xk887":
        lit = _literals(path, "SHEETS")
        fields = []
        for cfg in lit["SHEETS"]:
            who = cfg["title"].split("·")[-1].strip()
            for key, label, _col, kind in cfg["fields"]:
                fields.append(_f("%s_%s" % (cfg["sheet"], key), "%s · %s" % (who, label),
                                 _KIND.get(kind, "text"),
                                 help="blank on a new week = last week's + 1" if kind == "num_auto" else None))
        return {"form": form, "title": "xk887 · family review (last completed week)",
                "fields": fields}
    if form == "moralaudit":
        lit = _literals(path, "ROWS", "COLS")
        fields = []
        for rkey, rlabel, _sec in lit["ROWS"]:
            for ckey, clabel in lit["COLS"]:
                fields.append(_f(f"{rkey}__{ckey}__t", f"{rlabel} · {clabel}", "multiline"))
                fields.append(_f(f"{rkey}__{ckey}__r", f"{rlabel} · {clabel} rating", "choice",
                                 options=["1", "2", "3", "4", "5", "6", "7", "n/a"]))
        return {"form": form, "title": "moral audit · %s" % today.isoformat(), "fields": fields}
    if form == "1hcme":
        q = _literals(path, "QUESTION").get("QUESTION", "What did I learn this week?")
        return {"form": form, "title": "1 hcme · emotional check-in",
                "fields": [_f("answer", q, "multiline", help="one bullet per line")]}
    if form == "1i9":
        ratings = _literals(path, "RATINGS").get("RATINGS", {})
        return {"form": form, "title": "1 i9 · weekly review (last completed week)", "fields": [
            _f("rating", "Rating vs. expectations?", "choice", options=list(ratings),
               help=" · ".join(f"{k} = {v}" for k, v in ratings.items())),
            _f("done", "What did I get done last week?", "multiline", help="one item per line"),
            _f("exceed", "What does exceeding expectations this week look like?", "multiline",
               help="one item per line"),
        ]}
    if form == "1hcb":
        return {"form": form, "title": "1 hcb · health/body week", "fields": [
            _f("weight", "What was your lowest weight this week?", "text", help="pounds, e.g. 191.4"),
            _f("learned", "What did you learn from this week?", "multiline", help="one item per line"),
            _f("goals", "Goals for this week?", "multiline", help="one item per line"),
        ]}
    if form == "1f693":
        lit = _literals(path, "SLOTS", "DEFAULT_MIN", "DEFAULT_VAL")
        fields = []
        for i in range(1, lit.get("SLOTS", 6) + 1):
            fields.append(_f(f"who{i}", f"{i} · who (initials)", "text"))
            fields.append(_f(f"what{i}", f"{i} · what I need to do so they succeed", "text",
                             help="(N) min / [N] value optional; default (%s) [%s]" % (
                                 lit.get("DEFAULT_MIN", 15), lit.get("DEFAULT_VAL", 10))))
        return {"form": form, "title": "1 f693 · stoplight todos", "fields": fields}
    if form in ("-1g", "0g"):
        names = _literals(path, "FIELDS").get("FIELDS", ["goal 1", "goal 2", "goal 3"])
        hint = ("@code domain · {N} pts (default {15}) · (N) min · [N] becomes {N}" if form == "0g"
                else "@code domain · {N} bonus (default {10}) · (N) min · [N]-only = plain todo")
        return {"form": form, "title": "0g · daily goals" if form == "0g" else "-1g · block goals",
                "fields": [_f(f"goal{i + 1}", n, "text", help=hint) for i, n in enumerate(names)]}
    return None


def validate(form: str, values: dict) -> str | None:
    """None when `values` may be submitted, else the reason (HTTP 400)."""
    sp = spec(form)
    if sp is None:
        return "unknown form"
    v = {k: str(values.get(k) or "").strip() for k in (f["key"] for f in sp["fields"])}
    for f in sp["fields"]:
        val = v[f["key"]]
        if not val:
            continue
        if f["type"] == "int":
            try:
                float(val)
            except ValueError:
                return f"{f['label']}: not a number"
        if f["type"] == "choice" and val not in f["options"]:
            return f"{f['label']}: one of {', '.join(f['options'])}"
    if not any(v.values()):
        return "nothing filled"
    if form == "1i9":
        if not v["rating"]:
            return "rating required"
        if not v["done"]:
            return "'got done' answer required"
    if form == "1hcb":
        try:
            w = float(v["weight"])
        except ValueError:
            return "weight must be a number in pounds"
        if not 80 <= w <= 400:
            return "weight must be a number in pounds"
    if form == "1hcme" and not v["answer"]:
        return "answer required"
    return None


# ---------------------------------------------------------------------------
# Runner (subprocess only)
# ---------------------------------------------------------------------------
def _load(form: str):
    spec_ = importlib.util.spec_from_file_location("dtd_form_" + form.strip("-"), SCRIPTS[form])
    mod = importlib.util.module_from_spec(spec_)
    sys.modules[spec_.name] = mod
    spec_.loader.exec_module(mod)
    return mod


def _cli(args: list, timeout: int = 600) -> tuple[bool, str]:
    r = subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True,
                       timeout=timeout)
    lines = [l for l in ((r.stdout or "") + "\n" + (r.stderr or "")).splitlines() if l.strip()]
    tail = re.sub(r"\033\[[0-9;]*m", "", lines[-1]) if lines else ""
    return r.returncode == 0, tail or ("ok" if r.returncode == 0 else f"exit {r.returncode}")


def run_form(form: str, values: dict, tmpdir: Path) -> dict:
    v = {k: str(x if x is not None else "").strip() for k, x in values.items()}
    path = SCRIPTS[form]
    tmpdir.mkdir(parents=True, exist_ok=True)
    jf = tmpdir / f"{form.strip('-')}-values.json"
    if form == "0s":
        jf.write_text(json.dumps(v, ensure_ascii=False))
        ok, msg = _cli([path, "--from-json", jf])
    elif form == "1s":
        jf.write_text(json.dumps(v, ensure_ascii=False))
        ok, msg = _cli([path, "--from-json", jf, "--no-mark"])
    elif form == "1hcme":
        ok, msg = _cli([path, "--from-text", v.get("answer", "")])
    elif form == "1f693":
        n = sum(1 for k in v if k.startswith("who"))
        slots = [{"initials": v.get(f"who{i}", ""), "task": v.get(f"what{i}", "")}
                 for i in range(1, n + 1)]
        jf.write_text(json.dumps(slots, ensure_ascii=False))
        # --no-browser/--no-launch: the drawio tab, Obsidian and the cc
        # stoplight tab are desk-side steps; the screenshot is pasted there.
        ok, msg = _cli([path, "--from-json", jf, "--no-browser", "--no-launch"])
    elif form in ("-1g", "0g"):
        jf.write_text(json.dumps({"goals": [v.get(f"goal{i}", "") for i in (1, 2, 3)]},
                                 ensure_ascii=False))
        ok, msg = _cli([path, "--from-json", jf] + (["--daily"] if form == "0g" else []))
    elif form == "xk887":
        # run_paginated()'s finish: every page queued, then the week's family
        # cards closed (all pages submitted), all on the one writer thread.
        m = _load(form)
        sunday, _sat = m.week_range(None)
        answers = {k: x for k, x in v.items()}
        for cfg in m.SHEETS:
            m.queue_write(answers, sunday, cfg)
        marked = m.queue_finish_marks()
        ok = m.drain_writes()
        msg = "xk887 → week %s %s%s" % (m.week_row_label(sunday), "saved" if ok else "FAILED (answers in ~/.cache/xk887-recovery)",
                                       "; closed " + ", ".join(marked) if marked else "")
    elif form == "moralaudit":
        m = _load(form)
        target = _dt.date.today()
        try:
            p = m.write_note(v, target)
            ok, msg = True, f"moralaudit → wrote {p}"
        except Exception as e:  # noqa: BLE001
            rec = m._dump_recovery(v, target)
            ok, msg = False, f"moralaudit write failed ({e}); answers saved to {rec}"
    elif form == "1i9":
        m = _load(form)
        start, end, row, label = m.resolve_week(None, _dt.date.today())
        minutes = m.i9_minutes(start, end)
        sheet_label = m.check_label(row, label)
        if label and sheet_label != label:
            return {"ok": False, "error": f"sheet row {row} is labelled {sheet_label!r}, expected {label!r}; nothing written"}
        m.write_row(row, v["rating"].upper(), v["done"], v.get("exceed", ""), minutes)
        ok, msg = True, f"1 i9 {sheet_label}: {minutes}m · " + m.mark_done()
    elif form == "1hcb":
        m = _load(form)
        thu = m.week_thursday(_dt.date.today())
        weight = m.parse_weight(v.get("weight", ""))
        if weight is None:
            return {"ok": False, "error": "weight must be a number in pounds"}
        m.write_week(thu, weight, m.bullets(v.get("learned", "")), m.bullets(v.get("goals", "")))
        ok, msg = True, f"1 hcb {m.md(thu)} · " + m.mark_done()
    else:
        return {"ok": False, "error": "unknown form"}
    return {"ok": bool(ok), "message": msg} if ok else {"ok": False, "error": msg}


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 4 or argv[0] != "run" or argv[1] not in SCRIPTS:
        print("usage: dtd_forms.py run <form> <values.json> <result.json>", file=sys.stderr)
        return 2
    form, values_path, result_path = argv[1], Path(argv[2]), Path(argv[3])
    try:
        res = run_form(form, json.loads(values_path.read_text()), values_path.parent)
    except Exception as e:  # noqa: BLE001
        res = {"ok": False, "error": f"{form}: {e}"}
    result_path.write_text(json.dumps(res, ensure_ascii=False))
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
