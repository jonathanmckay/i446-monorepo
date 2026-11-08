"""Regression tests for export-claude-transcripts.py filename collisions.

Bug (2026-11-08, Dream v182): files were named <date>_<slug>[_pNN].md with no
session id. Current Claude Code sessions have no slug ("untitled"), so every
session in a project on the same date wrote to the same path and the last one
exported overwrote the rest (24 of 53 ix sessions since ~09-21 lost).
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "export_claude_transcripts", Path(__file__).parent / "export-claude-transcripts.py")
m = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(m)  # type: ignore[union-attr]


def _write_session(proj: Path, sid: str, n_turns: int, marker: str,
                   ts: str = "2026-11-05T18:00:00Z") -> Path:
    proj.mkdir(parents=True, exist_ok=True)
    p = proj / f"{sid}.jsonl"
    lines = []
    for i in range(n_turns):
        lines.append({"type": "user", "timestamp": ts,
                      "message": {"content": f"{marker} question {i}"}})
        lines.append({"type": "assistant",
                      "message": {"content": [{"type": "text", "text": f"{marker} answer {i}"}]}})
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return p


def _export(proj: Path, out: Path, force: bool = False) -> None:
    for jp in sorted(proj.glob("*.jsonl")):
        m.export_session(m.parse_session(jp), out, m.SEGMENT_TURNS, force=force, verbose=False)


def _session_ids(out: Path) -> set[str]:
    ids = set()
    for f in out.glob("*.md"):
        for line in f.read_text().splitlines():
            if line.startswith("session_id: "):
                ids.add(line.split(": ", 1)[1])
    return ids


SID_A = "aaaaaaaa-1111-4111-8111-111111111111"
SID_B = "bbbbbbbb-2222-4222-8222-222222222222"


def test_same_date_untitled_sessions_do_not_collide(tmp_path: Path) -> None:
    proj, out = tmp_path / "proj", tmp_path / "out"
    _write_session(proj, SID_A, 3, "alpha")
    _write_session(proj, SID_B, 3, "bravo")
    _export(proj, out)
    assert _session_ids(out) == {SID_A, SID_B}
    assert (out / "2026-11-05_untitled_aaaaaaaa.md").exists()
    assert (out / "2026-11-05_untitled_bbbbbbbb.md").exists()


def test_sharded_sessions_do_not_collide(tmp_path: Path) -> None:
    proj, out = tmp_path / "proj", tmp_path / "out"
    _write_session(proj, SID_A, 25, "alpha")   # 3 shards
    _write_session(proj, SID_B, 12, "bravo")   # 2 shards
    _export(proj, out)
    names = sorted(f.name for f in out.glob("*.md"))
    assert names == [
        "2026-11-05_untitled_aaaaaaaa_p01.md",
        "2026-11-05_untitled_aaaaaaaa_p02.md",
        "2026-11-05_untitled_aaaaaaaa_p03.md",
        "2026-11-05_untitled_bbbbbbbb_p01.md",
        "2026-11-05_untitled_bbbbbbbb_p02.md",
    ]
    assert "alpha" in (out / "2026-11-05_untitled_aaaaaaaa_p03.md").read_text()
    assert "bravo" in (out / "2026-11-05_untitled_bbbbbbbb_p02.md").read_text()


def test_idempotent_same_session_same_files(tmp_path: Path) -> None:
    proj, out = tmp_path / "proj", tmp_path / "out"
    _write_session(proj, SID_A, 12, "alpha")
    _export(proj, out)
    first = {f.name: f.read_text() for f in out.glob("*.md")}
    _export(proj, out)
    _export(proj, out, force=True)
    assert {f.name: f.read_text() for f in out.glob("*.md")} == first


def test_legacy_files_left_untouched(tmp_path: Path) -> None:
    proj, out = tmp_path / "proj", tmp_path / "out"
    out.mkdir(parents=True)
    legacy = out / "2026-11-05_untitled.md"
    legacy.write_text("legacy content\n")
    _write_session(proj, SID_A, 3, "alpha")
    _export(proj, out, force=True)
    assert legacy.read_text() == "legacy content\n"
    assert (out / "2026-11-05_untitled_aaaaaaaa.md").exists()
