#!/usr/bin/env python3
"""
dtd — Do The Damn thing. A swipeable, iPhone-first task list that fronts
Todoist and awards Neon points on completion.

Swipe a card right  → runs the /did pipeline (did-fast.py): closes the
Todoist task AND writes its points to Neon, exactly like the CLI.

Run:   python3 dtd.py           (binds 0.0.0.0:5559)
Open:  http://ix:5559           (from the phone, same as the dashboard)

MVP scope: shows "today | overdue" tasks. Right-swipe completes. No undo.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from flask import Flask, jsonify, render_template_string, request

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PORT = 5560
DID_FAST = Path.home() / "i446-monorepo/tools/did/did-fast.py"
TOKEN_FILE = Path.home() / ".config/todoist/token"
TODOIST_BASE = "https://api.todoist.com/api/v1"

def _token() -> str:
    return TOKEN_FILE.read_text().strip()

# Domain label → accent hue (HSL hue degrees). First matching label wins.
DOMAIN_HUE = {
    "i9": 210, "f693": 265, "f694": 265,          # work / reports — blue/violet
    "m5x2": 140,                                   # real estate — green
    "qz12": 160,                                   # finance — teal-green
    "g245": 280, "infra": 230, "n156": 230, "cc": 230,  # goals/infra — purple/indigo
    "hcmc": 30, "hcmc2": 30,                        # media — orange
    "hcb": 350, "hcbp": 340,                        # health — red/pink
    "hcm": 250,                                     # mindfulness — indigo
    "hci": 200,                                     # image — steel
    "xk87": 175, "xk88": 190,                       # family / Louisa — cyan
    "s897": 45,                                     # social — amber
    "i447": 220, "i444": 220, "i446": 220,          # infra admin — slate-blue
    "epcn": 40, "m828": 300, "家": 330,             # misc
}
DOMAINS = set(DOMAIN_HUE)

import re
_PT_BRACKET = re.compile(r"\[(\d+)\]")   # [N] base points
_PT_CURLY = re.compile(r"\{(\d+)\}")     # {N} bonus / 0g points
_STRIP = re.compile(r"\s*(\(\d+\)|\[\d+\]|\{\d+\}|@\w+|\*\*|😈|🔥)\s*")

def parse_points(content: str) -> int:
    m = _PT_BRACKET.search(content)
    if m:
        return int(m.group(1))
    m = _PT_CURLY.search(content)
    if m:
        return int(m.group(1))
    return 0

def clean_title(content: str) -> str:
    t = _STRIP.sub(" ", content)
    return re.sub(r"\s+", " ", t).strip()

def domain_of(labels: list[str]) -> str | None:
    for lab in labels:
        if lab in DOMAINS:
            return lab
    return None

# ---------------------------------------------------------------------------
# Todoist fetch (today | overdue) — mirrors did-fast.fetch_today
# ---------------------------------------------------------------------------
def fetch_today() -> list[dict]:
    tok = _token()
    all_tasks: list[dict] = []
    cursor = None
    for _ in range(5):
        url = f"{TODOIST_BASE}/tasks/filter?query={quote('today | overdue')}&limit=200"
        if cursor:
            url += f"&cursor={cursor}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"})
        raw = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    raw = json.loads(resp.read())
                break
            except Exception as e:
                print(f"WARN fetch_today attempt {attempt+1}: {e}", file=sys.stderr)
                if attempt < 2:
                    time.sleep(1)
        if raw is None:
            break
        tasks = raw if isinstance(raw, list) else raw.get("results", [])
        for t in tasks:
            due = (t.get("due") or {})
            all_tasks.append({
                "id": t.get("id", ""),
                "content": t.get("content", ""),
                "labels": t.get("labels", []),
                "priority": t.get("priority", 1),
                "due": due.get("date", "") or "",
            })
        cursor = raw.get("next_cursor") if isinstance(raw, dict) else None
        if not cursor:
            break

    today_iso = datetime.now().strftime("%Y-%m-%d")
    out = []
    seen = set()
    for t in all_tasks:
        if not t["due"] or t["due"][:10] > today_iso:  # drop future recurring
            continue
        if t["id"] in seen:
            continue
        seen.add(t["id"])
        pts = parse_points(t["content"])
        dom = domain_of(t["labels"])
        out.append({
            "id": t["id"],
            "title": clean_title(t["content"]) or t["content"],
            "raw": t["content"],
            "points": pts,
            "domain": dom or "",
            "hue": DOMAIN_HUE.get(dom, 220),
            "overdue": t["due"][:10] < today_iso,
            "priority": t["priority"],
        })
    # Highest points first, then priority (p1=4 highest in v1 API)
    out.sort(key=lambda x: (-x["points"], -(x["priority"] or 0)))
    return out

# ---------------------------------------------------------------------------
# Complete via did-fast (real /did: closes Todoist + writes Neon)
# ---------------------------------------------------------------------------
def complete(content: str) -> dict:
    try:
        proc = subprocess.run(
            ["/usr/bin/python3", str(DID_FAST), content],
            capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "did-fast timeout"}
    out = proc.stdout.strip()
    data = None
    # did-fast prefixes human lines before the JSON block; grab the JSON.
    brace = out.find("{")
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
        td = r0.get("todoist") or {}
        closed = bool(td.get("closed"))
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
        return jsonify({"ok": True, "tasks": fetch_today()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/done", methods=["POST"])
def api_done():
    body = request.get_json(force=True, silent=True) or {}
    content = (body.get("content") or "").strip()
    if not content:
        return jsonify({"ok": False, "error": "no content"}), 400
    res = complete(content)
    res["points"] = parse_points(content)
    return jsonify(res)

@app.route("/")
def index():
    return render_template_string(PAGE)

# ---------------------------------------------------------------------------
# Frontend — single file, no external deps
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
  :root { --bg:#0b0d10; --card:#171a1f; --edge:#242a31; --txt:#e8ecf1; --dim:#8b95a1; --go:#1fbf6b; }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  html,body { margin:0; height:100%; background:var(--bg); color:var(--txt);
    font:16px/1.35 -apple-system,BlinkMacSystemFont,"SF Pro Text",system-ui,sans-serif; }
  header { position:sticky; top:0; z-index:5; padding:calc(env(safe-area-inset-top) + 12px) 16px 10px;
    background:linear-gradient(#0b0d10ee,#0b0d10cc); backdrop-filter:blur(8px);
    display:flex; align-items:baseline; justify-content:space-between; }
  header h1 { margin:0; font-size:19px; letter-spacing:.5px; font-weight:700; }
  header h1 span { color:var(--go); }
  .tally { font-variant-numeric:tabular-nums; font-weight:700; }
  .tally b { color:var(--go); font-size:20px; }
  .tally small { color:var(--dim); font-weight:400; }
  #reload { background:none; border:1px solid var(--edge); color:var(--dim);
    border-radius:8px; padding:4px 9px; font-size:13px; }
  main { padding:6px 12px calc(env(safe-area-inset-bottom) + 40px); }
  .card { position:relative; margin:9px 0; border-radius:14px; overflow:hidden;
    background:var(--card); border:1px solid var(--edge); touch-action:pan-y; }
  .card .go { position:absolute; inset:0; display:flex; align-items:center; padding-left:22px;
    background:var(--go); color:#062; font-weight:800; font-size:17px; opacity:0; }
  .card .go::before { content:"✓ done"; }
  .inner { position:relative; background:var(--card); padding:14px 15px;
    border-left:4px solid hsl(var(--h) 55% 52%);
    transform:translateX(0); transition:transform .05s linear; will-change:transform; }
  .inner.snap { transition:transform .22s cubic-bezier(.2,.7,.2,1); }
  .row { display:flex; align-items:center; gap:12px; }
  .ttl { flex:1; font-size:16px; font-weight:600; }
  .ttl.overdue::after { content:"overdue"; margin-left:8px; font-size:11px; font-weight:700;
    color:#ff6b6b; border:1px solid #ff6b6b55; padding:1px 5px; border-radius:6px; vertical-align:middle; }
  .pts { font-variant-numeric:tabular-nums; font-weight:800; font-size:15px;
    color:hsl(var(--h) 65% 62%); white-space:nowrap; }
  .pts small { color:var(--dim); font-weight:500; }
  .dom { margin-top:3px; font-size:12px; color:var(--dim); text-transform:uppercase; letter-spacing:.6px; }
  .empty,.loading { text-align:center; color:var(--dim); padding:60px 20px; }
  .toast { position:fixed; left:50%; bottom:calc(env(safe-area-inset-bottom) + 22px);
    transform:translateX(-50%) translateY(20px); background:var(--go); color:#053; font-weight:800;
    padding:11px 20px; border-radius:999px; opacity:0; transition:.25s; z-index:20; box-shadow:0 6px 20px #0006; }
  .toast.show { opacity:1; transform:translateX(-50%) translateY(0); }
  .toast.err { background:#ff6b6b; color:#3a0000; }
  .hint { text-align:center; color:var(--dim); font-size:12px; padding:2px 0 8px; }
</style>
</head>
<body>
<header>
  <h1>d<span>t</span>d</h1>
  <div class="tally"><b id="tot">0</b> <small>分 · <span id="cnt">0</span> done</small></div>
  <button id="reload">↻</button>
</header>
<div class="hint">swipe a card right to complete →</div>
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
  toastEl._t = setTimeout(()=>toastEl.classList.remove('show'), 1600);
}

async function load(){
  list.innerHTML = '<div class="loading">loading…</div>';
  try {
    const r = await fetch('/api/tasks');
    const d = await r.json();
    if(!d.ok) throw new Error(d.error||'fetch failed');
    render(d.tasks);
  } catch(e){ list.innerHTML = '<div class="empty">⚠ '+e.message+'</div>'; }
}

function render(tasks){
  if(!tasks.length){ list.innerHTML = '<div class="empty">🎉 nothing left for today</div>'; return; }
  list.innerHTML = '';
  for(const t of tasks) list.appendChild(makeCard(t));
}

function makeCard(t){
  const card = document.createElement('div');
  card.className = 'card';
  const go = document.createElement('div'); go.className='go'; card.appendChild(go);
  const inner = document.createElement('div');
  inner.className = 'inner';
  inner.style.setProperty('--h', t.hue);
  inner.innerHTML =
    '<div class="row">'+
      '<div class="ttl'+(t.overdue?' overdue':'')+'"></div>'+
      '<div class="pts">'+(t.points||0)+' <small>分</small></div>'+
    '</div>'+
    (t.domain?'<div class="dom">'+t.domain+'</div>':'');
  inner.querySelector('.ttl').textContent = t.title;
  card.appendChild(inner);
  bindSwipe(card, inner, go, t);
  return card;
}

function bindSwipe(card, inner, go, t){
  let x0=null, dx=0, dragging=false;
  const W = () => card.offsetWidth;
  const start = (x)=>{ x0=x; dx=0; dragging=true; inner.classList.remove('snap'); };
  const move = (x)=>{
    if(!dragging) return;
    dx = Math.max(0, x - x0);            // right only
    inner.style.transform = 'translateX('+dx+'px)';
    go.style.opacity = Math.min(1, dx/(W()*0.4));
  };
  const end = ()=>{
    if(!dragging) return; dragging=false;
    inner.classList.add('snap');
    if(dx > W()*0.42){ fly(card, inner, t); }
    else { inner.style.transform='translateX(0)'; go.style.opacity=0; }
  };
  inner.addEventListener('touchstart', e=>start(e.touches[0].clientX), {passive:true});
  inner.addEventListener('touchmove',  e=>move(e.touches[0].clientX),  {passive:true});
  inner.addEventListener('touchend', end);
  // mouse (desktop testing)
  inner.addEventListener('mousedown', e=>{start(e.clientX);
    const mm=ev=>move(ev.clientX), mu=()=>{end();document.removeEventListener('mousemove',mm);document.removeEventListener('mouseup',mu);};
    document.addEventListener('mousemove',mm); document.addEventListener('mouseup',mu);});
}

function fly(card, inner, t){
  inner.style.transform = 'translateX('+ (card.offsetWidth+40) +'px)';
  card._done = true;
  setTimeout(()=>{ card.style.height = card.offsetHeight+'px';
    requestAnimationFrame(()=>{ card.style.transition='.2s'; card.style.height='0'; card.style.margin='0'; card.style.opacity='0'; });
    setTimeout(()=>card.remove(), 220);
  }, 180);
  commit(t);
}

async function commit(t){
  // optimistic tally
  total += (t.points||0); count += 1;
  document.getElementById('tot').textContent = total;
  document.getElementById('cnt').textContent = count;
  try {
    const r = await fetch('/api/done', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({id:t.id, content:t.raw})});
    const d = await r.json();
    if(!d.ok){ toast('saved locally · Neon write failed', true); return; }
    if(d.closed) toast('+'+(t.points||0)+' 分 ✓');
    else if(d.future_skipped) toast('+'+(t.points||0)+' 分 · recurring');
    else toast('+'+(t.points||0)+' 分');
  } catch(e){ toast('offline · not synced', true); }
  if(!document.querySelector('.card')){ list.innerHTML = '<div class="empty">🎉 nothing left for today</div>'; }
}

document.getElementById('reload').onclick = ()=>{ total=0;count=0;
  document.getElementById('tot').textContent=0; document.getElementById('cnt').textContent=0; load(); };
load();
</script>
</body>
</html>"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
