---
name: "attendance"
description: "Pull Theo's and Ren's CAIS attendance (tardies with arrival times, absences, early dismissals) from the Veracross parent portal via Chrome, log it to cais-attendance.md, and refresh the weekly summary in the Morning Routine section of independence-agency-enterprise. Runs weekly from /xk887. Usage: /attendance"
user-invocable: true
---

# CAIS Attendance (/attendance)

The Veracross parent portal requires a logged-in browser, so the page is read
through the Claude in Chrome extension; `veracross_attendance.py` does the
parsing and writing.

| Child | Recent Updates URL |
|---|---|
| Theo | https://portals.veracross.com/cais/parent/student/58454/recent-updates |
| Ren | https://portals.veracross.com/cais/parent/student/59455/recent-updates |

## Steps

1. Load the Chrome tools in ONE ToolSearch:
   `select:mcp__claude-in-chrome__tabs_context_mcp,mcp__claude-in-chrome__tabs_create_mcp,mcp__claude-in-chrome__navigate,mcp__claude-in-chrome__computer,mcp__claude-in-chrome__get_page_text,mcp__claude-in-chrome__tabs_close_mcp`
2. `tabs_context_mcp` (`createIfEmpty: true`), then `tabs_create_mcp`. If the
   extension isn't connected, `open -a "Google Chrome" https://claude.ai/chrome`,
   wait ~6s, retry once; still down → skip (step 7).
   **Browser choice:** Veracross is signed in on the **个** profile. When the
   tools say several browsers are connected, call `list_connected_browsers` and
   ask with the browser named `个` first, marked "(Recommended)" (the
   connected-name `m5x2 Claude Tab` is the m5c7 profile: not signed in).
3. For each child: `navigate` to the URL, `computer` `wait` 3s,
   `get_page_text`. If it lands on the Veracross login page, skip (step 7).
   Never enter credentials.
4. Save each page's text to a scratch file and ingest (idempotent):
   ```bash
   python3 ~/i446-monorepo/tools/xk87/veracross_attendance.py ingest --child Theo --text-file <theo.txt>
   python3 ~/i446-monorepo/tools/xk87/veracross_attendance.py ingest --child Ren  --text-file <ren.txt>
   python3 ~/i446-monorepo/tools/xk87/veracross_attendance.py render
   ```
5. `tabs_close_mcp` the tab.
6. Report one line per child: new records this run, and last week's tardies
   and average late arrival (from the rendered table).

7. **Skip quietly.** This step is best-effort: when the extension is down or
   Veracross isn't signed in, close any tab you opened and output exactly one
   line, `attendance: skipped (<reason>)`, then let the caller continue. Do NOT
   tell JM to sign in, reconnect, rename browsers, or do any other manual setup
   (2026-10-06: that advice cost him more time than the attendance data is worth).

## Files

- Log: `~/vault/xk87/xk23 学习 McKay Curriculum/cais-attendance.md` (one row per event)
- Summary: the `<!-- attendance:start -->…<!-- attendance:end -->` block in
  `independence-agency-enterprise.md` under `### Morning Routine`
- Unlisted school days are on-time arrivals; Veracross only records exceptions.
