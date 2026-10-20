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
# Sentinel that marks the auto-inserted "open the .docx" link line so re-runs
# replace it in place instead of stacking duplicates.
DOCX_LINK_SENTINEL = "<!-- msftshare:docx-link -->"
# Binary/office formats we share verbatim: no pandoc conversion and no vault
# frontmatter surgery (there's nothing markdown to rewrite, and they're already
# shareable). These may live anywhere on disk, not just inside the vault.
PASSTHROUGH_EXTS = {".pptx", ".ppt", ".xlsx", ".xls", ".pdf"}
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
    # Passthrough office files (e.g. .pptx) are shared verbatim and may live
    # anywhere — accept an explicit path (absolute, ~, cwd, or vault-relative)
    # without the vault-membership or markdown requirements below.
    ap = Path(arg).expanduser()
    for c in (ap, VAULT / arg):
        if c.is_file() and c.suffix.lower() in PASSTHROUGH_EXTS:
            return c.resolve()
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

def strip_docx_link_line(body: str) -> str:
    """Drop the auto-inserted 'Shared Word copy' banner line (and the blank
    line it leaves behind) so it never round-trips into the .docx itself —
    it's vault-navigation furniture, not document content. Idempotent/no-op
    if the sentinel isn't present."""
    lines = body.split("\n")
    kept = [ln for ln in lines if DOCX_LINK_SENTINEL not in ln]
    if len(kept) == len(lines):
        return body
    return "\n".join(kept).lstrip("\n")


def to_docx_text(text: str, out_docx: Path):
    """Convert markdown TEXT (not necessarily what's on disk) to docx via a
    scratch temp file. Used so the banner-stripped version, not whatever the
    vault file currently contains, is always what pandoc sees."""
    import tempfile
    out_docx.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False,
                                      encoding="utf-8") as tf:
        tf.write(text)
        tmp_path = tf.name
    try:
        r = subprocess.run(["pandoc", tmp_path, "-o", str(out_docx)],
                           capture_output=True, text=True)
    finally:
        os.unlink(tmp_path)
    if r.returncode != 0:
        die(f"pandoc failed: {r.stderr.strip()}")
    if not out_docx.exists() or out_docx.stat().st_size == 0:
        die(f"pandoc produced no output at {out_docx}")


def file_uri(p: Path) -> str:
    return "file://" + urllib.parse.quote(str(p))


def ensure_body_docx_link(body: str, docx_path: Path, stem: str) -> str:
    """Idempotently place a clickable link to the local .docx at the top of the
    body, so the shared Word doc is findable from Obsidian (default mode records
    only a frontmatter path otherwise, which you can't click). Replaces an
    existing sentinel-marked line in place; else inserts at the top."""
    link_line = (f"> 📄 **Shared Word copy:** [{stem}.docx]({file_uri(docx_path)})"
                 f" · right-click in OneDrive → Copy link to share. {DOCX_LINK_SENTINEL}")
    lines = body.split("\n")
    for i, ln in enumerate(lines):
        if DOCX_LINK_SENTINEL in ln:
            lines[i] = link_line
            return "\n".join(lines)
    return link_line + "\n\n" + body.lstrip("\n")


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

def share_passthrough(doc: Path, msft: bool):
    """Share a binary office file (e.g. .pptx) verbatim into Work OneDrive.

    No pandoc conversion and no vault frontmatter surgery — these formats are
    already shareable and usually live outside the vault. If the source happens
    to be inside the vault its folder path is mirrored; otherwise it lands at
    the vault-shared root under its own name."""
    import shutil
    name = doc.name
    try:
        rel_dir = doc.relative_to(VAULT.resolve()).parent
    except ValueError:
        rel_dir = Path(".")
    dest = SHARED_ROOT / rel_dir / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    src_size = doc.stat().st_size
    shutil.copy2(doc, dest)
    if not dest.is_file() or dest.stat().st_size != src_size:
        die(f"copy looks incomplete ({dest}) — source is {src_size} bytes")
    shadow_rel = str(Path("vault-shared") / rel_dir / name)
    out = [f"✓ shared verbatim: {shadow_rel}  ({dest.stat().st_size // 1024} KB)",
           f"  open: {file_uri(dest)}",
           f"  source of truth: the original file ({doc}) — not flipped",
           "  → in OneDrive, right-click the file → Copy link to share "
           "(wait for the cloud-sync badge to clear first)"]
    if msft:
        out.append("  note: the 'msft' flip is not supported for binary office "
                   "files — the original stays canonical; share via the link above.")
    print("\n".join(out))


def main():
    args = [a for a in sys.argv[1:] if a]
    if not args:
        die('usage: msftshare.py "<doc>" [msft]')
    msft = "msft" in args[1:]
    doc_arg = args[0]

    if not ONEDRIVE.is_dir():
        die("Work OneDrive not synced here — this skill only runs on Straylight")

    doc = resolve_doc(doc_arg)

    # Binary office formats (e.g. .pptx) are shared verbatim — no conversion,
    # no vault-relative resolution, no frontmatter/read_text below.
    if doc.suffix.lower() in PASSTHROUGH_EXTS:
        share_passthrough(doc, msft)
        return

    rel = doc.relative_to(VAULT.resolve())
    name = doc.stem

    text = doc.read_text(encoding="utf-8")
    fm, body, had_fm = split_frontmatter(text)
    stubbed = fm_get(fm, "source_of_truth") == STUB_MARKER

    # Destination under vault-shared. By default it mirrors the vault folder
    # path (which can leak personal vault codes like h335/i9). A doc may set
    # `msft_dest:` in frontmatter to a clean, work-friendly relative path; the
    # last path component becomes the Word doc name, the rest are folders.
    dest = fm_get(fm, "msft_dest")
    if dest:
        dp = Path(dest)
        if dp.suffix.lower() in (".docx", ".md"):
            dp = dp.with_suffix("")
        if dp.is_absolute() or ".." in dp.parts or not dp.name:
            die("msft_dest must be a relative path under vault-shared "
                "(no leading '/' or '..')")
        rel_dir, stem = dp.parent, dp.name
    else:
        rel_dir, stem = rel.parent, name
    docx_path = SHARED_ROOT / rel_dir / (stem + ".docx")
    sidecar = SHARED_ROOT / rel_dir / (stem + ".md")
    shadow_rel = str(Path("vault-shared") / rel_dir / (stem + ".docx"))
    sidecar_rel = str(Path("vault-shared") / rel_dir / (stem + ".md"))

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
    # Build the docx from a banner-stripped copy so the "Shared Word copy"
    # link line never round-trips into the Word doc, regardless of whether
    # a prior run already baked it into the vault file on disk.
    docx_body = strip_docx_link_line(body)
    docx_source_text = assemble(fm, docx_body) if had_fm else docx_body
    warns = fidelity_warnings(docx_source_text)
    to_docx_text(docx_source_text, docx_path)
    out = [f"✓ shadow: {shadow_rel}  ({docx_path.stat().st_size // 1024} KB)"]
    for w in warns:
        out.append(f"  ⚠ {w}")

    if not msft:
        # Default mode: vault stays source of truth; record the shadow pointer
        # AND inject a clickable link to the .docx into the body (frontmatter
        # alone isn't clickable, so the Word doc would otherwise be unfindable).
        fm2 = list(fm)
        fm_set(fm2, "msft_shadow", shadow_rel)
        body2 = ensure_body_docx_link(body, docx_path, stem)
        doc.write_text(assemble(fm2, body2), encoding="utf-8")
        out.append("  source of truth: vault (this file)")
        out.append("  ↪ clickable link to the .docx added at the top of the doc")
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
        f"- **Open (local Word doc):** [{stem}.docx]({file_uri(docx_path)})\n"
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
