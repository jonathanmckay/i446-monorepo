"""periodic-sync's heartbeat is per-host and untracked (Dream v172, 2026-10-29).

Tracked in the repo it produced ~60% of autopush commits (79 of 137 on
10-28/29 touched nothing else), and Syncthing let one Mac's heartbeat mask a
stalled sync on the other."""
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
HB = '${XDG_CACHE_HOME:-$HOME/.cache}/ai-dashboard'


def test_writer_and_watchdog_share_the_cache_path():
    writer = (HERE / "periodic-sync.sh").read_text()
    watchdog = (HERE / "watchdog.sh").read_text()
    assert f'HB_DIR="{HB}"' in writer and '"$HB_DIR/periodic-sync.heartbeat"' in writer
    assert f'HB_FILE="{HB}/periodic-sync.heartbeat"' in watchdog
    assert not re.search(r"SCRIPT_DIR/\.periodic-sync\.heartbeat|ai-dashboard/\.periodic-sync\.heartbeat",
                         writer + watchdog)


def test_heartbeat_is_not_tracked():
    r = subprocess.run(["git", "-C", str(HERE), "ls-files", ".periodic-sync.heartbeat"],
                       capture_output=True, text=True)
    assert r.stdout.strip() == ""


def test_writer_heartbeat_lands_where_watchdog_reads(tmp_path):
    # Run just the heartbeat lines of periodic-sync.sh with a fake cache dir.
    writer = (HERE / "periodic-sync.sh").read_text()
    lines = [l for l in writer.splitlines() if l.startswith(("HB_DIR=", "mkdir -p \"$HB_DIR\""))]
    subprocess.run(["bash", "-c", "\n".join(lines)], check=True,
                   env={"XDG_CACHE_HOME": str(tmp_path), "HOME": str(tmp_path), "PATH": "/bin:/usr/bin"})
    hb = tmp_path / "ai-dashboard" / "periodic-sync.heartbeat"
    assert hb.read_text().strip().isdigit()
