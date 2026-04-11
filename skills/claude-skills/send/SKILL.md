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

If only a name is given (not an email), resolve via:
1. **Teams**: `agency mcp m365-user` → `GetMultipleUsersDetails` with display name search
2. **Known contacts**: check common contacts below

### Known contacts (shorthand)

| Name | Email | Teams UPN |
|------|-------|-----------|
| Luke Hoban | lukehoban@github.com | lukehoban@microsoft.com |
| Asha Sharma | ashasharma@microsoft.com | ashasharma@microsoft.com |
| Carolina Pinzon | cpinzon@microsoft.com | cpinzon@microsoft.com |
| John Maeda | johnmaeda@microsoft.com | johnmaeda@microsoft.com |
| Andy Sharman | andysh@github.com | andysh@microsoft.com |
| Joe Ingraham | joeing@microsoft.com | joeing@microsoft.com |

If the person isn't in the table, use `GetMultipleUsersDetails` to resolve their UPN.

## Tools

All tools are accessed via bash:
```bash
cd ~/i446-monorepo/tools/ibx
python3 -c "<inline script>"
```

The Agency MCP client is at `~/i446-monorepo/tools/ibx/agency_mcp.py`.
