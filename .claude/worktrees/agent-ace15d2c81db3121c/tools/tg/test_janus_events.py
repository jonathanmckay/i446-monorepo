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


def test_per_host_file_under_sync_dir_and_read_all(tmp_path, monkeypatch):
    """2026-10-17: one file per host in the synced dir; read_all unions hosts."""
    monkeypatch.delenv("JANUS_EVENTS", raising=False)
    monkeypatch.setenv("JANUS_EVENTS_DIR", str(tmp_path))
    import janus_events
    je = importlib.reload(janus_events)
    assert je.PATH == tmp_path / f"janus-events.{je.HOST}.tsv"
    (tmp_path / "janus-events.ix.tsv").write_text("1700000002.0\ts1\taction\tcmd\t\tlater on ix\n")
    (tmp_path / "janus-events.straylight.tsv").write_text(
        "1700000001.0\ts2\tsession\tstart\t\tStraylight 93x57\nbad line\n")
    rows = je.read_all(tmp_path)
    assert [(r["host"], r["key"]) for r in rows] == [("straylight", "start"), ("ix", "cmd")]
    assert rows[1]["extra"] == "later on ix"


def test_legacy_local_log_migrates_once(tmp_path, monkeypatch):
    monkeypatch.delenv("JANUS_EVENTS", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("JANUS_EVENTS_DIR", str(tmp_path / "sync"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    legacy = tmp_path / "state" / "jm" / "janus-events.tsv"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("1700000000.0\ts\tsession\tstart\t\told\n")
    import janus_events
    # the pytest guard blocks migration during tests; call it with the guard lifted
    je = importlib.reload(janus_events)
    monkeypatch.setattr(je, "_under_pytest", lambda: False)
    je._migrate_legacy()
    assert (tmp_path / "sync" / f"janus-events.{je.HOST}.tsv").read_text().endswith("old\n")
    assert not legacy.exists() and legacy.with_suffix(".tsv.migrated").exists()
    je._migrate_legacy()  # idempotent
    assert (tmp_path / "sync" / f"janus-events.{je.HOST}.tsv").read_text().count("\n") == 1
