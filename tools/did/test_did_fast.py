"""Regression tests: -1neon carry-forward must not resurrect retired cards.

Bug 2026-07-30 ("dupe -1n tasks in dtd, should only be one -1g -1ibx -1l at
any time"): _refresh_task_queue_inner()'s -1neon union step carries the OLD
cache's -1neon cards forward when the live `/tasks?label=-1neon` fetch looks
flaky (empty or a smaller-than-expected subset). Two distinct defects fed the
same duplicate-card symptom:

1. The empty-fetch path had no block-boundary gate at all: a fully empty
   fetch right after a boundary carried the previous block's already
   deleted/closed ritual cards straight back into `today`, alongside the new
   block's freshly created ones. Fixed by gating it on `same_block`, like the
   partial-fetch path already was.

2. Even WITHIN the same block, both paths trusted "old card missing from the
   fresh fetch" as proof of a flake and blindly carried it forward -- but it
   is equally, and in the observed live case actually, proof the daemon
   legitimately deleted/closed that card at the boundary. Screenshot showed
   bare current-block -1g/-1ibx/-1t alongside stale, auto-triage-mangled
   previous-block -1g/-1ibx/-1l that Todoist itself had already deleted; a
   live refresh kept re-carrying them in every ~3min cycle for the rest of
   the block. Fixed by resolving the ambiguity with a per-card re-GET instead
   of guessing from the aggregate count.

Run: python3 -m pytest tools/did/test_did_fast.py -v
"""
import importlib.util
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

DID_FAST = Path(__file__).parent / "did-fast.py"


def _load_did_fast():
    spec = importlib.util.spec_from_file_location("did_fast_under_test", DID_FAST)
    mod = importlib.util.module_from_spec(spec)
    # dataclasses' field-type resolution looks itself up via
    # sys.modules[cls.__module__] -- must be registered before exec_module
    # runs the module's @dataclass definitions.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = json.dumps(payload).encode()
        self.status = status

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _make_fake_urlopen(single_task_checked, single_task_labels=("-1neon",)):
    """Labels and -1neon list-fetches return empty (the flaky state that
    exposed the bug); the today-filter fetch succeeds with one unrelated task
    so the SEPARATE "keep old today on total fetch failure" fallback isn't
    what's under test -- only the -1neon union's own carry-forward logic. A
    per-task GET (the new verification step) reports `single_task_checked`/
    `single_task_labels` for "stale-1", or 404s if `single_task_checked` is
    None (deleted)."""
    def _fake_urlopen(req, timeout=None):
        url = req.full_url
        if "/tasks/filter" in url:
            return _FakeResponse({"results": [
                {"id": "unrelated-1", "content": "some other task", "labels": [],
                 "priority": 1, "due": {"date": datetime.now().strftime("%Y-%m-%d")}},
            ], "next_cursor": None})
        if url.endswith("/tasks/stale-1"):
            if single_task_checked is None:
                raise Exception("404 Not Found")
            return _FakeResponse({"id": "stale-1", "checked": single_task_checked,
                                  "labels": list(single_task_labels)})
        return _FakeResponse([])
    return _fake_urlopen


def _stale_ritual_task():
    return {"id": "stale-1", "content": "😈 -1g (15) [15]", "labels": ["-1neon"],
            "priority": "p4", "due": datetime.now().strftime("%Y-%m-%d"),
            "due_string": "today", "recurring": False}


def _seed_old_cache(mod, tmp_path, updated_at: datetime, stale_task):
    cache_path = tmp_path / "task-queue.json"
    cache_path.write_text(json.dumps({
        "updated": updated_at.isoformat(),
        "today": [stale_task],
    }))
    mod.TASK_QUEUE_PATH = cache_path


def test_stale_ritual_card_not_carried_across_block_boundary(monkeypatch, tmp_path):
    mod = _load_did_fast()
    # Old cache belongs to a block that ended >2h ago -- a clearly different
    # 2h bucket regardless of current wall-clock position within its own bucket.
    _seed_old_cache(mod, tmp_path, datetime.now() - timedelta(hours=3), _stale_ritual_task())
    monkeypatch.setattr(mod.urllib.request, "urlopen", _make_fake_urlopen(single_task_checked=False))

    result = mod._refresh_task_queue_inner()

    stale_ids = [t["id"] for t in result["today"] if t.get("id") == "stale-1"]
    assert stale_ids == [], "previous block's -1neon card must not be resurrected by an empty fetch"


def test_ritual_card_still_carried_within_same_block_when_verified_open(monkeypatch, tmp_path):
    """Sanity check: the fix must not break the original 2026-07-26 flake
    tolerance -- within the SAME block, a card confirmed still open on
    re-GET must still be carried forward so it doesn't vanish from dtd."""
    mod = _load_did_fast()
    _seed_old_cache(mod, tmp_path, datetime.now(), _stale_ritual_task())
    monkeypatch.setattr(mod.urllib.request, "urlopen", _make_fake_urlopen(single_task_checked=False))

    result = mod._refresh_task_queue_inner()

    stale_ids = [t["id"] for t in result["today"] if t.get("id") == "stale-1"]
    assert stale_ids == ["stale-1"], "same-block flake tolerance regressed"


def test_ritual_card_not_carried_within_same_block_when_verified_deleted(monkeypatch, tmp_path):
    """The card is genuinely gone (deleted by the daemon at the boundary) --
    must not be resurrected just because the aggregate -1neon fetch happened
    to look partial/empty."""
    mod = _load_did_fast()
    _seed_old_cache(mod, tmp_path, datetime.now(), _stale_ritual_task())
    monkeypatch.setattr(mod.urllib.request, "urlopen", _make_fake_urlopen(single_task_checked=None))

    result = mod._refresh_task_queue_inner()

    stale_ids = [t["id"] for t in result["today"] if t.get("id") == "stale-1"]
    assert stale_ids == [], "a verified-deleted card must not be carried forward, even within the same block"


def test_ritual_card_not_carried_when_open_but_label_stripped(monkeypatch, tmp_path):
    """The exact live failure observed 2026-07-30: the task is still open
    (checked=False) but something stripped its -1neon label, so it's no
    longer a valid ritual card -- must not be resurrected just because it's
    technically still an open task."""
    mod = _load_did_fast()
    _seed_old_cache(mod, tmp_path, datetime.now(), _stale_ritual_task())
    monkeypatch.setattr(mod.urllib.request, "urlopen",
                        _make_fake_urlopen(single_task_checked=False, single_task_labels=()))

    result = mod._refresh_task_queue_inner()

    stale_ids = [t["id"] for t in result["today"] if t.get("id") == "stale-1"]
    assert stale_ids == [], "an open-but-unlabeled card must not be carried forward as a ritual card"


def test_route_items_no_double_credit_when_task_name_has_commas(monkeypatch):
    """Bug 2026-08-06: parse_input splits raw /did input on every comma with
    no awareness that a single task's own content can legitimately contain
    one. "/did quarterly checkin with theo, ren, ashan feedback and Ashan
    feedback" (a real [60] task) split into 3 fragments -- "...theo", "ren",
    "ashan feedback..." -- each of which independently word-overlap-matched
    the SAME task via the live-search fallback and each credited its own
    +60, tripling a single completion's payout to +180 in 0分!X (xk87).
    route_items must credit a task matched more than once in one batch
    exactly once."""
    mod = _load_did_fast()

    task = {
        "id": "quarterly-1",
        "content": "quarterly checkin with theo, ren, ashan feedback and Ashan feedback (60) [60]",
        "labels": ["xk87"], "priority": "p3",
        "due": {"date": "2026-07-31"}, "recurring": True,
    }

    def fake_live_search(query):
        # All 3 comma-split fragments legitimately best-match this one task.
        return task

    monkeypatch.setattr(mod, "_live_todoist_search", fake_live_search)

    raw = "quarterly checkin with theo, ren, ashan feedback and Ashan feedback"
    items = mod.parse_input(raw)
    assert len(items) == 3, "expected the naive comma-split to produce 3 fragments"

    results = mod.route_items(items, headers={"0n": {}, "1n": {}}, tq={})

    credited = [r for r in results if r.fen_points]
    assert len(credited) == 1, f"expected exactly 1 credited result, got {len(credited)}: {results}"
    assert credited[0].fen_points == 60
    assert credited[0].todoist_task["id"] == "quarterly-1"

    dup_skips = [r for r in results if r.step == "skipped" and "already matched" in (r.error or "")]
    assert len(dup_skips) == 2, f"expected the other 2 fragments flagged as duplicate-skips, got {len(dup_skips)}"
