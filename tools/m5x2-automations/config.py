"""
m5x2-automations config — edit here to add/remove auto-sign senders.
"""
import os
from pathlib import Path

# Emails whose forwarded AppFolio countersign requests are auto-signed
AUTOSIGN_SENDERS = [
    "stefanie@m5c7.com",
    "andrea@m5c7.com",
]

# AppFolio credentials (env var AF_PASSWORD or ~/.config/m5x2/af_password)
def _load_af_password() -> str:
    v = os.environ.get("AF_PASSWORD", "")
    if v:
        return v
    creds = Path.home() / ".config/m5x2/af_password"
    if creds.exists():
        return creds.read_text().strip()
    return ""

APPFOLIO_EMAIL    = "mckay@m5c7.com"
APPFOLIO_PASSWORD = _load_af_password()
APPFOLIO_SUBDOMAIN = "mckay"  # mckay.appfolio.com

# SQLite DB
DB_PATH = Path.home() / "vault/m5x2/automations.db"

# Dashboard port
DASHBOARD_PORT = 5557
