from mcp.server.fastmcp import FastMCP
from .excel_handler import NeonHandler
import os

# Initialize FastMCP server
mcp = FastMCP("Neon Excel Agent")

# Global handler instance
# In a real app we might want to reload this or handle concurrency better
base_path = os.path.abspath("../neon_agent_copy.xlsx")
handler = NeonHandler(base_path)

@mcp.tool()
def get_status():
    """Get the current status of tasks and points."""
    tasks = handler.get_tasks()
    return f"Found {len(tasks)} tasks definitions in meta sheet. (Date logic pending)"

@mcp.tool()
def list_available_tasks():
    """List all tasks defined in the meta sheet."""
    tasks = handler.get_tasks()
    # Format as list
    lines = ["Available Tasks:"]
    for t in tasks[:20]: # Show first 20
        lines.append(f"- [{t.get('priority')}] {t.get('name')} ({t.get('category')})")
    return "\n".join(lines)

@mcp.tool()
def log_task(task_name: str, value: str):
    """Log a task completion (Placeholder)."""
    return "Log task functionality implementation pending date row resolution."
