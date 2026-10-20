"""Regression: Todoist v1 /tasks ignores the `filter=` query param.

Bug (2026-06-12): did-fast/defer-fast/dtd queried `/tasks?filter=...`. The
unified v1 API silently ignores `filter` on /tasks and returns ALL tasks, so
every "filtered" fetch was a full-mailbox scan. fetch_today survived only via
its own `due <= today` post-filter; defer-fast's progressive widening
("today | overdue" → "7 days" → "all") was three identical full scans; dtd's
split search could match future tasks.

Fix: query filters go to `/tasks/filter?query=...` (same paginated
{"results", "next_cursor"} shape). Plain `/tasks` is only for unfiltered
listing; `/tasks?label=...` label filtering still works and is unaffected.

Symptom that surfaced it: dtd showed 20 tasks and the diagnosis revealed
'today | overdue', 'today', 'overdue', and 'all' all returned the identical
293 tasks.
"""
from pathlib import Path

HERE = Path(__file__).parent
SOURCES = [HERE / "did-fast.py", HERE / "defer-fast.py", HERE / "dtd.sh"]


def test_no_filter_param_on_tasks_endpoint():
    """`/tasks?filter=` is silently ignored by the v1 API — must not be used."""
    for src in SOURCES:
        text = src.read_text()
        assert "tasks?filter=" not in text, (
            f"{src.name} queries /tasks?filter=..., which the v1 API ignores "
            "(returns ALL tasks). Use /tasks/filter?query=... instead."
        )


# dtd.sh stopped running query filters on 2026-06-26 (3d421361): the split
# dialog fetches the exact task by the id dtd passes instead of re-searching
# "today | overdue". It stays in SOURCES for the negative check above.
QUERY_SOURCES = [s for s in SOURCES if s.name != "dtd.sh"]


def test_filter_queries_use_filter_endpoint():
    """Each source that filters by query must use /tasks/filter?query=."""
    for src in QUERY_SOURCES:
        text = src.read_text()
        # Every file in QUERY_SOURCES does query-filtered fetches somewhere.
        assert "tasks/filter?query=" in text, (
            f"{src.name} should run query filters through /tasks/filter?query=..."
        )
