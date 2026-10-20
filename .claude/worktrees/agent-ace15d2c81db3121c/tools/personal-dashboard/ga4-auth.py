#!/usr/bin/env python3
"""One-time OAuth flow for GA4 Analytics Data API.

Run this once to authorize the dashboard to read GA4 data:
  python3 ga4-auth.py

Requires ga4-oauth.keys.json (copy of your GCP OAuth client credentials).
Saves tokens to ga4-tokens.json.
"""

import json
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]
KEYS_FILE = Path(__file__).parent / "ga4-oauth.keys.json"
TOKENS_FILE = Path(__file__).parent / "ga4-tokens.json"


def main():
    if not KEYS_FILE.exists():
        print(f"Missing {KEYS_FILE}")
        print("Copy your GCP OAuth client credentials JSON here as ga4-oauth.keys.json")
        return

    flow = InstalledAppFlow.from_client_secrets_file(str(KEYS_FILE), SCOPES)
    creds = flow.run_local_server(port=8095)

    TOKENS_FILE.write_text(creds.to_json())
    print(f"Saved tokens to {TOKENS_FILE}")
    print("Dashboard can now access GA4 data.")


if __name__ == "__main__":
    main()
