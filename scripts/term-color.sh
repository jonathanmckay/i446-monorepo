#!/usr/bin/env bash
# term-color.sh — Change the current Terminal.app tab background color
# Usage: term-color.sh black|blue|green|reset
#
# Uses ps to find the controlling TTY (works even when stdin is a pipe, as in CC hooks).
# Colors:
#   black — AI working / default
#   blue  — waiting for permission or user input
#   green — AI done / session complete

COLOR=${1:-reset}

# Find the controlling terminal by walking up the process tree
TTY_DEVICE=""
PID=$$
for _ in 1 2 3 4 5; do
    T=$(ps -p "$PID" -o tty= 2>/dev/null | tr -d ' ')
    if [ -n "$T" ] && [ "$T" != "?" ]; then
        # macOS ps returns "s001" for /dev/ttys001
        if [[ "$T" == s[0-9]* ]]; then
            TTY_DEVICE="/dev/tty$T"
        elif [[ "$T" == ttys* || "$T" == tty* ]]; then
            TTY_DEVICE="/dev/$T"
        fi
        break
    fi
    PPID_VAL=$(ps -p "$PID" -o ppid= 2>/dev/null | tr -d ' ')
    [ -z "$PPID_VAL" ] && break
    PID=$PPID_VAL
done

[ -z "$TTY_DEVICE" ] && exit 0

# Colors are 16-bit RGB (0–65535) for AppleScript Terminal (8-bit × 257)
case "$COLOR" in
  black) R=2570;  G=2570;  B=2570  ;;  # Abyss       #0a0a0a
  blue)  R=3341;  G=15163; B=26214 ;;  # Deep Sea    #0d3b66
  green) R=6939;  G=24158; B=8224  ;;  # Emerald Shadow #1b5e20
  red)    R=39835; G=0;     B=8995  ;;  # Velvet      #9b0023
  orange) R=65535; G=28013; B=0     ;;  # Lava        #ff6d00
  reset) R=2570;  G=2570;  B=2570  ;;  # Abyss
  *) exit 1 ;;
esac

osascript <<APPLESCRIPT 2>/dev/null
tell application "Terminal"
  repeat with w in windows
    repeat with t in tabs of w
      if tty of t is "$TTY_DEVICE" then
        set background color of t to {$R, $G, $B}
        exit repeat
      end if
    end repeat
  end repeat
end tell
APPLESCRIPT
