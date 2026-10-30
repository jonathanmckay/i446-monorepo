"""Regression (2026-10-27): export-claude-transcripts.py named files
<date>_<slug>.md; Claude Code stopped writing `slug`, so every session was
"untitled" and same-day sessions overwrote each other (Straylight 2026-10-26:
14 sessions, 2 files). Names now carry a session-id suffix; legacy files a
session already owns keep their names."""
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("ect", HERE / "export-claude-transcripts.py")
ect = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ect)


def _jsonl(path: Path, turns: int, ts="2026-10-26T17:00:00Z", tag="x"):
    lines = []
    for i in range(turns):
        lines.append({"type": "user", "timestamp": ts,
                      "message": {"content": f"{tag} question {i}"}})
        lines.append({"type": "assistant",
                      "message": {"content": [{"type": "text", "text": f"{tag} answer {i}"}]}})
    path.write_text("\n".join(json.dumps(l) for l in lines))
    return path


def _export(src: Path, out: Path):
    s = ect.parse_session(src)
    ect.export_session(s, out, ect.SEGMENT_TURNS, force=False, verbose=False)
    return s


def test_same_day_untitled_sessions_do_not_collide(tmp_path):
    proj, out = tmp_path / "proj", tmp_path / "out"
    proj.mkdir()
    a = _jsonl(proj / "aaaaaaaa-1111-2222-3333-444444444444.jsonl", 2, tag="alpha")
    b = _jsonl(proj / "bbbbbbbb-1111-2222-3333-444444444444.jsonl", 3, tag="beta")
    _export(a, out); _export(b, out)
    files = sorted(p.name for p in out.glob("*.md"))
    assert files == ["2026-10-26_untitled_aaaaaaaa.md", "2026-10-26_untitled_bbbbbbbb.md"]
    assert "alpha answer 1" in (out / files[0]).read_text()
    assert "beta answer 2" in (out / files[1]).read_text()


def test_segmented_sessions_do_not_mix_shards(tmp_path):
    proj, out = tmp_path / "proj", tmp_path / "out"
    proj.mkdir()
    a = _jsonl(proj / "aaaaaaaa-0000.jsonl", 25, tag="alpha")
    b = _jsonl(proj / "bbbbbbbb-0000.jsonl", 12, tag="beta")
    _export(a, out); _export(b, out)
    names = sorted(p.name for p in out.glob("*.md"))
    assert names == [
        "2026-10-26_untitled_aaaaaaaa_p01.md", "2026-10-26_untitled_aaaaaaaa_p02.md",
        "2026-10-26_untitled_aaaaaaaa_p03.md",
        "2026-10-26_untitled_bbbbbbbb_p01.md", "2026-10-26_untitled_bbbbbbbb_p02.md",
    ]
    assert "session_id: aaaaaaaa-0000" in (out / names[2]).read_text()


def test_legacy_file_owned_by_session_is_updated_in_place(tmp_path):
    proj, out = tmp_path / "proj", tmp_path / "out"
    proj.mkdir(); out.mkdir()
    sid = "cccccccc-0000"
    legacy = out / "2026-10-26_untitled.md"
    legacy.write_text(f"---\nsession_id: {sid}\n---\nold\n")
    import os; os.utime(legacy, (0, 0))      # older than the jsonl -> refresh
    src = _jsonl(proj / f"{sid}.jsonl", 2, tag="gamma")
    _export(src, out)
    assert [p.name for p in out.glob("*.md")] == ["2026-10-26_untitled.md"]
    assert "gamma answer 1" in legacy.read_text()


def test_legacy_file_owned_by_other_session_is_left_alone(tmp_path):
    proj, out = tmp_path / "proj", tmp_path / "out"
    proj.mkdir(); out.mkdir()
    legacy = out / "2026-10-26_untitled.md"
    legacy.write_text("---\nsession_id: someone-else\n---\nkeep me\n")
    src = _jsonl(proj / "dddddddd-0000.jsonl", 2, tag="delta")
    _export(src, out)
    assert legacy.read_text().endswith("keep me\n")
    assert (out / "2026-10-26_untitled_dddddddd.md").exists()


def test_legacy_growth_does_not_clobber_neighbour_shard(tmp_path):
    # Session owns the legacy single file but has grown past SEGMENT_TURNS; a
    # different session already owns the legacy _p01 slot.
    proj, out = tmp_path / "proj", tmp_path / "out"
    proj.mkdir(); out.mkdir()
    sid = "eeeeeeee-0000"
    (out / "2026-10-26_untitled.md").write_text(f"---\nsession_id: {sid}\n---\n")
    other = out / "2026-10-26_untitled_p01.md"
    other.write_text("---\nsession_id: other\n---\nkeep\n")
    _export(_jsonl(proj / f"{sid}.jsonl", 15, tag="eps"), out)
    assert other.read_text().endswith("keep\n")
    assert (out / "2026-10-26_untitled_eeeeeeee_p02.md").exists()


def test_dry_run_writes_nothing(tmp_path):
    proj, out = tmp_path / "proj", tmp_path / "out"
    proj.mkdir()
    s = ect.parse_session(_jsonl(proj / "ffffffff-0000.jsonl", 2))
    n = ect.export_session(s, out, ect.SEGMENT_TURNS, force=False, verbose=False, dry_run=True)
    assert n == 1 and not out.exists()
