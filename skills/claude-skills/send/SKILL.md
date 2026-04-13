---
name: "send"
description: "Send a message to someone via Teams, Outlook, Gmail, iMessage, or Slack. Usage: /send <person> via <channel>: <message>"
user-invocable: true
---

# Send Message (/send)

Send a proactive message to someone. Supports Teams, Outlook email, Gmail, iMessage, and Slack.

## Response Style

**Minimal output.** After sending, confirm in one line. Examples:
- `Sent via Teams to Luke Hoban`
- `Sent via Outlook to Asha Sharma`
- `Sent via iMessage to Mom`
- `Sent via Teams to Luke Hoban, Asha Sharma` (group chat)

Do NOT explain what you're doing. Just execute — except when confirmation is required (see AI assist and recipient confirmation rules below).

## Syntax

```
/send <person(s)> via <channel>: <message>
```

- **person(s)**: one or more recipients, separated by `,` or `and`. When multiple people are listed, send **one message to all** (group chat / multi-recipient email) — NOT separate messages to each.
- **channel**: `teams`, `outlook`, `gmail`, `imessage`, `slack`
- **message**: the text to send. If any part is in `<angle brackets>`, treat it as an instruction for you to draft that portion.

### Examples

```
/send Luke Hoban via teams: here's the meeting link https://teams.microsoft.com/meet/123
/send Asha Sharma via outlook: Can we move our 1:1 to Thursday?
/send Mom via imessage: I'll be home by 6
/send Joe via teams: <tell him I liked his proposal and suggest we meet Thursday to discuss>
/send Carolina via outlook: <draft a professional reply declining the meeting but suggesting async followup>
/send Luke and Asha via teams: let's sync on the design doc tomorrow
/send Luke, Asha, Joe via outlook: Here's the agenda for Friday
```

## AI Assist (angle brackets)

When the message contains `<instructions>`:
1. Draft the full message based on the instruction
2. Show the draft in a panel
3. Ask: **Send? (y/n)**
4. Send only if confirmed

## Channel Dispatch

### Teams
```python
import agency_mcp as mcp

# Resolve all recipient emails first, then:

if len(emails) == 1:
    # 1:1 chat
    result = mcp.call_tool('teams', 'CreateChat', {
        'chatType': 'oneOnOne',
        'members_upns': [emails[0]]
    }, timeout=60)
else:
    # Group chat — one thread with all recipients
    result = mcp.call_tool('teams', 'CreateChat', {
        'chatType': 'group',
        'members_upns': emails,
        'topic': '',  # optional topic
    }, timeout=60)

chat_id = json.loads(result['content'][0]['text'])['id']

# Send the message once to the shared chat
mcp.call_tool('teams', 'PostMessage', {
    'chatId': chat_id,
    'content': '<message>',
    'contentType': 'text',
}, timeout=30)
```

### Outlook email
```python
import agency_mcp as mcp

# Pass ALL recipient emails in the 'to' array — one email to all
mcp.call_tool('mail', 'SendEmailWithAttachments', {
    'to': emails,  # e.g. ['asha@microsoft.com', 'luke@microsoft.com']
    'subject': '<subject>',
    'body': '<message>',
    'contentType': 'Text',
}, timeout=30)
```

### Gmail
```python
import ibx as _ibx
import base64
from email.mime.text import MIMEText

svc = _ibx.get_gmail_service(_ibx.ACCOUNTS[0]['tokens'], _ibx.ACCOUNTS[0]['creds'])
msg = MIMEText('<message>')
msg['To'] = ', '.join(emails)  # all recipients in one To: header
msg['From'] = 'mckay@m5x2.com'
msg['Subject'] = '<subject>'
raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
svc.users().messages().send(userId='me', body={'raw': raw}).execute()
```

### iMessage
```bash
osascript -e 'tell application "Messages" to send "<message>" to buddy "<phone_or_email>"'
```

### Slack
Use the Slack API via the slack module:
```python
import slack as _slack
tokens = json.load(open(Path.home() / '.config/slack/tokens.json'))
# Find channel, then send
_slack.send_reply(token, channel_id, '<message>')
```

## Recipient Resolution

Resolve recipients via **d359 lookup first**, then fall back to live lookups.

### Resolution order

1. **d359 vault lookup** — Search `~/vault/d359/` for a matching person file by name/slug.
   Read the `channels:` frontmatter block for email, work_email, teams_upn, phone, slack.
   Use the field matching the requested channel (e.g., `teams_upn` for teams, `phone` for imessage).
   If no channel was specified by the user, use `preferred:` to pick the channel.

2. **MS Graph lookup** (teams/outlook only) — `GetMultipleUsersDetails` via agency mcp m365-user.

3. **Gmail search** (gmail only) — Search recent threads for the person's name to find their email.

### d359 lookup implementation

```python
import re, glob
from pathlib import Path

def resolve_d359(name):
    """Search d359 files by name. Returns (display_name, channels) or None.
    Uses scored matching: exact > full-name-in-stem > partial. Best match wins."""
    slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
    name_lower = name.lower()
    d = Path.home() / 'vault/d359'
    candidates = []  # list of (score, display_name, channels)
    for p in d.glob('*.md'):
        stem = p.stem.lower()
        # Strip " d359" suffix for display name
        display = p.stem.replace(' d359', '')
        # Score: 3 = exact match, 2 = full name in stem, 1 = slug/partial match
        name_part = stem.replace(' d359', '').strip()
        if name_lower == name_part:
            score = 3
        elif name_lower in stem:
            score = 2
        elif slug in re.sub(r'[^a-z0-9]+', '-', stem):
            score = 1
        else:
            continue
        text = p.read_text()
        m = re.match(r'^---\n(.*?)\n---\n', text, re.DOTALL)
        if not m: continue
        fm = m.group(1)
        if 'channels:' not in fm: continue
        ch = {}
        in_ch = False
        for line in fm.split('\n'):
            if line.strip() == 'channels:': in_ch = True; continue
            if in_ch and line.startswith('  ') and ':' in line:
                k, _, v = line.strip().partition(':')
                ch[k.strip()] = v.strip().strip('"')
            elif in_ch: break
        if ch:
            candidates.append((score, display, ch))
    if not candidates:
        return None
    # Sort by score descending; return best match
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates  # Return ALL candidates for ambiguity detection
```

### Recipient confirmation

After resolving a recipient, **ask the user to confirm** if ANY of these apply:

1. **Multiple d359 matches** — more than one person matched the input name (e.g., two Andys).
   Show the top matches and ask which one.
2. **Low-confidence match** — best match score is 1 (partial/slug only, no exact or full-name match).
   Show the match and ask: "Did you mean {name}?"
3. **No d359 match, falling back to Graph/search** — the person has never been messaged before
   (no d359 entry). Show the resolved name/email from Graph and ask: "Send to {name} ({email})?"

**Do NOT ask for confirmation** when:
- Best match score is 2 or 3 (exact or full-name match) AND it's the only match

This ensures known, unambiguous contacts resolve silently, while new or ambiguous contacts get a quick confirmation.

### Channel → field mapping

| Channel   | Primary field  | Fallback field |
|-----------|---------------|----------------|
| teams     | teams_upn     | work_email     |
| outlook   | work_email    | email          |
| gmail     | email         | work_email     |
| imessage  | phone         | email          |
| slack     | slack         | —              |

### Lazy write-back

When a recipient is resolved via Graph/Gmail search (not d359), write the result back
into their d359 file's `channels:` block so future lookups skip the live search.

## Tools

All tools are accessed via bash:
```bash
cd ~/i446-monorepo/tools/ibx
python3 -c "<inline script>"
```

The Agency MCP client is at `~/i446-monorepo/tools/ibx/agency_mcp.py`.
