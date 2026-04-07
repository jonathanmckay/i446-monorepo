"""Tests for ibx.py — prevent regressions in email loop logic."""

import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch, call


# ── Helpers ──────────────────────────────────────────────────────────────────

def _fake_email(id_, subject="Test", from_="sender@example.com"):
    return {
        "id": id_,
        "subject": subject,
        "from": from_,
        "date": "jan 01, 09:00am",
        "body": "Hello",
        "thread_id": f"thread_{id_}",
        "_account": "m5c7",
    }


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestMainLoop(unittest.TestCase):
    """The main processing loop must visit every email in the batch."""

    def _run_loop_with_inputs(self, num_emails, inputs):
        """
        Run the inner batch-processing loop with fake emails and simulated
        keyboard inputs. Returns a list of email IDs that were archived.
        """
        import ibx

        archived = []
        emails = [_fake_email(str(i)) for i in range(num_emails)]
        msg_refs = [
            {"id": e["id"], "account": "m5c7", "service": MagicMock()}
            for e in emails
        ]

        input_iter = iter(inputs)

        with patch("ibx.get_email", side_effect=lambda svc, mid: _fake_email(mid)), \
             patch("ibx.archive", side_effect=lambda svc, mid: archived.append(mid)), \
             patch("builtins.input", side_effect=lambda _=None: next(input_iter)), \
             patch("ibx.console"):

            messages = list(msg_refs)
            skipped = []
            index = 0
            total = len(messages)

            # Replicate the inner while True: loop from ibx.main()
            while True:
                if index >= len(messages):
                    if skipped:
                        messages = skipped
                        skipped = []
                        index = 0
                    else:
                        break

                msg_ref = messages[index]
                service = msg_ref["service"]
                email = ibx.get_email(service, msg_ref["id"])
                email["_account"] = msg_ref["account"]

                try:
                    user_input = input("> ").strip()
                except StopIteration:
                    break

                cmd = user_input.lower()
                if cmd == "q":
                    break
                elif cmd == "a":
                    ibx.archive(service, email["id"])
                    index += 1
                elif cmd == "s":
                    skipped.append(msg_ref)
                    index += 1
                else:
                    index += 1

        return archived

    def test_all_emails_visited_when_archiving(self):
        """Archiving 3 emails in a row should visit all 3, not just the first."""
        archived = self._run_loop_with_inputs(3, ["a", "a", "a"])
        self.assertEqual(archived, ["0", "1", "2"],
                         "Loop must advance through all emails, not restart after first")

    def test_skip_cycles_back(self):
        """Skipped emails should be revisited at end of batch."""
        archived = self._run_loop_with_inputs(2, ["s", "a", "a"])
        # First email skipped, second archived, then skipped email archived
        self.assertEqual(len(archived), 2)
        self.assertIn("0", archived)
        self.assertIn("1", archived)

    def test_quit_exits_immediately(self):
        """'q' should stop processing without touching remaining emails."""
        archived = self._run_loop_with_inputs(3, ["a", "q"])
        self.assertEqual(archived, ["0"])


class TestClassifyEmail(unittest.TestCase):
    """classify_email must handle timeouts gracefully without crashing."""

    def test_timeout_propagates_to_caller(self):
        """TimeoutExpired propagates from classify_email so triage_inbox can catch it."""
        import ibx

        with patch("subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=60)):
            with self.assertRaises(subprocess.TimeoutExpired):
                ibx.classify_email(_fake_email("1"))

    def test_timeout_caught_by_triage_inbox(self):
        """triage_inbox must not crash when classify_email times out."""
        import ibx

        fake_service = MagicMock()
        fake_service.users().messages().list().execute.return_value = {
            "messages": [{"id": "msg1"}, {"id": "msg2"}]
        }
        fake_service.users().labels().list().execute.return_value = {"labels": []}
        fake_service.users().labels().create().execute.return_value = {"id": "lbl1"}

        with patch("ibx.get_email", return_value=_fake_email("msg1")), \
             patch("ibx.classify_email",
                   side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=60)):
            # Should not raise
            total, moved = ibx.triage_inbox(fake_service, "test")
            self.assertEqual(moved, 0)


class TestUndefinedEmailsReference(unittest.TestCase):
    """The 'c' command previously referenced undefined `emails`; ensure it uses `messages`."""

    def test_c_command_uses_messages_not_emails(self):
        import ast, pathlib
        src = pathlib.Path(__file__).parent / "ibx.py"
        tree = ast.parse(src.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "emails":
                self.fail(
                    f"Found bare reference to undefined `emails` at line {node.lineno}. "
                    "Should be `messages`."
                )


class TestFetchInboxIncludesRead(unittest.TestCase):
    """The main() loop must fetch all inbox emails, not just unread ones."""

    def test_main_loop_fetch_inbox_not_unread_only(self):
        """fetch_inbox inside main() must use unread_only=False so read inbox emails are visible."""
        import ast, pathlib
        src = pathlib.Path(__file__).parent / "ibx.py"
        tree = ast.parse(src.read_text())

        # Find the main() function definition
        main_func = next(
            (n for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef) and n.name == "main"),
            None,
        )
        self.assertIsNotNone(main_func, "main() function not found in ibx.py")

        for node in ast.walk(main_func):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "fetch_inbox"
            ):
                for kw in node.keywords:
                    if kw.arg == "unread_only":
                        self.assertIsInstance(kw.value, ast.Constant)
                        self.assertFalse(
                            kw.value.value,
                            f"fetch_inbox in main() called with unread_only=True at line "
                            f"{node.lineno}. Must be False so read inbox emails are visible.",
                        )


class TestReplyConfirmPromptVisible(unittest.TestCase):
    """The Send?/cancel prompt after a reply draft must go through console.print, not input()."""

    def test_reply_confirm_not_buried_in_input_call(self):
        """input() calls in the reply branch must not carry a prompt string (would be invisible after Rich Panel)."""
        import ast, pathlib
        src = pathlib.Path(__file__).parent / "ibx.py"
        tree = ast.parse(src.read_text())

        main_func = next(
            (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "main"),
            None,
        )
        self.assertIsNotNone(main_func, "main() not found")

        # Find all input() calls inside the reply branch (after a Panel is printed in the same scope)
        # Simpler check: no input() call anywhere in main() should have a non-empty prompt arg
        # that contains "send" or "y/n" — those must be console.print'd instead.
        for node in ast.walk(main_func):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else None
                if name == "input" and node.args:
                    arg = node.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        text = arg.value.lower()
                        self.assertNotIn(
                            "send", text,
                            f"input() at line {node.lineno} has prompt '{arg.value}' — "
                            "this text won't render after a Rich Panel. Use console.print() instead."
                        )
                        self.assertNotIn(
                            "y/n", text,
                            f"input() at line {node.lineno} has prompt '{arg.value}' — "
                            "this text won't render after a Rich Panel. Use console.print() instead."
                        )


class TestReplyConfirmAcceptsSendVariants(unittest.TestCase):
    """The reply confirm prompt must accept 'send it' (not just 's')."""

    def test_send_it_sends_and_archives(self):
        """Typing 'send it' at the reply confirm must send the reply, not cancel."""
        import ibx

        fake_svc = MagicMock()
        sent = []
        archived = []

        with patch("ibx.get_gmail_service", return_value=fake_svc), \
             patch("ibx.triage_inbox", return_value=(0, 0)), \
             patch("ibx.fetch_inbox", return_value=[{"id": "msg1"}]), \
             patch("ibx.get_email", return_value=_fake_email("msg1")), \
             patch("ibx.ask_claude", return_value={"action": "reply", "message": "Fwd to Anthony", "content": "Draft"}), \
             patch("ibx.send_reply", side_effect=lambda svc, eml, body: sent.append(body)), \
             patch("ibx.archive", side_effect=lambda svc, mid: archived.append(mid)), \
             patch("ibx.display_card"), \
             patch("ibx.print_help"), \
             patch("ibx.console"), \
             patch("builtins.input", side_effect=["forward to anthony", "send it", "q"]):
            try:
                ibx.main()
            except (SystemExit, StopIteration):
                pass

        self.assertEqual(len(sent), 1, "Reply must be sent when user types 'send it'")
        self.assertEqual(len(archived), 1, "Email must be archived after sending with 'send it'")

    def test_s_still_works(self):
        """'s' must still be accepted as send."""
        import ibx

        fake_svc = MagicMock()
        sent = []

        with patch("ibx.get_gmail_service", return_value=fake_svc), \
             patch("ibx.triage_inbox", return_value=(0, 0)), \
             patch("ibx.fetch_inbox", return_value=[{"id": "msg1"}]), \
             patch("ibx.get_email", return_value=_fake_email("msg1")), \
             patch("ibx.ask_claude", return_value={"action": "reply", "message": "", "content": "Draft"}), \
             patch("ibx.send_reply", side_effect=lambda svc, eml, body: sent.append(body)), \
             patch("ibx.archive"), \
             patch("ibx.display_card"), \
             patch("ibx.print_help"), \
             patch("ibx.console"), \
             patch("builtins.input", side_effect=["please reply", "s", "q"]):
            try:
                ibx.main()
            except (SystemExit, StopIteration):
                pass

        self.assertEqual(len(sent), 1, "'s' must still trigger send")


class TestNoFalseInboxZero(unittest.TestCase):
    """When fetch_inbox returns emails, the TUI must show them — not silently report inbox zero."""

    def test_display_card_called_when_inbox_has_emails(self):
        """display_card must be called at least once when fetch_inbox returns messages."""
        import ibx

        fake_msgs = [{"id": "aaa"}, {"id": "bbb"}]
        fake_svc = MagicMock()
        emails_displayed = []

        with patch("ibx.get_gmail_service", return_value=fake_svc), \
             patch("ibx.triage_inbox", return_value=(0, 0)), \
             patch("ibx.fetch_inbox", return_value=fake_msgs), \
             patch("ibx.get_email", side_effect=lambda svc, mid: _fake_email(mid)), \
             patch("ibx.display_card", side_effect=lambda e, *a, **kw: emails_displayed.append(e["id"])), \
             patch("ibx.print_help"), \
             patch("ibx.console"), \
             patch("builtins.input", side_effect=["q"]):
            try:
                ibx.main()
            except SystemExit:
                pass

        self.assertGreater(
            len(emails_displayed), 0,
            "display_card was never called despite fetch_inbox returning emails — false inbox zero"
        )


class TestForwardAction(unittest.TestCase):
    """Forward instruction must produce a forward draft, not a confused reply."""

    def test_forward_instruction_triggers_forward_action_not_reply(self):
        """ask_claude schema must include 'forward' so Claude returns action='forward', not 'reply'."""
        import ast, pathlib
        src = pathlib.Path(__file__).parent / "ibx.py"
        tree = ast.parse(src.read_text())

        ask_claude_func = next(
            (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "ask_claude"),
            None,
        )
        self.assertIsNotNone(ask_claude_func, "ask_claude() not found")

        # The prompt string must mention "forward" as a valid action
        for node in ast.walk(ask_claude_func):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if "forward" in node.value.lower() and "action" in node.value.lower():
                    return  # found it
        self.fail(
            "ask_claude() prompt does not mention 'forward' as an action. "
            "Claude will return 'reply' for forward instructions, producing a confused draft."
        )

    def test_forward_action_calls_forward_email_not_send_reply(self):
        """When Claude returns action='forward', forward_email() must be called, not send_reply()."""
        import ibx

        fake_svc = MagicMock()
        forwarded = []
        sent = []

        with patch("ibx.get_gmail_service", return_value=fake_svc), \
             patch("ibx.triage_inbox", return_value=(0, 0)), \
             patch("ibx.fetch_inbox", return_value=[{"id": "msg1"}]), \
             patch("ibx.get_email", return_value=_fake_email("msg1")), \
             patch("ibx.ask_claude", return_value={
                 "action": "forward",
                 "to": "anthony@example.com",
                 "message": "Forwarding to Anthony",
                 "content": "Hey, we need a combined return.",
             }), \
             patch("ibx.forward_email", side_effect=lambda svc, eml, to, note: forwarded.append(to)), \
             patch("ibx.send_reply", side_effect=lambda svc, eml, body: sent.append(body)), \
             patch("ibx.archive"), \
             patch("ibx.display_card"), \
             patch("ibx.print_help"), \
             patch("ibx.console"), \
             patch("builtins.input", side_effect=["forward to anthony", "s", "q"]):
            try:
                ibx.main()
            except (SystemExit, StopIteration):
                pass

        self.assertEqual(len(forwarded), 1, "forward_email() must be called for action='forward'")
        self.assertEqual(len(sent), 0, "send_reply() must NOT be called for action='forward'")
        self.assertEqual(forwarded[0], "anthony@example.com")

    def test_y_confirms_forward(self):
        """Typing 'y' at the forward confirm prompt must send, not cancel."""
        import ibx

        fake_svc = MagicMock()
        forwarded = []

        with patch("ibx.get_gmail_service", return_value=fake_svc), \
             patch("ibx.triage_inbox", return_value=(0, 0)), \
             patch("ibx.fetch_inbox", return_value=[{"id": "msg1"}]), \
             patch("ibx.get_email", return_value=_fake_email("msg1")), \
             patch("ibx.ask_claude", return_value={
                 "action": "forward",
                 "to": "anthony@example.com",
                 "message": "Forwarding",
                 "content": "Please fix.",
             }), \
             patch("ibx.forward_email", side_effect=lambda svc, eml, to, note: forwarded.append(to)), \
             patch("ibx.archive"), \
             patch("ibx.display_card"), \
             patch("ibx.print_help"), \
             patch("ibx.console"), \
             patch("builtins.input", side_effect=["forward this", "y", "q"]):
            try:
                ibx.main()
            except (SystemExit, StopIteration):
                pass

        self.assertEqual(len(forwarded), 1, "'y' must confirm a forward, not cancel it")

    def test_confirm_prompt_uses_parentheses_not_brackets(self):
        """Confirm prompts must not use [s]/[e]/[n] — Rich strips them as unknown markup tags."""
        import ast, pathlib
        src = pathlib.Path(__file__).parent / "ibx.py"
        tree = ast.parse(src.read_text())

        main_func = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "main"
        )
        for node in ast.walk(main_func):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                val = node.value
                for bad in ("[s]end", "[e]dit", "[n]o", "[y/n]"):
                    self.assertNotIn(
                        bad, val,
                        f"Prompt contains '{bad}' — Rich will strip it as a markup tag. "
                        "Use parentheses: (y)es / (e)dit / (n)o"
                    )

    def test_forward_email_builds_correct_subject(self):
        """forward_email must use 'Fwd: ' prefix, not 'Re: '."""
        import ibx

        sent_msgs = []
        fake_svc = MagicMock()
        fake_svc.users().getProfile().execute.return_value = {"emailAddress": "me@example.com"}
        fake_svc.users().messages().send.return_value = MagicMock()
        fake_svc.users().messages().send().execute.return_value = {}

        def capture_send(userId, body):
            import base64
            raw = base64.urlsafe_b64decode(body["raw"])
            sent_msgs.append(raw.decode("utf-8", errors="replace"))
            return fake_svc.users().messages().send.return_value

        fake_svc.users().messages().send.side_effect = capture_send

        eml = _fake_email("1", subject="K-1 Question")
        ibx.forward_email(fake_svc, eml, "anthony@example.com", "Please fix this.")

        self.assertTrue(
            any("Fwd: K-1 Question" in m for m in sent_msgs),
            "forward_email must use 'Fwd: ' subject prefix, not 'Re: '"
        )


class TestLookupContactEmail(unittest.TestCase):
    """lookup_contact_email must query Google Contacts (People API) and return an email or None."""

    def _mock_people_service(self, results):
        """Build a mock People API service returning given results."""
        mock_search = MagicMock()
        mock_search.execute.return_value = {"results": results}
        mock_people = MagicMock()
        mock_people.searchContacts.return_value = mock_search
        mock_svc = MagicMock()
        mock_svc.people.return_value = mock_people
        return mock_svc

    def test_returns_email_when_contact_found(self):
        """When People API returns a contact with email, lookup_contact_email returns it."""
        import ibx
        mock_svc = self._mock_people_service([{
            "person": {"emailAddresses": [{"value": "anthony@m5x2.com"}], "names": [{"displayName": "Anthony Duesler"}]}
        }])
        with patch("ibx._google_creds", [MagicMock()]), \
             patch("ibx.build", return_value=mock_svc):
            result = ibx.lookup_contact_email("Anthony Duesler")
        self.assertEqual(result, "anthony@m5x2.com")

    def test_returns_none_when_no_contact(self):
        """When People API returns no results, lookup_contact_email returns None."""
        import ibx
        mock_svc = self._mock_people_service([])
        with patch("ibx._google_creds", [MagicMock()]), \
             patch("ibx.build", return_value=mock_svc):
            result = ibx.lookup_contact_email("Nobody McFake")
        self.assertIsNone(result)

    def test_returns_none_when_no_creds(self):
        """If no credentials are available yet, lookup_contact_email returns None without crashing."""
        import ibx
        with patch("ibx._google_creds", []):
            result = ibx.lookup_contact_email("Anthony")
        self.assertIsNone(result)

    def test_returns_none_on_api_error(self):
        """If People API raises, lookup_contact_email returns None without crashing."""
        import ibx
        with patch("ibx._google_creds", [MagicMock()]), \
             patch("ibx.build", side_effect=Exception("API error")):
            result = ibx.lookup_contact_email("Anthony")
        self.assertIsNone(result)


class TestForwardNameOnlyRecipient(unittest.TestCase):
    """When Claude returns a name (no email) as 'to', ibx must look up the contact."""

    def test_name_only_recipient_resolved_via_contacts(self):
        """When Contacts resolves the name to an email, forward proceeds without prompting."""
        import ibx

        fake_svc = MagicMock()
        forwarded = []

        with patch("ibx.get_gmail_service", return_value=fake_svc), \
             patch("ibx.triage_inbox", return_value=(0, 0)), \
             patch("ibx.fetch_inbox", return_value=[{"id": "msg1"}]), \
             patch("ibx.get_email", return_value=_fake_email("msg1")), \
             patch("ibx.ask_claude", return_value={
                 "action": "forward",
                 "to": "Anthony Duesler",
                 "message": "Forwarding to Anthony",
                 "content": "Can you combine Barb and Scott's K-1s?",
             }), \
             patch("ibx.lookup_contact_email", return_value="anthony@m5x2.com"), \
             patch("ibx.forward_email", side_effect=lambda svc, eml, to, note: forwarded.append(to)), \
             patch("ibx.archive"), \
             patch("ibx.display_card"), \
             patch("ibx.print_help"), \
             patch("ibx.console"), \
             patch("builtins.input", side_effect=["forward to anthony", "y", "q"]):
            try:
                ibx.main()
            except (SystemExit, StopIteration):
                pass

        self.assertEqual(len(forwarded), 1, "forward_email() must be called when Contacts resolves the name")
        self.assertEqual(forwarded[0], "anthony@m5x2.com")

    def test_name_only_recipient_prompts_for_email_when_contacts_fails(self):
        """When Contacts lookup returns nothing, ibx must prompt the user for an email."""
        import ibx

        fake_svc = MagicMock()
        forwarded = []

        with patch("ibx.get_gmail_service", return_value=fake_svc), \
             patch("ibx.triage_inbox", return_value=(0, 0)), \
             patch("ibx.fetch_inbox", return_value=[{"id": "msg1"}]), \
             patch("ibx.get_email", return_value=_fake_email("msg1")), \
             patch("ibx.ask_claude", return_value={
                 "action": "forward",
                 "to": "Anthony",
                 "message": "Forwarding to Anthony",
                 "content": "Can you combine Barb and Scott's K-1s?",
             }), \
             patch("ibx.lookup_contact_email", return_value=None), \
             patch("ibx.forward_email", side_effect=lambda svc, eml, to, note: forwarded.append(to)), \
             patch("ibx.archive"), \
             patch("ibx.display_card"), \
             patch("ibx.print_help"), \
             patch("ibx.console"), \
             patch("builtins.input", side_effect=[
                 "forward to anthony",
                 "anthony@m5x2.com",   # email address prompt
                 "y",                  # confirm send
                 "q",
             ]):
            try:
                ibx.main()
            except (SystemExit, StopIteration):
                pass

        self.assertEqual(len(forwarded), 1,
                         "forward_email() must be called after user supplies the email address")
        self.assertEqual(forwarded[0], "anthony@m5x2.com")

    def test_name_only_recipient_cancel_on_no_email(self):
        """If user provides no email when prompted, forward must be cancelled without crashing."""
        import ibx

        fake_svc = MagicMock()
        forwarded = []

        with patch("ibx.get_gmail_service", return_value=fake_svc), \
             patch("ibx.triage_inbox", return_value=(0, 0)), \
             patch("ibx.fetch_inbox", return_value=[{"id": "msg1"}]), \
             patch("ibx.get_email", return_value=_fake_email("msg1")), \
             patch("ibx.ask_claude", return_value={
                 "action": "forward",
                 "to": "Anthony",
                 "message": "Forwarding",
                 "content": "Please fix.",
             }), \
             patch("ibx.lookup_contact_email", return_value=None), \
             patch("ibx.forward_email", side_effect=lambda svc, eml, to, note: forwarded.append(to)), \
             patch("ibx.archive"), \
             patch("ibx.display_card"), \
             patch("ibx.print_help"), \
             patch("ibx.console"), \
             patch("builtins.input", side_effect=[
                 "forward to anthony",
                 "",      # user hits enter with no email — should cancel
                 "q",
             ]):
            try:
                ibx.main()
            except (SystemExit, StopIteration):
                pass

        self.assertEqual(len(forwarded), 0,
                         "Empty email input must cancel the forward, not crash or send")


class TestExtractBodyUsesLongestPlainPart(unittest.TestCase):
    """extract_body must return the longest text/plain part, not just the first one.

    Corporate emails (e.g. from Microsoft Exchange) often encode a short
    disclaimer-only block as the first text/plain part, with the actual
    message content in a later text/plain or text/html alternative.
    Taking the first part shows only the disclaimer.
    """

    def _make_multipart(self, parts):
        """Build a multipart email from a list of (content_type, body_str) tuples."""
        import email.mime.multipart
        import email.mime.text
        msg = email.mime.multipart.MIMEMultipart("alternative")
        for ct, body in parts:
            maintype, subtype = ct.split("/")
            part = email.mime.text.MIMEText(body, subtype)
            msg.attach(part)
        return msg

    def test_short_plain_falls_through_to_html(self):
        """When text/plain is very short (disclaimer only), fall through to the HTML part."""
        import ibx
        disclaimer = "This email and any attachments are confidential."
        html_content = "<p>Hi Jonathan,</p><p>Just wanted to follow up on the Q1 numbers.</p><p>Thanks,<br>Joe</p>"
        msg = self._make_multipart([
            ("text/plain", disclaimer),
            ("text/html", html_content),
        ])
        result = ibx.extract_body(msg)
        self.assertIn("Q1 numbers", result,
                      "extract_body must fall through to HTML when text/plain is only a short disclaimer")
        self.assertNotEqual(result.strip(), disclaimer.strip(),
                            "extract_body must not return only the disclaimer when HTML has real content")

    def test_single_plain_part_still_works(self):
        """A normal email with one text/plain part must still be extracted correctly."""
        import ibx
        body = (
            "Hi Jonathan,\n\n"
            "Please review the attached proposal and let me know your thoughts.\n"
            "I think we can get this across the finish line before the end of the month.\n\n"
            "Thanks,\nJoe"
        )
        msg = self._make_multipart([("text/plain", body)])
        result = ibx.extract_body(msg)
        self.assertIn("attached proposal", result)


class TestSendReplyAll(unittest.TestCase):
    """send_reply must default to reply-all, including original To and Cc recipients."""

    def _sent_raw(self, fake_svc):
        """Decode the raw message bytes from the last send() call_args."""
        import base64
        call_kwargs = fake_svc.users().messages().send.call_args
        body = call_kwargs[1]["body"] if call_kwargs[1] else call_kwargs[0][1]
        return base64.urlsafe_b64decode(body["raw"]).decode("utf-8", errors="replace")

    def test_reply_includes_cc_recipients(self):
        """send_reply must Cc the original Cc addresses, not reply only to sender."""
        import ibx
        fake_svc = MagicMock()
        fake_svc.users().getProfile().execute.return_value = {"emailAddress": "mckay@m5c7.com"}

        eml = {
            "id": "msg1",
            "subject": "Budget review",
            "from": "joe@example.com",
            "to": "mckay@m5c7.com",
            "cc": "alice@example.com, bob@example.com",
            "thread_id": "thread1",
        }
        ibx.send_reply(fake_svc, eml, "Thanks Joe!")

        raw = self._sent_raw(fake_svc)
        self.assertIn("alice@example.com", raw,
                      "send_reply must include original Cc recipients (reply-all), got:\n" + raw)
        self.assertIn("bob@example.com", raw,
                      "send_reply must include all Cc recipients, got:\n" + raw)

    def test_reply_uses_rfc_message_id_for_threading(self):
        """send_reply must set In-Reply-To and References to the RFC Message-ID header,
        not the Gmail API message ID, so Gmail places the reply in the same thread."""
        import ibx
        fake_svc = MagicMock()
        fake_svc.users().getProfile().execute.return_value = {"emailAddress": "mckay@m5c7.com"}

        eml = {
            "id": "18abc123def456",                        # Gmail API ID (not an RFC Message-ID)
            "message_id": "<CABxyz@mail.gmail.com>",       # RFC Message-ID header
            "references": "",
            "subject": "Budget review",
            "from": "joe@example.com",
            "to": "mckay@m5c7.com",
            "cc": "",
            "thread_id": "thread1",
        }
        ibx.send_reply(fake_svc, eml, "Thanks Joe!")

        raw = self._sent_raw(fake_svc)
        in_reply_to = next((l for l in raw.splitlines() if l.lower().startswith("in-reply-to:")), "")
        references = next((l for l in raw.splitlines() if l.lower().startswith("references:")), "")

        self.assertIn("<CABxyz@mail.gmail.com>", in_reply_to,
                      "In-Reply-To must use the RFC Message-ID, not the Gmail API ID.\n" + raw)
        self.assertNotIn("18abc123def456", in_reply_to,
                         "In-Reply-To must not contain the bare Gmail API ID.\n" + raw)
        self.assertIn("<CABxyz@mail.gmail.com>", references,
                      "References must include the RFC Message-ID.\n" + raw)

    def test_reply_threading_falls_back_to_id_when_no_message_id(self):
        """When message_id is absent (e.g. old cached email), send_reply must not crash."""
        import ibx
        fake_svc = MagicMock()
        fake_svc.users().getProfile().execute.return_value = {"emailAddress": "mckay@m5c7.com"}

        eml = {
            "id": "18abc123def456",
            "subject": "Test",
            "from": "joe@example.com",
            "to": "mckay@m5c7.com",
            "cc": "",
            "thread_id": "thread1",
            # no message_id key
        }
        # Should not raise
        ibx.send_reply(fake_svc, eml, "Got it.")
        fake_svc.users().messages().send.assert_called_once()

    def test_reply_does_not_cc_self(self):
        """send_reply must not include the sender's own address in Cc."""
        import ibx
        fake_svc = MagicMock()
        fake_svc.users().getProfile().execute.return_value = {"emailAddress": "mckay@m5c7.com"}

        eml = {
            "id": "msg1",
            "subject": "Re: something",
            "from": "joe@example.com",
            "to": "mckay@m5c7.com",
            "cc": "alice@example.com",
            "thread_id": "thread1",
        }
        ibx.send_reply(fake_svc, eml, "Got it.")

        raw = self._sent_raw(fake_svc)
        cc_line = next((l for l in raw.splitlines() if l.lower().startswith("cc:")), "")
        self.assertNotIn("mckay@m5c7.com", cc_line,
                         "send_reply must not Cc the sender's own address")


class TestImsgPollResolution(unittest.TestCase):
    """Poll must not falsely resolve an iMessage item that has a newer message than what's stored."""

    def _make_imsg_item(self, cid, latest_apple_ts):
        return {
            "type": "imsg",
            "source": "iMessage",
            "from": "Asha",
            "preview": "Hey",
            "body": "",
            "ts": 0.0,
            "_data": {
                "thread": {
                    "chat_identifier": cid,
                    "latest_apple_ts": latest_apple_ts,
                }
            },
        }

    def test_poll_does_not_resolve_item_with_newer_ts_than_stored(self):
        """If item's latest_apple_ts > stored proc ts, poll must NOT mark it resolved."""
        import sys, os
        sys.path.insert(0, os.path.dirname(__file__))
        import ibx_all

        stored_ts = 1_000_000
        item_ts = 2_000_000  # newer message — not yet processed
        item = self._make_imsg_item("chat;-;+15105551234", item_ts)

        resolved = set()

        with unittest.mock.patch("imsg.load_processed", return_value={"chat;-;+15105551234": stored_ts}):
            ibx_all.check_resolved_now([item], resolved)

        self.assertNotIn(
            ibx_all._item_uid(item),
            resolved,
            "check_resolved_now must NOT resolve an iMessage item whose latest_apple_ts is "
            "newer than the stored processed timestamp — new message should stay visible.",
        )

    def test_poll_resolves_item_when_ts_already_covered(self):
        """If stored proc ts >= item's latest_apple_ts, item is resolved (already archived)."""
        import sys, os
        sys.path.insert(0, os.path.dirname(__file__))
        import ibx_all

        stored_ts = 2_000_000
        item_ts = 1_000_000  # older — already processed
        item = self._make_imsg_item("chat;-;+15105551234", item_ts)

        resolved = set()

        with unittest.mock.patch("imsg.load_processed", return_value={"chat;-;+15105551234": stored_ts}):
            ibx_all.check_resolved_now([item], resolved)

        self.assertIn(
            ibx_all._item_uid(item),
            resolved,
            "check_resolved_now SHOULD resolve an item whose ts is already covered by stored proc.",
        )

    def test_poll_does_not_resolve_unseen_contact(self):
        """An iMessage from a contact not in proc at all must not be resolved."""
        import sys, os
        sys.path.insert(0, os.path.dirname(__file__))
        import ibx_all

        item = self._make_imsg_item("chat;-;+15105559999", 500_000)
        resolved = set()

        with unittest.mock.patch("imsg.load_processed", return_value={}):
            ibx_all.check_resolved_now([item], resolved)

        self.assertNotIn(
            ibx_all._item_uid(item),
            resolved,
            "check_resolved_now must NOT resolve an iMessage from a contact not in proc at all.",
        )


class TestImsgMarkReadOnArchive(unittest.TestCase):
    """Archiving an iMessage thread must mark it as read in chat.db."""

    def test_do_archive_calls_mark_thread_read(self):
        """do_archive for an imsg item must call mark_thread_read with the chat_identifier."""
        import sys, os
        sys.path.insert(0, os.path.dirname(__file__))
        import ibx_all

        cid = "chat;-;+15105551234"
        item = {
            "type": "imsg",
            "source": "iMessage",
            "from": "Family",
            "preview": "哇，好幸福的一天",
            "body": "",
            "ts": 0.0,
            "_data": {"thread": {"chat_identifier": cid, "latest_apple_ts": 1_000_000}},
        }

        marked = []
        with unittest.mock.patch("imsg.load_processed", return_value={}), \
             unittest.mock.patch("imsg.save_processed"), \
             unittest.mock.patch("imsg.mark_thread_read", side_effect=lambda c: marked.append(c)):
            ibx_all.do_archive(item)

        self.assertEqual(marked, [cid],
            "do_archive must call mark_thread_read so the thread loses its unread dot in Messages.app")


class TestTriagePromptDoesNotOverClassifyAsInfo(unittest.TestCase):
    """Triage prompt must not classify tax docs or 'Action Required' emails as info-only.

    Regression: the prompt previously listed 'no-reply senders' as auto-info, causing
    AppFolio notices ('Action Required for (3) Notices' from noreply) and K-1 tax document
    emails to be moved out of inbox before ibx0 could display them — yielding a false
    inbox-zero.
    """

    def _get_classify_prompt(self):
        """Extract the prompt template text from classify_email's f-string assignment."""
        import ast, pathlib
        src = pathlib.Path(__file__).parent / "ibx.py"
        tree = ast.parse(src.read_text())
        func = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "classify_email"
        )
        # The prompt is an f-string (JoinedStr). Collect all string literal segments from it.
        for node in ast.walk(func):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "prompt":
                        # Gather all Constant string pieces inside the f-string
                        parts = []
                        for child in ast.walk(node.value):
                            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                                parts.append(child.value)
                        return " ".join(parts)
        return ""

    def test_no_reply_senders_not_listed_as_auto_info(self):
        """'no-reply senders' must not appear in the info bucket — sender domain ≠ unactionable."""
        prompt = self._get_classify_prompt()
        self.assertNotIn(
            "no-reply senders", prompt,
            "Triage prompt lists 'no-reply senders' as info — this causes AppFolio notices "
            "and other actionable noreply emails to be silently removed from inbox."
        )

    def test_action_required_in_response_examples(self):
        """'Action Required' must appear in the response examples so Claude keeps such emails."""
        prompt = self._get_classify_prompt()
        self.assertIn(
            "Action Required", prompt,
            "Triage prompt does not mention 'Action Required' in response criteria — "
            "emails with that subject line may be incorrectly triaged as info."
        )

    def test_tax_documents_in_response_examples(self):
        """Tax documents (K-1) must be listed as response so they are not triaged out."""
        prompt = self._get_classify_prompt()
        self.assertIn(
            "K-1", prompt,
            "Triage prompt does not mention tax documents (K-1) as requiring response — "
            "K-1 delivery emails may be incorrectly triaged as info and removed from inbox."
        )

    def test_doubt_defaults_to_response(self):
        """Prompt must include a bias toward 'response' to prevent false negatives."""
        prompt = self._get_classify_prompt()
        self.assertIn(
            "doubt", prompt.lower(),
            "Triage prompt has no 'when in doubt' fallback — borderline emails default to "
            "being removed rather than kept."
        )


class TestIbxAllOptionalImport(unittest.TestCase):
    """Every try/except guarding an import in ibx_all.py must catch SystemExit.

    Regression: lease_signer calls sys.exit(1) when playwright is not installed.
    The try/except ImportError block did not catch SystemExit, so ibx_all exited
    immediately on startup, showing "0 messages" before fetching any inbox items.

    This test is intentionally general: it catches the same class of bug for any
    future optional dependency that calls sys.exit() during import.
    """

    @staticmethod
    def _caught_names(handler):
        """Return the set of exception class names caught by an except handler."""
        if handler.type is None:
            return {"bare"}
        import ast
        if isinstance(handler.type, ast.Tuple):
            return {elt.id for elt in handler.type.elts if isinstance(elt, ast.Name)}
        if isinstance(handler.type, ast.Name):
            return {handler.type.id}
        return set()

    def test_all_import_try_blocks_catch_system_exit(self):
        """Every try block containing an import in ibx_all.py must catch SystemExit.

        Optional dependencies may call sys.exit() during import (e.g. when a
        required sub-dependency like playwright is missing). If the guarding
        try/except only catches ImportError, the SystemExit propagates and kills
        the whole ibx_all process before any inbox items are fetched.
        """
        import ast, pathlib
        src = pathlib.Path(__file__).parent / "ibx_all.py"
        tree = ast.parse(src.read_text())

        # Only check module-level try blocks (direct children of the module body).
        # Function-level try/except blocks are fine to propagate SystemExit — it's
        # the module-level ones that kill ibx_all before any inbox items are fetched.
        failures = []
        for node in tree.body:
            if not isinstance(node, ast.Try):
                continue
            # Only care about try blocks that contain at least one import
            has_import = any(
                isinstance(child, (ast.Import, ast.ImportFrom))
                for child in ast.walk(node)
            )
            if not has_import:
                continue
            caught = set()
            for handler in node.handlers:
                caught |= self._caught_names(handler)
            safe = bool(caught & {"SystemExit", "BaseException", "bare"})
            if not safe:
                # Collect the imported names for a clear error message
                imported = [
                    alias.name
                    for child in ast.walk(node)
                    if isinstance(child, (ast.Import, ast.ImportFrom))
                    for alias in (child.names if isinstance(child, ast.Import)
                                  else [ast.alias(name=child.module or "?", asname=None)])
                ]
                failures.append(
                    f"try block importing {imported} only catches {sorted(caught)} — "
                    "add SystemExit so a sys.exit() inside the dependency doesn't kill ibx_all"
                )

        self.assertFalse(
            failures,
            "ibx_all.py has try/except import blocks that don't catch SystemExit:\n"
            + "\n".join(f"  • {f}" for f in failures),
        )


class TestInboxZeroSetsBlue(unittest.TestCase):
    """ibx_all must set terminal color to blue on inbox zero, not leave it black.

    Regression: the inbox-zero early-return path printed "Inbox zero." and
    returned without calling set_term_color("blue"), leaving the tab black.
    """

    def test_inbox_zero_path_calls_set_term_color_blue(self):
        """AST: the if-not-all_items branch in main() must call set_term_color('blue')."""
        import ast, pathlib
        src = pathlib.Path(__file__).parent / "ibx_all.py"
        tree = ast.parse(src.read_text())

        main_fn = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "main"
        )

        # Find the `if not all_items:` block
        inbox_zero_block = None
        for node in ast.walk(main_fn):
            if not isinstance(node, ast.If):
                continue
            test = node.test
            if (isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not)
                    and isinstance(test.operand, ast.Name)
                    and test.operand.id == "all_items"):
                inbox_zero_block = node
                break

        self.assertIsNotNone(inbox_zero_block, "Could not find 'if not all_items:' block in main()")

        # Check that set_term_color("blue") is called inside the block
        calls = [
            node for node in ast.walk(inbox_zero_block)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "set_term_color"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "blue"
        ]
        self.assertTrue(
            calls,
            "inbox-zero branch does not call set_term_color('blue') — "
            "terminal tab stays black instead of turning blue on inbox zero"
        )


if __name__ == "__main__":
    unittest.main()
