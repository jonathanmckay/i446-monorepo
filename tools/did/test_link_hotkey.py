"""link_hotkey (2026-10-28): ⌃⌥L opens the focused cmux pane's links in the
right Chrome profile or Obsidian."""
import json

import pytest

import link_hotkey as L

P1, P5, P11 = "Profile 1", "Profile 5", "Profile 11"


def test_focused_surface_follows_key_window_not_callers_active():
    tree = {"active": {"surface_ref": "surface:9"}, "windows": [
        {"key": False, "workspaces": [{"active": True, "panes": [
            {"focused": True, "selected_surface_ref": "surface:1", "surfaces": [{"ref": "surface:1"}]}]}]},
        {"key": True, "workspaces": [{"active": True, "panes": [
            {"focused": False, "selected_surface_ref": "surface:2", "surfaces": [{"ref": "surface:2"}]},
            {"focused": True, "selected_surface_ref": "surface:4",
             "surfaces": [{"ref": "surface:3"}, {"ref": "surface:4", "tty": "ttys004"}]}]}]}]}
    assert L.focused_surface(tree)["ref"] == "surface:4"


@pytest.mark.parametrize("url,prof", [
    ("https://microsoft.sharepoint.com/teams/x/doc.docx", P1),
    ("https://teams.microsoft.com/l/message/1", P1),
    ("https://dev.azure.com/org/proj", P1),
    ("https://github.com/gaming-microsoft/repo/pull/3", P1),
    ("https://mckay.appfolio.com/reports", P11),
    ("https://github.com/jonathanmckay/i446/pull/9", None),   # personal repo
    ("https://example.com/notmicrosoft.com", None),           # host, not substring
    ("https://evilmicrosoft.com/", None),
])
def test_url_profile(url, prof):
    got = L.url_profile(url)
    assert (got[0] if got else None) == prof


def test_url_rule_beats_context_and_context_beats_default(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "VAULT", tmp_path)
    text = "see https://teams.microsoft.com/l/x and https://news.ycombinator.com/item"
    m5 = ("Profile 11", "m5c7.com")
    links = L.extract(text, "", m5)
    assert [(l["url"].split("/")[2], l["profile"]) for l in links] == [
        ("teams.microsoft.com", P1), ("news.ycombinator.com", P11)]
    assert L.extract("https://news.ycombinator.com/item", "", None)[0]["profile"] == P5


def test_extract_dedupes_and_skips_truncated():
    links = L.extract("a https://x.com/a. b https://x.com/a c https://x.com/lon…", "", None)
    assert [l["url"] for l in links] == ["https://x.com/a"]


def test_vault_paths_relative_absolute_and_outside(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / "xk87").mkdir(parents=True)
    note = vault / "xk87" / "plan.md"
    note.write_text("x")
    outside = tmp_path / "other.md"
    outside.write_text("x")
    monkeypatch.setattr(L, "VAULT", vault.resolve())
    text = f"wrote xk87/plan.md:12 and {outside} and {note}"
    links = L.extract(text, str(vault), None)
    assert links == [{"kind": "obsidian", "path": str(note.resolve())}]


def test_obsidian_uri_only_open_with_vault_path(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "a.md").write_text("x")
    monkeypatch.setattr(L, "VAULT", vault.resolve())
    p = str((vault / "a.md").resolve()).replace("/", "%2F")
    text = f"obsidian://advanced-uri?commandid=x obsidian://open?path={p}"
    assert L.extract(text, "", None) == [{"kind": "obsidian", "path": str((vault / 'a.md').resolve())}]


def test_unwrap_screen_rejoins_wrapped_url_newest_first():
    screen = "old\n" + "go https://a.com/abcd\n" + "   efgh end\n" + "newest\n"
    out = L.unwrap_screen(screen)       # widest line (21) is the wrap width
    assert out.split("\n")[0] == "newest"
    assert L.urls_in(out) == ["https://a.com/abcdefgh"]


def test_is_dtd_ignores_background_greps():
    s = {"title": "3 · some claude session"}
    assert not L.is_dtd(s, [(1, "claude --session-id x")])
    assert L.is_dtd(s, [(2, "zsh -c print $$ > /tmp/x.pid; source ~/i446-monorepo/tools/did/dtd.sh")])
    assert L.is_dtd({"title": "1 · dtd"}, [(3, "ssh ix")])


def test_claim_profile_uses_latest_claim_label(tmp_path, monkeypatch):
    claims = tmp_path / "by-session"
    claims.mkdir()
    (claims / "S1").write_text("T1\nT2\n")
    q = tmp_path / "q.json"
    q.write_text(json.dumps({"updated": "x", "today": [
        {"id": "T1", "labels": ["m5x2"]}, {"id": "T2", "labels": ["i9"]}]}))
    monkeypatch.setattr(L, "CLAIMS_DIR", claims)
    monkeypatch.setattr(L, "TASK_QUEUE", q)
    assert L.claim_profile("S1")[0] == P1
    assert L.claim_profile("nobody") is None


def test_cwd_profile():
    assert L.cwd_profile("/Users/mckay/code/m5x2-occupancy")[0] == P11
    assert L.cwd_profile("/Users/mckay") is None


def test_transcript_text_newest_first(tmp_path, monkeypatch):
    proj = tmp_path / "projects" / "-Users-mckay"
    proj.mkdir(parents=True)
    rows = [{"type": "assistant", "message": {"content": [{"type": "text", "text": "old https://a.com"}]}},
            {"type": "user", "message": {"content": "hi https://user.com"}},
            {"type": "assistant", "message": {"content": [{"type": "tool_use"}, {"type": "text", "text": "new https://b.com"}]}}]
    (proj / "SID.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    monkeypatch.setattr(L, "PROJECTS_DIR", tmp_path / "projects")
    t = L.transcript_text("SID")
    assert L.urls_in(t) == ["https://b.com", "https://a.com"]
