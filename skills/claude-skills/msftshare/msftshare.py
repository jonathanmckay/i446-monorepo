#!/usr/bin/env python3
"""msftshare — shadow a vault markdown doc into Work OneDrive as a shareable
Word doc, and optionally flip the source of truth to OneDrive (vault becomes a
pointer).

Usage:
    msftshare.py "<doc>"          # default: create/refresh a .docx shadow
    msftshare.py "<doc>" msft     # flip source of truth to OneDrive + stub vault

<doc> is a vault path (absolute, vault-relative, or with/without .md) or an
unambiguous filename. Ambiguous names are refused with a candidate list — a
wrong match in msft mode would destroy a file, so we never guess.

Layout (mirrors the vault folder path under a vault-shared/ root):
    ~/vault/<dir>/<name>.md
      → ~/Library/CloudStorage/OneDrive-Microsoft/vault-shared/<dir>/<name>.docx
      → ...                                       /vault-shared/<dir>/<name>.md   (sidecar)

Design guards (see the rubber-duck critique that shaped this):
  - msft re-run on an already-stubbed vault file regenerates the .docx from the
    OneDrive .md sidecar — never from the stub. Errors if the sidecar is gone.
  - sidecar is written AND size-verified before the vault file is truncated.
  - pandoc exit + non-zero output size checked before anything is recorded.
  - frontmatter is edited at text level (idempotent), preserving existing keys.
  - machine guard: only runs where OneDrive-Microsoft is synced (Straylight).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

VAULT = Path(os.environ.get("MSFTSHARE_VAULT", str(Path.home() / "vault")))
ONEDRIVE = Path(os.environ.get(
    "MSFTSHARE_ONEDRIVE",
    str(Path.home() / "Library/CloudStorage/OneDrive-Microsoft")))
SHARED_ROOT = ONEDRIVE / "vault-shared"
STUB_MARKER = "msft-onedrive"
# dirs not worth walking when resolving a bare name
PRUNE = {".git", ".stversions", ".obsidian", ".trash", "node_modules",
         "i446-monorepo", "drive-main", "drive-fundraising-legal",
         "drive-hr", "drive-investor-k1s"}


def die(msg: str, code: int = 1):
    print(f"✗ {msg}", file=sys.stderr)
    sys.exit(code)


# --- frontmatter (text-level, no YAML dep) --------------------------------

def split_frontmatter(text: str):
    """Return (fm_lines, body, had_fm). fm_lines are the lines between the
    opening and closing '---' fences (exclusive)."""
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            fm = text[4:end].split("\n")
            body = text[end + 5:]
            return fm, body, True
    return [], text, False


def fm_get(fm_lines, key):
    pat = re.compile(rf"^{re.escape(key)}\s*:\s*(.*)$")
    for ln in fm_lines:
        m = pat.match(ln)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return None


def fm_set(fm_lines, key, value):
    """Idempotently set key: \"value\" in fm_lines (list mutated, returned)."""
    line = f'{key}: "{value}"'
    pat = re.compile(rf"^{re.escape(key)}\s*:")
    for i, ln in enumerate(fm_lines):
        if pat.match(ln):
            fm_lines[i] = line
            return fm_lines
    fm_lines.append(line)
    return fm_lines


def assemble(fm_lines, body):
    return "---\n" + "\n".join(fm_lines) + "\n---\n" + body


# --- resolution -----------------------------------------------------------

def _norm(s: str) -> str:
    """Lowercase, collapse separators (space/hyphen/underscore) to single space."""
    return re.sub(r"[-_\s]+", " ", s.strip().lower())


def resolve_doc(arg: str) -> Path:
    # explicit path forms first
    cands = []
    p = Path(arg).expanduser()
    for c in (p, VAULT / arg, Path(arg + ".md").expanduser(), VAULT / (arg + ".md")):
        if c.is_file() and c.suffix == ".md":
            cands.append(c.resolve())
    for c in cands:
        try:
            c.relative_to(VAULT.resolve())
            return c
        except ValueError:
            die(f"{c} is not inside the vault ({VAULT})")
    # bare-name search — never auto-pick. Tiers, narrowest first; a tier with
    # exactly one hit wins, >1 lists candidates and stops, 0 falls through.
    # Separators (space/hyphen/underscore) are normalized so a loose reference
    # like "calendar preferences" matches "calendar-rules-and-preferences".
    target = arg[:-3] if arg.endswith(".md") else arg
    nq = _norm(target)
    nq_tokens = set(nq.split())
    # exact = case-insensitive stem OR normalized-exact (unambiguous intent).
    # fuzzy = normalized-substring OR all-query-tokens-present, POOLED so that
    # ambiguity across match types is surfaced — never tier-priority auto-pick
    # (e.g. "calendar preferences" must list both the 2023 doc and the current
    # one, not silently grab whichever a narrower tier hits first).
    exact, fuzzy = [], []
    for root, dirs, files in os.walk(VAULT):
        dirs[:] = [d for d in dirs if d not in PRUNE and not d.startswith(".")]
        for f in files:
            if not f.endswith(".md"):
                continue
            stem = f[:-3]
            ns = _norm(stem)
            p = Path(root) / f
            if stem.lower() == target.lower() or ns == nq:
                exact.append(p)
            elif nq in ns or (nq_tokens and nq_tokens <= set(ns.split())):
                fuzzy.append(p)
    for tier in (exact, fuzzy):
        if len(tier) == 1:
            return tier[0].resolve()
        if len(tier) > 1:
            _list_and_die(arg, tier)
    die(f"no vault markdown doc matches '{arg}'")


def _list_and_die(arg, matches):
    rels = sorted(str(m.resolve().relative_to(VAULT.resolve())) for m in matches)
    msg = [f"'{arg}' is ambiguous ({len(rels)} matches) — pass a vault-relative path:"]
    msg += [f"  {r}" for r in rels[:25]]
    if len(rels) > 25:
        msg.append(f"  … and {len(rels) - 25} more")
    die("\n".join(msg))


# --- fidelity scan --------------------------------------------------------

def fidelity_warnings(md_text: str):
    w = []
    if "![[" in md_text:
        w.append("Obsidian embeds (![[...]]) are DROPPED by pandoc")
    if re.search(r"(?<!!)\[\[", md_text):
        w.append("wikilinks ([[...]]) render as dead literal text in the .docx")
    for m in re.finditer(r"!\[[^\]]*\]\(([^)]+)\)", md_text):
        u = m.group(1).strip()
        if not (u.startswith("http") or u.startswith("/")):
            w.append(f"relative image won't resolve in the .docx: {u}")
            break
    return w


# --- pandoc ---------------------------------------------------------------

def to_docx(src_md: Path, out_docx: Path):
    out_docx.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["pandoc", str(src_md), "-o", str(out_docx)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        die(f"pandoc failed: {r.stderr.strip()}")
    if not out_docx.exists() or out_docx.stat().st_size == 0:
        die(f"pandoc produced no output at {out_docx}")


def file_uri(p: Path) -> str:
    return "file://" + urllib.parse.quote(str(p))


def update_index(doc: Path, name: str):
    """Idempotently link the doc in its folder note (file named after the
    folder) under a '## MSFT-shared' heading. Returns a status string, or None
    if there's no folder note to touch."""
    idx = doc.parent / (doc.parent.name + ".md")
    if not idx.is_file() or idx.resolve() == doc.resolve():
        return None
    link = f"- [[{name}]] — 🔗 MSFT OneDrive"
    itext = idx.read_text(encoding="utf-8")
    if link in itext:
        return f"index already links {name}"
    if "## MSFT-shared" in itext:
        itext = re.sub(r"(## MSFT-shared\n)", r"\1" + link + "\n", itext, count=1)
    else:
        itext = itext.rstrip() + f"\n\n## MSFT-shared\n{link}\n"
    idx.write_text(itext, encoding="utf-8")
    return f"index updated: {idx.relative_to(VAULT.resolve())}"


# --- main -----------------------------------------------------------------

def main():
    args = [a for a in sys.argv[1:] if a]
    if not args:
        die('usage: msftshare.py "<doc>" [msft]')
    msft = "msft" in args[1:]
    doc_arg = args[0]

    if not ONEDRIVE.is_dir():
        die("Work OneDrive not synced here — this skill only runs on Straylight")

    doc = resolve_doc(doc_arg)
    rel = doc.relative_to(VAULT.resolve())
    name = doc.stem
    docx_path = SHARED_ROOT / rel.parent / (name + ".docx")
    sidecar = SHARED_ROOT / rel.parent / (name + ".md")
    shadow_rel = str((Path("vault-shared") / rel.parent / (name + ".docx")))
    sidecar_rel = str((Path("vault-shared") / rel.parent / (name + ".md")))

    text = doc.read_text(encoding="utf-8")
    fm, body, had_fm = split_frontmatter(text)
    stubbed = fm_get(fm, "source_of_truth") == STUB_MARKER

    # ── Already flipped: OneDrive .docx is the LIVE source of truth ──────────
    # The user edits it in Word. Never regenerate it — that would clobber those
    # edits with a stale re-export. Re-runs only re-assert the vault pointer /
    # index and report. (The .md sidecar is a one-time snapshot from flip time,
    # NOT an ongoing source.)
    if stubbed:
        out = [f"• {rel}: already flipped — source of truth is the OneDrive Word doc.",
               f"  Word doc: {shadow_rel}  (edit it there; this vault file stays a pointer)"]
        if not sidecar.is_file():
            out.append("  ⚠ preserved-markdown sidecar is gone (original md only in .stversions)")
        if msft:
            note = update_index(doc, name)
            if note:
                out.append("  " + note)
        print("\n".join(out))
        return

    # ── Vault is the source of truth: (re)build the .docx shadow from it ─────
    warns = fidelity_warnings(text)
    to_docx(doc, docx_path)
    out = [f"✓ shadow: {shadow_rel}  ({docx_path.stat().st_size // 1024} KB)"]
    for w in warns:
        out.append(f"  ⚠ {w}")

    if not msft:
        # Default mode: vault stays source of truth; record the shadow pointer.
        fm2 = list(fm)
        fm_set(fm2, "msft_shadow", shadow_rel)
        doc.write_text(assemble(fm2, body), encoding="utf-8")
        out.append("  source of truth: vault (this file)")
        out.append("  → in OneDrive, right-click the .docx → Copy link to share "
                   "(wait for the cloud-sync badge to clear first)")
        print("\n".join(out))
        return

    # ── MSFT mode (first flip): preserve markdown, stub vault, update index ──
    # Preserve the real markdown as the OneDrive sidecar BEFORE truncating.
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(text, encoding="utf-8")
    if not sidecar.is_file() or sidecar.stat().st_size < max(1, len(text) // 2):
        die(f"sidecar write looks incomplete ({sidecar}) — aborting before "
            f"touching the vault file")

    # Build the stub: keep existing frontmatter, add the markers.
    stub_fm = list(fm) if had_fm else []
    fm_set(stub_fm, "source_of_truth", STUB_MARKER)
    fm_set(stub_fm, "msft_doc", shadow_rel)
    fm_set(stub_fm, "msft_shadow_md", sidecar_rel)
    share = fm_get(stub_fm, "msft_share_url") or ""
    if "msft_share_url" not in "\n".join(stub_fm):
        fm_set(stub_fm, "msft_share_url", share)

    stub_body = (
        f"> [!info] Source of truth: **Microsoft OneDrive** — shared with coworkers\n"
        f"> This doc is canonical in Work OneDrive. The vault keeps only this pointer.\n\n"
        f"- **Open (local Word doc):** [{name}.docx]({file_uri(docx_path)})\n"
        f"- **Markdown source (preserved):** `{sidecar_rel}`\n"
        f"- **Share link:** "
        + (share if share else "_not set — in OneDrive, right-click → Copy link, "
                                "then set `msft_share_url` in the frontmatter_")
        + "\n"
    )
    doc.write_text(assemble(stub_fm, stub_body), encoding="utf-8")
    out.append("  source of truth: Microsoft OneDrive (vault file is now a pointer)")
    out.append(f"  markdown preserved: {sidecar_rel}")

    note = update_index(doc, name)
    out.append("  " + (note or "(no folder index note to update)"))
    print("\n".join(out))


if __name__ == "__main__":
    main()
