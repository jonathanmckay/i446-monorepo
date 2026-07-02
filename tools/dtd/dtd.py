#!/usr/bin/env python3
"""
dtd — Do The Damn thing. iPhone-first mirror of the `dtd` fzf TUI.

Same data source (task-queue.json), same domain colors, same short names,
same right-justified (time)[value]{bonus} estimates — but as a swipeable
web list. Swipe a card right → runs the real /did (did-fast.py): closes the
Todoist task AND writes its points to Neon.

Run:   python3 dtd.py            (binds 0.0.0.0:5560)
Open:  http://ix:5560            (from the phone, same as the dashboard)
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from flask import Flask, jsonify, render_template_string, request

# ---------------------------------------------------------------------------
# Config / paths
# ---------------------------------------------------------------------------
PORT = 5560
DID_FAST = Path.home() / "i446-monorepo/tools/did/did-fast.py"
STATE_DIR = Path.home() / ".local/state/jm"
CACHE = STATE_DIR / "task-queue.json"
DONE_FILE = STATE_DIR / "completed-today.json"
CACHE_MAX_AGE = 180  # seconds; refresh from Todoist if staler

# Neon domain palette — mirrors tools/did/dtd.sh COLORS (RGB → hex).
COLORS = {
    "g245": "#00e676", "epcn": "#00bfa5", "s897": "#1b5e20", "hcmc2": "#ffd600",
    "xk87": "#fd6c1d", "xk88": "#e65100", "hci": "#63ede0", "i9": "#2979ff",
    "n156": "#1249b4", "hcmc": "#0d3b66", "m5x2": "#d50032", "hcb": "#f81d78",
    "hcbp": "#ff4081", "infra": "#9e9e9e", "i444": "#616161", "i447": "#a89c8a",
    "hcm": "#aa00ff", "hcmp": "#7c4dff", "hcmr": "#bda6ff", "家": "#ff4136",
    "睡觉": "#666666",
}
DEFAULT_COLOR = "#bdbdbd"  # unlabelled / f693 / i446 → terminal default fg

# ---------------------------------------------------------------------------
# Parsing helpers (mirror dtd.sh)
# ---------------------------------------------------------------------------
_ANN = re.compile(r" *\(\d*\)| *\[\d*G?\]| *\{\d*\}")
_TIME = re.compile(r"\((\d+)\)")
_VAL = re.compile(r"\[(\d+)G?\]")
_BONUS = re.compile(r"\{(\d+)\}")

def strip_ann(s: str) -> str:
    return re.sub(r"  +", " ", _ANN.sub("", s)).strip()

def parse_est(content: str) -> tuple[str, int]:
    """Return ('(30) [20]' canonical string, points)."""
    tm, vm, bm = _TIME.search(content), _VAL.search(content), _BONUS.search(content)
    toks = []
    if tm:
        toks.append("(%s)" % tm.group(1))
    if vm:
        toks.append("[%s]" % vm.group(1))
    if bm:
        toks.append("{%s}" % bm.group(1))
    pts = int(vm.group(1)) if vm else (int(bm.group(1)) if bm else 0)
    return " ".join(toks), pts

def color_of(labels: list[str]) -> tuple[str, str]:
    for lbl in labels:
        if lbl in COLORS:
            return COLORS[lbl], lbl
    return DEFAULT_COLOR, ""

def _prank(p) -> int:
    try:
        return -(int(p) or 1)
    except (TypeError, ValueError):
        return -1

# ---------------------------------------------------------------------------
# Cache load + section ordering (mirror dtd.sh list build)
# ---------------------------------------------------------------------------
def _refresh_cache_if_stale(force: bool = False):
    stale = force
    if not stale:
        try:
            d = json.loads(CACHE.read_text())
            upd = _dt.datetime.fromisoformat(d.get("updated", "1970-01-01"))
            stale = (_dt.datetime.now() - upd).total_seconds() > CACHE_MAX_AGE
        except Exception:
            stale = True
    if stale:
        try:
            subprocess.run(["/usr/bin/python3", str(DID_FAST), "--refresh-cache"],
                           capture_output=True, text=True, timeout=25)
        except Exception as e:
            print("WARN refresh-cache:", e, file=sys.stderr)

def _completed_names() -> set[str]:
    try:
        d = json.loads(DONE_FILE.read_text())
        if d.get("date") == _dt.date.today().isoformat():
            return {n.lower() for n in d.get("names", [])}
    except Exception:
        pass
    return set()

def build_tasks(force_refresh: bool = False) -> list[dict]:
    _refresh_cache_if_stale(force=force_refresh)
    try:
        d = json.loads(CACHE.read_text())
    except Exception as e:
        print("WARN read cache:", e, file=sys.stderr)
        return []

    today = _dt.date.today().isoformat()
    tomorrow = (_dt.date.today() + _dt.timedelta(days=1)).isoformat()

    def sec(key, bound):
        return [t for t in d.get(key, []) if isinstance(t, dict)
                and t.get("due") and t["due"] <= bound]

    today_tasks = [t for t in d.get("today", []) if isinstance(t, dict)
                   and t.get("due") and t["due"] <= today]
    has = lambda t, lab: lab in t.get("labels", [])

    rituals = [t for t in today_tasks if has(t, "-1neon")]
    neg1g = [t for t in today_tasks if has(t, "#-1g") and not has(t, "-1neon")]
    zeroneon = sec("0neon", tomorrow) + sec("夜neon", tomorrow)
    oneneon = sec("1neon", today)
    zerog = [t for t in today_tasks
             if has(t, "#0g") and not has(t, "-1neon") and not has(t, "#-1g")]
    critical = sec("关键路径", today)
    placed = lambda t: has(t, "-1neon") or has(t, "#-1g") or has(t, "#0g")
    rest = sorted([t for t in today_tasks if not placed(t)],
                  key=lambda t: _prank(t.get("priority")))
    ordered = rituals + neg1g + zeroneon + oneneon + zerog + critical + rest

    done_names = _completed_names()
    seen = set()
    out = []
    for t in ordered:
        tid = t.get("id")
        if not t.get("content") or tid in seen:
            continue
        seen.add(tid)
        raw = t["content"]
        clean = strip_ann(raw)
        if clean.lower() in done_names:
            continue
        display = t.get("short") or raw
        title = strip_ann(display) or raw
        est, pts = parse_est(raw)
        color, dom = color_of(t.get("labels", []))
        out.append({
            "id": tid,
            "raw": raw,
            "title": title,
            "est": est,
            "points": pts,
            "color": color,
            "domain": dom,
            "recurring": bool(t.get("recurring")),
        })
    return out

# ---------------------------------------------------------------------------
# Complete via did-fast (real /did: closes Todoist + writes Neon)
# ---------------------------------------------------------------------------
def complete(content: str) -> dict:
    try:
        proc = subprocess.run(["/usr/bin/python3", str(DID_FAST), content],
                              capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "did-fast timeout"}
    out = proc.stdout.strip()
    data, brace = None, out.find("{")
    if brace >= 0:
        try:
            data = json.loads(out[brace:])
        except Exception:
            data = None
    closed = False
    step = None
    if data and data.get("results"):
        r0 = data["results"][0]
        step = r0.get("step")
        closed = bool((r0.get("todoist") or {}).get("closed"))
    return {
        "ok": proc.returncode == 0,
        "closed": closed,
        "step": step,
        "future_skipped": bool(data and data.get("future_skipped")) if data else False,
        "stderr_tail": proc.stderr.strip()[-300:],
    }

# ---------------------------------------------------------------------------
# Flask
# ---------------------------------------------------------------------------
app = Flask(__name__)

@app.route("/api/tasks")
def api_tasks():
    try:
        force = request.args.get("refresh") == "1"
        return jsonify({"ok": True, "tasks": build_tasks(force_refresh=force)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/done", methods=["POST"])
def api_done():
    body = request.get_json(force=True, silent=True) or {}
    content = (body.get("content") or "").strip()
    if not content:
        return jsonify({"ok": False, "error": "no content"}), 400
    res = complete(content)
    res["points"] = parse_est(content)[1]
    return jsonify(res)

@app.route("/")
def index():
    return render_template_string(PAGE)

# ---------------------------------------------------------------------------
# Frontend — terminal-styled, single file, no external deps
# ---------------------------------------------------------------------------
PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>dtd</title>
<style>
  :root { --bg:#1b1b1b; --dim:#777; --go:#00e676; }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  html,body { margin:0; height:100%; background:var(--bg); color:#cfcfcf;
    font:15px/1.2 ui-monospace,"SF Mono",Menlo,Monaco,"Cascadia Mono",monospace;
    -webkit-font-smoothing:antialiased; }
  header { position:sticky; top:0; z-index:5;
    padding:calc(env(safe-area-inset-top) + 9px) 14px 8px;
    background:#1b1b1bee; backdrop-filter:blur(6px);
    display:flex; align-items:center; justify-content:space-between;
    border-bottom:1px solid #2a2a2a; }
  header .brand { font-weight:700; letter-spacing:1px; color:#cfcfcf; }
  header .brand b { color:var(--go); }
  .tally { color:var(--dim); font-variant-numeric:tabular-nums; }
  .tally b { color:var(--go); }
  #reload { background:none; border:1px solid #333; color:var(--dim);
    border-radius:6px; padding:3px 9px; font-family:inherit; font-size:15px; }
  main { padding:2px 0 calc(env(safe-area-inset-bottom) + 60px); }
  .row { position:relative; overflow:hidden; }
  .row .track { position:absolute; inset:0; background:var(--go); color:#003; font-weight:800;
    display:flex; align-items:center; padding-left:16px; opacity:0; }
  .row .track::before { content:"✓ done  +" ; white-space:pre; }
  .row .track .p { font-weight:800; }
  .line { position:relative; display:flex; align-items:center; gap:10px;
    padding:10px 14px; background:var(--bg); min-height:40px;
    transform:translateX(0); transition:transform .05s linear; will-change:transform;
    touch-action:pan-y; }
  .line.snap { transition:transform .22s cubic-bezier(.2,.7,.2,1); }
  .ttl { flex:1; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .est { white-space:nowrap; font-variant-numeric:tabular-nums; opacity:.92; }
  .empty,.loading { text-align:center; color:var(--dim); padding:60px 20px; }
  .toast { position:fixed; left:50%; bottom:calc(env(safe-area-inset-bottom) + 20px);
    transform:translateX(-50%) translateY(16px); background:var(--go); color:#003;
    font-weight:700; padding:9px 18px; border-radius:8px; opacity:0; transition:.22s;
    z-index:20; font-family:inherit; }
  .toast.show { opacity:1; transform:translateX(-50%) translateY(0); }
  .toast.err { background:#ff4081; color:#2a0010; }
</style>
</head>
<body>
<header>
  <div class="brand">d<b>t</b>d</div>
  <div class="tally"><b id="tot">0</b> 分 · <span id="cnt">0</span> done</div>
  <button id="reload">↻</button>
</header>
<main id="list"><div class="loading">loading…</div></main>
<div class="toast" id="toast"></div>
<script>
const list = document.getElementById('list');
const toastEl = document.getElementById('toast');
let total = 0, count = 0;

function toast(msg, err){
  toastEl.textContent = msg;
  toastEl.classList.toggle('err', !!err);
  toastEl.classList.add('show');
  clearTimeout(toastEl._t);
  toastEl._t = setTimeout(()=>toastEl.classList.remove('show'), 1500);
}

async function load(refresh){
  list.innerHTML = '<div class="loading">loading…</div>';
  try {
    const r = await fetch('/api/tasks' + (refresh?'?refresh=1':''));
    const d = await r.json();
    if(!d.ok) throw new Error(d.error||'fetch failed');
    render(d.tasks);
  } catch(e){ list.innerHTML = '<div class="empty">⚠ '+e.message+'</div>'; }
}

function render(tasks){
  if(!tasks.length){ list.innerHTML = '<div class="empty">🎉 nothing left for today</div>'; return; }
  list.innerHTML = '';
  for(const t of tasks) list.appendChild(makeRow(t));
}

function makeRow(t){
  const row = document.createElement('div');
  row.className = 'row';
  const track = document.createElement('div');
  track.className = 'track';
  track.innerHTML = '<span class="p">'+(t.points||0)+' 分</span>';
  row.appendChild(track);

  const line = document.createElement('div');
  line.className = 'line';
  line.style.color = t.color;
  const ttl = document.createElement('span');
  ttl.className = 'ttl';
  ttl.textContent = (t.recurring?'↻ ':'') + t.title;
  const est = document.createElement('span');
  est.className = 'est';
  est.textContent = t.est || '';
  line.appendChild(ttl); line.appendChild(est);
  row.appendChild(line);

  bindSwipe(row, line, track, t);
  return row;
}

function bindSwipe(row, line, track, t){
  let x0=null, dx=0, dragging=false;
  const W = () => row.offsetWidth;
  const start = x=>{ x0=x; dx=0; dragging=true; line.classList.remove('snap'); };
  const move = x=>{
    if(!dragging) return;
    dx = Math.max(0, x - x0);
    line.style.transform = 'translateX('+dx+'px)';
    track.style.opacity = Math.min(1, dx/(W()*0.4));
  };
  const end = ()=>{
    if(!dragging) return; dragging=false;
    line.classList.add('snap');
    if(dx > W()*0.42){ fly(row, line, t); }
    else { line.style.transform='translateX(0)'; track.style.opacity=0; }
  };
  line.addEventListener('touchstart', e=>start(e.touches[0].clientX), {passive:true});
  line.addEventListener('touchmove',  e=>move(e.touches[0].clientX),  {passive:true});
  line.addEventListener('touchend', end);
  line.addEventListener('mousedown', e=>{start(e.clientX);
    const mm=ev=>move(ev.clientX), mu=()=>{end();
      document.removeEventListener('mousemove',mm);document.removeEventListener('mouseup',mu);};
    document.addEventListener('mousemove',mm); document.addEventListener('mouseup',mu);});
}

function fly(row, line, t){
  line.style.transform = 'translateX('+(row.offsetWidth+40)+'px)';
  setTimeout(()=>{ row.style.height=row.offsetHeight+'px';
    requestAnimationFrame(()=>{ row.style.transition='.2s'; row.style.height='0'; row.style.opacity='0'; });
    setTimeout(()=>{ row.remove();
      if(!document.querySelector('.row')) list.innerHTML='<div class="empty">🎉 nothing left for today</div>';
    }, 210);
  }, 170);
  commit(t);
}

async function commit(t){
  total += (t.points||0); count += 1;
  document.getElementById('tot').textContent = total;
  document.getElementById('cnt').textContent = count;
  try {
    const r = await fetch('/api/done', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({id:t.id, content:t.raw})});
    const d = await r.json();
    if(!d.ok){ toast('saved · Neon write failed', true); return; }
    if(d.closed) toast('+'+(t.points||0)+' 分 ✓');
    else if(d.future_skipped) toast('+'+(t.points||0)+' 分 · recurring');
    else toast('+'+(t.points||0)+' 分');
  } catch(e){ toast('offline · not synced', true); }
}

document.getElementById('reload').onclick = ()=>{ total=0;count=0;
  document.getElementById('tot').textContent=0; document.getElementById('cnt').textContent=0;
  load(true); };
load(false);
</script>
</body>
</html>"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
