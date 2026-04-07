"""Tests for imsg.py — prevent regressions in iMessage display logic."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent))


class TestExtractAttributedText(unittest.TestCase):
    """extract_attributed_text must return the message body from binary attributedBody blobs."""

    def test_returns_message_text(self):
        """Real attributedBody data must yield the plain text content."""
        import sqlite3, shutil, imsg
        shutil.copy2(Path.home() / "Library/Messages/chat.db", "/tmp/imsg_test.db")
        conn = sqlite3.connect("/tmp/imsg_test.db")
        row = conn.execute(
            "SELECT attributedBody FROM message WHERE attributedBody IS NOT NULL LIMIT 1"
        ).fetchone()
        conn.close()
        if row is None:
            self.skipTest("No attributedBody rows in chat.db")
        text = imsg.extract_attributed_text(bytes(row[0]))
        self.assertIsInstance(text, str)
        self.assertGreater(len(text), 0, "extract_attributed_text returned empty string")

    def test_skips_ns_class_names(self):
        """Known NSObject class names must not be returned as message text."""
        import imsg
        payload = b"streamtyped\x00NSString\x00Hello, world!"
        text = imsg.extract_attributed_text(payload)
        self.assertNotIn("NSString", text)
        self.assertNotIn("streamtyped", text)

    def test_strips_typestream_prefix(self):
        """TypedStream '+X' length-prefix noise must be stripped from extracted text."""
        import imsg
        # Simulate "+pCheck the license plate..." pattern from real data
        payload = b"streamtyped\x00NSMutableAttributedString\x00NSString\x00+pCheck the license plate"
        text = imsg.extract_attributed_text(payload)
        self.assertFalse(text.startswith("+p"), f"TypedStream prefix not stripped: {text!r}")
        self.assertIn("Check", text)

    def test_raw_bytes_extracts_exact_text(self):
        """Raw-bytes scanner must handle length bytes that are uppercase/digit chars."""
        import imsg
        # Construct a real TypedStream '+' encoding where length byte = 0x41 ('A' = 65 chars)
        message = "A" * 65  # 65-char message; length byte is 0x41 = 'A' (uppercase)
        payload = b"\x04\x0bstreamtyped\x00" + b"\x2b" + bytes([len(message)]) + message.encode()
        text = imsg.extract_attributed_text(payload)
        self.assertEqual(text, message, f"Raw bytes scanner failed for uppercase length byte: {text!r}")

    def test_empty_data_returns_empty_string(self):
        """Empty or undecodable data must return empty string, not raise."""
        import imsg
        self.assertEqual(imsg.extract_attributed_text(b""), "")
        self.assertEqual(imsg.extract_attributed_text(b"\x00\x01\x02"), "")


class TestNormalizePhone(unittest.TestCase):
    """_normalize_phone must strip formatting and return last 10 digits."""

    def test_strips_formatting(self):
        import imsg
        self.assertEqual(imsg._normalize_phone("(509) 998-3309"), "5099983309")

    def test_strips_country_code(self):
        import imsg
        self.assertEqual(imsg._normalize_phone("+15099983309"), "5099983309")

    def test_international_truncates_to_10(self):
        import imsg
        result = imsg._normalize_phone("+964 7816073254")
        self.assertEqual(len(result), 10)

    def test_short_number_returned_as_is(self):
        import imsg
        self.assertEqual(imsg._normalize_phone("12345"), "12345")


class TestBuildContactCache(unittest.TestCase):
    """build_contact_cache must return a non-empty dict when AddressBook has contacts."""

    def test_returns_dict(self):
        import imsg
        cache = imsg.build_contact_cache()
        self.assertIsInstance(cache, dict)

    def test_populated_from_address_book(self):
        import imsg
        cache = imsg.build_contact_cache()
        self.assertGreater(len(cache), 0, "Contact cache is empty — AddressBook may be inaccessible")

    def test_values_are_non_empty_strings(self):
        import imsg
        cache = imsg.build_contact_cache()
        for key, value in list(cache.items())[:10]:
            self.assertIsInstance(key, str)
            self.assertGreater(len(key), 0)
            self.assertIsInstance(value, str)
            self.assertGreater(len(value), 0)


class TestResolveDisplayName(unittest.TestCase):
    """resolve_display_name must return contact name when known, else the raw identifier."""

    def test_resolves_known_number(self):
        import imsg
        with patch.object(imsg, "_contacts", {"5099983309": "Tim Engh"}):
            result = imsg.resolve_display_name("+15099983309")
        self.assertEqual(result, "Tim Engh")

    def test_falls_back_to_identifier(self):
        import imsg
        with patch.object(imsg, "_contacts", {}):
            result = imsg.resolve_display_name("+15078519145")
        self.assertEqual(result, "+15078519145")


class TestFetchThreadMessages(unittest.TestCase):
    """fetch_thread_messages must return messages even when text is NULL (attributedBody only)."""

    def _make_conn(self, rows):
        """Build an in-memory SQLite connection with fake message rows."""
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE message (
                ROWID INTEGER PRIMARY KEY, text TEXT, attributedBody BLOB,
                date INTEGER, is_from_me INTEGER, handle_id INTEGER
            )
        """)
        conn.execute("CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT, uncanonicalized_id TEXT)")
        conn.execute("CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER)")
        for r in rows:
            conn.execute(
                "INSERT INTO message VALUES (?,?,?,?,?,?)",
                (r["ROWID"], r.get("text"), r.get("attributedBody"), r["date"], r["is_from_me"], r.get("handle_id"))
            )
            conn.execute("INSERT INTO chat_message_join VALUES (1, ?)", (r["ROWID"],))
        return conn

    def test_returns_messages_with_plain_text(self):
        import imsg
        conn = self._make_conn([
            {"ROWID": 1, "text": "Hello there", "date": 700000000000000000, "is_from_me": 0}
        ])
        msgs = imsg.fetch_thread_messages(conn, 1)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["text"], "Hello there")

    def test_uses_attributed_body_when_text_is_null(self):
        import imsg
        # Create attributedBody that contains readable text after class names
        payload = b"streamtyped\x00NSMutableAttributedString\x00NSString\x00Hey this is the real message"
        conn = self._make_conn([
            {"ROWID": 1, "text": None, "attributedBody": payload, "date": 700000000000000000, "is_from_me": 0}
        ])
        msgs = imsg.fetch_thread_messages(conn, 1)
        self.assertEqual(len(msgs), 1, "Message with attributedBody only must not be dropped")
        self.assertIn("real message", msgs[0]["text"])

    def test_drops_messages_with_no_text_at_all(self):
        import imsg
        conn = self._make_conn([
            {"ROWID": 1, "text": None, "attributedBody": None, "date": 700000000000000000, "is_from_me": 0}
        ])
        msgs = imsg.fetch_thread_messages(conn, 1)
        self.assertEqual(len(msgs), 0, "Message with no text and no attributedBody must be dropped")


class TestCountImessageWithDictProcessed(unittest.TestCase):
    """count_imessage must use timestamp comparison when processed.json is a dict.

    Regression: processed.json migrated from list → dict format stores
    {chat_id: migration_ts}. The old code did set(dict) → set of keys, then
    subtracted ALL known contacts from unread threads. Any contact who had ever
    been processed showed count=0 even when new messages arrived, causing
    ibx_monitor to display "inbox zero" when messages were pending.
    """

    def _make_db_with_threads(self, threads):
        """Create an in-memory SQLite DB mimicking chat.db with given thread data.

        threads: list of (chat_identifier, msg_date, is_read, is_from_me)
        """
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, chat_identifier TEXT)")
        conn.execute("CREATE TABLE message (ROWID INTEGER PRIMARY KEY, date INTEGER, is_read INTEGER, is_from_me INTEGER)")
        conn.execute("CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER)")
        chat_ids = {}
        msg_id = 1
        for chat_identifier, msg_date, is_read, is_from_me in threads:
            if chat_identifier not in chat_ids:
                conn.execute("INSERT INTO chat (chat_identifier) VALUES (?)", (chat_identifier,))
                chat_ids[chat_identifier] = conn.execute(
                    "SELECT ROWID FROM chat WHERE chat_identifier=?", (chat_identifier,)
                ).fetchone()[0]
            conn.execute(
                "INSERT INTO message (ROWID, date, is_read, is_from_me) VALUES (?,?,?,?)",
                (msg_id, msg_date, is_read, is_from_me),
            )
            conn.execute(
                "INSERT INTO chat_message_join VALUES (?,?)",
                (chat_ids[chat_identifier], msg_id),
            )
            msg_id += 1
        conn.commit()
        return conn

    def test_new_message_from_known_contact_counts_as_pending(self):
        """A contact in processed.json with a NEW message (ts > stored ts) must be counted."""
        import ibx_status, json, tempfile, sqlite3

        stored_ts = 1_000_000
        new_msg_ts = 2_000_000  # newer than stored_ts → should show up

        conn = self._make_db_with_threads([
            ("+15551234567", new_msg_ts, 0, 0),  # unread, from them
        ])

        processed = {"+15551234567": stored_ts}
        processed_json = json.dumps(processed)

        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            f.write(processed_json)
            proc_path = f.name

        with patch("ibx_status.CHAT_DB", Path("/dev/null")), \
             patch("ibx_status.IMSG_PROCESSED", Path(proc_path)), \
             patch("shutil.copy2"), \
             patch("sqlite3.connect", return_value=conn):
            count = ibx_status.count_imessage()

        import os; os.unlink(proc_path)
        self.assertEqual(
            count, 1,
            "count_imessage must count a contact's new message even when that "
            "contact appears in processed.json — set(dict) subtraction was wrong."
        )

    def test_already_processed_message_not_counted(self):
        """A message with ts <= stored processed ts must NOT be counted."""
        import ibx_status, json, tempfile

        stored_ts = 2_000_000
        old_msg_ts = 1_000_000  # older than stored_ts → already handled

        conn = self._make_db_with_threads([
            ("+15551234567", old_msg_ts, 0, 0),
        ])

        processed = {"+15551234567": stored_ts}

        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            f.write(json.dumps(processed))
            proc_path = f.name

        with patch("ibx_status.CHAT_DB", Path("/dev/null")), \
             patch("ibx_status.IMSG_PROCESSED", Path(proc_path)), \
             patch("shutil.copy2"), \
             patch("sqlite3.connect", return_value=conn):
            count = ibx_status.count_imessage()

        import os; os.unlink(proc_path)
        self.assertEqual(count, 0, "Message already covered by stored ts must not be counted")

    def test_unknown_contact_still_counted(self):
        """A contact NOT in processed.json must be counted if they have unread messages."""
        import ibx_status, json, tempfile

        conn = self._make_db_with_threads([
            ("+15559999999", 1_000_000, 0, 0),
        ])

        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            f.write(json.dumps({}))  # empty processed
            proc_path = f.name

        with patch("ibx_status.CHAT_DB", Path("/dev/null")), \
             patch("ibx_status.IMSG_PROCESSED", Path(proc_path)), \
             patch("shutil.copy2"), \
             patch("sqlite3.connect", return_value=conn):
            count = ibx_status.count_imessage()

        import os; os.unlink(proc_path)
        self.assertEqual(count, 1, "Unknown contact with unread message must be counted")


class TestLoadProcessedMigration(unittest.TestCase):
    """load_processed must migrate list → dict using 0, not now_ts.

    Regression: old migration used now_ts so any unread message older than
    migration time was filtered out (latest_date <= now_ts), causing ibx_status
    to report 0 even when unread threads existed.
    """

    def test_list_migrates_to_dict_with_zero_cutoff(self):
        """Legacy list format must migrate to {id: 0}, not {id: now_ts}."""
        import imsg, json, tempfile
        data = ["+15551234567", "+15559999999"]
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump(data, f)
            path = f.name
        original = imsg.STATE_FILE
        try:
            from pathlib import Path
            imsg.STATE_FILE = Path(path)
            result = imsg.load_processed()
        finally:
            imsg.STATE_FILE = original
            import os; os.unlink(path)
        self.assertEqual(result, {"+15551234567": 0, "+15559999999": 0},
                         "Migration must use 0 so old messages still surface")

    def test_list_migration_zero_allows_old_messages_through_ibx_status(self):
        """After list→dict migration with 0, messages older than migration time must be counted."""
        import ibx_status, json, tempfile, sqlite3
        from pathlib import Path
        from unittest.mock import patch

        old_msg_ts = 1_000_000  # very old message

        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, chat_identifier TEXT)")
        conn.execute("CREATE TABLE message (ROWID INTEGER PRIMARY KEY, date INTEGER, is_read INTEGER, is_from_me INTEGER)")
        conn.execute("CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER)")
        conn.execute("INSERT INTO chat VALUES (1, '+15551234567')")
        conn.execute("INSERT INTO message VALUES (1, ?, 0, 0)", (old_msg_ts,))
        conn.execute("INSERT INTO chat_message_join VALUES (1, 1)")
        conn.commit()

        # processed.json after correct migration: stored=0
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump({"+15551234567": 0}, f)
            proc_path = f.name

        with patch("ibx_status.CHAT_DB", Path("/dev/null")), \
             patch("ibx_status.IMSG_PROCESSED", Path(proc_path)), \
             patch("shutil.copy2"), \
             patch("sqlite3.connect", return_value=conn):
            count = ibx_status.count_imessage()

        import os; os.unlink(proc_path)
        self.assertEqual(count, 1,
                         "Message older than migration time must count when stored=0 (not now_ts)")


if __name__ == "__main__":
    unittest.main()
