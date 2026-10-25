#!/bin/zsh
# agent-claim-hook.sh <working|idle|release|subagent-start|subagent-stop>
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
# `/d <task>[: request]` / `/d new <task>` (or /claim): claim (or create +
# claim) right here, before the model starts, so dtd shows it in ~1s. Python
# only runs for these prompts; its stdout lands in the model's context so the
# /d skill knows it's already done.
# The skills in agent_claims.SKILL_CLAIMS claim their own card the same way;
# this alternation must list exactly those keys (test_agent_claims enforces it).
# A bare `[N]` while holding a claim sets the claimed task's value (not points).
if [[ "$1" == working && ( "$j" =~ '"prompt"[[:space:]]*:[[:space:]]*"[[:space:]]*/(d|claim|0t|0g|notes|早餐|1s|1g|1s897|1hcmc|1hcb|1i9|1m5x2|xk887|1-1n|1-2g|f695|2n|3s|moralaudit|2026-4g-checkin|1-i446|1hcme|1f693|ibxi9|ibxm5x2)([[:space:]]|")'\
      || "$j" =~ 'command-name>[[:space:]]*/(d|claim|0t|0g|notes|早餐|1s|1g|1s897|1hcmc|1hcb|1i9|1m5x2|xk887|1-1n|1-2g|f695|2n|3s|moralaudit|2026-4g-checkin|1-i446|1hcme|1f693|ibxi9|ibxm5x2)[[:space:]]*<' \
      || "$j" =~ '"prompt"[[:space:]]*:[[:space:]]*"[[:space:]]*\[[0-9]+\][[:space:]]*"' ) ]]; then
  print -r -- "$j" | python3 "$HOME/i446-monorepo/tools/did/agent_claims.py" hook 2>/dev/null
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
  idle) [[ -n "$(_live_agents)" ]] && state=working || state=idle ;;
  release) state=released ;;
esac
case "$1" in
  working|idle|subagent-start|subagent-stop|release)
    # release = SessionEnd: hand the tasks back but keep them at the TOP of dtd
    # (2026-10-21): records stay, state 'released' (agent_claims.release does
    # the same; load() prunes after RELEASED_TTL). Only the session index goes.
    for tid in "${tids[@]}"; do
      print -r -- "$state $EPOCHSECONDS" > "$D/$tid.state.tmp" 2>/dev/null \
        && mv -f "$D/$tid.state.tmp" "$D/$tid.state" 2>/dev/null
    done
    [[ "$1" == release ]] && rm -f "$D/by-session/$sid" "$A" 2>/dev/null ;;
esac
# Mirror to Ix right away (Syncthing is ~1 min); detached so the hook returns now.
"$HOME/i446-monorepo/scripts/agent-claims-push.sh" &!
exit 0
