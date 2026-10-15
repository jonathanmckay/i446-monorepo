#!/usr/bin/env python3
"""cs: reference Claude Code sessions by title; fork or resume them from anywhere.

  cs ls [query] [--all]        list sessions (title, age, cwd, id); newest first
  cs id <query>                print the best-matching session id
  cs fork <query> [-- args]    new session branched from the match (claude --resume ID --fork-session)
  cs open <query> [-- args]    resume the match in place (same session id)
  cs index                     force a full rescan
Options: --from ix   resolve on another host and copy the transcript here first
         --cd        chdir to the session's original cwd before launching

Titles come from the lines Claude Code itself writes into each session jsonl:
{"type":"custom-title"} (/rename or -n) beats {"type":"ai-title"} (auto) beats the
first prompt. Index cache: ~/.local/state/jm/session-index.json (incremental by mtime).
"""
import json, os, re, subprocess, sys, time
from pathlib import Path

PROJECTS = Path.home() / '.claude' / 'projects'
STATE = Path.home() / '.local' / 'state' / 'jm'
INDEX = STATE / 'session-index.json'
TITLE_RE = re.compile(rb'"type":"(custom-title|ai-title)","(?:customTitle|aiTitle)":"((?:[^"\\]|\\.)*)"')


def session_files():
    for proj in PROJECTS.iterdir():
        if not proj.is_dir():
            continue
        for f in proj.glob('*.jsonl'):
            yield f


def scan_one(f: Path):
    rec = {'mtime': f.stat().st_mtime, 'id': f.stem, 'custom': '', 'ai': '', 'cwd': '', 'prompt': '',
           'proj': f.parent.name}
    try:
        out = subprocess.run(['grep', '-ao', '-E', r'"type":"(custom-title|ai-title)","(customTitle|aiTitle)":"([^"\\]|\\.)*"', str(f)],
                             capture_output=True).stdout
        for m in TITLE_RE.finditer(out):
            val = json.loads(b'"' + m.group(2) + b'"')
            rec['custom' if m.group(1) == b'custom-title' else 'ai'] = val
        with open(f, 'rb') as fh:
            for i, line in enumerate(fh):
                if i > 60:
                    break
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if not rec['cwd'] and o.get('cwd'):
                    rec['cwd'] = o['cwd']
                if not rec['prompt'] and o.get('type') == 'user' and not o.get('isSidechain'):
                    c = (o.get('message') or {}).get('content')
                    if isinstance(c, str) and not c.startswith('<'):
                        rec['prompt'] = ' '.join(c.split())[:80]
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
        if p not in idx or abs(idx[p].get('mtime', 0) - mt) > 1e-6:
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


def rank(idx, query):
    q = query.lower()
    hits = []
    for p, r in idx.items():
        score = None
        if q in r['custom'].lower(): score = 3
        elif q in r['ai'].lower(): score = 2
        elif q in r['prompt'].lower(): score = 1
        elif r['id'].startswith(q): score = 4
        if score is not None:
            hits.append((score, r['mtime'], p, r))
    hits.sort(key=lambda h: (h[0], h[1]), reverse=True)
    return hits


def cmd_ls(idx, query, show_all):
    rows = rank(idx, query) if query else sorted(((0, r['mtime'], p, r) for p, r in idx.items()), key=lambda h: h[1], reverse=True)
    if not show_all and not query:
        rows = rows[:40]
    for _, mt, p, r in rows:
        t = title_of(r)
        mark = '✎' if r['custom'] else ' '
        cwd = r['cwd'].replace(str(Path.home()), '~')
        print(f"{age(mt):>4}  {mark} {t[:48]:<48}  {cwd[:34]:<34}  {r['id']}")


def resolve(idx, query):
    hits = rank(idx, query)
    if not hits:
        sys.exit(f'cs: no session matches {query!r}')
    best = hits[0][3]
    if len(hits) > 1 and hits[1][0] == hits[0][0]:
        others = ', '.join(title_of(h[3])[:30] for h in hits[1:4])
        print(f"cs: picked newest of {len(hits)} matches: {title_of(best)!r} (others: {others})", file=sys.stderr)
    return best


def remote_fetch(host, query):
    out = subprocess.run(['ssh', host, f'python3 ~/i446-monorepo/tools/cs/cs.py id --path {json.dumps(query)}'],
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
    do_cd = '--cd' in argv; argv = [a for a in argv if a != '--cd']
    want_path = '--path' in argv; argv = [a for a in argv if a != '--path']
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
        return cmd_ls(idx, query, show_all)
    if not query:
        sys.exit(f'cs {cmd}: need a title query')
    r = remote_fetch(host, query) if host else resolve(idx, query)
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
