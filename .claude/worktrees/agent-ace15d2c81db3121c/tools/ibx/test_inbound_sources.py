"""Tests for inbound_sources cross-host gating helper."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import inbound_sources  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Strip the IBX_* env vars so tests start from a known state."""
    for k in ("IBX_FORCE_SOURCES", "IBX_DISABLE_SOURCES"):
        monkeypatch.delenv(k, raising=False)
    yield


def _patch_host(monkeypatch, name: str):
    monkeypatch.setattr(inbound_sources.socket, "gethostname", lambda: name)


def _patch_agency(monkeypatch, present: bool):
    monkeypatch.setattr(inbound_sources, "_agency_binary_present", lambda: present)


# ── Hostname matrix ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("host", [
    "Straylight-Refit.local",
    "straylight",
    "STRAYLIGHT.local",
    "straylight-refit",
])
def test_straylight_with_creds_enables_all(monkeypatch, host):
    _patch_host(monkeypatch, host)
    _patch_agency(monkeypatch, True)
    assert inbound_sources.available_sources() == list(inbound_sources.ALL_SOURCES)
    assert inbound_sources.skipped_sources() == []


@pytest.mark.parametrize("host", [
    "Mac-mini-of-mckay.local",
    "ix",
    "ix.local",
    "ix-server",
    "Some-Mac-mini-2026",
])
def test_ix_excludes_outlook_and_teams(monkeypatch, host):
    _patch_host(monkeypatch, host)
    # Even if Agency is hypothetically there, host rule still wins for ix.
    _patch_agency(monkeypatch, True)
    avail = inbound_sources.available_sources()
    assert "outlook" not in avail
    assert "teams" not in avail
    assert {"email", "imsg", "slack"}.issubset(set(avail))
    skipped = dict(inbound_sources.skipped_sources())
    assert "outlook" in skipped and host in skipped["outlook"]
    assert "teams" in skipped and host in skipped["teams"]


def test_unknown_host_acts_like_straylight(monkeypatch):
    _patch_host(monkeypatch, "some-other-mac.local")
    _patch_agency(monkeypatch, True)
    assert "outlook" in inbound_sources.available_sources()
    assert "teams" in inbound_sources.available_sources()


# ── Cred-presence ────────────────────────────────────────────────────────────


def test_missing_agency_disables_work_sources_on_straylight(monkeypatch):
    _patch_host(monkeypatch, "Straylight-Refit.local")
    _patch_agency(monkeypatch, False)
    avail = inbound_sources.available_sources()
    assert "outlook" not in avail
    assert "teams" not in avail
    skipped = dict(inbound_sources.skipped_sources())
    assert "Agency" in skipped["outlook"]
    assert "Agency" in skipped["teams"]


def test_missing_agency_does_not_affect_consumer_sources(monkeypatch):
    _patch_host(monkeypatch, "Mac-mini.local")
    _patch_agency(monkeypatch, False)
    avail = set(inbound_sources.available_sources())
    assert {"email", "imsg", "slack"}.issubset(avail)


# ── Env overrides ────────────────────────────────────────────────────────────


def test_force_sources_overrides_hostname(monkeypatch):
    _patch_host(monkeypatch, "Mac-mini.local")
    _patch_agency(monkeypatch, True)
    monkeypatch.setenv("IBX_FORCE_SOURCES", "outlook,teams")
    avail = inbound_sources.available_sources()
    assert avail == ["outlook", "teams"]


def test_force_sources_still_respects_missing_creds(monkeypatch):
    _patch_host(monkeypatch, "Straylight.local")
    _patch_agency(monkeypatch, False)
    monkeypatch.setenv("IBX_FORCE_SOURCES", "outlook,email")
    avail = inbound_sources.available_sources()
    # outlook forced but creds missing -> excluded; email survives.
    assert avail == ["email"]
    skipped = dict(inbound_sources.skipped_sources())
    assert "outlook" in skipped and "creds missing" in skipped["outlook"]


def test_force_sources_empty_means_none(monkeypatch):
    _patch_host(monkeypatch, "Straylight.local")
    _patch_agency(monkeypatch, True)
    monkeypatch.setenv("IBX_FORCE_SOURCES", "")
    assert inbound_sources.available_sources() == []


def test_disable_sources_subtracts(monkeypatch):
    _patch_host(monkeypatch, "Straylight.local")
    _patch_agency(monkeypatch, True)
    monkeypatch.setenv("IBX_DISABLE_SOURCES", "outlook")
    avail = inbound_sources.available_sources()
    assert "outlook" not in avail
    assert "teams" in avail


def test_disable_sources_accepts_whitespace_and_semicolons(monkeypatch):
    _patch_host(monkeypatch, "Straylight.local")
    _patch_agency(monkeypatch, True)
    monkeypatch.setenv("IBX_DISABLE_SOURCES", " outlook ; teams ")
    avail = inbound_sources.available_sources()
    assert "outlook" not in avail and "teams" not in avail


# ── API surface ──────────────────────────────────────────────────────────────


def test_skip_reason_unknown_source():
    assert "unknown" in (inbound_sources.skip_reason("invalid_source") or "")


def test_is_available_matches_skip_reason(monkeypatch):
    _patch_host(monkeypatch, "Mac-mini.local")
    _patch_agency(monkeypatch, True)
    for s in inbound_sources.ALL_SOURCES:
        assert inbound_sources.is_available(s) == (
            inbound_sources.skip_reason(s) is None
        )


def test_available_sources_preserves_canonical_order(monkeypatch):
    _patch_host(monkeypatch, "Straylight.local")
    _patch_agency(monkeypatch, True)
    out = inbound_sources.available_sources(["teams", "email", "outlook"])
    # Order follows ALL_SOURCES regardless of input order.
    assert out == ["email", "outlook", "teams"]


# ── ibx0 wiring ──────────────────────────────────────────────────────────────


def test_ibx0_uses_inbound_sources_helpers():
    """Regression: ibx0 must consult inbound_sources at fetch time, not just
    at import time. Otherwise IBX_DISABLE_SOURCES set after import (e.g. by
    a wrapper) is silently ignored."""
    src = (HERE / "ibx0.py").read_text()
    assert "import inbound_sources" in src
    assert "_outlook_active" in src and "_teams_active" in src
    # Fetch-site guards must call the helpers, not the stale availability
    # flags.
    assert "if not _outlook_active()" in src
    assert "if not _teams_active()" in src


def test_ibx0_main_skips_disabled_sources_without_attempting_fetch(monkeypatch):
    """When a source is disabled, ibx0.fetch_<source>() must short-circuit
    to [] instead of attempting any I/O."""
    import ibx0

    monkeypatch.setenv("IBX_FORCE_SOURCES", "email")
    # If the helpers route through inbound_sources correctly, both work
    # sources should report unavailable.
    assert ibx0.fetch_outlook() == []
    assert ibx0.fetch_teams() == []
