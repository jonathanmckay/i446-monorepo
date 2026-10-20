#!/bin/bash
# audio-switch.sh — Switch macOS audio output device.
# Usage:
#   audio-switch.sh meet    # Switch to "Meet Output" (BlackHole + speakers)
#   audio-switch.sh default # Switch back to default speakers
#
# Requires: switchaudio-osx (brew install switchaudio-osx)

set -u

case "${1:-}" in
  meet)
    SwitchAudioSource -s "Multi-Output Device" -t output 2>/dev/null \
      && echo "Audio output → Multi-Output Device" \
      || echo "WARN: 'Multi-Output Device' not found. Run Audio MIDI Setup to create it."
    ;;
  default|reset)
    SwitchAudioSource -s "MacBook Pro Speakers" -t output 2>/dev/null \
      || SwitchAudioSource -s "Built-in Output" -t output 2>/dev/null \
      || echo "WARN: Could not switch to default speakers"
    echo "Audio output → default speakers"
    ;;
  *)
    echo "Usage: audio-switch.sh [meet|default]"
    exit 1
    ;;
esac
