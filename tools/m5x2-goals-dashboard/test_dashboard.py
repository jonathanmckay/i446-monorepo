import json

import pytest

import dashboard


@pytest.fixture
def client():
    return dashboard.app.test_client()


def test_current_quarter_renders_roadmap_columns(client):
    html = client.get("/").get_data(as_text=True)
    assert "Q4 m5x2 GOALS" in html
    for header in ("Baseline", "Target", "Due", "Source"):
        assert f"<th>{header}</th>" in html
    for section in ("Construction", "TRS", "r204"):
        assert section in html


def test_values_are_escaped(client):
    html = client.get("/").get_data(as_text=True)
    assert "&lt; 30" in html  # 'Backlog past turn target' target
    assert "<= 60" not in html


def test_archived_quarter_uses_legacy_columns(client):
    html = client.get("/?q=2026q2").get_data(as_text=True)
    assert "<th>Q1 Actual</th>" in html
    assert "Limit move-outs to 94" in html


def test_bad_or_missing_quarter_404(client):
    assert client.get("/?q=../goals").status_code == 404
    assert client.get("/?q=2019q1").status_code == 404
    assert client.get("/api/goals?q=x").status_code == 404


def test_api_matches_file(client):
    data = client.get("/api/goals").get_json()
    assert data == json.loads(dashboard.GOALS_PATH.read_text())
    for s in data["sections"]:
        for g in s["goals"]:
            for k in ("metric", "baseline", "target", "due", "source", "dri"):
                assert g.get(k), (s["name"], g["goal"], k)


def test_archived_title_and_ampersand_escaped(client):
    html = client.get("/?q=2026q2").get_data(as_text=True)
    assert "<h1>Q2 m5x2 GOALS</h1>" in html
    assert "R203 R&amp;M" in html
