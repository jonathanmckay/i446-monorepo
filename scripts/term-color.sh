#!/usr/bin/env bash
# term-color.sh — Change the current terminal tab background color
# Usage: term-color.sh black|blue|green|reset
#
# Strategy:
#   1. Walk the process tree to find the controlling TTY device.
#   2. If in tmux (inside Terminal.app), use the tmux client's outer TTY.
#   3. Try Terminal.app AppleScript (sets window chrome color).
#   4. Fall back to OSC 11 escape sequence written directly to the TTY
#      (works for Ghostty/cmux and any other OSC 11-capable terminal).
#
# Colors:
#   black — AI working / processing
#   purple — "prayer" phase (user prompt submitted, AI thinking; auto-reverts to black after 10s)
#   green — waiting for user permission
#   orange — tool use failed (non-fatal)
#   blue  — AI done / task complete
#   red   — AI stopped with failure
#   gray  — idle terminal (no AI session active)

COLOR=${1:-reset}

# ── Step 1: resolve TTY ───────────────────────────────────────────────────────

TTY_DEVICE=""

# In standard tmux (inside Terminal.app), inner pane TTYs are unknown to
# Terminal.app. Use the tmux client's outer TTY instead.
if [ -n "$TMUX" ]; then
    CLIENT_TTY=$(tmux display-message -p '#{client_tty}' 2>/dev/null)
    [ -n "$CLIENT_TTY" ] && TTY_DEVICE="$CLIENT_TTY"
fi

# Walk up the process tree collecting unique TTY devices.
if [ -z "$TTY_DEVICE" ]; then
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
        [ -z "$TTY_DEVICE" ] && TTY_DEVICE="${SEEN_TTYS[-1]}"
    fi
fi

[ -z "$TTY_DEVICE" ] && exit 0

# ── Step 2: color values ──────────────────────────────────────────────────────

# 16-bit RGB for Terminal.app AppleScript (8-bit × 257)
# Hex pairs for OSC 11 escape sequence (8-bit)
case "$COLOR" in
  black)  R=2570;  G=2570;  B=2570;  RH=0a; GH=0a; BH=0a ;;  # #0a0a0a
  blue)   R=3341;  G=15163; B=26214; RH=0d; GH=3b; BH=66 ;;  # #0d3b66
  green)  R=6939;  G=24158; B=8224;  RH=1b; GH=5e; BH=20 ;;  # #1b5e20
  red)    R=39835; G=0;     B=8995;  RH=9b; GH=00; BH=23 ;;  # #9b0023
  orange) R=65535; G=28013; B=0;     RH=ff; GH=6d; BH=00 ;;  # #ff6d00
  purple) R=15677; G=0;     B=26214; RH=3d; GH=00; BH=66 ;;  # #3d0066
  gray)   R=9252;  G=9252;  B=9766;  RH=24; GH=24; BH=26 ;;  # #242426
  reset)  R=2570;  G=2570;  B=2570;  RH=0a; GH=0a; BH=0a ;;  # #0a0a0a
  *) exit 1 ;;
esac

# ── Step 3: Terminal.app (sets window chrome) ─────────────────────────────────

TERM_FOUND=$(osascript <<APPLESCRIPT 2>/dev/null
tell application "Terminal"
  repeat with w in windows
    repeat with t in tabs of w
      if tty of t is "$TTY_DEVICE" then
        set background color of t to {$R, $G, $B}
        return "found"
      end if
    end repeat
  end repeat
  return "not-found"
end tell
APPLESCRIPT
)

# ── Step 4: OSC 11 fallback (Ghostty/cmux and other terminals) ───────────────

if [ "$TERM_FOUND" != "found" ] && [ -w "$TTY_DEVICE" ]; then
    printf '\033]11;rgb:%s/%s/%s\a' "$RH" "$GH" "$BH" > "$TTY_DEVICE"
fi
