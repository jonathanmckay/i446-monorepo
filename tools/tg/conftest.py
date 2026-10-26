"""Keep tg-fast's project inference (toggl.sqlite history + a Haiku call) out
of every test in this directory; test_project_infer.py re-enables it with
mocks."""
import pytest


@pytest.fixture(autouse=True)
def _no_project_inference(monkeypatch):
    monkeypatch.setenv("TG_NO_INFER", "1")
