#!/usr/bin/env python3
"""Feature (2026-08-02): "add a header to dtd that tells me when I should
restart the app, similar to what I have on janus."

janus.py (tools/tg/janus.py) captures its own source mtime at import and has
render_header() replace the WHOLE header line in red once the file on disk
is newer -- a long-lived process only ever runs the code it loaded at
launch, so a shipped fix is otherwise invisible until a restart nobody
remembers to do (this exact session hit that: two dtd.sh fixes landed while
an open session kept running the old, buggy code).

dtd.sh has no single long-lived Python process to hang a check on -- it
sources itself once at launch to generate a family of tiny helper scripts,
the most frequently re-invoked of which is $DTD_HDRGEN (bound via
transform-header to load/result/resize and after nearly every keybinding).
That makes it the natural place to mirror janus's check: dtd.sh captures its
own mtime into $DTD_SRC_MTIME once at launch (baked as a literal into the
unquoted HDRGENEOF heredoc), and hdrgen re-stats the live file on every
repaint.
"""
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = (HERE / "dtd.sh").read_text()


def _hdrgen_body() -> str:
    m = re.search(
        r"cat > \"\$DTD_HDRGEN\" <<HDRGENEOF\n(.*?)\nHDRGENEOF",
        SRC, re.S)
    assert m, "could not find the DTD_HDRGEN heredoc in dtd.sh"
    return m.group(1)


# ── Structural ────────────────────────────────────────────────────────────

def test_self_mtime_captured_at_launch():
    assert 'DTD_SELF="$HOME/i446-monorepo/tools/did/dtd.sh"' in SRC
    assert 'DTD_SRC_MTIME=$(stat -f %m "$DTD_SELF"' in SRC
    # Must be captured BEFORE the hdrgen heredoc bakes it in.
    assert SRC.index("DTD_SRC_MTIME=") < SRC.index('cat > "$DTD_HDRGEN"')


def test_hdrgen_checks_live_mtime_before_anything_else():
    body = _hdrgen_body()
    assert body.startswith("#!/bin/zsh")
    lines = [l for l in body.splitlines() if l.strip() and not l.strip().startswith("#")]
    # First non-comment statement must be the live-mtime stat -- the check
    # has to run before any of the normal header composition.
    assert 'stat -f %m "$DTD_SELF"' in lines[0]


def test_stale_branch_replaces_the_whole_header_and_exits():
    body = _hdrgen_body()
    assert "RESTART DTD" in body
    # Must exit before falling into the normal ws/tally/keys composition --
    # otherwise the warning would just be a prefix, not a full replacement,
    # diverging from janus's "whole header goes red" behavior.
    warn_pos = body.index("RESTART DTD")
    exit_pos = body.index("exit 0", warn_pos)
    normal_pos = body.index('ws=\\$(cat "$DTD_HDR"')
    assert warn_pos < exit_pos < normal_pos


def test_stale_branch_uses_ansi_red_matching_the_rest_of_dtd():
    body = _hdrgen_body()
    assert "\\033[1;91m" in body or "\\033[38;2;255" in body, (
        "must use an ANSI escape (dtd already relies on --ansi for domain "
        "colors) so the warning actually renders red, not literal escape text")
    assert "\\033[0m" in body, "must reset color after the warning"


# ── Functional: run the real hdrgen body against fake mtimes ──────────────

def _run_hdrgen(tmp_path, self_mtime, live_mtime, dtd_hdr="", dtd_tally=""):
    fake_self = tmp_path / "dtd.sh"
    fake_self.write_text("#!/bin/zsh\n")
    os.utime(fake_self, (live_mtime, live_mtime))

    hdr = tmp_path / "hdr"
    tally = tmp_path / "tally"
    hdr.write_text(dtd_hdr)
    tally.write_text(dtd_tally)

    body = _hdrgen_body()
    # Substitute the baked-in outer vars the real heredoc would have
    # resolved at write time: $DTD_SELF -> our fake path, $DTD_SRC_MTIME ->
    # the launch-time baseline, $DTD_HDR/$DTD_TALLY -> our fixtures. These
    # are bare (unescaped) in the source precisely because HDRGENEOF is an
    # unquoted heredoc that bakes them in as literals when the real dtd.sh
    # writes this file.
    script = body.replace("$DTD_SELF", str(fake_self))
    script = re.sub(r"\$DTD_SRC_MTIME\b", str(int(self_mtime)), script)
    script = script.replace('"$DTD_HDR"', f'"{hdr}"')
    script = script.replace('"$DTD_TALLY"', f'"{tally}"')
    # Everything else (\$tally, \$ws, \$DTD_KEYS, \$(stat ...), \${...}) is
    # escaped in the SOURCE only so the outer heredoc doesn't touch it at
    # write time -- real dtd.sh strips that backslash when it writes the
    # file, leaving plain runtime references in the actual generated
    # hdrgen.sh. Replicate that unescaping to get valid standalone zsh.
    script = script.replace("\\$", "$")
    script_path = tmp_path / "hdrgen.sh"
    script_path.write_text(script)
    script_path.chmod(0o755)

    env = {**os.environ, "DTD_KEYS": "enter: start | ..."}
    r = subprocess.run(["zsh", str(script_path)], capture_output=True,
                       text=True, timeout=5, env=env)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_fresh_code_shows_normal_header(tmp_path):
    now = time.time()
    out = _run_hdrgen(tmp_path, self_mtime=now, live_mtime=now,
                      dtd_hdr="✓ done\n")
    assert "RESTART" not in out
    assert "left" in out


def test_code_updated_after_launch_shows_restart_warning(tmp_path):
    now = time.time()
    out = _run_hdrgen(tmp_path, self_mtime=now, live_mtime=now + 30)
    assert "RESTART DTD" in out
    assert "\033[1;91m" in out
    assert "left" not in out, "stale header must fully replace, not prefix"


def test_one_second_clock_skew_does_not_false_positive(tmp_path):
    """Matches janus's own +1.0s tolerance -- filesystem mtime resolution
    and write/stat timing jitter must not flap the warning on for a launch
    that didn't actually change anything."""
    now = time.time()
    out = _run_hdrgen(tmp_path, self_mtime=now, live_mtime=now + 0.5)
    assert "RESTART" not in out


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
