#!/usr/bin/env bash
# Run on each machine to dump diagnostic info into a per-machine subfolder.
# Usage: ./collect-logs.sh <hostname>
set -e
HOST="${1:-$(hostname -s)}"
OUT="diagnostics/claude-export/$HOST"
mkdir -p "$OUT"
echo "--- env ---" > "$OUT/env.txt"
uname -a >> "$OUT/env.txt"
echo >> "$OUT/env.txt"
echo "--- claude-export.log tail ---" > "$OUT/log-tail.txt"
tail -200 ~/vault/i447/i446/ai-transcripts/claude-export.log >> "$OUT/log-tail.txt" 2>&1 || echo "no log" >> "$OUT/log-tail.txt"
echo "--- crontab ---" > "$OUT/cron.txt"
crontab -l 2>/dev/null | grep -i export >> "$OUT/cron.txt" || echo "no cron entry" >> "$OUT/cron.txt"
echo "--- candidate scripts ---" > "$OUT/scripts.txt"
{ ls -la ~/i446-monorepo/scripts/*export* ~/i446-monorepo/tools/**/export* ~/bin/*export* 2>/dev/null; } >> "$OUT/scripts.txt" || true
echo "wrote diagnostics to $OUT"
