"""janus usage events share dtd's 6-column TSV schema (epoch, session, event,
key, ids, extra), so dtd-events.tsv and janus-events.tsv can be read together."""
import importlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _load(tmp_path, monkeypatch):
    monkeypatch.setenv("JANUS_EVENTS", str(tmp_path / "ev.tsv"))
    import janus_events
    return importlib.reload(janus_events)


def test_schema_matches_dtd(tmp_path, monkeypatch):
    je = _load(tmp_path, monkeypatch)
    je.emit("action", "edit", ["111", 222], "lunch\twith\nnewline @hcb")
    cols = (tmp_path / "ev.tsv").read_text().rstrip("\n").split("\t")
    assert len(cols) == 6
    float(cols[0])
    pid, epoch, rnd = cols[1].split("-")          # dtd session id shape
    assert pid == str(os.getpid()) and epoch.isdigit() and rnd.isdigit()
    assert cols[2:5] == ["action", "edit", "111,222"]
    assert cols[5] == "lunch with newline @hcb"


def test_never_raises_on_unwritable_path(tmp_path, monkeypatch):
    monkeypatch.setenv("JANUS_EVENTS", str(tmp_path / "missing-dir" / "ev.tsv"))
    import janus_events
    importlib.reload(janus_events).emit("session", "start")


def test_pytest_without_override_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("JANUS_EVENTS", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    (tmp_path / "jm").mkdir()
    import janus_events
    importlib.reload(janus_events).emit("action", "cmd")
    assert not (tmp_path / "jm" / "janus-events.tsv").exists()
