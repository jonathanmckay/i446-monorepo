"""JM 2026-11-22: push records itself when GitHub already has today's push
before noon; otherwise the card stays in dtd. The decisions are pure and
tested here; the job itself is scripts/push_auto.py under com.jm.push-auto."""
import datetime as dt
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).parent
TZ = dt.timezone(dt.timedelta(hours=-8))
DAY = dt.datetime(2026, 11, 23, 0, 0, tzinfo=TZ)
NOON = DAY.replace(hour=12)


def _load():
    spec = importlib.util.spec_from_file_location("push_auto_t", HERE / "push_auto.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["push_auto_t"] = mod
    spec.loader.exec_module(mod)
    return mod


def _data(*nodes, login="jonathanmckay"):
    return {"viewer": {"login": login},
            "r0": {"ref": {"target": {"history": {"nodes": list(nodes)}}}},
            "r1": {"ref": None}}


def _node(utc_iso, who="jonathanmckay", head="auto: 2026-11-23 08:10"):
    return {"committedDate": utc_iso, "messageHeadline": head, "author": {"user": {"login": who}}}


def test_a_commit_of_mine_this_morning_counts():
    m = _load()
    hits = m.pushed_today(_data(_node("2026-11-23T16:10:00Z")), DAY, NOON)     # 08:10 PST
    assert [h[0] for h in hits] == ["r0"]


def test_yesterday_afternoon_and_other_authors_do_not_count():
    m = _load()
    data = _data(_node("2026-11-23T07:59:00Z"),                # 23:59 PST the day before
                 _node("2026-11-23T20:30:00Z"),                # 12:30 PST: after noon
                 _node("2026-11-23T16:00:00Z", who="dependabot"),
                 {"committedDate": "2026-11-23T16:00:00Z", "author": {"user": None}})
    assert m.pushed_today(data, DAY, NOON) == []


def test_snapshots_can_be_excluded():
    m = _load()
    data = _data(_node("2026-11-23T16:10:00Z"), _node("2026-11-23T17:00:00Z", head="vault backup: 2026-11-23"))
    assert m.pushed_today(data, DAY, NOON, count_snapshots=False) == []
    data = _data(_node("2026-11-23T16:10:00Z", head="bookreview: publishing marks the book finished"))
    assert len(m.pushed_today(data, DAY, NOON, count_snapshots=False)) == 1


def test_no_viewer_or_missing_branch_is_no_push():
    m = _load()
    assert m.pushed_today({"r0": None}, DAY, NOON) == []
    assert m.pushed_today(_data(), DAY, NOON) == []


def test_query_names_each_pushed_branch_from_local_midnight():
    m = _load()
    q = m.query([("jonathanmckay/i446-monorepo", "wip"), ("jonathanmckay/vault", "main")], DAY)
    assert 'refs/heads/wip' in q and 'refs/heads/main' in q
    assert 'since:"2026-11-23T08:00:00Z"' in q and "viewer{login}" in q


def test_card_due_today_only_matches_the_open_habit_card():
    m = _load()
    today = dt.date(2026, 11, 23)
    card = {"content": "push (10) [20]", "labels": ["0neon", "g245", "push"], "due": {"date": "2026-11-23"}}
    assert m.card_due_today([card], today) is card
    assert m.card_due_today([dict(card, due={"date": "2026-11-22"})], today) is not None   # overdue still open
    assert m.card_due_today([dict(card, due={"date": "2026-11-24"})], today) is None       # done today already
    assert m.card_due_today([dict(card, content="push more", labels=["g245"])], today) is None
    assert m.card_due_today([dict(card, content="pushups (5)")], today) is None


def test_after_noon_the_job_does_nothing(capsys):
    m = _load()
    with patch.object(m, "_todoist_tasks", side_effect=AssertionError("must not be called")):
        assert m.main([], now=DAY.replace(hour=12, minute=5)) == 0
    assert "past noon" in capsys.readouterr().out


def test_records_through_did_fast_once_and_marks_the_day(tmp_path):
    m = _load()
    card = {"content": "push (10) [20]", "labels": ["0neon"], "due": {"date": "2026-11-23"}}
    calls, cards = [], [[card], []]                     # due before did-fast, gone after

    class VH:
        GH_REPOS = [("o/r", "wip")]
        _gh_graphql = staticmethod(lambda q: _data(_node("2026-11-23T16:10:00Z")))

    def run(cmd, **k):
        calls.append(cmd)
        return type("R", (), {"returncode": 0, "stdout": "{}", "stderr": ""})()

    with patch.object(m, "MARK_DIR", tmp_path), patch.object(m, "_vault_health", lambda: VH), \
            patch.object(m, "_todoist_tasks", side_effect=lambda: cards.pop(0)), \
            patch.object(m.subprocess, "run", run):
        assert m.main([], now=DAY.replace(hour=9)) == 0
        assert len(calls) == 1 and calls[0][-1] == "push" and calls[0][-2].endswith("did-fast.py")
        assert (tmp_path / "push-auto-2026-11-23").exists()
        assert m.main([], now=DAY.replace(hour=10)) == 0      # marked: no second call
    assert len(calls) == 1


def test_no_push_or_a_failed_write_leaves_the_day_open(tmp_path):
    m = _load()
    card = {"content": "push (10) [20]", "labels": ["0neon"], "due": {"date": "2026-11-23"}}

    class Quiet:
        GH_REPOS = [("o/r", "wip")]
        _gh_graphql = staticmethod(lambda q: _data())

    class Down(Quiet):
        _gh_graphql = staticmethod(lambda q: {"_error": "timeout"})

    class Pushed(Quiet):
        _gh_graphql = staticmethod(lambda q: _data(_node("2026-11-23T16:10:00Z")))

    ran = []
    fail = lambda cmd, **k: (ran.append(cmd), type("R", (), {"returncode": 1, "stdout": "", "stderr": ""})())[1]  # noqa: E731
    with patch.object(m, "MARK_DIR", tmp_path), patch.object(m, "_todoist_tasks", lambda: [card]), \
            patch.object(m.subprocess, "run", fail):
        with patch.object(m, "_vault_health", lambda: Quiet):
            assert m.main([], now=DAY.replace(hour=9)) == 0 and ran == []
        with patch.object(m, "_vault_health", lambda: Down):
            assert m.main([], now=DAY.replace(hour=9)) == 1 and ran == []
        with patch.object(m, "_vault_health", lambda: Pushed):
            assert m.main([], now=DAY.replace(hour=9)) == 1 and len(ran) == 1
    assert not list(tmp_path.iterdir())
