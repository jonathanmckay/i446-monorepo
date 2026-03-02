"""
Sync database to Google Sheets and back.

This module handles two-way sync:
1. Push application data from database → Google Sheets
2. Pull notes/custom fields from Google Sheets → database
"""
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

from .models import Application, ApplicationNote, CustomField, SyncLog, init_database

load_dotenv()

# Google Sheets API scopes
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

# Sheet column headers
HEADERS = [
    'Application ID',
    'Status',
    'Applicant Name',
    'Property Address',
    'Unit',
    'Rent',
    'Application Date',
    'Desired Move-In',
    'Days Pending',
    'Email',
    'Phone',
    'Screening Status',
    'Notes',  # User-editable
    'Follow-Up Date',  # User-editable custom field
    'Priority',  # User-editable custom field
    'Assigned To',  # User-editable custom field
    'Last Updated'
]

# Columns that users can edit (will be synced back to database)
EDITABLE_COLUMNS = ['Notes', 'Follow-Up Date', 'Priority', 'Assigned To']


def get_credentials() -> Credentials:
    """
    Get Google API credentials from service account file.
    
    Set GOOGLE_SERVICE_ACCOUNT_FILE env var to the path of your credentials JSON.
    """
    creds_file = os.getenv('GOOGLE_SERVICE_ACCOUNT_FILE')
    
    if not creds_file:
        # Try default location
        default_path = Path(__file__).parent.parent / 'credentials' / 'service_account.json'
        if default_path.exists():
            creds_file = str(default_path)
        else:
            raise ValueError(
                "Missing Google credentials. Either:\n"
                "1. Set GOOGLE_SERVICE_ACCOUNT_FILE env var, or\n"
                "2. Place service_account.json in ./credentials/\n\n"
                "To create credentials:\n"
                "1. Go to Google Cloud Console\n"
                "2. Create a Service Account\n"
                "3. Download the JSON key file\n"
                "4. Share your Google Sheet with the service account email"
            )
    
    return Credentials.from_service_account_file(creds_file, scopes=SCOPES)


def get_client() -> gspread.Client:
    """Get authenticated Google Sheets client."""
    creds = get_credentials()
    return gspread.authorize(creds)


def format_date(dt: Optional[datetime]) -> str:
    """Format datetime for display in sheet."""
    if dt is None:
        return ''
    return dt.strftime('%Y-%m-%d')


def calculate_days_pending(app_date: Optional[datetime]) -> str:
    """Calculate days since application was submitted."""
    if app_date is None:
        return ''
    delta = datetime.now() - app_date
    return str(delta.days)


def get_latest_note(notes: List[ApplicationNote]) -> str:
    """Get the most recent note for an application."""
    if not notes:
        return ''
    sorted_notes = sorted(notes, key=lambda n: n.created_at or datetime.min, reverse=True)
    return sorted_notes[0].note or ''


def get_custom_field(custom_fields: List[CustomField], field_name: str) -> str:
    """Get value of a custom field."""
    for field in custom_fields:
        if field.field_name == field_name:
            return field.field_value or ''
    return ''


def sync_to_sheets(spreadsheet_id: Optional[str] = None, sheet_name: str = 'Leasing Pipeline') -> Dict[str, Any]:
    """
    Push application data from database to Google Sheets.
    
    Args:
        spreadsheet_id: Google Sheets spreadsheet ID. If None, uses GOOGLE_SHEET_ID env var.
        sheet_name: Name of the worksheet to update.
    
    Returns:
        Dict with sync results
    """
    if spreadsheet_id is None:
        spreadsheet_id = os.getenv('GOOGLE_SHEET_ID')
    
    if not spreadsheet_id:
        raise ValueError(
            "Missing Google Sheet ID. Either:\n"
            "1. Pass spreadsheet_id parameter, or\n"
            "2. Set GOOGLE_SHEET_ID env var\n\n"
            "The ID is in the sheet URL: https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit"
        )
    
    Session = init_database()
    session = Session()
    
    sync_log = SyncLog(
        sync_type='sheets_export',
        sync_source=f'sheets:{spreadsheet_id}',
        started_at=datetime.utcnow()
    )
    
    try:
        # Get all in-progress applications
        applications = session.query(Application).filter(
            Application.status.notin_(['Signed', 'Denied', 'Cancelled', 'Completed'])
        ).order_by(Application.application_date.desc()).all()
        
        # Build rows
        rows = [HEADERS]  # Header row
        
        for app in applications:
            row = [
                app.application_id or '',
                app.status or '',
                app.applicant_name or '',
                app.property_address or '',
                app.unit or '',
                f"${app.rent_amount:,.0f}" if app.rent_amount else '',
                format_date(app.application_date),
                format_date(app.desired_move_in),
                calculate_days_pending(app.application_date),
                app.applicant_email or '',
                app.applicant_phone or '',
                app.screening_status or '',
                get_latest_note(app.notes),
                get_custom_field(app.custom_fields, 'follow_up_date'),
                get_custom_field(app.custom_fields, 'priority'),
                get_custom_field(app.custom_fields, 'assigned_to'),
                format_date(app.updated_at),
            ]
            rows.append(row)
        
        # Connect to Google Sheets
        client = get_client()
        spreadsheet = client.open_by_key(spreadsheet_id)
        
        # Get or create worksheet
        try:
            worksheet = spreadsheet.worksheet(sheet_name)
        except gspread.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=len(HEADERS))
        
        # Clear and update
        worksheet.clear()
        worksheet.update(range_name='A1', values=rows)
        
        # Format header row
        worksheet.format('A1:Q1', {
            'backgroundColor': {'red': 0.2, 'green': 0.2, 'blue': 0.3},
            'textFormat': {'bold': True, 'foregroundColor': {'red': 1, 'green': 1, 'blue': 1}},
            'horizontalAlignment': 'CENTER'
        })
        
        # Freeze header row
        worksheet.freeze(rows=1)
        
        # Color editable columns
        editable_col_indices = [HEADERS.index(col) + 1 for col in EDITABLE_COLUMNS if col in HEADERS]
        for col_idx in editable_col_indices:
            col_letter = chr(64 + col_idx)  # A=65, so 1->A, 2->B, etc.
            worksheet.format(f'{col_letter}2:{col_letter}1000', {
                'backgroundColor': {'red': 1, 'green': 0.98, 'blue': 0.9}
            })
        
        sync_log.records_processed = len(applications)
        sync_log.success = True
        sync_log.completed_at = datetime.utcnow()
        session.add(sync_log)
        session.commit()
        
        return {
            'success': True,
            'rows_written': len(rows),
            'spreadsheet_url': f'https://docs.google.com/spreadsheets/d/{spreadsheet_id}'
        }
    
    except Exception as e:
        sync_log.success = False
        sync_log.error_message = str(e)
        sync_log.completed_at = datetime.utcnow()
        session.add(sync_log)
        session.commit()
        raise
    
    finally:
        session.close()


def sync_from_sheets(spreadsheet_id: Optional[str] = None, sheet_name: str = 'Leasing Pipeline') -> Dict[str, Any]:
    """
    Pull notes and custom fields from Google Sheets back to database.
    
    This preserves user edits made in the sheet.
    
    Args:
        spreadsheet_id: Google Sheets spreadsheet ID
        sheet_name: Name of the worksheet
    
    Returns:
        Dict with sync results
    """
    if spreadsheet_id is None:
        spreadsheet_id = os.getenv('GOOGLE_SHEET_ID')
    
    if not spreadsheet_id:
        raise ValueError("Missing Google Sheet ID")
    
    Session = init_database()
    session = Session()
    
    sync_log = SyncLog(
        sync_type='sheets_import',
        sync_source=f'sheets:{spreadsheet_id}',
        started_at=datetime.utcnow()
    )
    
    try:
        client = get_client()
        spreadsheet = client.open_by_key(spreadsheet_id)
        worksheet = spreadsheet.worksheet(sheet_name)
        
        # Get all values
        values = worksheet.get_all_values()
        
        if len(values) < 2:
            return {'success': True, 'notes_updated': 0}
        
        headers = values[0]
        
        # Find column indices
        try:
            app_id_idx = headers.index('Application ID')
            notes_idx = headers.index('Notes')
            followup_idx = headers.index('Follow-Up Date')
            priority_idx = headers.index('Priority')
            assigned_idx = headers.index('Assigned To')
        except ValueError as e:
            raise ValueError(f"Missing required column: {e}")
        
        notes_updated = 0
        fields_updated = 0
        
        for row in values[1:]:
            if len(row) <= app_id_idx:
                continue
            
            app_id = row[app_id_idx].strip()
            if not app_id:
                continue
            
            # Get application
            app = session.query(Application).filter_by(application_id=app_id).first()
            if not app:
                continue
            
            # Update notes
            notes_value = row[notes_idx] if len(row) > notes_idx else ''
            if notes_value.strip():
                existing_note = session.query(ApplicationNote).filter_by(
                    application_id=app_id
                ).order_by(ApplicationNote.created_at.desc()).first()
                
                if existing_note:
                    if existing_note.note != notes_value:
                        existing_note.note = notes_value
                        existing_note.updated_at = datetime.utcnow()
                        notes_updated += 1
                else:
                    new_note = ApplicationNote(
                        application_id=app_id,
                        note=notes_value,
                        note_type='general'
                    )
                    session.add(new_note)
                    notes_updated += 1
            
            # Update custom fields
            custom_fields_data = [
                ('follow_up_date', row[followup_idx] if len(row) > followup_idx else ''),
                ('priority', row[priority_idx] if len(row) > priority_idx else ''),
                ('assigned_to', row[assigned_idx] if len(row) > assigned_idx else ''),
            ]
            
            for field_name, field_value in custom_fields_data:
                if field_value.strip():
                    existing_field = session.query(CustomField).filter_by(
                        application_id=app_id,
                        field_name=field_name
                    ).first()
                    
                    if existing_field:
                        if existing_field.field_value != field_value:
                            existing_field.field_value = field_value
                            existing_field.updated_at = datetime.utcnow()
                            fields_updated += 1
                    else:
                        new_field = CustomField(
                            application_id=app_id,
                            field_name=field_name,
                            field_value=field_value
                        )
                        session.add(new_field)
                        fields_updated += 1
        
        session.commit()
        
        sync_log.records_processed = len(values) - 1
        sync_log.records_updated = notes_updated + fields_updated
        sync_log.success = True
        sync_log.completed_at = datetime.utcnow()
        session.add(sync_log)
        session.commit()
        
        return {
            'success': True,
            'notes_updated': notes_updated,
            'fields_updated': fields_updated
        }
    
    except Exception as e:
        sync_log.success = False
        sync_log.error_message = str(e)
        sync_log.completed_at = datetime.utcnow()
        session.add(sync_log)
        session.commit()
        raise
    
    finally:
        session.close()


def full_sync(spreadsheet_id: Optional[str] = None, sheet_name: str = 'Leasing Pipeline') -> Dict[str, Any]:
    """
    Perform a full two-way sync:
    1. Pull notes/custom fields from sheet (preserve user edits)
    2. Push updated application data to sheet
    
    This ensures user edits are never lost.
    """
    # First, pull any edits from the sheet
    try:
        pull_result = sync_from_sheets(spreadsheet_id, sheet_name)
    except Exception:
        pull_result = {'success': False, 'notes_updated': 0}
    
    # Then push updated data
    push_result = sync_to_sheets(spreadsheet_id, sheet_name)
    
    return {
        'pull': pull_result,
        'push': push_result
    }

