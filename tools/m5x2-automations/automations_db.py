"""
SQLite store for m5x2 automation events (lease signings, etc.).
"""
import sqlite3
from datetime import datetime
from pathlib import Path


def _conn(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(str(db_path))


def init_db(db_path: Path):
    with _conn(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lease_signings (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                signed_at      TEXT    DEFAULT (datetime('now')),
                property       TEXT,
                unit           TEXT,
                tenants        TEXT,
                lease_type     TEXT,   -- 'renewal' | 'new'
                source_sender  TEXT,
                source_subject TEXT,
                appfolio_url   TEXT,
                status         TEXT    -- 'success' | 'failed' | 'timeout'
            )
        """)
        conn.commit()


def log_signing(db_path: Path, *, property: str = "", unit: str = "",
                tenants: str = "", lease_type: str = "renewal",
                source_sender: str = "", source_subject: str = "",
                appfolio_url: str = "", status: str = "success"):
    init_db(db_path)
    with _conn(db_path) as conn:
        conn.execute("""
            INSERT INTO lease_signings
                (property, unit, tenants, lease_type, source_sender,
                 source_subject, appfolio_url, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (property, unit, tenants, lease_type, source_sender,
              source_subject, appfolio_url, status))
        conn.commit()


def get_signings(db_path: Path, limit: int = 200):
    init_db(db_path)
    with _conn(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT * FROM lease_signings
            ORDER BY signed_at DESC LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_summary(db_path: Path):
    init_db(db_path)
    with _conn(db_path) as conn:
        total    = conn.execute("SELECT COUNT(*) FROM lease_signings WHERE status='success'").fetchone()[0]
        renewals = conn.execute("SELECT COUNT(*) FROM lease_signings WHERE status='success' AND lease_type='renewal'").fetchone()[0]
        new_l    = conn.execute("SELECT COUNT(*) FROM lease_signings WHERE status='success' AND lease_type='new'").fetchone()[0]
        failed   = conn.execute("SELECT COUNT(*) FROM lease_signings WHERE status!='success'").fetchone()[0]
        ytd      = conn.execute("""
            SELECT COUNT(*) FROM lease_signings
            WHERE status='success' AND strftime('%Y', signed_at) = strftime('%Y', 'now')
        """).fetchone()[0]
    return {"total": total, "renewals": renewals, "new": new_l, "failed": failed, "ytd": ytd}
