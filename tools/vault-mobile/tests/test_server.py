import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

CONFIG = """---
title: t
include:
  - "*.md"
  - "g245/**"
  - "d359/**"
  - "z_ibx/new-notes.md"
exclude:
  - "i447/**"
  - "**/{todoist}/**"
  - "**/*.sync-conflict-*"
extensions: [".md"]
max_file_kb: 4
max_total_mb: 1
---
body
"""


@pytest.fixture
def vault(tmp_path, monkeypatch):
    v = tmp_path / "vault"
    (v / "z_meta").mkdir(parents=True)
    (v / "z_meta/phone-subset.md").write_text(CONFIG)
    (v / "g245").mkdir()
    (v / "g245/a.md").write_text("alpha")
    (v / "g245/{todoist}").mkdir()
    (v / "g245/{todoist}/done.md").write_text("no")
    (v / "g245/b.sync-conflict-20260101-x.md").write_text("no")
    (v / "g245/big.md").write_text("x" * 5000)
    (v / "g245/note.txt").write_text("no")
    (v / "d359/sub").mkdir(parents=True)
    (v / "d359/sub/c.md").write_text("gamma")
    (v / "i447").mkdir()
    (v / "i447/secret.md").write_text("no")
    (v / "root.md").write_text("root")
    (v / "z_ibx").mkdir()
    (v / "z_ibx/new-notes.md").write_text("inbox")
    (v / "z_ibx/other.md").write_text("no")
    (v / ".obsidian").mkdir()
    (v / ".obsidian/x.md").write_text("no")
    monkeypatch.setenv("VAULT_MOBILE_VAULT", str(v))
    monkeypatch.setenv("VAULT_MOBILE_TOKENS", str(tmp_path / "tokens.json"))
    monkeypatch.setenv("VAULT_MOBILE_CACHE", str(tmp_path / "cache.json"))
    for m in ("server", "manifest"):
        sys.modules.pop(m, None)
    import server  # noqa

    server.manifests.ttl = 0
    return v, server


@pytest.fixture
def client(vault):
    v, server = vault
    tok = server.mint_token("imago-c")
    c = server.app.test_client()
    c.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {tok}"
    return c


def test_subset_selection(vault):
    v, server = vault
    m, _ = server.manifests.get()
    paths = sorted(f["p"] for f in m["files"])
    assert paths == ["d359/sub/c.md", "g245/a.md", "root.md", "z_ibx/new-notes.md"]
    assert m["skipped_large"] == 1
    assert m["count"] == 4


def test_double_star_does_not_leak_across_dirs(vault):
    from manifest import _match

    assert _match("g245/**", "g245/x/y.md")
    assert _match("*.md", "root.md")
    assert not _match("*.md", "g245/a.md")
    assert _match("**/{todoist}/**", "g245/{todoist}/x.md")
    assert not _match("g245/*", "g245/x/y.md")
    assert _match("h335/m5x2/**/drive-*/**", "h335/m5x2/m5x2-m/drive-main/a.md")


def test_auth_required(vault):
    v, server = vault
    c = server.app.test_client()
    assert c.get("/manifest").status_code == 401
    assert c.get("/healthz").status_code == 200
    c.environ_base["HTTP_AUTHORIZATION"] = "Bearer nope"
    assert c.get("/manifest").status_code == 401


def test_manifest_etag(client):
    r = client.get("/manifest")
    assert r.status_code == 200
    etag = r.headers["ETag"]
    assert client.get("/manifest", headers={"If-None-Match": etag}).status_code == 304


def test_file_only_from_manifest(client, vault):
    v, _ = vault
    r = client.get("/file", query_string={"p": "g245/a.md"})
    assert r.status_code == 200 and r.data == b"alpha"
    assert r.headers["X-Sha256"] == r.headers["X-Manifest-Sha256"]
    assert client.get("/file", query_string={"p": "i447/secret.md"}).status_code == 404
    assert client.get("/file", query_string={"p": "../tokens.json"}).status_code == 404
    assert client.get("/file", query_string={"p": "z_ibx/other.md"}).status_code == 404


def test_file_mid_write_mismatch(client, vault):
    v, server = vault
    client.get("/manifest")
    server.manifests.ttl = 3600  # freeze the published manifest, then write behind it
    (v / "g245/a.md").write_text("alpha2")
    r = client.get("/file", query_string={"p": "g245/a.md"})
    assert r.data == b"alpha2"
    assert r.headers["X-Sha256"] != r.headers["X-Manifest-Sha256"]


def test_drop_creates_file_and_is_idempotent(client, vault):
    v, _ = vault
    payload = {"id": "0f9a1b2c-1111-2222-3333-444444444444", "created_at": "2026-10-19T07:05:09", "body": "buy milk\nand eggs"}
    r = client.post("/drop", json=payload)
    assert r.status_code == 201, r.data
    path = r.get_json()["path"]
    assert path == "z_ibx/phone/2026-10-19-070509-0f9a-imago-c.md"
    text = (v / path).read_text()
    assert 'title: "buy milk"' in text and "id: 0f9a1b2c-1111-2222-3333-444444444444" in text
    assert text.endswith("buy milk\nand eggs\n")
    r2 = client.post("/drop", json=payload)
    assert r2.status_code == 200 and r2.get_json()["existing"] is True
    assert len(list((v / "z_ibx/phone").glob("*.md"))) == 1
    assert client.get("/captures").get_json()["captures"] == ["2026-10-19-070509-0f9a-imago-c.md"]


def test_drop_validation(client):
    assert client.post("/drop", json={"id": "zz", "body": "x"}).status_code == 400
    assert client.post("/drop", json={"id": "abcd1234", "body": "   "}).status_code == 400


def test_subset_too_large(vault, client):
    v, server = vault
    (v / "z_meta/phone-subset.md").write_text(CONFIG.replace("max_total_mb: 1", "max_total_mb: 0").replace("max_file_kb: 4", "max_file_kb: 4000"))
    r = client.get("/manifest", query_string={"force": "1"})
    assert r.status_code == 507
    assert r.get_json()["error"] == "subset_too_large"
