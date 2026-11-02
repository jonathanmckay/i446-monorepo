#!/usr/bin/env python3
"""Week-over-week 分 comparison for the -1₦ heatmap.

Usage: fen_compare.py START END   (YYYY-MM-DD inclusive; the current week)
Compares START..END against the 7 days before START. Prints markdown:
  1. per-week 0分 totals (Σ, -1₦ P, 0g Q) and ritual density
  2. per-day Σ for both weeks side by side
  3. mean per-block 分 (0分 G:O) for blocks with vs without each ritual,
     pooled over both weeks. Correlational: rituals cluster in blocks
     where JM is awake and at a desk, so read it as a direction, not an effect size.
Reads Neon read-only; runs make_heatmap.py for the ritual cells.
"""
import sys, os, re, subprocess, datetime as dt, statistics as st
import openpyxl

HERE = os.path.dirname(os.path.abspath(__file__))
NEON = os.path.expanduser('~/OneDrive/vault-excel/Neon分v12.2.xlsx')
RITUALS = [('☀️', 'prayer'), ('📧', 'inbox'), ('🎯', 'goal'), ('⏱️', 'time logged')]


def heatmap_cells(start, end):
    out = subprocess.run([sys.executable, os.path.join(HERE, 'make_heatmap.py'),
                          start.isoformat(), end.isoformat()],
                         capture_output=True, text=True, check=True).stdout
    cells, density = {}, None
    for line in out.splitlines():
        m = re.match(r'\| (\d+/\d+) \| \w+ \|(.*)\|\s*$', line)
        if m:
            cells[m.group(1)] = [c.strip() for c in m.group(2).split('|')]
        m = re.match(r'\| \d{4}-W\d+ \| *\d+ \| *([\d.]+) \|', line)
        if m:
            density = float(m.group(1))
    return cells, density


def fen_rows(start, end):
    ws = openpyxl.load_workbook(NEON, read_only=True, data_only=True)['0分']
    rows = {}
    for r in ws.iter_rows(min_row=3, values_only=True):
        b = r[1] if len(r) > 1 else None
        if isinstance(b, dt.datetime) and start <= b.date() <= end:
            n = lambda x: x if isinstance(x, (int, float)) else 0
            rows[b.date()] = {'sum': n(r[3]), 'blocks': [n(x) for x in r[6:15]],
                              'P': n(r[15]), 'Q': n(r[16])}
    return rows


def key(d):
    return f'{d.month}/{d.day}'


def main():
    cur_s, cur_e = (dt.date.fromisoformat(a) for a in sys.argv[1:3])
    prev_s, prev_e = cur_s - dt.timedelta(7), cur_s - dt.timedelta(1)
    fen = fen_rows(prev_s, cur_e)
    weeks = []
    for s, e in [(prev_s, prev_e), (cur_s, cur_e)]:
        cells, dens = heatmap_cells(s, e)
        days = [s + dt.timedelta(i) for i in range((e - s).days + 1)]
        tot = lambda f: sum(fen.get(d, {}).get(f, 0) for d in days)
        weeks.append(dict(s=s, e=e, cells=cells, dens=dens, days=days,
                          sum=tot('sum'), P=tot('P'), Q=tot('Q')))
    p, c = weeks
    pct = lambda a, b: f'{(b - a) / a:+.0%}' if a else 'n/a'
    lbl = lambda w: f"{w['s']:%m/%d}–{w['e']:%m/%d}"
    print('### Week-over-week\n')
    print(f'| Metric | {lbl(p)} | {lbl(c)} | Δ |\n|---|---:|---:|---:|')
    print(f"| Ritual density (/day) | {p['dens']} | {c['dens']} | {pct(p['dens'], c['dens'])} |")
    for f, name in [('sum', 'Σ 分'), ('P', '-1₦ 分 (P)'), ('Q', '0g 分 (Q)')]:
        print(f"| {name} | {p[f]:,.0f} | {c[f]:,.0f} | {pct(p[f], c[f])} |")
    print('\n| Day | ' + ' | '.join(f'{d:%a}' for d in p['days']) + ' |')
    print('|---|' + '---:|' * len(p['days']))
    for w in weeks:
        print(f"| {w['s']:%m/%d} wk Σ | " +
              ' | '.join(f"{fen.get(d, {}).get('sum', 0):,.0f}" for d in w['days']) + ' |')

    pool = []
    for w in weeks:
        for d in w['days']:
            for i, cell in enumerate(w['cells'].get(key(d), [])):
                if d in fen:
                    pool.append((cell, fen[d]['blocks'][i]))
    print('\n### Block 分 with vs without each ritual (both weeks pooled)\n')
    print('| Ritual | n with | mean 分 with | n without | mean 分 without |\n|---|---:|---:|---:|---:|')
    for sym, name in RITUALS + [('', 'empty block')]:
        hit = (lambda cl: cl == '') if not sym else (lambda cl, s=sym: s in cl)
        a = [v for cl, v in pool if hit(cl)]
        b = [v for cl, v in pool if not hit(cl)]
        if a and b:
            print(f'| {sym} {name} | {len(a)} | {st.mean(a):.0f} | {len(b)} | {st.mean(b):.0f} |')
    print('\n_Correlational: rituals cluster in blocks spent awake at a desk._')


if __name__ == '__main__':
    main()
