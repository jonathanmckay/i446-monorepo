#!/bin/zsh
# agent-claim-hook.sh <working|idle|release> — Claude Code hook for /claim.
# UserPromptSubmit → working, Stop → idle, SessionEnd → release. See
# tools/did/agent_claims.py for the store. 2026-10-05.
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
# `/0t`, `/1s897` and `/notes` claim their own card the same way (agent_claims.SKILL_CLAIMS).
# A bare `[N]` while holding a claim sets the claimed task's value (not points).
if [[ "$1" == working && ( "$j" =~ '"prompt"[[:space:]]*:[[:space:]]*"[[:space:]]*/(d|claim|0t|1s897|notes)([[:space:]]|")'\
      || "$j" =~ 'command-name>[[:space:]]*/(d|claim|0t|1s897|notes)[[:space:]]*<' \
      || "$j" =~ '"prompt"[[:space:]]*:[[:space:]]*"[[:space:]]*\[[0-9]+\][[:space:]]*"' ) ]]; then
  print -r -- "$j" | python3 "$HOME/i446-monorepo/tools/did/agent_claims.py" hook 2>/dev/null
fi
[[ -n "$sid" && -f "$D/by-session/$sid" ]] || exit 0
tid="$(<"$D/by-session/$sid")"
tid="${tid//[[:space:]]/}"
[[ -n "$tid" ]] || exit 0
zmodload zsh/datetime zsh/files 2>/dev/null
case "$1" in
  working|idle)
    print -r -- "$1 $EPOCHSECONDS" > "$D/$tid.state.tmp" 2>/dev/null \
      && mv -f "$D/$tid.state.tmp" "$D/$tid.state" 2>/dev/null ;;
  release)
    rm -f "$D/$tid.json" "$D/$tid.state" "$D/by-session/$sid" 2>/dev/null ;;
esac
# Mirror to Ix right away (Syncthing is ~1 min); detached so the hook returns now.
"$HOME/i446-monorepo/scripts/agent-claims-push.sh" &!
exit 0
