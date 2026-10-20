#!/usr/bin/env python3
"""
m5x2 org chart generator.

Reads the authoritative personnel roster (roster.tsv, exported from the
m5x2 Google Sheet) and renders a self-contained, shareable HTML org chart.

The roster supplies the structure (department, role, manager, location) plus
live employment fields (type, status, pay currency, start/end dates). The chart
layout is the nested card/compact-panel design from the original H2-2025 chart.

Run:  python3 build.py   ->  writes index.html
"""
import csv
import datetime as dt
import json
from pathlib import Path

HERE = Path(__file__).parent
ROSTER = HERE / "roster.tsv"
OUT = HERE / "index.html"

# location_id -> short code (airport codes; REM = remote)
LOC = {1: "SFO", 2: "MSO", 3: "REM", 4: "GEG", 5: "TRI", 6: "TAC"}

# Roster column indices
C_ID, C_FN, C_LN, C_EMAIL, C_TYPE = 0, 1, 2, 3, 4
C_CUR, C_ROLE, C_DEPT, C_STATUS = 6, 7, 8, 9
C_LOCID, C_MGR, C_START, C_END = 12, 13, 14, 15


def parse_roster():
    people = []
    with open(ROSTER) as f:
        for row in csv.reader(f, delimiter="\t"):
            if not row or not row[C_ID].strip():
                continue
            try:
                pid = int(row[C_ID])
            except ValueError:
                continue
            locid = row[C_LOCID].strip()
            mgr = row[C_MGR].strip()
            typ = row[C_TYPE].strip()
            people.append({
                "id": pid,
                "f": row[C_FN].strip(),
                "l": row[C_LN].strip(),
                "d": int(row[C_DEPT]) if row[C_DEPT].strip() else None,
                "r": int(row[C_ROLE]) if row[C_ROLE].strip() else None,
                "loc": LOC.get(int(locid), "") if locid else "",
                "m": int(mgr) if mgr else None,
                "own": 1 if typ == "General Partner" else 0,
                "type": typ,
                "status": row[C_STATUS].strip(),
                "cur": row[C_CUR].strip(),
                "email": row[C_EMAIL].strip(),
                "start": row[C_START].strip(),
                "end": row[C_END].strip(),
            })
    return people


def stats(people):
    from collections import Counter
    st = Counter(p["status"] for p in people)
    ty = Counter(p["type"] for p in people)
    cur = Counter(p["cur"] for p in people if p["cur"])
    return st, ty, cur


# ── HTML/CSS/JS template ──────────────────────────────────────────────────────
HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>m5x2 Org Chart</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', Roboto, sans-serif;
  background: #f1f5f9; padding: 32px; color: #0f172a;
}
h1 { font-size: 20px; font-weight: 700; color: #0f172a; display: inline-block; }
.sub { font-size: 13px; color: #64748b; margin-top: 3px; margin-bottom: 16px; }
#fitbtn {
  margin-left: 14px; font-size: 12px; font-weight: 600; cursor: pointer;
  background: #fff; border: 1px solid #cbd5e1; color: #334155;
  border-radius: 6px; padding: 4px 10px; vertical-align: 3px;
}
#fitbtn:hover { background: #f8fafc; }

.statbar { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 16px; }
.stat {
  background: #fff; border-radius: 8px; padding: 8px 12px;
  box-shadow: 0 1px 3px rgba(0,0,0,.07); min-width: 78px;
}
.stat .n { font-size: 18px; font-weight: 700; line-height: 1; }
.stat .k { font-size: 10px; color: #64748b; text-transform: uppercase; letter-spacing: .5px; margin-top: 4px; }

.legend {
  display: flex; flex-wrap: wrap; gap: 8px 18px;
  background: #fff; padding: 12px 16px; border-radius: 8px;
  box-shadow: 0 1px 3px rgba(0,0,0,.07); margin-bottom: 24px;
}
.legend-item { display: flex; align-items: center; gap: 6px; font-size: 12px; color: #374151; }
.ldot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.legend-sep { width: 1px; align-self: stretch; background: #e2e8f0; margin: 0 2px; }

#chart-outer { overflow: auto; background: #fff; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,.08); padding: 48px 40px; }
#chart-inner { position: relative; }
#svg-layer { position: absolute; top: 0; left: 0; pointer-events: none; }

.nc {
  position: absolute; background: #fff; border-radius: 8px;
  border: 1px solid #e2e8f0; border-top: 3px solid #94a3b8;
  box-shadow: 0 1px 4px rgba(0,0,0,.08); padding: 10px 12px 9px 12px; overflow: hidden;
}
.nc-ava {
  float: right; width: 26px; height: 26px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 10px; font-weight: 700; color: #fff; margin-left: 6px; margin-top: 1px; flex-shrink: 0;
}
.nc-name { font-size: 12.5px; font-weight: 600; color: #0f172a; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.nc-role { font-size: 10.5px; color: #64748b; margin-top: 2px; line-height: 1.35; }
.nc-badges { display: flex; flex-wrap: wrap; gap: 3px; margin-top: 6px; }
.b { font-size: 9.5px; font-weight: 600; padding: 1px 5px; border-radius: 8px; }
.bl { background: #eff6ff; color: #1d4ed8; }
.br { background: #fefce8; color: #92400e; }
.bg { background: #f0fdf4; color: #15803d; }
.bo { background: #faf5ff; color: #7c3aed; }
.bn { background: #f1f5f9; color: #94a3b8; font-style: italic; font-weight: 400; }
.bmx { background: #fff7ed; color: #9a3412; }
.bee { background: #f1f5f9; color: #475569; }
/* status badges */
.s-ramp   { background: #ecfeff; color: #0e7490; }
.s-notice { background: #fef2f2; color: #b91c1c; }
.s-flag   { background: #fff7ed; color: #c2410c; }
.s-pip    { background: #fee2e2; color: #991b1b; font-weight: 700; }
/* status accents on card right edge */
.nc.acc-notice { border-right: 3px solid #ef4444; }
.nc.acc-pip    { border-right: 3px solid #b91c1c; }
.nc.acc-flag   { border-right: 3px solid #f97316; }
.nc.acc-ramp   { border-right: 3px solid #06b6d4; }

.cp { position: absolute; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 12px 12px 10px; }
.cp-head { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .6px; color: #94a3b8; margin-bottom: 10px; }
.cp-section { margin-bottom: 10px; }
.cp-section:last-child { margin-bottom: 0; }
.cp-section-label { font-size: 9.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .5px; margin-bottom: 5px; }
.cp-grid { display: flex; flex-wrap: wrap; gap: 5px; }
.cc { background: #fff; border-radius: 6px; border-top: 2px solid #94a3b8; padding: 6px 9px; box-shadow: 0 1px 2px rgba(0,0,0,.06); }
.cc-name { font-size: 11px; font-weight: 600; color: #0f172a; white-space: nowrap; }
.cc-role { font-size: 9.5px; color: #64748b; margin-top: 1px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cc-badges { display: flex; flex-wrap: wrap; gap: 3px; margin-top: 4px; }
.cc.acc-notice { border-right: 2px solid #ef4444; }
.cc.acc-pip    { border-right: 2px solid #b91c1c; }
.cc.acc-flag   { border-right: 2px solid #f97316; }
.cc.acc-ramp   { border-right: 2px solid #06b6d4; }
</style>
</head>
<body>

<h1>m5x2 Org Chart</h1><button id="fitbtn">Actual size</button>
<p class="sub">__SUB__</p>
<div class="statbar" id="statbar"></div>
<div class="legend" id="legend"></div>
<div id="chart-outer"><div id="chart-inner"><svg id="svg-layer"></svg></div></div>

<script>
const GENERATED = "__GEN__";
const STATS = __STATS__;
"""

BODY_JS = r"""
const DEPTS = {
  1:{name:'Leadership',      color:'#2563eb', entity:'m5x2'},
  2:{name:'Finance',         color:'#059669', entity:'m5x2'},
  3:{name:'Tenant Relations',color:'#0891b2', entity:'r202'},
  4:{name:'Leasing',         color:'#d97706', entity:'r202'},
  5:{name:'Field Service',   color:'#dc2626', entity:'r203'},
  6:{name:'Turns',           color:'#7c3aed', entity:'r203'},
  7:{name:'Projects',        color:'#64748b', entity:'r203'},
  8:{name:'Special Projects',color:'#0d9488', entity:'m5x2'},
};
const ROLES = {
  1:'Ops Partner',           2:'Investing Partner',     3:'Head of Operations',
  4:'Head of Finance',       5:'Leasing Agent',         6:'Constr. Mgmt Tech',
  7:'Construction Manager',  8:'Maintenance Tech',      9:'Maint. & Constr. Mgr',
  10:'Groundskeeper',        11:'Janitorial',           12:'Grounds Keeper',
  13:'Lawn Care Tech',       14:'Field Service Mgr',    15:'Turn Tech',
  16:'Turn Manager',         17:'Tenant Retention Agt', 18:'Biz Automation Eng.',
  19:'Special Projects Assoc.', 20:'Special Projects Mgr', 21:'Tenant Comm. Agent',
  22:'Tenant Relations Agent',  23:'Tenant Relations Mgr', 24:'Leasing Manager',
  25:'Reg. Field Svc Mgr',   26:'Reg. Turn Manager',    27:'LIHTC Specialist',
  28:'Finance Assistant',    29:'Property Mgmt Generalist',
};
const RLVL = {3:'D1',4:'Frac',5:'IC1',6:'IC1',7:'IC2',8:'IC1',9:'M2',10:'IC0',11:'IC0',12:'IC0',
  13:'IC1',14:'M1',15:'IC1',16:'M1',17:'IC1',18:'IC1',19:'IC0',20:'M1',21:'IC0',22:'IC1',
  23:'M1',24:'M0',25:'M0',26:'M0',27:'IC2',28:'Frac',29:'IC2'};

const P = __PEOPLE__;

// ── TREE ──
const byId = {};
P.forEach(p => { byId[p.id] = p; p.kids = []; });
const roots = [];
P.forEach(p => { if (p.m && byId[p.m]) byId[p.m].kids.push(p); else if (!p.m) roots.push(p); });

// ── LAYOUT CONSTANTS ──
const CW = 150, CH = 78, HGAP = 10, VGAP = 52;
const COMPACT_N = 1, CCW = 132, CCH = 58, CCGAP = 5, PP = 11;
function dc(d) { return (DEPTS[d] || {color:'#94a3b8'}).color; }
function initials(p) { return p.f[0].toUpperCase() + p.l[0].toUpperCase(); }

function allLeaf(p) { return p.kids.every(k => k.kids.length === 0); }
function leafKids(p) { return p.kids.filter(k => k.kids.length === 0); }
function nonLeafKids(p) { return p.kids.filter(k => k.kids.length > 0); }
function useCompact(p) { return p.forceCompact || (p.kids.length > COMPACT_N && allLeaf(p)); }
function useMixed(p) {
  if (useCompact(p)) return false;
  const lk = leafKids(p), nlk = nonLeafKids(p);
  return nlk.length > 0 && lk.length >= COMPACT_N;
}
// ── HORIZONTAL (left-to-right) GEOMETRY ──
// Depth drives x (few levels → narrow); headcount drives y (stacks → tall).
const HLEVEL = 48, VSIB = 12, CCOLS = 2;
function panelCols(n) { return Math.max(1, Math.min(CCOLS, n)); }
function panelW(kids) {
  const depts = [...new Set(kids.map(k => k.d))];
  let mc = 0;
  depts.forEach(d => { const n = kids.filter(k => k.d === d).length; mc = Math.max(mc, panelCols(n)); });
  return PP*2 + mc*(CCW+CCGAP);
}
function panelH(kids) {
  const depts = [...new Set(kids.map(k => k.d))];
  let h = PP*2 + 18; // padding + header
  depts.forEach(d => {
    const n = kids.filter(k => k.d === d).length;
    const cols = panelCols(n), rows = Math.ceil(n / cols);
    h += 16 + rows*(CCH+CCGAP) - CCGAP + 8; // label + rows + section gap
  });
  return h;
}
function subtreeH(p) {
  if (!p.kids.length) return CH + VSIB;
  if (useCompact(p)) return Math.max(CH + VSIB, panelH(p.kids) + VSIB);
  if (useMixed(p)) {
    const nlkH = nonLeafKids(p).reduce((s,k) => s + subtreeH(k), 0);
    return Math.max(CH + VSIB, nlkH + panelH(leafKids(p)) + VSIB);
  }
  return Math.max(CH + VSIB, p.kids.reduce((s,k) => s + subtreeH(k), 0));
}
function layout(p, x, startY) {
  const sh = subtreeH(p);
  p.x = x; p.y = startY + sh/2 - CH/2;
  if (!p.kids.length) return;
  const childX = x + CW + HLEVEL;
  if (useCompact(p)) {
    const ph = panelH(p.kids);
    p.panelX = childX; p.panelY = startY + sh/2 - ph/2; p.panelW = panelW(p.kids); return;
  }
  if (useMixed(p)) {
    const nlk = nonLeafKids(p), lk = leafKids(p);
    const nlkH = nlk.reduce((s,k) => s + subtreeH(k), 0);
    const ph = panelH(lk);
    let cy = startY + (sh - (nlkH + ph)) / 2;
    nlk.forEach(k => { layout(k, childX, cy); cy += subtreeH(k); });
    p.panelX = childX; p.panelY = cy; p.panelW = panelW(lk); p.mixedLeafKids = lk; return;
  }
  const totalKids = p.kids.reduce((s,k) => s + subtreeH(k), 0);
  let cy = startY + (sh - totalKids)/2;
  p.kids.forEach(k => { layout(k, childX, cy); cy += subtreeH(k); });
}
let oy = 0;
roots.forEach(r => { layout(r, 0, oy); oy += subtreeH(r); });

let maxRight = 0, maxBottom = 0;
function measure(p) {
  maxRight = Math.max(maxRight, p.x + CW); maxBottom = Math.max(maxBottom, p.y + CH);
  if (useCompact(p)) {
    maxRight = Math.max(maxRight, p.panelX + p.panelW);
    maxBottom = Math.max(maxBottom, p.panelY + panelH(p.kids));
  } else if (useMixed(p)) {
    maxRight = Math.max(maxRight, p.panelX + p.panelW);
    maxBottom = Math.max(maxBottom, p.panelY + panelH(p.mixedLeafKids));
    nonLeafKids(p).forEach(measure);
  } else { p.kids.forEach(measure); }
}
roots.forEach(measure);
const W = maxRight + 48, H = maxBottom + 48;

const inner = document.getElementById('chart-inner');
inner.style.width = W + 'px'; inner.style.height = H + 'px';
const svg = document.getElementById('svg-layer');
svg.setAttribute('width', W); svg.setAttribute('height', H);

function svgEl(tag, attrs) {
  const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
  Object.entries(attrs).forEach(([k,v]) => el.setAttribute(k,v));
  svg.appendChild(el); return el;
}
function line(x1,y1,x2,y2) { svgEl('line',{x1,y1,x2,y2,stroke:'#cbd5e1','stroke-width':1.5}); }
function curveH(x1,y1,x2,y2) {
  const mx = (x1+x2)/2;
  svgEl('path',{d:`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`, stroke:'#cbd5e1','stroke-width':1.5,fill:'none'});
}

const STATUS_BADGE = {
  'Ramping': ['s-ramp','Ramping','acc-ramp'],
  'Notice':  ['s-notice','Notice','acc-notice'],
  'Flag':    ['s-flag','Flag','acc-flag'],
  'PIP':     ['s-pip','PIP','acc-pip'],
};
function accClass(p) { const s = STATUS_BADGE[p.status]; return s ? ' ' + s[2] : ''; }

function badges(p, small) {
  const s = small ? 'font-size:9px;padding:0 4px' : '';
  let h = '';
  const lvl = p.r ? RLVL[p.r] || '' : '';
  if (lvl)            h += `<span class="b bl" style="${s}">${lvl}</span>`;
  if (p.own)         h += `<span class="b bo" style="${s}">Owner</span>`;
  const sb = STATUS_BADGE[p.status];
  if (sb)            h += `<span class="b ${sb[0]}" style="${s}">${sb[1]}</span>`;
  if (p.loc==='REM') h += `<span class="b br" style="${s}">REM</span>`;
  else if (p.loc)    h += `<span class="b bg" style="${s}">${p.loc}</span>`;
  if (p.cur==='MXN') h += `<span class="b bmx" style="${s}">MXN</span>`;
  if (p.type==='employee') h += `<span class="b bee" style="${s}">EE</span>`;
  if (!p.r)          h += `<span class="b bn" style="${s}">No role</span>`;
  return h;
}
function tenure(p) {
  if (!p.start) return '';
  const d0 = new Date(p.start), now = new Date();
  const mo = (now.getFullYear()-d0.getFullYear())*12 + (now.getMonth()-d0.getMonth());
  if (mo < 12) return mo + 'mo';
  return (mo/12).toFixed(1) + 'y';
}
function cardTitle(p) {
  const parts = [`${p.f} ${p.l}`, ROLES[p.r]||'(no role)', p.type, p.email];
  if (p.start) parts.push('Started ' + p.start + ' (' + tenure(p) + ')');
  if (p.end) parts.push('End ' + p.end);
  if (p.status && p.status!=='Current' && p.status!=='n/a') parts.push('Status: ' + p.status);
  return parts.filter(Boolean).join('\n');
}

function renderCard(p, x, y, w) {
  const div = document.createElement('div');
  div.className = 'nc' + accClass(p);
  div.style.cssText = `left:${x}px;top:${y}px;width:${w}px;border-top-color:${dc(p.d)}`;
  div.title = cardTitle(p);
  const role = p.r ? (ROLES[p.r]||'') : '';
  div.innerHTML =
    `<div class="nc-ava" style="background:${dc(p.d)}">${initials(p)}</div>` +
    `<div class="nc-name">${p.f} ${p.l}</div>` +
    `<div class="nc-role">${role}</div>` +
    `<div class="nc-badges">${badges(p,false)}</div>`;
  inner.appendChild(div);
}
function renderPanel(mgr, leafOverride) {
  const kids = leafOverride || mgr.kids;
  const panel = document.createElement('div');
  panel.className = 'cp';
  panel.style.cssText = `left:${mgr.panelX}px;top:${mgr.panelY}px;width:${mgr.panelW}px`;
  const depts = [...new Set(kids.map(k => k.d))];
  const label = leafOverride ? 'Direct Reports' : 'Team';
  let html = `<div class="cp-head">${mgr.f}'s ${label} — ${kids.length} members</div>`;
  depts.forEach(d => {
    const members = kids.filter(k => k.d === d);
    const color = dc(d), dname = (DEPTS[d]||{name:''}).name;
    html += `<div class="cp-section"><div class="cp-section-label" style="color:${color}">${dname}</div><div class="cp-grid">`;
    members.forEach(k => {
      const role = k.r ? (ROLES[k.r]||'') : '';
      html += `<div class="cc${accClass(k)}" style="border-top-color:${dc(k.d)};width:${CCW}px" title="${cardTitle(k).replace(/"/g,'&quot;')}">` +
        `<div class="cc-name">${k.f} ${k.l}</div>` +
        `<div class="cc-role">${role}</div>` +
        `<div class="cc-badges">${badges(k,true)}</div></div>`;
    });
    html += `</div></div>`;
  });
  panel.innerHTML = html;
  inner.appendChild(panel);
  const px = mgr.x + CW, py = mgr.y + CH/2;
  const ty = mgr.panelY + panelH(kids)/2;
  curveH(px, py, mgr.panelX, ty);
}
function drawConnectors(p) {
  if (!p.kids.length) return;
  if (useCompact(p)) { renderPanel(p); return; }
  const px = p.x + CW, py = p.y + CH/2;
  if (useMixed(p)) {
    renderPanel(p, p.mixedLeafKids);
    nonLeafKids(p).forEach(k => curveH(px, py, k.x, k.y + CH/2));
    nonLeafKids(p).forEach(drawConnectors); return;
  }
  p.kids.forEach(k => curveH(px, py, k.x, k.y + CH/2));
  p.kids.forEach(drawConnectors);
}
function renderAll(p) {
  renderCard(p, p.x, p.y, CW);
  if (useCompact(p)) return;
  if (useMixed(p)) { nonLeafKids(p).forEach(renderAll); return; }
  p.kids.forEach(renderAll);
}
roots.forEach(drawConnectors);
roots.forEach(renderAll);

// ── STAT BAR ──
const sb = document.getElementById('statbar');
const order = [['Total', P.length, '#0f172a'], ['Current', STATS.status.Current||0, '#15803d'],
  ['Ramping', STATS.status.Ramping||0, '#0e7490'], ['Notice', STATS.status.Notice||0, '#b91c1c'],
  ['Flag', STATS.status.Flag||0, '#c2410c'], ['PIP', STATS.status.PIP||0, '#991b1b']];
order.forEach(([k,n,c]) => {
  if (n === 0 && !['Total','Current'].includes(k)) return;
  const el = document.createElement('div');
  el.className = 'stat';
  el.innerHTML = `<div class="n" style="color:${c}">${n}</div><div class="k">${k}</div>`;
  sb.appendChild(el);
});

// ── LEGEND ──
const leg = document.getElementById('legend');
Object.entries(DEPTS).filter(([k])=>k!='7').forEach(([k,d])=>{
  const item = document.createElement('div');
  item.className = 'legend-item';
  item.innerHTML = `<div class="ldot" style="background:${d.color}"></div>${d.name} <em style="color:#9ca3af;font-size:11px">${d.entity}</em>`;
  leg.appendChild(item);
});
const sep = document.createElement('div'); sep.className = 'legend-sep'; leg.appendChild(sep);
[['s-ramp','Ramping'],['s-flag','Flag'],['s-notice','Notice'],['s-pip','PIP'],['bmx','MXN payroll'],['bee','W-2 employee']].forEach(([cls,lab])=>{
  const item = document.createElement('div');
  item.className = 'legend-item';
  item.innerHTML = `<span class="b ${cls}">${lab.split(' ')[0]}</span> ${lab}`;
  leg.appendChild(item);
});

// ── FIT TO WIDTH (portrait; scrolls vertically) ──
const outer = document.getElementById('chart-outer');
const fitbtn = document.getElementById('fitbtn');
let fitted = true;
function applyFit() {
  const cw = outer.clientWidth - 96;   // outer has 48px L/R padding
  if (fitted) {
    const s = Math.min(1, cw / W);
    inner.style.transformOrigin = '0 0';
    inner.style.transform = `scale(${s})`;
    inner.style.marginRight = (-(W - W * s)) + 'px';
    inner.style.marginBottom = (-(H - H * s)) + 'px';
    fitbtn.textContent = 'Actual size';
  } else {
    inner.style.transform = 'none';
    inner.style.marginRight = '';
    inner.style.marginBottom = '';
    fitbtn.textContent = 'Fit width';
  }
}
fitbtn.addEventListener('click', () => { fitted = !fitted; applyFit(); });
window.addEventListener('resize', () => { if (fitted) applyFit(); });
applyFit();

</script>
</body>
</html>
"""


def main():
    people = parse_roster()
    st, ty, cur = stats(people)
    today = dt.date.today().isoformat()
    n = len(people)
    emp = ty.get("employee", 0)
    con = ty.get("contractor", 0)
    gp = ty.get("General Partner", 0)
    sub = (f"Live roster · {today} · {n} people "
           f"({gp} GP, {emp} W-2, {con} contractor) · "
           f"{cur.get('MXN',0)} on MXN payroll")
    stats_json = json.dumps({
        "status": dict(st), "type": dict(ty), "currency": dict(cur),
    })
    people_json = json.dumps(people, ensure_ascii=False)
    html = (HEAD.replace("__SUB__", sub)
                .replace("__GEN__", today)
                .replace("__STATS__", stats_json)
            + BODY_JS.replace("__PEOPLE__", people_json))
    OUT.write_text(html)
    print(f"Wrote {OUT} — {n} people")
    print(f"  status: {dict(st)}")
    print(f"  type:   {dict(ty)}")
    print(f"  currency: {dict(cur)}")


if __name__ == "__main__":
    main()
