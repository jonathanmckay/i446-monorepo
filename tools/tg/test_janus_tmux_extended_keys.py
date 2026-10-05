"""Bug 2026-10-05: Ctrl+= didn't go forward a day in janus.

janus runs inside tmux on Ix. tmux's default `extended-keys off` rewrites
Ctrl+= to a bare "=" (and Ctrl+-/Ctrl+/ to 0x1F), so janus's CSI-u aliases
(ESC[61;5u → F23 → _day_forward) never saw the key. The tracked tmux config
must forward modified keys in the same CSI-u shape janus parses."""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CONF = (REPO / "dotfiles/tmux.conf").read_text()
JANUS = (REPO / "tools/tg/janus.py").read_text()


def test_tmux_forwards_modified_keys_as_csi_u():
    assert re.search(r"^set -s extended-keys always\s*$", CONF, re.M)
    assert re.search(r"^set -s extended-keys-format csi-u\s*$", CONF, re.M)
    assert "extkeys" in CONF


def test_janus_aliases_the_csi_u_ctrl_equals_tmux_sends():
    assert 'ANSI_SEQUENCES["\\x1b[61;5u"] = Keys.F23' in JANUS
    assert '@kb.add("f23")' in JANUS
