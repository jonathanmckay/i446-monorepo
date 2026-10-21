#!/usr/bin/env python3
"""mg — Microsoft Graph mail from the terminal, via the Agency MCP mail server.

Straylight only. Agency is Microsoft's internal MCP tooling
(~/.config/agency/CurrentVersion/agency); it is already approved in the tenant
and carries this device's Entra identity, so no app registration and no
separate consent are needed. Direct MSAL/Graph-CLI auth does NOT work here:
device-code flow is blocked by Conditional Access and the generic "Microsoft
Graph Command Line Tools" app requires admin consent (verified 2026-10-21).

    mg.py mail [N] [--unread] [--preview]   # inbox, newest first (default 25)
    mg.py body <message-id>                 # one message, full text
    mg.py get '<odata query params>'        # raw escape hatch, JSON out
        e.g. mg.py get '?$top=5&$filter=isRead eq false'
    mg.py tools                             # what the mail server exposes

Sign-in: Agency opens a browser tab when its token expires. Finish that tab;
mg refuses to open a second one (EntraLoginPending).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "i446-monorepo" / "tools" / "ibx"))
import agency_mcp  # noqa: E402

SELECT = "id,subject,from,receivedDateTime,isRead,bodyPreview,importance,webLink"


def _text(result: dict) -> str:
    for c in result.get("content", []):
        if c.get("type") == "text":
            return c.get("text") or ""
    return ""


def _unwrap(raw: str) -> dict:
    """Agency double-encodes Graph: content[].text is JSON holding a
    rawResponse string that is itself JSON."""
    if not raw.strip():
        return {}
    outer = json.loads(raw)
    inner = outer.get("rawResponse", outer)
    return json.loads(inner) if isinstance(inner, str) else inner


def query(params: str, timeout: int = 90) -> dict:
    if not params.startswith("?"):
        params = "?" + params
    try:
        r = agency_mcp.call_tool("mail", "SearchMessagesQueryParameters",
                                 {"queryParameters": params}, timeout=timeout)
    except agency_mcp.EntraLoginPending as e:
        sys.exit(f"sign-in pending: {e}")
    raw = _text(r)
    if not raw.strip():
        # First call after a cold start can race the server coming up.
        r = agency_mcp.call_tool("mail", "SearchMessagesQueryParameters",
                                 {"queryParameters": params}, timeout=timeout)
        raw = _text(r)
    return _unwrap(raw)


def cmd_mail(args: list[str]) -> None:
    n = next((int(a) for a in args if a.isdigit()), 25)
    q = f"?$top={n}&$select={SELECT}&$orderby=receivedDateTime desc"
    if "--unread" in args:
        q += "&$filter=isRead eq false"
    for m in query(q).get("value", []):
        f = (m.get("from") or {}).get("emailAddress", {})
        who = f.get("name") or f.get("address") or "?"
        flag = " " if m.get("isRead") else "*"
        bang = "!" if m.get("importance") == "high" else " "
        print(f"{flag}{bang} {m['receivedDateTime'][:16]}  {who:<28.28}  {(m.get('subject') or '')[:70]}")
        if "--preview" in args:
            print("      " + (m.get("bodyPreview") or "").replace("\n", " ").strip()[:170])
        if "--ids" in args:
            print("      id: " + m.get("id", ""))


def cmd_body(args: list[str]) -> None:
    mid = args[0]
    d = query(f"/{mid}?$select=subject,from,receivedDateTime,body")
    f = (d.get("from") or {}).get("emailAddress", {})
    print(f"From: {f.get('name')} <{f.get('address')}>")
    print(f"Date: {d.get('receivedDateTime')}")
    print(f"Subj: {d.get('subject')}\n")
    print((d.get("body") or {}).get("content", ""))


def main(argv: list[str]) -> None:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return
    cmd, args = argv[0], argv[1:]
    if cmd == "mail":
        cmd_mail(args)
    elif cmd == "body":
        cmd_body(args)
    elif cmd == "get":
        print(json.dumps(query(args[0]), indent=1, ensure_ascii=False))
    elif cmd == "tools":
        srv = agency_mcp.get_server("mail")
        print(json.dumps(srv, indent=1, default=str)[:2000])
    else:
        sys.exit(f"unknown command {cmd!r}; see --help")


if __name__ == "__main__":
    main(sys.argv[1:])
