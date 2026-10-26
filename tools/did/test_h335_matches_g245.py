"""2026-10-26 (JM): "in Janus + dtd, let's make h335 the same color as g245,
and note that it maps to 个 in neon." Every color table carries h335 with
g245's color, and both 0分 routing maps send h335 to 个 (column T)."""
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "lib"))


def _hex_after(src: str, key: str) -> str:
    return re.search(r'["\']%s["\']\s*:\s*["\'](#[0-9a-fA-F]{6})' % key, src).group(1).lower()


def test_hex_color_tables_give_h335_g245s_color():
    for rel in ("tools/tg/janus.py", "tools/did/dtd-ticker.py", "tools/janus/mobile.py", "tools/dtd/dtd.py"):
        src = (ROOT / rel).read_text()
        assert _hex_after(src, "h335") == _hex_after(src, "g245") == "#00e676", rel


def test_dtd_ansi_palette_gives_h335_g245s_color():
    src = (ROOT / "tools/did/dtd.sh").read_text()
    ansi = lambda k: re.search(r"'%s':\s*'(\\033\[38;2;[0-9;]+m)'" % k, src).group(1)
    assert ansi("h335") == ansi("g245")


def test_h335_credits_ge_column():
    spec = importlib.util.spec_from_file_location("df_h335", ROOT / "tools/did/did-fast.py")
    df = importlib.util.module_from_spec(spec)
    sys.modules["df_h335"] = df
    spec.loader.exec_module(df)
    from neon import cols
    assert df.LABEL_TO_0FEN["h335"] == "T"
    assert cols.domain_col("0分", "h335") == "T" == cols.col("0分", "个")
