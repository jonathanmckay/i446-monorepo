import datetime
import re
from zoneinfo import ZoneInfo
from typing import Optional

from mcp.server.fastmcp import FastMCP

from . import toggl_api
from .config import PROJECT_MAP, PROJECT_NAMES, TIMEZONE

mcp = FastMCP(
    "Toggl Time Tracker",
    instructions="""When the user does not specify a project, infer the project code from the description using these domain mappings:

- hcmc — media & consumption: 新闻, news, podcasts, YouTube, reading, TV, movies, articles
- hcm — mindfulness: meditation, journaling, reflection, therapy
- hcb — health & body: exercise, gym, sports, running, yoga, walking, stretching
- i9 — Microsoft / GitHub work: meetings, coding (work), 1:1s, reviews, standups
- m5x2 — McKay Capital: real estate, property, tenant, rental
- qz12 — personal finance: investing, stocks, portfolio, taxes, budgeting
- 家 — home & family: cooking, cleaning, kids, errands, chores, groceries, family time
- 睡觉 — sleep: 睡觉, nap, sleep
- xk88 — social: friends, dinner out, party, socializing, dates
- s897 — social (extended): community events, networking
- g245 — goals & planning: weekly review, quarterly review, goal setting, planning
- i447 — infrastructure & admin: admin, setup, tooling, systems
- h335 — career: career development, resume, interviews, job search
- m828 — non-profit
- infra — technical infrastructure
- f8 — fast track / career progression

The user frequently uses abbreviations and shorthand for descriptions. When the description is ambiguous or abbreviated, use toggl_today to check recent entries for pattern matches — if the same or similar abbreviation was recently used with a specific project, reuse that project assignment.

If the description clearly matches a domain, always pass the inferred project code. If ambiguous and no recent pattern exists, omit the project and let the user clarify.""",
)


TZ = ZoneInfo(TIMEZONE)


def _resolve_project(code: str) -> Optional[int]:
    if not code:
        return None
    code_lower = code.lower().strip()
    if code_lower in PROJECT_MAP:
        return PROJECT_MAP[code_lower]
    # Try as numeric ID
    try:
        return int(code)
    except ValueError:
        return None


def _parse_time(time_str: str, ref_date: datetime.date = None) -> datetime.datetime:
    """Parse flexible time formats into a timezone-aware datetime."""
    time_str = time_str.strip()
    if ref_date is None:
        ref_date = datetime.datetime.now(TZ).date()

    # "HH:MM" -> today at that time
    if len(time_str) <= 5 and ":" in time_str:
        parts = time_str.split(":")
        h, m = int(parts[0]), int(parts[1])
        return datetime.datetime(ref_date.year, ref_date.month, ref_date.day, h, m, tzinfo=TZ)

    # "YYYY-MM-DD HH:MM"
    if " " in time_str and len(time_str) >= 10:
        date_part, time_part = time_str.rsplit(" ", 1)
        d = datetime.date.fromisoformat(date_part)
        parts = time_part.split(":")
        h, m = int(parts[0]), int(parts[1])
        return datetime.datetime(d.year, d.month, d.day, h, m, tzinfo=TZ)

    # ISO 8601 passthrough
    return datetime.datetime.fromisoformat(time_str).replace(tzinfo=TZ)


def _filter_entries_by_local_date(entries: list[dict], target_date: datetime.date) -> list[dict]:
    """Filter entries whose start time falls on target_date in local timezone."""
    result = []
    for e in entries:
        start_str = e.get("start", "")
        if not start_str:
            continue
        try:
            start_dt = datetime.datetime.fromisoformat(start_str).astimezone(TZ)
            if start_dt.date() == target_date:
                result.append(e)
        except (ValueError, TypeError):
            continue
    return result


def _format_duration(seconds: int) -> str:
    h, remainder = divmod(abs(seconds), 3600)
    m, _ = divmod(remainder, 60)
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m"


def _format_entry(e: dict) -> str:
    desc = e.get("description", "(no description)")
    proj_id = e.get("project_id")
    proj = PROJECT_NAMES.get(proj_id, str(proj_id) if proj_id else "")
    dur = e.get("duration", 0)
    tags = e.get("tags") or []
    tag_str = f" #{','.join(tags)}" if tags else ""

    start = e.get("start", "")
    if start:
        try:
            st = datetime.datetime.fromisoformat(start).astimezone(TZ)
            start = st.strftime("%H:%M")
        except (ValueError, TypeError):
            pass

    stop = e.get("stop", "")
    if stop:
        try:
            sp = datetime.datetime.fromisoformat(stop).astimezone(TZ)
            stop = sp.strftime("%H:%M")
        except (ValueError, TypeError):
            stop = "running"
    else:
        stop = "running"

    dur_str = _format_duration(dur) if dur > 0 else "running"
    proj_str = f" @{proj}" if proj else ""
    return f"{start}-{stop} {desc}{proj_str} ({dur_str}){tag_str} [id:{e['id']}]"


def _create_single_entry(
    description: str,
    start_dt: datetime.datetime,
    end_dt: datetime.datetime,
    project_id: Optional[int] = None,
    tags: list[str] = None,
) -> list[str]:
    """Create one entry, splitting at midnight if needed. Returns list of result strings."""
    results = []
    midnight = datetime.datetime(start_dt.year, start_dt.month, start_dt.day, 23, 59, 0, tzinfo=TZ)
    next_midnight = midnight + datetime.timedelta(minutes=1)

    if start_dt.date() != end_dt.date():
        dur1 = int((midnight - start_dt).total_seconds())
        dur2 = int((end_dt - next_midnight).total_seconds())
        e1 = toggl_api.create_entry(description, start_dt.isoformat(), midnight.isoformat(), dur1, project_id, tags)
        e2 = toggl_api.create_entry(description, next_midnight.isoformat(), end_dt.isoformat(), dur2, project_id, tags)
        results.append(f"Split at midnight: {_format_entry(e1)}")
        results.append(f"  continued: {_format_entry(e2)}")
    else:
        duration = int((end_dt - start_dt).total_seconds())
        entry = toggl_api.create_entry(description, start_dt.isoformat(), end_dt.isoformat(), duration, project_id, tags)
        results.append(f"Created: {_format_entry(entry)}")
    return results


@mcp.tool()
def toggl_create_entry(
    description: str,
    start_time: str,
    end_time: str,
    project: str = "",
    tags: list[str] = None,
    date: str = "",
) -> str:
    """Create a completed time entry with specific start and end times.

    Args:
        description: What you were doing
        start_time: Start time as "HH:MM" (today) or "YYYY-MM-DD HH:MM"
        end_time: End time as "HH:MM" (today) or "YYYY-MM-DD HH:MM"
        project: Project code (i9, hcb, g245, hcmc, etc.) or numeric ID
        tags: Optional list of tag strings
        date: Optional date as "YYYY-MM-DD" if start_time/end_time are HH:MM and you mean a different day
    """
    try:
        ref_date = datetime.date.fromisoformat(date) if date else None
        start_dt = _parse_time(start_time, ref_date)
        end_dt = _parse_time(end_time, ref_date)

        if end_dt <= start_dt:
            return f"Error: end_time ({end_time}) must be after start_time ({start_time})"

        project_id = _resolve_project(project)
        if project and not project_id:
            return f"Error: unknown project code '{project}'. Valid codes: {', '.join(sorted(PROJECT_MAP.keys()))}"

        results = _create_single_entry(description, start_dt, end_dt, project_id, tags)
        return "\n".join(results)

    except Exception as e:
        return f"Error: {e}"


def _resolve_date(date_str: str) -> datetime.date:
    """Resolve 'yesterday', 'today', or YYYY-MM-DD to a date object."""
    date_str = date_str.strip().lower()
    today = datetime.datetime.now(TZ).date()
    if not date_str or date_str == "today":
        return today
    if date_str == "yesterday":
        return today - datetime.timedelta(days=1)
    return datetime.date.fromisoformat(date_str)


_BATCH_ENTRY_RE = re.compile(
    r"^(\d{2}:?\d{2})-(\d{2}:?\d{2})\s+(.+?)(?:\s+@(\S+))?\s*$"
)


@mcp.tool()
def toggl_batch(entries: str, date: str = "") -> str:
    """Create multiple time entries in one call using shorthand notation.

    Each entry is comma-separated: "HHMM-HHMM description @project, ..."
    Times can be HH:MM or HHMM format.

    Args:
        entries: Comma-separated entries, e.g. "1600-1700 work @i9, 1700-1750 math @xk87"
        date: "yesterday", "today", or "YYYY-MM-DD" (default: today)
    """
    try:
        ref_date = _resolve_date(date)
    except ValueError:
        return f"Error: invalid date '{date}'"

    parts = [e.strip() for e in entries.split(",") if e.strip()]
    if not parts:
        return "Error: no entries provided"

    all_results = []
    success = 0
    fail = 0

    for part in parts:
        # Normalize HHMM to HH:MM
        normalized = re.sub(r"^(\d{2})(\d{2})-(\d{2})(\d{2})", r"\1:\2-\3:\4", part)
        m = _BATCH_ENTRY_RE.match(normalized)
        if not m:
            all_results.append(f"SKIP: could not parse '{part}'")
            fail += 1
            continue

        start_str, end_str, desc, proj_code = m.groups()
        desc = desc.strip()

        try:
            start_dt = _parse_time(start_str, ref_date)
            end_dt = _parse_time(end_str, ref_date)

            if end_dt <= start_dt:
                all_results.append(f"SKIP: {desc} — end before start")
                fail += 1
                continue

            project_id = _resolve_project(proj_code or "")
            if proj_code and not project_id:
                all_results.append(f"SKIP: {desc} — unknown project '{proj_code}'")
                fail += 1
                continue

            results = _create_single_entry(desc, start_dt, end_dt, project_id)
            all_results.extend(results)
            success += 1
        except Exception as e:
            all_results.append(f"FAIL: {desc} — {e}")
            fail += 1

    summary = f"\nBatch complete: {success} created, {fail} failed"
    all_results.append(summary)
    return "\n".join(all_results)


@mcp.tool()
def toggl_start(
    description: str,
    project: str = "",
    tags: list[str] = None,
) -> str:
    """Start a running timer.

    Args:
        description: What you're doing
        project: Project code (i9, hcb, g245, etc.) or numeric ID
        tags: Optional list of tag strings
    """
    try:
        project_id = _resolve_project(project)
        if project and not project_id:
            return f"Error: unknown project code '{project}'. Valid codes: {', '.join(sorted(PROJECT_MAP.keys()))}"

        entry = toggl_api.start_timer(description, project_id, tags)
        proj_name = PROJECT_NAMES.get(project_id, project) if project_id else ""
        proj_str = f" @{proj_name}" if proj_name else ""
        return f"Started: {description}{proj_str} [id:{entry['id']}]"

    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def toggl_stop() -> str:
    """Stop the currently running timer."""
    try:
        current = toggl_api.get_current()
        if not current:
            return "No timer is currently running."

        entry = toggl_api.stop_timer(current["id"])
        return f"Stopped: {_format_entry(entry)}"

    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def toggl_current() -> str:
    """Get the currently running timer, if any."""
    try:
        current = toggl_api.get_current()
        if not current:
            return "No timer is currently running."
        return f"Running: {_format_entry(current)}"

    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def toggl_today() -> str:
    """List all time entries for today."""
    try:
        today = datetime.datetime.now(TZ).date()
        raw_entries = toggl_api.get_entries(
            start_date=(today - datetime.timedelta(days=1)).isoformat(),
            end_date=(today + datetime.timedelta(days=2)).isoformat(),
        )
        entries = _filter_entries_by_local_date(raw_entries or [], today)
        if not entries:
            return "No entries today."

        # Sort by start time
        entries.sort(key=lambda e: e.get("start", ""))
        lines = [f"# Today ({today.isoformat()})"]
        total_sec = 0
        for e in entries:
            lines.append(f"  {_format_entry(e)}")
            dur = e.get("duration", 0)
            if dur > 0:
                total_sec += dur

        lines.append(f"\nTotal: {_format_duration(total_sec)}")
        return "\n".join(lines)

    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def toggl_date(date: str) -> str:
    """Get all time entries for a specific date.

    Args:
        date: Date as "YYYY-MM-DD"
    """
    try:
        target_date = datetime.date.fromisoformat(date)
        raw_entries = toggl_api.get_entries(
            start_date=(target_date - datetime.timedelta(days=1)).isoformat(),
            end_date=(target_date + datetime.timedelta(days=2)).isoformat(),
        )
        entries = _filter_entries_by_local_date(raw_entries or [], target_date)
        if not entries:
            return f"No entries on {date}."

        # Sort by start time
        entries.sort(key=lambda e: e.get("start", ""))
        lines = [f"# {date}"]
        total_sec = 0
        project_totals = {}  # {project_code: minutes}

        for e in entries:
            lines.append(f"  {_format_entry(e)}")
            dur = e.get("duration", 0)
            if dur > 0:
                total_sec += dur
                # Track by project
                proj_id = e.get("project_id")
                proj_code = PROJECT_NAMES.get(proj_id, "no project") if proj_id else "no project"
                project_totals[proj_code] = project_totals.get(proj_code, 0) + (dur // 60)

        lines.append(f"\nTotal: {_format_duration(total_sec)}")
        lines.append(f"\nProject breakdown (minutes):")
        for proj, mins in sorted(project_totals.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"  {proj}: {mins}m")

        return "\n".join(lines)

    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def toggl_delete(entry_id: int) -> str:
    """Delete a time entry by its ID.

    Args:
        entry_id: The Toggl time entry ID
    """
    try:
        toggl_api.delete_entry(entry_id)
        return f"Deleted entry {entry_id}."
    except Exception as e:
        return f"Error: {e}"
