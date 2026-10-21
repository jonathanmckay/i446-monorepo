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
    monkeypatch.setenv("VAULT_MOBILE_APPENDS", str(tmp_path / "appends.json"))
    monkeypatch.setenv("VAULT_MOBILE_VERSIONS", str(tmp_path / "versions"))
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


def test_drop_with_target_appends_to_new_notes(client, vault):
    """2026-10-20: the phone appends to new-notes in place (allow-listed),
    with a YYYY.MM.DD date line once per day, idempotent on id."""
    v, _ = vault
    p = v / "z_ibx/new-notes.md"
    p.write_text("inbox header\n\nold block\n")
    a = {"id": "aaaa1111-0000-0000-0000-000000000001", "created_at": "2026-10-20T09:10:11",
         "body": "call LR about w225", "target": "z_ibx/new-notes.md"}
    r = client.post("/drop", json=a)
    assert r.status_code == 201, r.data
    j = r.get_json()
    assert {k: j[k] for k in ("ok", "path", "existing", "appended")} == {"ok": True, "path": "z_ibx/new-notes.md", "existing": False, "appended": True}
    assert j["body"] == "inbox header\n\nold block\n\n2026.10.20\ncall LR about w225\n" and len(j["sha"]) == 64
    assert p.read_text() == "inbox header\n\nold block\n\n2026.10.20\ncall LR about w225\n"
    # same day → no second date line; same id → no duplicate
    b = dict(a, id="aaaa1111-0000-0000-0000-000000000002", body="second thought")
    assert client.post("/drop", json=b).status_code == 201
    assert client.post("/drop", json=a).status_code == 200
    assert p.read_text() == "inbox header\n\nold block\n\n2026.10.20\ncall LR about w225\n\nsecond thought\n"
    assert not list((v / "z_ibx/phone").glob("*.md")) if (v / "z_ibx/phone").exists() else True
    # anything else stays read-only
    r = client.post("/drop", json=dict(a, id="aaaa1111-0000-0000-0000-000000000003", target="g245/a.md"))
    assert r.status_code == 400 and (v / "g245/a.md").read_text() == "alpha"


def test_drop_put_replaces_new_notes_and_merges_a_moved_on_base(client, vault):
    """2026-10-20: the phone edits new-notes in full. Same base sha → plain
    replace. 2026-10-21: a moved-on base is three-way merged against the
    snapshot of the version the phone edited; the pre-merge server copy is
    archived; the response carries the final text so the phone adopts it."""
    import hashlib
    v, server = vault
    p = v / "z_ibx/new-notes.md"
    p.write_text("header\n\nold\n")
    base = hashlib.sha256(p.read_bytes()).hexdigest()
    # the phone saw this version through /file → the server remembers it
    assert client.get("/file", query_string={"p": "z_ibx/new-notes.md"}).status_code == 200
    a = {"id": "bbbb1111-0000-0000-0000-000000000001", "created_at": "2026-10-20T10:00:00",
         "body": "header\n\nold\n\nnew line\n", "target": "z_ibx/new-notes.md", "mode": "put", "base_sha": base}
    r = client.post("/drop", json=a)
    assert r.status_code == 201, r.data
    j = r.get_json()
    assert j["put"] is True and "conflict" not in j and "merged" not in j
    assert j["sha"] == hashlib.sha256(b"header\n\nold\n\nnew line\n").hexdigest() and j["body"] == a["body"]
    assert p.read_text() == "header\n\nold\n\nnew line\n"
    assert client.post("/drop", json=a).status_code == 200          # idempotent
    # manifest advertises the new sha at once (no 30 s TTL lag)
    m = client.get("/manifest").get_json()
    assert next(f for f in m["files"] if f["p"] == "z_ibx/new-notes.md")["h"] == j["sha"]

    # the desktop cleared the inbox (/notes) while the phone, still on `base`,
    # appended one more line: the result is the cleared header + that line
    p.write_text("header\n")
    b = dict(a, id="bbbb1111-0000-0000-0000-000000000002", body="header\n\nold\n\nnew line\nlater line\n",
             base_sha=j["sha"])   # the phone adopted its landed put as the new base
    r = client.post("/drop", json=b)
    assert r.status_code == 201, r.data
    j = r.get_json()
    assert j["merged"] is True and "conflicted" not in j
    assert p.read_text() == "header\nlater line\n"
    assert j["body"] == "header\nlater line\n"
    conflict = j["conflict"]
    assert conflict.startswith("z_ibx/archive/2026-10/new-notes.conflict-20261020-100000-imago-c")
    assert (v / conflict).read_text() == "header\n"
    assert client.post("/drop", json=dict(b, id="bbbb1111-0000-0000-0000-000000000003", target="g245/a.md")).status_code == 400


def test_drop_put_own_successor_is_a_plain_replace(client, vault):
    """Typing across a round trip: put N lands, put N+1 still carries the
    base of put N-1. The file equals this device's last put, so the phone's
    text is a strict successor: no merge, no archive copy."""
    import hashlib
    v, _ = vault
    p = v / "z_ibx/new-notes.md"
    p.write_text("h\n")
    base = hashlib.sha256(b"h\n").hexdigest()
    client.get("/file", query_string={"p": "z_ibx/new-notes.md"})
    a = {"id": "cccc1111-0000-0000-0000-000000000001", "created_at": "2026-10-21T10:00:00",
         "body": "h\nfirst\n", "target": "z_ibx/new-notes.md", "mode": "put", "base_sha": base}
    assert client.post("/drop", json=a).status_code == 201
    r = client.post("/drop", json=dict(a, id="cccc1111-0000-0000-0000-000000000002", body="h\nfirst wor\n"))
    j = r.get_json()
    assert r.status_code == 201 and "merged" not in j and "conflict" not in j
    assert p.read_text() == "h\nfirst wor\n"
    assert not list((v / "z_ibx/archive").rglob("*.conflict-*")) if (v / "z_ibx/archive").exists() else True


def test_drop_put_unknown_base_keeps_phone_text_and_archives_server_copy(client, vault):
    """A base this server never served or wrote cannot be merged: the phone's
    text wins (as before 2026-10-21) and the server copy is archived."""
    v, _ = vault
    p = v / "z_ibx/new-notes.md"
    p.write_text("desktop\n")
    a = {"id": "dddd1111-0000-0000-0000-000000000001", "created_at": "2026-10-21T10:00:00",
         "body": "phone\n", "target": "z_ibx/new-notes.md", "mode": "put", "base_sha": "0" * 64}
    r = client.post("/drop", json=a)
    j = r.get_json()
    assert r.status_code == 201 and "merged" not in j and j["conflict"]
    assert p.read_text() == "phone\n" and (v / j["conflict"]).read_text() == "desktop\n"


def test_drop_put_real_conflict_keeps_both_with_markers(client, vault):
    import hashlib
    v, _ = vault
    p = v / "z_ibx/new-notes.md"
    p.write_text("h\nline\n")
    base = hashlib.sha256(b"h\nline\n").hexdigest()
    client.get("/file", query_string={"p": "z_ibx/new-notes.md"})
    p.write_text("h\nline desktop\n")
    a = {"id": "eeee1111-0000-0000-0000-000000000001", "created_at": "2026-10-21T10:00:00",
         "body": "h\nline phone\n", "target": "z_ibx/new-notes.md", "mode": "put", "base_sha": base}
    j = client.post("/drop", json=a).get_json()
    assert j["merged"] and j["conflicted"]
    assert p.read_text() == "h\n<<<<<<< ix\nline desktop\n=======\nline phone\n>>>>>>> imago-c\n"


def test_old_list_ledger_still_read(client, vault):
    v, server = vault
    server.APPEND_LEDGER.write_text(json.dumps(["ffff1111-0000-0000-0000-000000000001"]))
    a = {"id": "ffff1111-0000-0000-0000-000000000001", "created_at": "2026-10-21T10:00:00",
         "body": "x", "target": "z_ibx/new-notes.md"}
    assert client.post("/drop", json=a).status_code == 200   # already applied
