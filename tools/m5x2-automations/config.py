"""
m5x2-automations config — edit here to add/remove auto-sign senders.
"""
import os
from pathlib import Path

# Emails whose forwarded AppFolio countersign requests are auto-signed
AUTOSIGN_SENDERS = [
    "stefanie@m5c7.com",
    "andie@m5c7.com",   # add when active
]

# AppFolio credentials (set AF_PASSWORD env var; don't hardcode password)
APPFOLIO_EMAIL    = "mckay@m5c7.com"
APPFOLIO_PASSWORD = os.environ.get("AF_PASSWORD", "")
APPFOLIO_SUBDOMAIN = "mckay"  # mckay.appfolio.com

# SQLite DB
DB_PATH = Path.home() / "vault/m5x2/automations.db"

# Dashboard port
DASHBOARD_PORT = 5557
