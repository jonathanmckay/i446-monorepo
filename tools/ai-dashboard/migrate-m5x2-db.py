#!/usr/bin/env python3
"""
Migration script to add m5x2 columns to llm-sessions.db

Adds:
- user_id (TEXT) - user identifier (jm, lx, etc.)
- property_code (TEXT) - property/project code (r202, m221, etc.)
"""

import sqlite3
from pathlib import Path

DB_PATH = Path.home() / "vault" / "i447" / "i446" / "llm-sessions.db"

def migrate():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Check if columns already exist
    cursor.execute("PRAGMA table_info(sessions)")
    columns = [col[1] for col in cursor.fetchall()]

    if "user_id" not in columns:
        print("Adding user_id column...")
        cursor.execute("ALTER TABLE sessions ADD COLUMN user_id TEXT DEFAULT 'jm'")
        print("✓ user_id column added")
    else:
        print("✓ user_id column already exists")

    if "property_code" not in columns:
        print("Adding property_code column...")
        cursor.execute("ALTER TABLE sessions ADD COLUMN property_code TEXT")
        print("✓ property_code column added")
    else:
        print("✓ property_code column already exists")

    # Create indexes for new columns
    try:
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_property ON sessions(property_code)")
        print("✓ Indexes created")
    except Exception as e:
        print(f"Note: {e}")

    conn.commit()
    conn.close()
    print("\n✅ Migration complete!")
    print(f"Database: {DB_PATH}")

if __name__ == "__main__":
    migrate()
