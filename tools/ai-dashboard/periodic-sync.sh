#!/bin/bash
# periodic-sync.sh — merge llm-sessions.db (Copilot CLI etc.) into the
# m5x2 dashboard cache and push, even when Claude Code's Stop hook hasn't
# fired today. Designed for cron (every ~30 min).
#
# Smoke test: tools/ai-dashboard/test_smoke.py hits the three local
# dashboards (5555/5556/5558) + the 5555 JSON APIs. Not wired in here
# (too noisy for cron) — run manually after restarts/deploys:
#   python3 "$(dirname "$0")/test_smoke.py"

# One run at a time (2026-10-11): at */2 on Straylight a run outlasted the
# interval, so copies stacked up and kept the machine loaded. mkdir is atomic;
# a lock whose pid is gone is stale and gets taken over.
LOCK="${TMPDIR:-/tmp}/periodic-sync.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  if kill -0 "$(cat "$LOCK/pid" 2>/dev/null)" 2>/dev/null; then exit 0; fi
  rm -rf "$LOCK"; mkdir "$LOCK" 2>/dev/null || exit 0
fi
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT

REPO_DIR="$HOME/code/m5x2-ai-stats"; [ -d "$REPO_DIR" ] || REPO_DIR="$HOME/m5x2-ai-stats"   # ~/code since 2026-10-27
USER_ID="${M5X2_USER_ID:-jm}"
STATS_FILE="$HOME/.claude/stats-cache.json"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOST=$(hostname -s | tr '[:upper:]' '[:lower:]')

# Straylight is the authoritative host for stats-cache.json: it has both its
# own ~/.claude/projects/ AND ix's mirror at ~/.claude/projects-ix/ (kept fresh
# by pull-from-ix.sh). We rebuild the cache from raw JSONLs because Claude
# Code 2.1.96 (cmux-bundled) no longer auto-maintains stats-cache.json.
# Other hosts skip the rebuild AND skip the stats-cache.json git-add so they
# don't clobber Straylight's union with their partial view.
IS_AUTHORITATIVE=0
if [[ "$HOST" == straylight* ]]; then
  IS_AUTHORITATIVE=1
  bash "$SCRIPT_DIR/pull-from-ix.sh" >/dev/null 2>&1 || true
  python3 "$SCRIPT_DIR/build-stats-cache.py" >>"$SCRIPT_DIR/.build-stats-cache.log" 2>&1 || true
fi

# Ix ingest (2026-11-06): the 5555 server's `GET /` runs the incremental
# ingest_claude_sessions(), so hitting it here every 15 min keeps
# llm-sessions.db current on every host that runs the AI dashboard, whatever
# its ingest thread is doing (on Ix the DB sat stale 3 nights running, Dream
# v178-v180). ~25 s warm; capped at 150 s. Connection refused (no dashboard
# on this host) returns at once. The direct-ingest backstop below then finds
# the DB fresh and skips.
curl -s -o /dev/null --max-time 150 http://localhost:5555/ \
  || echo "[$(date '+%F %T')] 5555 ingest request failed (curl exit=$?)" >>"$SCRIPT_DIR/.periodic-sync.log"

# Session ingest (2026-11-05): llm-sessions.db is kept fresh by the 5555
# server's own 15-min ingest thread. If that thread or the server dies, the DB
# goes stale silently (it sat 8 days stale before 2026-10-26). Backstop: when
# the DB hasn't been written in 30 min, run the same incremental ingest
# directly (idempotent upserts; unchanged JSONLs are skipped). Capped at 150 s
# with SIGALRM since macOS has no timeout(1). Runs before the stats-cache
# early exits so a host without stats-cache.json still ingests.
LLM_DB="$HOME/vault/i447/i446/llm-sessions.db"
if [ -f "$LLM_DB" ] && [ -n "$(find "$LLM_DB" -mmin +30 2>/dev/null)" ]; then
  (cd "$SCRIPT_DIR" && python3 -c 'import signal; signal.alarm(150); import dashboard; dashboard.ingest_claude_sessions()') \
    >>"$SCRIPT_DIR/.periodic-sync.log" 2>&1 \
    || echo "[$(date '+%F %T')] session ingest failed (exit=$?)" >>"$SCRIPT_DIR/.periodic-sync.log"
fi

[ -f "$STATS_FILE" ] || exit 0
[ -d "$REPO_DIR/.git" ] || exit 0

mkdir -p "$REPO_DIR/$USER_ID"

if [ "$IS_AUTHORITATIVE" = 1 ]; then
  if ! python3 "$SCRIPT_DIR/merge-llm-sessions.py" \
        --src "$STATS_FILE" \
        --out "$REPO_DIR/$USER_ID/stats-cache.json" \
        --device "$HOST" 2>>"$SCRIPT_DIR/.periodic-sync.log"; then
    echo "[$(date '+%F %T')] merge failed (exit=$?), falling back to cp" >>"$SCRIPT_DIR/.periodic-sync.log"
    cp "$STATS_FILE" "$REPO_DIR/$USER_ID/stats-cache.json"
  fi
fi

# Refresh session-stats.json too (MCP/skill/latency from JSONL)
python3 "$SCRIPT_DIR/compute-session-stats.py" \
  --user "$USER_ID" \
  --out "$REPO_DIR/$USER_ID/session-stats.json" \
  >/dev/null 2>&1 || true

cd "$REPO_DIR" || exit 0
if [ "$IS_AUTHORITATIVE" = 1 ]; then
  git add "$USER_ID/stats-cache.json" "$USER_ID/session-stats.json" 2>/dev/null
else
  git add "$USER_ID/session-stats.json" 2>/dev/null
fi
# Heartbeat: this run got as far as the commit decision. A no-op run (no
# Claude use since the last one) commits nothing, so commit age alone made
# the watchdog cry "stalled" after every quiet stretch (2026-10-26).
# Per-host and untracked (2026-10-29): in the repo it made ~60% of all
# autopush commits, and Syncthing let one Mac's heartbeat mask a stall on
# the other. watchdog.sh reads the same path.
HB_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/ai-dashboard"
mkdir -p "$HB_DIR" && date +%s > "$HB_DIR/periodic-sync.heartbeat"
git diff --cached --quiet && exit 0

git commit -m "stats: $USER_ID periodic $(date '+%Y-%m-%d %H:%M')" -q
git pull --rebase origin main -q 2>/dev/null || true
git push origin main -q 2>/dev/null || true
