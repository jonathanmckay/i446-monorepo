"""janus_done.write_failures: the clients' copy of did-fast's _write_failures
(2026-11-12, lost 0分 write flashed ✓ +100分 on desktop janus)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import janus_done  # noqa: E402


def test_lists_failed_write_blocks_only():
    data = {"results": [{"name": "Jon/Sarah connect", "0fen": {"points": 100}}],
            "0fen_write": {"ok": False, "error": "ssh: connect timed out"},
            "hcbi_write": {"ok": True, "output": "OK:hcbi row=317"},
            "0n_write": {"ok": False}}
    assert janus_done.write_failures(data) == ["0fen_write: ssh: connect timed out", "0n_write: failed"]


def test_clean_run_and_none_are_empty():
    assert janus_done.write_failures(None) == []
    assert janus_done.write_failures({"results": [], "0fen_write": {"ok": True}}) == []
