#!/bin/bash
# open-in-obsidian.sh — PostToolUse hook for Write|Edit
# Opens vault files in Obsidian when they have substantial content (3+ paragraphs).
# Reads tool_input JSON from stdin (piped by Claude Code hook system).

input=$(cat)

file_path=$(echo "$input" | python3 -c "
import json, sys, urllib.parse
try:
    d = json.load(sys.stdin)
    fp = d.get('tool_input', {}).get('file_path', '')
    content = d.get('tool_input', {}).get('content', '') or d.get('tool_input', {}).get('new_string', '') or ''
    if not fp.startswith('/Users/mckay/vault/'):
        sys.exit(0)
    if content.count('\n\n') < 2:
        sys.exit(0)
    rel = fp.replace('/Users/mckay/vault/', '')
    if rel.endswith('.md'):
        rel = rel[:-3]
    print('obsidian://open?vault=vault&file=' + urllib.parse.quote(rel))
except Exception:
    sys.exit(0)
" 2>/dev/null)

[ -n "$file_path" ] && open "$file_path" 2>/dev/null || true
