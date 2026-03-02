# x954-g245.1 (Neon Agent)

A Python-based CLI and agentic interface for interacting with the Neon Excel tracking system.

## Setup

1. Create a virtual environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Place your Excel file:
   - This tool expects a file named `neon_agent_copy.xlsx` in the root directory.
   - *Note*: This file is gitignored to protect your data.

## Usage (CLI)

Use the `neon.py` script to interact with your data.

### Check today's status:
```bash
python neon.py status
```

### Log data:
```bash
python neon.py log "Title of the Day" "Hello World"
python neon.py log "Points" 10
```

### Check Points:
```bash
python neon.py points --date yesterday
```

## Structure
- `neon.py`: Main CLI entry point.
- `neon_server/`: Contains core logic (`excel_handler.py`) and date cache.
