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


def test_resolve_d359_uses_scored_matching():
    """
    Bug: resolve_d359 used simple substring matching and returned the first
    glob hit. When multiple people share a first name (e.g., two Andys),
    the wrong person could be selected.

    Fix: resolve_d359 must score matches (exact > full-name > partial) and
    return all candidates so ambiguity can be detected.
    """
    text = SKILL_MD.read_text()
    assert "score" in text.lower() and "candidates" in text.lower(), (
        "resolve_d359 must use scored matching with candidates list"
    )
    assert "score = 3" in text and "score = 2" in text and "score = 1" in text, (
        "resolve_d359 must assign tiered scores: 3 (exact), 2 (full name), 1 (partial)"
    )


def test_recipient_confirmation_on_ambiguous_match():
    """
    Bug: /send Andy Shaman via teams sent to the wrong Andy because the skill
    never asked for confirmation when the match was ambiguous.

    Fix: SKILL.md must require confirmation when multiple d359 matches exist,
    when match confidence is low, or when falling back to Graph (first-time contact).
    """
    text = SKILL_MD.read_text()
    assert "Recipient confirmation" in text or "recipient confirmation" in text, (
        "SKILL.md must have a recipient confirmation section"
    )
    assert "Multiple d359 matches" in text, (
        "Must require confirmation when multiple people match the input name"
    )
    assert "Low-confidence match" in text, (
        "Must require confirmation when match score is low (partial only)"
    )
    assert "No d359 match" in text, (
        "Must require confirmation when falling back to Graph/search for unknown contacts"
    )
