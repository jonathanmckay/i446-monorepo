import time
import re
import os
from neon_server.excel_handler import NeonHandler
from datetime import datetime

DASHBOARD_PATH = "dashboard.md"
EXCEL_PATH = "neon_agent_copy.xlsx"

def update_status_section(content, status_text):
    # Find Status section
    pattern = r"(## Status for Today\n)(.*?)(\n## Commands)"
    new_section = f"## Status for Today\n*Last Updated: {datetime.now()}*\n\n{status_text}\n\n## Commands"
    return re.sub(pattern, new_section, content, flags=re.DOTALL)

def process_commands(content, handler):
    lines = content.split('\n')
    new_lines = []
    
    for line in lines:
        if "- [ ]" in line and "<!-- command:" in line:
            # Parse command
            try:
                cmd_match = re.search(r"command: (.*?)( -->|$)", line)
                if cmd_match:
                    cmd_str = cmd_match.group(1)
                    parts = [p.strip() for p in cmd_str.split(',')]
                    action = parts[0]
                    
                    result_msg = ""
                    if action == "update":
                        result_msg = "Updated status."
                    elif action == "log":
                        # col: X, val: Y
                        col_param = next((p for p in parts if p.startswith('col:')), None)
                        val_param = next((p for p in parts if p.startswith('val:')), None)
                        
                        if col_param and val_param:
                            col = col_param.split(':', 1)[1].strip()
                            val = val_param.split(':', 1)[1].strip()
                            
                            row = handler.find_today_row()
                            if row:
                                col_idx = handler.get_column_index(col)
                                if col_idx:
                                    handler.write_cell(row, col_idx, val)
                                    result_msg = f"Logged {val} to {col}"
                                else:
                                    result_msg = f"Error: Column {col} not found"
                            else:
                                result_msg = "Error: Today row not found"
                        else:
                            result_msg = "Error: Invalid log params"
                    
                    # Mark as done and append result
                    line = line.replace("- [ ]", "- [x]") + f"   Request processed: {result_msg}"
            except Exception as e:
                 line = line.replace("- [ ]", "- [x]") + f"   Error: {e}"
        
        new_lines.append(line)
    
    return "\n".join(new_lines)

def main():
    print("Agent Loop Started. Monitoring dashboard.md...")
    base_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(base_dir, DASHBOARD_PATH)
    excel_full_path = os.path.join(base_dir, EXCEL_PATH)
    
    handler = NeonHandler(excel_full_path)
    
    # 1. Initial Update
    print("Performing initial status update...")
    with open(file_path, 'r') as f:
        content = f.read()
    
    status = handler.get_day_status()
    content = update_status_section(content, status)
    
    with open(file_path, 'w') as f:
        f.write(content)
        
    print("Dashboard updated. Checking for commands...")
    
    # Simple one-shot run. In a real scenario this would loop or watch.
    # We will process existing commands now.
    
    with open(file_path, 'r') as f:
        content = f.read()
        
    new_content = process_commands(content, handler)
    
    if new_content != content:
        print("Commands processed. updating file.")
        with open(file_path, 'w') as f:
            f.write(new_content)
    else:
        print("No new commands.")

if __name__ == "__main__":
    main()
