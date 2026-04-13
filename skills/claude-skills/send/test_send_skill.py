"""Regression tests for the send skill (SKILL.md)."""
from pathlib import Path

SKILL_MD = Path(__file__).parent / "SKILL.md"


def test_teams_dispatch_supports_group_chat():
    """
    Bug: listing two names in /send (e.g., "/send Luke and Asha via teams: ...")
    created two separate 1:1 chats instead of one group chat.

    Fix: Teams dispatch must use chatType 'group' when multiple recipients are
    specified, creating a single thread with all participants.
    """
    text = SKILL_MD.read_text()
    assert "'group'" in text, (
        "SKILL.md Teams section must include chatType 'group' for multi-recipient sends"
    )
    assert "len(emails) == 1" in text or "len(emails)==1" in text, (
        "SKILL.md Teams section must branch on single vs multiple recipients"
    )


def test_syntax_documents_multiple_recipients():
    """
    Bug: syntax section only showed single-person examples, so the LLM
    would loop over recipients and send individually.

    Fix: syntax must explicitly state multiple recipients go in one message.
    """
    text = SKILL_MD.read_text()
    assert "one or more recipients" in text.lower() or "person(s)" in text, (
        "Syntax section must document multiple recipient support"
    )
    assert "NOT separate messages" in text or "not separate" in text.lower(), (
        "Syntax section must explicitly say NOT to send separate messages"
    )


def test_outlook_dispatch_uses_email_list():
    """
    Outlook 'to' field must accept a list of emails, not just a single-element array.
    """
    text = SKILL_MD.read_text()
    # The 'to' field in Outlook dispatch must show emails (plural variable)
    assert "'to': emails" in text, (
        "Outlook dispatch must use 'to': emails (a list), not 'to': ['<email>']"
    )


def test_gmail_dispatch_joins_recipients():
    """
    Gmail To: header must join multiple emails with comma for multi-recipient sends.
    """
    text = SKILL_MD.read_text()
    assert "join(emails)" in text, (
        "Gmail dispatch must use ', '.join(emails) for the To: header"
    )
