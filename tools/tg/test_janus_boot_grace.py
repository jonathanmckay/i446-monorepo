"""Regression (2026-06-11): cmux respawn-pane types the launch command into
the pane; the queued tty text reached tg-tui's always-focused input buffer and
the enter handler started a Toggl timer named
'python3 ~/i446-monorepo/tools/tg/tg-tui.py'. The enter handler must ignore
submissions during the boot grace window."""
import importlib.util
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent


def _load_tui():
    spec = importlib.util.spec_from_file_location("tg_tui_boot", HERE / "tg-tui.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tg_tui_boot"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_boot_grace_active_right_after_start():
    mod = _load_tui()
    mod.STATE.boot_time = time.monotonic()
    assert mod._boot_grace_active() is True


def test_boot_grace_expires():
    mod = _load_tui()
    mod.STATE.boot_time = time.monotonic() - 10
    assert mod._boot_grace_active() is False


def test_enter_handler_checks_boot_grace():
    """Structural: the enter handler must consult _boot_grace_active BEFORE
    running tg-fast (which starts timers)."""
    src = (HERE / "tg-tui.py").read_text()
    handler = src.split('@kb.add("enter")', 1)[1].split("@kb.add", 1)[0]
    assert "_boot_grace_active()" in handler, "enter handler lost the boot-grace gate"
    assert handler.index("_boot_grace_active()") < handler.index("run_tg_fast"), \
        "boot-grace check must come before the timer start"


def test_main_rearms_boot_time():
    """Structural: main() must reset boot_time when the app takes the tty —
    module import can happen seconds earlier."""
    src = (HERE / "tg-tui.py").read_text()
    main_src = src.split("async def main()", 1)[1]
    assert "STATE.boot_time = time.monotonic()" in main_src
