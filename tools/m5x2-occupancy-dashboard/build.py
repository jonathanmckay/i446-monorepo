#!/usr/bin/env python3
"""Build the m5x2 occupancy dashboard (static HTML) from the consolidated data.

Inputs (data/):
  occupancy-history.json  — real per-date portfolio snapshots (occupancy_summary)
  vacancy.json            — current non-occupied units w/ move dates (unit_vacancy_detail)

Output:
  index.html — self-contained dashboard: units view (-90d actual → +60d projected),
  long-term % view, and an occupancy newsfeed / daily tenant tickler.

Refresh the data with fetch.py (pulls AppFolio), then re-run this.

State model (AppFolio): a unit is occupied (incl. on-notice) or vacant; cross-cut
by rented (future tenant lined up) vs unrented. The four "action" states:
  Vacant-Unrented (red)  Vacant-Rented (green)
  Notice-Unrented (red)  Notice-Rented (green)
plus stable Occupied. Unrented = exposure (red), Rented = covered (green).
"""
import json, datetime as dt
from pathlib import Path

DIR = Path(__file__).parent
TODAY = dt.date(2026, 6, 14)   # snapshot date; fetch.py stamps this
BACK_DAYS, FWD_DAYS = 90, 60

def load(name): return json.loads((DIR / "data" / name).read_text())
def pdate(s):
    try: return dt.date.fromisoformat(s[:10]) if s else None
    except Exception: return None

hist = load("occupancy-history.json")
vac = load("vacancy.json")
cur = hist[-1]                    # today's snapshot
UNITS = cur["units"]

# ── Forward projection: replay each unit's known move events day by day ──────
# Start from today's buckets; notice→vacant on move-out, vacant/notice-rented→
# occupied on move-in. Unknowns (will an unrented vacant get leased?) are NOT
# guessed — projection only applies *known* scheduled moves.
buckets = {"occ_stable": cur["occ_stable"], "nr": cur["nr"], "nu": cur["nu"],
           "vr": cur["vr"], "vu": cur["vu"]}
events = []   # (date, kind, rented)
for u in vac:
    st = (u.get("unit_status") or "")
    mo = pdate(u.get("last_move_out")); mi = pdate(u.get("next_move_in"))
    rented = st.endswith("Rented")
    if "Notice" in st and mo and mo > TODAY:
        events.append((mo, "OUT", rented))         # current tenant vacates
    if mi and mi > TODAY:
        events.append((mi, "IN", rented))           # new tenant arrives
events.sort(key=lambda e: e[0])

series = []   # list of {date, occ_stable, nr, nu, vr, vu, projected}
def snap(date, projected):
    s = {"date": date.isoformat(), "projected": projected}
    s.update(buckets); return s

# backward actuals (real snapshots) up to today
actual_by_date = {h["date"]: h for h in hist}
start = TODAY - dt.timedelta(days=BACK_DAYS)
# Plot the real snapshots as the backward series (sparse but true).
back = [h for h in hist if pdate(h["date"]) >= start]

# forward: walk today → +FWD_DAYS, applying events
ev_idx = 0
day = TODAY
fwd = [snap(day, False)]   # today = actual anchor
end = TODAY + dt.timedelta(days=FWD_DAYS)
day += dt.timedelta(days=1)
while day <= end:
    while ev_idx < len(events) and events[ev_idx][0] == day:
        _, kind, rented = events[ev_idx]; ev_idx += 1
        if kind == "OUT":
            if rented: buckets["nr"] = max(0, buckets["nr"]-1); buckets["vr"] += 1
            else:      buckets["nu"] = max(0, buckets["nu"]-1); buckets["vu"] += 1
        else:  # IN
            if buckets["vr"] > 0: buckets["vr"] -= 1
            elif buckets["nr"] > 0: buckets["nr"] -= 1
            buckets["occ_stable"] += 1
    fwd.append(snap(day, True))
    day += dt.timedelta(days=1)

# ── Newsfeed / daily tenant tickler ─────────────────────────────────────────
feed = []
for u in vac:
    p = u.get("property_name"); unit = u.get("unit"); st = u.get("unit_status")
    mo = pdate(u.get("last_move_out")); mi = pdate(u.get("next_move_in"))
    rent = u.get("schd_rent") or u.get("advertised_rent")
    where = f"{p} #{unit}"
    if mo and mo > TODAY:
        feed.append((mo, "notice", f"📤 Notice / scheduled move-out — {where}",
                     f"vacates {mo.isoformat()}" + (" · backfill lined up" if st.endswith("Rented") else " · no backfill yet")))
    if mo and TODAY - dt.timedelta(days=30) <= mo <= TODAY:
        feed.append((mo, "moveout", f"🚪 Moved out — {where}", f"left {mo.isoformat()}, now {st}"))
    if st == "Vacant-Rented":
        # leased + awaiting move-in (conveys the scheduled move-in; no separate movein event)
        feed.append((mi or TODAY, "leased", f"✅ Leased, awaiting move-in — {where}",
                     f"move-in {mi.isoformat() if mi else 'TBD'}"))
    elif mi and mi > TODAY:
        feed.append((mi, "movein", f"📥 Move-in scheduled — {where}", f"new tenant {mi.isoformat()}"))
# sort: upcoming first (future asc), then recent past (desc) — a forward-looking tickler
future = sorted([f for f in feed if f[0] > TODAY], key=lambda x: x[0])
past = sorted([f for f in feed if f[0] <= TODAY], key=lambda x: x[0], reverse=True)
feed_sorted = future + past

payload = {
    "today": TODAY.isoformat(), "units": UNITS,
    "current": {"occ_stable": cur["occ_stable"], "nr": cur["nr"], "nu": cur["nu"],
                "vr": cur["vr"], "vu": cur["vu"], "occ_pct": round(cur["occ"]/UNITS*100, 1)},
    "back": back, "forward": fwd,
    "feed": [{"date": d.isoformat(), "kind": k, "title": t, "sub": s} for d, k, t, s in feed_sorted],
}

# ── Render ──────────────────────────────────────────────────────────────────
HTML = """<!doctype html><html><head><meta charset="utf-8">
<title>m5x2 Occupancy</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
:root{--bg:#0d0f12;--card:#161a1f;--text:#e7ecf0;--muted:#8b96a3;--border:#242a31;
 --vu:#e23b3b;--vr:#2faa4d;--nu:#ff8a3d;--nr:#7ed957;--occ:#2a3340;--blue:#2979ff;}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
 font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;padding:24px;max-width:1180px;margin:auto}
h1{font-size:22px;margin:0 0 2px}h2{font-size:15px;color:var(--muted);font-weight:600;
 margin:28px 0 10px;text-transform:uppercase;letter-spacing:.04em}
.sub{color:var(--muted);margin-bottom:18px}
.kpis{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:8px}
.kpi{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:12px 16px;min-width:120px}
.kpi .v{font-size:24px;font-weight:700}.kpi .l{color:var(--muted);font-size:12px}
.kpi.vu .v{color:var(--vu)}.kpi.vr .v{color:var(--vr)}.kpi.nu .v{color:var(--nu)}.kpi.nr .v{color:var(--nr)}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px;margin-bottom:14px}
.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:12px;color:var(--muted);margin-top:8px}
.legend span{display:inline-flex;align-items:center;gap:6px}.sw{width:12px;height:12px;border-radius:3px;display:inline-block}
.feed{list-style:none;padding:0;margin:0}
.feed li{display:flex;gap:12px;padding:9px 0;border-bottom:1px solid var(--border)}
.feed .dt{color:var(--muted);font-variant-numeric:tabular-nums;min-width:90px}
.feed .ti{flex:1}.feed .su{color:var(--muted);font-size:12px}
.tag{font-size:10px;padding:1px 7px;border-radius:20px;text-transform:uppercase;letter-spacing:.03em}
.t-notice{background:#3a2a16;color:var(--nu)}.t-moveout{background:#3a1c1c;color:var(--vu)}
.t-movein{background:#15321d;color:var(--vr)}.t-leased{background:#15321d;color:var(--nr)}
.note{color:var(--muted);font-size:12px;margin-top:6px}
.divider{font-size:11px;color:var(--muted);margin:14px 0 6px;text-transform:uppercase}
</style></head><body>
<h1>m5x2 Occupancy</h1>
<div class="sub">As of __TODAY__ · __UNITS__ units · first pass (data: AppFolio occupancy_summary + unit_vacancy_detail)</div>
<div class="kpis" id="kpis"></div>

<h2>Occupancy — 90 days back → 60 days projected (units)</h2>
<div class="card"><canvas id="unitsChart" height="110"></canvas>
<div class="legend">
 <span><i class="sw" style="background:var(--occ)"></i>Occupied (stable)</span>
 <span><i class="sw" style="background:var(--nr)"></i>Notice-Rented</span>
 <span><i class="sw" style="background:var(--nu)"></i>Notice-Unrented</span>
 <span><i class="sw" style="background:var(--vr)"></i>Vacant-Rented</span>
 <span><i class="sw" style="background:var(--vu)"></i>Vacant-Unrented</span>
 <span style="margin-left:auto">dashed = projected from known scheduled moves</span></div>
<div class="note">Backward points are real AppFolio snapshots (sparse, ~biweekly). Forward is today's
counts replayed against scheduled move-outs (notice→vacant) and move-ins (rented→occupied); it does
not assume new leasing of currently-unrented units. Daily snapshots (fetch.py) will densify history.</div></div>

<h2>Long-term — same states as % of portfolio</h2>
<div class="card"><canvas id="pctChart" height="90"></canvas></div>

<h2>Occupancy Newsfeed — daily tenant tickler</h2>
<div class="card"><ul class="feed" id="feed"></ul>
<div class="note">Derived from unit move dates. The email-driven lease_signings capture is currently
failing (12 rows, all blocked/failed) — wire that up to add live "lease signed" events.</div></div>

<script>
const D = __PAYLOAD__;
const C={occ:'#2a3340',nr:'#7ed957',nu:'#ff8a3d',vr:'#2faa4d',vu:'#e23b3b'};
// KPIs
const c=D.current, kp=[['occ_pct','Occupied %','',c.occ_pct+'%'],
 ['vu','Vacant-Unrented','vu',c.vu],['vr','Vacant-Rented','vr',c.vr],
 ['nu','Notice-Unrented','nu',c.nu],['nr','Notice-Rented','nr',c.nr]];
document.getElementById('kpis').innerHTML=kp.map(k=>
 `<div class="kpi ${k[2]}"><div class="v">${k[3]}</div><div class="l">${k[1]}</div></div>`).join('');
// merge back + forward into one timeline
const rows=[...D.back.map(r=>({...r,projected:false})), ...D.forward.slice(1)];
const labels=rows.map(r=>r.date);
const splitIdx=D.back.length-1;  // boundary
function ds(key,label,color){
 return {label,data:rows.map(r=>r[key]),backgroundColor:color,borderColor:color,
  fill:true,stepped:false,pointRadius:0,tension:.2,
  segment:{borderDash:ctx=>ctx.p0DataIndex>=splitIdx?[5,4]:undefined}};
}
const order=[['occ_stable','Occupied',C.occ],['nr','Notice-Rented',C.nr],
 ['nu','Notice-Unrented',C.nu],['vr','Vacant-Rented',C.vr],['vu','Vacant-Unrented',C.vu]];
new Chart(document.getElementById('unitsChart'),{type:'line',
 data:{labels,datasets:order.map(o=>ds(o[0],o[1],o[2]))},
 options:{responsive:true,interaction:{mode:'index',intersect:false},
  plugins:{legend:{display:false}},
  scales:{x:{stacked:true,ticks:{color:'#8b96a3',maxTicksLimit:14},grid:{display:false}},
   y:{stacked:true,ticks:{color:'#8b96a3'},grid:{color:'#1e242b'}}}}});
// % chart (action states only, as % of portfolio)
const U=D.units;
function pds(key,label,color){return {label,data:rows.map(r=>+(r[key]/U*100).toFixed(2)),
 backgroundColor:color,borderColor:color,fill:true,pointRadius:0,tension:.2,
 segment:{borderDash:ctx=>ctx.p0DataIndex>=splitIdx?[5,4]:undefined}};}
new Chart(document.getElementById('pctChart'),{type:'line',
 data:{labels,datasets:[pds('vu','Vacant-Unrented %',C.vu),pds('nu','Notice-Unrented %',C.nu),
  pds('vr','Vacant-Rented %',C.vr),pds('nr','Notice-Rented %',C.nr)]},
 options:{responsive:true,interaction:{mode:'index',intersect:false},
  plugins:{legend:{labels:{color:'#8b96a3',boxWidth:12}}},
  scales:{x:{stacked:true,ticks:{color:'#8b96a3',maxTicksLimit:14},grid:{display:false}},
   y:{stacked:true,ticks:{color:'#8b96a3',callback:v=>v+'%'},grid:{color:'#1e242b'}}}}});
// feed
document.getElementById('feed').innerHTML=D.feed.map(f=>
 `<li><span class="dt">${f.date}</span><span class="ti">${f.title}<br><span class="su">${f.sub}</span></span>`+
 `<span class="tag t-${f.kind}">${f.kind}</span></li>`).join('');
</script></body></html>"""

out = (HTML.replace("__TODAY__", payload["today"])
           .replace("__UNITS__", str(UNITS))
           .replace("__PAYLOAD__", json.dumps(payload)))
(DIR / "index.html").write_text(out)
print("wrote", DIR / "index.html")
print(f"  back snapshots: {len(back)} | forward days: {len(fwd)} | feed events: {len(payload['feed'])}")
