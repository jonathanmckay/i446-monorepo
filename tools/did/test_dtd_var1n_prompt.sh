#!/bin/zsh
# Regression (2026-07-26): completing a variable 1n+ weekly habit (s897,
# family, "1 kids nature", …) in dtd never asked for minutes, so points fell
# back to base/zero (their value is base + rate×minutes). ⌃⏎ on any variable
# 1n+ card must route through the tty-prompting DONE path, with the name list
# imported from did-fast's VARIABLE_1N (+ its aliases) so dtd can't drift.
set -e
cd "$(dirname "$0")"
DTD=dtd.sh
fail() { echo "FAIL: $1"; exit 1; }

# ── 1. Structural ────────────────────────────────────────────────────────────
grep -q 'DTD_VAR1N_PAT=' "$DTD" || fail "variable-1n pattern must be computed at launch"
grep -q 'df.VARIABLE_1N' "$DTD" || fail "pattern must import did-fast's VARIABLE_1N (no hardcoded copy)"
grep -q 'ONENEON_ALIASES' "$DTD" || fail "pattern must include aliases pointing at variable habits"
# Both the router (chooses execute-with-tty) and done.sh (asks the question)
# must carry the pattern branch.
[[ $(grep -c '${DTD_VAR1N_PAT})' "$DTD") -ge 1 ]] || fail "done.sh must prompt on the variable-1n branch"
grep -q 'cpap|xk20|xk22|xk26|i444|${DTD_VAR1N_PAT})' "$DTD" \
  || fail "done router must send variable-1n cards to execute (tty)"
grep -q '__no_variable_1n__' "$DTD" || fail "empty-pattern fallback missing (broken case syntax risk)"

# ── 2. Functional: the launch snippet emits a working case pattern ──────────
PAT=$(python3 - "$PWD/did-fast.py" <<'VARPY'
import importlib.util, re, sys
spec = importlib.util.spec_from_file_location("df_var", sys.argv[1])
df = importlib.util.module_from_spec(spec)
sys.modules["df_var"] = df
spec.loader.exec_module(df)
norm = {df.header_normalize(n) for n in df.VARIABLE_1N}
names = {n.lower() for n in df.VARIABLE_1N}
names |= {a.lower() for a, t in df.ONENEON_ALIASES.items()
          if df.header_normalize(t) in norm}
names = {re.sub(r"\s*\{\d+\}", "", n).strip() for n in names}
print("|".join('"%s"' % n for n in sorted(names)))
VARPY
)
[[ -n "$PAT" ]] || fail "pattern generation produced nothing"

_match() {
  eval "case \"\$1\" in ${PAT}) return 0;; *) return 1;; esac"
}
for name in "s897" "family" "1 kids nature" "relax" "家" "一起饭" "aos" "s+hcbp"; do
  _match "$name" || fail "'$name' must match the variable-1n prompt pattern"
done
_match "0t" && fail "'0t' must NOT match the variable-1n prompt pattern"
_match "cpap" && fail "'cpap' has its own branch and must not be in the 1n pattern"

echo "PASS: variable 1n+ cards route to the minutes prompt"
