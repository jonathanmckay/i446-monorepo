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


if __name__ == "__main__":
    unittest.main()
