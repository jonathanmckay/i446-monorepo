#!/bin/bash
# Usage: new-meeting d359 "First Last" [context]
#        new-meeting d358 "Meeting Name" [tags]

set -e

TYPE="$1"
NAME="$2"
EXTRA="$3"
VAULT="$HOME/vault"

if [ -z "$TYPE" ] || [ -z "$NAME" ]; then
    echo "Usage: new-meeting <d358|d359> \"Name\" [context/tags]"
    exit 1
fi

DATE=$(date +%Y-%m-%d)
DATETIME=$(date +"%B %-d, %Y %-I:%M %p")

if [ "$TYPE" = "d359" ]; then
    DIR="$VAULT/h335/d359"
    FILENAME="$NAME d359"
    CONTEXT="${EXTRA:-i9}"
    INDEX="$DIR/d359.md"

    if [ -f "$DIR/$FILENAME.md" ]; then
        echo "Already exists: $DIR/$FILENAME.md"
        echo "Opening and adding new meeting entry..."

        # Prepend a new dated section after the About block
        ENTRY="\n## $DATE\n\n"
        # Insert after the first --- ... --- block and About section
        python3 -c "
import sys
with open('$DIR/$FILENAME.md', 'r') as f:
    content = f.read()
# Find the meeting log marker
marker = '---\n\n## '
idx = content.find(marker, content.find('---', 3))
if idx == -1:
    # No meetings yet, just append
    content += '\n## $DATE\n\n'
else:
    # Insert before the first ## date entry
    lines = content.split('\n')
    inserted = False
    out = []
    for i, line in enumerate(lines):
        if not inserted and line.startswith('## 20') and i > 5:
            out.append('## $DATE')
            out.append('')
            out.append('')
            inserted = True
        out.append(line)
    if not inserted:
        out.append('')
        out.append('## $DATE')
        out.append('')
    content = '\n'.join(out)
with open('$DIR/$FILENAME.md', 'w') as f:
    f.write(content)
"
    else
        # Create new note with template
        cat > "$DIR/$FILENAME.md" << EOF
---
title: "$FILENAME"
date: $DATE
type: meeting-note
tags:
  - d359
  - h335
  - $CONTEXT
context: $CONTEXT
---

# $NAME

## About

- **Role:**
- **Team:**
- **Location:**
- **Family:**
- **Interests:**
- **Notes:**

---

## $DATE

EOF

        # Insert at top of index table (line 11 = first data row)
        awk -v row="| [[$FILENAME]] | $CONTEXT | $DATETIME |  |" \
            'NR==11{print row} {print}' "$INDEX" > "$INDEX.tmp" && mv "$INDEX.tmp" "$INDEX"
    fi

    echo "✓ $FILENAME"

elif [ "$TYPE" = "d358" ]; then
    DIR="$VAULT/h335/d358"
    FILENAME="$NAME d358"
    TAGS="${EXTRA:-}"
    INDEX="$DIR/d358.md"

    if [ -f "$DIR/$FILENAME.md" ]; then
        echo "Already exists: $DIR/$FILENAME.md"
        echo "Opening and adding new meeting entry..."

        python3 -c "
import sys
with open('$DIR/$FILENAME.md', 'r') as f:
    content = f.read()
lines = content.split('\n')
inserted = False
out = []
for i, line in enumerate(lines):
    if not inserted and line.startswith('## 20') and i > 5:
        out.append('## $DATE')
        out.append('')
        out.append('')
        inserted = True
    out.append(line)
if not inserted:
    out.append('')
    out.append('## $DATE')
    out.append('')
content = '\n'.join(out)
with open('$DIR/$FILENAME.md', 'w') as f:
    f.write(content)
"
    else
        cat > "$DIR/$FILENAME.md" << EOF
---
title: "$FILENAME"
date: $DATE
type: meeting-note
tags:
  - d358
  - h335
---

# $NAME d358

## $DATE

EOF

        awk -v row="| [[$FILENAME]] | $DATETIME | $TAGS |" \
            'NR==11{print row} {print}' "$INDEX" > "$INDEX.tmp" && mv "$INDEX.tmp" "$INDEX"
    fi

    echo "✓ $FILENAME"
else
    echo "Unknown type: $TYPE (use d358 or d359)"
    exit 1
fi

# Open in Obsidian
open "obsidian://open?vault=vault&file=h335/$TYPE/$FILENAME"
