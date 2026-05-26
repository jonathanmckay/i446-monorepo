#!/bin/bash
# Archive .wav files that have a matching .txt transcript.
# Moves them to vault/z_old/wav-archive/ to reduce Syncthing bloat.
# Intended to run on a cron (e.g. daily).

VAULT="$HOME/vault"
ARCHIVE="$VAULT/z_old/wav-archive"

mkdir -p "$ARCHIVE"

count=0
while IFS= read -r -d '' wav; do
    txt="${wav%.wav}.txt"
    if [[ -f "$txt" ]]; then
        mv "$wav" "$ARCHIVE/"
        count=$((count + 1))
    fi
done < <(find "$VAULT" -path "$VAULT/z_old" -prune -o -name "*.wav" -print0)

if [[ $count -gt 0 ]]; then
    echo "archived $count .wav files to $ARCHIVE"
fi
