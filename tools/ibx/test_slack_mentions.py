"""Channel-mention cards + forward-only mark_read (2026-11-15).

Mentions in public channels become ibx queue items so the dtd badge and the
queue agree; a reply goes into the mention's thread; archiving never moves a
channel's read marker backward.
"""
import os
import sys

os.environ.setdefault("ANTHROPIC_API_KEY", "test")
sys.path.insert(0, os.path.dirname(__file__))
import slack  # noqa: E402

ME = "UME"


def _fake_get(history, last_read="100.0"):
    def get(token, method, **p):
        if method == "conversations.history":
            return {"messages": history}
        if method == "conversations.info":
            return {"channel": {"last_read": last_read}}
        if method == "users.info":
            return {"user": {"real_name": "Ian", "name": "ian"}}
        raise AssertionError(method)
    return get


def test_is_mention():
    assert slack.is_mention({"type": "message", "user": "U2", "text": "hi <@UME>"}, ME)
    assert slack.is_mention({"type": "message", "user": "U2", "text": "<!here> lunch"}, ME)
    assert not slack.is_mention({"type": "message", "user": "U2", "text": "hi all"}, ME)
    assert not slack.is_mention({"type": "message", "user": ME, "text": "<@UME>"}, ME)


def test_mention_thread_only_unread_mentions(monkeypatch):
    hist = [  # newest first, as the API returns
        {"type": "message", "user": "U2", "text": "<@UME> second", "ts": "300.0", "thread_ts": "250.0"},
        {"type": "message", "user": "U2", "text": "chatter", "ts": "200.0"},
        {"type": "message", "user": "U2", "text": "<@UME> already read", "ts": "90.0"},
    ]
    monkeypatch.setattr(slack, "slack_get", _fake_get(hist))
    th = slack.build_mention_thread("t", {"id": "C1", "name": "ops"}, ME, 0)
    assert th["display_name"] == "#ops" and th["is_mention"] and not th["is_im"]
    assert [m["ts"] for m in th["messages"]] == ["300.0"]
    assert th["latest_ts"] == "300.0"
    assert th["reply_thread_ts"] == "250.0"   # reply inside the existing thread


def test_mention_thread_none_when_all_read_or_no_mention(monkeypatch):
    hist = [{"type": "message", "user": "U2", "text": "<@UME> old", "ts": "90.0"}]
    monkeypatch.setattr(slack, "slack_get", _fake_get(hist))
    assert slack.build_mention_thread("t", {"id": "C1", "name": "ops"}, ME, 0) is None
    monkeypatch.setattr(slack, "slack_get", _fake_get(
        [{"type": "message", "user": "U2", "text": "no mention", "ts": "300.0"}]))
    assert slack.build_mention_thread("t", {"id": "C1", "name": "ops"}, ME, 0) is None


def test_member_channels_missing_scope_is_empty_not_error(monkeypatch):
    def get(token, method, **p):
        raise RuntimeError("users.conversations: missing_scope")
    monkeypatch.setattr(slack, "slack_get", get)
    slack._member_cache.clear()
    assert slack.fetch_member_channels("tok-noscope") == []
    assert slack.fetch_mention_threads("tok-noscope", ME, 0) == []


def test_mark_read_never_moves_backward(monkeypatch):
    posts = []
    monkeypatch.setattr(slack, "slack_post", lambda t, m, **p: posts.append(p))
    monkeypatch.setattr(slack, "slack_get", _fake_get([], last_read="500.0"))
    slack.mark_read("t", "C1", "300.0")
    assert posts == []
    slack.mark_read("t", "C1", "600.0")
    assert posts == [{"channel": "C1", "ts": "600.0"}]


def test_send_reply_threads_only_when_asked(monkeypatch):
    posts = []
    monkeypatch.setattr(slack, "slack_post", lambda t, m, **p: posts.append(p))
    slack.send_reply("t", "D1", "hi")
    slack.send_reply("t", "C1", "hi", thread_ts="250.0")
    assert posts == [{"channel": "D1", "text": "hi"},
                     {"channel": "C1", "text": "hi", "thread_ts": "250.0"}]
