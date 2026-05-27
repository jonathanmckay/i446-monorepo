#!/usr/bin/env python3
"""
meet.py — Record a meeting, transcribe, extract notes + todos, file to vault.

Usage:
    python3 meet.py "1:1 with Ashish"        # mic + system audio (default)
    python3 meet.py "standup" --no-teams     # mic only (in-person, no call)
    python3 meet.py "standup" --no-todos      # skip Todoist
    python3 meet.py "retro" --tx notes.txt    # use existing transcript
    python3 meet.py --devices                 # list available audio devices

Teams/Zoom setup (one-time):
    1. brew install blackhole-2ch
    2. Open Audio MIDI Setup → Create Multi-Output Device:
          ✓ BlackHole 2ch
          ✓ MacBook Pro Speakers (or headphones)
       Name it "Meet Output"
    3. Before a call: set System Output → "Meet Output"
    4. Run: python3 meet.py "meeting name"
    The script records both BlackHole (their audio) + mic (your audio) mixed together.
"""

import os
import sys
import wave
import json
import signal
import tempfile
import argparse
import urllib.request
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import subprocess as _sp

import numpy as np
import sounddevice as sd
import anthropic


_TERM_COLOR = os.path.expanduser("~/i446-monorepo/scripts/term-color.sh")
_IMESSAGE_RECIPIENT = "jonathan.b.mckay@gmail.com"


def _notify(title, body, critical=False):
    """Alert the user via auto-dismissing dialog, terminal color, and iMessage.

    - Always: osascript display dialog (auto-dismisses after 10s)
    - Always: terminal tab → orange
    - critical=True: also send iMessage to self
    """
    escaped_body = body.replace('"', '\\"').replace('\n', '\\n')
    escaped_title = title.replace('"', '\\"')

    # 1. Notification Center alert (non-blocking, visible even when Terminal is hidden)
    try:
        _sp.run([
            "osascript", "-e",
            f'display notification "{escaped_body}" with title "{escaped_title}" sound name "Funk"'
        ], timeout=5, capture_output=True)
    except Exception:
        pass

    # 2. Auto-dismissing dialog (gives up after 10s, never blocks forever)
    try:
        _sp.run([
            "osascript", "-e",
            f'display dialog "{escaped_title}: {escaped_body}" buttons {{"OK"}} default button "OK" giving up after 10'
        ], timeout=15, capture_output=True)
    except Exception:
        pass

    # 3. Terminal tab → orange
    try:
        _sp.run([_TERM_COLOR, "orange"], timeout=5, capture_output=True)
    except Exception:
        pass

    # 4. iMessage to self (critical alerts only, e.g. auto-stop)
    if critical:
        try:
            msg = f"{title}: {body}"
            _sp.run([
                "osascript", "-e",
                f'tell application "Messages" to send "{msg}" to buddy "{_IMESSAGE_RECIPIENT}" of (first account whose service type is iMessage)'
            ], timeout=10, capture_output=True)
        except Exception:
            pass
from typing import Optional

SAMPLE_RATE = 16000   # Whisper works best at 16kHz
CHANNELS = 1
TODOIST_TOKEN = "7eb82f47aba8b334769351368e4e3e3284f980e5"
VAULT_DIR = Path.home() / "vault"
WHISPER_MODEL = "base.en"  # fast, English-only. Use "small.en" for more accuracy.
SPEECH_THRESH = 400

# Domain → Todoist label + vault subfolder
DOMAIN_MAP = {
    "i9":   {"label": "i9",   "folder": "h335/i9"},
    "m5x2": {"label": "m5x2", "folder": "h335/m5x2"},
    "s897": {"label": "s897", "folder": "s897"},
    "g245": {"label": "g245", "folder": "g245"},
    "xk87": {"label": "xk87", "folder": "xk87"},
    "xk88": {"label": "xk88", "folder": "xk88"},
    "d357": {"label": "d357", "folder": "d357"},
}
DEFAULT_DOMAIN = "i9"


# ── Recording ────────────────────────────────────────────────────────────────

def find_device(name_fragment: str) -> Optional[int]:
    for i, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0 and name_fragment.lower() in dev["name"].lower():
            return i
    return None


def list_devices():
    print("\nAvailable input devices:")
    for i, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0:
            print(f"  [{i:2d}] {dev['name']}")
    print()


class GracefulStop:
    """Turn SIGINT/SIGTERM into one graceful recording stop request."""

    def __init__(self):
        self.requested = False
        self.count = 0
        self._previous = {}

    def _handle(self, sig, frame):
        self.count += 1
        name = signal.Signals(sig).name
        if not self.requested:
            print(f"\n⏹  {name} received — stopping recording and preserving audio...")
            self.requested = True
        else:
            print(f"\n⏳  {name} received again — already stopping; preserving recording...")

    def __enter__(self):
        for sig in (signal.SIGINT, signal.SIGTERM):
            self._previous[sig] = signal.getsignal(sig)
            signal.signal(sig, self._handle)
        return self

    def __exit__(self, exc_type, exc, tb):
        for sig, handler in self._previous.items():
            signal.signal(sig, handler)
        return False


@contextmanager
def defer_termination(reason: str):
    """Temporarily defer stop signals while writing irreplaceable artifacts."""
    received = []
    previous = {}

    def _handle(sig, frame):
        received.append(sig)
        name = signal.Signals(sig).name
        print(f"\n⏳  {name} received during {reason}; deferring until file write completes.")

    for sig in (signal.SIGINT, signal.SIGTERM):
        previous[sig] = signal.getsignal(sig)
        signal.signal(sig, _handle)
    try:
        yield received
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        if received:
            names = ", ".join(signal.Signals(sig).name for sig in received)
            print(f"   Deferred {names}; artifact preserved.")


def airpods_hfp_active() -> bool:
    """Return True when AirPods output is in mono/24kHz HFP mode."""
    try:
        for dev in sd.query_devices():
            name = str(dev.get("name", ""))
            if "airpods" not in name.lower() or dev.get("max_output_channels", 0) <= 0:
                continue
            channels = int(dev.get("max_output_channels") or 0)
            rate = float(dev.get("default_samplerate") or 0)
            if channels == 1 and rate <= 24000:
                return True
    except Exception:
        return False
    return False


def speech_ratio(audio: np.ndarray) -> float:
    if audio is None or len(audio) == 0:
        return 0.0
    return float(np.mean(np.abs(audio) > SPEECH_THRESH))


def capture_quality_warnings(audio: np.ndarray, transcript: str) -> list[str]:
    """Flag recordings likely to be silence, one-sided, or Whisper hallucination."""
    duration = len(audio) / SAMPLE_RATE if audio is not None else 0
    words = len(transcript.split())
    wpm = (words / duration * 60) if duration else 0
    ratio = speech_ratio(audio)
    warnings = []
    if duration >= 10 and words < 20:
        warnings.append(f"transcript too short: {words} words over {duration:.0f}s")
    if duration >= 300 and wpm < 40:
        warnings.append(f"low word rate: {wpm:.0f} wpm over {duration / 60:.1f}min")
    if duration >= 60 and ratio < 0.005:
        warnings.append(f"low speech signal: {ratio:.2%} samples above speech threshold")
    return warnings


def record_audio(teams_mode: bool = False, max_duration: int = 0,
                  idle_timeout: int = 600, mic_name: str = None) -> np.ndarray:
    """Record audio until Ctrl+C, max_duration, or idle timeout.

    teams_mode=True: mix BlackHole (system/Teams audio) + mic.
    teams_mode=False: mic only (default).
    max_duration: auto-stop after this many seconds (0 = no limit).
    idle_timeout: auto-stop after this many seconds of silence once
                  conversation has been detected (default 600s = 10min).
    mic_name: override mic device (name fragment). Default: MacBook Pro Microphone.
    """
    mic_frames: list = []
    bh_frames: list = []
    streams = []

    if teams_mode and airpods_hfp_active():
        msg = "AirPods are in HFP mode (mono/24kHz); BlackHole routing will fail. Recording mic-only."
        print(f"⚠  {msg}")
        _notify("⚠ Recording: AirPods HFP", msg, critical=True)
        teams_mode = False
        idle_timeout = 0

    # Mic stream
    if mic_name:
        mic_idx = find_device(mic_name)
        if mic_idx is None:
            print(f"⚠  Mic '{mic_name}' not found, falling back to default")
            mic_idx = find_device("MacBook Pro Microphone") or find_device("Built-in Microphone")
    else:
        mic_idx = find_device("MacBook Pro Microphone") or find_device("Built-in Microphone")
    def mic_cb(indata, fc, ti, st):
        mic_frames.append(indata.copy())
    streams.append(sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS,
                                  dtype="int16", device=mic_idx, callback=mic_cb))
    print(f"🎙  Mic: {sd.query_devices(mic_idx)['name'] if mic_idx is not None else 'default'}")

    # System audio stream (Teams/Zoom)
    if teams_mode:
        # Prefer BlackHole (reliable via Multi-Output Device); Teams Audio is unreliable
        sys_idx = find_device("BlackHole") or find_device("Microsoft Teams Audio")
        if sys_idx is not None:
            sys_name = sd.query_devices(sys_idx)["name"]
            def sys_cb(indata, fc, ti, st):
                bh_frames.append(indata.copy())
            streams.append(sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS,
                                          dtype="int16", device=sys_idx, callback=sys_cb))
            print(f"🖥  Call audio: {sys_name}")
        else:
            print("⚠  No call audio device found (tried Teams Audio, BlackHole) — mic only.")
            teams_mode = False
            idle_timeout = 0

    # Silence / one-sided detection
    onesided_warned = False
    last_teams_check = 0
    teams_warn_count = 0
    SILENCE_THRESH = 500       # int16 amplitude below this = silence
    CHECK_INTERVAL_MIC = 120   # seconds before first check (mic-only mode)
    CHECK_INTERVAL_BOTH = 60   # seconds between teams-mode checks
    SILENCE_RATIO_WARN = 0.55  # warn if >55% of audio is silence
    SPEECH_RATIO_MIN = 0.01    # need at least 1% of samples above speech threshold

    def _has_speech(frames, window_secs=60):
        """Check if the last N seconds of frames contain actual speech."""
        if not frames:
            return False
        samples_needed = SAMPLE_RATE * window_secs
        recent = np.concatenate(frames[-samples_needed:], axis=0) if len(frames) > 1 else frames[-1]
        if len(recent) > samples_needed:
            recent = recent[-samples_needed:]
        speech_ratio = np.mean(np.abs(recent) > SPEECH_THRESH)
        return speech_ratio >= SPEECH_RATIO_MIN

    # Auto-stop state
    conversation_detected = False  # True once we've seen speech
    idle_since = 0.0              # elapsed time when silence started (after conversation)

    early_check_done = False  # quick 15s check for call audio signal

    if max_duration:
        print(f"   Auto-stop after {max_duration // 60}min (calendar duration)")
    if idle_timeout:
        print(f"   Auto-stop after {idle_timeout // 60}min of silence (post-conversation)")
    print("\nRecording... press Ctrl+C to stop\n")
    for s in streams:
        s.start()
    with GracefulStop() as stop:
        elapsed = 0
        while not stop.requested:
            sd.sleep(500)
            elapsed += 0.5

            # Early check (15s): if call audio has zero signal, warn immediately
            if teams_mode and not early_check_done and elapsed >= 15 and bh_frames:
                early_check_done = True
                bh_peak = int(np.max(np.abs(np.concatenate(bh_frames, axis=0))))
                if bh_peak == 0:
                    print("⚠  Call audio device has zero signal at 15s.")
                    print("   Will auto-fallback to mic-only if still silent at 3min.")

            # Auto-stop: max duration reached
            if max_duration and elapsed >= max_duration:
                msg = f"Auto-stopped after {int(elapsed // 60)}min (calendar duration)"
                print(f"\n⏹  {msg}")
                _notify("Recording Auto-Stopped", msg)
                break

            # Teams mode: check every 60s (repeating, not one-shot)
            if (teams_mode and elapsed >= CHECK_INTERVAL_BOTH
                    and elapsed - last_teams_check >= CHECK_INTERVAL_BOTH):
                last_teams_check = elapsed
                mic_has_speech = _has_speech(mic_frames)
                bh_has_speech = _has_speech(bh_frames)
                any_speech = mic_has_speech or bh_has_speech

                # Track conversation state for idle auto-stop
                if any_speech:
                    conversation_detected = True
                    idle_since = 0.0  # reset idle timer
                elif conversation_detected:
                    if idle_since == 0.0:
                        idle_since = elapsed  # start idle timer
                    elif idle_timeout and (elapsed - idle_since) >= idle_timeout:
                        msg = f"Auto-stopped: {int(idle_timeout // 60)}min silence after conversation ended"
                        print(f"\n⏹  {msg}")
                        _notify("Recording Auto-Stopped", msg)
                        break

                # Fallback: mic works but call audio is dead after 3 min
                # Instead of stopping, drop the dead system audio stream and
                # continue with mic-only. The mic picks up both sides (speaker
                # bleed or AirPods passthrough) at lower quality.
                if mic_has_speech and not bh_has_speech and elapsed >= 180:
                    teams_warn_count += 1
                    if teams_warn_count >= 2:
                        msg = "Call audio silent — falling back to mic-only recording."
                        print(f"\n⚠  {msg}")
                        _notify("⚠ Recording: mic-only fallback", msg)
                        # Stop and discard the system audio stream
                        for s in streams:
                            if hasattr(s, 'device') or True:
                                pass  # keep all streams running but ignore bh_frames
                        bh_frames.clear()
                        teams_mode = False  # switch checks to mic-only mode
                        idle_timeout = 0
                        teams_warn_count = 0  # reset so we don't re-trigger
                        continue

                if not mic_has_speech and not bh_has_speech:
                    teams_warn_count += 1
                    msg = f"NO SPEECH on either channel ({int(elapsed)}s, #{teams_warn_count})"
                    print(f"\n⚠  {msg}")
                    print("   Both mic and system audio lack speech.")
                    print("   Check: is the call connected? Is system output set to 'Meet Output'?\n")
                    _notify("⚠ Recording Problem", msg)
                elif mic_has_speech and not bh_has_speech:
                    teams_warn_count += 1
                    msg = f"CALL AUDIO HAS NO SPEECH ({int(elapsed)}s, #{teams_warn_count})"
                    print(f"\n⚠  {msg}")
                    print("   Your mic is picking up speech, but system audio is not.")
                    print("   Check: system output → 'Meet Output'? Is the other person talking?\n")
                    _notify("⚠ Recording Problem", msg)
                elif not mic_has_speech and bh_has_speech:
                    teams_warn_count += 1
                    msg = f"MIC HAS NO SPEECH ({int(elapsed)}s, #{teams_warn_count})"
                    print(f"\n⚠  {msg}")
                    print("   Call audio has speech, but your mic does not.")
                    print("   Check your mic input device.\n")
                    _notify("⚠ Recording Problem", msg)
                else:
                    if teams_warn_count > 0:
                        print(f"\n✓  Both channels now have speech ({int(elapsed)}s). Recording looks good.\n")
                        teams_warn_count = 0

            # Mic-only mode: after 2 min, check for one-sided audio
            if (not teams_mode and not onesided_warned
                    and elapsed >= CHECK_INTERVAL_MIC and mic_frames):
                audio_so_far = np.concatenate(mic_frames, axis=0)
                silent = np.mean(np.abs(audio_so_far) < SILENCE_THRESH)
                if silent > SILENCE_RATIO_WARN:
                    print(f"\n⚠  ONE-SIDED AUDIO DETECTED ({silent:.0%} silence)")
                    print("   Only your mic is being captured.")
                    print("   Fix Teams/audio routing, then restart without --no-teams to capture both sides.\n")
                    onesided_warned = True
    for s in streams:
        s.stop()
        s.close()

    def concat(frames):
        return np.concatenate(frames, axis=0) if frames else None

    mic_audio = concat(mic_frames)
    bh_audio = concat(bh_frames)

    if mic_audio is None and bh_audio is None:
        print("No audio captured.")
        sys.exit(1)

    if mic_audio is not None and bh_audio is not None:
        min_len = min(len(mic_audio), len(bh_audio))
        mixed = mic_audio[:min_len].astype(np.int32) + bh_audio[:min_len].astype(np.int32)
        audio = np.clip(mixed, -32768, 32767).astype(np.int16)
    else:
        audio = mic_audio if mic_audio is not None else bh_audio

    duration = len(audio) / SAMPLE_RATE
    print(f"\n⏹  Stopped. {duration:.0f}s recorded.")
    return audio


def save_wav(audio: np.ndarray, path: Path):
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)  # int16 = 2 bytes
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio.tobytes())


# ── Transcription ─────────────────────────────────────────────────────────────

def transcribe(wav_path: Path, model_name: str = WHISPER_MODEL) -> str:
    print("📝 Transcribing (first run downloads ~150MB model)...")
    from faster_whisper import WhisperModel
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        str(wav_path),
        beam_size=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
    )
    text = " ".join(seg.text.strip() for seg in segments)
    print(f"   {len(text.split())} words transcribed.")
    return text


# ── One-sided detection ──────────────────────────────────────────────────────

FILLER_WORDS = {
    "mm-hmm", "mmhmm", "mm", "hmm", "yep", "yeah", "yup", "uh-huh",
    "uh", "um", "ok", "okay", "right", "sure", "yes", "no", "mhm",
}


def check_one_sided(transcript: str, threshold: float = 0.40) -> Optional[int]:
    """Detect if transcript is one-sided (only one person's audio captured).

    Splits transcript into chunks by sentence-ish boundaries. If >threshold
    of chunks are pure filler (mm-hmm, yep, yeah, etc.), the transcript is
    likely one-sided. Returns filler percentage if one-sided, None otherwise.
    """
    import re
    # Split on sentence boundaries (period, question mark, or long pauses
    # that Whisper renders as capitalized starts)
    chunks = re.split(r'(?<=[.?!])\s+|(?<=\w)\.\s+', transcript)
    # Further split on capitalized starts mid-text
    refined = []
    for chunk in chunks:
        parts = re.split(r'(?<=[a-z])\s+(?=[A-Z])', chunk)
        refined.extend(parts)

    if len(refined) < 10:
        return None  # too short to judge

    filler_count = 0
    for chunk in refined:
        words = set(chunk.strip().lower().rstrip(".!?,").split())
        if words and words.issubset(FILLER_WORDS):
            filler_count += 1

    pct = round(100 * filler_count / len(refined))
    return pct if pct >= int(threshold * 100) else None


# ── Extraction ────────────────────────────────────────────────────────────────

EXTRACT_PROMPT = """You are processing a meeting transcript for Jonathan McKay.

Meeting name: {meeting_name}

Transcript:
{transcript}

Extract structured meeting notes. Return ONLY a JSON object, no other text:
{{
  "title": "concise title (≤60 chars)",
  "attendees": ["first last", ...],
  "summary": "2-3 sentence summary of what was discussed and decided",
  "key_points": ["point 1", "point 2", ...],
  "decisions": ["decision 1", ...],
  "todos": [
    {{
      "task": "specific, actionable task",
      "owner": "jm (Jonathan) | name | null",
      "due_hint": "today | this week | next week | null",
      "domain": "i9 | m5x2 | s897 | g245 | xk87 | xk88 | null"
    }}
  ],
  "followup_email": "draft followup email to send to attendees, or null if not needed"
}}

Rules:
- todos.owner = "jm" if the action is Jonathan's
- todos.domain: i9=work/Microsoft, m5x2=real estate, s897=social, g245=personal goals
- Only include todos that are real commitments, not passing mentions
- followup_email: include if there were clear decisions or action items worth summarizing
"""


def extract_meeting_data(transcript: str, meeting_name: str) -> dict:
    print("🤖 Extracting notes and todos...")
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": EXTRACT_PROMPT.format(
                meeting_name=meeting_name,
                transcript=transcript
            )
        }]
    )
    raw = msg.content[0].text.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:])
        if raw.endswith("```"):
            raw = raw[:-3]
    return json.loads(raw)


# ── Filing ────────────────────────────────────────────────────────────────────

def write_meeting_note(data: dict, transcript: str, meeting_name: str,
                       domain: str) -> Path:
    date = datetime.now().strftime("%Y.%m.%d")
    slug = (
        meeting_name.lower()
        .replace(" ", "-")
        .replace("/", "-")
        .replace(":", "")
        .replace("'", "")
    )[:50]

    folder = VAULT_DIR / DOMAIN_MAP.get(domain, DOMAIN_MAP[DEFAULT_DOMAIN])["folder"]
    folder.mkdir(parents=True, exist_ok=True)
    filepath = folder / f"{date}-{slug}.md"

    attendees_yaml = ", ".join(data.get("attendees", []))
    key_points = "\n".join(f"- {p}" for p in data.get("key_points", [])) or "None"
    decisions = "\n".join(f"- {d}" for d in data.get("decisions", [])) or "None"
    todos = "\n".join(
        f"- [ ] {t['task']}" + (f" ({t['owner']})" if t.get("owner") and t["owner"] != "null" else "")
        for t in data.get("todos", [])
    ) or "None"

    content = f"""---
title: "{data.get('title', meeting_name)}"
date: {date}
type: meeting
tags: [{domain}, meeting]
attendees: [{attendees_yaml}]
source: meet.py
---

# {data.get('title', meeting_name)}

## Summary
{data.get('summary', '')}

## Key Points
{key_points}

## Decisions
{decisions}

## Action Items
{todos}
"""

    followup = data.get("followup_email")
    if followup and followup != "null":
        content += f"\n## Followup Email Draft\n{followup}\n"

    content += f"\n---\n\n## Raw Transcript\n\n{transcript}\n"

    filepath.write_text(content)
    print(f"📁 Filed: {filepath}")
    return filepath


# ── Todoist ───────────────────────────────────────────────────────────────────

DUE_MAP = {
    "today": "today",
    "this week": "this week",
    "next week": "next week",
}

OWNER_JM = {"jm", "jonathan", "jonathan mckay", "me", "i", ""}


def create_todoist_tasks(todos: list, default_domain: str):
    jm_todos = [
        t for t in todos
        if str(t.get("owner", "")).lower().strip() in OWNER_JM
    ]
    if not jm_todos:
        print("   No JM-owned todos to create.")
        return

    print(f"\n📋 Creating {len(jm_todos)} Todoist tasks...")
    for todo in jm_todos:
        domain = todo.get("domain") or default_domain
        if domain == "null":
            domain = default_domain

        due = DUE_MAP.get(str(todo.get("due_hint", "")).lower())

        payload = json.dumps({
            "content": todo["task"],
            "labels": [domain],
            **({"due_string": due} if due else {}),
        }).encode()

        req = urllib.request.Request(
            "https://api.todoist.com/api/v1/tasks",
            data=payload,
            headers={
                "Authorization": f"Bearer {TODOIST_TOKEN}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req) as resp:
                task = json.loads(resp.read())
                print(f"   ✓ {task['content']}")
        except Exception as e:
            print(f"   ✗ Failed: {todo['task']} ({e})")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Record and process a meeting")
    parser.add_argument("name", nargs="?", default="meeting",
                        help="Meeting name, e.g. '1:1 with Ashish'")
    parser.add_argument("--domain", default=DEFAULT_DOMAIN,
                        choices=list(DOMAIN_MAP.keys()),
                        help="Domain for filing + Todoist labels (default: i9)")
    parser.add_argument("--no-teams", action="store_true",
                        help="Mic only (skip system audio capture)")
    parser.add_argument("--todos", action="store_true",
                        help="Create Todoist tasks from action items (off by default)")
    parser.add_argument("--tx", metavar="FILE",
                        help="Use existing transcript file instead of recording")
    parser.add_argument("--model", default=WHISPER_MODEL,
                        help=f"Whisper model (default: {WHISPER_MODEL}). "
                             "Options: tiny.en, base.en, small.en, medium.en")
    parser.add_argument("--max-duration", type=int, default=0, metavar="MIN",
                        help="Auto-stop after N minutes (0 = no limit)")
    parser.add_argument("--idle-timeout", type=int, default=5, metavar="MIN",
                        help="Auto-stop after N min of silence post-conversation (default 5, 0 = off)")
    parser.add_argument("--mic", metavar="NAME",
                        help="Mic device name fragment (e.g. 'AirPods'). Default: MacBook Pro Microphone")
    parser.add_argument("--devices", action="store_true",
                        help="List available audio input devices and exit")
    args = parser.parse_args()

    if args.devices:
        list_devices()
        sys.exit(0)

    meeting_name = args.name
    whisper_model = args.model

    # 1. Get transcript
    if args.tx:
        transcript = Path(args.tx).read_text()
        print(f"📄 Using transcript: {args.tx} ({len(transcript.split())} words)")
    else:
        audio = record_audio(
            teams_mode=not args.no_teams,
            max_duration=args.max_duration * 60,  # convert min to sec
            idle_timeout=args.idle_timeout * 60,
            mic_name=args.mic,
        )
        recordings_dir = VAULT_DIR / "h335" / "i9" / "recordings"
        recordings_dir.mkdir(parents=True, exist_ok=True)
        date_slug = datetime.now().strftime("%Y.%m.%d-%H%M")
        name_slug = meeting_name.lower().replace(" ", "-")[:30]
        wav_path = recordings_dir / f"{date_slug}-{name_slug}.wav"
        with defer_termination("wav save"):
            save_wav(audio, wav_path)
        print(f"🎙  Saved recording: {wav_path}")
        transcript = transcribe(wav_path, whisper_model)

    if not transcript.strip():
        print("⚠  No speech detected.")
        sys.exit(1)

    # 2. Save transcript to a text file alongside the WAV
    tx_path = wav_path.with_suffix(".txt") if not args.tx else Path(args.tx).with_suffix(".transcript.txt")
    with defer_termination("transcript save"):
        tx_path.write_text(transcript)
    print(f"📄 Transcript saved: {tx_path}")
    if not args.tx:
        warnings = capture_quality_warnings(audio, transcript)
        if warnings:
            print("\n⚠  CAPTURE_QUALITY failure")
            for warning in warnings:
                print(f"   - {warning}")
            _notify("⚠ Recording quality failure", "; ".join(warnings), critical=True)

    # Note: extraction + filing is handled by Claude Code on /d357 stop.
    # meet.py only records + transcribes.
    print(f"\n✅  Done! Recording + transcript saved.")
    print(f"   WAV → {wav_path if not args.tx else '(from file)'}")
    print(f"   TXT → {tx_path}")


if __name__ == "__main__":
    main()
