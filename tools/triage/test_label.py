"""label: batch -> propose -> answer moves files and records the eval row; score agrees."""
import importlib.util
import json
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("label", Path(__file__).with_name("label.py"))
label = importlib.util.module_from_spec(spec); sys.modules["label"] = label
spec.loader.exec_module(label)


def _setup(tmp_path, monkeypatch):
    files = tmp_path / "files"; (files / "qz12" / "banking").mkdir(parents=True)
    for i in range(3):
        (files / "qz12" / "banking" / f"stmt{i}.pdf").write_text("x")
    cfg = {"root": files, "areas": ["qz12"], "skip": ["archive", "_meta"], "depth": 3, "min_files": 3}
    monkeypatch.setattr(label.classify, "load_config", lambda: cfg)
    monkeypatch.setattr(label.classify, "snippet", lambda p, mb: "text")
    monkeypatch.setattr(label, "STATE", tmp_path / "state")
    monkeypatch.setattr(label, "FILES", files)
    monkeypatch.setattr(label, "LABELS", files / "_meta" / "labels.jsonl")
    monkeypatch.setattr(label, "TRASH", tmp_path / "trash")
    inbox = tmp_path / "inbox"; inbox.mkdir()
    for n in ("a.pdf", "b.pdf", "c.pdf"):
        (inbox / n).write_text(n)
    return files, inbox


def test_round_trip_records_labels_and_scores(tmp_path, monkeypatch):
    files, inbox = _setup(tmp_path, monkeypatch)
    data = label.cmd_batch(inbox, 3, 1)
    assert "qz12/banking" in data["catalog"] and len(data["rows"]) == 3
    b = data["batch"]
    label.cmd_propose(b, {"1": {"a": "qz12/banking", "b": "archive", "suggest": "a"},
                          "2": {"a": "qz12/banking", "suggest": "a"},
                          "3": {"a": "archive", "suggest": "a"}})
    assert "| 1 | `a.pdf` | qz12/banking | archive |" in label.cmd_show(b)
    recs = label.cmd_answer(b, "1a 2x 3s")
    assert [r["chosen"] for r in recs] == ["qz12/banking", "trash", "skip"]
    assert (files / "qz12" / "banking" / "a.pdf").exists() and (tmp_path / "trash" / "b.pdf").exists()
    assert (inbox / "c.pdf").exists()                                  # skip leaves it
    s = label.cmd_score()
    assert s["overall"] == {"n": 2, "agree": 1, "pct": 50}             # skips don't count
    again = label.cmd_batch(inbox, 5, 1)
    assert [r["name"] for r in again["rows"]] == []                    # labeled files aren't re-asked


def test_choosing_the_folder_a_file_is_already_in_leaves_it(tmp_path, monkeypatch):
    files, _ = _setup(tmp_path, monkeypatch)
    arch = files / "archive"; arch.mkdir()
    (arch / "r.pdf").write_text("r")
    b = label.cmd_batch(arch, 5, 1)["batch"]
    label.cmd_propose(b, {"1": {"a": "archive", "suggest": "a"}})
    label.cmd_answer(b, "1a")
    assert (arch / "r.pdf").exists() and not (arch / "r ~1.pdf").exists()


def test_expand_ranges_and_default():
    assert label.expand("1-3 x, 5b", [1, 2, 3, 4, 5], "a") == [(1, "x"), (2, "x"), (3, "x"), (4, "a"), (5, "b")]
    assert label.expand("2c", [1, 2], "") == [(2, "c")]
