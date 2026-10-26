---
name: "inkwelltodo"
description: "Pull the iOS / i446 app requests and bugs out of new-notes, file one Todoist task each ([2], @i447), start agents to build them in parallel lanes, then build, commit and deploy the apps (iPhone + Ix servers). Morning kickoff. Usage: /inkwelltodo [--dry-run]"
user-invocable: true
---

# /inkwelltodo — notes → app tasks → agents → deployed

JM jots app requests and bugs in `~/vault/z_ibx/new-notes.md` (and phone
captures in `~/vault/z_ibx/phone/`) under per-app headings: `dtd ios`,
`janus ios`, `inkwell`, `dtd` / `janus` (CLI or web), with numbered items
under each. This skill turns them into shipped work. First run: 2026-10-25.

## 1. Extract
- Read `new-notes.md` and every file in `z_ibx/phone/`. An app block starts
  at a line naming an app (dtd / janus / inkwell / dash / vault, optionally
  with ios / cli / web) and runs to the next blank-line-separated non-list
  paragraph. Each numbered or bulleted item with text is one request; skip
  empty items (`5.`). Ignore everything else (o314, people notes, todos):
  that stays for `/notes`.
- Rewrite each item as a crisp task: `<app> <surface>: <request> (N) [2]`,
  N = 10–20 min. Keep JM's meaning; if an item is garbled, give your best
  reading and put his exact words in the task description.
- With `--dry-run`, show the list and stop.

## 2. File + claim
- One Todoist task per item: project `6Crfmq5Pqm2PFQFc` (i447 tools),
  label `i447`, due today, `[2]` value (set 2026-10-25), duration from (N),
  JM's original words in the description.
- Claim each for this session so dtd shows them in progress:
  `python3 ~/i446-monorepo/tools/did/agent_claims.py claim --also "<distinctive words>"`
  (refresh the cache first: `did-fast.py --refresh-cache`).
- Remove the extracted lines (and any image embed that headed them) from
  `new-notes.md` so `/notes` doesn't route them again; leave the rest intact.

## 3. Run agents in lanes, not one per task
Most items touch the same few Swift files (EntryBar/key rows, JanusView,
DtdView), and parallel agents editing one file clobber each other. Group
tasks into lanes by file area and run one background agent per lane
(`Agent`, `run_in_background: true`), lanes in parallel, tasks within a lane
in order. Typical lanes:
- **keys / shared UI**: EntryBar.swift, KeyRow.swift, RefreshableTextEditor.swift, sheet keyboard wiring
- **janus**: JanusView (list, swipes, timer card), JanusStore, TimelineRow in InkwellCore, `tools/janus/mobile.py`
- **dtd**: DtdView, DtdStore, DtdTask in InkwellCore, `tools/dtd/dtd.py`, `dtd_forms.py`
- **Inkwell / vault**: VaultView, VaultTabStore, VaultCore, `tools/vault-mobile/`
- **CLI**: `tools/did/dtd.sh`, `tools/tg/janus.py` (run `test_dtd_launch_smoke.py` after dtd.sh edits)

Every agent brief must say: its files and what other lanes own; re-read a
shared file right before each edit and use small exact replacements; compile
with its OWN `-derivedDataPath /private/tmp/claude-501/dd-<lane>`; run
CoreCheck / pytest for what it touched; never write real Excel/Neon/Todoist/
Toggl in tests; **do not commit, deploy or restart services**; mark each
finished task with `agent_claims.py done <todoist id>`; report per task.

## 4. Integrate, commit, deploy (after ALL lanes report)
1. Build all three apps once with one derived-data dir; run
   `swift run CoreCheck` (InkwellCore) and the pytest files the lanes touched
   (`tools/dtd`, `tools/janus`, …). Fix integration breaks yourself.
2. Commit only the lanes' files via a worktree on `origin/wip` (never a
   checkout in the shared tree): `git worktree add --detach /private/tmp/<name> origin/wip`,
   copy the files in, commit (attribution trailer), `git push origin HEAD:wip`,
   `git worktree remove`. Check `git diff origin/wip -- <file>` first so you
   only ship the lanes' hunks.
3. iPhone: `echo "dtd janus Inkwell jmdash" > ~/.cache/jm/ios-deploy-trigger`, then
   read `~/.cache/jm/ios-deploy.log` until `deploy done`; only list the apps
   that changed. "device not able to fulfill usage assertion" = the phone is
   locked: ask JM to unlock and retry. A dropped connection: retry once.
4. Web/servers on Ix (only if their files changed):
   `ssh ix 'launchctl kickstart -k gui/$(id -u)/com.jm.dtd'` (dtd web + phone API),
   `ssh ix 'launchctl kickstart -k gui/$(id -u)/com.jm.janus-mobile'`; smoke
   `curl -s localhost:5560/api/tasks | head -c 200` / `:5561/api/timeline?lite=1`.
5. Terminal dtd picks up dtd.sh changes on restart: `echo local >
   /tmp/ix-tui-dtd-<wrapper pid>.switch` then POST `abort` to its fzf port
   (see the ix-tui wrapper); janus restarts itself on Ix when idle.

## 5. Report
One line per task (✅ / needs-device / blocked + why), the commits, what was
deployed where, and anything JM must test on the phone. Leave the tasks for
JM to complete in dtd (✅ = agent thinks it's done).
