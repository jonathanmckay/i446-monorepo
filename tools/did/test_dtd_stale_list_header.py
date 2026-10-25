"""dtd's header flags a stale task list (2026-10-25: "I thought I marked lock
down #data channel done but I see it today"). dtd opens on the saved cache and
refreshes in the background; after a laptop wake the list was last night's for
~3 minutes, including a task completed in the Todoist app since. The header now
says '⏳ list from HH:MM' while the live cache is older than DTD_CACHE_MAX_AGE."""
import os
import subprocess
import time
from pathlib import Path

DTD = (Path(__file__).parent / "dtd.sh").read_text()


def _hdrgen(tmp: Path, cache_age_s: int) -> str:
    start = DTD.index('cat > "$DTD_HDRGEN" <<HDRGENEOF')
    end = DTD.index("HDRGENEOF\n", start + 40) + len("HDRGENEOF\n")
    cache = tmp / "task-queue.json"
    cache.write_text("{}")
    t = time.time() - cache_age_s
    os.utime(cache, (t, t))
    (tmp / "self.sh").write_text("")
    gen = tmp / "gen.zsh"
    gen.write_text(
        f'DTD_HDRGEN={tmp}/hdrgen; DTD_SELF={tmp}/self.sh; DTD_SRC_MTIME=$(( $(date +%s) + 100 ));\n'
        f'DTD_TIMING=/dev/null; DTD_TALLY={tmp}/tally; DTD_HDR={tmp}/hdr; CACHE={cache}; DTD_CACHE_MAX_AGE=600\n'
        + DTD[start:end])
    subprocess.run(["zsh", str(gen)], check=True)
    r = subprocess.run(["zsh", str(tmp / "hdrgen")], capture_output=True, text=True,
                       env={**os.environ, "DTD_KEYS": "keys"})
    return r.stdout


def test_fresh_cache_shows_no_warning(tmp_path):
    out = _hdrgen(tmp_path, 60)
    assert "list from" not in out and out.endswith("keys")


def test_stale_cache_says_how_old_the_list_is(tmp_path):
    out = _hdrgen(tmp_path, 11 * 3600)
    assert "⏳ list from" in out and "refreshing (ctrl-r)" in out
    assert time.strftime("%H:%M", time.localtime(time.time() - 11 * 3600)) in out
