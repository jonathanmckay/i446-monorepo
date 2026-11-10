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


def test_p1_rows_render_before_p2(monkeypatch, tmp_path):
    import json
    data = {"title": "T", "quarter": "Q", "sections": [{"name": "S", "goals": [
        {"p": "P2", "dri": "a", "goal": "second", "target": "x"},
        {"p": "P1", "dri": "b", "goal": "first", "target": "y"},
        {"p": "P2", "dri": "c", "goal": "third", "target": "z"},
    ]}]}
    f = tmp_path / "goals.json"; f.write_text(json.dumps(data))
    monkeypatch.setattr(dashboard, "GOALS_PATH", f)
    html = dashboard.render()
    assert html.index("first") < html.index("second") < html.index("third")


def test_now_column_from_status_json(monkeypatch, tmp_path):
    import json
    goals = {"title": "T", "quarter": "Q", "sections": [{"name": "S", "goals": [
        {"p": "P1", "dri": "b", "goal": "first", "target": "y"},
        {"p": "P2", "dri": "a", "goal": "second", "target": "x"},
    ]}]}
    (tmp_path / "goals.json").write_text(json.dumps(goals))
    (tmp_path / "status.json").write_text(json.dumps({"as_of": "2026-11-10T13:05-08:00", "rows": [
        {"section": "S", "goal": "first", "status": "82 not-ready", "note": "c702 18"},
        {"section": "S", "goal": "second", "status": "manual", "note": "LeeRoy"},
    ]}))
    monkeypatch.setattr(dashboard, "GOALS_PATH", tmp_path / "goals.json")
    monkeypatch.setattr(dashboard, "STATUS_PATH", tmp_path / "status.json")
    html = dashboard.render()
    assert "<th" in html and "Now" in html
    assert "82 not-ready" in html and "c702 18" in html
    assert 'class="now manual"' in html
    assert "now as of 2026-11-10 13:05" in html
    # no status.json → column renders with a dash, no crash
    monkeypatch.setattr(dashboard, "STATUS_PATH", tmp_path / "missing.json")
    assert "—" in dashboard.render()
