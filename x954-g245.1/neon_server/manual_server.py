import sys
import json
import logging
import os
from excel_handler import NeonHandler

# Configure logging to stderr so it doesn't interfere with stdout JSON-RPC
logging.basicConfig(level=logging.INFO, stream=sys.stderr, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("manual_server")

try:
    base_path = os.path.abspath("neon_agent_copy.xlsx")
    handler = NeonHandler(base_path)
    logger.info(f"Loaded Excel handler for {base_path}")
except Exception as e:
    logger.error(f"Failed to init handler: {e}")
    sys.exit(1)

def handle_request(request):
    try:
        method = request.get("method")
        params = request.get("params", {})
        id = request.get("id")

        logger.info(f"Received request: {method}")

        result = None
        error = None

        # Standard MCP-like tools
        if method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": "get_today_status",
                        "description": "Get the status and notes for the current day from the Excel sheet.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {}
                        }
                    },
                    {
                        "name": "update_cell",
                        "description": "Update a cell for today's row given a column header or index.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "column_header": {"type": "string", "description": "Name of the column header (e.g. 'Goal')"},
                                "column_index": {"type": "integer", "description": "1-based column index (optional, overrides header)"},
                                "value": {"type": "string", "description": "Value to write"}
                            },
                            "required": ["value"]
                        }
                    }
                ]
            }
        elif method == "tools/call":
            name = params.get("name")
            args = params.get("arguments", {})
            
            if name == "get_today_status":
                content = handler.get_day_status()
                result = {"content": [{"type": "text", "text": str(content)}]}
            elif name == "update_cell":
                col_header = args.get("column_header")
                col_idx = args.get("column_index")
                val = args.get("value")
                
                row = handler.find_today_row()
                if not row:
                     raise Exception("Today's row not found")
                
                target_col = col_idx
                if not target_col and col_header:
                    target_col = handler.get_column_index(col_header)
                
                if target_col:
                    msg = handler.write_cell(row, target_col, val)
                    result = {"content": [{"type": "text", "text": msg}]}
                else:
                    error = {"code": -32602, "message": "Could not determine column"}
            else:
                error = {"code": -32601, "message": "Method not found"}
        
        else:
             # Just ignore other messages (notifications etc) or return standard error
             pass

        response = {
            "jsonrpc": "2.0",
            "id": id,
        }
        if error:
            response["error"] = error
        elif result is not None:
            response["result"] = result
        
        print(json.dumps(response), flush=True)

    except Exception as e:
        logger.error(f"Error handling request: {e}")
        err_resp = {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "error": {"code": -32000, "message": str(e)}
        }
        print(json.dumps(err_resp), flush=True)

def main():
    logger.info("Server started. Listening on stdin...")
    for line in sys.stdin:
        if not line:
            break
        try:
            req = json.loads(line)
            handle_request(req)
        except json.JSONDecodeError:
            logger.error("Invalid JSON")

if __name__ == "__main__":
    main()
