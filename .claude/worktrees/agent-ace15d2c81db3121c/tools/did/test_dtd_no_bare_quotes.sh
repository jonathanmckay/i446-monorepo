#!/bin/zsh
# Regression test: the binding scripts embed `python3 -c "<code>"` inside zsh
# double quotes, so ANY bare (unescaped) double quote in that code terminates
# the -c argument early — Python then runs truncated code with mangled argv and
# the action silently does nothing. This bit the split twice (2026-06-26: a
# comment containing  "Rev on ground ... 2"  broke every ctrl-p split with
# `int('ground')`). Only \" is allowed inside these blocks. dtd.sh itself warns
# about this at ~line 551.
set -e
cd "$(dirname "$0")"
fail() { echo "FAIL: $1"; exit 1; }

python3 - dtd.sh <<'PY' || exit 1
import re, sys
src = open(sys.argv[1]).read()

# Every embedded interpreter block: python3 -c "<newline> ... <newline>" "<args>
# (the close is a lone `"` that begins the trailing positional args).
blocks = re.findall(r'python3 -c "\n(.*?)\n"\s', src, re.DOTALL)
assert blocks, "no python3 -c blocks found — test or file structure changed"

bad = []
for bi, code in enumerate(blocks):
    for ln in code.split('\n'):
        # a bare double quote = a " not immediately preceded by a backslash
        if re.search(r'(?<!\\)"', ln):
            bad.append(ln.strip()[:100])

if bad:
    print("FAIL: bare (unescaped) double quotes inside a python -c block:")
    for b in bad:
        print("   ", b)
    sys.exit(1)
print(f"PASS: {len(blocks)} python -c block(s), no bare double quotes")
PY
