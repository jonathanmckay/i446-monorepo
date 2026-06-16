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
from collections import Counter
from pathlib import Path

DIR = Path(__file__).parent
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
TODAY = dt.date.fromisoformat(hist[-1]["date"])   # the latest real snapshot = "now"
vac = load("vacancy.json")

# ── Repair the historical breakdown ──────────────────────────────────────────
# occupancy_summary's `as_of_to` snapshots give reliable `units` and `occupied`
# (occupancy% tracks reality), but the rented/unrented cross-cut is NOT a true
# as-of-then split: AppFolio back-applies each unit's *current* rented flag to
# past dates. A unit that sat vacant-unrented on Apr 1 but has since been leased
# is reported as vacant-RENTED as of Apr 1, so historical unrented is badly
# undercounted (Apr 1 reports vu=23; the real as-of-then figure was ~61). The
# only reliable totals are units and occupied, so we reconstruct the four action
# states from those: vacant = units − occupied (reliable), split by the *current*
# unrented share measured from today's live unit-level vacancy report, which has
# no retroactive bias. Notice is anchored off occupied (it tracks occupancy, not
# vacancy) and only re-split when the raw notice numbers are implausible.
def is_corrupt(s):
    V = s["units"] - s["occ"]
    return (s["vu"] == 0 and V > 3) or (s["nr"] > 0.08 * s["units"])

def trusted_bands(hist, vu_share):
    clean = [s for s in hist if not is_corrupt(s)]
    notice_rate = med([(s["nu"] + s["nr"]) / (s["occ"] or 1) for s in clean], 0.06)
    nr_frac = med([s["nr"] / ((s["nu"] + s["nr"]) or 1) for s in clean
                   if s["nu"] + s["nr"] > 0], 0.1)
    out = {}
    for s in hist:
        U, O = s["units"], s["occ"]; V = max(0, U - O)
        # vacant split: always reconstruct from the live (unbiased) unrented share
        vu = round(V * vu_share); vr = V - vu
        # notice split: trust the raw report unless it is implausible
        if is_corrupt(s):
            N = round(O * notice_rate); nr = round(N * nr_frac); nu = N - nr
        else:
            nu, nr = s["nu"], s["nr"]
        out[s["date"]] = {"date": s["date"], "units": U, "occ": O,
                          "nr": nr, "nu": nu, "vr": vr, "vu": vu,
                          "occ_stable": O - nr - nu}
    return out

# Live, unbiased unrented share from today's unit-level vacancy report.
_vac_vu = sum(1 for u in vac if u.get("unit_status") == "Vacant-Unrented")
_vac_vr = sum(1 for u in vac if u.get("unit_status") == "Vacant-Rented")
VU_SHARE = (_vac_vu / (_vac_vu + _vac_vr)) if (_vac_vu + _vac_vr) else 0.8

TB = trusted_bands(hist, VU_SHARE)
hdates = sorted(TB)
cur = TB[hdates[-1]]               # today's reconstructed snapshot (today is clean → raw)
UNITS = cur["units"]
cur["occ_pct"] = round(cur["occ"] / UNITS * 100, 1)

def nearest(diso):
    """Latest snapshot on or before diso; else the earliest available (forward-fill)."""
    le = [x for x in hdates if x <= diso]
    return TB[le[-1]] if le else TB[hdates[0]]

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
k_out, k_in = 1 / NOTICE_DAYS, 1 / MOVEIN_LAG

def week_start(d):  # Monday-anchored week
    return d - dt.timedelta(days=d.weekday())

events_all = load("events.json")

# ── Lease-up velocity: trailing re-let demand, not a stock-clearing rate ──────
# The old model leased the entire vacant-unrented stock at 1/LEASE_DAYS, where
# LEASE_DAYS was the median days-vacant of *already-rented* units (≈14d, a
# survivorship-biased sample of fast leasers). With ~80 vacant-unrented units that
# implies ~40 signings/week — roughly 4× reality. Leasing at m5x2 is demand-
# limited, not stock-limited: units lease at the rate the market absorbs them, so
# we anchor the run-rate to trailing re-let demand (the weekly rate at which units
# fall vacant-unrented and must be re-leased), measured from the event log. This
# is a flat ~units/week velocity, applied flow-wise (not vu·k), so the forecast
# does not assume the whole stock clears inside one mean-time.
_vac_by_week = Counter(week_start(pdate(e["known"])) for e in events_all
                       if e.get("kind") == "vacant" and pdate(e.get("known")))
_recent_weeks = [week_start(TODAY) - dt.timedelta(weeks=i) for i in range(1, 9)]
_vac_vals = sorted(_vac_by_week.get(w, 0) for w in _recent_weeks)
# trimmed mean: drop the single largest week (lease-up/bulk-delivery spikes)
_trim = _vac_vals[:-1] if len(_vac_vals) > 3 else _vac_vals
WEEKLY_SIGNINGS = clamp(sum(_trim) / len(_trim) if _trim else 8.0, 3.0, 25.0)
daily_signings = WEEKLY_SIGNINGS / 7.0

# Committed move-ins: already-signed leases with a known move-in date. These are
# real, not modeled — they drive vacant-rented → occupied on their actual dates.
committed_movein = Counter(pdate(e["effective"]) for e in events_all
                           if e.get("kind") == "leased" and pdate(e.get("effective"))
                           and pdate(e["effective"]) >= TODAY)

# Share of on-notice units that get pre-leased before going vacant. Empirically ~0
# at m5x2 (backfills are signed only after a unit goes vacant), calibrated from the
# recent notice-rented share so it rises automatically if that ever changes.
recent = [TB[d] for d in hdates[-14:]]
prelease_frac = med([s["nr"] / ((s["nu"] + s["nr"]) or 1) for s in recent
                     if s["nu"] + s["nr"] > 0], 0.0)

b = {k: float(cur[k]) for k in ("occ_stable", "nr", "nu", "vr", "vu")}
def fsnap(date):
    return {"date": date.isoformat(), "projected": True,
            "nr": round(b["nr"], 1), "nu": round(b["nu"], 1),
            "vr": round(b["vr"], 1), "vu": round(b["vu"], 1)}
fwd = [{"date": TODAY.isoformat(), "projected": False, "nr": cur["nr"],
        "nu": cur["nu"], "vr": cur["vr"], "vu": cur["vu"]}]
fwd_signings = []  # (date, predicted new leases signed that day) — drives the weekly lease model
for i in range(1, FWD_DAYS + 1):
    di = TODAY + dt.timedelta(days=i)
    occ, nr, nu, vr, vu = b["occ_stable"], b["nr"], b["nu"], b["vr"], b["vu"]
    f_new = lam                            # occ → notice-unrented (fresh notice)
    f_nu_vu = nu * k_out                   # notice period ends, still unrented
    f_nr_vr = nr * k_out                   # notice period ends, backfill lined up
    # Gross signings this day, demand-limited (flat velocity), drawn first from the
    # vacant-unrented pool, with a small remainder pre-leasing on-notice units.
    sign = min(daily_signings, vu + nu * prelease_frac)
    f_vu_vr = min(vu, sign)                 # vacant-unrented unit gets leased
    f_nu_nr = sign - f_vu_vr                # remainder pre-leases an on-notice unit
    # Move-ins: honor the committed schedule when present, else the modeled lag,
    # capped at the vacant-rented stock so units are conserved.
    f_vr_occ = min(vr, max(committed_movein.get(di, 0), vr * k_in))
    b["occ_stable"] = occ - f_new + f_vr_occ
    b["nu"] = nu + f_new - f_nu_nr - f_nu_vu
    b["nr"] = nr + f_nu_nr - f_nr_vr
    b["vu"] = vu + f_nu_vu - f_vu_vr
    b["vr"] = vr + f_nr_vr + f_vu_vr - f_vr_occ
    # A "lease" = a unit entering the rented/covered set (signed lease), whether
    # it was vacant or still on notice when signed.
    fwd_signings.append((di, f_vu_vr + f_nu_nr))
    fwd.append(fsnap(di))

# ── Weekly lease-count model: predicted signings vs. already-scheduled move-ins ─
# predicted  = the forecast's weekly lease-up run-rate (Σ daily signings per week)
# scheduled  = leases already signed in the pipeline, bucketed by move-in week
#              (from the event log). The first is a model; the second is committed.
pred_by_week = {}
for d, s in fwd_signings:
    pred_by_week[week_start(d)] = pred_by_week.get(week_start(d), 0.0) + s

sched_by_week = {}
for e in events_all:
    if e.get("kind") == "leased" and e.get("effective"):
        ed = pdate(e["effective"])
        if ed and ed >= TODAY:
            sched_by_week[week_start(ed)] = sched_by_week.get(week_start(ed), 0) + 1

leases_weekly = []
w = week_start(TODAY)
w_end = week_start(TODAY + dt.timedelta(days=FWD_DAYS))
while w <= w_end:
    leases_weekly.append({"week": w.isoformat(),
                          "predicted": round(pred_by_week.get(w, 0.0), 1),
                          "scheduled": sched_by_week.get(w, 0)})
    w += dt.timedelta(days=7)

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

# ── Occupancy timeline: re-anchored tickler-delta daily occupied series ───────
# AppFolio reports a reliable point-in-time `occupied`; the tenant tickler reports
# the daily move events. Anchor on the reliable occupied snapshots and apply
# Move-in (+1) / Move-out (−1) deltas to fill DAILY between them and to project
# forward to the last scheduled move. Re-anchoring at EVERY snapshot (not just
# today) cancels acquisition drift: an onboarded property's sitting tenants never
# fire Move-in events, so a pure walk overcounts history ~100 units — but each
# snapshot already includes them, so resetting the level there keeps it exact
# (validated ±1–4 vs AppFolio occupied from 2026-03-28, the last acquisition, on).
import bisect
_tk = load("tickler.json")
def pmdy(s):
    try: return dt.datetime.strptime(s, "%m/%d/%Y").date()
    except Exception: return None
_delta = {}
for r in _tk:
    e = r.get("Event"); dd = pmdy(r.get("OccurredDate"))
    if dd is None: continue
    if e == "Move-in":  _delta[dd] = _delta.get(dd, 0) + 1
    elif e == "Move-out": _delta[dd] = _delta.get(dd, 0) - 1
_ev = sorted(_delta); _pref = {}; _run = 0
for dd in _ev: _run += _delta[dd]; _pref[dd] = _run
def cum(date):
    i = bisect.bisect_right(_ev, date) - 1
    return _pref[_ev[i]] if i >= 0 else 0
_anc = sorted((dt.date.fromisoformat(h["date"]), h["occ"], h["units"]) for h in hist)
_ad = [a[0] for a in _anc]; _aocc = {a[0]: a[1] for a in _anc}; _aun = {a[0]: a[2] for a in _anc}
def _near_anchor(date):
    i = bisect.bisect_right(_ad, date) - 1
    return _ad[i] if i >= 0 else _ad[0]
_today_occ = _aocc[_ad[-1]]
def occ_at(date):
    if date <= TODAY:
        a = _near_anchor(date); return _aocc[a] + (cum(date) - cum(a))
    return _today_occ + (cum(date) - cum(TODAY))
def units_at(date):
    return _aun[_near_anchor(min(date, TODAY))]
tl_start = _ad[0]   # first reliable occupied anchor; don't extrapolate before it
tl_end = max(_ev) if _ev else TODAY
occ_timeline = []
d = tl_start
while d <= tl_end:
    o = occ_at(d); u = units_at(d) or UNITS
    occ_timeline.append({"date": d.isoformat(), "occ": o, "units": u,
                         "pct": round(o / u * 100, 2), "projected": d > TODAY})
    d += dt.timedelta(days=1)

# ── Newsfeed / tickler from the event log ────────────────────────────────────
# Each event carries `known` (when our daily diff first saw it) and `effective`
# (when the move actually happens). On the first run every event is stamped
# `known`=today as a one-time baseline backfill, so dating the feed by `known`
# piles the entire back-catalogue (40 notices, 26 signed leases, …) onto today —
# e.g. "17 leases signed June 14", which never happened. AppFolio exposes no true
# signed/received timestamp, so for baseline rows we fall back to the only real
# per-event date we have, `effective`, which spreads them across their actual
# move dates. Genuinely new (non-baseline) rows keep their real detection date.
events = load("events.json")
LABELS = {"ntv": ("📤", "Notice to vacate", "vacates"),
          "leased": ("✅", "Lease signed", "move-in"),
          "renewal": ("🔁", "Lease renewed", "term from"),
          "moveout": ("🚪", "Moved out", "left"),
          "movein": ("📥", "Moved in", "in"),
          "vacant": ("🔻", "Vacant / unrented", "open since")}
feed = []
for e in events:
    emoji, label, verb = LABELS.get(e["kind"], ("•", e["kind"], "on"))
    where = f"{e['prop']} #{e['unit']}"
    sub = []
    if e.get("tenant"): sub.append(e["tenant"])
    if e.get("effective"): sub.append(f"{verb} {e['effective']}")
    base = bool(e.get("baseline"))
    if base: sub.append("baseline (pre-tracking)")
    # `known` is the daily-diff detection date, but on the first run every event is
    # stamped today, which piles the whole back-catalogue onto one day. `effective`
    # (move/vacate/since date) is the only real per-event date AppFolio gives, so we
    # date every row by it when present and fall back to `known` only when it is
    # missing. Caption reflects which date is shown.
    eff = e.get("effective")
    fdate = eff if eff else e["known"]
    feed.append({"date": fdate, "cap": (verb if eff else "learned"),
                 "known": e["known"], "effective": eff or "",
                 "kind": e["kind"], "title": f"{emoji} {label} — {where}",
                 "sub": " · ".join(sub)})
feed.sort(key=lambda x: (x["date"], x["known"]), reverse=True)

payload = {
    "today": TODAY.isoformat(), "units": UNITS,
    "params": {"notice_days": round(NOTICE_DAYS, 1), "lease_days": round(LEASE_DAYS, 1),
               "movein_lag": round(MOVEIN_LAG, 1), "lambda": round(lam, 2),
               "weekly_signings": round(WEEKLY_SIGNINGS, 1)},
    "current": {"occ_stable": cur["occ_stable"], "nr": cur["nr"], "nu": cur["nu"],
                "vr": cur["vr"], "vu": cur["vu"], "occ_pct": round(cur["occ"] / UNITS * 100, 1)},
    "back": back, "forward": fwd, "longterm": longterm, "feed": feed,
    "leases_weekly": leases_weekly, "occ_timeline": occ_timeline,
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
.feed .dt{color:var(--text);font-variant-numeric:tabular-nums;min-width:96px}
.feed .dt .cap{display:block;font-size:9px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}
.feed .ti{flex:1}.feed .su{color:var(--muted);font-size:12px}
.tag{font-size:10px;padding:1px 7px;border-radius:20px;text-transform:uppercase;letter-spacing:.03em;height:fit-content}
.t-ntv{background:#3a2a16;color:var(--nu)}.t-moveout{background:#3a1c1c;color:var(--vu)}
.t-vacant{background:#3a1c1c;color:var(--vu)}
.t-movein{background:#15321d;color:var(--vr)}.t-leased{background:#3a1f2e;color:var(--nr)}
.t-renewal{background:#14303a;color:#5bc8e8}
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

<h2>Leases per week — predicted signings vs. scheduled move-ins</h2>
<div class="card"><canvas id="leasesChart" height="86"></canvas>
<div class="legend">
 <span><i class="sw" style="background:var(--blue)"></i>Predicted signings (model)</span>
 <span><i class="sw" style="background:var(--vr)"></i>Scheduled move-ins (signed)</span></div>
<div class="note" id="leaseNote"></div>
<div class="note">First-pass lease model. <b>Predicted</b> = the forecast's weekly lease-up
run-rate, anchored to trailing re-let demand (the rate units fall vacant-unrented and must be
re-leased), not a stock-clearing rate. <b>Scheduled</b> = leases already signed in the pipeline,
bucketed by their move-in week — committed, and fed into the forecast as real move-ins. On-notice
units are assumed to lease only after going vacant (the observed m5x2 pattern), so they are not
pre-leased in bulk.</div></div>

<h2>Occupancy % — daily, full history → last scheduled move</h2>
<div class="card"><canvas id="occChart" height="90"></canvas>
<div class="note" id="occNote"></div>
<div class="note">Built the way AppFolio actually supports it: today's reliable point-in-time occupied is the
anchor, and the tenant tickler's Move-in (+1) / Move-out (−1) events fill every day forward and back.
Re-anchored at each occupied snapshot so portfolio acquisitions (onboarded tenants never fire a Move-in)
don't drift the line. Solid = actual, dashed = forward from scheduled moves.</div></div>

<h2>Long-term — same four states as % of portfolio, weekly since 2024</h2>
<div class="card"><canvas id="pctChart" height="90"></canvas>
<div class="note">AppFolio's rented/unrented split is not a true as-of-then snapshot — it back-applies
each unit's <b>current</b> rented flag to past dates, so historical vacant-unrented is undercounted
(a unit vacant-unrented in April but leased since reads as vacant-rented in April). Only units &amp;
occupied are reliable, so all four bands are <b>reconstructed</b>: vacancy = units − occupied (real),
split by today's live unrented share from the unit-level report; notice is estimated off occupied when
the raw figures are implausible.</div></div>

<h2>Occupancy newsfeed — daily tenant tickler</h2>
<div class="card"><ul class="feed" id="feed"></ul>
<div class="note">Each row is dated by the real event date, newest first. Genuinely new notices and
signings show their <b>detection date</b> (when our daily snapshot diff first saw them); rows marked
<i>baseline (pre-tracking)</i> predate that diffing and have no detection date, so they are dated by
their <b>move date</b> (effective) instead of all being stamped today. AppFolio exposes no signed/
received timestamp, so the effective date is the most truthful anchor for the backfill.</div></div>

<script>
const D = __PAYLOAD__;
const C={nr:'#ff5fa2',nu:'#ff8a3d',vr:'#2faa4d',vu:'#e23b3b',blue:'#2979ff'};
// Axis label formatters: drop the year for single-year spans (M/D), keep a compact
// year for the multi-year long-term chart (M/YY). `this` is the Chart.js scale.
function fmtMD(value){const s=this.getLabelForValue(value);const d=new Date(s+'T00:00');
 return (d.getMonth()+1)+'/'+d.getDate();}
function fmtMYY(value){const s=this.getLabelForValue(value);const d=new Date(s+'T00:00');
 return (d.getMonth()+1)+'/'+String(d.getFullYear()).slice(2);}
// KPIs
const c=D.current, kp=[['occ_pct','Occupied %','',c.occ_pct+'%'],
 ['vu','Vacant-Unrented','vu',c.vu],['vr','Vacant-Rented','vr',c.vr],
 ['nu','Notice-Unrented','nu',c.nu],['nr','Notice-Rented','nr',c.nr]];
document.getElementById('kpis').innerHTML=kp.map(k=>
 `<div class="kpi ${k[2]}"><div class="v">${k[3]}</div><div class="l">${k[1]}</div></div>`).join('');
const P=D.params;
document.getElementById('paramNote').textContent=
 `Forecast rates (calibrated): ${P.lambda} new notices/day · notice→vacant ~${P.notice_days}d · `+
 `lease-up velocity ~${P.weekly_signings}/wk · move-in lag ~${P.movein_lag}d.`;
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
  scales:{x:{stacked:true,grid:{display:false},
    ticks:{color:'#8b96a3',maxTicksLimit:30,autoSkip:true,maxRotation:90,minRotation:90,callback:fmtMD}},
   y:{stacked:true,ticks:{color:'#8b96a3'},grid:{color:'#1e242b'}}}}});
// ── leases-per-week chart: predicted signings vs scheduled move-ins ──
document.getElementById('leaseNote').textContent=
 `Next ${D.leases_weekly.length} weeks · predicted ~${D.leases_weekly.reduce((a,r)=>a+r.predicted,0).toFixed(0)} `+
 `signings vs ${D.leases_weekly.reduce((a,r)=>a+r.scheduled,0)} already scheduled.`;
const lw=D.leases_weekly, lwLabels=lw.map(r=>r.week);
new Chart(document.getElementById('leasesChart'),{type:'bar',
 data:{labels:lwLabels,datasets:[
   {label:'Predicted signings',data:lw.map(r=>r.predicted),backgroundColor:C.blue,borderRadius:3},
   {label:'Scheduled move-ins',data:lw.map(r=>r.scheduled),backgroundColor:C.vr,borderRadius:3}]},
 options:{responsive:true,interaction:{mode:'index',intersect:false},
  plugins:{legend:{display:false},tooltip:{callbacks:{title:i=>'week of '+i[0].label}}},
  scales:{x:{grid:{display:false},
    ticks:{color:'#8b96a3',maxRotation:90,minRotation:60,callback:fmtMD}},
   y:{beginAtZero:true,ticks:{color:'#8b96a3',precision:0},grid:{color:'#1e242b'}}}}});
// ── occupancy % timeline: daily, re-anchored tickler-delta ──
const ot=D.occ_timeline, otLabels=ot.map(r=>r.date);
const otSplit=ot.findIndex(r=>r.projected);
new Chart(document.getElementById('occChart'),{type:'line',
 data:{labels:otLabels,datasets:[{label:'Occupancy %',data:ot.map(r=>r.pct),
  borderColor:'#2faa4d',backgroundColor:'rgba(47,170,77,.10)',fill:true,pointRadius:0,
  tension:.1,borderWidth:1.5,
  segment:{borderDash:c=>otSplit>=0&&c.p0DataIndex>=otSplit-1?[5,4]:undefined}}]},
 options:{responsive:true,interaction:{mode:'index',intersect:false},
  plugins:{legend:{display:false},
   tooltip:{callbacks:{label:i=>{const r=ot[i.dataIndex];
     return `${r.pct}%  (${r.occ}/${r.units})`+(r.projected?'  · projected':'');}}}},
  scales:{x:{grid:{display:false},
    ticks:{color:'#8b96a3',maxTicksLimit:24,autoSkip:true,maxRotation:60,minRotation:60,callback:fmtMYY}},
   y:{ticks:{color:'#8b96a3',callback:v=>v+'%'},grid:{color:'#1e242b'}}}}});
{const a=ot.find(r=>r.date===D.today)||ot[ot.length-1];
 const lo=ot.reduce((m,r)=>r.pct<m.pct?r:m), hi=ot.reduce((m,r)=>r.pct>m.pct?r:m);
 document.getElementById('occNote').innerHTML=
  `Today: <b>${a.pct}%</b> (${a.occ}/${a.units}). Range ${ot[0].date}→${ot[ot.length-1].date}: `+
  `low ${lo.pct}% (${lo.date}), high ${hi.pct}% (${hi.date}).`;}
// ── long-term % chart: weekly since 2024 ──
const lt=D.longterm, ltLabels=lt.map(r=>r.date);
function pds(key,label,color){return {label,data:lt.map(r=>r[key]),
 backgroundColor:color,borderColor:color,fill:true,pointRadius:0,tension:.15,borderWidth:1};}
new Chart(document.getElementById('pctChart'),{type:'line',
 data:{labels:ltLabels,datasets:[pds('vu','Vacant-Unrented %',C.vu),pds('nu','Notice-Unrented %',C.nu),
  pds('vr','Vacant-Rented %',C.vr),pds('nr','Notice-Rented %',C.nr)]},
 options:{responsive:true,interaction:{mode:'index',intersect:false},
  plugins:{legend:{labels:{color:'#8b96a3',boxWidth:12}}},
  scales:{x:{stacked:true,grid:{display:false},
    ticks:{color:'#8b96a3',maxTicksLimit:26,autoSkip:true,maxRotation:60,minRotation:60,callback:fmtMYY}},
   y:{stacked:true,ticks:{color:'#8b96a3',callback:v=>v+'%'},grid:{color:'#1e242b'}}}}});
// ── feed: date column = real event date (effective for baseline rows) ──
document.getElementById('feed').innerHTML=D.feed.map(f=>
 `<li><span class="dt">${f.date}<span class="cap">${f.cap}</span></span>`+
 `<span class="ti">${f.title}<br><span class="su">${f.sub}</span></span>`+
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
