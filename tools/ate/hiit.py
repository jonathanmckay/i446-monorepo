#!/usr/bin/env python3
"""hiit.py — /hiit: future-fit workouts done into hcbi AE (`ff`) on the named
day (default today).

    hiit.py "<count> [when]"  e.g. "1", "2 yesterday", "1 10/30"
    hiit.py --parse "<text>"

Grammar and behavior: tools/ate/hcbi_day.py.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hcbi_day import Cell  # noqa: E402

HIIT = Cell("hiit", "ff", "hiit|ff|future-?fit|workouts?")

if __name__ == "__main__":
    sys.exit(HIIT.main(sys.argv[1:], __doc__))
