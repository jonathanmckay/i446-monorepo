#!/bin/bash
# export-v14.sh: run the ledger -> Neon分v14-export.xlsx backup, then keep a
# dated copy of it (export_rotate.py: 7 blocks, 2 days, a 7 and a 30 day one).
#
# LaunchAgent com.jm.neon-v14-export (Ix; 04:30 and ten past every block
# boundary 06..22) runs this. The export reads and writes OneDrive, which a
# launchd-spawned python on Ix cannot do (no Full Disk Access for the
# com.apple.python3 bundle; see
# tools/personal-dashboard/refresh-points-cache-relay.sh). An ssh-invoked
# command can, so this hops through a loopback ssh. A failed export (its
# read-back gate) leaves the previous export in place and takes no snapshot.
set -uo pipefail
# range_probe (2026-11-25) rides along every block: book vs Excel on the ranges
# the /read_range callers use. It only measures; its verdict is in
# ~/.cache/neon-rules/range-probe.jsonl and never fails the export.
ssh -o BatchMode=yes -o ConnectTimeout=20 ix \
  '/usr/bin/python3 ~/i446-monorepo/tools/neon-ledger/range_probe.py run >/dev/null 2>&1 || true'
exec ssh -o BatchMode=yes -o ConnectTimeout=20 ix \
  '/usr/bin/python3 ~/i446-monorepo/tools/neon-ledger/export_xlsx.py && /usr/bin/python3 ~/i446-monorepo/tools/neon-ledger/export_rotate.py'
