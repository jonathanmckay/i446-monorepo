#!/usr/bin/env python3
"""-1g suggestion eval rollup for the -1₦ heatmap.

Usage: suggest_eval.py START END   (YYYY-MM-DD inclusive)

Reads every `vault/g245/v_logs/-1g-suggest.*.jsonl` record written by the
/-2n / /inbound goal card (tools/ibx/-2n.py log_1g_suggestions) and prints
markdown:
  1. response mix: pick / mixed / custom / skip / keep
  2. pick rate per source (cal, 1g <dom>, 0g, 0n, todoist) = picked / shown
  3. pick rate per block
  4. reason codes typed on skip/custom (-w wrong domain, -s stale, -b not 2h, -d dup)
  5. pool coverage of custom goals: typed goal fuzzy-matched a shown candidate
     (ranker outranked it) vs absent from the pool (a source is missing)
  6. outcome: picked goals that ended the block `- [x]` in that day's
     build-order snapshot (archive/YYYY/YYYY.MM.DD/build-order.md, v_logs, or
     the live file for today).
The user's pick is the label; no judge model. Degrades per-section on error.
"""
from __future__ import annotations

import datetime as dt
import difflib
import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict

V_LOGS = os.path.expanduser('~/vault/g245/v_logs')
ARCHIVE_ROOT = os.path.expanduser('~/vault/g245/archive')
LIVE_BUILD_ORDER = os.path.expanduser('~/vault/g245/5e-1/build-order.md')
MATCH_RATIO = 0.8  # outcome: snapshot line vs picked content
COVER_RATIO = 0.6  # coverage: typed custom goal vs shown candidate

_STRIP = re.compile(r'\{\d+\}|\[\d+\]|\(\d{1,2}:\d{2}\)|\(\d+\)|@\w+|[✓✔]|\s+')


def normalize_goal(text: str) -> str:
    """Casefold and drop {N}/[N]/(N)/(HH:MM)/@dom/✓ and all whitespace so a
    snapshot line `prep for Standup (09:00) {10}` matches the suggestion
    `prep for Standup (09:00)` and the typed `Prep standup`."""
    return _STRIP.sub('', (text or '').casefold())


def similar(a: str, b: str) -> float:
    a, b = normalize_goal(a), normalize_goal(b)
    if not a or not b:
        return 0.0
    if a == b or a in b or b in a:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def load_records(start: dt.date, end: dt.date, log_dir: str = V_LOGS) -> list[dict]:
    recs = []
    for path in sorted(glob.glob(os.path.join(log_dir, '-1g-suggest.*.jsonl'))):
        with open(path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    d = dt.date.fromisoformat(r['date'])
                except (ValueError, KeyError):
                    continue
                if start <= d <= end:
                    recs.append(r)
    recs.sort(key=lambda r: r.get('ts', ''))
    return recs


def source_family(source: str) -> str:
    """`1g m5c7` → `1g`; everything else as-is."""
    return (source or 'todoist').split()[0]


def response_mix(recs):
    return Counter(r.get('response_kind', '?') for r in recs)


def pick_rates_by_source(recs):
    shown, picked = Counter(), Counter()
    for r in recs:
        picks = set(r.get('picks') or [])
        for i, s in enumerate(r.get('suggestions') or [], 1):
            fam = source_family(s.get('source'))
            shown[fam] += 1
            if i in picks:
                picked[fam] += 1
    return shown, picked


def pick_rates_by_block(recs):
    shown, picked = Counter(), Counter()
    for r in recs:
        b = r.get('block', '?')
        if not r.get('suggestions'):
            continue
        shown[b] += 1
        if r.get('picks'):
            picked[b] += 1
    return shown, picked


def coverage(recs):
    """For each custom goal: 'outranked' if a shown candidate matches, else 'absent'."""
    out = Counter()
    examples = []
    for r in recs:
        cands = [s.get('content', '') for s in (r.get('suggestions') or [])]
        for g in r.get('custom') or []:
            best = max((similar(g, c) for c in cands), default=0.0)
            key = 'outranked' if best >= COVER_RATIO else 'absent'
            out[key] += 1
            if key == 'absent' and len(examples) < 5:
                examples.append(f"{r.get('date')} {r.get('block')}: {g}")
    return out, examples


# ---------- outcome: picked goal `- [x]` in the day's snapshot ----------

def _snapshot_path(day: dt.date):
    stamp = day.strftime('%Y.%m.%d')
    for p in (f"{ARCHIVE_ROOT}/{day.year}/{stamp}/build-order.md",
              f"{V_LOGS}/{stamp}-build-order.md"):
        if os.path.exists(p):
            return p
    if day == dt.date.today() and os.path.exists(LIVE_BUILD_ORDER):
        return LIVE_BUILD_ORDER
    return None


def block_goal_lines(text: str) -> dict[str, list[tuple[bool, str]]]:
    """{block: [(done, goal_text), ...]} from the `## -1₲` section."""
    out: dict[str, list[tuple[bool, str]]] = defaultdict(list)
    if '## -1₲' not in text:
        return out
    section = text[text.index('## -1₲'):]
    block = None
    for line in section.split('\n'):
        if line.startswith('## ') and line.strip() != '## -1₲':
            break
        if line.startswith('- ') and not line.startswith('    '):
            rest = line[2:].strip()
            block = rest.split()[0] if rest else None
            continue
        m = re.match(r'^\s*- \[([ xX])\]\s*(\S.*)$', line)
        if block and m:
            out[block].append((m.group(1).lower() == 'x', m.group(2).strip()))
    return out


def outcomes(recs, snapshot_loader=None):
    """Counter(done / not_done / unmatched) over picked suggestions."""
    load = snapshot_loader or _default_snapshot_loader
    res = Counter()
    cache: dict[dt.date, dict] = {}
    for r in recs:
        picks = r.get('picks') or []
        if not picks:
            continue
        try:
            day = dt.date.fromisoformat(r['date'])
        except (KeyError, ValueError):
            continue
        if day not in cache:
            cache[day] = load(day)
        goals = cache[day].get(r.get('block'), [])
        sugg = r.get('suggestions') or []
        for i in picks:
            if not 1 <= i <= len(sugg):
                continue
            content = sugg[i - 1].get('content', '')
            best, best_done = 0.0, False
            for done, g in goals:
                sc = similar(content, g)
                if sc > best:
                    best, best_done = sc, done
            if best < MATCH_RATIO:
                res['unmatched'] += 1
            else:
                res['done' if best_done else 'not_done'] += 1
    return res


def _default_snapshot_loader(day: dt.date) -> dict:
    p = _snapshot_path(day)
    if not p:
        return {}
    try:
        return block_goal_lines(open(p, encoding='utf-8').read())
    except OSError:
        return {}


# ---------- render ----------

def _pct(n, d):
    return f"{100 * n / d:.0f}%" if d else '–'


def render(recs, start: dt.date, end: dt.date) -> str:
    lines = [f"## -1g suggestion eval ({start.isoformat()} .. {end.isoformat()})", '']
    if not recs:
        lines.append('_No suggestion records in this range._')
        return '\n'.join(lines)
    mix = response_mix(recs)
    n = len(recs)
    lines.append(f"{n} cards shown · "
                 + ' · '.join(f"{k} {mix[k]} ({_pct(mix[k], n)})"
                              for k in ('pick', 'mixed', 'custom', 'skip', 'keep') if mix.get(k)))
    lines.append('')

    shown, picked = pick_rates_by_source(recs)
    lines += ['| source | shown | picked | rate |', '|---|---:|---:|---:|']
    for src, c in sorted(shown.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {src} | {c} | {picked[src]} | {_pct(picked[src], c)} |")
    lines.append('')

    bshown, bpicked = pick_rates_by_block(recs)
    order = ['卯', '辰', '巳', '午', '未', '申', '酉', '戌', '亥']
    lines += ['| block | cards | any pick | rate |', '|---|---:|---:|---:|']
    for b in sorted(bshown, key=lambda x: order.index(x) if x in order else 99):
        lines.append(f"| {b} | {bshown[b]} | {bpicked[b]} | {_pct(bpicked[b], bshown[b])} |")
    lines.append('')

    reasons = Counter(r['reason'] for r in recs if r.get('reason'))
    if reasons:
        lines.append('Reasons: ' + ', '.join(f"{k} {v}" for k, v in reasons.most_common()))
        lines.append('')

    cov, examples = coverage(recs)
    tot = sum(cov.values())
    if tot:
        lines.append(f"Custom goals {tot}: outranked {cov['outranked']} ({_pct(cov['outranked'], tot)}) · "
                     f"absent from pool {cov['absent']} ({_pct(cov['absent'], tot)})")
        for e in examples:
            lines.append(f"- absent: {e}")
        lines.append('')

    try:
        oc = outcomes(recs)
    except Exception as e:  # noqa: BLE001
        oc = Counter()
        lines.append(f"<!-- outcome warning: {e} -->")
    pt = sum(oc.values())
    if pt:
        lines.append(f"Picked {pt}: done by block end {oc['done']} ({_pct(oc['done'], pt)}) · "
                     f"not done {oc['not_done']} · unmatched in snapshot {oc['unmatched']}")
        lines.append('')
    lines.append('_Read: outranked = ranker weights wrong; absent = a source is missing. '
                 'Pick rate is a preference label, not an outcome._')
    return '\n'.join(lines)


def main():
    if len(sys.argv) >= 3:
        start, end = (dt.date.fromisoformat(a) for a in sys.argv[1:3])
    else:
        end = dt.date.today() - dt.timedelta(days=1)
        start = end - dt.timedelta(days=6)
    print(render(load_records(start, end), start, end))


if __name__ == '__main__':
    main()
