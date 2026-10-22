"""dtd renumbers cmux tabs when the claim store changes (2026-10-22): an ix-hosted session's hooks
run on ix (no cmux), so its pane number, the N in "N😈", only reached panes.json when some local
session's hook ran. The watcher now runs a detached, throttled `cs retitle` on a claims change."""
from pathlib import Path

DTD = (Path(__file__).parent / "dtd.sh").read_text()


def test_claims_change_triggers_detached_throttled_retitle():
    i = DTD.index('cur_ac=$(stat -f %Fm "$HOME/vault/z_ibx/agent-claims"')
    block = DTD[i:i + 1500]
    assert '( "$HOME/.local/bin/cs" retitle </dev/null >/dev/null 2>&1 & )' in block
    assert "(( EPOCHSECONDS - ${last_retitle:-0} >= 10 ))" in block
    assert "command -v cmux" in block
    # inside the mtime-changed branch, not on every watcher tick
    assert block.index('"$cur_ac" != "${last_ac:-}"') < block.index("cs\" retitle") < block.index("_acs=(")
