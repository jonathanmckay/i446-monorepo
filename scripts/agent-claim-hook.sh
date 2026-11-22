#!/bin/zsh
# agent-claim-hook.sh <working|idle|release|subagent-start|subagent-stop|busy>
# Claude Code hook for /claim. UserPromptSubmit → working, Stop → idle,
# SessionEnd → release, SubagentStart/SubagentStop → track in-flight
# subagents. See tools/did/agent_claims.py for the store. 2026-10-05.
#
# Background subagents (2026-10-19): a turn can end (Stop) while an Agent it
# launched in the background is still working, and the session resumes with
# no UserPromptSubmit when that agent reports back. Stop used to write idle
# regardless, so the dtd card showed 🧮 🕐 "waiting on JM" mid-work ("neon
# off excel should still be claimed by 3"). SubagentStart/Stop now keep a
# per-session list of running agent ids; Stop writes working while any are
# still running, and SubagentStop writes working because the parent resumes
# to process the result (its own Stop then decides). Entries older than
# AGENT_STALE_S are ignored so a killed subagent can't pin the card forever.
#
# Background shells (2026-11-05): a run_in_background Bash ends the turn the
# same way but fires no Subagent event, so Stop wrote idle mid-work ("fix
# yiqifan bugs" waiting on JM with a test run still going). When the shell
# exits the session resumes and its own Stop decides, like an agent. Stop
# looks for live ones among the claude process's children; there is no id to
# track because nothing fires on exit. An hour-old shell doesn't count: that's
# a dev server, and the card goes stale at the same age (agent_claims.STALE_SECS).
# busy stays agents-only: nothing re-checks after Stop, so a server started on
# the turn's last call would hold the tab black for good.
#
# Runs on EVERY prompt and turn end in every session, so the common case (this
# session holds no claim) must cost nothing: builtins only, no python, no jq.
# Never fails loudly: a hook error must not disturb the session.
D="$HOME/vault/z_ibx/agent-claims"
# The hook's stdin JSON names the session that fired it. Prefer it over the
# env var, which a nested `claude -p` could inherit from its parent session.
sid=""
IFS= read -r -d '' -t 1 j 2>/dev/null
[[ "$j" =~ '"session_id"[[:space:]]*:[[:space:]]*"([^"]+)"' ]] && sid="${match[1]}"
[[ -z "$sid" ]] && sid="${CLAUDE_CODE_SESSION_ID:-}"
# /fin's exit request (see "/fin closes the tab" below) is void once JM sends
# anything else here: he kept working in this thread.
# First thing on a prompt (2026-11-22): below the claim/regrab/autoclaim python
# calls (autoclaim can hold for seconds) the typist woke first and typed /exit
# into the prompt JM had queued behind /fin.
# The fin-close marker goes with it (2026-11-22): left behind, the next ordinary
# exit of this thread would end its tmux session and close the tab.
if [[ "$1" == working && -n "$sid" && -f "$D/by-session/$sid.exit" ]]; then
  for _l in ${(f)"$(<"$D/by-session/$sid.exit")"}; do
    [[ "$_l" == tmux=?* && "${_l#tmux=}" != */* ]] && rm -f "${FIN_CLOSE_DIR:-$HOME/.local/state/jm/fin-close}/${_l#tmux=}" 2>/dev/null
  done
  rm -f "$D/by-session/$sid.exit" 2>/dev/null
fi
# `/d <task>[: request]` / `/d new <task>` (or /claim): claim (or create +
# claim) right here, before the model starts, so dtd shows it in ~1s. Python
# only runs for these prompts; its stdout lands in the model's context so the
# /d skill knows it's already done.
# The skills in agent_claims.SKILL_CLAIMS claim their own card the same way;
# this alternation must list exactly those keys (test_agent_claims enforces it).
# A bare `[N]` while holding a claim sets the claimed task's value (not points).
if [[ "$1" == working && ( "$j" =~ '"prompt"[[:space:]]*:[[:space:]]*"[[:space:]]*/(d|claim|dt|0t|0g|notes|早餐|1s|1g|1s897|1hcmc|1hcb|1i9|1m5x2|xk887|1-1n|1-2g|f695|2n|3s|moralaudit|2026-4g-checkin|1-i446|1hcme|1f693|ibxi9|ibxm5x2|2hci|2n1)([[:space:]]|")'\
      || "$j" =~ 'command-name>[[:space:]]*/(d|claim|dt|0t|0g|notes|早餐|1s|1g|1s897|1hcmc|1hcb|1i9|1m5x2|xk887|1-1n|1-2g|f695|2n|3s|moralaudit|2026-4g-checkin|1-i446|1hcme|1f693|ibxi9|ibxm5x2|2hci|2n1)[[:space:]]*<' \
      || "$j" =~ '"prompt"[[:space:]]*:[[:space:]]*"[[:space:]]*\[[0-9]+\][[:space:]]*"' ) ]]; then
  print -r -- "$j" | python3 "$HOME/i446-monorepo/tools/did/agent_claims.py" hook 2>/dev/null
fi
# Re-grab (2026-11-21): this session marked a task ✅ (agent_claims.mark_done
# left by-session/<sid>.done) and JM now sends an ordinary follow-up: the
# thread takes the task back (agent_claims.regrab; python only on this path).
# Ahead of auto-claim, which then sees the index and stays out of it.
if [[ "$1" == working && -z "${DTD_NOTE_CHILD:-}" && -n "$sid" \
      && ( -z "${CLAUDE_CODE_SESSION_ID:-}" || "$CLAUDE_CODE_SESSION_ID" == "$sid" ) \
      && ! -f "$D/by-session/$sid" && -f "$D/by-session/$sid.done" \
      && "$j" =~ '"prompt"[[:space:]]*:[[:space:]]*"[[:space:]]*([^"[:space:]])' \
      && "${match[1]}" != [/\<\[] ]]; then
  print -r -- "$j" | python3 "$HOME/i446-monorepo/tools/did/agent_claims.py" regrab 2>/dev/null
fi
# Auto-claim (2026-11-14): a session's FIRST ordinary prompt is matched to a dtd
# task without /d (agent_claims.autoclaim: one Haiku call, match-only, never
# creates). Interactive cc sessions only: CC_SESSION is set by claude-tracked, so
# the headless `claude -p` callers (ibx wrappers, triage, 0t, wakeup) spawning
# fresh sids all day never reach it. A `claude -p` nested inside a session
# inherits the parent's CLAUDE_CODE_SESSION_ID, which then differs from its own
# stdin sid: skipped too. Once per session (python writes the .auto marker even
# when nothing matches). A `/` command, a <command-…> wrapper, or a bare [N] is
# never a first prompt worth matching, so those cost nothing here.
if [[ "$1" == working && -n "${CC_SESSION:-}" && -z "${DTD_NOTE_CHILD:-}" && -n "$sid" \
      && ( -z "${CLAUDE_CODE_SESSION_ID:-}" || "$CLAUDE_CODE_SESSION_ID" == "$sid" ) \
      && ! -f "$D/by-session/$sid" && ! -f "$D/by-session/$sid.auto" \
      && "$j" =~ '"prompt"[[:space:]]*:[[:space:]]*"[[:space:]]*([^"[:space:]])' \
      && "${match[1]}" != [/\<\[] ]]; then
  print -r -- "$j" | python3 "$HOME/i446-monorepo/tools/did/agent_claims.py" autoclaim 2>/dev/null
fi
# A free tab (~~, agent_claims.set_free after /fin) is free until JM types an
# ordinary prompt here (2026-11-14). Slash commands, <command-…> wrappers and a
# bare [N] are not a new start. session-title.sh retitles on the same event.
if [[ "$1" == working && -n "$sid" && -f "$D/by-session/$sid.free" \
      && "$j" =~ '"prompt"[[:space:]]*:[[:space:]]*"[[:space:]]*([^"[:space:]])' \
      && "${match[1]}" != [/\<\[] ]]; then
  rm -f "$D/by-session/$sid.free" 2>/dev/null
fi
zmodload zsh/datetime zsh/files 2>/dev/null
AGENT_STALE_S=7200
# In-flight subagents of this session: "<agent_id> <epoch>" per line. Kept
# for every session (builtins only), claimed or not, so a claim made after
# the agent started still sees it.
A="$D/by-session/$sid.agents"
aid=""
[[ "$j" =~ '"agent_id"[[:space:]]*:[[:space:]]*"([^"]+)"' ]] && aid="${match[1]}"
_live_agents() {   # prints the still-fresh entries, minus $1 if given
  local l out=() keep
  [[ -f "$A" ]] || return 0
  for l in "${(@f)$(<"$A")}"; do
    [[ -z "$l" ]] && continue
    keep=1
    [[ -n "$1" && "${l%% *}" == "$1" ]] && keep=0
    (( EPOCHSECONDS - ${${l##* }:-0} > AGENT_STALE_S )) && keep=0
    (( keep )) && out+=("$l")
  done
  (( ${#out} )) && print -rl -- "${out[@]}"
}
_live_shells() {   # prints this session's background Bash shells, under an hour old
  local l p e; local -a f; local -A parent argv0
  local -a rows=("${(@f)$(ps -axo pid=,ppid=,etime=,command= 2>/dev/null)}")
  for l in "${rows[@]}"; do
    f=(${=l}); (( ${#f} >= 4 )) || continue
    parent[$f[1]]=$f[2]; argv0[$f[1]]=${f[4]:t}
  done
  p=$$
  while [[ -n "$p" && "${argv0[$p]}" != (claude|claude.exe) ]]; do p=${parent[$p]}; done
  [[ -n "$p" ]] || return 0
  for l in "${rows[@]}"; do
    f=(${=l}); (( ${#f} >= 4 )) || continue
    [[ "$f[2]" == "$p" && "$l" == *"/.claude/shell-snapshots/snapshot-"* ]] || continue
    e=$f[3]   # [[dd-]hh:]mm:ss
    [[ "$e" == *-* || "$e" == *:*:* ]] || print -r -- "$f[1]"
  done
}
# busy (2026-11-05): exit 0 while this session's background agents run, so the
# Stop colour hook leaves the tab black. Blue means waiting on JM, same as 🕐.
if [[ "$1" == busy ]]; then
  [[ -n "$sid" && -n "$(_live_agents)" ]]
  exit $?
fi
if [[ -n "$sid" ]]; then
  case "$1" in
    subagent-start)
      [[ -n "$aid" ]] && { mkdir -p "$D/by-session" 2>/dev/null; print -r -- "$aid $EPOCHSECONDS" >> "$A" 2>/dev/null; } ;;
    subagent-stop)
      live="$(_live_agents "$aid")"
      if [[ -n "$live" ]]; then print -r -- "$live" > "$A" 2>/dev/null; else rm -f "$A" 2>/dev/null; fi ;;
  esac
fi
# Stop on a claimed task: a 😈 two-sentence summary of the turn goes onto the
# task as a Todoist comment (agent_claims.note_turn, 2026-10-25). Detached;
# `.note` queues ids the turn marked done (they already left the index).
# DTD_NOTE_CHILD: the summarizer's own `claude -p` must not note itself.
if [[ "$1" == idle && -n "$sid" && -z "$DTD_NOTE_CHILD" \
      && ( -s "$D/by-session/$sid" || -f "$D/by-session/$sid.note" ) ]]; then
  print -r -- "$j" | python3 "$HOME/i446-monorepo/tools/did/agent_claims.py" note >/dev/null 2>&1 &!
fi
# /fin closes the tab (2026-11-22, /bug "didn't exit the tab"): when the tab is
# driven from another host, done_session.mark_tab_close leaves fin-close/<tmux
# session> for the launchers (claude-tracked) AND by-session/<sid>.exit here.
# The launchers only act once claude exits, and a skill cannot run /exit, so
# this types it, the same way the reverse /fin below types /clear: only on a
# true idle (nothing in the background, input free), detached so the keys land
# after the hook returns. C-u first: a half-typed line would turn `/exit` into
# an ordinary prompt. Sits above the "holds a claim" gate because /fin has
# already cleared the index. One builtin file test in the common case.
if [[ "$1" == idle && -n "$sid" && -f "$D/by-session/$sid.exit" ]]; then
  if [[ -n "$TMUX_PANE" && -z "$DTD_NOTE_CHILD" && -z "$(_live_agents)" && -z "$(_live_shells)" ]]; then
    # The request stays on disk through the wait (2026-11-22): a prompt JM queued
    # behind /fin is submitted as this turn ends, and its `working` hook voids
    # the request, so the tab stays open for it. The rm is the claim: no `-f`,
    # so a voided request, or a second Stop's typist, types nothing.
    _x="$D/by-session/$sid.exit"
    { sleep 1; rm "$_x" 2>/dev/null || exit 0; tmux send-keys -t "$TMUX_PANE" C-u; tmux send-keys -t "$TMUX_PANE" -l '/exit'; sleep 0.3; tmux send-keys -t "$TMUX_PANE" Enter; } >/dev/null 2>&1 &!
  fi
fi
# SessionEnd: a re-grabbed task (see Re-grab above) returns to ✅, and the
# markers go so a resumed session starts clean.
regrabbed=()
[[ "$1" == release && -n "$sid" ]] && rm -f "$D/by-session/$sid.exit" 2>/dev/null
if [[ "$1" == release && -n "$sid" ]]; then
  [[ -f "$D/by-session/$sid.regrab" ]] && regrabbed=(${(f)"$(<"$D/by-session/$sid.regrab")"})
  rm -f "$D/by-session/$sid.done" "$D/by-session/$sid.regrab" 2>/dev/null
fi
[[ -n "$sid" && -f "$D/by-session/$sid" ]] || exit 0
# One id per line (2026-10-22: `claim --also` lets a session hold several
# tasks it fanned out to subagents); every held task follows the session.
tids=(${(f)"$(<"$D/by-session/$sid")"})
tids=(${tids//[[:space:]]/})
tids=(${tids:#})
(( ${#tids} )) || exit 0
state=""
case "$1" in
  working|subagent-start|subagent-stop) state=working ;;
  idle) [[ -n "$(_live_agents)" || -n "$(_live_shells)" ]] && state=working || state=idle ;;
  release) state=released ;;
esac
# Reverse /fin (2026-11-14): did-fast closed a task this session holds and
# left by-session/<sid>.fin (or, for a claim made here and closed on Ix, Ix's
# fin/<sid>, pulled into remote/ix/ by agent-claims-pull.sh). Only on a true
# idle: typing into a running turn could answer a permission prompt. Detached,
# so the keys land after the hook returns and the input is free. The index
# goes with it: the task is closed. See agent_claims.fin_holders.
if [[ "$1" == idle && "$state" == idle && -n "$TMUX_PANE" ]]; then
  for m in "$D/by-session/$sid.fin" "$D/remote/ix/fin/$sid"; do
    [[ -f "$m" ]] || continue
    ftid="$(<"$m")"; ftid="${ftid//[[:space:]]/}"
    rm -f "$m" 2>/dev/null
    [[ "$m" == */remote/ix/* ]] && ssh -o BatchMode=yes -o ConnectTimeout=5 ix "rm -f vault/z_ibx/agent-claims/fin/$sid" >/dev/null 2>&1 &!
    [[ -n "$ftid" && " ${tids[*]} " == *" $ftid "* ]] || continue
    { sleep 1; tmux send-keys -t "$TMUX_PANE" -l '/clear'; sleep 0.3; tmux send-keys -t "$TMUX_PANE" Enter; } >/dev/null 2>&1 &!
    rm -f "$D/by-session/$sid" 2>/dev/null
    print -r -- "$EPOCHSECONDS" > "$D/by-session/$sid.free" 2>/dev/null   # tab → ~~
    python3 "$HOME/i446-monorepo/tools/cs/title_tiers.py" apply "$sid" >/dev/null 2>&1 &!
    exit 0
  done
fi
case "$1" in
  working|idle|subagent-start|subagent-stop|release)
    # release = SessionEnd: hand the tasks back but keep them at the TOP of dtd
    # (2026-10-21): records stay, state 'released' (agent_claims.release does
    # the same; load() prunes after RELEASED_TTL). Only the session index goes.
    for tid in "${tids[@]}"; do
      s="$state"
      [[ "$1" == release && -n "${regrabbed[(r)$tid]}" ]] && s=done
      print -r -- "$s $EPOCHSECONDS" > "$D/$tid.state.tmp" 2>/dev/null \
        && mv -f "$D/$tid.state.tmp" "$D/$tid.state" 2>/dev/null
    done
    [[ "$1" == release ]] && rm -f "$D/by-session/$sid" "$A" 2>/dev/null ;;
esac
# Mirror to Ix right away (Syncthing is ~1 min); detached so the hook returns now.
"$HOME/i446-monorepo/scripts/agent-claims-push.sh" &!
exit 0
