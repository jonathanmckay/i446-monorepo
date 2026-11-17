"""dtd's inline-form wrapper and background-saving forms (2026-11-17).

JM: "move saving the 1xk887 survey ... to the background so that the UI is
more snappy, and then we can have the bottom notification letting me know
when it is saved." Two wrapper behaviours make that notification real:
  * the form's child gets dtd's listen-port file and header generator, so it
    can ask fzf to repaint (nothing watches the header file);
  * the wrapper's generic "✓ <form>" must not overwrite a status the form or
    its saver already wrote, or the header says done while Excel is still
    being written.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

DTD = (Path(__file__).parent / "dtd.sh").read_text()


def _wrapper() -> str:
    i = DTD.index('cat > "$DTD_FORM" << FORMEOF')
    return DTD[i:DTD.index("\nFORMEOF\n", i)]


def test_wrapper_exports_what_a_saver_needs_to_repaint_the_header():
    w = _wrapper()
    assert 'export DTD_PORT_FILE="$DTD_PORT"' in w
    assert 'export DTD_HDRGEN_FILE="$DTD_HDRGEN"' in w


def test_xk887_route_saves_in_the_background():
    assert 'tools/xk887/xk887-survey.py --detach"' in DTD


def _tick_logic(tmp_path, before: str, during: str | None) -> str:
    """Run the wrapper's own header lines against a real file."""
    w = _wrapper()
    snap = next(l for l in w.splitlines() if l.startswith('_h0=""'))
    j = w.index("  _h1=")
    tick = w[j:w.index("\nfi\n", j)]
    hdr = tmp_path / "hdr"
    hdr.write_text(before)
    # trailing `true`: in the wrapper the tick line sits inside if/fi + exit 0
    script = "HDR=%s\nform=/x/xk887-survey.py\n%s\n%s\n%s\ntrue\n" % (
        hdr, snap.replace("\\$", "$"),
        ("print -r -- %r > $HDR" % during) if during is not None else ":",
        tick.replace("\\$", "$"))
    subprocess.run(["zsh", "-c", script], check=True, env=dict(os.environ))
    return hdr.read_text().strip()


def test_wrapper_ticks_when_the_form_left_the_header_alone(tmp_path):
    assert _tick_logic(tmp_path, "✓ 0t\n", None) == "✓ xk887-survey.py"


def test_wrapper_keeps_a_status_the_form_or_its_saver_wrote(tmp_path):
    assert _tick_logic(tmp_path, "✓ 0t\n", "⏳ xk887 week 11.2: saving xk20 (2 of 4) …") \
        == "⏳ xk887 week 11.2: saving xk20 (2 of 4) …"
