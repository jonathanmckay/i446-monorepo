"""Tests for edit-fast.py — dtd ctrl-g unified edit (name / domain / points)."""
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load():
    spec = importlib.util.spec_from_file_location("edit_fast", HERE / "edit-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["edit_fast"] = mod
    spec.loader.exec_module(mod)
    return mod


# ── parse_edits: @code → domain, standalone int → points, rest → name ──

def test_parse_all_three():
    mod = _load()
    assert mod.parse_edits("fix bug 30 @i9") == ("fix bug", "i9", 30)


def test_parse_domain_only():
    mod = _load()
    assert mod.parse_edits("@m5x2") == (None, "m5x2", None)


def test_parse_points_only():
    mod = _load()
    assert mod.parse_edits("30") == (None, None, 30)


def test_parse_name_only():
    mod = _load()
    assert mod.parse_edits("rename this task") == ("rename this task", None, None)


def test_parse_standalone_number_in_name_becomes_points():
    # The accepted tradeoff: any standalone number is points, even mid-name.
    mod = _load()
    assert mod.parse_edits("review 2026 budget") == ("review budget", None, 2026)


def test_parse_last_number_wins_earlier_demoted_to_name():
    mod = _load()
    assert mod.parse_edits("foo 30 40") == ("foo 30", None, 40)


def test_parse_empty():
    mod = _load()
    assert mod.parse_edits("   ") == (None, None, None)


# ── bracketed points + retyped annotations must not duplicate ──
# Bug 2026-07-24: ctrl-g "Matt Booty Instagram photos [20]" on a task
# "…photos (15) [15]" produced "…photos [20] (15) [15]" — the display-syntax
# [20] was treated as name text and the old tail appended after it.

def test_parse_bracketed_points_token():
    mod = _load()
    assert mod.parse_edits("[20]") == (None, None, 20)


def test_parse_name_with_bracketed_points():
    mod = _load()
    assert mod.parse_edits("Matt Booty Instagram photos [20]") == \
        ("Matt Booty Instagram photos", None, 20)


def test_full_line_retype_replaces_points_without_duplicating():
    mod = _load()
    orig = "see Matt booty Instagram photos (15) [15]"
    name, _dom, pts = mod.parse_edits("Matt Booty Instagram photos [20]")
    out = mod.set_name(orig, name)
    out = mod._pf.set_points(out, pts)
    assert out == "Matt Booty Instagram photos (15) [20]"


def test_set_name_typed_annotation_overrides_same_kind_tail():
    # Retyping the (N) inline replaces the preserved (N); other kinds survive.
    mod = _load()
    assert mod.set_name("call dad (5) [10]", "ring dad (30)") == "ring dad (30) [10]"


def test_set_name_nonnumeric_tail_annotations_survive():
    mod = _load()
    assert mod.set_name("ship it (5) [0G]", "ship it now") == "ship it now (5) [0G]"


# ── set_name preserves trailing annotations ──

def test_set_name_keeps_annotations():
    mod = _load()
    assert mod.set_name("call dad (5) [10]", "ring dad") == "ring dad (5) [10]"


def test_set_name_no_annotations():
    mod = _load()
    assert mod.set_name("call dad", "ring dad") == "ring dad"


# ── main: integration with mocked Todoist API + temp cache ──

def _cache(tmp_path, content="review budget [10]", labels=("i9", "0neon"), short=None):
    task = {"id": "42", "content": content, "labels": list(labels)}
    if short is not None:
        task["short"] = short
    cache = {"0neon": [task]}
    cf = tmp_path / "cache.json"
    cf.write_text(json.dumps(cache))
    return cf


def test_main_rename_clears_stale_short(tmp_path, monkeypatch):
    mod = _load()
    cf = _cache(tmp_path, content="old long name [10]", labels=("i9",), short="old shrt")
    mod._df._api = lambda *a, **k: None
    monkeypatch.setattr(sys, "argv", ["edit-fast.py", "old long name", "brand new name", str(cf)])
    assert mod.main() == 0
    task = json.loads(cf.read_text())["0neon"][0]
    assert task["content"] == "brand new name [10]"
    assert "short" not in task, "stale Haiku short name must be cleared on rename"


def test_main_rename_to_long_name_regenerates_short(tmp_path, monkeypatch):
    # Bug 2026-07-28: renaming a task to something too long for dtd (e.g. "JY
    # and Florencia...") dropped the stale short but never generated a NEW
    # one — the row just fell back to fzf's raw middle-truncation until
    # whenever the next full --refresh-cache happened to run, unlike every
    # other long task, which gets shortened by that same refresh pass.
    mod = _load()
    cf = _cache(tmp_path, content="call dad [10]", labels=("i9",))
    mod._df._api = lambda *a, **k: None
    long_name = "JY and Florencia sync on the Q3 roadmap and staffing plan"
    calls = []

    def fake_shorten_tasks(tasks):
        calls.append(tasks)
        return {t["id"]: "JY/Florencia Q3 sync" for t in tasks}

    monkeypatch.setattr(mod._short, "shorten_tasks", fake_shorten_tasks)
    monkeypatch.setattr(sys, "argv", ["edit-fast.py", "call dad", long_name, str(cf)])
    assert mod.main() == 0

    # shorten_tasks was called with the task's NEW (post-rename) content.
    assert calls and calls[0][0]["id"] == "42"
    assert long_name in calls[0][0]["content"]

    task = json.loads(cf.read_text())["0neon"][0]
    assert task.get("short") == "JY/Florencia Q3 sync", (
        "a rename that crosses the length cap must get a fresh short immediately")


def test_main_rename_to_short_name_still_clears_old_short(tmp_path, monkeypatch):
    # Companion case: renaming to something short must still drop the stale
    # short (shorten_tasks correctly returns nothing for short-enough prose).
    mod = _load()
    cf = _cache(tmp_path, content="old long name [10]", labels=("i9",), short="old shrt")
    mod._df._api = lambda *a, **k: None
    monkeypatch.setattr(mod._short, "shorten_tasks", lambda tasks: {})
    monkeypatch.setattr(sys, "argv", ["edit-fast.py", "old long name", "brand new name", str(cf)])
    assert mod.main() == 0
    task = json.loads(cf.read_text())["0neon"][0]
    assert "short" not in task


def test_main_applies_name_domain_points(tmp_path, monkeypatch):
    mod = _load()
    cf = _cache(tmp_path, content="old name (5) [10]", labels=("i9", "0neon"))
    calls = []
    mod._df._api = lambda method, path, body=None, **k: calls.append((path, body))

    monkeypatch.setattr(sys, "argv", ["edit-fast.py", "old name", "new name 30 @m5x2", str(cf)])
    assert mod.main() == 0

    bodies = {tuple(b.keys())[0]: b for _, b in calls}
    # content call: name replaced, time annotation kept, points updated
    assert bodies["content"]["content"] == "new name (5) [30]"
    # labels call: domain swapped, bookkeeping preserved
    assert set(bodies["labels"]["labels"]) == {"0neon", "m5x2"}

    patched = json.loads(cf.read_text())["0neon"][0]
    assert patched["content"] == "new name (5) [30]"
    assert patched["labels"] == ["0neon", "m5x2"]


def test_main_points_only_keeps_name(tmp_path, monkeypatch):
    mod = _load()
    cf = _cache(tmp_path, content="call dad (5) [10]", labels=("i9",))
    calls = []
    mod._df._api = lambda method, path, body=None, **k: calls.append((path, body))
    monkeypatch.setattr(sys, "argv", ["edit-fast.py", "call dad", "25", str(cf)])
    assert mod.main() == 0
    assert len(calls) == 1 and calls[0][0] == "/tasks/42"
    assert calls[0][1]["content"] == "call dad (5) [25]"


def test_main_rejects_unknown_domain(tmp_path, monkeypatch):
    mod = _load()
    cf = _cache(tmp_path)
    called = []
    mod._df._api = lambda *a, **k: called.append(a)
    monkeypatch.setattr(sys, "argv", ["edit-fast.py", "review budget", "@nope", str(cf)])
    assert mod.main() == 1
    assert not called, "invalid domain must not touch Todoist"


def test_main_empty_edit_is_noop(tmp_path, monkeypatch):
    mod = _load()
    cf = _cache(tmp_path)
    called = []
    mod._df._api = lambda *a, **k: called.append(a)
    monkeypatch.setattr(sys, "argv", ["edit-fast.py", "review budget", "   ", str(cf)])
    assert mod.main() == 1
    assert not called


def test_points_only_edit_resyncs_stale_short(tmp_path, monkeypatch):
    """2026-07-31 ("I changed it to 10 points but it didn't reflect in
    render"): _resync_short only ran on RENAMES, so a points-only ctrl-g
    edit updated the cache content but left the stale Haiku `short` — which
    dtd renders preferentially — showing the OLD [N] until the next full
    refresh. Any content change must resync the short."""
    mod = _load()
    cf = _cache(tmp_path,
                content='Change LinkedIn title to be "Player of Games" (30) [30]',
                labels=("i9",),
                short="Change LinkedIn title to Player of (30) [30]")
    mod._df._api = lambda *a, **k: None
    monkeypatch.setattr(mod._short, "shorten_tasks",
                        lambda tasks: {t["id"]: "Change LinkedIn title to Player of (30) [10]"
                                       for t in tasks})
    monkeypatch.setattr(sys, "argv", ["edit-fast.py", "Change LinkedIn title", "10", str(cf)])
    assert mod.main() == 0
    task = json.loads(cf.read_text())["0neon"][0]
    assert task["content"].endswith("[10]")
    assert task.get("short") == "Change LinkedIn title to Player of (30) [10]", (
        "a points-only edit must regenerate the short — dtd renders it "
        "preferentially, so a stale one keeps showing the old [N]")


# ── ctrl-o/ctrl-g form: labeled fields (2026-10-11) ──

def _form_task():
    return {"id": "t1", "content": "write memo (see doc) (30) [20] {5}", "labels": ["i9", "#-1g"]}


def test_form_prefill_splits_title_domain_points_description():
    mod = _load()
    cur = mod.current_fields(_form_task(), "notes here")
    assert cur == {"name": "write memo (see doc)", "domain": "i9", "points": "20", "time": "30",
                   "short": "", "desc": "notes here"}


def test_form_untouched_fields_change_nothing():
    mod = _load()
    cur = mod.current_fields(_form_task(), "notes")
    assert mod.changed_fields(cur, dict(cur)) == {}


def test_form_blank_domain_or_points_means_keep_but_blank_description_clears():
    mod = _load()
    cur = mod.current_fields(_form_task(), "notes")
    new = dict(cur, domain="", points=" ", desc="")
    assert mod.changed_fields(cur, new) == {"desc": ""}


def test_form_submit_writes_description_with_content_in_one_call(tmp_path, monkeypatch):
    mod = _load()
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({"tasks": [_form_task()]}))
    calls = []
    monkeypatch.setattr(mod._df, "_api", lambda m, path, body: calls.append(body))
    monkeypatch.setattr(mod, "_description", lambda tid: "old notes")
    monkeypatch.setattr(mod, "_resync_short", lambda *a: None)
    monkeypatch.setattr(sys, "argv", ["edit-fast.py", "--fields", "--id", "t1", str(cache),
                                      "--name", "write memo (see doc)", "--domain", "i9",
                                      "--points", "40", "--desc", "new notes"])
    assert mod.main() == 0
    assert calls == [{"content": "write memo (see doc) (30) [40] {5}", "description": "new notes"}]


# ── one-line form (2026-10-13): title @domain [N] // description ──

def test_line_prefill_round_trips_to_no_change():
    mod = _load()
    cur = mod.current_fields(_form_task(), "notes here")
    line = mod.format_line(cur)
    assert line == "write memo (see doc) @i9 [20] // notes here"
    assert mod.changed_fields(cur, mod.parse_line(line)) == {}


def test_line_edits_description_only():
    mod = _load()
    cur = mod.current_fields(_form_task(), "old")
    new = mod.parse_line("write memo (see doc) @i9 [20] // see https://x.com/a//b")
    assert mod.changed_fields(cur, new) == {"desc": "see https://x.com/a//b"}


def test_line_changes_all_fields_at_once():
    mod = _load()
    cur = mod.current_fields(_form_task(), "old")
    new = mod.parse_line("call 3 tenants @m5x2 [40] // new notes")
    assert mod.changed_fields(cur, new) == {
        "name": "call 3 tenants", "domain": "m5x2", "points": "40", "desc": "new notes"}


def test_line_absent_parts_keep_bare_slashes_clear():
    mod = _load()
    cur = mod.current_fields(_form_task(), "old")
    assert mod.changed_fields(cur, mod.parse_line("write memo (see doc)")) == {}
    assert mod.changed_fields(cur, mod.parse_line("write memo (see doc) //")) == {"desc": ""}
    assert mod.changed_fields(cur, mod.parse_line("// only notes")) == {"desc": "only notes"}


def test_line_multiline_description():
    mod = _load()
    assert mod.parse_line("t // a\nb")["desc"] == "a\nb"


def test_line_submit_one_call(tmp_path, monkeypatch):
    mod = _load()
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({"tasks": [_form_task()]}))
    calls = []
    monkeypatch.setattr(mod._df, "_api", lambda m, path, body: calls.append(body))
    monkeypatch.setattr(mod, "_description", lambda tid: "old notes")
    monkeypatch.setattr(mod, "_resync_short", lambda *a: None)
    monkeypatch.setattr(sys, "argv", ["edit-fast.py", "--fields", "--id", "t1", str(cache),
                                      "--line", "write memo (see doc) @i9 [40] // new notes"])
    assert mod.main() == 0
    assert calls == [{"content": "write memo (see doc) (30) [40] {5}", "description": "new notes"}]


# ── rename on a host with no Haiku (2026-10-13: ix had no API key) ──

def _long_task():
    return {"id": "t1", "content": "call the property manager about the water heater (30) [20]", "labels": ["m5x2"]}


def _rename(tmp_path, monkeypatch, haiku_result):
    mod = _load()
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({"tasks": [_long_task()]}))
    monkeypatch.setattr(mod._df, "_api", lambda *a: None)
    monkeypatch.setattr(mod, "_description", lambda tid: "")
    monkeypatch.setattr(mod._short, "shorten_tasks", lambda tasks, **k: haiku_result)
    monkeypatch.setattr(sys, "argv", ["edit-fast.py", "--fields", "--id", "t1", str(cache), "--line",
                                      "call the property manager about the broken water heater in unit 4 @m5x2 [20]"])
    return mod, cache


def test_rename_warns_when_haiku_short_name_fails(tmp_path, monkeypatch, capsys):
    mod, cache = _rename(tmp_path, monkeypatch, {})
    assert mod.main() == 0
    assert "⚠ no short name" in capsys.readouterr().out
    assert "short" not in json.loads(cache.read_text())["tasks"][0]


def test_rename_uses_haiku_short_name(tmp_path, monkeypatch, capsys):
    mod, cache = _rename(tmp_path, monkeypatch, {"t1": "PM: water heater unit 4 (30) [20]"})
    assert mod.main() == 0
    assert "⚠" not in capsys.readouterr().out
    assert json.loads(cache.read_text())["tasks"][0]["short"] == "PM: water heater unit 4 (30) [20]"


# ── split on the edit line (2026-10-21): … ✓ <pts> [what I did] ──

def test_line_split_segment_parses_and_keeps_total():
    mod = _load()
    new = mod.parse_line("write memo (see doc) @i9 [20] // notes ✓ 5 drafted memo")
    assert new["split"] == {"pts": 5, "name": "drafted memo"}
    assert (new["name"], new["points"], new["desc"]) == ("write memo (see doc)", "20", "notes")
    assert mod.parse_line("t ✓ [7]")["split"] == {"pts": 7, "name": ""}


def test_line_bare_check_is_stripped_and_literal_check_stays_text():
    mod = _load()
    assert mod.parse_line("write memo [20] ✓ ") == {"split_bare": True, "name": "write memo", "points": "20"}
    assert mod.parse_line("write memo ✓ oops") == {"name": "write memo ✓ oops"}
    assert mod.parse_line("call ✓ 3 tenants ✓ 2")["name"] == "call ✓ 3 tenants"


def _split_run(tmp_path, monkeypatch, line, desc="old notes"):
    mod = _load()
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({"tasks": [_form_task()]}))
    calls = []
    monkeypatch.setattr(mod._df, "_api", lambda m, path, body: calls.append(body))
    monkeypatch.setattr(mod, "_description", lambda tid: desc)
    monkeypatch.setattr(mod, "_resync_short", lambda *a: None)
    monkeypatch.setattr(sys, "argv", ["edit-fast.py", "--fields", "--id", "t1", str(cache), "--line", line])
    return mod, calls, cache


def test_split_applies_edits_first_then_hands_off_one_shell_line(tmp_path, monkeypatch, capsys):
    mod, calls, cache = _split_run(tmp_path, monkeypatch,
                                   "write memo (see doc) @i9 [40] // old notes ✓ 15 it's half\ndone")
    assert mod.main() == 0
    assert calls == [{"content": "write memo (see doc) (30) [40] {5}"}]   # [N] = new TOTAL, applied before the split
    out = capsys.readouterr().out.splitlines()
    assert out[-1] == "SPLIT pts=15 done_name='it'\"'\"'s half done' done_min= rem_pts= rem_min="   # one line, newline sanitised
    assert json.loads(cache.read_text())["tasks"][0]["content"].endswith("[40] {5}")


def test_split_with_no_other_change_skips_the_api(tmp_path, monkeypatch, capsys):
    mod, calls, _ = _split_run(tmp_path, monkeypatch, "write memo (see doc) @i9 [20] // old notes ✓ 5")
    assert mod.main() == 0 and calls == []
    assert capsys.readouterr().out.strip() == "SPLIT pts=5 done_name='' done_min= rem_pts= rem_min="


def test_seeded_check_left_alone_is_a_cancelled_split(tmp_path, monkeypatch, capsys):
    mod, calls, _ = _split_run(tmp_path, monkeypatch, "write memo (see doc) @i9 [20] // old notes ✓ ")
    assert mod.main() == 0 and calls == []
    assert "split cancelled" in capsys.readouterr().out


def test_new_check_without_points_is_refused_not_renamed(tmp_path, monkeypatch, capsys):
    mod, calls, _ = _split_run(tmp_path, monkeypatch, "write memo (see doc) @i9 [20] // old notes ✓ drafted")
    assert mod.main() == 1 and calls == []
    assert "split needs points" in capsys.readouterr().out


def test_existing_check_in_description_still_round_trips(tmp_path, monkeypatch, capsys):
    mod, calls, _ = _split_run(tmp_path, monkeypatch, "write memo (see doc) @i9 [20] // done ✓ yes", desc="done ✓ yes")
    assert mod.main() == 0 and calls == []
    assert "no change" in capsys.readouterr().out


# ── edit-form.py result (2026-10-21: the ctrl-o/ctrl-p form) ────────────────

def test_set_time_replaces_or_inserts_ahead_of_the_points_tail():
    mod = _load()
    assert mod.set_time("write memo (15) [20]", 30) == "write memo (30) [20]"
    assert mod.set_time("write memo [20]", 30) == "write memo (30) [20]"
    assert mod.set_time("write memo", 30) == "write memo (30)"
    assert mod.set_time("call (3) tenants [20]", 45) == "call (45) tenants [20]"  # first (N) wins, as set_points does


def test_json_form_result_applies_time_and_split_minutes(tmp_path, monkeypatch, capsys):
    mod = _load()
    cache = {"today": [{"id": "t1", "content": "write memo (30) [40]", "labels": ["i9"]}]}
    cf = tmp_path / "c.json"; cf.write_text(json.dumps(cache))
    calls = []
    monkeypatch.setattr(mod._df, "_api", lambda m, p, body=None: calls.append(body) or {})
    monkeypatch.setattr(mod, "_description", lambda tid: "notes")
    monkeypatch.setattr(mod, "_resync_short", lambda *a: True)
    res = tmp_path / "r.json"
    res.write_text(json.dumps({"name": "write memo", "points": "40", "time": "60", "short": "",
                               "short_enabled": False, "domain": "i9", "desc": "notes",
                               "split": {"pts": 15, "minutes": 20, "name": "half", "rem_pts": 30, "rem_min": None}}))
    monkeypatch.setattr(sys, "argv", ["edit-fast.py", "--fields", "--id", "t1", str(cf), "--json", str(res)])
    assert mod.main() == 0
    assert calls == [{"content": "write memo (60) [40]"}]
    assert capsys.readouterr().out.strip().splitlines()[-1] == "SPLIT pts=15 done_name=half done_min=20 rem_pts=30 rem_min="


def test_json_form_result_null_desc_means_never_loaded_keep(tmp_path, monkeypatch, capsys):
    mod = _load()
    cache = {"today": [{"id": "t1", "content": "write memo [40]", "labels": ["i9"]}]}
    cf = tmp_path / "c.json"; cf.write_text(json.dumps(cache))
    calls = []
    monkeypatch.setattr(mod._df, "_api", lambda m, p, body=None: calls.append(body) or {})
    monkeypatch.setattr(mod, "_description", lambda tid: "keep me")
    res = tmp_path / "r.json"
    res.write_text(json.dumps({"name": "write memo", "points": "40", "time": "", "short": "",
                               "short_enabled": False, "domain": "i9", "desc": None, "split": None}))
    monkeypatch.setattr(sys, "argv", ["edit-fast.py", "--fields", "--id", "t1", str(cf), "--json", str(res)])
    assert mod.main() == 0 and calls == []
    assert "no change" in capsys.readouterr().out


def test_manual_short_is_stored_for_long_titles_only(tmp_path, monkeypatch):
    mod = _load()
    stored = {}
    monkeypatch.setattr(mod._short, "_load_sidecar", lambda: {})
    monkeypatch.setattr(mod._short, "_save_sidecar", lambda d: stored.update(d))
    monkeypatch.setattr(mod._short, "_comment_store", lambda tid, h, short: stored.setdefault("comment", (tid, short)))
    long_title = "write the quarterly planning memo for the whole org (30) [20]"
    cache = {"today": [{"id": "t1", "content": long_title}]}
    assert mod.set_manual_short(cache, "t1", long_title, "Quarterly memo") == "Quarterly memo (30) [20]"
    assert cache["today"][0]["short"] == "Quarterly memo (30) [20]"
    assert stored["comment"] == ("t1", "Quarterly memo (30) [20]")
    assert mod.set_manual_short({}, "t2", "short title [5]", "x") is None
