"""Every screenshot naming pattern in the Dropbox pile maps to a date-first
name, and migrate copies without touching the source."""
import datetime as dt
import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("sn", HERE / "screenshot_names.py")
sn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sn)

MTIME = dt.datetime(2024, 3, 1, 10, 15, 0).timestamp()


@pytest.mark.parametrize("name,want", [
    ("Screenshot 2026-10-27 at 8.20.47 AM.png", "2026-10-27_082047--ss.png"),
    ("Screenshot 2026-10-26 at 9.37.26 PM.png", "2026-10-26_213726--ss.png"),
    ("Screenshot 2026-01-05 at 12.01.02 AM.png", "2026-01-05_000102--ss.png"),
    ("Screenshot 2026-01-05 at 12.01.02 PM.png", "2026-01-05_120102--ss.png"),
    ("Screenshot 2018-10-02 13.45.10.png", "2018-10-02_134510--ss.png"),
    ("Screenshot 2021-04-09 at 17.03.44.png", "2021-04-09_170344--ss.png"),
    ("Screen Shot 2020-06-01 at 9.05.00 AM.png", "2020-06-01_090500--ss.png"),
    ("Screen Shot 2020-06-01 at 9.05.00 AM (2).png", "2020-06-01_090500--ss-2.png"),
    ("Screenshot 2019-07-04 10.00.00(3).png", "2019-07-04_100000--ss-3.png"),
    ("2017-03-02 10.11.12.png", "2017-03-02_101112--ss.png"),
    ("2017-03-02 10.11.12-1.PNG", "2017-03-02_101112--ss-2.png"),
    ("IMG_1234.jpg", "2024-03-01_101500--dl--IMG_1234.jpg"),
    ("2026-10-27_082047--ss.png", "2026-10-27_082047--ss.png"),
])
def test_new_name(name, want):
    assert sn.new_name(name, MTIME) == want


def test_migrate_copies_and_dedupes(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    (src / "Screenshot 2026-10-27 at 8.20.47 AM.png").write_bytes(b"a")
    (src / "Screenshot 2026-10-27 08.20.47.png").write_bytes(b"bb")  # same instant
    (src / ".DS_Store").write_bytes(b"x")
    (src / "deck.pptx").write_bytes(b"p")
    assert sn.migrate(src, dst) == 2
    assert sorted(p.name for p in dst.iterdir()) == [
        "2026-10-27_082047--ss-2.png", "2026-10-27_082047--ss.png"]
    assert len(list(src.iterdir())) == 4  # source untouched


def test_migrate_move_leaves_only_non_images(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    (src / "Screenshot 2026-10-27 at 8.20.47 AM.png").write_bytes(b"a")
    (src / "deck.pptx").write_bytes(b"p")
    assert sn.migrate(src, dst, move=True) == 1
    assert [p.name for p in dst.iterdir()] == ["2026-10-27_082047--ss.png"]
    assert [p.name for p in src.iterdir()] == ["deck.pptx"]
