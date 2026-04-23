---
name: "-2n"
description: "Unified interrupt queue: salah, -1l, -1g check, ibx0, then start goals. Wraps ibx0 with goal-setting and ritual prompts. Usage: /-2n"
user-invocable: true
---

# Interrupt Queue (/-2n)

A unified interrupt layer that orchestrates all periodic check-ins. Presents cards in a fixed order, using the same hotkey interface as ibx0.

## Ordering

Every invocation follows this sequence:

```
1. صلاة الشمس (salah check)
2. -1l (daily ritual check)
3. -1g (set 2h block goals, if not already set)
3.5. meeting prep (if any staged briefs from mtg.py)
4. ibx0 (inbox cards)
5. → start working on goals
```

## Usage

```
/-2n
```

No arguments. Opens in a cmux surface (replaces the ibx tab).

## Launch

Open a new cmux surface and run the TUI:

```bash
cmux new-surface --type terminal
# parse surface:N and pane:N from output, then:
cmux respawn-pane --surface surface:<N> --command "bash ~/i446-monorepo/tools/ibx/-2n_wrapper.sh"
cmux focus-pane --pane pane:<N>
```

Then confirm: `-2n opened in a new cmux tab`

## TUI Implementation

The TUI is at `~/i446-monorepo/tools/ibx/-2n.py`. It:
1. Runs pre-inbox cards (salah, -1g, meeting prep) with Rich panels
2. Delegates to `ibx0.main()` for the full inbox flow (polling, cards, hotkeys)
3. After ibx0 exits, offers to start a Toggl timer for the current block's goals

## Steps (for reference — implemented in -2n.py)

### Step 1: Check صلاة الشمس

Read the 0₦ sheet for today's row. Check the `ص` column (salah). If empty/0, prompt:

```
Card 1/4: صلاة
Have you prayed? (y/skip)
```

- `y` → run `/did ص`
- `skip` → move on

### Step 2: Check -1l

Check if `-1l` has been logged today (0₦ column for -1l, or check Todoist). If not done, prompt:

```
Card 2/4: -1l
Daily ritual review not done. Run now? (y/skip)
```

- `y` → the user will handle -1l separately (just flag it)
- `skip` → move on

### Step 3: Check -1g for current block

Read the build order file (`~/vault/g245/-1₦ , 0₦ - Neon {Build Order}.md`). Find the `## -1₲` section. Determine the current 2h block from wall clock time:

| Block | Local Time | 地支 |
|-------|-----------|------|
| 0 | 05:00-06:59 | 卯 |
| 1 | 07:00-08:59 | 辰 |
| 2 | 09:00-10:59 | 巳 |
| 3 | 11:00-12:59 | 午 |
| 4 | 13:00-14:59 | 未 |
| 5 | 15:00-16:59 | 申 |
| 6 | 17:00-18:59 | 酉 |
| 7 | 19:00-20:59 | 戌 |
| 8 | 21:00-22:59 | 亥 |

Check if the current block's line has goals (non-empty checkbox items under it). If empty:

```
Card 3/4: -1g
No goals set for <地支 name> (<HH:MM>-<HH:MM>). Set now?
> 
```

Wait for user input. If they type goals, run `/-1g <goals>`. If they type `skip`, move on.

If goals ARE already set, show them briefly and move on:

```
-1g (<地支 name>): ✓ already set
  - goal 1
  - goal 2
```

### Step 3.5: Meeting prep cards

Read `~/vault/z_ibx/mtg-briefs.json`. If the file exists and contains entries, show each as a card:

```
Card 3.5/5: 📅 Meeting Prep
[MTG PREP] 1:1 with Ashish (10:00am)

## 1:1 with Ashish
**Time:** 10:00am (30min) | **Type:** teams

### Ashish
PM, CoreAI Growth. Son applying to colleges.

**Recent:**
## 2026-04-07
Discussed Q2 OKRs, agreed on MAU target

**Open tasks:**
- Review growth dashboard mockup

(ack/skip)
```

- `ack` → remove this brief from the staging file, move on
- `skip` → leave it (will show again next -2n)

After processing, rewrite `mtg-briefs.json` with remaining briefs. If all acknowledged, delete the file.

If the file doesn't exist or is empty, skip silently (no card shown).

### Step 4: Run ibx0

Execute `/ibx0` — this marks all inbox habits done (ibx s897, ibx i9, slack github, slack m5x2, ibx m5x2, teams).

### Step 5: Suggest starting on goals

After ibx0 completes, show the current block's goals as a "start working" prompt:

```
Goals for <地支 name> (<HH:MM>-<HH:MM>):
  1. <goal 1>
  2. <goal 2>

Start timer for goal 1? (y/skip/N)
```

- `y` or `1` → run `/tg` for goal 1
- `N` → run `/tg` for goal N
- `skip` → done

### Step 6: Report

```
-2n complete: <N> cards processed
```

## Terminal Colors

-2n is a long-running interrupt listener. Colors reflect system state:

- **blue** — Idle. All cards processed, waiting for next inbound interrupt.
- **black** — Processing an inbound item (fetching inbox, checking state).
- **red** — Card ready for user. A prompt/decision is waiting.

Set color via `~/i446-monorepo/scripts/term-color.sh <color>`.

**Flow:**
1. On `/-2n` start → black (processing)
2. Before each card prompt → red (user action needed)
3. After user responds → black (processing next)
4. After Step 5 (goals shown, timer started or skipped) → blue (idle, listening)
5. When a new interrupt arrives → black → red (card ready)

## Design Notes

- ibx0 remains independently callable via `/ibx0`
- -1g remains independently callable via `/-1g`
- -2n just orchestrates them in the correct order
- The card interface uses the same hotkey pattern as ibx0 (a=archive/done, skip, etc.)
- Future card sources can be added (meeting prep/BCL, stale contacts, etc.)

## Build Order Snapshot

Daily snapshot of the build order is saved to `~/vault/g245/v_logs/YYYY-MM-DD-build-order.md` via the `cc` wrapper (TODO: not yet implemented — tracked in Todoist).
