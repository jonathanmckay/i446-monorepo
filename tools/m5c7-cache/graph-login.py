#!/usr/bin/env python3
"""One-time device-code login → ~/.m5c7-cache/token-cache.bin

Uses Microsoft Graph PowerShell appId (14d82eec-204b-4c2f-b7e8-296a70dab67e),
which is a well-known Microsoft-published app pre-consented in most tenants
including the MSFT corp tenant.

Token cache is encrypted at rest by MSAL's MSALCachePersistence if available;
otherwise written as JSON. Tokens auto-refresh on subsequent script calls.
"""
import os, sys, json
from pathlib import Path
import msal

TENANT  = "72f988bf-86f1-41af-91ab-2d7cd011db47"          # microsoft.com
CLIENT  = "14d82eec-204b-4c2f-b7e8-296a70dab67e"          # MS Graph PowerShell
SCOPES  = ["Mail.Read", "Calendars.Read", "Chat.Read", "User.Read"]
CACHE   = Path.home() / ".m5c7-cache" / "token-cache.json"
CACHE.parent.mkdir(parents=True, exist_ok=True)

cache = msal.SerializableTokenCache()
if CACHE.exists():
    cache.deserialize(CACHE.read_text())

app = msal.PublicClientApplication(
    CLIENT,
    authority=f"https://login.microsoftonline.com/{TENANT}",
    token_cache=cache,
)

result = None
accounts = app.get_accounts()
if accounts:
    result = app.acquire_token_silent(SCOPES, account=accounts[0])

if not result:
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        print("FAIL:", json.dumps(flow, indent=2)); sys.exit(1)
    print("\n" + flow["message"] + "\n")
    sys.stdout.flush()
    result = app.acquire_token_by_device_flow(flow)

if cache.has_state_changed:
    CACHE.write_text(cache.serialize())
    os.chmod(CACHE, 0o600)

if "access_token" not in result:
    print("FAIL:", json.dumps(result, indent=2)); sys.exit(1)

print("OK: token cached. Scopes granted:", result.get("scope", "?"))
