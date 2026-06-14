#!/usr/bin/env python3
"""Build the m5x2 occupancy dashboard (static HTML) from the consolidated data.

Inputs (data/):
  occupancy-history.json  — real per-date portfolio snapshots (occupancy_summary).
                            Daily for the trailing ~90d, weekly back to 2024.
  vacancy.json            — current non-occupied units w/ move dates (unit_vacancy).

Output:
  index.html — self-contained dashboard:
    1. Units view: the four action states, one column per day, -90d actual →
       +60d forecast. Stable-occupied is intentionally omitted.
    2. Long-term % view: same four states as % of portfolio, weekly back to 2024.
    3. Occupancy newsfeed / daily tenant tickler, strict reverse-chronological.

State model (AppFolio): a unit is occupied (incl. on-notice) or vacant; cross-cut
by rented (future tenant lined up) vs unrented. The four action states:
  Vacant-Unrented (red)  Vacant-Rented (green)
  Notice-Unrented (red)  Notice-Rented (green)
Unrented = exposure (red), Rented = covered (green).

Forward forecast is a linear compartment model (occ→notice→vacant→occ) whose
rates are calibrated from the live pipeline:
  λ  notice arrivals/day      = (nr+nu) / NOTICE_DAYS  (self-calibrates to pipeline)
  1/NOTICE_DAYS notice→vacant = median scheduled notice lead time
  1/LEASE_DAYS  unrented→rent = median days-vacant of currently-rented units
  1/MOVEIN_LAG  rented→occ    = median scheduled move-in lead time
Total units are conserved each step. It is a forecast of the bands, not a replay
of individual scheduled moves (those drive the tickler).
"""
import json, datetime as dt
from pathlib import Path

DIR = Path(__file__).parent
TODAY = dt.date(2026, 6, 14)   # snapshot date; fetch.py stamps this
BACK_DAYS, FWD_DAYS = 90, 60
LT_START = dt.date(2024, 1, 1)  # long-term % view start

def load(name): return json.loads((DIR / "data" / name).read_text())
def pdate(s):
    try: return dt.date.fromisoformat(s[:10]) if s else None
    except Exception: return None
def num(x):
    try: return float(str(x).replace(",", ""))
    except Exception: return None
def med(xs, default):
    xs = sorted(v for v in xs if v is not None)
    if not xs: return default
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2
def clamp(v, lo, hi): return max(lo, min(hi, v))

hist = sorted(load("occupancy-history.json"), key=lambda h: h["date"])
vac = load("vacancy.json")
cur = hist[-1]                     # today's snapshot
UNITS = cur["units"]
by_date = {h["date"]: h for h in hist}
hdates = sorted(by_date)

def nearest(diso):
    """Latest snapshot on or before diso; else the earliest available (forward-fill)."""
    le = [x for x in hdates if x <= diso]
    return by_date[le[-1]] if le else by_date[hdates[0]]

# ── Backward daily series (one column per day, -90d → today) ─────────────────
back = []
d = TODAY - dt.timedelta(days=BACK_DAYS)
while d <= TODAY:
    s = nearest(d.isoformat())
    back.append({"date": d.isoformat(), "nr": s["nr"], "nu": s["nu"],
                 "vr": s["vr"], "vu": s["vu"], "projected": False})
    d += dt.timedelta(days=1)

# ── Forward forecast: data-calibrated compartment model ──────────────────────
notice_leads = [(pdate(u["last_move_out"]) - TODAY).days for u in vac
                if "Notice" in (u.get("unit_status") or "") and pdate(u.get("last_move_out"))
                and pdate(u["last_move_out"]) > TODAY]
movein_leads = [(pdate(u["next_move_in"]) - TODAY).days for u in vac
                if pdate(u.get("next_move_in")) and pdate(u["next_move_in"]) > TODAY]
lease_obs = [num(u.get("days_vacant")) for u in vac
             if u.get("unit_status") == "Vacant-Rented"]

NOTICE_DAYS = clamp(med(notice_leads, 30), 10, 60)
MOVEIN_LAG = clamp(med(movein_leads, 18), 5, 45)
LEASE_DAYS = clamp(med(lease_obs, 35), 14, 90)
lam = (cur["nr"] + cur["nu"]) / NOTICE_DAYS          # notice arrivals/day
k_out, k_lease, k_in = 1 / NOTICE_DAYS, 1 / LEASE_DAYS, 1 / MOVEIN_LAG

b = {k: float(cur[k]) for k in ("occ_stable", "nr", "nu", "vr", "vu")}
def fsnap(date):
    return {"date": date.isoformat(), "projected": True,
            "nr": round(b["nr"], 1), "nu": round(b["nu"], 1),
            "vr": round(b["vr"], 1), "vu": round(b["vu"], 1)}
fwd = [{"date": TODAY.isoformat(), "projected": False, "nr": cur["nr"],
        "nu": cur["nu"], "vr": cur["vr"], "vu": cur["vu"]}]
for i in range(1, FWD_DAYS + 1):
    occ, nr, nu, vr, vu = b["occ_stable"], b["nr"], b["nu"], b["vr"], b["vu"]
    f_new = lam              # occ → notice-unrented (fresh notice)
    f_nu_nr = nu * k_lease   # noticed unit gets pre-leased
    f_vu_vr = vu * k_lease   # vacant unit gets leased
    f_nu_vu = nu * k_out     # notice period ends, still unrented
    f_nr_vr = nr * k_out     # notice period ends, backfill lined up
    f_vr_occ = vr * k_in     # new tenant moves in
    b["occ_stable"] = occ - f_new + f_vr_occ
    b["nu"] = nu + f_new - f_nu_nr - f_nu_vu
    b["nr"] = nr + f_nu_nr - f_nr_vr
    b["vu"] = vu + f_nu_vu - f_vu_vr
    b["vr"] = vr + f_nr_vr + f_vu_vr - f_vr_occ
    fwd.append(fsnap(TODAY + dt.timedelta(days=i)))

# ── Long-term weekly % series (2024 → today, % of each snapshot's portfolio) ──
longterm = []
d = LT_START
while d <= TODAY:
    s = nearest(d.isoformat())
    u = s["units"] or UNITS
    longterm.append({"date": d.isoformat(),
                     "vu": round(s["vu"] / u * 100, 2), "nu": round(s["nu"] / u * 100, 2),
                     "vr": round(s["vr"] / u * 100, 2), "nr": round(s["nr"] / u * 100, 2)})
    d += dt.timedelta(days=7)

# ── Newsfeed / daily tenant tickler (strict reverse chronological) ───────────
feed = []
RECENT = TODAY - dt.timedelta(days=30)
for u in vac:
    p = u.get("property_name"); unit = u.get("unit"); st = u.get("unit_status") or ""
    mo = pdate(u.get("last_move_out")); mi = pdate(u.get("next_move_in"))
    where = f"{p} #{unit}"
    if mo and mo > TODAY:
        feed.append((mo, "notice", f"📤 Notice / scheduled move-out — {where}",
                     f"vacates {mo.isoformat()}" +
                     (" · backfill lined up" if st.endswith("Rented") else " · no backfill yet")))
    if mo and RECENT <= mo <= TODAY:
        feed.append((mo, "moveout", f"🚪 Moved out — {where}", f"left {mo.isoformat()}, now {st}"))
    if st == "Vacant-Rented":
        feed.append((mi or TODAY, "leased", f"✅ Leased, awaiting move-in — {where}",
                     f"move-in {mi.isoformat() if mi else 'TBD'}"))
    elif mi and mi > TODAY:
        feed.append((mi, "movein", f"📥 Move-in scheduled — {where}", f"new tenant {mi.isoformat()}"))
feed_sorted = sorted(feed, key=lambda x: x[0], reverse=True)

payload = {
    "today": TODAY.isoformat(), "units": UNITS,
    "params": {"notice_days": round(NOTICE_DAYS, 1), "lease_days": round(LEASE_DAYS, 1),
               "movein_lag": round(MOVEIN_LAG, 1), "lambda": round(lam, 2)},
    "current": {"occ_stable": cur["occ_stable"], "nr": cur["nr"], "nu": cur["nu"],
                "vr": cur["vr"], "vu": cur["vu"], "occ_pct": round(cur["occ"] / UNITS * 100, 1)},
    "back": back, "forward": fwd, "longterm": longterm,
    "feed": [{"date": d.isoformat(), "kind": k, "title": t, "sub": s} for d, k, t, s in feed_sorted],
}

# ── Render ──────────────────────────────────────────────────────────────────
HTML = """<!doctype html><html><head><meta charset="utf-8">
<title>m5x2 Occupancy</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
:root{--bg:#0d0f12;--card:#161a1f;--text:#e7ecf0;--muted:#8b96a3;--border:#242a31;
 --vu:#e23b3b;--vr:#2faa4d;--nu:#ff8a3d;--nr:#ff5fa2;--occ:#2a3340;--blue:#2979ff;}
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
.tag{font-size:10px;padding:1px 7px;border-radius:20px;text-transform:uppercase;letter-spacing:.03em;height:fit-content}
.t-notice{background:#3a2a16;color:var(--nu)}.t-moveout{background:#3a1c1c;color:var(--vu)}
.t-movein{background:#15321d;color:var(--vr)}.t-leased{background:#3a1f2e;color:var(--nr)}
.note{color:var(--muted);font-size:12px;margin-top:6px}
</style></head><body>
<h1>m5x2 Occupancy</h1>
<div class="sub">As of __TODAY__ · __UNITS__ units · data: AppFolio occupancy_summary + unit_vacancy</div>
<div class="kpis" id="kpis"></div>

<h2>Action states — one column per day, 90d back → 60d forecast (units)</h2>
<div class="card"><canvas id="unitsChart" height="110"></canvas>
<div class="legend">
 <span><i class="sw" style="background:var(--vu)"></i>Vacant-Unrented</span>
 <span><i class="sw" style="background:var(--nu)"></i>Notice-Unrented</span>
 <span><i class="sw" style="background:var(--vr)"></i>Vacant-Rented</span>
 <span><i class="sw" style="background:var(--nr)"></i>Notice-Rented</span>
 <span style="margin-left:auto">dashed = forecast</span></div>
<div class="note" id="paramNote"></div>
<div class="note">Stable-occupied is omitted by design — this shows only the exposed/in-transition units.
Backward is real daily AppFolio snapshots; forward is a compartment forecast calibrated to the current
pipeline (notice→vacant→occupied flows). Red = unrented exposure, green = covered.</div></div>

<h2>Long-term — same four states as % of portfolio, weekly since 2024</h2>
<div class="card"><canvas id="pctChart" height="90"></canvas></div>

<h2>Occupancy newsfeed — daily tenant tickler</h2>
<div class="card"><ul class="feed" id="feed"></ul>
<div class="note">Strict reverse-chronological (newest first), derived from unit move dates. The
email-driven lease_signings capture is currently failing — wire that up to add live "lease signed" events.</div></div>

<script>
const D = __PAYLOAD__;
const C={nr:'#ff5fa2',nu:'#ff8a3d',vr:'#2faa4d',vu:'#e23b3b'};
// KPIs
const c=D.current, kp=[['occ_pct','Occupied %','',c.occ_pct+'%'],
 ['vu','Vacant-Unrented','vu',c.vu],['vr','Vacant-Rented','vr',c.vr],
 ['nu','Notice-Unrented','nu',c.nu],['nr','Notice-Rented','nr',c.nr]];
document.getElementById('kpis').innerHTML=kp.map(k=>
 `<div class="kpi ${k[2]}"><div class="v">${k[3]}</div><div class="l">${k[1]}</div></div>`).join('');
const P=D.params;
document.getElementById('paramNote').textContent=
 `Forecast rates (calibrated): ${P.lambda} new notices/day · notice→vacant ~${P.notice_days}d · `+
 `lease-up ~${P.lease_days}d · move-in lag ~${P.movein_lag}d.`;
// ── units chart: daily back + daily forecast, 4 stacked bands ──
const rows=[...D.back, ...D.forward.slice(1)];
const labels=rows.map(r=>r.date);
const splitIdx=D.back.length-1;
function ds(key,label,color){
 return {label,data:rows.map(r=>r[key]),backgroundColor:color,borderColor:color,
  fill:true,pointRadius:0,tension:.15,borderWidth:1,
  segment:{borderDash:ctx=>ctx.p0DataIndex>=splitIdx?[5,4]:undefined}};
}
const order=[['vu','Vacant-Unrented',C.vu],['nu','Notice-Unrented',C.nu],
 ['vr','Vacant-Rented',C.vr],['nr','Notice-Rented',C.nr]];
new Chart(document.getElementById('unitsChart'),{type:'line',
 data:{labels,datasets:order.map(o=>ds(o[0],o[1],o[2]))},
 options:{responsive:true,interaction:{mode:'index',intersect:false},
  plugins:{legend:{display:false},tooltip:{callbacks:{title:i=>i[0].label+(i[0].dataIndex>splitIdx?'  (forecast)':'')}}},
  scales:{x:{stacked:true,ticks:{color:'#8b96a3',maxTicksLimit:16},grid:{display:false}},
   y:{stacked:true,ticks:{color:'#8b96a3'},grid:{color:'#1e242b'}}}}});
// ── long-term % chart: weekly since 2024 ──
const lt=D.longterm, ltLabels=lt.map(r=>r.date);
function pds(key,label,color){return {label,data:lt.map(r=>r[key]),
 backgroundColor:color,borderColor:color,fill:true,pointRadius:0,tension:.15,borderWidth:1};}
new Chart(document.getElementById('pctChart'),{type:'line',
 data:{labels:ltLabels,datasets:[pds('vu','Vacant-Unrented %',C.vu),pds('nu','Notice-Unrented %',C.nu),
  pds('vr','Vacant-Rented %',C.vr),pds('nr','Notice-Rented %',C.nr)]},
 options:{responsive:true,interaction:{mode:'index',intersect:false},
  plugins:{legend:{labels:{color:'#8b96a3',boxWidth:12}}},
  scales:{x:{stacked:true,ticks:{color:'#8b96a3',maxTicksLimit:18},grid:{display:false}},
   y:{stacked:true,ticks:{color:'#8b96a3',callback:v=>v+'%'},grid:{color:'#1e242b'}}}}});
// ── feed ──
document.getElementById('feed').innerHTML=D.feed.map(f=>
 `<li><span class="dt">${f.date}</span><span class="ti">${f.title}<br><span class="su">${f.sub}</span></span>`+
 `<span class="tag t-${f.kind}">${f.kind}</span></li>`).join('');
</script></body></html>"""

out = (HTML.replace("__TODAY__", payload["today"])
           .replace("__UNITS__", str(UNITS))
           .replace("__PAYLOAD__", json.dumps(payload)))
(DIR / "index.html").write_text(out)
print("wrote", DIR / "index.html")
print(f"  back days: {len(back)} | forecast days: {len(fwd)} | "
      f"longterm weeks: {len(longterm)} | feed: {len(payload['feed'])}")
print(f"  params: λ={lam:.2f} notice_days={NOTICE_DAYS} lease_days={LEASE_DAYS} movein_lag={MOVEIN_LAG}")
