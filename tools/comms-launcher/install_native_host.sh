#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <chrome_extension_id>"
  exit 1
fi

EXTENSION_ID="$1"
HOST_NAME="com.jonathanmckay.comms_launcher"
HOST_DIR="$HOME/Library/Application Support/Google/Chrome/NativeMessagingHosts"
HOST_MANIFEST_PATH="$HOST_DIR/$HOST_NAME.json"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOST_BINARY_PATH="$SCRIPT_DIR/native-host/comms_launcher_host.py"

mkdir -p "$HOST_DIR"

cat > "$HOST_MANIFEST_PATH" <<JSON
{
  "name": "$HOST_NAME",
  "description": "Comms launcher native host",
  "path": "$HOST_BINARY_PATH",
  "type": "stdio",
  "allowed_origins": [
    "chrome-extension://$EXTENSION_ID/"
  ]
}
JSON

chmod 644 "$HOST_MANIFEST_PATH"

cat <<DONE
Installed native host manifest:
$HOST_MANIFEST_PATH

Next:
1) Reload the extension in chrome://extensions
2) Click the extension button and press Launch
DONE
