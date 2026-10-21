---
name: "ibxi9"
description: "Work inbox pass (the 'ibx i9' card). Opens two tabs in the MSFT Chrome profile: Scout chat (https://scout.cloud.microsoft/#/chat) for the AI filter pass, then Outlook mail (https://outlook.cloud.microsoft/mail/) for JM's own pass over what is left. Usage: /ibxi9"
user-invocable: true
---

# /ibxi9 — work inbox pass

Two-stage inbox: an AI filter first (Scout), then JM reads what survives
(Outlook). For now the skill only sets the stage; the filter itself is run by
JM in Scout (2026-10-21). The claim hook claims the `ibx i9` card when this
skill is invoked; JM completes it in dtd (⌥↵) or via `/ibx0`.

## Steps

1. Open both tabs in the **MSFT** Chrome profile (`Profile 1`,
   jomckay@microsoft.com). `-n` + `--args` is what makes Chrome honour the
   profile; plain `open <url>` lands in the last-used profile (and is hijacked
   by the cmux shim):
   ```bash
   open -na "Google Chrome" --args --profile-directory="Profile 1" \
     "https://scout.cloud.microsoft/#/chat" \
     "https://outlook.cloud.microsoft/mail/"
   ```
2. Print the filter prompt below so JM can paste it into Scout, then stop.
   Do not read, triage or act on the mail yourself.

## Filter prompt (draft, refine as Scout's abilities become clear)

```
Go through my Outlook inbox: everything unread, plus anything that arrived in
the last 24 hours. Sort each message into exactly one bucket:
  REPLY   — a person is waiting on an answer or decision from me
  ACT     — no reply needed, but there is something I must do (approve, sign, book)
  FYI     — informational; I should skim it once
  NOISE   — automated notifications, newsletters, calendar churn, threads I am
            only cc'd on with no ask
Archive NOISE. Leave REPLY, ACT and FYI in the inbox, and give me the REPLY
and ACT lists with one line each: sender, ask, deadline if any.
```

## After the pass

JM reads what is left in Outlook. When the inbox is at zero, `/ibx0` marks
`ibx i9` (and the other inbox habits) done; nothing here logs points.
