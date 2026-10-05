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
   wait ~6s, retry once; still down → stop and report it.
3. For each child: `navigate` to the URL, `computer` `wait` 3s,
   `get_page_text`. If the text has no "DAILY ATTENDANCE" lines and shows a
   sign-in form, stop: `WARN: Veracross not signed in`. Never enter credentials.
4. Save each page's text to a scratch file and ingest (idempotent):
   ```bash
   python3 ~/i446-monorepo/tools/xk87/veracross_attendance.py ingest --child Theo --text-file <theo.txt>
   python3 ~/i446-monorepo/tools/xk87/veracross_attendance.py ingest --child Ren  --text-file <ren.txt>
   python3 ~/i446-monorepo/tools/xk87/veracross_attendance.py render
   ```
5. `tabs_close_mcp` the tab.
6. Report one line per child: new records this run, and last week's tardies
   and average late arrival (from the rendered table).

## Files

- Log: `~/vault/xk87/xk23 学习 McKay Curriculum/cais-attendance.md` (one row per event)
- Summary: the `<!-- attendance:start -->…<!-- attendance:end -->` block in
  `independence-agency-enterprise.md` under `### Morning Routine`
- Unlisted school days are on-time arrivals; Veracross only records exceptions.
