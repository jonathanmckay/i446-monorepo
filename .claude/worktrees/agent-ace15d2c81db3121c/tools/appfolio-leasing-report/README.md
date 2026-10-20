# AppFolio Leasing Report → Google Sheets

Automated daily sync of in-progress lease applications from AppFolio to Google Sheets, with your own database for notes and custom fields.

```
AppFolio → Python Script (daily) → SQLite Database → Google Sheets
                                        ↑
                              Your notes stored here
```

## Why This Exists

- **Your notes persist**: Add notes in Google Sheets, they won't be overwritten
- **Custom fields**: Track follow-up dates, priority, assigned agent
- **Daily automation**: Set it and forget it
- **Your data**: Everything stored locally in SQLite

---

## Quick Start

### 1. Install Dependencies

```bash
cd "/Users/jonathanmckay/m5x2 - 01 - leasing report"
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Set Up Google Sheets API

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a new project (or use existing)
3. Enable **Google Sheets API** and **Google Drive API**
4. Create a **Service Account** (APIs & Services → Credentials → Create Credentials)
5. Download the JSON key file
6. Save it as `./credentials/service_account.json`

### 3. Create Your Google Sheet

1. Create a new Google Sheet
2. Copy the Sheet ID from the URL:
   ```
   https://docs.google.com/spreadsheets/d/[THIS_IS_YOUR_SHEET_ID]/edit
   ```
3. **Important**: Share the sheet with your service account email
   - Find the email in your service account JSON (`client_email` field)
   - Share the sheet with that email (Editor access)

### 4. Configure Environment

```bash
cp .env.example .env
# Edit .env and add your GOOGLE_SHEET_ID
```

### 5. Initialize & Import

```bash
# Initialize database
python -m src.cli init

# Export CSV from AppFolio:
#   → Login to AppFolio
#   → Leasing → Metrics
#   → Filter: Status = In Progress, Pending, Approved
#   → Actions → Export as CSV

# Import the CSV
python -m src.cli import-csv path/to/your/export.csv

# Push to Google Sheets
python -m src.cli push
```

---

## Daily Workflow

### Option A: Manual Updates

```bash
# 1. Export fresh CSV from AppFolio
# 2. Import and sync
python -m src.cli import-csv latest-export.csv
python -m src.cli sync  # preserves your notes!
```

### Option B: Automated Daily Sync

**Using the built-in scheduler:**
```bash
python -m src.scheduler
# Runs sync daily at 6am (configurable in .env)
```

**Using cron (macOS/Linux):**
```bash
# Edit crontab
crontab -e

# Add this line (runs at 6am daily)
0 6 * * * cd "/Users/jonathanmckay/m5x2 - 01 - leasing report" && ./venv/bin/python -m src.scheduler --once
```

**Auto-import CSVs:**
Drop AppFolio exports into the `./imports/` folder - the scheduler will process them automatically.

---

## CLI Commands

```bash
# Import data
python -m src.cli import-csv FILE.csv    # Import from CSV
python -m src.cli import-api             # Import from Skywalk API

# Sync with Google Sheets
python -m src.cli push                   # Push data to Sheets
python -m src.cli pull                   # Pull notes from Sheets
python -m src.cli sync                   # Two-way sync (recommended)

# Manage data
python -m src.cli list-apps              # Show applications
python -m src.cli add-note APP_ID "Note" # Add a note
python -m src.cli status                 # Show sync status

# Setup
python -m src.cli init                   # Initialize & show setup guide
```

---

## Google Sheets Layout

The sheet will have these columns:

| Column | Editable? | Description |
|--------|-----------|-------------|
| Application ID | ❌ | Unique ID from AppFolio |
| Status | ❌ | Application status |
| Applicant Name | ❌ | Primary applicant |
| Property Address | ❌ | Property applying for |
| Unit | ❌ | Unit number |
| Rent | ❌ | Monthly rent |
| Application Date | ❌ | When they applied |
| Desired Move-In | ❌ | Requested move-in date |
| Days Pending | ❌ | Days since application |
| Email | ❌ | Contact email |
| Phone | ❌ | Contact phone |
| Screening Status | ❌ | Background check status |
| **Notes** | ✅ | Your notes (preserved!) |
| **Follow-Up Date** | ✅ | When to follow up |
| **Priority** | ✅ | High/Medium/Low |
| **Assigned To** | ✅ | Team member handling |
| Last Updated | ❌ | Last data refresh |

Yellow-highlighted columns are editable and will be preserved across syncs.

---

## Skywalk API (Optional)

For fully automated data pulls (no manual CSV exports), you can use [Skywalk API](https://skywalkapi.com):

1. Sign up at skywalkapi.com
2. Connect your AppFolio account
3. Add credentials to `.env`:
   ```
   SKYWALK_API_KEY=your_api_key
   SKYWALK_COMPANY_ID=your_company_id
   ```
4. Run: `python -m src.cli import-api`

---

## File Structure

```
.
├── credentials/
│   └── service_account.json  # Google API credentials
├── data/
│   └── leasing.db            # SQLite database
├── imports/
│   └── (drop CSVs here)      # Auto-processed by scheduler
├── logs/
│   └── sync.log              # Sync history
├── src/
│   ├── models.py             # Database models
│   ├── appfolio_import.py    # CSV/API import
│   ├── sheets_sync.py        # Google Sheets sync
│   ├── cli.py                # Command-line interface
│   └── scheduler.py          # Daily automation
├── .env                      # Your configuration
├── .env.example              # Template
└── requirements.txt          # Python dependencies
```

---

## Troubleshooting

### "No module named 'src'"
Make sure you're running from the project root:
```bash
cd "/Users/jonathanmckay/m5x2 - 01 - leasing report"
python -m src.cli status
```

### "Missing Google credentials"
1. Check that `./credentials/service_account.json` exists
2. Or set `GOOGLE_SERVICE_ACCOUNT_FILE` in `.env`

### "Permission denied" on Google Sheet
Share your Google Sheet with the service account email (found in your JSON credentials as `client_email`).

### Notes getting overwritten
Always use `sync` instead of just `push` - it pulls your notes first.

---

## Adding Custom Fields

To add more editable fields, edit `src/sheets_sync.py`:

1. Add to `HEADERS` list
2. Add to `EDITABLE_COLUMNS` list
3. Update `sync_from_sheets()` to save the field
4. Run `sync` to update the sheet
