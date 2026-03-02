import sys
import os

# Add current directory to path so we can import neon_server
sys.path.append(os.getcwd())

from neon_server.server import mcp

if __name__ == "__main__":
    mcp.run()
