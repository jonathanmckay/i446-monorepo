#!/usr/bin/env bash
# Test loop: restore the countersign email, run ibx, capture errors,
# ask Claude to fix, and retry until signing succeeds.

SCRIPT="$HOME/i446-monorepo/tools/ibx/ibx_all.py"
EMAIL_ID="19d6a42edc060e83"
MAX_ATTEMPTS=10
ERR_FILE=$(mktemp /tmp/autosign_test.XXXXXX)

cleanup() { rm -f "$ERR_FILE"; }
trap cleanup EXIT

restore_email() {
    python3 -c "
import sys; sys.path.insert(0, '$HOME/i446-monorepo/tools/ibx')
import ibx as _ibx
for acct in _ibx.ACCOUNTS:
    if acct['name'] != 'm5c7': continue
    svc = _ibx.get_gmail_service(acct['tokens'], acct['creds'])
    svc.users().messages().modify(userId='me', id='$EMAIL_ID',
        body={'addLabelIds': ['INBOX']}).execute()
    print('  ↩ email restored to inbox')
"
}

check_success() {
    python3 -c "
import sqlite3, pathlib
db = pathlib.Path.home() / 'vault/m5x2/automations.db'
conn = sqlite3.connect(str(db))
row = conn.execute(\"SELECT status FROM lease_signings ORDER BY signed_at DESC LIMIT 1\").fetchone()
conn.close()
if row and row[0] == 'success':
    print('SUCCESS')
else:
    print('FAILED')
"
}

for attempt in $(seq 1 $MAX_ATTEMPTS); do
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  Attempt $attempt/$MAX_ATTEMPTS"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    restore_email

    echo "  Running ibx..."
    python3 "$SCRIPT" 2>"$ERR_FILE"
    EXIT_CODE=$?

    # Check DB for success
    RESULT=$(check_success)
    if [[ "$RESULT" == "SUCCESS" ]]; then
        echo ""
        echo "✅ Lease signed successfully on attempt $attempt!"
        exit 0
    fi

    ERROR=$(cat "$ERR_FILE")
    if [[ -z "$ERROR" ]]; then
        # Check stdout for inline error messages
        echo "  No stderr — checking DB status..."
    fi

    if [[ -n "$ERROR" ]]; then
        echo ""
        echo "  Error on attempt $attempt:"
        echo "$ERROR" | head -20
        echo ""
        echo "  Asking Claude to fix..."
        claude -p "Fix this error in the lease auto-signing code. The relevant files are:
- ~/i446-monorepo/tools/m5x2-automations/lease_signer.py (CUA signing logic)
- ~/i446-monorepo/tools/ibx/ibx_all.py (_autosign_item function around line 199)

Error:
$ERROR" --allowedTools "Read,Edit,Grep"
        echo ""
        echo "  Fix applied. Retrying..."
    fi

    sleep 2
done

echo ""
echo "❌ Failed after $MAX_ATTEMPTS attempts."
exit 1
