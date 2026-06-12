#!/usr/bin/env python3
"""Generate the -1n (-1₦) block-ritual heatmap: day x 2-hour Earthly-Branch block,
showing which build-order ritual icons were hit. Multi-source:
  Toggl       -> ☀️ prayer, 📧 inbox, ⏱️ time-log  (bucketed by entry start time)
  Build order -> 🎯 goal set for the block (-1₲ checkbox; v_logs archives + live)
  Todoist     -> ✓ task completed (completed_at bucketed by block)
Usage: make_heatmap.py START END   (YYYY-MM-DD inclusive). Prints markdown.
"""
import sys, os, json, datetime as dt, urllib.request, base64, re
from collections import defaultdict

PT = dt.timezone(dt.timedelta(hours=-7))  # America/Los_Angeles (DST, summer)

# 2-hour blocks by local start hour (06:00..00:00)
BLOCKS = [(6,'卯'),(8,'辰'),(10,'巳'),(12,'午'),(14,'未'),(16,'申'),(18,'酉'),(20,'戌'),(22,'亥')]
BRANCHES = [b for _,b in BLOCKS]
BIDX = {b:i for i,b in enumerate(BRANCHES)}
def block_of(hour):
    for start,b in BLOCKS:
        if start <= hour < start+2:
            return b
    return None

ICON_ORDER = ['☀️','📧','🎯','⏱️','✓']

GOALS_T = {'-1t','0t','-1l','0l'}  # time-log ritual descriptions
PRAYERS = {'الفاتحة','الشمس','النور','الظهر','العصر','المغرب','العشاء','الفجر'}

# ---------- Toggl ----------
def toggl_entries(start, end):
    sys.path.insert(0, '/Users/mckay/i446-monorepo/mcp/toggl_server')
    import toggl_cli  # noqa: loads API key
    from toggl_server import toggl_api as t
    from toggl_server.config import PROJECT_NAMES
    s = start.isoformat()+'T00:00:00-07:00'
    e = (end+dt.timedelta(days=1)).isoformat()+'T00:00:00-07:00'
    out = []
    for x in t.get_entries(s, e):
        st = x.get('start')
        if not st: continue
        try:
            d0 = dt.datetime.fromisoformat(st.replace('Z','+00:00'))
        except Exception:
            continue
        loc = d0.astimezone(PT)
        out.append((loc, (x.get('description') or '').strip(),
                    PROJECT_NAMES.get(x.get('project_id'), '')))
    return out

def add_toggl(grid, start, end):
    for loc, desc, proj in toggl_entries(start, end):
        day = loc.date()
        if day < start or day > end: continue
        b = block_of(loc.hour)
        if b is None: continue
        dl = desc.lower()
        if desc in PRAYERS or (proj == 'hcm' and any('\u0600' <= c <= '\u06FF' for c in desc)):
            grid[day][b].add('☀️')
        if dl.startswith('ibx'):
            grid[day][b].add('📧')
        if desc in GOALS_T:
            grid[day][b].add('⏱️')

# ---------- Build-order goals -> 🎯 ----------
# 🎯 = a -1g goal was actually SET for the block: the day's build order has a
# non-empty checkbox under that block's header in the ## -1₲ section. Past
# days come from the daily archives (v_logs), today from the live file.
# (Until 2026-06-12 this read per-block 分 from the Neon sheet, which lit 🎯
# for any block where points landed, regardless of whether a goal was set.)
V_LOGS = '/Users/mckay/vault/g245/v_logs'
LIVE_BUILD_ORDER = '/Users/mckay/vault/g245/build-order.md'

def _block_line_name(line):
    """First token after the bullet — headers carry variable annotations
    (`- 辰 (25min)   (32min) ⏰`), so only the leading token is stable."""
    rest = line[2:].strip()
    return rest.split()[0] if rest else ''

def add_goals(grid, start, end):
    today = dt.datetime.now(PT).date()
    day = start
    while day <= end:
        path = f"{V_LOGS}/{day.strftime('%Y.%m.%d')}-build-order.md"
        if day == today and not os.path.exists(path):
            path = LIVE_BUILD_ORDER
        try:
            text = open(path, encoding='utf-8').read()
        except OSError:
            day += dt.timedelta(days=1)
            continue
        if '## -1₲' in text:
            section = text[text.index('## -1₲'):]
            block = None
            for line in section.split('\n'):
                if line.startswith('## ') and line.strip() != '## -1₲':
                    break
                if line.startswith('- ') and not line.startswith('    '):
                    name = _block_line_name(line)
                    block = name if name in BIDX else None
                elif block and re.match(r'^\s*- \[[ xX]\]\s*\S', line):
                    grid[day][block].add('🎯')
        day += dt.timedelta(days=1)

# ---------- Todoist completed -> ✓ ----------
def add_todoist(grid, start, end):
    token = '7eb82f47aba8b334769351368e4e3e3284f980e5'
    since = start.isoformat()+'T00:00:00Z'
    until = (end+dt.timedelta(days=1)).isoformat()+'T00:00:00Z'
    url = (f'https://api.todoist.com/api/v1/tasks/completed'
           f'?since={since}&until={until}&limit=200')
    items = []
    while url:
        req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
        with urllib.request.urlopen(req, timeout=30) as resp:
            d = json.loads(resp.read().decode())
        items.extend(d.get('items', []))
        nxt = d.get('next_cursor')
        url = (f'https://api.todoist.com/api/v1/tasks/completed'
               f'?since={since}&until={until}&limit=200&cursor={nxt}') if nxt else None
    for it in items:
        ca = it.get('completed_at')
        if not ca: continue
        try:
            d0 = dt.datetime.fromisoformat(ca.replace('Z','+00:00'))
        except Exception:
            continue
        loc = d0.astimezone(PT)
        day = loc.date()
        if day < start or day > end: continue
        b = block_of(loc.hour)
        if b is None: continue
        grid[day][b].add('✓')

# ---------- render ----------
DOW = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']
KEY = [('☀️','prayer (salah)'),('📧','inbox processed'),('🎯','goal set for block (-1g)'),
       ('⏱️','time logged'),('✓','task completed (Todoist)')]

def iso_week(d):
    y,w,_ = d.isocalendar()
    return f'{y}-W{w:02d}'

def render(grid, start, end):
    L = []
    sr = f'{start.strftime("%b %-d")} - {end.strftime("%b %-d, %Y")}'
    L.append(f'## -1₦ Block Ritual Completion ({sr})')
    L.append('')
    L.append('Each cell shows which build-order rituals were hit in that 2-hour block.')
    L.append('')
    L.append('### Key')
    L.append('')
    L.append('| Symbol | Meaning |')
    L.append('|--------|---------|')
    for s,m in KEY:
        L.append(f'| {s} | {m} |')
    L.append('')
    L.append('| Date | Day | '+' | '.join(BRANCHES)+' |')
    L.append('|------|-----|'+'------|'*len(BRANCHES))
    d = start
    while d <= end:
        cells = []
        for b in BRANCHES:
            ics = grid.get(d, {}).get(b, set())
            cells.append(''.join(i for i in ICON_ORDER if i in ics))
        L.append(f'| {d.month}/{d.day} | {DOW[d.weekday()]} | '+' | '.join(cells)+' |')
        d += dt.timedelta(days=1)
    L.append('')
    # weekly density
    wk = defaultdict(lambda: [0,0])  # week -> [days, actions]
    perday = defaultdict(int)
    d = start
    ndays = 0
    while d <= end:
        ndays += 1
        n = sum(len(grid.get(d,{}).get(b,set())) for b in BRANCHES)
        perday[d] = n
        w = iso_week(d)
        wk[w][0] += 1; wk[w][1] += n
        d += dt.timedelta(days=1)
    L.append('### Weekly Ritual Density')
    L.append('')
    L.append('| Week | Days | Avg/Day | Total |')
    L.append('|------|-----:|--------:|------:|')
    for w in sorted(wk):
        days, tot = wk[w]
        L.append(f'| {w} | {days} | {tot/days:.1f} | {tot} |')
    L.append('')
    # hit rate by block
    L.append('### Ritual Hit Rate by Block')
    L.append('')
    L.append('Percentage of days each ritual appeared in each block:')
    L.append('')
    L.append('| Ritual | '+' | '.join(BRANCHES)+' | **Avg** |')
    L.append('|--------|'+'-----:|'*len(BRANCHES)+'------:|')
    for sym, name in KEY:
        rates = []
        for b in BRANCHES:
            cnt = sum(1 for d in grid if sym in grid[d].get(b,set()))
            rates.append(cnt/ndays*100 if ndays else 0)
        cells = [(f'{r:.0f}%' if r else '—') for r in rates]
        avg = sum(rates)/len(rates) if rates else 0
        L.append(f'| {sym} {name.split(" (")[0]} | '+' | '.join(cells)+f' | {avg:.0f}% |')
    L.append('')
    L.append(f'Generated {dt.date.today().strftime("%Y.%m.%d")}.')
    return '\n'.join(L)

def main():
    if len(sys.argv) >= 3:
        start = dt.date.fromisoformat(sys.argv[1]); end = dt.date.fromisoformat(sys.argv[2])
    else:
        end = dt.date.today() - dt.timedelta(days=1)
        start = end - dt.timedelta(days=6)
    grid = defaultdict(lambda: defaultdict(set))
    errs = []
    for fn in (add_toggl, add_goals, add_todoist):
        try:
            fn(grid, start, end)
        except Exception as e:
            errs.append(f'{fn.__name__}: {e}')
    out = render(grid, start, end)
    if errs:
        out += '\n\n<!-- source warnings: ' + '; '.join(errs) + ' -->'
    print(out)

if __name__ == '__main__':
    main()
