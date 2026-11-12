"""relogin.py's self-healing pieces (2026-11-12): the SMS code is read from
Ix's Messages over ssh; only codes that arrived after the request count; the
digits survive whether chat.db kept them in `text` or only in attributedBody."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import relogin as rl  # noqa: E402

SMS = "128533 Use this code to verify your AppFolio account. This code will expire in 10 minutes."


def test_extract_code_takes_the_standalone_six_digits():
    assert rl.extract_code(SMS) == "128533"
    assert rl.extract_code("expires in 10 minutes") is None
    assert rl.extract_code("ref 1234567 then 654321 ok") == "654321"   # 7-digit run is not a code


def test_code_from_attributed_body_when_text_is_null():
    blob = b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString+" + SMS.encode() + b"\x86"
    assert rl.code_from_row(None, blob.hex()) == "128533"
    assert rl.code_from_row("", "zz") is None


def test_fetch_sms_code_filters_sender_and_since_and_returns_newest():
    seen = {}
    def query(sql):
        seen["sql"] = sql
        return [["", ""], [SMS, ""]]
    since = time.time()
    assert rl.fetch_sms_code(since, query=query) == "128533"
    sql = seen["sql"]
    assert f"'%{rl.SMS_SENDER}%'" in sql and "is_from_me = 0" in sql and "order by m.date desc" in sql
    since_ns = int(sql.split("m.date > ")[1].split()[0])
    # Apple epoch nanoseconds, with the small clock-skew slack subtracted
    assert abs(since_ns / 1e9 - (since - rl.SMS_SLACK_S - rl._APPLE_EPOCH)) < 1


def test_sms_provider_polls_until_code_arrives_and_survives_ssh_errors(monkeypatch):
    calls = []
    def query(sql):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("ssh hiccup")
        if len(calls) == 2:
            return []
        return [[SMS, ""]]
    monkeypatch.setattr(rl.time, "sleep", lambda s: None)
    get_code = rl.sms_code_provider(timeout_s=30, poll_s=0, query=query)
    assert get_code(time.time()) == "128533"
    assert len(calls) == 3


def test_sms_provider_gives_up_after_timeout(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(rl.time, "time", lambda: clock[0])
    def sleep(s):
        clock[0] += 10
    monkeypatch.setattr(rl.time, "sleep", sleep)
    get_code = rl.sms_code_provider(timeout_s=25, poll_s=10, query=lambda sql: [])
    assert get_code(0.0) is None


def test_relogin_does_not_ask_for_a_code_when_no_2fa_prompt():
    # The no-radios branch must never call get_code: an SMS provider would
    # block ~150s for a text that was never sent (rubber-duck 2026-11-12).
    src = (Path(__file__).parent / "relogin.py").read_text()
    body = src.split("radios = page.locator")[1]
    else_branch = body.split("else:")[1].split("ls._skip_passkey_prompt")[0]
    assert "get_code" not in else_branch
    assert "requested_at = time.time()" in body.split(".first.click()")[0]   # stamped before the request click
