#!/usr/bin/env python3
"""label: file documents interactively, with JM's answers kept as the eval set.

The classifier is the Claude Code session itself (no API calls, no credits):

  batch <dir> [--n 12]     next N unlabeled top-level files in <dir>, each with a
                           text snippet, plus the ~/files destination catalog
                           (classify.catalog). Saved as a batch file; prints JSON.
  propose <batch> <json>   the session writes, per row, up to 3 destinations
                           {"1": {"a": "<dest key or path>", "b": ..., "suggest": "a"}}
  show <batch>             the decision table JM fills in: one letter per row
                           (a/b/c = an option, x = trash, s = skip, leave in place)
  answer <batch> "1a 2c"   moves each file (logged to the triage log, undoable),
                           appends one row per file to ~/files/_meta/labels.jsonl
  score                    agreement of the suggestion with JM's choice, overall,
                           by area and by batch

labels.jsonl is the eval sheet: it lives in ~/files (Syncthing to Ix, never the
GitHub-pushed vault) because it names tax and family documents. classify.py's
eval can score a model against it.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("classify", HERE / "classify.py")
classify = importlib.util.module_from_spec(_spec); sys.modules["classify"] = classify
_spec.loader.exec_module(classify)

STATE = classify.STATE
FILES = Path.home() / "files"
LABELS = FILES / "_meta" / "labels.jsonl"
TRASH = Path.home() / ".Trash"
ANSWER_RE = re.compile(r"(\d+)\s*([a-z])", re.I)


def _batch_path(batch: str) -> Path:
    return STATE / f"label-{batch}.json"


def _labeled() -> set[str]:
    try:
        return {json.loads(l)["path"] for l in LABELS.read_text().splitlines() if l.strip()}
    except OSError:
        return set()


def cmd_batch(folder: Path, n: int, max_mb: float, match: str = "", exclude: str = "") -> dict:
    done = _labeled()
    files = [p for p in sorted(folder.iterdir()) if p.is_file() and not p.name.startswith(".")
             and str(p) not in done
             and (not match or re.search(match, p.name, re.I))
             and not (exclude and re.search(exclude, p.name, re.I))][:n]
    cfg = classify.load_config()
    cat = classify.catalog(cfg)
    rows = [{"n": i, "path": str(p), "name": p.name, "size": p.stat().st_size,
             "snippet": classify.snippet(p, max_mb)[:400]} for i, p in enumerate(files, 1)]
    batch = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    data = {"batch": batch, "folder": str(folder), "rows": rows,
            "catalog": {k: [f.name for f in d.files[:4]] for k, d in sorted(cat.items())}}
    STATE.mkdir(parents=True, exist_ok=True)
    _batch_path(batch).write_text(json.dumps(data, indent=1, ensure_ascii=False))
    return data


def cmd_propose(batch: str, proposals: dict) -> None:
    p = _batch_path(batch)
    data = json.loads(p.read_text())
    for row in data["rows"]:
        prop = proposals.get(str(row["n"]))
        if prop:
            row["options"] = {k: v for k, v in prop.items() if k in "abc" and v}
            row["suggest"] = prop.get("suggest", "a")
    p.write_text(json.dumps(data, indent=1, ensure_ascii=False))


def cmd_show(batch: str) -> str:
    data = json.loads(_batch_path(batch).read_text())
    out = [f"Batch {batch}: one letter per row (a/b/c = option, x = trash, s = skip)", "",
           "| # | File | a | b | c | Suggest |", "|---|---|---|---|---|---|"]
    for r in data["rows"]:
        o = r.get("options", {})
        out.append(f"| {r['n']} | `{r['name']}` | {o.get('a', '')} | {o.get('b', '')} | {o.get('c', '')} "
                   f"| **{r.get('suggest', '')}** |")
    return "\n".join(out)


def _area(dest: str) -> str:
    return dest.split("/")[0] if dest not in ("trash", "skip") else dest


def _target(dest: str, name: str) -> Path:
    """A catalog key (templated or not) or a path under ~/files."""
    if "{year}" in dest:
        m = classify.YEAR_RE.search(name)
        year = m.group(1) if m else str(dt.date.today().year)
        return classify.resolve(classify.load_config(), dest, year)
    p = Path(dest).expanduser()
    return p if p.is_absolute() else FILES / dest


def _free(d: Path, name: str) -> Path:
    t, k = d / name, 1
    while t.exists():
        t = d / f"{Path(name).stem} ~{k}{Path(name).suffix}"
        k += 1
    return t


def cmd_answer(batch: str, answers: str, dry_run: bool = False) -> list[dict]:
    data = json.loads(_batch_path(batch).read_text())
    rows = {r["n"]: r for r in data["rows"]}
    log = STATE / f"{dt.date.today()}.jsonl"
    out = []
    for num, letter in ANSWER_RE.findall(answers):
        r, letter = rows.get(int(num)), letter.lower()
        if not r:
            continue
        opts = r.get("options", {})
        chosen = {"x": "trash", "s": "skip"}.get(letter) or opts.get(letter)
        if chosen is None:
            print(f"  {num}: no option {letter}; skipped", file=sys.stderr)
            continue
        sl = r.get("suggest", "")
        sug = {"x": "trash", "s": "skip"}.get(sl) or opts.get(sl, sl)
        src = Path(r["path"])
        dst = None
        if not dry_run and chosen != "skip" and src.exists():
            to_dir = TRASH if chosen == "trash" else _target(chosen, src.name)
            to_dir.mkdir(parents=True, exist_ok=True)
            dst = _free(to_dir, src.name)
            shutil.move(str(src), str(dst))
            STATE.mkdir(parents=True, exist_ok=True)
            with log.open("a") as f:
                f.write(json.dumps({"ts": dt.datetime.now().isoformat(timespec="seconds"), "rule": "label",
                                    "action": "trash" if chosen == "trash" else "move",
                                    "src": str(src), "dst": str(dst), "batch": batch}) + "\n")
        rec = {"ts": dt.datetime.now().isoformat(timespec="seconds"), "batch": batch, "path": r["path"],
               "name": r["name"], "snippet_sha": hashlib.sha256(r.get("snippet", "").encode()).hexdigest()[:12],
               "suggested": sug, "chosen": chosen, "agree": sug == chosen,
               "letter": letter, "suggest_letter": r.get("suggest"), "dst": str(dst) if dst else None}
        out.append(rec)
        if not dry_run:
            LABELS.parent.mkdir(parents=True, exist_ok=True)
            with LABELS.open("a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return out


def cmd_score() -> dict:
    try:
        recs = [json.loads(l) for l in LABELS.read_text().splitlines() if l.strip()]
    except OSError:
        recs = []
    recs = [r for r in recs if r["chosen"] != "skip"]

    def rate(rs):
        return {"n": len(rs), "agree": sum(r["agree"] for r in rs),
                "pct": round(100 * sum(r["agree"] for r in rs) / len(rs)) if rs else None}
    by_area, by_batch = {}, {}
    for r in recs:
        by_area.setdefault(_area(r["chosen"]), []).append(r)
        by_batch.setdefault(r["batch"], []).append(r)
    return {"overall": rate(recs), "by_area": {k: rate(v) for k, v in sorted(by_area.items())},
            "by_batch": {k: rate(v) for k, v in sorted(by_batch.items())}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("batch"); b.add_argument("folder", type=Path); b.add_argument("--n", type=int, default=12)
    b.add_argument("--max-mb", type=float, default=10)
    b.add_argument("--match", default="", help="only names matching this regex")
    b.add_argument("--exclude", default="", help="skip names matching this regex")
    pr = sub.add_parser("propose"); pr.add_argument("batch"); pr.add_argument("json")
    sh = sub.add_parser("show"); sh.add_argument("batch")
    an = sub.add_parser("answer"); an.add_argument("batch"); an.add_argument("answers")
    an.add_argument("--dry-run", action="store_true")
    sub.add_parser("score")
    a = ap.parse_args(argv)
    if a.cmd == "batch":
        print(json.dumps(cmd_batch(a.folder.expanduser(), a.n, a.max_mb, a.match, a.exclude), indent=1, ensure_ascii=False))
    elif a.cmd == "propose":
        cmd_propose(a.batch, json.loads(a.json)); print(cmd_show(a.batch))
    elif a.cmd == "show":
        print(cmd_show(a.batch))
    elif a.cmd == "answer":
        for r in cmd_answer(a.batch, a.answers, a.dry_run):
            print(f"  {'✓' if r['agree'] else '✗'} {r['name']} -> {r['chosen']}")
        print(json.dumps(cmd_score()["overall"]))
    elif a.cmd == "score":
        print(json.dumps(cmd_score(), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
