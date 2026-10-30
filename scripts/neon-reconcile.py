#!/usr/bin/env python3
"""Nightly/morning Neon reconcile + value snapshots.

Asks the excel-http daemon (localhost:9876 on ix) to /reconcile today's and
yesterday's rows. Changed FORMULAS are journaled as `reconcile` (source
3p-app / via excel: an Excel edit no pipeline write revealed) or `baseline`
(never seen). With "snapshot": true (2026-10-19) a changed VALUE under an
unchanged formula is journaled as `snapshot`: that is how the rule-engine
parallel run (lib/neon/rules.py, tools/neon-ledger/neon.py diff) gets Excel's
truth for the formula cells (0分 D, G:O, P..Z) and for every 0n / hcbi
constant it must derive from. Cron on ix: 03:50 and 23:55 (config/ix.crontab).

Sheets / ranges:
  0分  D..Z   (Σ, E/F, blocks, points)       for yesterday + today
  0n   D..BK  (habits, media AU..AX, Σ cells) for yesterday + today, + row 2 (weights)
  1n+  C..AL row 5 (expected points referenced by 0分 credits)
  hcbi E..BE  (daily dozen, bands BC:BE incl., 0s…)  for yesterday + today
"""
import datetime
import json
import sys
import urllib.request

URL = "http://127.0.0.1:9876/reconcile"


def col_letter(n: int) -> str:
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def col_num(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n


def span(a: str, b: str) -> list:
    return [col_letter(i) for i in range(col_num(a), col_num(b) + 1)]


RANGES = {"0分": span("D", "Z"), "0n": span("D", "BK"), "hcbi": span("E", "BE")}
WEEK_ROW5 = span("C", "AL")   # 1n+ row 5: expected points the 0分 credits reference


def post(body: dict) -> dict:
    req = urllib.request.Request(URL, data=json.dumps(body, ensure_ascii=False).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def main() -> int:
    today = datetime.date.today()
    rc = 0
    jobs = []
    for d in (today - datetime.timedelta(days=1), today):
        for sheet, cols in RANGES.items():
            jobs.append({"sheet": sheet, "date": f"{d.month}/{d.day}", "cols": cols, "snapshot": True})
    # 0n row 2 = the habit weights the rule engine applies; a changed weight is
    # itself a ledger event this way (row-addressed, no date).
    jobs.append({"sheet": "0n", "row": 2, "cols": RANGES["0n"], "snapshot": True, "src": "snapshot 0n weights"})
    jobs.append({"sheet": "1n+", "row": 5, "cols": WEEK_ROW5, "snapshot": True, "src": "snapshot 1n+ row5"})
    for body in jobs:
        out = post(body)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        n = len(out.get("changes", [])) if out.get("ok") else out.get("error")
        print(f"{stamp} {body['sheet']} {body.get('date') or 'r' + str(body.get('row'))} "
              f"changes={n}", flush=True)
        rc |= 0 if out.get("ok") else 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
