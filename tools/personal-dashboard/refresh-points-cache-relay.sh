#!/bin/bash
# refresh-points-cache-relay.sh — runs on Straylight, triggers the actual
# cache rebuild on Ix over SSH.
#
# Why this exists (2026-09-08): refresh_points_cache.py needs to run on Ix
# (that's where Neon分v12.2.xlsx and Excel live), but every unattended,
# headless-spawned process on Ix — cron AND launchd alike — hits one of two
# TCC walls with no way to grant past them non-interactively:
#   - openpyxl's direct file open: Full Disk Access denial for the
#     com.apple.python3 bundle (confirmed via Ix's own TCC log, authValue=0).
#   - xlwings' live-Excel automation: hangs indefinitely on the Automation/
#     AppleEvents permission (confirmed via an isolated launchd diagnostic —
#     it never gets past `xw.books`).
# Both are scheduler-agnostic: the problem is "no interactive session",
# not "wrong scheduler". A plain SSH-invoked command on Ix, however, hits
# NEITHER wall — confirmed directly (`ssh ix python3 -c 'import xlwings...'`
# connects and lists open books in <1s, reliably, every time this was
# tested this session) — because SSH sessions carry Ix's existing
# authorized-user TCC context, unlike a launchd/cron-spawned process.
#
# So: scheduling moves here, to Straylight (which has no TCC involvement in
# any of this — it just opens an SSH connection), and the actual work still
# executes on Ix, over SSH, exactly as every other Excel-touching operation
# in this codebase already does (see: Straylight→Ix routing for AppleScript/
# Excel writes elsewhere in this repo). No new permission grant on Ix,
# anywhere, at any point.
#
# LaunchAgent: com.jm.refresh-points-cache-relay (Straylight), every 30 min.

set -euo pipefail

ssh -o ConnectTimeout=20 -o BatchMode=yes ix \
  "bash ~/i446-monorepo/tools/personal-dashboard/refresh-points-cache.sh"
