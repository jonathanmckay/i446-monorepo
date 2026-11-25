#!/usr/bin/env python3
"""
Regenerate ~/i446-monorepo/config/neon-cols.json from the live Neon sheets,
read through the excel-http daemon on ix.

Reads row 1 of each tracked sheet, builds {header → column letter}, preserves the
hand-curated alias maps (domain_aliases, to_0fen_col_map, daily_dozen, ate_bands).
"""

import json
import re
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
    """Row 1 of each sheet through the excel-http daemon (lib/neon/excel.py),
    as {sheet: {header: letter}}. Exits on any failed or short read: a partial
    answer must never be written over neon-cols.json."""
    lib = str(Path(__file__).resolve().parent.parent / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    from neon import excel as neon_excel
    rng = f"A1:{col_letter(MAX_COL)}1"
    out: dict = {}
    for sn in SHEETS:
        res = neon_excel.read_range(sn, rng)
        rows = res.get("rows") if res.get("ok") else None
        if not rows or len(rows[0]) != MAX_COL:
            sys.exit(f"excel-http read_range {sn}!{rng} failed: {res.get('error') or res}")
        out[sn] = {}
        for n, v in enumerate(rows[0], 1):
            hdr = str(v).strip()
            # Numeric headers come back stringified with a float tail
            # ("-2" → "-2.0"), which breaks cols.col("0n", "-2") lookups (the
            # media value-tag minute columns, 2026-07-28). Normalize to the
            # header as typed in the sheet.
            if re.fullmatch(r"-?\d+\.0", hdr):
                hdr = hdr[:-2]
            if hdr:
                out[sn][hdr] = col_letter(n)
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
