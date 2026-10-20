#!/usr/bin/env python3
"""Report where dtd's keypress latency goes, from /tmp/dtd-<id>.timing.log.

Usage: dtd-timing-report.py [session-id | path]   (default: newest log)

Each generated script stamps `$EPOCHREALTIME<TAB>stage<TAB>arg` at its
boundaries (see DTD_TIMING in dtd.sh). An alt-enter is reconstructed as the
span from router-start to the next hdr-end; the gaps BETWEEN scripts are
fzf's own spawn/handoff cost, which is where an exec-scan stall shows up.
"""
import glob, os, statistics, sys
from collections import defaultdict

def load(path):
    rows = []
    for line in open(path):
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 2:
            continue
        try:
            rows.append((float(parts[0]), parts[1], parts[2] if len(parts) > 2 else ""))
        except ValueError:
            pass
    return rows

def main():
    if len(sys.argv) > 1:
        a = sys.argv[1]
        path = a if os.path.exists(a) else f"/tmp/dtd-{a}.timing.log"
    else:
        logs = sorted(glob.glob("/tmp/dtd-*.timing.log"), key=os.path.getmtime)
        if not logs:
            sys.exit("no timing logs")
        path = logs[-1]
    rows = load(path)
    print(f"{path}: {len(rows)} stamps\n")

    # --- alt-enter sequences ------------------------------------------------
    seqs = []
    i = 0
    while i < len(rows):
        if rows[i][1] == "router-start":
            seq = [rows[i]]
            j = i + 1
            while j < len(rows) and rows[j][1] not in ("router-start",):
                seq.append(rows[j])
                if rows[j][1] == "hdr-end":
                    break
                j += 1
            seqs.append(seq)
            i = j + 1
        else:
            i += 1
    cols = ["router", "→hide", "hide→done", "done", "→list", "list", "→hdr", "hdr", "TOTAL", "row-gone"]
    print("alt-enter breakdown (seconds)")
    print(f"{'task':16} " + " ".join(f"{c:>9}" for c in cols))
    agg = defaultdict(list)
    for seq in seqs:
        t = {}
        for ts, st, arg in seq:
            t.setdefault(st, ts)
        if "router-end" not in t:
            continue
        tid = seq[0][2][:16]
        def d(a, b):
            return (t[b] - t[a]) if a in t and b in t else None
        vals = [d("router-start", "router-end"), d("router-end", "hide"), d("hide", "done-start"),
                d("done-start", "done-end"), d("done-end", "list-start"), d("list-start", "list-end"),
                d("list-end", "hdr-start"), d("hdr-start", "hdr-end"), d("router-start", "hdr-end"),
                d("router-start", "router-end")]
        for c, v in zip(cols, vals):
            if v is not None:
                agg[c].append(v)
        print(f"{tid:16} " + " ".join(f"{v:9.3f}" if v is not None else f"{'-':>9}" for v in vals))
    if agg:
        print(f"{'MEDIAN':16} " + " ".join(f"{statistics.median(agg[c]):9.3f}" if agg[c] else f"{'-':>9}" for c in cols))
        print(f"{'MAX':16} " + " ".join(f"{max(agg[c]):9.3f}" if agg[c] else f"{'-':>9}" for c in cols))
    print("\nrow-gone = when fzf's exclude fires (router-end). Gaps (→) are fzf spawn/handoff.")

    # --- worker ---------------------------------------------------------------
    starts = {}
    durs = []
    for ts, st, arg in rows:
        if st == "didfast-start":
            starts[arg] = ts
        elif st == "didfast-end":
            key = arg.split(" rc=")[0]
            if key in starts:
                durs.append((arg, ts - starts.pop(key)))
    if durs:
        print("\nbackground did-fast per task (seconds; this is the ⏳ → ✓ wait, not the row):")
        for arg, dd in durs:
            print(f"  {dd:7.2f}  {arg[:60]}")
        print(f"  median {statistics.median(d for _, d in durs):.2f}")

    # --- other paths ----------------------------------------------------------
    for a, b, label in (("defer-start", "defer-prompt", "ctrl-d → prompt shown"),
                        ("defer-prompt", "defer-end", "defer prompt → done"),
                        ("arm-start", "arm-end", "ctrl-v arm script"),
                        ("list-start", "list-end", "list regen (all reloads)")):
        ds = []
        last = None
        for ts, st, _ in rows:
            if st == a:
                last = ts
            elif st == b and last is not None:
                ds.append(ts - last); last = None
        if ds:
            print(f"\n{label}: n={len(ds)} median {statistics.median(ds):.3f}s max {max(ds):.3f}s")

if __name__ == "__main__":
    main()
