#!/usr/bin/env python3
"""cs: reference Claude Code sessions by title; fork or resume them from anywhere.

  cs ls [query] [--all] [-l]   list sessions (age, title), newest first. Bare ls = last 7 days;
                               a query searches all history; --all lifts the window. -l adds cwd + id
  cs id <query>                print the best-matching session id
  cs fork [query] [-- args]    new session branched from the match (claude --resume ID --fork-session)
  cs open [query] [-- args]    resume the match in place (same session id)
  cs pick [query]              interactive fzf picker; prints the chosen id
  cs index                     force a full rescan
Options: --from ix   resolve on another host and copy the transcript here first
         --cd        chdir to the session's original cwd before launching
         --pick      always open the fzf picker, even when the query is unambiguous
         --no-pick   never open fzf; take the best fuzzy match (for scripts)

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
TITLE_RE = re.compile(rb'"type":"(custom-title|ai-title)","(?:customTitle|aiTitle)":"((?:[^"\\]|\\.)*)"')
SCHEMA = 4                      # bump when scan_one's output changes; stale records get rescanned
CMD_RE = re.compile(r'<command-name>\s*/?([^<]*?)\s*</command-name>(?:.*?<command-args>(.*?)</command-args>)?', re.S)
TAG_RE = re.compile(r'<(system-reminder|command-message|command-name|command-args|local-command-stdout|local-command-caveat|pasted_content[^>]*)>.*?</\1>', re.S)
SKIP_PREFIXES = ('Base directory for this skill', '<local-command')
# Task text may not contain a quote or a backslash: inside the JSON string a quote or
# an escaped newline (\n) ends the task, which keeps skill docs that quote the hook
# line from matching. "(id …) for /<skill>" is a skill claiming its own card (/0g,
# /notes, …), not something JM claimed; those are left out.
CLAIM_GREP = r'\[claim hook\] [^"\\]*😈 claimed: [^"\\]*\(id [A-Za-z0-9]+\)'
CLAIM_RE = re.compile(r'😈 claimed: ([^"\\]*?) \(id ([A-Za-z0-9]+)\)(?! for /)')
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


def session_files():
    for proj in PROJECTS.iterdir():
        if not proj.is_dir():
            continue
        for f in proj.glob('*.jsonl'):
            yield f


def scan_one(f: Path):
    rec = {'v': SCHEMA, 'mtime': f.stat().st_mtime, 'id': f.stem, 'custom': '', 'ai': '', 'cwd': '', 'prompt': '',
           'proj': f.parent.name, 'claims': []}
    try:
        out = subprocess.run(['grep', '-ao', '-E', r'"type":"(custom-title|ai-title)","(customTitle|aiTitle)":"([^"\\]|\\.)*"', str(f)],
                             capture_output=True).stdout
        for m in TITLE_RE.finditer(out):
            val = json.loads(b'"' + m.group(2) + b'"')
            rec['custom' if m.group(1) == b'custom-title' else 'ai'] = val
        out = subprocess.run(['grep', '-ao', '-E', CLAIM_GREP, str(f)], capture_output=True).stdout
        seen = {}
        for m in CLAIM_RE.finditer(out.decode('utf-8', 'replace')):
            seen.pop(m.group(2), None)                 # re-claim moves it to the end (most recent)
            seen[m.group(2)] = ' '.join(ANNOT_RE.sub('', m.group(1)).split())
        rec['claims'] = list(seen.values())[-MAX_CLAIMS:]
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
    lines = [f"{age(mt):>4}  {'✎' if r['custom'] else ' '} {title_of(r)[:72]:<72}  {r['id']}" for _, mt, p, r in rows]
    out = subprocess.run(['fzf', '--no-sort', '--layout=reverse', '--height=50%', '--with-nth=1..-2',
                          '--prompt=session> ', '--query', query],
                         input='\n'.join(lines), capture_output=True, text=True)
    if out.returncode or not out.stdout.strip():
        return None
    sid = out.stdout.strip().split()[-1]
    return next(r for _, _, _, r in rows if r['id'] == sid)


LS_WINDOW = 7 * 86400


def claims_of(r):
    """'😈 newest · older · oldest' for the row, '' if the session never claimed."""
    c = r.get('claims') or []
    return '😈 ' + ' · '.join(reversed(c)) if c else ''


def cmd_ls(idx, query, show_all, long=False):
    rows = rank(idx, query) if query else sorted(((0, r['mtime'], p, r) for p, r in idx.items()), key=lambda h: h[1], reverse=True)
    if not show_all and not query:
        cutoff = time.time() - LS_WINDOW
        rows = [h for h in rows if h[1] >= cutoff]
    width = shutil.get_terminal_size((160, 40)).columns
    for _, mt, p, r in rows:
        t = title_of(r)
        mark = '✎' if r['custom'] else ' '
        if long:
            cwd = r['cwd'].replace(str(Path.home()), '~')
            line = f"{age(mt):>4}  {mark} {t[:48]:<48}  {cwd[:34]:<34}  {r['id']}  {claims_of(r)}"
        else:
            line = f"{age(mt):>4}  {mark} {t[:60]:<60}  {claims_of(r)}"
        print(line.rstrip()[:width])


def resolve(idx, query, force_pick=False, no_pick=False):
    if force_pick or not query:
        r = pick(idx, query)
        if r is None:
            sys.exit('cs: nothing picked' if query or shutil.which('fzf') else 'cs: need a title query (fzf not installed)')
        return r
    hits = rank(idx, query)
    if not hits:
        sys.exit(f'cs: no session matches {query!r}')
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
    idx = load_index(full)
    if cmd == 'ls':
        return cmd_ls(idx, query, show_all, long)
    if cmd == 'pick':
        r = pick(idx, query)
        if r is None:
            sys.exit('cs: nothing picked')
        print(r['id']); return
    if host and not query:
        sys.exit(f'cs {cmd}: --from needs a title query')
    r = remote_fetch(host, query) if host else resolve(idx, query, force_pick, no_pick)
    if cmd == 'id':
        if want_path:
            print(next(p for p, v in idx.items() if v['id'] == r['id']))
        else:
            print(r['id'])
        return
    if cmd in ('fork', 'open'):
        if do_cd and r['cwd'] and os.path.isdir(r['cwd']):
            os.chdir(r['cwd'])
        args = ['claude', '--resume', r['id']]
        if cmd == 'fork':
            args += ['--fork-session', '-n', f"{title_of(r)[:50]} ⑂"]
        os.execvp('claude', args + extra)
    sys.exit(f'cs: unknown command {cmd!r}')


if __name__ == '__main__':
    main(sys.argv[1:])
