from mcp.server.fastmcp import FastMCP

from .config import validate_path
from .excel_handler import NeonHandler

mcp = FastMCP("Neon Excel Agent")

SHEET_INFO = {
    "0分": "Daily points/scoring — 9 time blocks per day, category point accruals",
    "0₦": "Daily habits tracking — 60+ columns of habit completions",
    "0s": "Daily status summary — happiness, pain, sleep, totals",
    "hcbi": "Health & body tracking",
    "m5x2": "McKay Capital real estate tracking",
    "i9": "Microsoft work tracking",
    "1₦+": "Weekly ritual time allocations (minutes per habit)",
    "1₹+1s": "Weekly points and scorecard",
    "1g": "Weekly goals — goals grouped by domain with points and daily progress",
    "meta": "System metadata and configuration",
}


def _handler():
    path = validate_path()
    return NeonHandler(path)


def _format_goals(data: dict) -> str:
    """Format weekly goals data as readable text."""
    lines = []
    lines.append(f"# Weekly Goals: {data['week_label']}")
    if data["tldr"]:
        lines.append(f"tl;dr: {data['tldr'][:200]}")
    lines.append("")

    for cat in data["categories"]:
        lines.append(f"## {cat['code']}")
        if not cat["goals"]:
            lines.append("  (no goals)")
        for i, g in enumerate(cat["goals"]):
            pct = g["pct_done"]
            if isinstance(pct, (int, float)):
                pct_str = f"{pct:.0%}" if pct <= 1 else f"{pct}%"
            else:
                pct_str = str(pct) if pct else "0%"
            bonus = g["bonus"] if g["bonus"] else 0
            mins = g["minutes"] if g["minutes"] else ""
            mins_str = f" ({mins}m)" if mins else ""
            daily_parts = []
            for d, v in g["daily"].items():
                if v is not None:
                    daily_parts.append(f"{d}={'x' if v else '.'}")
            daily = " ".join(daily_parts)
            lines.append(
                f"  [{i}] {g['text']}{mins_str} — {bonus}pts, {pct_str}"
                + (f" | {daily}" if daily else "")
            )
        lines.append("")

    if data["totals"]:
        t = data["totals"]
        pct = t.get("pct_done")
        if isinstance(pct, (int, float)) and pct <= 1:
            pct_str = f"{pct:.0%}"
        else:
            pct_str = str(pct)
        lines.append(
            f"**Totals:** {t.get('bonus', 0)} bonus pts, "
            f"{pct_str} done, score={t.get('score', 0)}"
        )

    return "\n".join(lines)


@mcp.tool()
def neon_get_weekly_goals() -> str:
    """Get all weekly goals from the 1g sheet, grouped by category with points and completion status."""
    try:
        h = _handler()
        data = h.get_weekly_goals()
        return _format_goals(data)
    except PermissionError:
        return "Error: Excel file is locked (probably open in Excel). Close it and retry."
    except Exception as e:
        return f"Error reading weekly goals: {e}"


@mcp.tool()
def neon_update_weekly_goal(
    category: str,
    goal_index: int,
    field: str,
    value: str,
) -> str:
    """Update a specific field of a weekly goal.

    Args:
        category: Domain code (e.g. "g245", "i8", "hcb")
        goal_index: 0-based index within the category (shown in brackets by neon_get_weekly_goals)
        field: Field to update — "text", "minutes", "bonus", "pct_done", or "daily_T"/"daily_W"/"daily_Th"/"daily_F"/"daily_Sa"/"daily_Su"/"daily_M"
        value: New value (strings auto-converted to numbers where appropriate; "50%" works for pct_done)
    """
    try:
        h = _handler()
        return h.update_weekly_goal(category, goal_index, field, value)
    except PermissionError:
        return "Error: Excel file is locked. Close it and retry."
    except Exception as e:
        return f"Error updating goal: {e}"


@mcp.tool()
def neon_add_weekly_goal(
    category: str,
    goal_text: str,
    minutes: int = 0,
    bonus: int = 0,
) -> str:
    """Add a new goal to a category in the 1g weekly goals sheet.

    Args:
        category: Domain code (e.g. "g245", "i8", "hcb")
        goal_text: The goal description
        minutes: Estimated time in minutes (optional)
        bonus: Focus bonus points (optional)
    """
    try:
        h = _handler()
        return h.add_weekly_goal(category, goal_text, minutes, bonus)
    except PermissionError:
        return "Error: Excel file is locked. Close it and retry."
    except Exception as e:
        return f"Error adding goal: {e}"


@mcp.tool()
def neon_set_weekly_summary(tldr: str) -> str:
    """Update the tl;dr summary text at the top of the weekly goals sheet.

    Args:
        tldr: The summary text for this week's goals
    """
    try:
        h = _handler()
        return h.set_weekly_tldr(tldr)
    except PermissionError:
        return "Error: Excel file is locked. Close it and retry."
    except Exception as e:
        return f"Error setting summary: {e}"


@mcp.tool()
def neon_list_sheets() -> str:
    """List all sheets in the Neon workbook with descriptions."""
    try:
        h = _handler()
        sheets = h.list_sheets()
        lines = ["# Neon Workbook Sheets", ""]
        for name in sheets:
            desc = SHEET_INFO.get(name, "(no description)")
            lines.append(f"- **{name}**: {desc}")
        return "\n".join(lines)
    except PermissionError:
        return "Error: Excel file is locked. Close it and retry."
    except Exception as e:
        return f"Error listing sheets: {e}"
