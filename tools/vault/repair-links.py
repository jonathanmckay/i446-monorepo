#!/usr/bin/env python3
"""
Repair dead wikilinks in the Obsidian vault at ~/vault.

Strategy:
  1. Build a filename index of every .md file in the vault (excluding
     .git, .obsidian, hcmc/readwise, z_ibx).
  2. Build multiple lookup maps:
     - basename_lower (without .md) -> [path,...]
     - basename_normalized -> [path,...]  (lowercased, arrows/symbols/spaces collapsed)
     - path_stem_lower -> [path,...]      (e.g. "d359/vinod d359" or relative)
     - basename_relaxed -> [path,...]     (lowercased, all non-alphanum stripped)
  3. Scan all writable .md files for wikilinks: [[target]] or [[target|alias]].
  4. Skip:
     - image embeds ![[...]] or wikilinks whose target has an image extension
     - targets that begin with "Pasted image "
     - targets pointing at headings only (#heading)
     - resolved links (target already exists)
     - files in excluded dirs
  5. For each dead link, classify:
     - "resolved" (not dead)
     - "moved" / "variant" - single unique match via one of the indexes
     - "folder-index" - target ends in "/" and there's a file <name>.md or <name>/<name>.md
     - "truncated-mailto" - mailto: link with no TLD (e.g. ryankura@yahoo)
     - "ambiguous" - multiple candidates
     - "unresolvable" - no candidates
  6. Apply fixes conservatively: only when unique match and repair is safe.
     Preserve the alias (|alias) portion of the link if present.

Run in dry-run mode by default; pass --apply to actually edit files.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

VAULT = Path.home() / "vault"
EXCLUDE_DIRS = {".git", ".obsidian"}
# Paths never indexed at all (excluded entirely)
EXCLUDE_PATH_PREFIXES: tuple[str, ...] = ()
# Paths that should NOT be edited as sources, but still indexed as resolvable targets.
# Matches original scan exclusions (z_ibx, hcmc/readwise) plus AI transcripts which
# are historical conversation records.
NO_EDIT_PREFIXES = (
    "z_ibx/",
    "hcmc/readwise/",
    "i447/i446/ai-transcripts/",
)
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".tiff", ".pdf", ".heic", ".mp4", ".mov", ".webm"}


def is_excluded(rel: str) -> bool:
    # rel path relative to vault root
    parts = Path(rel).parts
    if not parts:
        return True
    if parts[0] in EXCLUDE_DIRS:
        return True
    for pref in EXCLUDE_PATH_PREFIXES:
        if rel.startswith(pref):
            return True
    return False


def is_no_edit(rel: str) -> bool:
    for pref in NO_EDIT_PREFIXES:
        if rel.startswith(pref):
            return True
    return False


def normalize_name(name: str) -> str:
    """Aggressive normalization: lowercase, strip arrows/punct, collapse whitespace."""
    s = name.lower()
    # Unicode normalize
    s = unicodedata.normalize("NFKC", s)
    # Replace arrows and common separators with space
    s = re.sub(r"[→➔➝➞➡️\-_/\\:]+", " ", s)
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s


def relax_name(name: str) -> str:
    """Even more aggressive: strip all non-alphanum, lower."""
    s = unicodedata.normalize("NFKC", name).lower()
    s = re.sub(r"[^0-9a-z一-鿿぀-ゟ゠-ヿ؀-ۿ]+", "", s)
    return s


def walk_vault():
    for root, dirs, files in os.walk(VAULT):
        # prune excluded dirs
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        rel_root = Path(root).relative_to(VAULT).as_posix()
        if rel_root == ".":
            rel_root = ""
        for f in files:
            if not f.endswith(".md"):
                continue
            rel = f"{rel_root}/{f}" if rel_root else f
            if is_excluded(rel):
                continue
            yield rel, Path(root) / f


def build_index():
    """Return dict of lookups and the set of all known rel paths."""
    all_paths: set[str] = set()
    basename_lower: dict[str, list[str]] = defaultdict(list)
    basename_norm: dict[str, list[str]] = defaultdict(list)
    basename_relaxed: dict[str, list[str]] = defaultdict(list)
    stem_lower: dict[str, list[str]] = defaultdict(list)  # path stem lower

    for rel, _full in walk_vault():
        all_paths.add(rel)
        stem = rel[:-3] if rel.endswith(".md") else rel
        base = os.path.basename(stem)
        basename_lower[base.lower()].append(rel)
        basename_norm[normalize_name(base)].append(rel)
        basename_relaxed[relax_name(base)].append(rel)
        stem_lower[stem.lower()].append(rel)

    return {
        "all_paths": all_paths,
        "basename_lower": basename_lower,
        "basename_norm": basename_norm,
        "basename_relaxed": basename_relaxed,
        "stem_lower": stem_lower,
    }


# wikilink regex: captures target, optional alias, handles embed !
# Target may contain spaces, unicode, slashes, pipes for alias.
# [[target]] or [[target|alias]] or ![[target]] (embed)
WIKILINK_RE = re.compile(r"(!?)\[\[([^\[\]\n]+?)\]\]")


def split_link(inner: str) -> tuple[str, str | None, bool]:
    """Split 'target#heading|alias' into (target_without_heading, alias, escaped_pipe).

    escaped_pipe is True when the pipe was written as `\|` (table-cell safe).
    """
    # Detect escaped pipe vs literal pipe. Obsidian inside markdown tables uses \|.
    escaped = False
    if "\\|" in inner:
        escaped = True
        # Split only on the escaped pipe (the first one, since alias is last segment)
        tgt, alias = inner.split("\\|", 1)
    elif "|" in inner:
        tgt, alias = inner.split("|", 1)
    else:
        tgt, alias = inner, None
    # strip heading fragment from target
    if "#" in tgt:
        tgt = tgt.split("#", 1)[0]
    return tgt, alias, escaped


def target_is_image(target: str) -> bool:
    t = target.strip()
    if t.lower().startswith("pasted image"):
        return True
    for ext in IMAGE_EXTS:
        if t.lower().endswith(ext):
            return True
    return False


def resolve_target(target: str, idx: dict, source_rel: str) -> str | None:
    """Return the resolved rel path if target already points to a known file, else None.

    Obsidian wikilink resolution rules (simplified):
      - exact path match (with or without .md)
      - basename match, case-insensitive (first-match wins)
    """
    if not target:
        return None
    tgt = target.strip()
    # normalize backslashes
    tgt_posix = tgt.replace("\\", "/")
    candidates = [tgt_posix]
    if not tgt_posix.endswith(".md"):
        candidates.append(tgt_posix + ".md")
    # Exact path
    for c in candidates:
        if c in idx["all_paths"]:
            return c
    # basename-only match (Obsidian resolves to whichever it chose — if any exist, consider resolved)
    base = os.path.basename(tgt_posix)
    base_stem = base[:-3] if base.endswith(".md") else base
    matches = idx["basename_lower"].get(base_stem.lower(), [])
    if matches:
        return matches[0]
    # full stem path lower match
    stem = tgt_posix[:-3] if tgt_posix.endswith(".md") else tgt_posix
    matches = idx["stem_lower"].get(stem.lower(), [])
    if matches:
        return matches[0]
    return None


def find_fix(target: str, idx: dict, source_rel: str) -> tuple[str | None, str]:
    """Return (new_target_rel_stem_or_None, category)."""
    tgt = target.strip().replace("\\", "/")

    if not tgt:
        return None, "empty"

    # image embeds or image-like targets — skip
    if target_is_image(tgt):
        return None, "image"

    # mailto: inside wikilinks shouldn't really happen, but guard
    if tgt.lower().startswith("mailto:"):
        return None, "mailto-in-wiki"

    # folder reference with trailing slash: try <name>/<name>.md then <name>.md
    if tgt.endswith("/"):
        name = tgt.rstrip("/")
        # try <name>/<name>.md (relative to source dir)
        source_dir = os.path.dirname(source_rel)
        if source_dir:
            rel_candidate = f"{source_dir}/{name}/{os.path.basename(name)}.md"
            if rel_candidate in idx["all_paths"]:
                return rel_candidate[:-3], "folder-index"
            rel_flat = f"{source_dir}/{name}.md"
            if rel_flat in idx["all_paths"]:
                return rel_flat[:-3], "folder-index"
        # try <name>/<name>.md (vault root)
        candidate = f"{name}/{os.path.basename(name)}.md"
        if candidate in idx["all_paths"]:
            return candidate[:-3], "folder-index"
        # try <name>.md at vault root (skip if source is nested — ambiguous)
        if f"{name}.md" in idx["all_paths"] and not source_dir:
            return name, "folder-index"
        # basename matches — prefer those nested under source directory
        base = os.path.basename(name)
        matches = idx["basename_lower"].get(base.lower(), [])
        if source_dir:
            nested = [m for m in matches if m.startswith(source_dir + "/")]
            if len(nested) == 1:
                return nested[0][:-3], "folder-index"
        src_top = source_rel.split("/", 1)[0] if "/" in source_rel else ""
        if src_top:
            filtered = [m for m in matches if m.startswith(src_top + "/")]
            if len(filtered) == 1:
                return filtered[0][:-3], "folder-index"
        if len(matches) == 1:
            return matches[0][:-3], "folder-index"
        return None, "folder-unresolved"

    base = os.path.basename(tgt)
    base_stem = base[:-3] if base.endswith(".md") else base

    # 1) exact basename (case-insensitive) unique
    matches = idx["basename_lower"].get(base_stem.lower(), [])
    if len(matches) == 1:
        return matches[0][:-3], "basename"
    if len(matches) > 1:
        # If the original path contains a directory hint, prefer matches under that dir
        if "/" in tgt:
            dir_hint = tgt.rsplit("/", 1)[0].lower()
            filtered = [m for m in matches if dir_hint in m.lower()]
            if len(filtered) == 1:
                return filtered[0][:-3], "basename-dir-hinted"
        # Prefer matches in the same top-level domain as source
        src_top = source_rel.split("/", 1)[0] if "/" in source_rel else ""
        if src_top:
            filtered = [m for m in matches if m.startswith(src_top + "/")]
            if len(filtered) == 1:
                return filtered[0][:-3], "basename-domain-hinted"
        return None, "basename-ambiguous"

    # 2) normalized basename
    norm = normalize_name(base_stem)
    matches = idx["basename_norm"].get(norm, [])
    if len(matches) == 1:
        return matches[0][:-3], "variant"
    if len(matches) > 1:
        src_top = source_rel.split("/", 1)[0] if "/" in source_rel else ""
        if src_top:
            filtered = [m for m in matches if m.startswith(src_top + "/")]
            if len(filtered) == 1:
                return filtered[0][:-3], "variant-domain-hinted"
        return None, "variant-ambiguous"

    # 3) relaxed basename (last resort — skip by default, too risky)
    relaxed = relax_name(base_stem)
    if relaxed:
        matches = idx["basename_relaxed"].get(relaxed, [])
        if len(matches) == 1:
            return matches[0][:-3], "relaxed"

    return None, "unresolvable"


def build_new_wikilink(fix_stem: str, original_alias: str | None, original_target: str, source_rel: str, escaped_pipe: bool = False) -> str:
    """Return the inner text for the new wikilink (without the [[ ]])."""
    new_target = fix_stem  # full rel path sans .md
    if original_alias is not None:
        sep = "\\|" if escaped_pipe else "|"
        return f"{new_target}{sep}{original_alias}"
    return new_target


def scan_and_classify(idx: dict, restrict_source_prefixes: list[str] | None = None):
    """Yield tuples of (source_rel, start, end, original_inner, target, alias, fix_stem, category)."""
    for rel, full in walk_vault():
        if is_no_edit(rel):
            continue
        if restrict_source_prefixes:
            if not any(rel.startswith(p) for p in restrict_source_prefixes):
                continue
        try:
            text = full.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        for m in WIKILINK_RE.finditer(text):
            embed_marker = m.group(1)
            inner = m.group(2)
            if embed_marker == "!":
                continue  # skip embeds (images, transclusions)
            target, alias, escaped_pipe = split_link(inner)
            if not target.strip():
                continue
            # Skip image-like targets
            if target_is_image(target):
                continue
            # Skip purely header-local links (e.g. "#foo") — handled by split_link returning empty target
            # Skip :space: style placeholders
            if target.strip() == ":space:":
                continue

            resolved = resolve_target(target, idx, rel)
            if resolved:
                continue

            fix_stem, category = find_fix(target, idx, rel)
            yield {
                "source": rel,
                "start": m.start(),
                "end": m.end(),
                "original": m.group(0),
                "inner": inner,
                "target": target,
                "alias": alias,
                "escaped_pipe": escaped_pipe,
                "fix_stem": fix_stem,
                "category": category,
            }


def scan_mailto_links(idx: dict):
    """Find markdown links of the form [text](mailto:...) where the mailto has no TLD."""
    md_link_re = re.compile(r"\[([^\]\n]*)\]\(mailto:([^)\s]+)\)")
    results = []
    for rel, full in walk_vault():
        if is_no_edit(rel):
            continue
        try:
            text = full.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for m in md_link_re.finditer(text):
            addr = m.group(2)
            # check for TLD presence after the @
            if "@" in addr:
                user, domain = addr.rsplit("@", 1)
                if "." not in domain:
                    results.append({
                        "source": rel,
                        "start": m.start(),
                        "end": m.end(),
                        "original": m.group(0),
                        "addr": addr,
                        "category": "truncated-mailto",
                    })
    return results


def apply_fix(file_path: Path, original: str, new: str) -> bool:
    """Replace first occurrence of original with new. Returns True if replaced."""
    text = file_path.read_text(encoding="utf-8")
    if original not in text:
        return False
    # Replace ALL occurrences to be safe (dead link may appear multiple times with same text)
    new_text = text.replace(original, new)
    file_path.write_text(new_text, encoding="utf-8")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Actually write edits (default: dry-run)")
    ap.add_argument("--report", type=str, default=str(Path.home() / "vault/z_ibx/overnight/04b-vault-links-repaired.md"))
    ap.add_argument("--limit", type=int, default=0, help="Limit number of fixes (0 = no limit)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    print(f"Building index from {VAULT}...", file=sys.stderr)
    idx = build_index()
    print(f"  {len(idx['all_paths'])} md files indexed", file=sys.stderr)

    print("Scanning wikilinks...", file=sys.stderr)
    findings = list(scan_and_classify(idx))
    print(f"  {len(findings)} dead wikilinks found", file=sys.stderr)

    print("Scanning truncated mailto links...", file=sys.stderr)
    mailto_findings = scan_mailto_links(idx)
    print(f"  {len(mailto_findings)} truncated mailto found", file=sys.stderr)

    # Classify
    categories = defaultdict(list)
    for f in findings:
        categories[f["category"]].append(f)

    fixable_cats = {"basename", "variant", "folder-index", "basename-dir-hinted",
                    "basename-domain-hinted", "variant-domain-hinted"}

    to_fix = [f for f in findings if f["category"] in fixable_cats and f["fix_stem"]]
    if args.limit:
        to_fix = to_fix[: args.limit]

    # Dry-run print
    print("\n=== DRY-RUN PLAN ===", file=sys.stderr)
    for cat, items in sorted(categories.items(), key=lambda kv: -len(kv[1])):
        marker = "FIX" if cat in fixable_cats else "skip"
        print(f"  [{marker}] {cat}: {len(items)}", file=sys.stderr)
    print(f"  [FIX] truncated-mailto: {len(mailto_findings)}", file=sys.stderr)

    # Group fixes per file
    fixes_by_file: dict[str, list[dict]] = defaultdict(list)
    for f in to_fix:
        fixes_by_file[f["source"]].append(f)

    mailto_by_file: dict[str, list[dict]] = defaultdict(list)
    for f in mailto_findings:
        mailto_by_file[f["source"]].append(f)

    # Apply (or simulate)
    applied = []
    skipped_duplicate_original = []
    mailto_applied = []

    # Cross-domain safety: if source top-level domain differs from fix top-level domain,
    # only allow for specific safe categories (variant, basename-domain-hinted are domain-correct by construction).
    # basename and folder-index with cross-domain leaps are risky — skip those.
    cross_domain_skipped = []
    for source, items in fixes_by_file.items():
        fp = VAULT / source
        src_top = source.split("/", 1)[0] if "/" in source else ""
        for f in items:
            fix_top = f["fix_stem"].split("/", 1)[0] if "/" in f["fix_stem"] else ""
            cross = bool(src_top) and bool(fix_top) and (src_top != fix_top)
            # Cross-domain safety:
            # - variant: unique normalized match -> safe
            # - basename-dir-hinted: explicit directory hint -> safe
            # - folder-index and basename: too generic cross-domain -> skip
            # - basename-domain-hinted: by construction source-domain matches -> never cross-domain here
            cross_domain_safe_cats = {"variant", "basename-dir-hinted", "variant-domain-hinted"}
            if cross and f["category"] not in cross_domain_safe_cats:
                cross_domain_skipped.append({
                    "file": source, "original": f["original"], "fix": f["fix_stem"],
                    "category": f["category"],
                })
                continue

            new_inner = build_new_wikilink(f["fix_stem"], f["alias"], f["target"], source, f["escaped_pipe"])
            new_link = f"[[{new_inner}]]"
            if new_link == f["original"]:
                continue
            entry = {
                "file": source,
                "original": f["original"],
                "new": new_link,
                "category": f["category"],
                "target": f["target"],
                "fix": f["fix_stem"],
            }
            if args.apply:
                try:
                    ok = apply_fix(fp, f["original"], new_link)
                    if ok:
                        applied.append(entry)
                    else:
                        skipped_duplicate_original.append(entry)
                except Exception as e:
                    skipped_duplicate_original.append({**entry, "error": str(e)})
            else:
                applied.append(entry)

    # Mailto fixes: comment out by prepending HTML-comment markers around the link
    for source, items in mailto_by_file.items():
        fp = VAULT / source
        for f in items:
            # Replace mailto:addr with mailto:addr (commented) — leave as-is but wrap in <!-- -->
            # Simpler: replace the full link with a plain text version.
            # Actually, let's just leave them alone and report them — editing mailto strings is risky.
            mailto_applied.append({
                "file": source,
                "original": f["original"],
                "addr": f["addr"],
                "category": "truncated-mailto",
                "action": "reported-only",
            })

    # Build report
    unresolvable = [f for f in findings if f["category"] in {"unresolvable", "basename-ambiguous", "variant-ambiguous", "folder-unresolved", "relaxed", "mailto-in-wiki"}]

    report_lines = []
    report_lines.append("---")
    report_lines.append("title: Vault Link Repair")
    report_lines.append("date: 2026-04-21")
    report_lines.append("type: report")
    report_lines.append("tags: [vault, links, maintenance, repair]")
    report_lines.append("---")
    report_lines.append("")
    report_lines.append("# Vault Link Repair — 2026-04-21")
    report_lines.append("")
    mode = "APPLIED" if args.apply else "DRY-RUN"
    report_lines.append(f"Mode: **{mode}**")
    report_lines.append(f"Repair script: `~/i446-monorepo/tools/vault/repair-links.py`")
    report_lines.append("")
    report_lines.append("## Summary counts")
    report_lines.append("")
    report_lines.append("| Category | Count |")
    report_lines.append("|---|--:|")
    for cat, items in sorted(categories.items(), key=lambda kv: -len(kv[1])):
        marker = "fixable" if cat in fixable_cats else "skip"
        report_lines.append(f"| {cat} ({marker}) | {len(items)} |")
    report_lines.append(f"| truncated-mailto (reported) | {len(mailto_findings)} |")
    report_lines.append("")
    report_lines.append(f"- Fixes applied: **{len(applied)}**")
    report_lines.append(f"- Unresolvable (manual follow-up): **{len(unresolvable)}**")
    report_lines.append(f"- Skipped duplicates/errors: **{len(skipped_duplicate_original)}**")
    report_lines.append(f"- Skipped cross-domain (safety): **{len(cross_domain_skipped)}**")
    report_lines.append("")

    report_lines.append("## Fixes applied")
    report_lines.append("")
    if not applied:
        report_lines.append("_(none)_")
    else:
        # Group by file
        by_file: dict[str, list[dict]] = defaultdict(list)
        for a in applied:
            by_file[a["file"]].append(a)
        for source in sorted(by_file.keys()):
            report_lines.append(f"### `{source}`")
            for a in by_file[source]:
                report_lines.append(f"- [{a['category']}] `{a['original']}` -> `{a['new']}`")
            report_lines.append("")

    report_lines.append("## Unresolvable dead links (manual follow-up)")
    report_lines.append("")
    if not unresolvable:
        report_lines.append("_(none)_")
    else:
        by_file: dict[str, list[dict]] = defaultdict(list)
        for u in unresolvable:
            by_file[u["source"]].append(u)
        for source in sorted(by_file.keys()):
            report_lines.append(f"### `{source}`")
            for u in by_file[source]:
                report_lines.append(f"- [{u['category']}] `{u['original']}`")
            report_lines.append("")

    report_lines.append("## Truncated mailto links (reported, not edited)")
    report_lines.append("")
    if not mailto_applied:
        report_lines.append("_(none)_")
    else:
        for m in mailto_applied:
            report_lines.append(f"- `{m['file']}`: `{m['original']}` (addr=`{m['addr']}`)")
    report_lines.append("")

    if cross_domain_skipped:
        report_lines.append("## Skipped (cross-domain safety)")
        report_lines.append("")
        by_file: dict[str, list[dict]] = defaultdict(list)
        for s in cross_domain_skipped:
            by_file[s["file"]].append(s)
        for source in sorted(by_file.keys()):
            report_lines.append(f"### `{source}`")
            for s in by_file[source]:
                report_lines.append(f"- [{s['category']}] `{s['original']}` -> would have pointed to `{s['fix']}`")
            report_lines.append("")

    if skipped_duplicate_original:
        report_lines.append("## Skipped (duplicate original / write error)")
        report_lines.append("")
        for s in skipped_duplicate_original:
            err = f" — {s.get('error','')}" if "error" in s else ""
            report_lines.append(f"- `{s['file']}`: `{s['original']}`{err}")
        report_lines.append("")

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(report_lines), encoding="utf-8")

    print(f"\nReport written to: {report_path}", file=sys.stderr)
    print(f"Mode: {mode}", file=sys.stderr)
    print(f"  Applied: {len(applied)}", file=sys.stderr)
    print(f"  Unresolvable: {len(unresolvable)}", file=sys.stderr)


if __name__ == "__main__":
    main()
