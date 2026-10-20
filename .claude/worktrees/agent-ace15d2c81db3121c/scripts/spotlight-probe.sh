#!/bin/bash
# Spotlight rebuild probe: samples index coverage until the vault is searchable.
#
# Written 2026-09-29 after Straylight's disk hit 0.7% free (09-26): mds_stores
# segfaulted in index_FlushCache on 09-28, and the vault vanished from Finder/
# Alfred. Neither `sudo mdutil -E /System/Volumes/Data` nor deleting
# .Spotlight-V100 fixed it while the ORIGINAL mds (query server, up since
# before the crash) kept running; the fix was `sudo kill -TERM $(pgrep -x mds)`
# (launchd respawns it; SIP blocks `launchctl kickstart`), after which the
# vault was searchable in under a minute. Run this after any Spotlight
# surgery instead of guessing: it exits 0 with DONE when a Finder-style name
# search resolves a known note AND >90% of vault .md files are indexed.
# NB: a bare `mdfind -count "kMDItemFSName == '*'"` (no -onlyin) can report 0
# on a healthy index — judge by the -onlyin ~/vault columns, not `volume`.
# usage: spotlight-probe.sh <max_minutes> [interval_s]
MAX=${1:-45}; INT=${2:-30}; END=$(( $(date +%s) + MAX*60 ))
VAULT_MD=$(find ~/vault -name '*.md' -not -path '*/.git/*' | wc -l | tr -d ' ')
printf "%-9s %-8s %-9s %-8s %-8s %s\n" time volume vault_all vault_md finder_ok mds_stores
while [ $(date +%s) -lt $END ]; do
  V=$(mdfind -count "kMDItemFSName == '*'" 2>/dev/null); VA=$(mdfind -count -onlyin ~/vault 'kMDItemFSName == "*"' 2>/dev/null)
  VM=$(mdfind -count -onlyin ~/vault 'kMDItemFSName == "*.md"' 2>/dev/null)
  # the Finder/Alfred test: a name search for a known note resolves to its path
  F=$(mdfind -onlyin ~/vault -name "build-order" 2>/dev/null | grep -c "g245/5e-1/build-order.md")
  P=$(ps -o pid=,etime= -p $(pgrep -x mds_stores) 2>/dev/null | tr -s ' ')
  printf "%-9s %-8s %-9s %-8s %-8s %s\n" "$(date +%H:%M:%S)" "$V" "$VA" "$VM/$VAULT_MD" "$([ "$F" -gt 0 ] && echo yes || echo no)" "$P"
  if [ "$F" -gt 0 ] && [ "$VM" -gt $(( VAULT_MD * 9 / 10 )) ]; then echo "DONE: vault searchable ($VM/$VAULT_MD md indexed)"; exit 0; fi
  sleep $INT
done
echo "TIMEOUT after ${MAX}m (vault_md=$VM/$VAULT_MD)"; exit 2
