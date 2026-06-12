---
name: "卯"
description: "Open the wakeup GUI — a phone web app that drives you through the five -1₦ block rituals (prayer, goal, timer, task, inbox) in a forced-linear, no-skip sequence. The activating counterpart to /inbound, built for a half-asleep thumb. Usage: /卯"
user-invocable: true
---

# 卯 — Wakeup GUI (/卯)

The whole purpose: get through **-1₦** (the block-ritual icons) the moment you
wake up. Where `/inbound` is a passive triage queue, `/卯` is an **activating**
engine — a **forced linear** flow where each ritual must be completed before the
next card appears (commitment via friction).

This is a **phone web app**, not a TUI — at 5am, half-dead, you tap, you don't
type into a terminal. Open it fullscreen on your phone (Fuchikoma or iPhone).

- **URL:** `http://ix:5570`
- Backend: standalone Flask service `wakeup_server.py` on **ix** (always-on Mac),
  LaunchAgent `com.jm.wakeup` (KeepAlive). It reuses the `-2n.py` primitives.
- Targets the **current** 2-hour block (intended for 卯, 04:00–05:59, but works
  any time you wake). The block is frozen for the whole ritual.

## What /卯 does (when invoked from the CLI)

1. Health-check the service: `curl -s -m 6 http://ix:5570/healthz`
2. If it is not `ok`, restart it:
   `ssh ix 'launchctl kickstart -k gui/$(id -u)/com.jm.wakeup'` then re-check.
3. Print the URL `http://ix:5570` and confirm it is live. On a Mac you may open
   it for testing: `open http://ix:5570`.

The actual ritual happens by tapping through the GUI on the phone.

## Card order (one big card at a time, no skip)

Activating order — get up → decide → commit → act → process:

1. **☀️ صلاة** — one button: "I'm up". Writes the per-block prayer marker.
2. **🎯 intention** — tap one or more suggestion chips (calendar / weekly 1g /
   daily 0g / unfinished 0n) or type a goal. Writes the build order + syncs
   Todoist in the background. **≥1 required.**
3. **⏱️ commit** — start a Toggl timer (→ g245). If a fresh timer is already
   running you tap "keep going"; otherwise start one. The commit-to-action
   keystone. **Required.**
4. **✓ one thing** — tap an unfinished `0n` habit or type a task. Marks the ✅
   icon and logs via `/did` in the background. **Required.**
5. **📧 close -1₦** — one button. Writes the 📧 marker and shows the completion
   screen. (Full email/Slack triage stays in `/inbound`; at 5am the job is just
   to close the row and get activated.)

The front-end always renders the server's next forced step and only advances
after the server confirms the step is done. Buttons disable during a tap
(double-tap guard); a stale restored page resyncs automatically.

## Phone setup (one time)

Open `http://ix:5570` in the phone browser → **Add to Home Screen**. It launches
fullscreen (PWA meta tags set), so it behaves like a native wakeup app.

## Behavior notes

- **No busywork:** steps already satisfied when the ritual starts (prayer logged,
  goals set, a *fresh* timer running, inbox already marked) are pre-completed, so
  you only get carded for what's left. The **task** step is always required.
- **Stale timer:** a timer running but started >3h ago does **not** count — you
  start a fresh one. The activation point is to begin focused work now.
- **✅ semantics:** the task ✅ means "acknowledged at wakeup" (the human ritual).
  The `/did` log to 0n/Todoist is best-effort and detached (you don't wait ~120s).
- **Ritual instance:** persisted to `~/.cache/wakeup/ritual.json` (frozen block,
  idempotent side effects, resumes across a service restart).

## Architecture

- `tools/ibx/wakeup_server.py` — Flask app on ix:5570. Endpoints: `GET /`
  (mobile SPA), `GET /api/state` (fast), `GET /api/suggestions` + `/api/habits`
  (lazy), `POST /api/{prayer,goal,timer,task,inbox}`, `GET /healthz`.
- Loads `-2n.py` via importlib; reuses `get_current_block`, prayer/inbox markers,
  `read_block_goals`, `write_block_goals`, `spawn_1g_background`, `start_toggl`,
  `fetch_block_suggestions`, `_unfinished_0n_today`.
- Restart: `ssh ix 'launchctl kickstart -k gui/$(id -u)/com.jm.wakeup'`.
  Logs: `/tmp/wakeup.log`, `/tmp/wakeup.err` on ix.

## CLI fallback

`tools/ibx/wakeup.py` is the original forced-linear **TUI** (loads `-2n.py`,
hands off to `ibx0`). Kept as a no-GUI fallback for an SSH/Termius session:
`bash ~/i446-monorepo/tools/ibx/wakeup_wrapper.sh`.
