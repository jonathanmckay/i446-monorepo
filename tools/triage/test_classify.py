"""classify: destination catalog, year templating, model-reply parsing, apply log."""
import importlib.util
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("classify", HERE / "classify.py")
classify = importlib.util.module_from_spec(spec)
sys.modules["classify"] = classify
spec.loader.exec_module(classify)


def _tree(root: Path, files: list[str]) -> None:
    for f in files:
        p = root / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f)


def _cfg(root: Path, **kw) -> dict:
    return {"root": root, "areas": ["qz12", "Archive"], "skip": ["Archive/Web"], "depth": 3,
            "min_files": 2, "samples": 3, "model": "haiku", "batch": 2, "parallel": 1,
            "max_hydrate_mb": 10, "threshold": 0.8, **kw}


def test_year_series_templated_one_off_kept(tmp_path):
    _tree(tmp_path, ["qz12/Taxes/2018 Tax/a.pdf", "qz12/Taxes/2019 Tax/b.pdf",
                     "qz12/Trips/2014 HK Trip/c.pdf", "qz12/Trips/2014 HK Trip/d.pdf",
                     "qz12/Disputes/2012.12.10 Bank/e.pdf", "qz12/Disputes/2012.11.01 Card/f.pdf"])
    cat = classify.catalog(_cfg(tmp_path))
    assert "qz12/Taxes/{year} Tax" in cat and len(cat["qz12/Taxes/{year} Tax"].files) == 2
    assert "qz12/Trips/2014 HK Trip" in cat                 # no sibling series
    assert not any("{year}" in k for k in cat if "Disputes" in k)   # dated, not yearly


def test_depth_cap_rolls_up_and_skip_and_min_files(tmp_path):
    _tree(tmp_path, ["qz12/A/B/C/deep1.pdf", "qz12/A/B/deep2.pdf", "qz12/Lonely/one.pdf",
                     "Archive/Web/site/index.html", "Archive/Web/site/b.html",
                     "qz12/Saved_files/x.js", "qz12/Saved_files/y.js"])
    cat = classify.catalog(_cfg(tmp_path))
    assert set(cat) == {"qz12/A/B"}


def test_resolve_reuses_existing_year_folder(tmp_path):
    _tree(tmp_path, ["qz12/Taxes/2018 Tax/a.pdf", "qz12/Taxes/2019 Tax/b.pdf"])
    cfg = _cfg(tmp_path)
    assert classify.resolve(cfg, "qz12/Taxes/{year} Tax", "2019") == tmp_path / "qz12/Taxes/2019 Tax"
    assert classify.resolve(cfg, "qz12/Taxes/{year} Tax", "2021") == tmp_path / "qz12/Taxes/2021 Tax"
    assert classify.resolve(cfg, "qz12/Taxes/{year} Tax", None) is None


def test_hidden_files_never_shown_as_samples(tmp_path):
    _tree(tmp_path, ["qz12/R/secret.pdf", "qz12/R/other.pdf", "qz12/R/third.pdf"])
    cat = classify.catalog(_cfg(tmp_path))
    prompt = classify.system_prompt(list(cat.values()), {tmp_path / "qz12/R/secret.pdf"},
                                    random.Random(0), 5)
    assert "secret.pdf" not in prompt and "other.pdf" in prompt


def test_classify_parses_reply_and_falls_back_to_name_year(tmp_path, monkeypatch):
    _tree(tmp_path, ["qz12/Taxes/2018 Tax/a.pdf", "qz12/Taxes/2019 Tax/b.pdf", "in/W-2 2020.pdf",
                     "in/menu.pdf", "in/junk.pdf"])
    cfg = _cfg(tmp_path)
    dests = list(classify.catalog(cfg).values())
    replies = iter([
        json.dumps({"result": 'Sure: [{"id": 0, "dest": 0, "year": null, "confidence": 0.9},'
                              ' {"id": 1, "dest": null, "confidence": 0.2}]'}),
        json.dumps({"result": '[{"id": 2, "dest": 99, "confidence": 0.9}]'}),   # out of range
    ])
    monkeypatch.setattr(classify, "_run_claude", lambda cmd, prompt: next(replies))
    paths = [tmp_path / "in/W-2 2020.pdf", tmp_path / "in/menu.pdf", tmp_path / "in/junk.pdf"]
    g = classify.classify(paths, dests, cfg, names_only=True)
    assert (g[0].key, g[0].year, g[0].confidence) == ("qz12/Taxes/{year} Tax", "2020", 0.9)
    assert g[1].key is None and g[2].key is None


def test_apply_moves_only_above_threshold_and_logs(tmp_path, monkeypatch):
    _tree(tmp_path, ["qz12/R/x.pdf", "qz12/R/y.pdf", "in/sure.pdf", "in/unsure.pdf"])
    monkeypatch.setattr(classify, "STATE", tmp_path / "state")
    (tmp_path / "state").mkdir()
    cfg = _cfg(tmp_path)
    G = classify.Guess
    props = [(G(tmp_path / "in/sure.pdf", "qz12/R", None, 0.9), tmp_path / "qz12/R"),
             (G(tmp_path / "in/unsure.pdf", "qz12/R", None, 0.5), tmp_path / "qz12/R")]
    assert classify.cmd_apply(cfg, props) == 1
    assert (tmp_path / "qz12/R/sure.pdf").exists() and (tmp_path / "in/unsure.pdf").exists()
    rec = json.loads(next((tmp_path / "state").glob("*.jsonl")).read_text())
    assert rec["rule"] == "classify" and rec["dst"].endswith("qz12/R/sure.pdf")
