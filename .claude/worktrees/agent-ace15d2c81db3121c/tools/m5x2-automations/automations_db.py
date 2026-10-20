"""
Google Sheets store for m5x2 automation events (lease signings, etc.).
Sheet: "m5x2 Automations" → tab "Lease Signings"
"""
import json
import os
from datetime import datetime
from pathlib import Path

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SPREADSHEET_ID = "1TxXdor6iOPCXGANtHqNJR85K0IH5mQ48J60VqPQzPWs"
SHEET_NAME = "Lease Signings"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

_TOKEN_PATH = Path.home() / ".config/m5x2/sheets_token.json"


def _get_sheets_service():
    """Build a Google Sheets API service using saved OAuth token."""
    token_data = json.loads(_TOKEN_PATH.read_text())
    creds = Credentials(
        token=token_data.get("token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=token_data.get("client_id"),
        client_secret=token_data.get("client_secret"),
        scopes=SCOPES,
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("sheets", "v4", credentials=creds)


def log_signing(db_path: Path = None, *, property: str = "", unit: str = "",
                tenants: str = "", lease_type: str = "renewal",
                source_sender: str = "", source_subject: str = "",
                appfolio_url: str = "", status: str = "success"):
    """Append a row to the Lease Signings sheet."""
    svc = _get_sheets_service()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = [now, property, unit, tenants, lease_type, source_sender, source_subject, status]
    svc.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{SHEET_NAME}!A:H",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]},
    ).execute()


def count_successful(db_path: Path = None) -> int:
    """Count rows where status = 'success'."""
    rows = _read_all_rows()
    return sum(1 for r in rows if len(r) >= 8 and r[7] == "success")


def get_signings(db_path: Path = None, limit: int = 200):
    """Return recent signings as list of dicts."""
    headers = ["signed_at", "property", "unit", "tenants", "lease_type",
               "source_sender", "source_subject", "status"]
    rows = _read_all_rows()
    result = []
    for r in reversed(rows[-limit:]):
        d = {}
        for i, h in enumerate(headers):
            d[h] = r[i] if i < len(r) else ""
        result.append(d)
    return result


def get_summary(db_path: Path = None):
    """Return summary stats."""
    rows = _read_all_rows()
    year = str(datetime.now().year)
    total = renewals = new_l = failed = ytd = 0
    for r in rows:
        if len(r) < 8:
            continue
        status = r[7]
        if status == "success":
            total += 1
            if len(r) >= 5 and r[4] == "renewal":
                renewals += 1
            else:
                new_l += 1
            if r[0].startswith(year):
                ytd += 1
        else:
            failed += 1
    return {"total": total, "renewals": renewals, "new": new_l, "failed": failed, "ytd": ytd}


def _read_all_rows():
    """Read all data rows (skip header)."""
    svc = _get_sheets_service()
    result = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{SHEET_NAME}!A2:H",
    ).execute()
    return result.get("values", [])
