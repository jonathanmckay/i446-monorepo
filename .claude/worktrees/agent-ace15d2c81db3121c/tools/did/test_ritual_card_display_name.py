"""Ritual cards may carry a display name ('card') distinct from their tag.

2026-10-14: "can we rename the repeating سمش to s سمش". The daemon now creates
the card as `😈 s سمش`; every reader still resolves it to the سمش tag because
they match the tag as a whole token. This pins both halves.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "lib"))
import neon_blocks  # noqa: E402

CFG = json.loads((ROOT / "config/block-rituals.json").read_text())


def test_samsh_card_name_is_s_prefixed():
    r = {x["tag"]: x for x in CFG["rituals"]}["سمش"]
    assert r["card"] == "s سمش"


def test_every_card_name_contains_its_tag_as_token():
    for r in CFG["rituals"]:
        assert r["tag"] in (r.get("card") or r["tag"]).split()


def test_card_name_resolves_back_to_tag():
    for r in CFG["rituals"]:
        name = f"{CFG['auto_marker']} {r.get('card') or r['tag']}"
        assert neon_blocks.ritual_card_tag(name, CFG) == r["tag"]


def test_daemon_creates_card_from_display_name():
    src = (ROOT / "scripts/build-order-daemon.py").read_text()
    assert "content = f\"{marker} {r.get('card') or tag}\"" in src
