"""User report 2026-07-31: `/tg 1427-1447 冥想 #xk26, 1427-1652 hcm #-1` —
item 2's MECE trim DELETED the 冥想 entry item 1 had just created (deliberate
overlap between the user's own batch items), and the domain-only fallback
reinstated the raw "#-1" into the hcm entry's description."""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load():
    spec = importlib.util.spec_from_file_location("tg_fast_batch", HERE / "tg-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tg_fast_batch"] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeTogglApi:
    def __init__(self):
        self.trim_calls = []

    def trim_range(self, start_dt, end_dt, exclude_ids=None):
        self.trim_calls.append((start_dt, end_dt, set(exclude_ids or ())))
        return []


def test_strip_tag_tokens():
    mod = _load()
    assert mod._strip_tag_tokens("hcm #-1") == "hcm"
    assert mod._strip_tag_tokens("冥想 #xk26") == "冥想"
    assert mod._strip_tag_tokens("plain desc") == "plain desc"


def test_range_item_never_leaks_hash_tag_into_description(monkeypatch):
    """Domain-only + #tag: resolve() empties the desc, and the fallback must
    be the TAG-STRIPPED text — not raw "hcm #-1"."""
    mod = _load()
    fake = _FakeTogglApi()
    monkeypatch.setattr(mod, "_toggl_api", lambda: fake)
    created = []

    def fake_cli(*args):
        created.append(args)
        return "Created: x [id:111]"

    monkeypatch.setattr(mod, "_run_cli", fake_cli)
    out = mod._process_entry("1427-1652 hcm #-1")
    assert created, "range item must create an entry"
    args = created[0]
    assert args[0] == "create"
    assert "#" not in args[1], f"#tag leaked into the description: {args!r}"
    assert "--tag" in args and "-1" in args, "the tag must ride as a Toggl tag"
    assert "Created" in out


def test_batch_sibling_entries_are_excluded_from_trim(monkeypatch):
    """The id created by item 1 must be in item 2's trim exclude set."""
    mod = _load()
    mod._CREATED_IDS.clear()
    fake = _FakeTogglApi()
    monkeypatch.setattr(mod, "_toggl_api", lambda: fake)
    ids = iter([4501, 4502])
    monkeypatch.setattr(mod, "_run_cli",
                        lambda *a: f"Created: x [id:{next(ids)}]")
    mod._process_entry("1427-1447 冥想 #xk26")
    mod._process_entry("1427-1652 hcm #-1")
    assert len(fake.trim_calls) == 2
    first_excl, second_excl = fake.trim_calls[0][2], fake.trim_calls[1][2]
    assert 4501 not in first_excl, "nothing created yet at item 1's trim"
    assert 4501 in second_excl, (
        "item 2's trim must exclude the entry item 1 just created — "
        "deliberate batch overlaps must survive")


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
