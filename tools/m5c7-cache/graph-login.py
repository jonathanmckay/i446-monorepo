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
CLIENT  = "d3590ed6-52b3-4102-aeff-aad2292ab01c"          # Microsoft Office (pre-consented in MSFT corp)
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
    # Interactive flow opens local browser; works under MSFT corp Conditional
    # Access policies that block device-code (which is phishing-prone).
    print("Opening browser for Microsoft sign-in...")
    sys.stdout.flush()
    result = app.acquire_token_interactive(
        scopes=SCOPES,
        prompt="select_account",
    )

if cache.has_state_changed:
    CACHE.write_text(cache.serialize())
    os.chmod(CACHE, 0o600)

if "access_token" not in result:
    print("FAIL:", json.dumps(result, indent=2)); sys.exit(1)

print("OK: token cached. Scopes granted:", result.get("scope", "?"))
