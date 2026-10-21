#!/usr/bin/env python3
"""mg: Microsoft Graph from the terminal (Straylight only; the Microsoft tenant's
Conditional Access needs the Intune-managed device, so this does not run on Ix).

Uses MSAL device-code auth with the first-party Graph PowerShell client id, which
the tenant already consents to, so no app registration is needed. The token
cache persists at ~/.config/msgraph/token_cache.json; after the first login
refresh is silent.

    mg.py login                  # device code: prints URL + code, finish in Chrome
    mg.py me                     # who am I
    mg.py mail [N] [--unread]    # last N inbox messages (default 25), compact
    mg.py get <graph path> [--raw]   # any GET, e.g. 'me/messages?$top=5'
    mg.py logout
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request

import msal

CLIENT_ID = os.environ.get("MG_CLIENT_ID", "14d82eec-204b-4c2f-b7e8-296a70dab67e")  # Microsoft Graph PowerShell
AUTHORITY = os.environ.get("MG_AUTHORITY", "https://login.microsoftonline.com/organizations")
SCOPES = ["User.Read", "Mail.ReadWrite", "Calendars.Read"]
CACHE = os.path.expanduser("~/.config/msgraph/token_cache.json")
GRAPH = "https://graph.microsoft.com/v1.0/"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CHROME_PROFILE = os.environ.get("MG_CHROME_PROFILE", "Profile 1")  # MSFT, jomckay@microsoft.com


def _app() -> tuple[msal.PublicClientApplication, msal.SerializableTokenCache]:
    cache = msal.SerializableTokenCache()
    if os.path.exists(CACHE):
        with open(CACHE) as f:
            cache.deserialize(f.read())
    app = msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY, token_cache=cache)
    return app, cache


def _save(cache: msal.SerializableTokenCache) -> None:
    if cache.has_state_changed:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        with open(CACHE, "w") as f:
            f.write(cache.serialize())
        os.chmod(CACHE, 0o600)


def token(interactive: bool = False) -> str:
    app, cache = _app()
    accounts = app.get_accounts()
    result = app.acquire_token_silent(SCOPES, account=accounts[0]) if accounts else None
    if not result and interactive:
        if os.environ.get("MG_DEVICE_CODE"):
            # Blocked by the Microsoft tenant's Conditional Access (2026-10-21:
            # "authentication flow that is restricted by your admin"); kept for
            # other tenants.
            flow = app.initiate_device_flow(scopes=SCOPES)
            if "user_code" not in flow:
                sys.exit(f"device flow failed: {json.dumps(flow, indent=1)}")
            print(flow["message"], flush=True)
            result = app.acquire_token_by_device_flow(flow)
        else:
            # Auth-code + PKCE with a localhost redirect, in the MSFT Chrome
            # profile (plain `open` is hijacked by the cmux shim, and only that
            # profile carries the Intune device identity CA requires).
            import webbrowser
            chrome = webbrowser.GenericBrowser(
                [CHROME, f"--profile-directory={CHROME_PROFILE}", "%s"])
            webbrowser.open = chrome.open  # MSAL calls webbrowser.open() on macOS
            print("sign in via Chrome (MSFT profile) ...", flush=True)
            result = app.acquire_token_interactive(
                SCOPES, timeout=300,
                success_template="<html><body>mg: signed in. You can close this tab.</body></html>")
    _save(cache)
    if not result or "access_token" not in result:
        err = (result or {}).get("error_description", "no cached token; run: mg.py login")
        sys.exit(f"auth failed: {err}")
    return result["access_token"]


def get(path: str) -> dict:
    url = path if path.startswith("http") else GRAPH + path.lstrip("/")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token()}",
                                               "Prefer": 'outlook.body-content-type="text"'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def cmd_mail(args: list[str]) -> None:
    n = next((int(a) for a in args if a.isdigit()), 25)
    flt = "&$filter=isRead eq false" if "--unread" in args else ""
    q = (f"me/mailFolders/inbox/messages?$top={n}&$orderby=receivedDateTime desc"
         f"&$select=id,subject,from,receivedDateTime,isRead,bodyPreview,importance,"
         f"conversationId,webLink{flt}")
    for m in get(q).get("value", []):
        frm = (m.get("from") or {}).get("emailAddress", {})
        flag = " " if m.get("isRead") else "*"
        print(f"{flag} {m['receivedDateTime'][:16]}  {frm.get('name') or frm.get('address','?'):<28.28}  {m.get('subject','')[:70]}")
        if "--preview" in args:
            print("     " + (m.get("bodyPreview") or "").replace("\n", " ")[:160])


def main(argv: list[str]) -> None:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__); return
    cmd, args = argv[0], argv[1:]
    if cmd == "login":
        token(interactive=True)
        me = get("me")
        print(f"signed in as {me.get('displayName')} <{me.get('mail') or me.get('userPrincipalName')}>")
    elif cmd == "logout":
        if os.path.exists(CACHE):
            os.remove(CACHE)
        print("token cache removed")
    elif cmd == "me":
        me = get("me")
        print(json.dumps({k: me.get(k) for k in ("displayName", "mail", "userPrincipalName", "jobTitle")}, indent=1))
    elif cmd == "mail":
        cmd_mail(args)
    elif cmd == "get":
        print(json.dumps(get(args[0]), indent=1, ensure_ascii=False))
    else:
        sys.exit(f"unknown command {cmd!r}; see --help")


if __name__ == "__main__":
    main(sys.argv[1:])
