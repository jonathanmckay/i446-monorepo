#!/usr/bin/env python3
"""d359-contacts-sync.py — weekly two-way sync between d359 vault profiles and
macOS Contacts, via the contacts-bridge Swift binary (the only thing holding
the TCC grant; this script never touches CNContactStore directly).

Default is --dry-run: report planned changes, write nothing. Pass --apply to
actually write. First run for any given contact is fill-blanks-only (no
diff-based conflict detection possible without a prior snapshot) — see the
design notes in the accompanying conversation for why naive mtime-based
last-write-wins was rejected.

Usage:
    python3 d359-contacts-sync.py [--apply] [--vault PATH] [--bridge PATH]
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

VAULT = Path.home() / "vault"
D359_DIR = VAULT / "d359"
REGISTRY = D359_DIR / "contacts-registry.md"
BRIDGE = Path.home() / "i446-monorepo/tools/d359/contacts-bridge"
STATE_DIR = Path.home() / ".local/state/jm/d359-apple-sync"
STATE_FILE = STATE_DIR / "state.json"
CONFLICTS_FILE = STATE_DIR / "conflicts.md"
REVIEW_FILE = STATE_DIR / "needs-review.md"

FIELDS = ["phone", "email", "work_email"]  # the only fields round-tripped

# --- frontmatter helpers (text-level, matches the pattern used elsewhere in this vault's tooling) ---

def split_frontmatter(text: str):
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[4:end].split("\n"), text[end + 5:], True
    return [], text, False


def parse_channels_block(fm_lines: list[str]) -> dict:
    """Very small indented-YAML reader for just the channels: sub-block."""
    out = {}
    in_block = False
    for ln in fm_lines:
        if re.match(r"^channels\s*:\s*$", ln):
            in_block = True
            continue
        if in_block:
            m = re.match(r"^\s+([a-z_]+)\s*:\s*(.*)$", ln)
            if m:
                out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
                continue
            if ln.strip() == "" or re.match(r"^\S", ln):
                in_block = False
    return out


def fm_get(fm_lines, key):
    pat = re.compile(rf"^{re.escape(key)}\s*:\s*(.*)$")
    for ln in fm_lines:
        m = pat.match(ln)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return None


def normalize_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z ]", "", s.lower()).strip()


# --- load d359 profiles ---

def load_d359_profiles():
    profiles = []
    for f in sorted(D359_DIR.glob("*.md")):
        if f.name == "contacts-registry.md" or f.name == "d359.md":
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        fm, body, had_fm = split_frontmatter(text)
        if not had_fm:
            continue
        title = fm_get(fm, "title") or f.stem
        channels = parse_channels_block(fm)
        apple_id = fm_get(fm, "apple_contact_id")
        relationship = fm_get(fm, "relationship")
        profiles.append({
            "path": f, "slug": f.stem, "title": title,
            "norm_name": normalize_name(title),
            "channels": channels, "apple_contact_id": apple_id,
            "relationship": relationship, "fm_lines": fm, "body": body,
        })
    return profiles


# --- Apple side via contacts-bridge ---

def bridge_dump():
    r = subprocess.run([str(BRIDGE), "dump"], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"✗ contacts-bridge dump failed: {r.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    return json.loads(r.stdout)


def apple_field_values(contact):
    """Map Apple's labeled arrays to the three flat d359 slots. Deterministic
    tie-break for multiple same-labeled values: first one encountered in the
    dump (stable given Swift dict iteration is not guaranteed-ordered, so this
    is 'a' deterministic choice, not 'the' semantically correct one — flagged
    in the design review as worth revisiting if it ever matters in practice)."""
    phones, emails = contact["phones"], contact["emails"]
    phone = phones.get("mobile") or next(iter(phones.values()), None)
    email = emails.get("home") or emails.get("iCloud") or next(iter(emails.values()), None)
    work_email = emails.get("work")
    return {"phone": phone, "email": email, "work_email": work_email}


# --- state ---

def load_state():
    if STATE_FILE.is_file():
        return json.loads(STATE_FILE.read_text())
    return {"schema": 1, "contacts": {}}


def save_state(state, apply_changes):
    if not apply_changes:
        return
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=1, ensure_ascii=False))


# --- main sync ---

def main():
    apply_changes = "--apply" in sys.argv

    if not BRIDGE.is_file():
        print(f"✗ contacts-bridge not found at {BRIDGE}", file=sys.stderr)
        sys.exit(1)

    profiles = load_d359_profiles()
    apple_contacts = bridge_dump()
    apple_by_id = {c["id"]: c for c in apple_contacts}
    apple_by_name = {}
    for c in apple_contacts:
        n = normalize_name(f"{c['givenName']} {c['familyName']}")
        apple_by_name.setdefault(n, []).append(c)

    state = load_state()
    conflicts, review, fills_to_d359, fills_to_apple = [], [], [], []
    linked, newly_linked, ambiguous, no_match = 0, 0, 0, 0

    for p in profiles:
        aid = p["apple_contact_id"]
        contact = apple_by_id.get(aid) if aid else None

        if contact is None and aid:
            # orphaned id — the linked Apple contact no longer exists.
            # Per design review: this is NOT a "deletion to ignore", it's a
            # dangling reference that will fail silently forever if left as-is.
            review.append(f"- **{p['title']}** (`{p['slug']}`): apple_contact_id `{aid}` no longer resolves (contact deleted on Apple side?) — needs manual re-link or clear")
            continue

        if contact is None:
            # bootstrap: try to link by name
            matches = apple_by_name.get(p["norm_name"], [])
            if len(matches) == 1:
                contact = matches[0]
                newly_linked += 1
                p["apple_contact_id"] = contact["id"]
            elif len(matches) > 1:
                ambiguous += 1
                review.append(f"- **{p['title']}** (`{p['slug']}`): AMBIGUOUS — {len(matches)} Apple contacts share this name ({', '.join(m['id'][:8] for m in matches)})")
                continue
            else:
                no_match += 1
                continue  # no Apple contact at all; not creating one this run (see report)

        linked += 1
        aid = contact["id"]
        apple_vals = apple_field_values(contact)
        d359_vals = {f: p["channels"].get(f) or None for f in FIELDS}
        snap = state["contacts"].get(aid, {}).get("last_synced", {})
        first_sync_contact = aid not in state["contacts"]

        resolved = {}
        for field in FIELDS:
            a_val, d_val = apple_vals.get(field), d359_vals.get(field)
            s_val = snap.get(field)

            if first_sync_contact or field not in snap:
                # FILL-ONLY mode — no snapshot to diff against.
                if a_val and not d_val:
                    fills_to_d359.append((p, field, a_val))
                    resolved[field] = a_val
                elif d_val and not a_val:
                    fills_to_apple.append((p, contact, field, d_val))
                    resolved[field] = d_val
                elif a_val and d_val and a_val != d_val:
                    review.append(f"- **{p['title']}** (`{p['slug']}`): pre-existing mismatch on `{field}` — d359=`{d_val}` vs Apple=`{a_val}` (first sync, not auto-resolving)")
                    resolved[field] = d_val  # keep d359's value in snapshot; don't overwrite either side
                else:
                    resolved[field] = a_val or d_val
                continue

            # normal 3-way diff against snapshot
            a_changed = a_val != s_val
            d_changed = d_val != s_val
            if not a_changed and not d_changed:
                resolved[field] = s_val
            elif a_changed and not d_changed:
                fills_to_d359.append((p, field, a_val))
                resolved[field] = a_val
            elif d_changed and not a_changed:
                fills_to_apple.append((p, contact, field, d_val))
                resolved[field] = d_val
            elif a_val == d_val:
                resolved[field] = a_val
            else:
                conflicts.append((p, field, s_val, a_val, d_val))
                resolved[field] = s_val  # neither side touched until resolved

        state["contacts"][aid] = {"d359_path": str(p["path"]), "last_synced": resolved}

    # --- unlinked Apple contacts -> registry candidates ---
    linked_ids = {c.get("apple_contact_id") for c in profiles if c.get("apple_contact_id")}
    unlinked_apple = [c for c in apple_contacts if c["id"] not in linked_ids
                       and (c["phones"] or c["emails"])]

    # --- report ---
    print(f"d359 profiles: {len(profiles)} | Apple contacts: {len(apple_contacts)}")
    print(f"linked: {linked} (newly linked this run: {newly_linked})")
    print(f"unlinked d359 profiles: no-Apple-match={no_match}, ambiguous={ambiguous}")
    print(f"unlinked Apple contacts (candidates for registry): {len(unlinked_apple)}")
    print(f"fills -> d359: {len(fills_to_d359)}")
    print(f"fills -> Apple: {len(fills_to_apple)}")
    print(f"conflicts (not auto-resolved): {len(conflicts)}")
    print(f"review items: {len(review)}")
    print(f"mode: {'APPLY' if apply_changes else 'DRY-RUN (nothing written)'}")

    if conflicts:
        print("\n--- conflicts ---")
        for p, field, s, a, d in conflicts[:20]:
            print(f"  {p['slug']}: {field} — snapshot={s!r} apple={a!r} d359={d!r}")

    if not apply_changes:
        print("\nRe-run with --apply to write these changes.")
        return

    # --- apply: write d359 frontmatter fills ---
    touched_paths = set()
    by_path = {}
    for p, field, val in fills_to_d359:
        by_path.setdefault(p["path"], (p, {}))[1][field] = val
    for path, (p, updates) in by_path.items():
        text = path.read_text(encoding="utf-8")
        fm, body, had_fm = split_frontmatter(text)
        # patch channels: block textually — find or create it
        chan_idx = next((i for i, ln in enumerate(fm) if re.match(r"^channels\s*:\s*$", ln)), None)
        if chan_idx is None:
            fm.append("channels:")
            chan_idx = len(fm) - 1
        for field, val in updates.items():
            sub_pat = re.compile(rf"^\s+{field}\s*:")
            j = chan_idx + 1
            found = False
            while j < len(fm) and (fm[j].strip() == "" or re.match(r"^\s", fm[j])):
                if sub_pat.match(fm[j]):
                    fm[j] = f"  {field}: {val}"
                    found = True
                    break
                j += 1
            if not found:
                fm.insert(chan_idx + 1, f"  {field}: {val}")
        if p["apple_contact_id"]:
            aid_idx = next((i for i, ln in enumerate(fm) if re.match(r"^apple_contact_id\s*:", ln)), None)
            line = f'apple_contact_id: "{p["apple_contact_id"]}"'
            if aid_idx is not None:
                fm[aid_idx] = line
            else:
                fm.append(line)
        path.write_text("---\n" + "\n".join(fm) + "\n---\n" + body, encoding="utf-8")
        touched_paths.add(path)

    # newly-linked profiles with no field fills still need apple_contact_id written
    for p in profiles:
        if p["apple_contact_id"] and p["path"] not in touched_paths:
            text = p["path"].read_text(encoding="utf-8")
            fm, body, had_fm = split_frontmatter(text)
            if not any(re.match(r"^apple_contact_id\s*:", ln) for ln in fm):
                fm.append(f'apple_contact_id: "{p["apple_contact_id"]}"')
                p["path"].write_text("---\n" + "\n".join(fm) + "\n---\n" + body, encoding="utf-8")

    # --- apply: write Apple-side updates via contacts-bridge ---
    if fills_to_apple:
        updates_json = []
        for p, contact, field, val in fills_to_apple:
            label_map = {"phone": ("mobile", "setPhones"), "email": ("home", "setEmails"), "work_email": ("work", "setEmails")}
            label, key = label_map[field]
            existing = next((u for u in updates_json if u["id"] == contact["id"]), None)
            if existing is None:
                existing = {"id": contact["id"], "setPhones": {}, "setEmails": {}}
                updates_json.append(existing)
            existing[key][label] = val
        tmp = STATE_DIR / "pending-apple-updates.json"
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(updates_json))
        r = subprocess.run([str(BRIDGE), "apply", str(tmp)], capture_output=True, text=True)
        print("\ncontacts-bridge apply:", r.stdout.strip() or r.stderr.strip())

    # --- registry rows for unlinked Apple contacts ---
    if unlinked_apple and REGISTRY.is_file():
        reg_text = REGISTRY.read_text(encoding="utf-8")
        new_rows = []
        for c in unlinked_apple:
            name = f"{c['givenName']} {c['familyName']}".strip()
            if not name or name in reg_text:
                continue
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            vals = apple_field_values(c)
            new_rows.append(f"| {slug} | {name} |  | {vals.get('email') or ''} | {vals.get('work_email') or ''} | {vals.get('phone') or ''} |  | apple-contacts-sync |  |")
        if new_rows:
            reg_text = reg_text.rstrip() + "\n" + "\n".join(new_rows) + "\n"
            REGISTRY.write_text(reg_text, encoding="utf-8")
            print(f"\nregistry: added {len(new_rows)} new rows")

    # --- write review/conflict files ---
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if review:
        REVIEW_FILE.write_text("# d359 Contacts Sync — Needs Review\n\n" + "\n".join(review) + "\n")
        print(f"\nreview items written to {REVIEW_FILE}")
    if conflicts:
        lines = [f"- **{p['title']}** (`{p['slug']}`): `{field}` — snapshot={s!r} apple={a!r} d359={d!r}" for p, field, s, a, d in conflicts]
        CONFLICTS_FILE.write_text("# d359 Contacts Sync — Conflicts\n\n" + "\n".join(lines) + "\n")
        print(f"conflicts written to {CONFLICTS_FILE}")

    save_state(state, apply_changes)
    print(f"\nstate saved to {STATE_FILE}")


if __name__ == "__main__":
    main()
