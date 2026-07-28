#!/bin/zsh
# Regression test: dtd must AUTO-reload an open picker when the live task cache
# changes on its own (e.g. a /-1g or /0g add elsewhere runs
# `did-fast --refresh-cache`), so a new goal appears without a manual ctrl-r.
# Guards the 2026-06-24 gap where adding a -1g never surfaced in an open dtd
# because reloads only ever read the frozen startup snapshot.
set -e
cd "$(dirname "$0")"
DTD=dtd.sh
fail() { echo "FAIL: $1"; exit 1; }

# ── 1. Structural: the watcher exists and is wired correctly ────────────────
grep -q 'DTD_WATCH_RELOAD' "$DTD"            || fail "no auto-reload watcher (DTD_WATCH_RELOAD) in $DTD"
grep -q 'stat -f %m "\$CACHE"' "$DTD"        || fail "watcher does not poll the LIVE cache mtime"
grep -q 'cp "\$CACHE" "\$DTD_CACHE_FILE"' "$DTD" || fail "watcher does not refresh the frozen snapshot from the live cache"
grep -q 'reload(\$DTD_WATCH_RELOAD)' "$DTD"  || fail "watcher does not POST a reload to fzf"
grep -q 'WATCHER_PID=\$!' "$DTD"             || fail "watcher PID not captured"
grep -q 'kill "\$WATCHER_PID"' "$DTD"        || fail "watcher not killed at cleanup"

# Watcher must launch before the UI loop (so it's live for the whole session).
watch_line=$(grep -n 'WATCHER_PID=\$!' "$DTD" | head -1 | cut -d: -f1)
loop_line=$(grep -n '^while true; do' "$DTD" | head -1 | cut -d: -f1)
[[ -n "$watch_line" && -n "$loop_line" && "$watch_line" -lt "$loop_line" ]] \
  || fail "watcher ($watch_line) must launch before the UI loop ($loop_line)"

# ── 2. Functional: prove the watcher mechanism — on a cache mtime advance it
#       copies live->snapshot and POSTs reload(...) to the fzf port; it stays
#       silent when the cache is unchanged. Uses the SAME constructs as dtd.sh.
TMP=$(mktemp -d)
trap "rm -rf $TMP; [[ -n \$SRV ]] && kill \$SRV 2>/dev/null; [[ -n \$W ]] && kill \$W 2>/dev/null" EXIT
CACHE="$TMP/cache.json"; SNAP="$TMP/snap.json"; PORT_FILE="$TMP/port"; GOT="$TMP/got.txt"
: > "$GOT"
echo '{"v":1}' > "$CACHE"; cp "$CACHE" "$SNAP"

# Mock fzf --listen endpoint: records every POST body, publishes its port.
python3 - "$PORT_FILE" "$GOT" <<'PY' &
import http.server, socketserver, sys
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get('Content-Length', 0))
        open(sys.argv[2], 'ab').write(self.rfile.read(n) + b'\n')
        self.send_response(200); self.end_headers()
    def log_message(self, *a): pass
with socketserver.TCPServer(("localhost", 0), H) as s:
    open(sys.argv[1], 'w').write(str(s.server_address[1]))
    s.serve_forever()
PY
SRV=$!
for _ in {1..30}; do [[ -s "$PORT_FILE" ]] && break; sleep 0.1; done
[[ -s "$PORT_FILE" ]] || fail "mock listener never published a port"

# Watcher loop (mirrors dtd.sh; bounded so the test self-terminates).
(
  last_m=$(stat -f %m "$CACHE" 2>/dev/null)
  for _ in {1..20}; do
    sleep 0.5
    cur_m=$(stat -f %m "$CACHE" 2>/dev/null)
    [[ -z "$cur_m" || "$cur_m" == "$last_m" ]] && continue
    last_m="$cur_m"
    cp "$CACHE" "$SNAP" 2>/dev/null
    port=$(cat "$PORT_FILE" 2>/dev/null)
    [[ -z "$port" ]] && continue
    curl -s -XPOST "localhost:$port" --data "reload(noop)" >/dev/null 2>&1
  done
) &
W=$!

# Quiet period: no cache change -> no POST.
sleep 1.5
[[ ! -s "$GOT" ]] || fail "watcher POSTed without a cache change (should be quiet)"

# Change the live cache (force a new mtime second) -> watcher should react.
sleep 1
echo '{"v":2,"new":"goal"}' > "$CACHE"
for _ in {1..20}; do grep -q 'reload(' "$GOT" 2>/dev/null && break; sleep 0.5; done

grep -q 'reload(' "$GOT" || fail "watcher did not POST a reload after the cache changed"
diff -q "$CACHE" "$SNAP" >/dev/null || fail "watcher did not copy the live cache into the snapshot"

echo "PASS: dtd auto-reload watcher fires on live-cache change"
