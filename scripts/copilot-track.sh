#!/bin/bash
# Wrapper around gh copilot that logs each invocation to llm-sessions.db
# Replaces the ?? and suggest aliases in .zshrc

START=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
CMD_TYPE="$1"
shift

gh copilot "$CMD_TYPE" "$@"
EXIT_CODE=$?

END=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

python3 - <<PYEOF
import uuid, sqlite3
DB = "$HOME/vault/i447/i446/llm-sessions.db"
sid = "copilot-" + uuid.uuid4().hex[:8]
try:
    conn = sqlite3.connect(DB)
    conn.execute(
        "INSERT INTO sessions "
        "(session_id, provider, product, model, start_time, end_time, message_count, status, user_id) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (sid, "copilot", "cli", "copilot-cli", "$START", "$END", 1, "completed", "jm")
    )
    conn.commit()
    conn.close()
except Exception as e:
    pass  # never block the user
PYEOF

exit $EXIT_CODE
