"""Regression (2026-10-09): splitting an @m828 task in dtd logged none of the
done-today points. The split script matched the task's labels against its own
hardcoded domain tuple, which lacked m828 (added to did-fast's LABEL_TO_0FEN on
2026-10-05), so did-fast got no @label, answered "needs domain disambiguation",
and the 205 points vanished while the header still said "+205 today"."""
import importlib.util
import os
import sys
import re
from pathlib import Path

HERE = Path(__file__).parent
DTD = HERE / "dtd.sh"


def _split_body() -> str:
    text = DTD.read_text()
    m = re.search(r"cat > \"\$DTD_SPLIT\" << 'SPLITEOF'\n(.*?)\nSPLITEOF\n", text, re.S)
    assert m, "could not extract DTD_SPLIT body"
    return m.group(1)


def _did_fast():
    spec = importlib.util.spec_from_file_location("did_fast_t", HERE / "did-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["did_fast_t"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_split_domain_labels_come_from_did_fast_map():
    body = _split_body()
    snippet = body[body.index("import subprocess, importlib.util"):body.index("label_arg = ''")]
    snippet = snippet.replace("$HOME", os.path.expanduser("~"))
    ns: dict = {}
    exec(snippet, ns)
    assert "_df" in ns, "did-fast import failed; split fell back to the hardcoded list"
    assert "m828" in ns["domains"]
    assert ns["domains"] == set(_did_fast().LABEL_TO_0FEN) - {"0g"}


def test_split_header_flags_unlogged_points():
    assert "NOT logged" in _split_body()


def _label_arg(labels):
    body = _split_body()
    snippet = body[body.index("import subprocess, importlib.util"):body.index("# did-fast splits its input")]
    snippet = snippet.replace("$HOME", os.path.expanduser("~"))
    ns: dict = {"labels": labels}
    exec(snippet, ns)
    return ns["label_arg"]


def test_split_of_task_without_a_domain_label_credits_ge():
    """Regression (2026-10-22): splitting a qz12 task ("Collect mom taxes and
    send out") logged none of its [20] — qz12 has no 0分 column, so no
    @label was passed and did-fast answered "needs domain disambiguation".
    A normal completion of the same task credits 个 (_project_fen_col's
    catch-all); the split must land in the same column."""
    df = _did_fast()
    ge = df.neon_cols.domain_col("0分", "个")
    for labels in (["qz12"], [], ["christian", "n156"]):
        arg = _label_arg(labels)
        assert arg, labels
        assert df.LABEL_TO_0FEN[arg[1:]] == ge == df._project_fen_col(labels), (labels, arg)


def test_split_keeps_a_mapped_domain_label():
    assert _label_arg(["qz12", "i9"]) == "@i9"
    assert _label_arg(["m828"]) == "@m828"
