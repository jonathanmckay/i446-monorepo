"""build_order_heal — merge ritual stamps lost to Syncthing conflicts.

build-order.md has two concurrent writers on two machines: Straylight stamps
ritual emojis on block headers at completion time (did-fast run_ritual, /0g,
/-1g), while Ix rewrites the file at every even-hour lock-and-mark fire and
whenever link-meetings finds a new d357 recording. Syncthing resolves the
inevitable races by picking one side and leaving the loser as a LOCAL
`build-order.sync-conflict-*.md` (conflict files are not synced back), so
the losing side's stamps simply vanish — and the daemon's next
reconcile_p_for_day then SETs 0分!P from the surviving stamps, converting
the file-level loss into a points loss (user report 2026-07-29: "I did all
-1n for 辰 but only 7 -1n points got recorded"; 🎯/✅ were in the 07:30
conflict copy, not the canonical file).

heal() unions the ritual emojis from same-day conflict copies back into the
canonical file's block-header lines, then deletes the processed conflict
files. Both writers call it BEFORE acting: run_ritual (so a fresh stamp
lands on a healed file) and run_lock_and_mark (so the reconcile counts
healed stamps, on Ix's own local conflicts). Union-only — a stamp is never
removed here; validation/stripping stays the daemon's job.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import neon_blocks as _nb  # noqa: E402

RITUAL_EMOJIS = ("☀️", "🎯", "📧", "⏱️", "✅")
_BLOCK_RE = re.compile(r"^- ([寅卯辰巳午未申酉戌亥子])(?=\s|$)")
_DATE_RE = re.compile(r"^date:\s*(\S+)", re.MULTILINE)


def _doc_date(text: str) -> str | None:
    m = _DATE_RE.search(text)
    return m.group(1).strip("\"'") if m else None


def _block_stamps(text: str) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for ln in text.split("\n"):
        m = _BLOCK_RE.match(ln)
        if m:
            out[m.group(1)] = {e for e in RITUAL_EMOJIS if e in ln}
    return out


def merge_stamps(canonical: str, conflict: str) -> tuple[str, list[str]]:
    """Union the conflict copy's block-header ritual emojis into the
    canonical text. Returns (merged_text, ["辰+🎯", ...]). Missing emojis
    are inserted after the branch glyph and any existing emojis, before a
    "(...)" annotation when one exists, so the line keeps its shape:
    `- 辰 ☀️ ⏱️ 📧 (68min) 😈` → `- 辰 ☀️ ⏱️ 📧 🎯 (68min) 😈`."""
    conf = _block_stamps(conflict)
    added: list[str] = []
    out_lines = []
    for ln in canonical.split("\n"):
        m = _BLOCK_RE.match(ln)
        if m:
            branch = m.group(1)
            missing = [e for e in RITUAL_EMOJIS
                       if e in conf.get(branch, set()) and e not in ln]
            if missing:
                ins = " ".join(missing)
                if "(" in ln:
                    i = ln.index("(")
                    ln = f"{ln[:i].rstrip()} {ins} {ln[i:]}"
                else:
                    ln = f"{ln.rstrip()} {ins}"
                added.extend(f"{branch}+{e}" for e in missing)
        out_lines.append(ln)
    return "\n".join(out_lines), added


def heal(build_order: Path) -> dict:
    """Merge every SAME-DAY conflict sibling into `build_order`, delete the
    processed conflict files, and return a summary. Conflict copies from a
    different day are left alone (their branch lines describe another day's
    blocks — merging them would stamp rituals that never happened today).

    Lock-protected (2026-08-02): this runs on whichever machine calls it
    (Straylight's did-fast run_ritual, Ix's run_lock_and_mark), and two
    invocations racing on the SAME machine — e.g. two rapid ritual
    completions each calling heal() first — could otherwise lose one
    side's merge the exact same way the ritual stamps themselves used to.
    The lock is local-machine-scoped like everything else in this module
    (see neon_blocks.build_order_lock's docstring on flock's cross-machine
    limits) — appropriate here since heal() only ever reads/writes
    THIS machine's own local conflict files and local canonical copy."""
    result = {"merged": [], "skipped": [], "errors": []}
    with _nb.build_order_lock(build_order):
        try:
            canonical = build_order.read_text(encoding="utf-8")
        except OSError as e:
            result["errors"].append(str(e))
            return result
        day = _doc_date(canonical)
        stem = build_order.stem  # "build-order"
        processed: list[Path] = []
        for cf in sorted(build_order.parent.glob(f"{stem}.sync-conflict-*.md")):
            try:
                ctext = cf.read_text(encoding="utf-8")
            except OSError as e:
                result["errors"].append(f"{cf.name}: {e}")
                continue
            if day is None or _doc_date(ctext) != day:
                result["skipped"].append(cf.name)
                continue
            canonical, added = merge_stamps(canonical, ctext)
            result["merged"].append({"file": cf.name, "added": added})
            processed.append(cf)
        # Write BEFORE deleting the conflict copies — a failed write must not
        # discard the only surviving copy of the stamps.
        if any(m["added"] for m in result["merged"]):
            tmp = build_order.with_suffix(".md.tmp")
            tmp.write_text(canonical, encoding="utf-8")
            tmp.rename(build_order)
        for cf in processed:
            try:
                cf.unlink()
            except OSError as e:
                result["errors"].append(f"unlink {cf.name}: {e}")
    return result
