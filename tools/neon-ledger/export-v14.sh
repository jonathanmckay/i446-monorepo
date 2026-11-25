#!/bin/bash
# export-v14.sh: run the daily ledger -> Neon分v14-export.xlsx backup.
#
# LaunchAgent com.jm.neon-v14-export (Ix, daily 04:30) runs this. The export
# reads and writes OneDrive, which a launchd-spawned python on Ix cannot do
# (no Full Disk Access for the com.apple.python3 bundle; see
# tools/personal-dashboard/refresh-points-cache-relay.sh). An ssh-invoked
# command can, so this hops through a loopback ssh.
set -euo pipefail
exec ssh -o BatchMode=yes -o ConnectTimeout=20 ix \
  '/usr/bin/python3 ~/i446-monorepo/tools/neon-ledger/export_xlsx.py'
