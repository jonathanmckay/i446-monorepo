"""ai-timing-log.sh must record whether the prompt came from a terminal.

Bug (2026-11-03): tty=True was hardcoded, so cron's hourly `claude -p` and
Dream's 03:30 run logged as typed prompts and /1-1n marked 🖥️ desk in every
block. A hook with no controlling terminal anywhere up its ancestry must log
tty False.
"""
import json
import os
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_prompt_without_terminal_logs_tty_false(tmp_path):
    home = tmp_path
    (home / ".claude").mkdir()
    (home / ".claude" / ".host-name").write_text("testhost")
    (home / "i446-monorepo").symlink_to(REPO)
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST")}
    env["HOME"] = str(home)
    # start_new_session: no controlling terminal, like cron / launchd.
    subprocess.run(["bash", str(REPO / "scripts/ai-timing-log.sh"), "prompt_start"],
                   input='{"session_id": "s-test"}', text=True, env=env,
                   start_new_session=True, check=True, timeout=20)
    log_dir = home / "vault" / "i447" / "i446" / "source-log"
    deadline = time.time() + 15
    rows = []
    while time.time() < deadline and not rows:
        rows = [json.loads(l) for f in log_dir.glob("testhost-*.jsonl")
                for l in f.read_text().splitlines()] if log_dir.exists() else []
        time.sleep(0.2)
    assert rows, "no source-log row written"
    assert rows[0]["metric"] == "prompt" and rows[0]["ext_id"] == "s-test"
    assert rows[0]["tty"] is False
