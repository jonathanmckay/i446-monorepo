#!/usr/bin/env python3
import json
import os
import struct
import subprocess
import sys


def read_message():
    raw_length = sys.stdin.buffer.read(4)
    if not raw_length:
        return None
    message_length = struct.unpack("<I", raw_length)[0]
    message = sys.stdin.buffer.read(message_length).decode("utf-8")
    return json.loads(message)


def send_message(message):
    encoded = json.dumps(message).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(encoded)))
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


def main():
    message = read_message()
    if message is None:
      return

    script_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "open_comms_channels.sh"))

    if not os.path.isfile(script_path):
        send_message({"ok": False, "error": f"Launcher script not found: {script_path}"})
        return

    try:
        result = subprocess.run(
            ["/bin/bash", script_path],
            capture_output=True,
            text=True,
            check=True,
        )
        send_message({"ok": True, "stdout": result.stdout})
    except subprocess.CalledProcessError as exc:
        send_message({"ok": False, "error": exc.stderr or exc.stdout or "Launcher failed"})


if __name__ == "__main__":
    main()
