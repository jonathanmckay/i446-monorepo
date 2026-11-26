#!/usr/bin/env python3
"""janus_ate: /ate typed into janus's input box (JM 2026-11-26).

Same grammar as the /ate skill:
    /ate [<branch>] <name>[, <kcal>[, <protein_g>]] [(<group> <count>, ...)]
    /早餐 ...                      (the 辰 band)
The write is tools/ate/ate.py's single batch. What the line leaves out (kcal,
protein, or the groups; `help` in a slot means the same) is estimated first by
a headless `claude -p` that returns JSON and touches nothing; a typed value is
never overridden. `()` says "no groups" without asking for an estimate.
No prompt_toolkit here: janus.py only routes the line.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ATE = str(Path.home() / "i446-monorepo" / "tools" / "ate" / "ate.py")
BRANCHES = "卯辰巳午未申戌"
ALIASES = {"/ate": None, "/早餐": "辰"}
GROUPS = "beans, berries, fruit, cruciferous, greens, vegetables, flax, grains, nuts, spices, water"
ESTIMATE_MODEL = "sonnet"
ESTIMATE_TIMEOUT_S = 90
WRITE_TIMEOUT_S = 45
_NUM = re.compile(r"^[\d.+\-*/ ()x]*\d[\d.+\-*/ ()x]*$")


def _slot(text: str):
    """A kcal / protein slot: its text, or None for blank or `help`."""
    text = text.strip()
    return None if not text or text.lower() == "help" else text


def _is_slot(text: str) -> bool:
    text = text.strip()
    return text.lower() == "help" or bool(_NUM.match(text))


def parse(text: str) -> dict | None:
    """{name, kcal, protein, groups, branch} for an /ate line, None for
    anything else. kcal / protein / groups are None when left to estimate;
    groups is "" for an explicit `()`."""
    head, _, rest = text.strip().partition(" ")
    if head not in ALIASES or not rest.strip():
        return None
    branch, rest = ALIASES[head], rest.strip()
    first, _, tail = rest.partition(" ")
    if first in BRANCHES and len(first) == 1 and tail.strip():
        branch, rest = first, tail.strip()
    groups = None
    m = re.search(r"\(([^()]*)\)\s*$", rest)
    if m:
        groups = "" if not m.group(1).strip() else m.group(1).strip()
        if groups.lower() == "help":
            groups = None
        rest = rest[:m.start()].rstrip().rstrip(",")
    parts = [p.strip() for p in rest.split(",")]
    kcal = protein = None
    # the numbers are the trailing slots; commas inside the name stay in it
    if len(parts) >= 3 and _is_slot(parts[-1]) and _is_slot(parts[-2]):
        kcal, protein, parts = _slot(parts[-2]), _slot(parts[-1]), parts[:-2]
    elif len(parts) >= 2 and _is_slot(parts[-1]):
        kcal, parts = _slot(parts[-1]), parts[:-1]
    name = ", ".join(p for p in parts if p)
    if not name:
        return None
    return {"name": name, "kcal": kcal, "protein": protein, "groups": groups, "branch": branch}


def missing(spec: dict) -> list:
    return [k for k in ("kcal", "protein", "groups") if spec[k] is None]


def estimate(spec: dict, run=subprocess.run) -> dict:
    """Fill the None fields of `spec` from a headless Claude; raises
    RuntimeError with a one-line reason when it cannot."""
    need = missing(spec)
    prompt = (
        "Estimate nutrition for one food-log entry. Reply with ONE line of JSON and nothing else: "
        '{"kcal": <int>, "protein": <int grams>, "groups": {"<group>": <servings>, ...}}. '
        "Infer the portion from the wording. Groups are Daily Dozen servings, only from: " + GROUPS
        + "; leave out groups the food does not count toward. Do not use any tools.\n"
        "Entry: " + spec["name"]
        + ("\nKnown kcal: %s" % spec["kcal"] if spec["kcal"] else "")
        + ("\nKnown protein g: %s" % spec["protein"] if spec["protein"] else ""))
    try:
        r = run(["claude", "-p", prompt, "--model", ESTIMATE_MODEL], capture_output=True, text=True,
                timeout=ESTIMATE_TIMEOUT_S, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise RuntimeError("no `claude` on this Mac to estimate %s: type them" % "/".join(need))
    except subprocess.TimeoutExpired:
        raise RuntimeError("estimate timed out: nothing written")
    m = re.search(r"\{.*\}", r.stdout or "", re.S)
    try:
        got = json.loads(m.group(0)) if m else None
    except ValueError:
        got = None
    if r.returncode != 0 or not isinstance(got, dict):
        tail = (r.stderr or r.stdout or "").strip().splitlines()
        raise RuntimeError("estimate failed: nothing written (%s)" % (tail[-1][:80] if tail else "no reply"))
    out = dict(spec)
    for k in ("kcal", "protein"):
        if out[k] is None:
            if not isinstance(got.get(k), (int, float)) or isinstance(got.get(k), bool):
                raise RuntimeError("estimate gave no %s: nothing written" % k)
            out[k] = "%g" % round(got[k])
    if out["groups"] is None:
        g = got.get("groups") if isinstance(got.get("groups"), dict) else {}
        out["groups"] = ", ".join("%s %g" % (k, v) for k, v in g.items()
                                  if isinstance(v, (int, float)) and v > 0 and k in GROUPS.split(", "))
    return out


def argv(spec: dict, date: str | None = None) -> list:
    out = [sys.executable, ATE, "--name", spec["name"], "--kcal", spec["kcal"], "--protein", spec["protein"]]
    if spec.get("groups"):
        out += ["--groups", spec["groups"]]
    if spec.get("branch"):
        out += ["--branch", spec["branch"]]
    if date:
        out += ["--date", date]
    return out


def run_ate(spec: dict, date: str | None = None, run=subprocess.run) -> str:
    """Blocking (call from a worker thread): estimate what is missing, write
    once, return the line to flash."""
    need = missing(spec)
    try:
        full = estimate(spec, run) if need else spec
    except RuntimeError as e:
        return "err: ate: %s" % e
    try:
        r = run(argv(full, date), capture_output=True, text=True, timeout=WRITE_TIMEOUT_S,
                stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return "err: ate timed out, the entry may have landed: check hcbi before redoing"
    except Exception as e:                                  # noqa: BLE001
        return "err: ate: %s" % e
    out = (r.stdout if r.returncode == 0 else r.stderr or r.stdout or "").strip().splitlines()
    line = out[-1] if out else "(no output)"
    if r.returncode != 0:
        return "err: " + line
    return line + (" · est. " + "/".join(need) if need else "")
