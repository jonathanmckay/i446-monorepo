#!/usr/bin/env python3
"""
meet.py — Record a meeting, transcribe, extract notes + todos, file to vault.

Usage:
    python3 meet.py "1:1 with Ashish"     # record until Ctrl+C
    python3 meet.py "standup" --no-todos  # skip Todoist
    python3 meet.py "retro" --tx transcript.txt  # use existing transcript
    python3 meet.py "all-hands" --domain i9      # force domain
"""

import os
import sys
import wave
import json
import signal
import tempfile
import argparse
import urllib.request
from datetime import datetime
from pathlib import Path

import numpy as np
import sounddevice as sd
import anthropic

SAMPLE_RATE = 16000   # Whisper works best at 16kHz
CHANNELS = 1
TODOIST_TOKEN = "7eb82f47aba8b334769351368e4e3e3284f980e5"
VAULT_DIR = Path.home() / "vault"
WHISPER_MODEL = "base.en"  # fast, English-only. Use "small.en" for more accuracy.

# Domain → Todoist label + vault subfolder
DOMAIN_MAP = {
    "i9":   {"label": "i9",   "folder": "h335/i9"},
    "m5x2": {"label": "m5x2", "folder": "h335/m5x2"},
    "s897": {"label": "s897", "folder": "s897"},
    "g245": {"label": "g245", "folder": "g245"},
    "xk87": {"label": "xk87", "folder": "xk87"},
    "xk88": {"label": "xk88", "folder": "xk88"},
}
DEFAULT_DOMAIN = "i9"


# ── Recording ────────────────────────────────────────────────────────────────

def record_audio() -> np.ndarray:
    """Record from mic until Ctrl+C. Returns int16 numpy array."""
    print("🎙  Recording... press Ctrl+C to stop\n")
    frames = []
    stop = False

    def callback(indata, frame_count, time_info, status):
        if not stop:
            frames.append(indata.copy())

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS,
                        dtype="int16", callback=callback):
        try:
            while not stop:
                sd.sleep(500)
        except KeyboardInterrupt:
            stop = True

    if not frames:
        print("No audio captured.")
        sys.exit(1)

    audio = np.concatenate(frames, axis=0)
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
    segments, info = model.transcribe(str(wav_path), beam_size=5)
    text = " ".join(seg.text.strip() for seg in segments)
    print(f"   {len(text.split())} words transcribed.")
    return text


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
    date = datetime.now().strftime("%Y-%m-%d")
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
    parser.add_argument("--no-todos", action="store_true",
                        help="Skip Todoist task creation")
    parser.add_argument("--tx", metavar="FILE",
                        help="Use existing transcript file instead of recording")
    parser.add_argument("--model", default=WHISPER_MODEL,
                        help=f"Whisper model (default: {WHISPER_MODEL}). "
                             "Options: tiny.en, base.en, small.en, medium.en")
    args = parser.parse_args()

    meeting_name = args.name
    whisper_model = args.model

    # 1. Get transcript
    if args.tx:
        transcript = Path(args.tx).read_text()
        print(f"📄 Using transcript: {args.tx} ({len(transcript.split())} words)")
    else:
        audio = record_audio()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            wav_path = Path(f.name)
        save_wav(audio, wav_path)
        try:
            transcript = transcribe(wav_path, whisper_model)
        finally:
            wav_path.unlink(missing_ok=True)

    if not transcript.strip():
        print("⚠  No speech detected.")
        sys.exit(1)

    # 2. Extract structured data
    data = extract_meeting_data(transcript, meeting_name)

    # 3. File meeting note
    note_path = write_meeting_note(data, transcript, meeting_name, args.domain)

    # 4. Create Todoist tasks
    if not args.no_todos:
        create_todoist_tasks(data.get("todos", []), args.domain)

    # 5. Summary
    print(f"\n✅  Done! \"{data.get('title', meeting_name)}\"")
    print(f"   Note → {note_path}")

    followup = data.get("followup_email")
    if followup and followup != "null":
        print("\n─── Followup email draft ───")
        print(followup)
        print("────────────────────────────")


if __name__ == "__main__":
    main()
