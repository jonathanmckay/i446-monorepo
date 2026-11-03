#!/usr/bin/env python3
"""out.py — /out: outdoor score into hcbi AI on the named day (default today).

    out.py "<score> [when]"   e.g. "10", "-1 yesterday", "10 10/30"
    out.py --parse "<text>"

Grammar and behavior: tools/ate/hcbi_day.py.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hcbi_day import Cell  # noqa: E402

OUT = Cell("out", "AI", "out|outside|outdoors?|score")

if __name__ == "__main__":
    sys.exit(OUT.main(sys.argv[1:], __doc__))
