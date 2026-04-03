#!/usr/bin/env bash
# term-color.sh — Change the current Terminal.app tab background color
# Usage: term-color.sh black|blue|green|reset
#
# Uses ps to find the controlling TTY (works even when stdin is a pipe, as in CC hooks).
# Colors:
#   black — AI working / processing
#   purple — "prayer" phase (user prompt submitted, AI thinking; auto-reverts to black after 10s)
#   green — waiting for user permission
#   orange — tool use failed (non-fatal)
#   blue  — AI done / task complete
#   red   — AI stopped with failure
#   gray  — idle terminal (no AI session active)

COLOR=${1:-reset}

# Find the Terminal.app tab TTY by walking up the process tree.
# Tools like Copilot CLI allocate their own PTY, so the immediate tty may not
# match any Terminal tab. We collect all unique ttys seen while walking up and
# then pick the one that belongs to a Terminal.app tab via a single osascript.
SEEN_TTYS=()
PID=$$
for _ in $(seq 1 20); do
    T=$(ps -p "$PID" -o tty= 2>/dev/null | tr -d ' ')
    if [ -n "$T" ] && [ "$T" != "??" ] && [ "$T" != "?" ]; then
        if [[ "$T" == s[0-9]* ]]; then
            CANDIDATE="/dev/tty$T"
        elif [[ "$T" == ttys* || "$T" == tty* ]]; then
            CANDIDATE="/dev/$T"
        else
            CANDIDATE=""
        fi
        if [ -n "$CANDIDATE" ]; then
            # Avoid duplicates
            local_dup=0
            for seen in "${SEEN_TTYS[@]}"; do
                [ "$seen" = "$CANDIDATE" ] && { local_dup=1; break; }
            done
            [ "$local_dup" = "0" ] && SEEN_TTYS+=("$CANDIDATE")
        fi
    fi
    PPID_VAL=$(ps -p "$PID" -o ppid= 2>/dev/null | tr -d ' ')
    [ -z "$PPID_VAL" ] || [ "$PPID_VAL" = "0" ] || [ "$PPID_VAL" = "1" ] && break
    PID=$PPID_VAL
done

# If only one TTY seen, use it directly (fast path for Claude Code).
# If multiple, ask Terminal.app which one is a real tab.
TTY_DEVICE=""
if [ "${#SEEN_TTYS[@]}" -eq 1 ]; then
    TTY_DEVICE="${SEEN_TTYS[0]}"
elif [ "${#SEEN_TTYS[@]}" -gt 1 ]; then
    KNOWN_TTYS=$(osascript -e '
tell application "Terminal"
  set ttys to {}
  repeat with w in windows
    repeat with t in tabs of w
      set end of ttys to (tty of t)
    end repeat
  end repeat
  return ttys
end tell' 2>/dev/null)
    for candidate in "${SEEN_TTYS[@]}"; do
        if echo "$KNOWN_TTYS" | grep -qF "$candidate"; then
            TTY_DEVICE="$candidate"
        fi
    done
    # Fallback to last seen (highest in tree)
    [ -z "$TTY_DEVICE" ] && TTY_DEVICE="${SEEN_TTYS[-1]}"
fi

[ -z "$TTY_DEVICE" ] && exit 0

# Colors are 16-bit RGB (0–65535) for AppleScript Terminal (8-bit × 257)
case "$COLOR" in
  black)  R=2570;  G=2570;  B=2570  ;;  # Abyss       #0a0a0a
  blue)   R=3341;  G=15163; B=26214 ;;  # Deep Sea    #0d3b66
  green)  R=6939;  G=24158; B=8224  ;;  # Emerald Shadow #1b5e20
  red)    R=39835; G=0;     B=8995  ;;  # Velvet      #9b0023
  orange) R=65535; G=28013; B=0     ;;  # Lava        #ff6d00
  purple) R=15677; G=0;     B=26214 ;;  # Void        #3d0066
  gray)   R=9252;  G=9252;  B=9766  ;;  # Slate       #242426
  reset)  R=2570;  G=2570;  B=2570  ;;  # Abyss
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
