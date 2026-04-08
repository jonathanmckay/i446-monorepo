#!/usr/bin/env python3
"""One-time OAuth setup for Google Sheets access. Run interactively."""
import json
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
GCP_CREDS = Path.home() / ".config/google-calendar-mcp/gcp-oauth.keys.json"
TOKEN_OUT = Path.home() / ".config/m5x2/sheets_token.json"

def main():
    TOKEN_OUT.parent.mkdir(parents=True, exist_ok=True)
    flow = InstalledAppFlow.from_client_secrets_file(str(GCP_CREDS), SCOPES)
    creds = flow.run_local_server(port=0)
    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
    }
    TOKEN_OUT.write_text(json.dumps(token_data, indent=2))
    print(f"Sheets token saved to {TOKEN_OUT}")

if __name__ == "__main__":
    main()
