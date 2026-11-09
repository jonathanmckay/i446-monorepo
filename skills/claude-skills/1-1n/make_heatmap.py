#!/usr/bin/env python3
"""Generate the -1n (-1₦) block-ritual heatmap: day x 2-hour Earthly-Branch block,
showing which build-order ritual icons were hit. Multi-source:
  Toggl       -> ⏱️ time-log, 📧 inbox fallback  (bucketed by entry start time)
  Build order -> 🎯 goal set (-1₲ checkbox), ☀️ prayer + 📧 inbox (header stamps); archive + live
  Todoist     -> ✓ task completed (completed_at bucketed by block)
Usage: make_heatmap.py START END   (YYYY-MM-DD inclusive). Prints markdown.
"""
import sys, os, json, datetime as dt, urllib.request, base64, re
from collections import defaultdict

sys.path.insert(0, str(os.path.expanduser('~/i446-monorepo/lib')))
from blocks import is_future_block  # shared future-block gate (single source of truth)

from zoneinfo import ZoneInfo
PT = ZoneInfo("America/Los_Angeles")  # DST-aware (was fixed -07:00)

# 2-hour blocks by local start hour (04:00..22:00). 卯 = 04-06: the convention
# the build order and 0分 sheet are written with (see build-order-daemon). Local
# table (waking blocks only, no sleep); canonical schedule + the future-block
# gate live in lib/blocks.py (is_future_block).
BLOCKS = [(4,'卯'),(6,'辰'),(8,'巳'),(10,'午'),(12,'未'),(14,'申'),(16,'酉'),(18,'戌'),(20,'亥')]
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
        # ☀️ prayer now comes from build-order header stamps (add_goals), not
        # Toggl: the header stamp is the authoritative ritual marker, and a
        # prayer can be marked without a timed Toggl entry.
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
# Since 2026-07-14 the nightly archive lands in archive/2026/<date>/ instead
# of v_logs (v_logs stops at 07.13) — the 0% ☀️/🎯 week of Jul 18-24 was this
# path going stale, not missing rituals. Try both, newest scheme first.
ARCHIVE_ROOT = '/Users/mckay/vault/g245/archive'
LIVE_BUILD_ORDER = '/Users/mckay/vault/g245/5e-1/build-order.md'


def _snapshot_path(day):
    stamp = day.strftime('%Y.%m.%d')
    candidates = [
        f"{ARCHIVE_ROOT}/{day.year}/{stamp}/build-order.md",
        f"{V_LOGS}/{stamp}-build-order.md",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None

def _block_line_name(line):
    """First token after the bullet — headers carry variable annotations
    (`- 辰 (25min)   (32min) 😈`), so only the leading token is stable."""
    rest = line[2:].strip()
    return rest.split()[0] if rest else ''

def add_goals(grid, start, end):
    today = dt.datetime.now(PT).date()
    day = start
    while day <= end:
        path = _snapshot_path(day)
        if path is None and day == today:
            path = LIVE_BUILD_ORDER
        if path is None:
            day += dt.timedelta(days=1)
            continue
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
                    # ☀️ prayer: stamp on the block header (written by /ص,
                    # prayer_marker, did-fast). Authoritative ritual marker.
                    if block and '☀️' in line:
                        grid[day][block].add('☀️')
                    # 📧 inbox: header stamp written by the -1ibx card close
                    # (did-fast run_ritual), -2n/inbound, and did-fast's ibx
                    # habit marks (2026-11-09). Toggl `ibx` entries remain a
                    # fallback in add_toggl; the week of Nov 2-8 had 38/63
                    # stamped headers against 3 Toggl entries.
                    if block and '📧' in line:
                        grid[day][block].add('📧')
                elif block and re.match(r'^\s*- \[[ xX]\]\s*\S', line):
                    grid[day][block].add('🎯')
        day += dt.timedelta(days=1)

# ---------- Todoist completed -> ✓ ----------
def add_todoist(grid, start, end):
    token = open(os.path.expanduser('~/.config/todoist/token')).read().strip()
    # One query PER DAY (2026-10-05): the endpoint returns at most 200 items
    # for the whole since/until window and no next_cursor, so a busy week came
    # back as only its newest ~3 days and the early days showed no ✓ at all.
    # Day windows are padded by a day each side (UTC vs PT) and de-duplicated
    # by id; the PT-day filter below keeps only the requested range.
    items, seen = [], set()
    day = start - dt.timedelta(days=1)
    while day <= end + dt.timedelta(days=1):
        since = day.isoformat()+'T00:00:00Z'
        until = (day+dt.timedelta(days=1)).isoformat()+'T00:00:00Z'
        url = (f'https://api.todoist.com/api/v1/tasks/completed'
               f'?since={since}&until={until}&limit=200')
        while url:
            req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
            with urllib.request.urlopen(req, timeout=30) as resp:
                d = json.loads(resp.read().decode())
            for it in d.get('items', []):
                key = (it.get('id') or it.get('task_id'), it.get('completed_at'))
                if key not in seen:
                    seen.add(key)
                    items.append(it)
            nxt = d.get('next_cursor')
            url = (f'https://api.todoist.com/api/v1/tasks/completed'
                   f'?since={since}&until={until}&limit=200&cursor={nxt}') if nxt else None
        if len(d.get('items', [])) >= 200:
            sys.stderr.write(f'warning: {day} hit the 200-item cap; ✓ may undercount\n')
        day += dt.timedelta(days=1)
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

# ---------- Interface -> 🖥️ desk / 📱 mobile ----------
# Which interface JM drove the system through in each block (2026-11-02).
# Source: the synced source-log (lib/jmsource.py: every first-party Todoist /
# Toggl write plus every Claude prompt, with host, net, ssh_from, tty) and
# phone captures. Third-party app writes (Todoist/Toggl's own apps) carry no
# device and are ignored. Laptop = desk only on highgarden (JM). Rules and
# device IPs: config/devices.json.
DESK, MOBILE = '🖥️', '📱'
SOURCE_LOG = '/Users/mckay/vault/i447/i446/source-log'
DEVICES_JSON = '/Users/mckay/i446-monorepo/config/devices.json'
Z_IBX = '/Users/mckay/vault/z_ibx'


def _devices():
    cfg = json.load(open(DEVICES_JSON))
    kind_by_ip, kind_by_host = {}, {}
    for name, d in cfg.get('devices', {}).items():
        for ip in d.get('ips', []):
            kind_by_ip[ip] = d['kind']
        for h in d.get('hosts', [name]):
            kind_by_host[h] = d['kind']
    lans = []
    import ipaddress
    for n in cfg.get('networks', {}).values():
        if n.get('lan'):
            lans.append(ipaddress.ip_network(n['lan']))
    return cfg, kind_by_ip, kind_by_host, lans


def classify_row(r, kind_by_ip, kind_by_host, lans, daemon_vias):
    """One source-log row -> DESK, MOBILE or None (no device signal)."""
    import ipaddress
    if r.get('via') in daemon_vias or r.get('ok') is False:
        return None
    if r.get('source') in ('1p-app', 'watch'):
        return MOBILE
    # Rows since 2026-11-02 carry `tty`; a non-interactive write (cron, a
    # backgrounded daemon) says nothing about where JM was. Prompts are typed.
    if 'tty' in r and not r['tty'] and r.get('metric') != 'prompt':
        return None
    ssh = r.get('ssh_from')
    if ssh:
        k = kind_by_ip.get(ssh)
        if k == 'mobile':
            return MOBILE
        if k == 'desk':
            return DESK
        try:
            if any(ipaddress.ip_address(ssh) in n for n in lans):
                return DESK   # client on the home LAN = on highgarden
        except ValueError:
            pass
        return None           # laptop over Tailscale: may be away
    k = kind_by_host.get(r.get('host'))
    if k == 'desk':
        return DESK
    if k == 'laptop':
        return DESK if r.get('net') == 'highgarden' else None
    if k == 'mobile':
        return MOBILE
    return None


def add_device(dev, start, end):
    cfg, by_ip, by_host, lans = _devices()
    daemons = set(cfg.get('daemon_vias', []))
    months = {f'{d.year}-{d.month:02d}' for d in (start, end)}
    import glob
    for path in glob.glob(f'{SOURCE_LOG}/*.jsonl'):
        if not any(path.endswith(f'{m}.jsonl') for m in months):
            continue
        for line in open(path, encoding='utf-8'):
            try:
                r = json.loads(line)
                loc = dt.datetime.fromisoformat(r['ts']).astimezone(PT)
            except Exception:
                continue
            day, b = loc.date(), block_of(loc.hour)
            if b is None or day < start or day > end:
                continue
            g = classify_row(r, by_ip, by_host, lans, daemons)
            if g:
                dev[day][b].add(g)
    # Phone-app captures: YYYY-MM-DD-HHMMSS-<id>-<device>.md, inbox or archive.
    import glob
    for p in glob.glob(f'{Z_IBX}/phone/*.md') + glob.glob(f'{Z_IBX}/archive/*/phone/*.md'):
        m = re.match(r'(\d{4}-\d{2}-\d{2})-(\d{2})\d{4}-', os.path.basename(p))
        if not m:
            continue
        day, b = dt.date.fromisoformat(m.group(1)), block_of(int(m.group(2)))
        if b and start <= day <= end:
            dev[day][b].add(MOBILE)


def device_glyph(s):
    return ''.join(g for g in (DESK, MOBILE) if g in s)


# ---------- render ----------
DOW = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']
KEY = [('☀️','prayer (salah)'),('📧','inbox processed'),('🎯','goal set for block (-1g)'),
       ('⏱️','time logged'),('✓','task completed (Todoist)')]
DEV_KEY = [(DESK,'desk: terminal/Claude on ix, or laptop on highgarden'),
           (MOBILE,'mobile: phone apps, phone captures, ssh from a phone')]

def iso_week(d):
    y,w,_ = d.isocalendar()
    return f'{y}-W{w:02d}'

def render(grid, start, end, dev=None):
    dev = dev or {}
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
    for s,m in KEY + DEV_KEY:
        L.append(f'| {s} | {m} |')
    L.append('')
    L.append('| Date | Day | '+' | '.join(BRANCHES)+' |')
    L.append('|------|-----|'+'------|'*len(BRANCHES))
    d = start
    while d <= end:
        cells = []
        for b in BRANCHES:
            ics = grid.get(d, {}).get(b, set())
            cell = ''.join(i for i in ICON_ORDER if i in ics)
            g = device_glyph(dev.get(d, {}).get(b, set()))
            cells.append(f'{cell} · {g}' if cell and g else (cell or g))
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
    for sym, name in DEV_KEY:
        rates = [sum(1 for d in dev if sym in dev[d].get(b, set()))/ndays*100 if ndays else 0
                 for b in BRANCHES]
        cells = [(f'{r:.0f}%' if r else '—') for r in rates]
        L.append(f'| {sym} {name.split(":")[0]} | '+' | '.join(cells)+f' | {sum(rates)/len(rates):.0f}% |')
    L.append('')
    L.append(f'Generated {dt.date.today().strftime("%Y.%m.%d")}.')
    return '\n'.join(L)

def prune_future(grid):
    """A ritual cannot be completed in a block that hasn't started. The live
    build order pre-stamps future block headers (☀️/🎯) and pre-writes goal
    text for the whole day, so add_goals would light future blocks. Clear, for
    today, any block whose start hour is still in the future (keep the in-
    progress block, start <= now < start+2). Without this, today's row reads as
    'complete' for blocks that haven't happened and over-counts vs Neon col P."""
    now = dt.datetime.now(PT)
    today = now.date()
    if today not in grid:
        return
    for start_h, b in BLOCKS:
        if is_future_block(start_h, now):  # block hasn't started yet
            grid[today].pop(b, None)

def main():
    if len(sys.argv) >= 3:
        start = dt.date.fromisoformat(sys.argv[1]); end = dt.date.fromisoformat(sys.argv[2])
    else:
        end = dt.date.today() - dt.timedelta(days=1)
        start = end - dt.timedelta(days=6)
    grid = defaultdict(lambda: defaultdict(set))
    errs = []
    dev = defaultdict(lambda: defaultdict(set))
    for fn, g in ((add_toggl, grid), (add_goals, grid), (add_todoist, grid), (add_device, dev)):
        try:
            fn(g, start, end)
        except Exception as e:
            errs.append(f'{fn.__name__}: {e}')
    prune_future(grid)
    prune_future(dev)
    out = render(grid, start, end, dev)
    if errs:
        out += '\n\n<!-- source warnings: ' + '; '.join(errs) + ' -->'
    print(out)

if __name__ == '__main__':
    main()
