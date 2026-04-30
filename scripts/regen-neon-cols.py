#!/usr/bin/env python3
"""
Regenerate ~/i446-monorepo/config/neon-cols.json from the live Neon spreadsheet on ix.

Reads row 1 of each tracked sheet, builds {header → column letter}, preserves the
hand-curated alias maps (domain_aliases, to_0fen_col_map, daily_dozen, ate_bands).
"""

import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

CONFIG = Path.home() / "i446-monorepo/config/neon-cols.json"
SHEETS = ["0分", "0n", "1n+", "hcbi"]
MAX_COL = 70


def col_letter(n: int) -> str:
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def read_headers() -> dict:
    """SSH to ix, read row 1 of each sheet, return {sheet: {header: letter}}."""
    sheet_loop = "{" + ", ".join(f'"{s}"' for s in SHEETS) + "}"
    script = f'''
tell application "Microsoft Excel"
    set wb to active workbook
    set out to ""
    repeat with sn in {sheet_loop}
        set sh to sheet sn of wb
        set out to out & "##" & sn & linefeed
        repeat with c from 1 to {MAX_COL}
            set v to value of cell 1 of column c of sh
            if v is not missing value and (v as string) is not "" then
                set out to out & c & "|" & (v as string) & linefeed
            end if
        end repeat
    end repeat
    return out
end tell
'''
    # Pass script via stdin to avoid shell-escaping issues with `{}` and quotes
    result = subprocess.run(
        ["ssh", "ix", "osascript", "-"],
        input=script,
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        sys.exit(f"ssh ix failed: {result.stderr}")
    out: dict = {}
    cur = None
    for line in result.stdout.splitlines():
        if line.startswith("##"):
            cur = line[2:]
            out[cur] = {}
            continue
        if not line or "|" not in line or cur is None:
            continue
        n, hdr = line.split("|", 1)
        hdr = hdr.strip()
        if hdr:
            out[cur][hdr] = col_letter(int(n))
    return out


def main():
    live = read_headers()
    if not CONFIG.exists():
        sys.exit(f"missing {CONFIG} — bootstrap it first")
    cfg = json.loads(CONFIG.read_text())
    for sn, hdrs in live.items():
        if sn not in cfg["sheets"]:
            print(f"  new sheet: {sn}", file=sys.stderr)
            cfg["sheets"][sn] = {"headers": {}}
        cfg["sheets"][sn]["headers"] = hdrs
    cfg["_meta"]["last_regen"] = date.today().isoformat()
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    print(f"updated {CONFIG} ({sum(len(v.get('headers', {})) for v in cfg['sheets'].values())} headers across {len(cfg['sheets'])} sheets)")


if __name__ == "__main__":
    main()
