"""photos_import: album plan, screenshot routing, and dedupe against the
library and within the archive (Photos' own duplicate dialog blocks
scripted imports, so dedupe must happen before Photos sees the files)."""
import importlib.util
from pathlib import Path

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("pi", HERE / "photos_import.py")
pi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pi)


def _tree(root):
    files = {
        "2010 - 2011 Baghdad Tour/a.jpg": b"aaa",
        "2010 - 2011 Baghdad Tour/sub/b.JPG": b"bbbb",
        "Photos I like/a.jpg": b"aaa",                # repeat of Baghdad a.jpg
        "Photos I like/Screenshot 2015-01-02 at 3.04.05 PM.png": b"s",
        "loose.jpg": b"l",
        "song.mp3": b"m",
        "old.jpg": b"already",
    }
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


def test_plan_routes_albums_screenshots_and_skips(tmp_path):
    _tree(tmp_path)
    albums, shots, skipped = pi.plan(tmp_path)
    assert sorted(albums) == ["2010 - 2011 Baghdad Tour", pi.LOOSE_ALBUM, "Photos I like"]
    assert [p.name for p in albums["2010 - 2011 Baghdad Tour"]] == ["a.jpg", "b.JPG"]
    assert [p.name for p in shots] == ["Screenshot 2015-01-02 at 3.04.05 PM.png"]
    assert [p.name for p in skipped] == ["song.mp3"]


def test_dedupe_against_library_and_within_archive(tmp_path):
    _tree(tmp_path)
    albums, _, _ = pi.plan(tmp_path)
    out, n_lib, n_rep = pi.dedupe(albums, {("old.jpg", len(b"already"))})
    assert n_lib == 1 and n_rep == 1
    assert "Photos I like" not in out          # its only photo repeats Baghdad's
    assert [p.name for p in out[pi.LOOSE_ALBUM]] == ["loose.jpg"]
