"""Regression (2026-10-27): dream-prompt-base.md wrote `v{{VERSION_SHORT}}`, but
dream-launch.sh sets VERSION_SHORT="v${GLOBAL_VER}", so prompts rendered "vv170".
The prompt base lives in the vault, so this skips where the vault is absent."""
import re
from pathlib import Path

import pytest

HERE = Path(__file__).parent
BASE = Path.home() / "vault" / "i447" / "i446" / "dream-prompt-base.md"


def test_version_short_already_has_v_prefix():
    src = (HERE / "dream-launch.sh").read_text()
    assert re.search(r'^VERSION_SHORT="v\$\{GLOBAL_VER\}"', src, re.M)


@pytest.mark.skipif(not BASE.exists(), reason="vault prompt base not on this host")
def test_prompt_base_does_not_double_the_v():
    assert not re.search(r"v\{\{VERSION(_SHORT)?\}\}", BASE.read_text())


@pytest.mark.skipif(not BASE.exists(), reason="vault prompt base not on this host")
def test_every_placeholder_is_rendered():
    """v172 PROMPT.md read "2026-10-29 ({{WEEKDAY}})": the base used a placeholder
    the launcher's sed never substituted."""
    src = (HERE / "dream-launch.sh").read_text()
    rendered = set(re.findall(r'-e "s\|\{\{([A-Z_]+)\}\}\|', src))
    used = set(re.findall(r"\{\{([A-Z_]+)\}\}", BASE.read_text()))
    assert used <= rendered, sorted(used - rendered)


def test_launcher_renders_weekday():
    src = (HERE / "dream-launch.sh").read_text()
    assert re.search(r"^WEEKDAY=\$\(date '\+%A'\)", src, re.M)
    assert '-e "s|{{WEEKDAY}}|$WEEKDAY|g"' in src
