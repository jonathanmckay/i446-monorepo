"""Regression test for tg-tui project-code resolution.

Bug: a duplicate Toggl project named 'xk87' (id 220114400, created via the
mobile picker) wasn't in the static PROJECT_MAP, so 一起饭 entries rendered
uncolored (white) in tg-tui. proj_code must fall back to resolving unknown
project ids by NAME via the Toggl API.
"""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load_tui():
    spec = importlib.util.spec_from_file_location("tg_tui_t", HERE / "tg-tui.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tg_tui_t"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_proj_code_resolves_unknown_id_by_project_name(monkeypatch):
    m = _load_tui()
    # Fresh fallback state; fake a duplicate 'xk87' project under a new id
    monkeypatch.setattr(m, "_PROJECTS_FETCHED", False)
    m.PROJECT_CODE.pop(999001, None)
    monkeypatch.setattr(
        m.toggl_api, "get_projects",
        lambda: [{"id": 999001, "name": "xk87", "active": True}],
    )
    assert m.proj_code(999001) == "xk87", \
        "duplicate project named like a known code must resolve to that code"
    # Cached for subsequent calls without refetching
    assert m.PROJECT_CODE.get(999001) == "xk87"


def test_proj_code_unknown_name_stays_blank(monkeypatch):
    m = _load_tui()
    monkeypatch.setattr(m, "_PROJECTS_FETCHED", False)
    m.PROJECT_CODE.pop(999002, None)
    monkeypatch.setattr(
        m.toggl_api, "get_projects",
        lambda: [{"id": 999002, "name": "some-random-project", "active": True}],
    )
    assert m.proj_code(999002) == "", \
        "projects not named like a known code must not be force-mapped"


def test_proj_code_api_failure_is_safe(monkeypatch):
    m = _load_tui()
    monkeypatch.setattr(m, "_PROJECTS_FETCHED", False)

    def _boom():
        raise RuntimeError("offline")

    monkeypatch.setattr(m.toggl_api, "get_projects", _boom)
    assert m.proj_code(999003) == ""  # no crash, no color
