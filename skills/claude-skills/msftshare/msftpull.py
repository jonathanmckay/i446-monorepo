#!/usr/bin/env python3
"""msftpull.py — weekly pull + edit report for /msftshare shadows.

For every .docx under OneDrive vault-shared/:
  1. Extract its text (pandoc docx → gfm).
  2. Diff against the last pull's snapshot (word-level) → coworker edit volume.
  3. Flipped docs (vault file is a stub): refresh the OneDrive .md sidecar from
     the current .docx so the vault-adjacent markdown mirror stays current for
     AI access. The flip-time original is preserved once as <name>.orig.md.
  4. Vault-truth docs (default /msftshare mode): additionally diff the .docx
     text against a fresh render of the vault source — divergence here means
     coworker edits exist that a re-run of /msftshare would CLOBBER.
  5. Prepend a dated section to the rolling vault report and update state.

State: ~/.local/state/jm/msftpull/  (state.json + texts/<slug>.md snapshots)
Report: ~/vault/i447/msftshare-pull-report.md  (newest section first)

Scheduled weekly via launchd (com.jm.msftpull) on Straylight — the only
machine with OneDrive-Microsoft synced. Safe to run manually any time.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ONEDRIVE = Path.home() / "Library/CloudStorage/OneDrive-Microsoft"
SHARED_ROOT = ONEDRIVE / "vault-shared"
VAULT = Path.home() / "vault"
STATE_DIR = Path.home() / ".local/state/jm/msftpull"
TEXTS_DIR = STATE_DIR / "texts"
REPORT = VAULT / "i447" / "msftshare-pull-report.md"
PANDOC = "/opt/homebrew/bin/pandoc"

# Word-diff noise floor: pandoc round-trips wobble slightly (quotes, breaks).
NOISE_WORDS = 5


def die(msg: str, code: int = 1):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def slug(rel: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", rel)


def docx_to_md(docx: Path) -> str:
    r = subprocess.run([PANDOC, str(docx), "-t", "gfm", "--wrap=none"],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"pandoc failed on {docx.name}: {r.stderr.strip()[:200]}")
    return r.stdout


def md_via_docx(md: Path) -> str:
    """Render a vault md the same way its shadow was made (md→docx→gfm), so a
    comparison against the live .docx text is apples-to-apples."""
    with tempfile.NamedTemporaryFile(suffix=".docx", delete=True) as tf:
        r = subprocess.run([PANDOC, str(md), "-o", tf.name],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(f"pandoc md→docx failed: {r.stderr.strip()[:200]}")
        return docx_to_md(Path(tf.name))


def words(text: str) -> list[str]:
    return re.findall(r"\S+", text)


def word_diff(old: str, new: str) -> tuple[int, int]:
    """(words_added, words_removed) via SequenceMatcher opcodes."""
    a, b = words(old), words(new)
    added = removed = 0
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():
        if op in ("replace", "delete"):
            removed += i2 - i1
        if op in ("replace", "insert"):
            added += j2 - j1
    return added, removed


def vault_source_for(shadow_rel: str) -> Path | None:
    """Find the vault md whose frontmatter references this shadow (msft_shadow
    or msft_doc), excluding junk trees."""
    try:
        r = subprocess.run(
            ["rg", "-l", "--glob", "!.stversions", "--glob", "!z_old",
             "--glob", "!ai-transcripts", "--glob", "!dream-runs",
             "-F", shadow_rel, str(VAULT)],
            capture_output=True, text=True, timeout=60)
    except Exception:
        return None
    cands = [Path(p) for p in r.stdout.splitlines()
             if p.endswith(".md") and "msftshare" not in p]
    for c in cands:
        try:
            head = c.read_text(encoding="utf-8", errors="ignore")[:2000]
        except OSError:
            continue
        if re.search(rf"^(msft_shadow|msft_doc):\s*\"?{re.escape(shadow_rel)}\"?",
                     head, re.MULTILINE):
            return c
    return None


def is_stub(vault_md: Path | None) -> bool:
    if not vault_md:
        return False
    try:
        head = vault_md.read_text(encoding="utf-8", errors="ignore")[:1000]
    except OSError:
        return False
    return "source_of_truth:" in head


def main() -> int:
    if not SHARED_ROOT.is_dir():
        die("OneDrive vault-shared not synced here — msftpull only runs on Straylight")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    TEXTS_DIR.mkdir(parents=True, exist_ok=True)
    state_file = STATE_DIR / "state.json"
    state = json.loads(state_file.read_text()) if state_file.is_file() else {}

    now = datetime.now()
    rows = []          # report table rows
    total_edits = 0
    flagged = []

    docx_files = sorted(SHARED_ROOT.rglob("*.docx"))
    if not docx_files:
        # Almost certainly macOS TCC blocking CloudStorage for this process
        # (launchd-spawned python3 lacks the terminal's Files-and-Folders
        # grant) — NOT an empty share tree. Refuse to write a junk report.
        die("no .docx visible under vault-shared — TCC/permissions? "
            "(launchd python3 needs Full Disk Access, or run from a terminal)")

    for docx in docx_files:
        if docx.name.startswith("~$"):     # Word lock files
            continue
        rel = str(docx.relative_to(ONEDRIVE))
        s = slug(rel)
        try:
            cur = docx_to_md(docx)
        except RuntimeError as e:
            rows.append((rel, "⚠ pandoc failed", str(e)))
            continue
        cur_hash = hashlib.sha256(cur.encode()).hexdigest()

        snap_file = TEXTS_DIR / (s + ".md")
        prev = snap_file.read_text(encoding="utf-8") if snap_file.is_file() else None
        entry = state.get(rel, {})

        vault_md = vault_source_for(rel)
        stubbed = is_stub(vault_md)
        mode = "OneDrive-truth (flipped)" if stubbed else "vault-truth"

        # -- edit volume since last pull --
        if prev is None:
            note = "baseline established"
            add = rm = 0
        elif entry.get("hash") == cur_hash:
            note = "no change"
            add = rm = 0
        else:
            add, rm = word_diff(prev, cur)
            note = f"edited since last pull: +{add}/−{rm} words"
            total_edits += add + rm

        # -- vault-truth docs: divergence from vault source = clobber risk --
        # Attribution: docx changed since last pull → coworker edits; vault
        # source changed since last pull → stale shadow. First run is ambiguous.
        clobber = ""
        vault_hash = None
        if not stubbed and vault_md and vault_md.is_file():
            vault_hash = hashlib.sha256(
                vault_md.read_bytes()).hexdigest()
            try:
                vault_render = md_via_docx(vault_md)
                va, vr = word_diff(vault_render, cur)
                if va + vr > NOISE_WORDS:
                    docx_moved = prev is not None and entry.get("hash") != cur_hash
                    vault_moved = (entry.get("vault_hash") is not None
                                   and entry.get("vault_hash") != vault_hash)
                    if docx_moved and not vault_moved:
                        who = "coworker edits — do NOT re-run /msftshare (re-export clobbers them)"
                    elif vault_moved and not docx_moved:
                        who = "stale shadow (vault edited since export) — re-run /msftshare to refresh"
                    else:
                        who = ("coworker edits or stale shadow (ambiguous) — "
                               "diff before re-running /msftshare")
                    clobber = (f"⚠ shadow and vault source differ by "
                               f"+{va}/−{vr} words: {who}")
                    flagged.append(rel)
            except RuntimeError:
                pass

        # -- flipped docs: refresh the md sidecar for AI access --
        if stubbed:
            sidecar = docx.with_suffix(".md")
            orig = docx.with_suffix(".orig.md")
            if sidecar.is_file() and not orig.is_file():
                orig.write_text(sidecar.read_text(encoding="utf-8"),
                                encoding="utf-8")
            sidecar.write_text(cur, encoding="utf-8")
            note += " · sidecar refreshed"

        snap_file.write_text(cur, encoding="utf-8")
        state[rel] = {"hash": cur_hash, "words": len(words(cur)),
                      "last_pull": now.isoformat(timespec="seconds"),
                      "mode": mode,
                      "vault_source": str(vault_md) if vault_md else None,
                      "vault_hash": vault_hash}
        rows.append((rel, mode, note + (("\n  " + clobber) if clobber else "")))

    state_file.write_text(json.dumps(state, indent=2))

    # ---- rolling vault report (newest section first) ----
    stamp = now.strftime("%Y.%m.%d")
    lines = [f"## {stamp}", ""]
    lines.append(f"- Docs scanned: {len(rows)} · coworker edit volume since last "
                 f"pull: **{total_edits} words** · clobber-risk flags: {len(flagged)}")
    lines.append("")
    lines.append("| Shadow | Mode | Status |")
    lines.append("|---|---|---|")
    for rel, mode, note in rows:
        lines.append(f"| `{rel}` | {mode} | {note.replace(chr(10), '<br>')} |")
    lines.append("")
    section = "\n".join(lines)

    if REPORT.is_file():
        text = REPORT.read_text(encoding="utf-8")
        m = re.match(r"^---\n.*?\n---\n", text, re.DOTALL)
        if m:
            fm_end = m.end()
            fm = re.sub(r"^updated:.*$", f"updated: {now:%Y-%m-%d}",
                        text[:fm_end], flags=re.MULTILINE)
            # keep the intro (everything up to the first ## section)
            body = text[fm_end:]
            first = body.find("\n## ")
            intro, rest = (body, "") if first < 0 else (body[:first + 1], body[first + 1:])
            REPORT.write_text(fm + intro + section + "\n" + rest, encoding="utf-8")
        else:
            REPORT.write_text(section + "\n" + text, encoding="utf-8")
    else:
        REPORT.write_text(
            "---\n"
            f"title: \"MSFT Share — Weekly Pull Report\"\n"
            f"date: {now:%Y-%m-%d}\n"
            "type: log\n"
            "tags: [i447, msftshare]\n"
            "status: active\n"
            f"updated: {now:%Y-%m-%d}\n"
            "---\n\n"
            "Weekly `msftpull.py` runs: coworker edit volume on OneDrive-shared "
            "Word docs, sidecar refreshes for flipped docs, and clobber-risk "
            "flags for vault-truth shadows. Newest first.\n\n"
            + section + "\n",
            encoding="utf-8")

    print(f"msftpull: {len(rows)} docs · {total_edits} words of coworker edits · "
          f"{len(flagged)} clobber flags · report → {REPORT.relative_to(VAULT)}")
    for rel in flagged:
        print(f"  ⚠ clobber risk: {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
