#!/usr/bin/env python3
"""卯 — wakeup GUI server (-1₦ forced-linear ritual).

A mobile web app JM opens the moment he wakes up. It walks him through the five
-1₦ block-ritual icons in an *activating*, forced-linear order — no skips, one
big card at a time, designed for a half-asleep thumb:

  ☀️ prayer → 🎯 goal → ⏱️ timer → ✓ task → 📧 inbox

This is the phone-native counterpart to the /inbound TUI. The phone is a dumb
fullscreen client; this server (on ix, the always-on Mac) is the brain. It
reuses the tested primitives in `-2n.py` (markers, -1g writer, Toggl, habit
reader) instead of duplicating them.

A *ritual instance* is persisted to ~/.cache/wakeup/ritual.json so that:
  • the 2h block is frozen for the whole run (no mid-flow rollover corruption),
  • each step's side effect fires exactly once (idempotent against double-taps),
  • a LaunchAgent restart resumes the in-progress ritual instead of resetting.

Semantics note: the ✅ task marker means "task acknowledged at wakeup" (the human
ritual is done). The actual /did logging to 0n/Todoist is best-effort detached
(JM should not wait ~120s at 5am); its output is logged to ~/.cache/wakeup/.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import time
import secrets
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, jsonify, request, render_template_string

_HERE = Path(__file__).resolve().parent
_TWO_N_PATH = _HERE / "-2n.py"
_TOGGL_CLI = Path.home() / "i446-monorepo/mcp/toggl_server/toggl_cli.py"

STATE_DIR = Path.home() / ".cache" / "wakeup"
RITUAL_FILE = STATE_DIR / "ritual.json"

STEPS = ["prayer", "goal", "timer", "task", "inbox"]
TASK_MARKER = "✅"
TIMER_PROJECT = "g245"
TIMER_FRESH_MIN = 180  # a running timer counts as done only if started ≤3h ago


def _load_two_n():
    spec = importlib.util.spec_from_file_location("_two_n", _TWO_N_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module from {_TWO_N_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


M = _load_two_n()
app = Flask(__name__)


@app.after_request
def _no_store(resp):
    # A half-asleep user must never act on a restored/stale page.
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


# ── Toggl helpers ─────────────────────────────────────────────────────────


def _running_timer():
    """Return (desc, start_hhmm) of the active Toggl entry, or (None, None)."""
    try:
        r = subprocess.run(
            ["python3", str(_TOGGL_CLI), "current"],
            capture_output=True, text=True, timeout=6,
        )
    except Exception:
        return None, None
    if r.returncode != 0 or not r.stdout.startswith("Running:"):
        return None, None
    line = r.stdout.split("Running:", 1)[1].strip()
    # e.g. "15:28-running c702 loan @m5x2 (running) [id:4443348139]"
    m = re.match(r"(\d{1,2}:\d{2})", line)
    start = m.group(1) if m else None
    desc = re.sub(r"^\d{1,2}:\d{2}-\S+\s*", "", line)
    desc = re.sub(r"\s*\(running\)\s*", " ", desc)
    desc = re.sub(r"\s*\[id:[^\]]*\]\s*$", "", desc).strip()
    return (desc or "running timer"), start


def _timer_fresh():
    """True if a timer is running AND started within TIMER_FRESH_MIN minutes.

    A stale overnight timer must NOT auto-complete the activation step."""
    desc, start = _running_timer()
    if not start:
        return False, desc
    try:
        sh, sm = (int(x) for x in start.split(":"))
        now = datetime.now()
        started = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
        if started > now:  # clock crossed midnight → started yesterday
            started -= timedelta(days=1)
        age_min = (now - started).total_seconds() / 60.0
        return (age_min <= TIMER_FRESH_MIN), desc
    except Exception:
        return (desc is not None), desc


# ── Build-order marker helpers (task ✅ — generic, mirrors -2n primitives) ──


def _has_block_marker(block_name, marker):
    bo = M.BUILD_ORDER
    if not bo.exists():
        return False
    text = bo.read_text()
    if "## -1₲" not in text:
        return False
    section = text[text.index("## -1₲"):]
    for line in section.split("\n"):
        if line.startswith("- ") and not line.startswith("    "):
            if M._block_name_from_header(line) == block_name and marker in line:
                return True
    return False


def _write_block_marker(block_name, marker):
    """Append a marker to the block header line. Idempotent."""
    bo = M.BUILD_ORDER
    if not bo.exists():
        return
    text = bo.read_text()
    if "## -1₲" not in text or _has_block_marker(block_name, marker):
        return
    out, appended = [], False
    for line in text.split("\n"):
        if (not appended and line.startswith("- ")
                and not line.startswith("    ")
                and M._block_name_from_header(line) == block_name):
            out.append(f"{line.rstrip()} {marker}")
            appended = True
        else:
            out.append(line)
    if appended:
        bo.write_text("\n".join(out))


def _spawn_did(task):
    """Fire-and-forget `claude -p /did <task>` (detached). Best-effort 0n/
    Todoist sync; the ritual does not block on it."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    log_fh = open(STATE_DIR / f"did-{int(time.time())}.log", "wb")
    return subprocess.Popen(
        ["claude", "-p", f"/did {task}", "--allowedTools",
         "Skill,Bash,Read,Edit,Write,mcp__todoist__complete-tasks,"
         "mcp__todoist__find-tasks"],
        stdin=subprocess.DEVNULL, stdout=log_fh, stderr=log_fh,
        start_new_session=True, close_fds=True,
    )


# ── Ritual instance (frozen block + idempotency + crash-resume) ────────────


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _ambient_done(block_name):
    """Steps already satisfied by underlying signals at ritual creation.

    Respects JM's no-busywork preference: don't force a step whose work is
    already done. The task step is never seeded (always do one at wakeup)."""
    done = []
    if M.has_prayer_marker(block_name):
        done.append("prayer")
    goals = M.read_block_goals().get(block_name, [])
    if goals and any(g.strip() for g in goals):
        done.append("goal")
    fresh, _ = _timer_fresh()
    if fresh:
        done.append("timer")
    if _has_block_marker(block_name, M.INBOX_MARKER):
        done.append("inbox")
    return done


def _save_ritual(r):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    RITUAL_FILE.write_text(json.dumps(r))


def _new_ritual():
    idx, name, start, end = M.get_current_block()
    r = {
        "date": _today(),
        "ritual_id": f"{_today()}-{name}-{secrets.token_hex(3)}",
        "block": name, "block_idx": idx,
        "block_start": start, "block_end": end,
        "done": _ambient_done(name),
        "goal": [], "task": None, "timer_desc": None,
        "created_at": time.time(), "completed_at": None,
    }
    _save_ritual(r)
    return r


def _load_ritual():
    if not RITUAL_FILE.exists():
        return None
    try:
        r = json.loads(RITUAL_FILE.read_text())
    except Exception:
        return None
    return r if r.get("date") == _today() else None


def _get_ritual():
    """Return today's ritual: resume if in-progress, start fresh if none/stale,
    or start fresh if a completed ritual's block has rolled over."""
    r = _load_ritual()
    if r is None:
        return _new_ritual()
    if r.get("completed_at"):
        _, name, _, _ = M.get_current_block()
        if name != r.get("block"):
            return _new_ritual()
    return r


def _next(r):
    for s in STEPS:
        if s not in r["done"]:
            return s
    return None


def _state(r):
    nxt = _next(r)
    out = {
        "ritual_id": r["ritual_id"],
        "block": r["block"],
        "block_start": r["block_start"],
        "block_end": r["block_end"],
        "done": r["done"],
        "steps": STEPS,
        "next": nxt,
        "task": r.get("task"),
        "timer_desc": r.get("timer_desc"),
        "completed_at": r.get("completed_at"),
    }
    if nxt == "timer":
        desc, _ = _running_timer()
        if desc:
            out["running_timer"] = desc
    if nxt == "goal":
        out["existing_goals"] = M.read_block_goals().get(r["block"], [])
    if nxt == "timer" and not out.get("running_timer"):
        # offer the top goal as the default timer description
        goals = r.get("goal") or M.read_block_goals().get(r["block"], [])
        if goals:
            out["suggested_desc"] = re.sub(r"\s*\{?\d+\}?\s*$", "",
                                           goals[0]).strip()
    return out


def _stale(r):
    """409 if the client posted against a different ritual_id (page restore)."""
    if not request.is_json:
        return False
    rid = (request.get_json(silent=True) or {}).get("ritual_id")
    return bool(rid) and rid != r["ritual_id"]


# ── API ────────────────────────────────────────────────────────────────────


@app.route("/healthz")
def healthz():
    ok = _TWO_N_PATH.exists() and M.BUILD_ORDER.exists() and _TOGGL_CLI.exists()
    return jsonify({"ok": ok, "build_order": M.BUILD_ORDER.exists(),
                    "toggl_cli": _TOGGL_CLI.exists()}), (200 if ok else 503)


@app.route("/api/state")
def api_state():
    return jsonify(_state(_get_ritual()))


@app.route("/api/suggestions")
def api_suggestions():
    r = _get_ritual()
    sugg = (M.fetch_block_suggestions(r["block"], r["block_start"], r["block_end"])
            or M.fetch_suggested_goals(max_results=3) or [])
    existing = M.read_block_goals().get(r["block"], [])
    return jsonify({"suggestions": sugg, "existing": existing})


@app.route("/api/habits")
def api_habits():
    return jsonify({"habits": M._unfinished_0n_today()})


@app.route("/api/prayer", methods=["POST"])
def api_prayer():
    r = _get_ritual()
    if _stale(r):
        return jsonify({"error": "stale", "state": _state(r)}), 409
    if "prayer" not in r["done"]:
        M.write_prayer_marker(r["block"])
        r["done"].append("prayer")
        _save_ritual(r)
    return jsonify(_state(r))


@app.route("/api/goal", methods=["POST"])
def api_goal():
    r = _get_ritual()
    if _stale(r):
        return jsonify({"error": "stale", "state": _state(r)}), 409
    if "goal" not in r["done"]:
        data = request.get_json(silent=True) or {}
        raw = data.get("goals")
        goals = []
        if isinstance(raw, str):
            goals = M.parse_goals_text(raw)
        elif isinstance(raw, list):
            for g in raw:
                if isinstance(g, str):
                    goals.extend(M.parse_goals_text(g))
        if not goals:  # confirming existing goals with no edits
            goals = [g.strip() for g in M.read_block_goals().get(r["block"], [])
                     if g.strip()]
        if not goals:
            return jsonify({"error": "empty", "state": _state(r)}), 400
        M.write_block_goals(r["block"], goals)
        M.spawn_1g_background("\n".join(goals))
        r["goal"] = goals
        r["done"].append("goal")
        _save_ritual(r)
    return jsonify(_state(r))


@app.route("/api/timer", methods=["POST"])
def api_timer():
    r = _get_ritual()
    if _stale(r):
        return jsonify({"error": "stale", "state": _state(r)}), 409
    if "timer" not in r["done"]:
        data = request.get_json(silent=True) or {}
        if data.get("keep"):
            desc, _ = _running_timer()
            if not desc:
                return jsonify({"error": "no_timer", "state": _state(r)}), 409
            r["timer_desc"] = desc
        else:
            desc = (data.get("desc") or "").strip()
            if not desc:
                return jsonify({"error": "empty", "state": _state(r)}), 400
            M.start_toggl(desc, TIMER_PROJECT)
            time.sleep(1.0)
            running, _ = _running_timer()
            if not running:
                return jsonify({"error": "start_failed", "state": _state(r)}), 409
            r["timer_desc"] = desc
        r["done"].append("timer")
        _save_ritual(r)
    return jsonify(_state(r))


@app.route("/api/task", methods=["POST"])
def api_task():
    r = _get_ritual()
    if _stale(r):
        return jsonify({"error": "stale", "state": _state(r)}), 409
    if "task" not in r["done"]:
        data = request.get_json(silent=True) or {}
        task = (data.get("task") or "").strip()
        if not task:
            return jsonify({"error": "empty", "state": _state(r)}), 400
        _spawn_did(task)
        _write_block_marker(r["block"], TASK_MARKER)
        r["task"] = task
        r["done"].append("task")
        _save_ritual(r)
    return jsonify(_state(r))


@app.route("/api/inbox", methods=["POST"])
def api_inbox():
    r = _get_ritual()
    if _stale(r):
        return jsonify({"error": "stale", "state": _state(r)}), 409
    if "inbox" not in r["done"]:
        M.write_inbox_marker(r["block"])
        r["done"].append("inbox")
    if _next(r) is None and not r.get("completed_at"):
        r["completed_at"] = time.time()
    _save_ritual(r)
    return jsonify(_state(r))


@app.route("/")
def index():
    return render_template_string(PAGE)


PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, maximum-scale=1, user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="卯">
<meta name="theme-color" content="#0a0e14">
<title>卯 wakeup</title>
<style>
  :root{
    --bg:#0a0e14; --card:#121821; --ink:#e8f0f8; --dim:#7c8a9a;
    --neon:#9bff3b; --neon2:#00e0ff; --purple:#b46bff; --warn:#ffb347; --err:#ff5d6c;
  }
  *{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
  html,body{margin:0;height:100%}
  body{
    background:var(--bg);color:var(--ink);
    font:500 18px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
    display:flex;flex-direction:column;min-height:100vh;
    padding:env(safe-area-inset-top) 0 env(safe-area-inset-bottom);
    overscroll-behavior:none;
  }
  header{padding:18px 20px 6px;display:flex;align-items:baseline;gap:10px}
  header .blk{font-size:30px;font-weight:800;letter-spacing:.02em}
  header .time{color:var(--dim);font-size:14px}
  .dots{display:flex;gap:8px;padding:4px 20px 14px}
  .dot{flex:1;height:6px;border-radius:3px;background:#222c38;transition:.3s}
  .dot.done{background:var(--neon);box-shadow:0 0 10px var(--neon)}
  .dot.cur{background:var(--neon2);box-shadow:0 0 12px var(--neon2)}
  main{flex:1;display:flex;flex-direction:column;justify-content:center;padding:8px 20px 24px}
  .card{
    background:var(--card);border-radius:24px;padding:28px 22px;
    border:1px solid #1d2733;box-shadow:0 14px 40px rgba(0,0,0,.5);
  }
  .icon{font-size:64px;text-align:center;line-height:1;margin-bottom:6px}
  h1{font-size:26px;margin:6px 0 4px;text-align:center;font-weight:800}
  .sub{color:var(--dim);text-align:center;margin:0 0 22px;font-size:15px}
  button.big{
    width:100%;padding:22px;border:none;border-radius:18px;
    font-size:21px;font-weight:800;color:#06120a;background:var(--neon);
    box-shadow:0 0 22px rgba(155,255,59,.45);cursor:pointer;
    transition:transform .08s, opacity .2s;
  }
  button.big:active{transform:scale(.97)}
  button.big.alt{background:transparent;color:var(--neon2);
    border:2px solid var(--neon2);box-shadow:none;margin-top:12px;font-size:18px}
  button[disabled]{opacity:.45;pointer-events:none}
  .chips{display:flex;flex-direction:column;gap:10px;margin-bottom:16px}
  .chip{
    padding:16px 18px;border-radius:14px;background:#0e151e;border:2px solid #243040;
    color:var(--ink);font-size:17px;text-align:left;cursor:pointer;
    display:flex;align-items:center;gap:10px;transition:.12s;
  }
  .chip .src{font-size:11px;color:var(--dim);margin-left:auto;text-transform:uppercase;letter-spacing:.05em}
  .chip.sel{border-color:var(--neon);background:rgba(155,255,59,.10);box-shadow:0 0 14px rgba(155,255,59,.25)}
  .chip.sel::before{content:"✓ ";color:var(--neon);font-weight:800}
  input[type=text]{
    width:100%;padding:18px;border-radius:14px;border:2px solid #243040;
    background:#0e151e;color:var(--ink);font-size:18px;margin-bottom:14px;
  }
  input[type=text]:focus{outline:none;border-color:var(--neon2)}
  .hint{color:var(--dim);font-size:13px;text-align:center;margin-top:14px;min-height:18px}
  .loading{color:var(--dim);text-align:center;padding:30px}
  .done-screen{text-align:center}
  .done-screen .big-emoji{font-size:88px}
  .done-screen h1{font-size:30px;color:var(--neon)}
  .spinner{display:inline-block;width:18px;height:18px;border:3px solid #2a3340;
    border-top-color:var(--neon2);border-radius:50%;animation:spin .8s linear infinite;vertical-align:-3px}
  @keyframes spin{to{transform:rotate(360deg)}}
</style>
</head>
<body>
  <header><span class="blk" id="blk">卯</span><span class="time" id="time"></span></header>
  <div class="dots" id="dots"></div>
  <main id="main"><div class="loading">waking…</div></main>

<script>
const STEPS=["prayer","goal","timer","task","inbox"];
const META={
  prayer:{icon:"☀️",title:"صلاة",sub:"Where is the sun? Get up and find it."},
  goal:{icon:"🎯",title:"intention",sub:"What matters this block?"},
  timer:{icon:"⏱️",title:"commit",sub:"Start the clock. Begin."},
  task:{icon:"✓",title:"one thing",sub:"Do one thing now, then log it."},
  inbox:{icon:"📧",title:"close -1₦",sub:"Process and close the row."},
};
let state=null, busy=false;
const $=s=>document.querySelector(s);

function fmtTime(){const d=new Date();return d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});}

async function getState(){const r=await fetch('/api/state',{cache:'no-store'});return r.json();}
async function post(path,body){
  body=body||{}; body.ritual_id=state&&state.ritual_id;
  const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},
    cache:'no-store',body:JSON.stringify(body)});
  const j=await r.json();
  if(r.status===409 && j.state){ // stale page or block rollover → resync
    state=j.state; render(); return null;
  }
  if(!r.ok){ return {error:j.error||'error'}; }
  return j;
}

function renderDots(){
  const d=$('#dots'); d.innerHTML='';
  STEPS.forEach(s=>{
    const e=document.createElement('div'); e.className='dot';
    if(state.done.includes(s)) e.classList.add('done');
    else if(s===state.next) e.classList.add('cur');
    d.appendChild(e);
  });
}

function header(){
  $('#blk').textContent=state.block+ ' · wakeup';
  $('#time').textContent=(state.block_start||'')+'–'+(state.block_end||'');
}

function card(inner){return `<div class="card">${inner}</div>`;}
function head(step){const m=META[step];return `<div class="icon">${m.icon}</div><h1>${m.title}</h1><p class="sub">${m.sub}</p>`;}
function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}

function render(){
  header(); renderDots();
  const m=$('#main');
  if(state.next===null){ // complete
    m.innerHTML=`<div class="card done-screen"><div class="big-emoji">🌅</div>
      <h1>-1₦ complete</h1><p class="sub">You're up and moving.${state.task?'<br>did: '+esc(state.task):''}</p></div>`;
    document.body.style.boxShadow='inset 0 0 120px rgba(155,255,59,.12)';
    return;
  }
  ({prayer:rPrayer,goal:rGoal,timer:rTimer,task:rTask,inbox:rInbox}[state.next])(m);
}

// ── step renderers ──
function rPrayer(m){
  m.innerHTML=card(head('prayer')+`<button class="big" id="go">☀️ I'm up</button>
    <div class="hint" id="h"></div>`);
  $('#go').onclick=async()=>{await act($('#go'),()=>post('/api/prayer'));};
}

function rGoal(m){
  m.innerHTML=card(head('goal')+`<div id="chips" class="chips"><div class="loading"><span class="spinner"></span> loading…</div></div>
    <input type="text" id="txt" placeholder="or type a goal" autocapitalize="none">
    <button class="big" id="go">🎯 set intention</button><div class="hint" id="h"></div>`);
  const sel=new Set();
  fetch('/api/suggestions',{cache:'no-store'}).then(r=>r.json()).then(d=>{
    const c=$('#chips'); c.innerHTML='';
    const items=[];
    (d.existing||[]).forEach(g=>items.push({content:g,source:'current'}));
    (d.suggestions||[]).forEach(s=>items.push(s));
    if(!items.length){c.innerHTML='<div class="hint">no suggestions — type one</div>';return;}
    items.forEach(it=>{
      const el=document.createElement('div'); el.className='chip';
      el.innerHTML=`<span>${esc(it.content)}</span><span class="src">${esc(it.source||'')}</span>`;
      el.onclick=()=>{el.classList.toggle('sel');
        el.classList.contains('sel')?sel.add(it.content):sel.delete(it.content);};
      c.appendChild(el);
    });
  }).catch(()=>{$('#chips').innerHTML='<div class="hint">suggestions unavailable — type one</div>';});
  $('#go').onclick=async()=>{
    const goals=[...sel]; const t=$('#txt').value.trim(); if(t)goals.push(t);
    if(!goals.length){$('#h').textContent='pick or type at least one';return;}
    await act($('#go'),()=>post('/api/goal',{goals}));
  };
}

function rTimer(m){
  if(state.running_timer){
    m.innerHTML=card(head('timer')+`<p class="sub" style="margin-top:-10px">running: <b>${esc(state.running_timer)}</b></p>
      <button class="big" id="keep">▶ keep going</button>
      <button class="big alt" id="newbtn">start something new</button><div class="hint" id="h"></div>`);
    $('#keep').onclick=async()=>{await act($('#keep'),()=>post('/api/timer',{keep:true}));};
    $('#newbtn').onclick=()=>startNew(m);
  } else { startNew(m); }
}
function startNew(m){
  const def=state.suggested_desc||'';
  m.innerHTML=card(head('timer')+`<input type="text" id="txt" value="${esc(def)}" placeholder="what are you starting?">
    <button class="big" id="go">⏱️ start ▶ ${TIMER_PROJ()}</button><div class="hint" id="h"></div>`);
  $('#go').onclick=async()=>{
    const desc=$('#txt').value.trim(); if(!desc){$('#h').textContent='type what to start';return;}
    const r=await act($('#go'),()=>post('/api/timer',{desc}));
  };
}
function TIMER_PROJ(){return '→ g245';}

function rTask(m){
  m.innerHTML=card(head('task')+`<div id="habits" class="chips"><div class="loading"><span class="spinner"></span> loading…</div></div>
    <input type="text" id="txt" placeholder="or type what you did" autocapitalize="none">
    <button class="big" id="go">✓ done</button><div class="hint" id="h"></div>`);
  fetch('/api/habits',{cache:'no-store'}).then(r=>r.json()).then(d=>{
    const c=$('#habits'); c.innerHTML='';
    const hs=(d.habits||[]);
    if(!hs.length){c.innerHTML='<div class="hint">no unfinished habits — type a task</div>';return;}
    hs.forEach(h=>{
      const el=document.createElement('div'); el.className='chip';
      el.innerHTML=`<span>${esc(h.habit)}</span>`;
      el.onclick=()=>act(el,()=>post('/api/task',{task:h.habit}));
      c.appendChild(el);
    });
  }).catch(()=>{$('#habits').innerHTML='<div class="hint">habits unavailable — type one</div>';});
  $('#go').onclick=async()=>{
    const t=$('#txt').value.trim(); if(!t){$('#h').textContent='pick or type a task';return;}
    await act($('#go'),()=>post('/api/task',{task:t}));
  };
}

function rInbox(m){
  m.innerHTML=card(head('inbox')+`<button class="big" id="go">📧 close -1₦</button>
    <div class="hint">opens your day. process the rest in /inbound later.</div>`);
  $('#go').onclick=async()=>{await act($('#go'),()=>post('/api/inbox'));};
}

// disable target while a POST is in flight (double-tap guard), then re-render
async function act(el, fn){
  if(busy) return; busy=true;
  document.querySelectorAll('button,.chip').forEach(b=>b.setAttribute('disabled',''));
  try{
    const j=await fn();
    if(j && j.error){ const h=$('#h'); if(h) h.textContent=({empty:'required',start_failed:'timer didn\'t start — retry',no_timer:'no timer running'}[j.error]||j.error);
      document.querySelectorAll('button,.chip').forEach(b=>b.removeAttribute('disabled')); busy=false; return; }
    if(j){ state=j; render(); }
  }catch(e){ const h=$('#h'); if(h) h.textContent='connection error — retry';
    document.querySelectorAll('button,.chip').forEach(b=>b.removeAttribute('disabled')); }
  busy=false;
}

(async function init(){
  try{ state=await getState(); render(); }
  catch(e){ $('#main').innerHTML='<div class="loading">server unreachable — pull to refresh</div>'; }
})();
</script>
</body>
</html>"""


if __name__ == "__main__":
    # startup dependency check (logged; KeepAlive will surface crash loops)
    for label, ok in [("-2n.py", _TWO_N_PATH.exists()),
                       ("build-order", M.BUILD_ORDER.exists()),
                       ("toggl_cli", _TOGGL_CLI.exists())]:
        print(f"[wakeup] dep {label}: {'ok' if ok else 'MISSING'}", flush=True)
    app.run(host="0.0.0.0", port=5559, threaded=True)
