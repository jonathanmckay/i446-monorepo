#!/usr/bin/env python3
"""dream-tui — minimal terminal UI for grading a Dream morning brief.

Reads `cards.json` from the latest Dream run dir, presents each card with its
pre-drafted multiple-choice responses, and writes grading + responses back to
`grades.json`. JM picks by single keypress (a/b/c/d or 1-5 grade).

Usage:
    python3 dream.py [run-dir]

If run-dir is omitted, picks the latest dream-runs/YYYY.MM.DD-dry-run-v* dir.
"""
from __future__ import annotations

import json
import os
import sys
import termios
import tty
from datetime import datetime
from pathlib import Path

DREAM_RUNS = Path.home() / "vault" / "i447" / "i446" / "dream-runs"


def latest_run() -> Path:
    runs = sorted(DREAM_RUNS.glob("*-dry-run-v*"))
    if not runs:
        sys.exit("no dream runs found")
    return runs[-1]


def getch() -> str:
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return ch


def clear() -> None:
    os.system("clear" if os.name != "nt" else "cls")


def render_card(card: dict, idx: int, total: int) -> None:
    clear()
    print(f"\033[1mCard {idx + 1}/{total}\033[0m  ·  {card.get('status', '?')}")
    print()
    print(f"\033[1m{card['title']}\033[0m  [{card.get('points', '?')}] (self-grade {card.get('self_grade', '?')})")
    print()
    if anchor := card.get("anchor"):
        print(f"  \033[90mAnchor:\033[0m {anchor}")
    if did := card.get("did"):
        print(f"  \033[90mDid:\033[0m {did}")
    if leaves := card.get("leaves"):
        print(f"  \033[90mLeaves:\033[0m {leaves}")
    print()
    print("\033[1mPick one:\033[0m")
    for k, v in (card.get("choices") or {}).items():
        print(f"  ({k}) {v}")
    print()
    print("\033[90mGrade after picking (1=worst, 5=best, space to skip, q to quit)\033[0m")


def main() -> None:
    run_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else latest_run()
    cards_path = run_dir / "cards.json"
    if not cards_path.exists():
        sys.exit(f"no cards.json in {run_dir} — Dream v7+ required")
    cards = json.loads(cards_path.read_text())
    grades_path = run_dir / "grades.json"
    grades = json.loads(grades_path.read_text()) if grades_path.exists() else {}

    for i, card in enumerate(cards):
        if card.get("id") in grades:
            continue  # already graded this run
        render_card(card, i, len(cards))
        choice = getch()
        if choice == "q":
            break
        if choice == " ":
            continue
        valid_choices = set((card.get("choices") or {}).keys())
        if choice not in valid_choices:
            print(f"\n  invalid choice '{choice}' — press space to retry")
            getch()
            continue
        print(f"\n  → picked ({choice})  ·  grade (1-5): ", end="", flush=True)
        grade = getch()
        if grade not in "12345":
            grade = ""
        grades[card["id"]] = {
            "choice": choice,
            "grade": grade,
            "ts": datetime.now().isoformat(timespec="seconds"),
        }
        grades_path.write_text(json.dumps(grades, indent=2))
        print(grade)

    print(f"\n\nWrote {len(grades)} grades to {grades_path}")
    print("\nDream can read this to learn JM patterns + execute the chosen options.")


if __name__ == "__main__":
    main()
