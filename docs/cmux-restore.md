# cmux: bringing the right programs back after a Straylight restart

Set 2026-11-12 ("CMUX on restart"). cmux saves its session on quit and restores
window, workspace, pane layout, working directories and scrollback on relaunch,
but **not processes**. What it does restore is a *resume binding*: a command
recorded per tab that cmux re-runs after JM approves the command prefix once in
**Settings > Terminal > Resume Commands** (approval is keyed on the prefix plus
the tab's working directory, so launch from `$HOME`).

Internet drops are a different case and need nothing: dtd and janus keep
running (on ix in tmux, or locally), mosh tabs reconnect on their own, and
rerunning the same `cc` reattaches an ssh tab.

## Who registers what

Every path goes through `scripts/cmux-resume-bind.sh set <checkpoint> <shell>`,
a silent no-op without `CMUX_SURFACE_ID` or the cmux CLI (ix, tmux on ix, phone).

| Tab | Normal mode | Local process in the cmux pane | Registers | Binding re-runs |
|---|---|---|---|---|
| 1 dtd | on ix over mosh | `scripts/winsize-guard.py` | winsize-guard (sees `tmux new -A -s dtd` in its argv) | `zsh -ic dtd` |
| 2 janus | on ix over mosh | `scripts/winsize-guard.py` | winsize-guard (`-s janus`) | `zsh -ic janus` |
| 1/2 local mode | dtd.sh / janus.py on Straylight | themselves | dtd.sh startup, janus `main()` | same |
| 3+ cc | claude in tmux on ix | mosh/ssh started by `claude-tracked` | `exec_on_ix`, `ix_handoff`, `exec_in_local_tmux` | `claude-tracked --reattach <tmux name>` |

`dtd` / `janus` are the launcher functions in `~/.config/zsh/ix-tui.zsh`
(Straylight only, outside the repo); re-running them lets the launcher pick ix
or local afresh. If that file ever gains a single call to the helper inside
`_ix_run`, the winsize-guard/dtd.sh/janus.py registrations become redundant.

## `cc --reattach <name>`

Attaches to tmux session `<name>` (local first, then ix) and does nothing else.
It never resolves titles or starts claude, so a restart with ix unreachable
cannot replay the tab's original words (a title, or a dtd agent's whole prompt)
as a fresh conversation. Outcomes:

- live on ix → `tmux attach -d` over mosh (ssh fallback), tagged with the
  surface id so `cs`/dtd keep numbering the tab
- gone on ix → message, binding cleared, login shell left in the tab
- ix unreachable → message, binding kept for the next restart

A finished local-tmux cc clears its own binding in `release_tab` via
`CC_RESUME_CHECKPOINT`. A cc that finished on ix leaves its binding; the next
restart hits the "gone" path once and clears it.

## Verifying on Straylight (not testable from ix: no cmux there)

```
cmux surface resume show --json          # inside a tab: binding present?
cmux surface resume set --help           # flags match? only --kind tmux is documented
tail ~/.local/state/jm/cmux-resume-bind.log
```

Open questions the first real restart answers: whether cmux *types* the resume
command into the pane (janus has a 2 s boot grace for typed text; dtd's fzf does
not), whether approval survives per-tab environment differences, and whether a
hard crash keeps the last saved layout (cmux saves on quit).

Tests: `tests/test_claude_tracked.py`, `scripts/test_winsize_guard.py`,
`scripts/test_cmux_resume_bind.py`.
