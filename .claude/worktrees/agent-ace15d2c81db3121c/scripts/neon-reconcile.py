#!/usr/bin/env python3
"""Nightly/morning Neon reconcile for JM Dash's source dimension.

Asks the excel-http daemon (localhost:9876 on ix) to /reconcile today's and
yesterday's 0分 point cells (-1₦ through n156). Changed cells are journaled as
source 3p-app / via excel (an Excel edit no pipeline write revealed); cells the
ledger has never seen get a sourceless baseline. Cron on ix runs it at 03:50
(baseline today, close out yesterday) and 23:55 (catch today's edits).
"""
import datetime
import json
import sys
import urllib.request
from pathlib import Path

COLS_FILE = Path.home() / "i446-monorepo" / "config" / "neon-cols.json"
FIRST, LAST = "-1₦", "n156"
URL = "http://127.0.0.1:9876/reconcile"


def point_cols() -> list[str]:
    headers = json.loads(COLS_FILE.read_text())["sheets"]["0分"]["headers"]
    names = list(headers)
    return [headers[n] for n in names[names.index(FIRST):names.index(LAST) + 1]]


def main() -> int:
    cols = point_cols()
    today = datetime.date.today()
    rc = 0
    for d in (today - datetime.timedelta(days=1), today):
        body = {"sheet": "0分", "date": f"{d.month}/{d.day}", "cols": cols}
        req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                out = json.loads(r.read())
        except Exception as e:
            out = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        print(f"{stamp} {body['date']} {json.dumps(out, ensure_ascii=False)}", flush=True)
        rc |= 0 if out.get("ok") else 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
