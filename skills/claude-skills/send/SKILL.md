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

Do NOT explain what you're doing. Do NOT ask for confirmation unless the message contains `<angle brackets>` (see AI assist below). Just execute.

## Syntax

```
/send <person> via <channel>: <message>
```

- **person**: name or email of the recipient
- **channel**: `teams`, `outlook`, `gmail`, `imessage`, `slack`
- **message**: the text to send. If any part is in `<angle brackets>`, treat it as an instruction for you to draft that portion.

### Examples

```
/send Luke Hoban via teams: here's the meeting link https://teams.microsoft.com/meet/123
/send Asha Sharma via outlook: Can we move our 1:1 to Thursday?
/send Mom via imessage: I'll be home by 6
/send Joe via teams: <tell him I liked his proposal and suggest we meet Thursday to discuss>
/send Carolina via outlook: <draft a professional reply declining the meeting but suggesting async followup>
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

# 1. Create/get the 1:1 chat
result = mcp.call_tool('teams', 'CreateChat', {
    'chatType': 'oneOnOne',
    'members_upns': ['<email>']
}, timeout=60)
chat_id = json.loads(result['content'][0]['text'])['id']

# 2. Send the message
mcp.call_tool('teams', 'PostMessage', {
    'chatId': chat_id,
    'content': '<message>',
    'contentType': 'text',
}, timeout=30)
```

### Outlook email
```python
import agency_mcp as mcp

mcp.call_tool('mail', 'SendEmailWithAttachments', {
    'to': ['<email>'],
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
msg['To'] = '<email>'
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
    """Search d359 files by name. Returns channels dict or None."""
    slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
    d = Path.home() / 'vault/d359'
    for p in d.glob('*.md'):
        stem = p.stem.lower()
        if slug in stem or name.lower() in stem:
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
            if ch: return ch
    return None
```

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
