#!/usr/bin/env python3
"""salat-fast.py — fast prayer-counter write for /ص and /salat.

No args: increment today's 0n ص cell by 1. With a number: set it to that
value. Writes via the excel-http daemon on ix (lib/neon/excel.py client,
~30ms; falls back to ssh+osascript automatically), then stamps the ☀️ صلاة
marker on the current 地支 block in the local build order (prayer_marker.py,
idempotent) so janus / -2n / wakeup / 1-1n all see it.

Usage:
    salat-fast.py            # +1
    salat-fast.py 3          # set to 3 (accepts ٣ / ۳ / 三 too)

Output: one line `ص: N`. Exit 1 on parse or write failure.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "i446-monorepo/lib"))
from neon import cols, excel  # noqa: E402
import daytime  # noqa: E402  shared "now"/"today" resolution — see lib/daytime.py

# AppleScript's `as number` (and the daemon's float()) only coerce ASCII
# digits — `/ص ٨` used to fail silently. Normalize before validating.
_DIGIT_MAP = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩"    # Arabic-Indic
    "۰۱۲۳۴۵۶۷۸۹"    # Eastern Arabic-Indic (Persian)
    "零一二三四五六七八九",  # CJK
    "0123456789" * 3,
)


def normalize_numeral(arg: str) -> int:
    """Parse a count from ASCII, Arabic-Indic, Persian, or CJK numerals.
    Raises ValueError when the argument isn't a number."""
    s = arg.strip()
    if s == "十":
        return 10
    return int(s.translate(_DIGIT_MAP))


def main() -> int:
    count = None
    if len(sys.argv) > 1 and sys.argv[1].strip():
        try:
            count = normalize_numeral(sys.argv[1])
        except ValueError:
            print(f"ص: cannot parse {sys.argv[1]!r} as a number")
            return 1

    now = daytime.local_now()
    today = f"{now.month}/{now.day}"
    col = cols.maybe_col("0n", "ص") or "AP"

    if count is None:
        res = excel.append("0n", col, date=today, value="+1")
    else:
        res = excel.write("0n", col, date=today, value=str(count))
    if not res.get("ok"):
        print(f"ص: write failed — {res.get('error', 'unknown error')}")
        return 1

    # Local build-order ☀️ stamp; never fail the count on a marker problem.
    marker_note = ""
    try:
        m = subprocess.run(
            [sys.executable, str(Path(__file__).parent / "prayer_marker.py")],
            capture_output=True, text=True, timeout=20)
        if m.returncode != 0:
            marker_note = "  (⚠ prayer marker failed)"
    except Exception:
        marker_note = "  (⚠ prayer marker failed)"

    val = res.get("value", "")
    try:
        val = f"{float(val):g}"
    except (TypeError, ValueError):
        val = str(val or (count if count is not None else ""))
    print(f"ص: {val}{marker_note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
