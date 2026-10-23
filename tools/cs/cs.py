#!/usr/bin/env python3
"""cs: reference Claude Code sessions by title; fork or resume them from anywhere.

  cs ls [query] [--all] [-l]   list sessions (#, age, host, mark, title, ▸ claimed tasks), newest first.
                               #: the session's number in the current cmux workspace (dtd 1, janus 2,
                               Claude panes 3+ in reading order, top-left to bottom-right); blank = not open.
                               host: machine the session last launched from (ix, sl=straylight,
                               im=imago-c; blank = before host tracking).
                               mark: ✎ title you set, 😈 title the AI wrote, blank = first prompt.
                               Bare ls = the 6 newest; a query searches all history; --all lifts
                               the limit. -l adds cwd + id
  cs id <query>                print the best-matching session id (-l: id, title, live|idle;
                               --title: only accept a title/claim match, never a first-prompt one;
                               --latest: skip matching, take the session you last typed into;
                               --not-live: with --latest, skip sessions a claude process here is running)
  cs tmux <query>              print the tmux session holding the match's live claude process
                               (same flags as id; exits 1 when it is not running in tmux here)
  cs tmux-map                  JSON [{tmux, surface, sid, title}] for every live claude in tmux here
                               (Straylight's cmux_panes asks ix for it to number ix-hosted tabs)
  cs fork [query] [-- args]    new session branched from the match (claude --resume ID --fork-session)
  cs open [query] [-- args]    resume the match in place (same session id)
  cs pick [query]              interactive fzf picker; prints the chosen id
  cs index                     force a full rescan
  cs order [name ...|--clear] pin this workspace's pane numbering (`cs order taxes claude janus`;
                               names are fuzzy or current numbers; unnamed panes follow by position)
  cs retitle                   rename every pane's cmux tab to 'N · title' (dtd 1, janus 2, Claude 3+)
                               (the session-title hook calls this on start/prompt/stop)
Options: --from ix   resolve on another host and copy the transcript here first
         --cd        chdir to the session's original cwd before launching
         --pick      always open the fzf picker, even when the query is unambiguous
         --no-pick   never open fzf; take the best fuzzy match (for scripts)

A bare number (`cs fork 3`) means the Claude pane with that cmux number (3 is the first Claude
pane; 1 and 2 are dtd and janus). Numbers follow reading order unless `cs order` pinned them (~/.local/state/jm/pane-order.json, per workspace).
Matching is fuzzy: words in any order, each word may be a substring, an fzf-style
in-order subsequence, or a close misspelling (difflib). Titles outrank first prompts;
ties go to the newest session. With no query, or when the top two matches score the
same, fork/open/id drop into fzf (if on a tty) so you pick instead of guessing.

Titles come from the lines Claude Code itself writes into each session jsonl:
{"type":"custom-title"} (/rename or -n) beats {"type":"ai-title"} (auto) beats the
first prompt. A first prompt that was a slash command renders as "/cmd args" (the raw
<command-message> wrapper used to be skipped, leaving ~1/3 of sessions titled by id).
Each row also shows the last three dtd tasks the session claimed via /d or /claim
(skills that claim their own card, like /0g, don't count), read from the "[claim hook] 😈 claimed: <task> (id …)" lines the
UserPromptSubmit hook leaves in the transcript; a task name is also a valid query.
Index cache: ~/.local/state/jm/session-index.json (incremental by mtime; SCHEMA bump rescans).
"""
import difflib, json, os, re, shutil, subprocess, sys, time
from pathlib import Path

PROJECTS = Path.home() / '.claude' / 'projects'
STATE = Path.home() / '.local' / 'state' / 'jm'
INDEX = STATE / 'session-index.json'
ORDER = STATE / 'pane-order.json'   # {workspace_id: [session_id, ...]} pinned by `cs order`
TITLE_RE = re.compile(rb'"type":"(custom-title|ai-title)","(?:customTitle|aiTitle)":"((?:[^"\\]|\\.)*)"')
SCHEMA = 7                      # bump when scan_one's output changes; stale records get rescanned
CMD_RE = re.compile(r'<command-name>\s*/?([^<]*?)\s*</command-name>(?:.*?<command-args>(.*?)</command-args>)?', re.S)
TAG_RE = re.compile(r'<(system-reminder|command-message|command-name|command-args|local-command-stdout|local-command-caveat|pasted_content[^>]*)>.*?</\1>', re.S)
SKIP_PREFIXES = ('Base directory for this skill', '<local-command')
# Only the hook's own output counts: Claude Code records it as an `attachment` record
# ({"type":"attachment","attachment":{"type":"hook_success","hookEvent":"UserPromptSubmit",
# "content":"[claim hook] 😈 claimed: <task> (id …)…"}}). Tool results (a session grepping
# transcripts), assistant echoes and skill docs quoting the line never match.
# "(id …) for /<skill>" is a skill claiming its own card (/0g, /notes, …), not something
# JM claimed: left out.
CLAIM_RE = re.compile(r'😈 claimed: (.*?) \(id ([A-Za-z0-9]+)\)(?! for /)')
ANNOT_RE = re.compile(r'\s*(\(\d+\)|\[\s*\d+\s*\]|\{\d+\})')   # (30) [15] {10} task annotations
MAX_CLAIMS = 3


def prompt_text(o):
    """Human-readable text of a user record, or '' if it has none (tool results, skill bodies, ...)."""
    c = (o.get('message') or {}).get('content')
    if isinstance(c, list):
        c = ' '.join(b.get('text', '') for b in c if isinstance(b, dict) and b.get('type') == 'text')
    if not isinstance(c, str) or not c.strip():
        return ''
    if c.startswith(SKIP_PREFIXES):
        return ''
    m = CMD_RE.search(c)
    if m:
        c = '/' + m.group(1) + (' ' + m.group(2) if m.group(2) else '')
    else:
        c = TAG_RE.sub('', c)
    return ' '.join(c.split())[:80]


# User records that are not something JM typed: background-task and system notices the
# harness injects, and the echo of a local command's output.
INJECTED_PREFIXES = ('<task-notification>', '[SYSTEM NOTIFICATION', '<local-command-stdout>',
                     '<local-command-caveat>', '<bash-stdout>', '<bash-stderr>')


def last_user_ts(f: Path) -> float:
    """Epoch of the last prompt JM typed into session `f` (0.0 if none). The file's mtime moves
    whenever an agent is still working, so "latest" by mtime picked a session churning on its own
    over the one he was just typing in (2026-10-22). Tool results, skill bodies (isMeta), sidechains
    and injected notices are not input; slash commands and `!` shell lines are."""
    import datetime
    out = subprocess.run(['grep', '-a', '-F', '"type":"user"', str(f)], capture_output=True).stdout
    for line in reversed(out.splitlines()):
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get('type') != 'user' or o.get('isMeta') or o.get('isSidechain'):
            continue
        c = (o.get('message') or {}).get('content')
        if isinstance(c, list):
            c = ' '.join(b.get('text', '') for b in c if isinstance(b, dict) and b.get('type') == 'text')
        if not isinstance(c, str) or not c.strip() or c.lstrip().startswith(INJECTED_PREFIXES):
            continue
        try:
            return datetime.datetime.fromisoformat(o['timestamp'].replace('Z', '+00:00')).timestamp()
        except Exception:
            continue
    return 0.0


def session_files():
    for proj in PROJECTS.iterdir():
        if not proj.is_dir():
            continue
        for f in proj.glob('*.jsonl'):
            yield f


def scan_one(f: Path):
    rec = {'v': SCHEMA, 'mtime': f.stat().st_mtime, 'id': f.stem, 'custom': '', 'ai': '', 'cwd': '', 'prompt': '',
           'proj': f.parent.name, 'claims': [], 'last_user': 0.0}
    try:
        out = subprocess.run(['grep', '-ao', '-E', r'"type":"(custom-title|ai-title)","(customTitle|aiTitle)":"([^"\\]|\\.)*"', str(f)],
                             capture_output=True).stdout
        for m in TITLE_RE.finditer(out):
            val = json.loads(b'"' + m.group(2) + b'"')
            rec['custom' if m.group(1) == b'custom-title' else 'ai'] = val
        out = subprocess.run(['grep', '-a', '-F', '"hook_success"', str(f)], capture_output=True).stdout
        seen = {}
        for line in out.splitlines():
            if b'[claim hook]' not in line:
                continue
            try:
                a = json.loads(line).get('attachment') or {}
            except Exception:
                continue
            if a.get('type') != 'hook_success' or a.get('hookEvent') != 'UserPromptSubmit':
                continue
            for m in CLAIM_RE.finditer(a.get('content') or a.get('stdout') or ''):
                seen.pop(m.group(2), None)                 # re-claim moves it to the end (most recent)
                seen[m.group(2)] = ' '.join(ANNOT_RE.sub('', m.group(1)).split())
        rec['claims'] = list(seen.values())[-MAX_CLAIMS:]
        rec['last_user'] = last_user_ts(f)
        with open(f, 'rb') as fh:
            for i, line in enumerate(fh):
                if i > 150:
                    break
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if not rec['cwd'] and o.get('cwd'):
                    rec['cwd'] = o['cwd']
                if not rec['prompt'] and o.get('type') == 'user' and not o.get('isSidechain'):
                    rec['prompt'] = prompt_text(o)
                if rec['prompt'] and rec['cwd']:
                    break
    except Exception:
        pass
    return rec


def load_index(full=False):
    idx = {}
    if INDEX.exists() and not full:
        try:
            idx = json.loads(INDEX.read_text())
        except Exception:
            idx = {}
    seen, changed = set(), False
    for f in session_files():
        p = str(f)
        seen.add(p)
        try:
            mt = f.stat().st_mtime
        except FileNotFoundError:
            continue
        if p not in idx or idx[p].get('v') != SCHEMA or abs(idx[p].get('mtime', 0) - mt) > 1e-6:
            idx[p] = scan_one(f)
            changed = True
    for p in list(idx):
        if p not in seen:
            del idx[p]; changed = True
    if changed:
        STATE.mkdir(parents=True, exist_ok=True)
        INDEX.write_text(json.dumps(idx))
    return idx


def title_of(r):
    return r['custom'] or r['ai'] or r['prompt'] or r['id'][:8]


def age(ts):
    d = time.time() - ts
    for n, u in ((86400, 'd'), (3600, 'h'), (60, 'm')):
        if d >= n:
            return f'{int(d // n)}{u}'
    return 'now'


def _subseq(q, t):
    """True if q's chars appear in t in order (fzf-style)."""
    it = iter(t)
    return all(ch in it for ch in q)


def word_score(w, text):
    """0..1: how well one query word matches a text. Substring 1, subsequence .7, misspelling by ratio."""
    if not text:
        return 0.0
    if w in text:
        return 1.0
    best = 0.0
    for tw in text.split():
        if w in tw:
            return 1.0
        if len(w) >= 3 and _subseq(w, tw):
            best = max(best, 0.7)
        r = difflib.SequenceMatcher(None, w, tw).ratio()
        if r >= 0.75:
            best = max(best, 0.6 + 0.4 * (r - 0.75) / 0.25)
    return best


def fuzzy(query, r):
    """Score a session record against a query: 0 (no match) .. ~3.3. Field weight: custom > ai > prompt."""
    q = query.lower()
    if r['id'].startswith(q):
        return 4.0
    words = q.split()
    if not words:
        return 0.0
    best = 0.0
    for field, weight in (('custom', 3.0), ('ai', 2.0), ('claims', 1.5), ('prompt', 1.0)):
        text = r[field] if field != 'claims' else ' · '.join(r.get('claims') or [])
        text = text.lower()
        if not text:
            continue
        if q in text:                       # whole phrase present: top score for this field
            best = max(best, weight + 0.3)
            continue
        scores = [word_score(w, text) for w in words]
        if min(scores) <= 0.0:              # every word has to land somewhere
            continue
        best = max(best, weight + 0.3 * (sum(scores) / len(scores)) - 0.3)
    return best


def rank(idx, query):
    hits = []
    for p, r in idx.items():
        score = fuzzy(query, r)
        if score > 0:
            hits.append((score, r['mtime'], p, r))
    hits.sort(key=lambda h: (h[0], h[1]), reverse=True)
    return hits


def pick(idx, query='', rows=None):
    """fzf over sessions (newest first, pre-filtered by query if given); returns the record or None."""
    if not shutil.which('fzf') or not sys.stdin.isatty():
        return None
    if rows is None:
        rows = rank(idx, query) if query else sorted(((0, r['mtime'], p, r) for p, r in idx.items()), key=lambda h: h[1], reverse=True)
    if not rows:
        return None
    nums = cmux_numbers()
    lines = [f"{(str(nums[r['id']]) if r['id'] in nums else ''):>2} {age(mt):>4}  {mark_of(r)} {title_of(r)[:60]:<60}  {claims_of(r)[:40]:<40}  {r['id']}" for _, mt, p, r in rows]
    out = subprocess.run(['fzf', '--no-sort', '--layout=reverse', '--height=50%', '--with-nth=1..-2',
                          '--prompt=session> ', '--query', query],
                         input='\n'.join(lines), capture_output=True, text=True)
    if out.returncode or not out.stdout.strip():
        return None
    sid = out.stdout.strip().split()[-1]
    return next(r for _, _, _, r in rows if r['id'] == sid)


LS_LIMIT = 6   # bare `cs ls` rows; --all or a query lifts it


def _cmux(method):
    try:
        out = subprocess.run(['cmux', 'rpc', method, '{}'], capture_output=True, text=True, timeout=2,
                             env={**os.environ, 'CMUX_QUIET': '1'}).stdout
        return json.loads(out)
    except Exception:
        return {}


CLAUDE_PROC_RE = re.compile(r'^\s*\d+\s+(?:\S*/)?claude\s')   # a ps line for a claude process (any path)
TOOL_PANES = ('dtd', 'janus')   # non-Claude TUI panes, numbered 1 and 2 in this fixed order
CLAUDE_FIRST = len(TOOL_PANES) + 1   # Claude panes continue the same series: 3, 4, ...


SURFACE_SESSIONS = STATE / 'surface-sessions'   # <surface_id> → session id, written by session-title.sh


def _etime_secs(t: str) -> int:
    """ps etime ([[dd-]hh:]mm:ss) → seconds."""
    days, _, t = t.rpartition('-')
    parts = [int(x) for x in t.split(':')]
    while len(parts) < 3:
        parts.insert(0, 0)
    return (int(days) if days else 0) * 86400 + parts[0] * 3600 + parts[1] * 60 + parts[2]


def _apply_surface_records(sid_of, pid_of):
    """Prefer the session id the tab's own hook recorded over the one in claude's argv, when the
    record is newer than that claude process. A fork runs as `claude --resume <parent>
    --fork-session` and an in-session /resume switches ids, so argv names the wrong session
    (2026-10-22: the forked tab got no number, its claims a bare 😈). A record older than the
    process is left over from an earlier session in that tab and is ignored."""
    if not pid_of:
        return
    try:
        out = subprocess.run(['ps', '-o', 'pid=,etime=', '-p', ','.join(map(str, pid_of.values()))],
                             capture_output=True, text=True).stdout
        started = {int(p): time.time() - _etime_secs(e) for p, e in (l.split() for l in out.splitlines() if l.strip())}
    except Exception:
        return
    for sf, pid in pid_of.items():
        f = SURFACE_SESSIONS / sf
        try:
            rec = f.read_text().strip()
            if re.fullmatch(r'[0-9a-f-]{36}', rec) and f.stat().st_mtime >= started.get(pid, float('inf')) - 2:
                sid_of[sf] = rec
        except OSError:
            continue


def cmux_panes():
    """[(n, session_id, surface_id, surface_title)] for the Claude sessions open in the current
    cmux workspace, numbered from CLAUDE_FIRST (3) in reading order of their panes (top-left to
    bottom-right); 1 and 2 belong to the dtd and janus TUI panes (TOOL_PANES). Session
    ids come from the live claude processes (CMUX_SURFACE_ID in env + --session-id/--resume in
    argv); cmux's own resume_binding.checkpoint_id fills in any surface without a visible
    process. [] outside cmux."""
    if not shutil.which('cmux') or not os.environ.get('CMUX_SOCKET_PATH', '') and not Path.home().joinpath('.local/state/cmux').exists():
        return []
    pl = _cmux('pane.list')
    panes = pl.get('panes') or []
    global _cmux_answered
    _cmux_answered = bool(pl)
    if not panes:
        return []
    wsid = pl.get('workspace_id') or ''
    surfaces = _cmux('surface.list').get('surfaces') or []
    sid_of, stitle = {}, {}
    for sf in surfaces:
        stitle[sf['id']] = sf.get('title') or ''
        cp = (sf.get('resume_binding') or {}).get('checkpoint_id')
        if cp and (sf.get('resume_binding') or {}).get('kind', 'claude') == 'claude':
            sid_of[sf['id']] = cp
    try:
        # Scan argv+env of every process rather than `pgrep -x claude`: a claude launched through
        # cmux's shim keeps its full path as the process name, which pgrep -x does not match.
        ps = subprocess.run(['ps', '-Eww', '-axo', 'pid=,command='], capture_output=True, text=True).stdout
        pid_of = {}
        for line in ps.splitlines():
            if not CLAUDE_PROC_RE.match(line):
                continue
            m_s = re.search(r'CMUX_SURFACE_ID=([0-9A-Fa-f-]{36})', line)
            m_i = re.search(r'--(?:session-id|resume)[ =]([0-9a-f-]{36})', line)
            if m_s and m_i:
                sid_of[m_s.group(1)] = m_i.group(1)      # a live process beats cmux's recorded binding
                pid_of[m_s.group(1)] = int(line.split()[0])
        _apply_surface_records(sid_of, pid_of)
    except Exception:
        pass
    try:
        missing = {sf['id']: sf.get('ref') for sf in surfaces
                   if sf['id'] not in sid_of and sf.get('type') == 'terminal'
                   and NUM_PREFIX_RE.sub('', sf.get('title') or '').strip().lower() not in TOOL_PANES}
        for sfid, (rsid, rtitle) in _remote_surfaces(missing).items():
            sid_of[sfid] = rsid
            if rtitle:
                REMOTE_TITLES[rsid] = rtitle
    except Exception:
        pass
    skip = os.environ.get('CS_EXCLUDE_SID', '')   # set by the SessionEnd hook: the exiting session is still in ps
    order = []
    for pn in panes:
        f = pn.get('pixel_frame') or {}
        for sfid in pn.get('surface_ids') or [pn.get('selected_surface_id')]:
            if sfid in sid_of and sid_of[sfid] != skip:
                order.append((round(f.get('y', 0) / 40), round(f.get('x', 0)), sid_of[sfid], sfid))
    order.sort()
    pinned = load_order().get(wsid, [])
    order.sort(key=lambda t: (pinned.index(t[2]) if t[2] in pinned else len(pinned)))   # stable: pinned first, rest by position
    out, seen = [], set()
    for _, _, sid, sfid in order:
        if sid not in seen:
            seen.add(sid); out.append((len(out) + CLAUDE_FIRST, sid, sfid, stitle.get(sfid, '')))
    nums = {sid: n for n, sid, _, _ in out}
    try:
        PANES_CACHE.parent.mkdir(parents=True, exist_ok=True)
        PANES_CACHE.write_text(json.dumps(nums))
    except Exception:
        pass
    # dtd shows the number before 😈 on a claimed card (2026-10-21: "which cmux session has the
    # task"). The claim store is what dtd (here and on Ix) reads, so the map goes there too,
    # written only when it changed: a write bumps the dir mtime, which is dtd's reload signal.
    try:
        if CLAIMS_PANES.parent.is_dir():
            text = json.dumps(nums, sort_keys=True)
            if not CLAIMS_PANES.exists() or CLAIMS_PANES.read_text() != text:
                tmp = CLAIMS_PANES.with_name(CLAIMS_PANES.name + '.tmp')
                tmp.write_text(text)
                tmp.replace(CLAIMS_PANES)
    except Exception:
        pass
    return out


def load_order():
    try:
        return json.loads(ORDER.read_text())
    except Exception:
        return {}


def cmd_order(idx, args):
    """`cs order taxes claude janus spokane`: pin the numbering of this workspace's Claude panes.
    Each arg names one pane (fuzzy, against its tab title, the title hook's state file, and the
    index title) or is its current number. Unnamed panes follow in reading order. No args: show
    the current numbering. `cs order --clear`: back to pure reading order. Retitles afterwards."""
    panes = cmux_panes()
    if not panes:
        sys.exit('cs order: no Claude panes in this cmux workspace')
    wsid = _cmux('pane.list').get('workspace_id') or ''
    store = load_order()
    if args and args[0] in ('--clear', 'reset', 'clear'):
        store.pop(wsid, None)
    elif args:
        by_id = {v['id']: v for v in idx.values()}
        titles = STATE / 'session-titles'
        chosen = []
        for a in args:
            q = a.lower().strip()
            if q.isdigit():
                hit = [t for t in panes if t[0] == int(q)]
                if not hit:
                    sys.exit(f'cs order: no pane numbered {q}')
                best = hit[0]
            else:
                scored = []
                for t in panes:
                    n, sid, sfid, cur = t
                    f = titles / sid
                    texts = [NUM_PREFIX_RE.sub('', cur), f.read_text() if f.exists() else '']
                    r = by_id.get(sid)
                    if r:
                        texts += [r['custom'], r['ai'], r['prompt']]
                    sc = 0.0
                    for text in texts:
                        text = text.lower()
                        if not text:
                            continue
                        if q in text:
                            sc = max(sc, 2.0); continue
                        ws = [word_score(w, text) for w in q.split()]
                        if ws and min(ws) > 0:
                            sc = max(sc, sum(ws) / len(ws))
                    scored.append((sc, t))
                scored.sort(key=lambda x: -x[0])
                if scored[0][0] <= 0 or (len(scored) > 1 and scored[0][0] == scored[1][0]):
                    sys.exit(f'cs order: {a!r} does not pick out one pane (see `cs ls`)')
                best = scored[0][1]
            if best in chosen:
                sys.exit(f'cs order: {a!r} names a pane already placed')
            chosen.append(best)
        store[wsid] = [t[1] for t in chosen]
    ORDER.parent.mkdir(parents=True, exist_ok=True)
    ORDER.write_text(json.dumps(store, indent=1))
    cmd_retitle()


PANES_CACHE = STATE / 'pane-numbers.json'   # {session_id: n}, written by cmux_panes() whenever cmux answers
CLAIMS_PANES = Path.home() / 'vault/z_ibx/agent-claims/panes.json'   # same map, for dtd (see did/agent_claims.py)
_cmux_answered = False


def _live_sids():
    """Session ids some claude process on this host is running (--session-id/--resume in argv)."""
    ps = subprocess.run(['ps', '-axo', 'command='], capture_output=True, text=True).stdout
    return set(re.findall(r'--(?:session-id|resume)[ =]([0-9a-f-]{36})', ps))


def _tmux_claudes():
    """{session_id: tmux_session_name} for every live claude process inside a tmux pane here.
    Walks each claude process up its parent chain to a pane's root pid, so it finds the session
    whatever wrapper (claude-tracked, zsh -lic) sits between them."""
    ps = subprocess.run(['ps', '-axo', 'pid=,ppid=,command='], capture_output=True, text=True).stdout
    procs = {}
    for line in ps.splitlines():
        parts = line.split(None, 2)
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            procs[int(parts[0])] = (int(parts[1]), parts[2] if len(parts) > 2 else '')
    try:
        tl = subprocess.run(['tmux', 'list-panes', '-a', '-F', '#{pane_pid} #{session_name}'],
                            capture_output=True, text=True).stdout
    except FileNotFoundError:
        return None
    panes = {int(p): n for p, n in (l.split(' ', 1) for l in tl.splitlines() if ' ' in l) if p.isdigit()}
    out = {}
    for pid, (_pp, cmd) in procs.items():
        m = re.search(r'--(?:session-id|resume)[ =]([0-9a-f-]{36})\b', cmd)
        if not m or m.group(1) in out:
            continue
        p, hops = pid, 0
        while p in procs and hops < 20:
            if p in panes:
                out[m.group(1)] = panes[p]
                break
            p, hops = procs[p][0], hops + 1
    return out


def tmux_session_of(sid):
    """Name of the tmux session holding the live claude process for `sid`, or None."""
    return (_tmux_claudes() or {}).get(sid)


def tmux_map(idx):
    """[{tmux, surface, sid, title}] for every live claude in tmux here. `surface` is the cmux
    surface id cc tagged the tmux session with (@cmux_surface; '' when untagged, e.g. a phone
    session or a hand-typed `tmux attach`)."""
    tags = {}
    try:
        tl = subprocess.run(['tmux', 'list-sessions', '-F', '#{session_name}\t#{@cmux_surface}'],
                            capture_output=True, text=True).stdout
        tags = dict(l.split('\t', 1) for l in tl.splitlines() if '\t' in l)
    except FileNotFoundError:
        pass
    by_id = {v['id']: v for v in idx.values()}
    live = _tmux_claudes()
    out = [{'tmux': t, 'surface': tags.get(t, ''), 'sid': sid,
            'title': title_of(by_id[sid]) if sid in by_id else ''}
           for sid, t in sorted(live.items())]
    # One hop of nesting: a tmux session whose pane runs `ssh ix tmux attach -t X` (attached from
    # inside ix) shows X's Claude, so it inherits X's entry under its own name and tag.
    by_tmux = {e['tmux']: e for e in out}
    try:
        tl = subprocess.run(['tmux', 'list-panes', '-a', '-F', '#{pane_pid} #{session_name}'],
                            capture_output=True, text=True).stdout
        ps = subprocess.run(['ps', '-ww', '-axo', 'pid=,ppid=,command='], capture_output=True, text=True).stdout
        kids = {}
        for line in ps.splitlines():
            f = line.split(None, 2)
            if len(f) == 3 and f[1].isdigit():
                kids.setdefault(int(f[1]), []).append((int(f[0]), f[2]))
        for line in tl.splitlines():
            pid, _, name = line.partition(' ')
            if name in by_tmux or not pid.isdigit():
                continue
            stack, hops = [int(pid)], 0
            while stack and hops < 50:
                p = stack.pop(); hops += 1
                for c, cmd in kids.get(p, []):
                    m = TMUX_NAME_RE.search(cmd) if SSH_IX_RE.search(cmd) else None
                    if m and m.group(1) in by_tmux:
                        out.append({**by_tmux[m.group(1)], 'tmux': name, 'surface': tags.get(name, '')})
                        stack = []
                        break
                    stack.append(c)
    except Exception:
        pass
    return out


IX_MAP_CACHE = STATE / 'ix-tmux-map.json'   # {ts, map}: ix's tmux_map, refreshed at most every 20 s
SSH_IX_RE = re.compile(r'(?:^|/)(?:ssh|mosh|mosh-client)\s.*\bix(?:\.local)?\b')
TMUX_NAME_RE = re.compile(r'tmux\s+(?:attach(?:-session)?|a|new(?:-session)?)\b[^;]*?\s-[ts]\s+[\'"]?([A-Za-z0-9_.:-]+)')


def _ix_tmux_map(force=False):
    try:
        c = json.loads(IX_MAP_CACHE.read_text())
        if not force and time.time() - c.get('ts', 0) < 20:
            return c.get('map') or []
    except Exception:
        c = {}
    try:
        r = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=2', 'ix',
                            '~/.local/bin/cs tmux-map'], capture_output=True, text=True, timeout=5)
        m = json.loads(r.stdout) if r.returncode == 0 else None
    except Exception:
        m = None
    if m is None:
        return c.get('map') or []
    try:
        IX_MAP_CACHE.parent.mkdir(parents=True, exist_ok=True)
        IX_MAP_CACHE.write_text(json.dumps({'ts': time.time(), 'map': m}))
    except Exception:
        pass
    return m


def _remote_surfaces(surface_ids):
    """{surface_id: (session_id, title)} for cmux tabs whose Claude runs on ix: the tab's own
    process is an ssh/mosh to ix, so the local scan finds no claude to read a session id from
    (2026-10-22: tabs `cc` sends to ix, and hand-attached phone sessions, had no pane number, so
    dtd drew a bare 😈 for their claims). Matched to ix's live tmux sessions by the surface tag cc
    sets, else by the tmux name in the ssh/mosh argv on that tab's tty."""
    if not surface_ids:
        return {}
    try:
        tree = json.loads(subprocess.run(['cmux', 'tree', '--json'], capture_output=True, text=True,
                                         timeout=3).stdout)
    except Exception:
        return {}
    ref_tty = {}

    def walk(o):
        if isinstance(o, dict):
            if o.get('ref') and o.get('tty'):
                ref_tty[o['ref']] = o['tty']
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(tree)
    sf_tty = {sid: ref_tty.get(ref) for sid, ref in surface_ids.items() if ref_tty.get(ref)}
    if not sf_tty:
        return {}
    ps = subprocess.run(['ps', '-ww', '-axo', 'tty=,command='], capture_output=True, text=True).stdout
    names = {}
    for line in ps.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) < 2 or not SSH_IX_RE.search(parts[1]):
            continue
        for sf, tty in sf_tty.items():
            if parts[0] == tty:
                m = TMUX_NAME_RE.search(parts[1])
                names.setdefault(sf, m.group(1) if m else '')
    if not names:
        return {}
    out = {}
    for force in (False, True):
        mp = _ix_tmux_map(force)
        by_tag = {e['surface']: e for e in mp if e.get('surface')}
        by_name = {e['tmux']: e for e in mp}
        for sf, name in names.items():
            e = by_tag.get(sf) or by_name.get(name)
            if e:
                out[sf] = (e['sid'], e.get('title') or '')
        if len(out) == len(names):
            break
    return out


REMOTE_TITLES = {}   # session_id -> title, for ix-hosted panes (cmd_retitle has no local transcript)


def cmux_numbers():
    """{session_id: n} from cmux_panes(). cmux's socket only answers from inside the GUI login
    session (over ssh/cron `cmux rpc` dies with a broken pipe), so when it does not answer the
    numbering cached by the last GUI-side call (the session-title hook runs one on every prompt)
    is used instead, limited to sessions whose claude process is still alive."""
    nums = {sid: n for n, sid, _, _ in cmux_panes()}
    if nums or _cmux_answered:
        return nums
    try:
        cached = json.loads(PANES_CACHE.read_text())
    except Exception:
        return {}
    live = _live_sids()
    return {sid: n for sid, n in cached.items() if sid in live}


NUM_PREFIX_RE = re.compile(r'^\d{1,2} · ')


def cmd_retitle():
    """Rename every numbered Claude pane in the current cmux workspace to 'N · title' (N from 3), and
    the dtd/janus TUI panes to '1 · dtd' / '2 · janus' (matched by tab title). The title
    is the session-title hook's state file (~/.local/state/jm/session-titles/<sid>) when it
    exists, else the pane's current title; an old 'N · ' prefix is stripped first so numbers never
    stack. Prints one 'N<TAB>surface_id<TAB>title' line per pane renamed, so the hook can tell
    whether its own surface was covered."""
    titles = STATE / 'session-titles'
    claude_sf = {sfid for _, _, sfid, _ in cmux_panes()}
    for sf in _cmux('surface.list').get('surfaces') or []:
        base = NUM_PREFIX_RE.sub('', sf.get('title') or '').strip()
        if sf['id'] in claude_sf or base.lower() not in TOOL_PANES:
            continue
        title = f'{TOOL_PANES.index(base.lower()) + 1} · {base}'
        subprocess.run(['cmux', 'rename-tab', '--surface', sf['id'], '--', title], capture_output=True,
                       timeout=2, env={**os.environ, 'CMUX_QUIET': '1'})
        print(f'{title.split()[0]}\t{sf["id"]}\t{title}')
    for n, sid, sfid, cur in cmux_panes():
        f = titles / sid
        base = f.read_text().strip() if f.exists() else (REMOTE_TITLES.get(sid) or cur)
        base = NUM_PREFIX_RE.sub('', base).strip()
        if not base:
            continue
        title = f'{n} · {base}'
        subprocess.run(['cmux', 'rename-tab', '--surface', sfid, '--', title], capture_output=True,
                       timeout=2, env={**os.environ, 'CMUX_QUIET': '1'})
        print(f'{n}\t{sfid}\t{title}')


HOSTS = STATE / 'session-hosts'   # <sid> → short hostname of the machine that last launched it (claude-tracked writes these)
HOST_CODES = {'ix': 'ix', 'straylight': 'sl', 'imago-c': 'im'}


def host_of(r):
    """2-char code of the machine the session last launched from; '' before host tracking."""
    try:
        h = (HOSTS / r['id']).read_text().strip().lower()
    except OSError:
        return ''
    return HOST_CODES.get(h, h[:2])


def record_host(sid):
    """`cs open` relaunches a session here without going through claude-tracked: keep the host
    record current ourselves."""
    import socket
    try:
        HOSTS.mkdir(parents=True, exist_ok=True)
        (HOSTS / sid).write_text(socket.gethostname().split('.')[0].lower())
    except OSError:
        pass


def mark_of(r):
    """✎ title set by JM (/rename, -n); 😈 title written by the AI; blank = first prompt / id."""
    return '✎ ' if r['custom'] else '😈' if r['ai'] else '  '


def claims_of(r):
    """'▸ newest · older · oldest' for the row, '' if the session never claimed."""
    c = r.get('claims') or []
    return '▸ ' + ' · '.join(reversed(c)) if c else ''


def cmd_ls(idx, query, show_all, long=False):
    rows = rank(idx, query) if query else sorted(((0, r['mtime'], p, r) for p, r in idx.items()), key=lambda h: h[1], reverse=True)
    if not show_all and not query:
        rows = rows[:LS_LIMIT]
    width = shutil.get_terminal_size((160, 40)).columns
    nums = cmux_numbers()
    if not query:
        known = {r['id'] for _, _, _, r in rows}
        for sid, n in nums.items():
            if sid in known:
                continue
            old = next(((p, v) for p, v in idx.items() if v['id'] == sid), None)
            if old:                          # open in cmux but older than the window: list it anyway
                rows.insert(0, (0, old[1]['mtime'], old[0], old[1]))
            else:                            # open in cmux but nothing typed yet: no transcript to index
                rows.insert(0, (0, time.time(), '', {'id': sid, 'custom': '', 'ai': '', 'prompt': '(new session, nothing typed yet)',
                                                    'cwd': '', 'claims': []}))
    for _, mt, p, r in rows:
        t = title_of(r)
        mark = mark_of(r)
        num = f"{nums[r['id']]:>2}" if r['id'] in nums else '  '
        host = f'{host_of(r):<2}'
        if long:
            cwd = r['cwd'].replace(str(Path.home()), '~')
            line = f"{num} {age(mt):>4} {host}  {mark} {t[:48]:<48}  {cwd[:34]:<34}  {r['id']}  {claims_of(r)}"
        else:
            line = f"{num} {age(mt):>4} {host}  {mark} {t[:60]:<60}  {claims_of(r)}"
        print(line.rstrip()[:width])


TITLE_MIN = 1.4   # fuzzy() score reachable only via a title or a claimed task, never via the first prompt alone


def resolve(idx, query, force_pick=False, no_pick=False, title_only=False):
    if query.strip().isdigit() and len(query.strip()) <= 2:
        n = int(query)
        for sid, k in cmux_numbers().items():
            if k == n:
                for r in idx.values():
                    if r['id'] == sid:
                        return r
        if n in cmux_numbers().values():
            sys.exit(f'cs: pane {n} is a new Claude session with no transcript yet; nothing to fork or resume')
        if 0 < n < CLAUDE_FIRST:
            sys.exit(f'cs: pane {n} is {TOOL_PANES[n - 1]}, not a Claude session (Claude panes start at {CLAUDE_FIRST})')
        sys.exit(f'cs: no Claude pane numbered {n} in this cmux workspace (see `cs ls`)')
    if force_pick or not query:
        r = pick(idx, query)
        if r is None:
            sys.exit('cs: nothing picked' if query or shutil.which('fzf') else 'cs: need a title query (fzf not installed)')
        return r
    hits = rank(idx, query)
    if title_only:
        hits = [h for h in hits if h[0] >= TITLE_MIN]
    if not hits:
        sys.exit(f'cs: no session {"title " if title_only else ""}matches {query!r}')
    ambiguous = len(hits) > 1 and abs(hits[1][0] - hits[0][0]) < 1e-9
    if ambiguous and not no_pick:
        r = pick(idx, query, hits)
        if r is not None:
            return r
    best = hits[0][3]
    if ambiguous:
        others = ', '.join(title_of(h[3])[:30] for h in hits[1:4])
        print(f"cs: picked newest of {len(hits)} matches: {title_of(best)!r} (others: {others})", file=sys.stderr)
    return best


def remote_fetch(host, query):
    out = subprocess.run(['ssh', host, f'python3 ~/i446-monorepo/tools/cs/cs.py id --path --no-pick {json.dumps(query)}'],
                         capture_output=True, text=True)
    if out.returncode or not out.stdout.strip():
        sys.exit(f'cs: {host}: {out.stderr.strip() or "no match"}')
    rpath = out.stdout.strip()
    rel = Path(rpath).relative_to(Path(rpath).parents[1])  # <proj>/<id>.jsonl
    local = PROJECTS / rel
    local.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['scp', '-q', f'{host}:{rpath}', str(local)], check=True)
    return scan_one(local)


def main(argv):
    full = '--full' in argv; argv = [a for a in argv if a != '--full']
    show_all = '--all' in argv; argv = [a for a in argv if a != '--all']
    long = any(a in ('-l', '--long') for a in argv); argv = [a for a in argv if a not in ('-l', '--long')]
    do_cd = '--cd' in argv; argv = [a for a in argv if a != '--cd']
    want_path = '--path' in argv; argv = [a for a in argv if a != '--path']
    force_pick = '--pick' in argv; argv = [a for a in argv if a != '--pick']
    no_pick = '--no-pick' in argv; argv = [a for a in argv if a != '--no-pick']
    title_only = '--title' in argv; argv = [a for a in argv if a != '--title']
    latest = '--latest' in argv; argv = [a for a in argv if a != '--latest']
    not_live = '--not-live' in argv; argv = [a for a in argv if a != '--not-live']
    host = None
    if '--from' in argv:
        i = argv.index('--from'); host = argv[i + 1]; del argv[i:i + 2]
    if not argv or argv[0] in ('-h', '--help'):
        print(__doc__.strip()); return
    cmd, rest = argv[0], argv[1:]
    extra = []
    if '--' in rest:
        j = rest.index('--'); extra = rest[j + 1:]; rest = rest[:j]
    query = ' '.join(rest)
    if cmd == 'index':
        n = len(load_index(full=True)); print(f'indexed {n} sessions'); return
    if cmd == 'retitle':
        return cmd_retitle()
    idx = load_index(full)
    if cmd == 'ls':
        return cmd_ls(idx, query, show_all, long)
    if cmd == 'tmux-map':
        print(json.dumps(tmux_map(idx))); return
    if cmd == 'order':
        return cmd_order(idx, rest)
    if cmd == 'pick':
        r = pick(idx, query)
        if r is None:
            sys.exit('cs: nothing picked')
        print(r['id']); return
    if host and not query:
        sys.exit(f'cs {cmd}: --from needs a title query')
    if latest:
        if not idx:
            sys.exit('cs: no sessions indexed')
        # the session JM last typed into, not the one whose file last changed (an agent still
        # running moves mtime); sessions with no typed prompt rank by mtime, below all others
        # --not-live (cc r, 2026-10-22): the session JM is typing into right now is always the
        # latest, and it is already open on this screen; cc r means "the one I left", so skip
        # anything a claude process here is running.
        live_here = _live_sids() if not_live else set()
        pool = [v for v in idx.values() if v['id'] not in live_here]
        if not pool:
            sys.exit('cs: no session that is not already open')
        r = max(pool, key=lambda v: (v.get('last_user') or 0.0, v['mtime']))
    else:
        r = remote_fetch(host, query) if host else resolve(idx, query, force_pick, no_pick, title_only)
    if cmd == 'tmux':
        t = tmux_session_of(r['id'])
        if not t:
            sys.exit(1)
        print(t); return
    if cmd == 'id':
        if want_path:
            print(next(p for p, v in idx.items() if v['id'] == r['id']))
        elif long:
            live = r['id'] in cmux_numbers() or bool(re.search(
                rf'--(?:session-id|resume)[ =]{r["id"]}',
                subprocess.run(['ps', '-axo', 'command='], capture_output=True, text=True).stdout))
            # 4th column: epoch of JM's last typed prompt (cc compares hosts with it)
            print(f"{r['id']}\t{title_of(r)}\t{'live' if live else 'idle'}\t{r.get('last_user') or 0.0:.0f}")
        else:
            print(r['id'])
        return
    if cmd in ('fork', 'open'):
        if do_cd and r['cwd'] and os.path.isdir(r['cwd']):
            os.chdir(r['cwd'])
        args = ['claude', '--resume', r['id']]
        if cmd == 'fork':
            args += ['--fork-session', '-n', f"{title_of(r)[:50]} ⑂"]
        else:
            record_host(r['id'])
        os.execvp('claude', args + extra)
    sys.exit(f'cs: unknown command {cmd!r}')


if __name__ == '__main__':
    main(sys.argv[1:])
