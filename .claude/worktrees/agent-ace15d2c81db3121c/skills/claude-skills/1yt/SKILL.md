---
name: "1yt"
description: "YouTube watch-time review from a Google Takeout export. Estimates watch time per video (gap-to-next-entry capped at real video length), reports totals/top channels/top videos, writes a report to vault/hcmc/youtube/. Usage: /1yt [path to watch-history.json or Takeout zip]"
user-invocable: true
---

# YouTube Watch History Review (/1yt)

Turn a Google Takeout export into a watch-time + content report. Built to close
the mobile-YouTube blind spot: ActivityWatch only sees desktop, and Apple
Screen Time's cross-device store is walled off from Full Disk Access even with
root (confirmed 2026-08-11 debugging Ix — kernel Sandbox "System Policy" deny,
not a settings problem). YouTube's own server-side watch history is the one
source that covers every device tied to the account, mobile included.

**This skill is review-only.** It does not write to Neon or Toggl — the
gap-to-next-entry duration heuristic hasn't been validated against real data
yet. Once a few weeks of reports look right, decide together where the numbers
should feed into the points system.

## Prerequisite (one-time, user does this)

Takeout export: **takeout.google.com** → deselect all → select only
"YouTube and YouTube Music" → in its options, select only "history" → JSON
format → create export → download the zip.

Optional but recommended — real per-video durations instead of a flat cap:
enable "YouTube Data API v3" in a Google Cloud project (console.cloud.google.com,
APIs & Services → Library) and create an API key (no OAuth needed, it's public
metadata). Store it as `YOUTUBE_API_KEY` in the shell environment, or pass
`--api-key` directly.

## Steps

1. **Locate the export.** If the user gave a path, use it. Otherwise check
   `~/Downloads` for the most recent `takeout-*.zip` or an already-extracted
   `watch-history.json`. A Takeout zip nests the file at
   `Takeout/YouTube and YouTube Music/history/watch-history.json` — unzip to a
   scratch dir if needed:
   ```bash
   unzip -o ~/Downloads/takeout-*.zip -d /tmp/takeout-yt
   find /tmp/takeout-yt -name watch-history.json
   ```
   If nothing is found, ask the user for the path rather than guessing.

2. **Determine `--since`.** Default to 7 days before today (this is a weekly
   review), unless the user asks for a different range or this is the first
   run (in which case process the full export once for a baseline).

3. **Run the analyzer.**
   ```bash
   python3 ~/i446-monorepo/tools/hcmc/youtube_history.py <watch-history.json> \
     --since <YYYY-MM-DD> ${YOUTUBE_API_KEY:+--api-key "$YOUTUBE_API_KEY"}
   ```

4. **Save the report.** Write the script's markdown output to
   `~/vault/hcmc/youtube/<week-end-date>.md` (create the `youtube/` dir if it
   doesn't exist). Prepend frontmatter matching the vault convention:
   ```
   ---
   title: YouTube watch history — <range>
   date: <today, YYYY-MM-DD>
   type: review
   tags: [hcmc, youtube]
   ---
   ```

5. **Report to the user.** Print the summary inline (total hours, top
   channels, top videos). If total watch time is notably up or down from the
   prior report in `vault/hcmc/youtube/`, say so. Do not editorialize with
   guilt/scolding language — the point is visibility, not a lecture; let the
   user draw their own conclusion about reapportioning.

## Failure modes

- No `watch-history.json` found in the zip → the user selected the wrong
  Takeout product/scope; tell them to re-export with only YouTube history
  selected.
- Empty result for the date range → either genuinely didn't watch anything,
  or Takeout's account doesn't match the device being tracked (e.g. exported
  from the wrong Google account) — ask which account is signed into YouTube
  on the phone before assuming zero.
