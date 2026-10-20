"""Build the weekly feature-grouped dashboard from weekly_data.json.

Renders three sections (Stopped / Started / Currently active) grouped by Feature.
Within each feature card, steps are grouped by stage (GIA > Omega > Beta > Canary).
Includes floating nav with jump links and an "Experiments Ready for Review" section.

Design: mockup_v2_feature_accordion.html (v2.4)
"""

from __future__ import annotations

import base64
import html
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
DATA = json.loads((ROOT / "weekly_data.json").read_text(encoding="utf-8"))
OUT = ROOT / "weekly_dashboard.html"
LOGO_PATH = ROOT / "xbox_logo.png"

EXP_FEATURE_URL = "https://exp.microsoft.com/feature/{fid}"
SCORECARD_URL = "https://exp.microsoft.com/scorecard?stepId={sid}"

EXCLUDED_GROUPS = {"xbox~validation~user", "xbox~validation~device", "xbox~xtarget"}
EXCLUDED_NAME_PATTERNS = re.compile(r"^(test |a/a\b|aa\b|test flight)", re.IGNORECASE)

REVIEW_THRESHOLD_DAYS = 14
REVIEW_NEW_WINDOW_DAYS = 7  # steps that crossed threshold within this many days are "newly ready"

DASH = "\u2014"  # em-dash placeholder (kept out of f-string braces for py<3.12 compat)

# ---------- helpers ----------

def _parse_iso(ts: str) -> datetime:
    ts = ts.replace("Z", "+00:00")
    # Python <3.11 fromisoformat requires fractional seconds to be exactly 3 or 6
    # digits. Normalize any fractional-second component to 6 digits (pad or trim).
    ts = re.sub(r"\.(\d+)", lambda m: "." + (m.group(1) + "000000")[:6], ts)
    return datetime.fromisoformat(ts)


def _duration(start: str, stop: str | None, today: datetime) -> str:
    s = _parse_iso(start)
    e = _parse_iso(stop) if stop else today
    secs = int((e - s).total_seconds())
    if secs < 0:
        secs = 0
    d = secs // 86400
    h = (secs % 86400) // 3600
    if d == 0:
        return f"{h}h"
    return f"{d}d {h}h"


def _duration_days(start: str, today: datetime) -> float:
    s = _parse_iso(start)
    return (today - s).total_seconds() / 86400


def _esc(s: str | None) -> str:
    return html.escape(s or "", quote=True)


def _derive_stage(audience: str, step_name: str, exp_name: str) -> tuple[str, int]:
    """Use experiment displayName as the stage name. Assign a rank for sorting."""
    stage = exp_name.strip() if exp_name.strip() else "\u2014"
    lower = stage.lower()

    # Rank for sorting (higher = broader audience, shown first)
    if any(x in lower for x in ["global", "gia", "prod"]):
        return stage, 6
    if lower in ("ga", "general audience", "general") or "general" in lower:
        return stage, 5
    if any(x in lower for x in ["omega", "delta"]):
        return stage, 4
    if any(x in lower for x in ["beta", "external"]):
        return stage, 3
    if "alpha" in lower:
        return stage, 2
    if any(x in lower for x in ["canary", "dogfood", "internal", "selfhost", "takehome"]):
        return stage, 1
    return stage, 0


def _stage_css_class(stage: str) -> str:
    s = stage.lower()
    if any(x in s for x in ["global", "gia", "ga", "general", "prod"]):
        return "stage-gia"
    if any(x in s for x in ["omega", "delta"]):
        return "stage-omega"
    if any(x in s for x in ["beta", "external"]):
        return "stage-beta"
    if any(x in s for x in ["canary", "dogfood", "internal", "selfhost", "alpha", "takehome"]):
        return "stage-canary"
    return ""


def _shorten_audience(audience: str) -> str:
    """Shorten long audience filter expressions for display."""
    if not audience or len(audience) <= 60:
        return audience
    parts = []
    for segment in audience.split(";"):
        segment = segment.strip()
        match = re.search(r'IN \("([^"]+)"', segment)
        if match:
            parts.append(match.group(1))
    if parts:
        return " \u00b7 ".join(parts[:3])
    return audience[:57] + "\u2026"


def _build_experiment_step_sequence():
    """Build a map of experimentId -> list of all steps sorted by startedAt."""
    exp_steps = defaultdict(list)
    for bucket in ("started", "stopped", "active"):
        for step in DATA["buckets"][bucket]:
            exp_steps[step["experimentId"]].append(step)
    # Sort each experiment's steps by startedAt
    for eid in exp_steps:
        exp_steps[eid].sort(key=lambda s: s.get("startedAt", ""))
    return exp_steps

_EXP_STEP_SEQ = _build_experiment_step_sequence()


def _compute_outcome(step: dict) -> str:
    """Classify a stopped step's decision.

    Logic:
    1. If nextStepName is populated, use it to classify.
    2. Otherwise, check if a later step started for the same experiment.
       If yes, the current step was promoted/rolled out.
    3. If no successor exists, it's Stopped or Completed.
    """
    next_step = (step.get("nextStepName") or "").strip()

    # If nextStepName is explicitly set, use it
    if next_step:
        lower = next_step.lower()
        if "ab" in lower or "a/b" in lower:
            return "Promoted"
        if "dc" in lower or "datacollection" in lower or "100%" in lower or "100 %" in lower:
            return "Rolled out"
        if "switch to" in lower or "%" in lower:
            return "Promoted"
        return "Promoted"

    # Check if a later step exists for this experiment
    exp_id = step["experimentId"]
    steps_in_exp = _EXP_STEP_SEQ.get(exp_id, [])
    step_start = step.get("startedAt", "")

    for s in steps_in_exp:
        if s.get("startedAt", "") > step_start and s["stepId"] != step["stepId"]:
            # A later step exists — this one was promoted
            later_type = s.get("analysisType", "")
            if later_type == "DataCollection" or "100" in s.get("splits", ""):
                return "Rolled out"
            return "Promoted"

    # No successor — check if it's a DC 100% (completed) or truly stopped
    is_dc = step.get("analysisType") == "DataCollection"
    splits = step.get("splits") or ""
    if is_dc and "100" in splits:
        return "Completed"

    return "Stopped"


def _decision_class(outcome: str) -> str:
    m = {
        "Promoted": "decision-promoted",
        "Rolled out": "decision-rolled",
        "Completed": "decision-rolled",
        "Stopped": "decision-killed",
        "Running": "decision-running",
    }
    return m.get(outcome, "decision-pending")


def _is_excluded(feature: dict, feature_id: str) -> bool:
    group = feature.get("experimentationGroup", "")
    name = feature.get("displayName", "")
    if group in EXCLUDED_GROUPS:
        return True
    if EXCLUDED_NAME_PATTERNS.match(name):
        return True
    return False


def _is_review_required(step: dict, today: datetime) -> bool:
    """Step running >= 14 days with scorecard available."""
    if step.get("stoppedAt"):
        return False
    days = _duration_days(step["startedAt"], today)
    if days < REVIEW_THRESHOLD_DAYS:
        return False
    # If hasScorecard field exists, use it; otherwise assume available at 14+ days
    if "hasScorecard" in step:
        return step["hasScorecard"]
    return True


def _is_newly_ready(step: dict, today: datetime) -> bool:
    """Step crossed 14-day threshold THIS week (14 to 14+7 days old)."""
    if step.get("stoppedAt"):
        return False
    days = _duration_days(step["startedAt"], today)
    return REVIEW_THRESHOLD_DAYS <= days < REVIEW_THRESHOLD_DAYS + REVIEW_NEW_WINDOW_DAYS


def _is_previously_ready(step: dict, today: datetime) -> bool:
    """Step was already past 14 days before this week (21+ days old)."""
    if step.get("stoppedAt"):
        return False
    days = _duration_days(step["startedAt"], today)
    return days >= REVIEW_THRESHOLD_DAYS + REVIEW_NEW_WINDOW_DAYS


# ---------- grouping logic ----------

def _group_steps_by_feature(buckets: dict) -> dict:
    """Group all steps by featureId, preserving which bucket they came from."""
    feature_steps = defaultdict(lambda: {"started": [], "stopped": [], "active": []})

    for bucket_name in ("started", "stopped", "active"):
        for step in buckets[bucket_name]:
            exp_id = step["experimentId"]
            exp = DATA["experiments"].get(exp_id)
            if not exp:
                continue
            feature_id = exp["featureId"]
            feature_steps[feature_id][bucket_name].append(step)

    return feature_steps


def _assign_feature_bucket(steps_by_bucket: dict) -> str:
    """Assign a feature to a single section.

    Priority: active > started > stopped.
    A feature only lands in "stopped" if ALL its steps ended this week.
    """
    if steps_by_bucket["active"]:
        return "active"
    if steps_by_bucket["started"]:
        return "started"
    if steps_by_bucket["stopped"]:
        return "stopped"
    return "active"


def _group_steps_by_stage(steps: list[dict], today: datetime) -> list[dict]:
    """Group a list of steps by their derived stage, sorted by stage rank (highest first)."""
    stage_groups = defaultdict(list)
    stage_ranks = {}

    for step in steps:
        exp = DATA["experiments"].get(step["experimentId"], {})
        audience = exp.get("audience", "")
        exp_name = exp.get("displayName", "")
        stage_label, rank = _derive_stage(audience, step.get("stepName", ""), exp_name)
        stage_groups[stage_label].append(step)
        stage_ranks[stage_label] = max(stage_ranks.get(stage_label, 0), rank)

    sorted_stages = sorted(stage_groups.keys(), key=lambda s: stage_ranks.get(s, 0), reverse=True)

    result = []
    for stage in sorted_stages:
        exp = DATA["experiments"].get(stage_groups[stage][0]["experimentId"], {})
        audience = exp.get("audience", "")
        result.append({
            "stage": stage,
            "rank": stage_ranks[stage],
            "audience": _shorten_audience(audience),
            "steps": sorted(stage_groups[stage], key=lambda s: s.get("startedAt", ""), reverse=True),
        })
    return result


# ---------- HTML rendering ----------

def _render_step_row(step: dict, today: datetime, is_stopped: bool) -> str:
    stop_time = step.get("stoppedAt")
    dur = _duration(step["startedAt"], stop_time, today)

    # Determine outcome per-step (a card may have both stopped and running steps)
    step_is_stopped = bool(stop_time)
    if step_is_stopped:
        outcome = _compute_outcome(step)
    else:
        outcome = "Running"

    decision_cls = _decision_class(outcome)
    step_id = step["stepId"]
    scorecard_link = SCORECARD_URL.format(sid=step_id)

    # Scorecard availability: use hasScorecard field from data if available,
    # otherwise fall back to heuristic (step running >= 1 day).
    days_running = _duration_days(step["startedAt"], today)
    if "hasScorecard" in step:
        has_sc = step["hasScorecard"]
    else:
        has_sc = step_is_stopped or days_running >= 1
    if has_sc:
        sc_html = f'<a class="sc-link" href="{scorecard_link}" target="_blank">\U0001f4ca View \u2197</a>'
    else:
        sc_html = '<span class="sc-unavailable">No scorecard</span>'

    if step_is_stopped and not step.get("nextStepName") and stop_time:
        dur_secs = (_parse_iso(stop_time) - _parse_iso(step["startedAt"])).total_seconds()
        if dur_secs < 86400:
            sc_html = '<span class="sc-unavailable">No scorecard</span>'

    # Format start/end dates
    start_date = _parse_iso(step["startedAt"]).strftime("%b %d") if step.get("startedAt") else "\u2014"
    end_date = _parse_iso(stop_time).strftime("%b %d") if stop_time else "\u2014"

    # Review-ready: row tint class + sub-row badge
    ready_cls = " is-ready-review" if (not step_is_stopped and _is_review_required(step, today)) else ""
    review_sub_row = ""
    if ready_cls:
        review_sub_row = f'\n            <tr class="review-sub-row"><td colspan="7">\U0001f3af 2-week scorecard available</td></tr>'

    return f'            <tr class="step-row{ready_cls}"><td>{_esc(step.get("stepName", DASH))}</td><td class="split-chip">{_esc(step.get("splits", DASH))}</td><td>{start_date}</td><td>{end_date}</td><td class="duration-chip">{dur}</td><td><span class="decision-chip {decision_cls}">{_esc(outcome)}</span></td><td>{sc_html}</td></tr>{review_sub_row}'


def _render_stage_group(sg: dict, today: datetime, is_stopped: bool) -> str:
    stage_cls = _stage_css_class(sg["stage"])
    rows = "\n".join(_render_step_row(s, today, is_stopped) for s in sg["steps"])
    return f"""      <div class="stage-group">
        <div class="stage-group-header">
          <span class="stage-name {stage_cls}">{_esc(sg['stage'])}</span>
          <span class="stage-audience">{_esc(sg['audience'])}</span>
        </div>
        <table class="step-table">
          <thead><tr><th>Step</th><th>Split</th><th>Start</th><th>End</th><th>Duration</th><th>Decision</th><th>Scorecard</th></tr></thead>
          <tbody>
{rows}
          </tbody>
        </table>
      </div>"""


def _render_feature_card(feature_id: str, feature: dict, all_steps: list[dict],
                         today: datetime, is_stopped: bool, is_open: bool = False) -> str:
    review_required = any(_is_review_required(s, today) for s in all_steps if not s.get("stoppedAt"))
    review_cls = " review-required" if review_required else ""
    review_attr = ' data-review="true"' if review_required else ""
    open_cls = " open" if is_open else ""

    feature_link = EXP_FEATURE_URL.format(fid=feature_id)
    group = feature.get("experimentationGroup", "")
    desc = feature.get("description", "") or ""
    if len(desc) > 280:
        desc = desc[:277] + "\u2026"

    stage_groups = _group_steps_by_stage(all_steps, today)
    stages_html = "\n".join(_render_stage_group(sg, today, is_stopped) for sg in stage_groups)

    return f"""  <div class="feature-card{open_cls}{review_cls}"{review_attr}>
    <div class="feature-header" onclick="this.parentElement.classList.toggle('open')">
      <div class="feature-toggle">\u25b6</div>
      <div class="feature-name">{_esc(feature['displayName'])}</div>
      <div class="feature-group">{_esc(group)}</div>
      <a class="exp-link" href="{feature_link}" target="_blank" onclick="event.stopPropagation()">\U0001f9e9 EXP \u2197</a>
    </div>
    <div class="feature-body">
      <div class="feature-desc">{_esc(desc)}</div>
{stages_html}
    </div>
  </div>"""


# ---------- main ----------

def main():
    today = datetime.fromisoformat(DATA["generated_for_date"]).replace(tzinfo=timezone.utc)

    feature_steps = _group_steps_by_feature(DATA["buckets"])

    sections = {"stopped": [], "started": [], "active": []}

    for feature_id, steps_by_bucket in feature_steps.items():
        feature = DATA["features"].get(feature_id)
        if not feature:
            continue
        if _is_excluded(feature, feature_id):
            continue

        ab_steps = {
            k: [s for s in v if s.get("analysisType") == "AB"]
            for k, v in steps_by_bucket.items()
        }

        all_ab = ab_steps["started"] + ab_steps["stopped"] + ab_steps["active"]
        if not all_ab:
            continue

        bucket = _assign_feature_bucket(ab_steps)
        # Show ALL steps for the feature (across all buckets) so nothing is hidden
        display_steps = ab_steps["started"] + ab_steps["stopped"] + ab_steps["active"]

        sections[bucket].append((feature_id, feature, display_steps))

    # Build "Experiments Ready for Review" section
    # Features from started+active that have review-ready steps
    review_new = []   # features with at least one newly-ready step (14-21 days)
    review_older = [] # features with review-ready steps but all 21+ days
    for bucket in ("started", "active"):
        for fid, feature, steps in sections[bucket]:
            active_steps = [s for s in steps if not s.get("stoppedAt")]
            has_newly = any(_is_newly_ready(s, today) for s in active_steps)
            has_prev = any(_is_previously_ready(s, today) for s in active_steps)
            if has_newly:
                review_new.append((fid, feature, steps))
            elif has_prev:
                review_older.append((fid, feature, steps))

    review_count = len(review_new) + len(review_older)

    # Count outcomes in stopped
    n_promoted = 0
    n_rolled = 0
    n_stopped_decision = 0
    for _, _, steps in sections["stopped"]:
        outcomes = [_compute_outcome(s) for s in steps]
        if "Rolled out" in outcomes or "Completed" in outcomes:
            n_rolled += 1
        elif "Promoted" in outcomes:
            n_promoted += 1
        else:
            n_stopped_decision += 1

    # Render sections
    review_new_html = "\n".join(
        _render_feature_card(fid, f, steps, today, is_stopped=False, is_open=(i == 0))
        for i, (fid, f, steps) in enumerate(review_new)
    )
    review_older_html = "\n".join(
        _render_feature_card(fid, f, steps, today, is_stopped=False)
        for fid, f, steps in review_older
    )
    stopped_html = "\n".join(
        _render_feature_card(fid, f, steps, today, is_stopped=True, is_open=(i == 0))
        for i, (fid, f, steps) in enumerate(sections["stopped"])
    )
    started_html = "\n".join(
        _render_feature_card(fid, f, steps, today, is_stopped=False)
        for fid, f, steps in sections["started"]
    )
    active_html = "\n".join(
        _render_feature_card(fid, f, steps, today, is_stopped=False)
        for fid, f, steps in sections["active"]
    )

    n_stopped = len(sections["stopped"])
    n_started = len(sections["started"])
    n_active = len(sections["active"])

    # Load Xbox logo as base64
    logo_b64 = ""
    if LOGO_PATH.exists():
        logo_b64 = base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii")

    logo_img = f'<img src="data:image/png;base64,{logo_b64}" alt="Xbox">' if logo_b64 else ""

    html_out = _build_html(
        logo_img=logo_img,
        week_label=DATA["week_label"],
        gen_date=DATA["generated_for_date"],
        n_stopped=n_stopped, n_started=n_started, n_active=n_active,
        n_promoted=n_promoted, n_rolled=n_rolled,
        review_count=review_count,
        n_review_new=len(review_new), n_review_older=len(review_older),
        review_new_html=review_new_html, review_older_html=review_older_html,
        stopped_html=stopped_html, started_html=started_html, active_html=active_html,
    )

    OUT.write_text(html_out, encoding="utf-8")
    print(f"Wrote {OUT}")
    print(f"  stopped: {n_stopped} features ({n_promoted} promoted, {n_rolled} rolled out, {n_stopped_decision} stopped)")
    print(f"  started: {n_started} features")
    print(f"  active:  {n_active} features")
    print(f"  ready for review: {review_count} ({len(review_new)} newly ready, {len(review_older)} previously ready)")


def _build_html(*, logo_img, week_label, gen_date,
                n_stopped, n_started, n_active,
                n_promoted, n_rolled, review_count,
                n_review_new, n_review_older,
                review_new_html, review_older_html,
                stopped_html, started_html, active_html) -> str:
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>XBOX Experimentation \u2014 Weekly Activity</title>
<script>
(() => {{
  const p = new URLSearchParams(location.search).get("clawpilotTheme");
  document.documentElement.setAttribute("data-theme",
    p || (matchMedia("(prefers-color-scheme:dark)").matches ? "dark" : "light"));
}})();
</script>
<style>
:root {{
  color-scheme:light;
  --bg:#f7f4ef; --surface:#fff; --surface-soft:#f5f5f5;
  --border:#dedede; --border-strong:#919191;
  --text:#242424; --text-muted:#5c5c5c; --text-soft:#6f6f6f;
  --accent:#107c10; --accent-soft:rgba(16,124,16,.08);
  --success:#16a34a; --success-soft:rgba(22,163,74,.10);
  --danger:#dc2626; --danger-soft:rgba(220,38,38,.10);
  --info:#0078d4; --info-soft:rgba(0,120,212,.10);
  --warn:#d97706; --warn-soft:rgba(217,119,6,.12);
  --link:#0078d4;
  --shadow:0 1px 3px rgba(0,0,0,.08),0 1px 2px rgba(0,0,0,.12);
  --shadow-lg:0 4px 12px rgba(0,0,0,.10);
  --ready:#7c3aed; --ready-soft:rgba(124,58,237,.10);
}}
html[data-theme="dark"] {{
  color-scheme:dark;
  --bg:#1a1a1a; --surface:#252525; --surface-soft:#2e2e2e;
  --border:#3a3a3a; --border-strong:#555;
  --text:#e0e0e0; --text-muted:#999; --text-soft:#b0b0b0;
  --accent:#5dc21e; --accent-soft:rgba(93,194,30,.14);
  --success:#4ade80; --success-soft:rgba(74,222,128,.14);
  --danger:#f87171; --danger-soft:rgba(248,113,113,.14);
  --info:#4da6ff; --info-soft:rgba(77,166,255,.14);
  --warn:#fbbf24; --warn-soft:rgba(251,191,36,.14);
  --link:#4da6ff;
  --shadow:0 1px 3px rgba(0,0,0,.3);
  --shadow-lg:0 4px 12px rgba(0,0,0,.4);
  --ready:#a78bfa; --ready-soft:rgba(167,139,250,.14);
}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:var(--bg);color:var(--text);font-family:"Segoe UI",Aptos,Calibri,-apple-system,sans-serif;max-width:1080px;margin:0 auto;padding:32px 24px;line-height:1.5}}
.header{{display:flex;align-items:center;gap:16px;margin-bottom:24px;padding-bottom:20px;border-bottom:1px solid var(--border)}}
.header img{{height:100px;width:auto}}
.header-text h1{{font-size:22px;font-weight:700;letter-spacing:-.3px}}
.header-text .subtitle{{font-size:13px;color:var(--text-muted);margin-top:2px}}
.floating-nav{{position:sticky;top:0;z-index:10;background:var(--bg);border-bottom:1px solid var(--border);padding:10px 0;margin-bottom:20px;display:flex;gap:8px;flex-wrap:wrap;align-items:center}}
.nav-link{{font-size:12px;font-weight:600;color:var(--text-muted);text-decoration:none;padding:6px 12px;border-radius:6px;border:1px solid var(--border);background:var(--surface);transition:all .12s}}
.nav-link:hover{{background:var(--info-soft);border-color:var(--link);color:var(--link)}}
.nav-link .nav-count{{font-size:10px;font-weight:700;margin-left:4px;opacity:.7}}
.nav-divider{{width:1px;height:20px;background:var(--border);margin:0 4px}}

.section{{margin-bottom:32px;scroll-margin-top:60px}}
.section-header{{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:12px}}
.section-title{{font-size:17px;font-weight:600}}
.section-count{{font-size:12px;color:var(--text-muted)}}
.subsection-header{{font-size:13px;font-weight:600;color:var(--text-muted);margin:20px 0 10px 0;padding-bottom:6px;border-bottom:1px solid var(--border)}}
.feature-card{{background:var(--surface);border:1px solid var(--border);border-radius:10px;box-shadow:var(--shadow);margin-bottom:8px;overflow:hidden;transition:all .15s}}
.feature-card:hover{{box-shadow:var(--shadow-lg)}}
.feature-card.review-required{{border-left:3px solid var(--ready)}}

.feature-header{{display:flex;align-items:center;gap:10px;padding:12px 16px;cursor:pointer;user-select:none}}
.feature-header:hover{{background:var(--surface-soft)}}
.feature-toggle{{width:18px;height:18px;display:flex;align-items:center;justify-content:center;font-size:11px;color:var(--text-muted);transition:transform .2s;flex-shrink:0}}
.feature-card.open .feature-toggle{{transform:rotate(90deg)}}
.feature-name{{font-size:14px;font-weight:600;flex:1;line-height:1.3}}
.feature-group{{font-size:10px;color:var(--text-muted);background:var(--surface-soft);border:1px solid var(--border);padding:2px 7px;border-radius:4px;white-space:nowrap}}
.ready-badge{{font-size:10px;font-weight:700;color:var(--ready);background:var(--ready-soft);border:1px solid var(--ready);padding:2px 7px;border-radius:999px;white-space:nowrap;letter-spacing:.2px}}
.exp-link{{font-size:11px;font-weight:600;color:var(--link);text-decoration:none;display:inline-flex;align-items:center;gap:3px;padding:3px 8px;border-radius:5px;border:1px solid var(--border);flex-shrink:0}}
.exp-link:hover{{background:var(--info-soft);border-color:var(--link)}}
.feature-body{{display:none;padding:0 16px 14px;border-top:1px solid var(--border)}}
.feature-card.open .feature-body{{display:block}}
.feature-desc{{font-size:12px;color:var(--text-soft);margin:10px 0 12px;line-height:1.4}}
.stage-group{{margin-bottom:10px}}
.stage-group:last-child{{margin-bottom:0}}
.stage-group-header{{display:flex;align-items:center;gap:8px;margin-bottom:6px;padding:4px 0}}
.stage-name{{font-size:11px;font-weight:600;padding:2px 7px;border-radius:4px;background:var(--surface-soft);border:1px solid var(--border);display:inline-block}}
.stage-gia{{border-color:var(--accent);color:var(--accent)}}
.stage-omega{{border-color:var(--info);color:var(--info)}}
.stage-beta{{border-color:var(--warn);color:var(--warn)}}
.stage-canary{{border-color:var(--border-strong);color:var(--text-muted)}}
.stage-audience{{font-size:10px;color:var(--text-muted);font-style:italic}}
.step-table{{width:100%;border-collapse:collapse;font-size:12px;margin-left:8px}}
.step-table th{{text-align:left;font-weight:600;color:var(--text-muted);font-size:10px;text-transform:uppercase;letter-spacing:.4px;padding:4px 8px;border-bottom:1px solid var(--border);background:var(--surface-soft)}}
.step-table td{{padding:6px 8px;border-bottom:1px solid var(--border);vertical-align:middle}}
.step-table tr:last-child td{{border-bottom:none}}
.step-row.is-ready-review{{background:linear-gradient(90deg, rgba(246,193,119,0.08), transparent 60%)}}
.step-row.is-ready-review td:first-child{{position:relative}}
.step-row.is-ready-review td:first-child::before{{content:"";position:absolute;left:0;top:6px;bottom:6px;width:3px;border-radius:999px;background:var(--ready);box-shadow:0 0 6px rgba(246,193,119,0.4)}}
.review-sub-row td{{padding:3px 8px 6px 12px!important;font-size:11px;font-weight:600;color:var(--ready);border-bottom:1px solid var(--border);background:rgba(246,193,119,0.04)}}
.split-chip{{font-weight:600;color:var(--text)}}
.duration-chip{{font-size:11px;color:var(--text-muted)}}
.decision-chip{{font-size:10px;font-weight:600;padding:2px 7px;border-radius:4px;display:inline-block}}
.decision-promoted{{background:var(--success-soft);color:var(--success)}}
.decision-rolled{{background:var(--accent-soft);color:var(--accent)}}
.decision-killed{{background:var(--danger-soft);color:var(--danger)}}
.decision-running{{background:var(--info-soft);color:var(--info)}}
.decision-pending{{background:var(--surface-soft);color:var(--text-muted);border:1px solid var(--border)}}
.sc-link{{font-size:11px;font-weight:600;color:var(--link);text-decoration:none;display:inline-flex;align-items:center;gap:3px}}
.sc-link:hover{{text-decoration:underline}}
.sc-unavailable{{font-size:11px;color:var(--text-muted);font-style:italic;opacity:.7}}
.excluded-note{{font-size:11px;color:var(--text-muted);font-style:italic;padding:8px 0}}
.empty{{padding:18px;color:var(--text-muted);background:var(--surface-soft);border-radius:8px;border:1px dashed var(--border);font-size:13px;text-align:center}}
@media(max-width:700px){{body{{padding:20px 12px}} .step-table{{font-size:11px}} .feature-header{{flex-wrap:wrap}} .floating-nav{{gap:4px}}}}
</style>
</head>
<body>

<div class="header">
  {logo_img}
  <div class="header-text">
    <h1>XBOX Experimentation \u2014 Weekly</h1>
    <div class="subtitle">Week of {week_label} \u00b7 Generated {gen_date}</div>
  </div>
</div>

<nav class="floating-nav">
  <a class="nav-link" href="#section-review">\U0001f3af Ready for Review<span class="nav-count">{n_review_new} new \u00b7 {n_review_older} older</span></a>
  <div class="nav-divider"></div>
  <a class="nav-link" href="#section-stopped">Stopped<span class="nav-count">{n_stopped}</span></a>
  <a class="nav-link" href="#section-started">Started<span class="nav-count">{n_started}</span></a>
  <a class="nav-link" href="#section-active">Active<span class="nav-count">{n_active}</span></a>
</nav>

<div class="section" id="section-review">
  <div class="section-header">
    <div class="section-title">Experiments Ready for Review</div>
    <div class="section-count">{review_count} features</div>
  </div>
  <div class="subsection-header">Newly ready \u2014 2-week scorecard now available ({n_review_new})</div>
{review_new_html if review_new_html else '  <div class="empty">No experiments newly ready for review this week.</div>'}
  <div class="subsection-header">Previously ready \u2014 scorecard available 3+ weeks ({n_review_older})</div>
{review_older_html if review_older_html else '  <div class="empty">No older experiments awaiting review.</div>'}
</div>

<div class="section" id="section-stopped">
  <div class="section-header">
    <div class="section-title">Stopped last week</div>
    <div class="section-count">{n_stopped} features</div>
  </div>
  <div class="excluded-note">Validation group and test experiments excluded.</div>
{stopped_html if stopped_html else '  <div class="empty">No features with stopped A/B steps this week.</div>'}
</div>

<div class="section" id="section-started">
  <div class="section-header">
    <div class="section-title">Started last week</div>
    <div class="section-count">{n_started} features</div>
  </div>
  <div class="excluded-note">Validation group and test experiments excluded.</div>
{started_html if started_html else '  <div class="empty">No new A/B experiments started this week.</div>'}
</div>

<div class="section" id="section-active">
  <div class="section-header">
    <div class="section-title">Currently active</div>
    <div class="section-count">{n_active} features running</div>
  </div>
  <div class="excluded-note">Validation group and test experiments excluded.</div>
{active_html if active_html else '  <div class="empty">No active A/B experiments.</div>'}
</div>

<div style="margin-top:40px;padding:12px 16px;background:var(--surface-soft);border-radius:8px;border:1px dashed var(--border);font-size:11px;color:var(--text-muted);line-height:1.5">
<strong>Scope.</strong> A/B steps only. Validation group and test experiments excluded. Grouped by Feature \u2192 Stage (GIA \u2192 Omega \u2192 Beta \u2192 Canary). <strong>Ready for Review</strong> = step running \u226514 days (2-week scorecard available). "Newly ready" = crossed threshold this week (14\u201321 days). Data pulled from ExP MCP.
</div>

</body>
</html>'''


if __name__ == "__main__":
    main()
