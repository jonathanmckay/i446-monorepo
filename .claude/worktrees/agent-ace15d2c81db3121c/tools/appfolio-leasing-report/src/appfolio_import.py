"""
Import lease application data from AppFolio.

Supports:
1. CSV file import (manual export from AppFolio)
2. Skywalk API (third-party AppFolio API)
3. Direct AppFolio API (if you have access)
"""
import csv
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Any

import pandas as pd
import requests
from dotenv import load_dotenv

from .models import Application, SyncLog, init_database

load_dotenv()


# Column name mappings from AppFolio CSV exports
# Adjust these based on your actual AppFolio export format
COLUMN_MAPPINGS = {
    # AppFolio column name -> our database field
    'Application ID': 'application_id',
    'Application Id': 'application_id',
    'ID': 'application_id',
    
    'Applicant Name': 'applicant_name',
    'Applicant': 'applicant_name',
    'Name': 'applicant_name',
    'Primary Applicant': 'applicant_name',
    
    'Email': 'applicant_email',
    'Applicant Email': 'applicant_email',
    
    'Phone': 'applicant_phone',
    'Applicant Phone': 'applicant_phone',
    'Phone Number': 'applicant_phone',
    
    'Property': 'property_name',
    'Property Name': 'property_name',
    
    'Property Address': 'property_address',
    'Address': 'property_address',
    
    'Unit': 'unit',
    'Unit Number': 'unit',
    'Unit #': 'unit',
    
    'Status': 'status',
    'Application Status': 'status',
    
    'Application Date': 'application_date',
    'Date Applied': 'application_date',
    'Created': 'application_date',
    'Created Date': 'application_date',
    
    'Move-In Date': 'desired_move_in',
    'Desired Move-In': 'desired_move_in',
    'Move In Date': 'desired_move_in',
    
    'Lease Start': 'lease_start_date',
    'Lease Start Date': 'lease_start_date',
    
    'Lease End': 'lease_end_date',
    'Lease End Date': 'lease_end_date',
    
    'Rent': 'rent_amount',
    'Rent Amount': 'rent_amount',
    'Monthly Rent': 'rent_amount',
    
    'Deposit': 'deposit_amount',
    'Security Deposit': 'deposit_amount',
    
    'Screening Status': 'screening_status',
    'Background Check': 'screening_status',
    
    'Credit Score': 'credit_score',
}


def parse_date(value: Any) -> Optional[datetime]:
    """Parse various date formats."""
    if pd.isna(value) or value is None or value == '':
        return None
    
    if isinstance(value, datetime):
        return value
    
    date_formats = [
        '%Y-%m-%d',
        '%m/%d/%Y',
        '%m/%d/%y',
        '%Y-%m-%d %H:%M:%S',
        '%m/%d/%Y %H:%M:%S',
        '%B %d, %Y',
    ]
    
    for fmt in date_formats:
        try:
            return datetime.strptime(str(value).strip(), fmt)
        except ValueError:
            continue
    
    return None


def parse_number(value: Any) -> Optional[float]:
    """Parse currency/number values."""
    if pd.isna(value) or value is None or value == '':
        return None
    
    # Remove currency symbols and commas
    cleaned = str(value).replace('$', '').replace(',', '').strip()
    
    try:
        return float(cleaned)
    except ValueError:
        return None


def map_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Map AppFolio column names to our database fields."""
    rename_map = {}
    
    for col in df.columns:
        col_clean = col.strip()
        if col_clean in COLUMN_MAPPINGS:
            rename_map[col] = COLUMN_MAPPINGS[col_clean]
    
    return df.rename(columns=rename_map)


def import_from_csv(csv_path: Path, status_filter: Optional[List[str]] = None) -> Dict[str, int]:
    """
    Import lease applications from a CSV file exported from AppFolio.
    
    Args:
        csv_path: Path to the CSV file
        status_filter: Optional list of statuses to include. 
                       If None, imports all non-signed/non-denied applications.
    
    Returns:
        Dict with counts: {'processed': N, 'added': N, 'updated': N}
    """
    Session = init_database()
    session = Session()
    
    sync_log = SyncLog(
        sync_type='appfolio_import',
        sync_source=str(csv_path),
        started_at=datetime.utcnow()
    )
    
    try:
        # Read CSV
        df = pd.read_csv(csv_path)
        
        # Map column names
        df = map_columns(df)
        
        # Check for required column
        if 'application_id' not in df.columns:
            # Try to use first column as ID if no mapping found
            if len(df.columns) > 0:
                first_col = df.columns[0]
                df = df.rename(columns={first_col: 'application_id'})
            else:
                raise ValueError("Cannot find application ID column in CSV")
        
        # Default status filter - exclude signed and denied
        if status_filter is None:
            status_filter = ['In Progress', 'Pending', 'Pending Approval', 'Approved', 'Applied']
        
        # Filter by status if status column exists
        if 'status' in df.columns and status_filter:
            # Case-insensitive filter
            df_filtered = df[df['status'].str.lower().isin([s.lower() for s in status_filter])]
            if len(df_filtered) < len(df):
                print(f"Filtered to {len(df_filtered)} rows (excluded signed/denied)")
            df = df_filtered
        
        added = 0
        updated = 0
        
        for _, row in df.iterrows():
            app_id = str(row.get('application_id', '')).strip()
            if not app_id or app_id == 'nan':
                continue
            
            # Check if application exists
            existing = session.query(Application).filter_by(application_id=app_id).first()
            
            if existing:
                # Update existing record
                for field in ['applicant_name', 'applicant_email', 'applicant_phone',
                              'property_name', 'property_address', 'unit', 'status',
                              'screening_status']:
                    if field in row and pd.notna(row[field]):
                        setattr(existing, field, str(row[field]).strip())
                
                # Parse date fields
                for field in ['application_date', 'desired_move_in', 'lease_start_date', 'lease_end_date']:
                    if field in row:
                        setattr(existing, field, parse_date(row[field]))
                
                # Parse numeric fields
                for field in ['rent_amount', 'deposit_amount']:
                    if field in row:
                        setattr(existing, field, parse_number(row[field]))
                
                if 'credit_score' in row and pd.notna(row['credit_score']):
                    try:
                        existing.credit_score = int(float(row['credit_score']))
                    except (ValueError, TypeError):
                        pass
                
                existing.last_synced_at = datetime.utcnow()
                updated += 1
            else:
                # Create new record
                app = Application(application_id=app_id)
                
                for field in ['applicant_name', 'applicant_email', 'applicant_phone',
                              'property_name', 'property_address', 'unit', 'status',
                              'screening_status']:
                    if field in row and pd.notna(row[field]):
                        setattr(app, field, str(row[field]).strip())
                
                for field in ['application_date', 'desired_move_in', 'lease_start_date', 'lease_end_date']:
                    if field in row:
                        setattr(app, field, parse_date(row[field]))
                
                for field in ['rent_amount', 'deposit_amount']:
                    if field in row:
                        setattr(app, field, parse_number(row[field]))
                
                if 'credit_score' in row and pd.notna(row['credit_score']):
                    try:
                        app.credit_score = int(float(row['credit_score']))
                    except (ValueError, TypeError):
                        pass
                
                session.add(app)
                added += 1
        
        session.commit()
        
        sync_log.records_processed = len(df)
        sync_log.records_added = added
        sync_log.records_updated = updated
        sync_log.success = True
        sync_log.completed_at = datetime.utcnow()
        session.add(sync_log)
        session.commit()
        
        return {'processed': len(df), 'added': added, 'updated': updated}
    
    except Exception as e:
        sync_log.success = False
        sync_log.error_message = str(e)
        sync_log.completed_at = datetime.utcnow()
        session.add(sync_log)
        session.commit()
        raise
    
    finally:
        session.close()


def import_from_appfolio_api() -> Dict[str, int]:
    """
    Import lease applications directly from AppFolio API.
    
    Required environment variables:
        APPFOLIO_API_URL: Base URL for your AppFolio API (e.g., https://yourcompany.appfolio.com/api/v1)
        APPFOLIO_CLIENT_ID: Your API client ID
        APPFOLIO_CLIENT_SECRET: Your API client secret
    
    The API field mappings can be customized via APPFOLIO_FIELD_MAP env var (JSON string).
    """
    import json
    
    api_url = os.getenv('APPFOLIO_API_URL')
    client_id = os.getenv('APPFOLIO_CLIENT_ID')
    client_secret = os.getenv('APPFOLIO_CLIENT_SECRET')
    
    if not all([api_url, client_id, client_secret]):
        raise ValueError(
            "Missing AppFolio API credentials. Set these environment variables:\n"
            "  APPFOLIO_API_URL - Your AppFolio API base URL\n"
            "  APPFOLIO_CLIENT_ID - Your API client ID\n"
            "  APPFOLIO_CLIENT_SECRET - Your API client secret"
        )
    
    # Default field mappings from AppFolio API response to our database
    # Customize via APPFOLIO_FIELD_MAP env var if your API uses different field names
    default_field_map = {
        'id': 'application_id',
        'application_id': 'application_id',
        'applicant_name': 'applicant_name',
        'tenant_name': 'applicant_name',
        'primary_applicant_name': 'applicant_name',
        'email': 'applicant_email',
        'applicant_email': 'applicant_email',
        'phone': 'applicant_phone',
        'applicant_phone': 'applicant_phone',
        'property_name': 'property_name',
        'property': 'property_name',
        'property_address': 'property_address',
        'address': 'property_address',
        'unit': 'unit',
        'unit_number': 'unit',
        'status': 'status',
        'application_status': 'status',
        'application_date': 'application_date',
        'created_at': 'application_date',
        'submitted_at': 'application_date',
        'desired_move_in_date': 'desired_move_in',
        'move_in_date': 'desired_move_in',
        'lease_start_date': 'lease_start_date',
        'lease_end_date': 'lease_end_date',
        'rent': 'rent_amount',
        'rent_amount': 'rent_amount',
        'monthly_rent': 'rent_amount',
        'deposit': 'deposit_amount',
        'security_deposit': 'deposit_amount',
        'screening_status': 'screening_status',
    }
    
    # Allow custom field mapping via env var
    custom_map = os.getenv('APPFOLIO_FIELD_MAP')
    if custom_map:
        try:
            default_field_map.update(json.loads(custom_map))
        except json.JSONDecodeError:
            pass
    
    Session = init_database()
    session = Session()
    
    sync_log = SyncLog(
        sync_type='appfolio_api',
        sync_source=api_url,
        started_at=datetime.utcnow()
    )
    
    try:
        # Authenticate with AppFolio API
        # Try OAuth2 client credentials flow first
        auth_response = requests.post(
            f"{api_url.rstrip('/')}/oauth/token",
            data={
                'grant_type': 'client_credentials',
                'client_id': client_id,
                'client_secret': client_secret,
            },
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        )
        
        if auth_response.status_code == 200:
            token_data = auth_response.json()
            access_token = token_data.get('access_token')
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json'
            }
        else:
            # Fall back to basic auth or API key
            headers = {
                'Authorization': f'Basic {client_id}:{client_secret}',
                'X-API-Key': client_secret,
                'Content-Type': 'application/json'
            }
        
        # Fetch applications - try common endpoint patterns
        applications = []
        endpoints_to_try = [
            '/applications',
            '/lease_applications', 
            '/leases',
            '/rental_applications',
        ]
        
        for endpoint in endpoints_to_try:
            try:
                response = requests.get(
                    f"{api_url.rstrip('/')}{endpoint}",
                    headers=headers,
                    params={
                        'status': 'in_progress,pending,approved,applied',
                        'per_page': 500,
                    }
                )
                if response.status_code == 200:
                    data = response.json()
                    # Handle different response structures
                    if isinstance(data, list):
                        applications = data
                    elif isinstance(data, dict):
                        applications = data.get('applications', 
                                       data.get('data', 
                                       data.get('results', 
                                       data.get('items', []))))
                    if applications:
                        print(f"Found {len(applications)} applications via {endpoint}")
                        break
            except requests.RequestException:
                continue
        
        if not applications:
            raise ValueError(
                f"Could not fetch applications from AppFolio API.\n"
                f"Tried endpoints: {endpoints_to_try}\n"
                f"Check your API URL and credentials."
            )
        
        added = 0
        updated = 0
        
        for app_data in applications:
            # Map API fields to our database fields
            mapped = {}
            for api_field, db_field in default_field_map.items():
                if api_field in app_data:
                    mapped[db_field] = app_data[api_field]
            
            app_id = str(mapped.get('application_id', ''))
            if not app_id:
                continue
            
            # Filter by status - only in-progress applications
            status = str(mapped.get('status', '')).lower()
            if status in ['signed', 'denied', 'cancelled', 'completed', 'rejected']:
                continue
            
            existing = session.query(Application).filter_by(application_id=app_id).first()
            
            if existing:
                # Update existing record
                for field in ['applicant_name', 'applicant_email', 'applicant_phone',
                              'property_name', 'property_address', 'unit', 'status',
                              'screening_status']:
                    if field in mapped and mapped[field]:
                        setattr(existing, field, str(mapped[field]))
                
                for field in ['application_date', 'desired_move_in', 'lease_start_date', 'lease_end_date']:
                    if field in mapped:
                        setattr(existing, field, parse_date(mapped[field]))
                
                for field in ['rent_amount', 'deposit_amount']:
                    if field in mapped:
                        setattr(existing, field, parse_number(mapped[field]))
                
                existing.last_synced_at = datetime.utcnow()
                updated += 1
            else:
                app = Application(application_id=app_id)
                
                for field in ['applicant_name', 'applicant_email', 'applicant_phone',
                              'property_name', 'property_address', 'unit', 'status',
                              'screening_status']:
                    if field in mapped and mapped[field]:
                        setattr(app, field, str(mapped[field]))
                
                for field in ['application_date', 'desired_move_in', 'lease_start_date', 'lease_end_date']:
                    if field in mapped:
                        setattr(app, field, parse_date(mapped[field]))
                
                for field in ['rent_amount', 'deposit_amount']:
                    if field in mapped:
                        setattr(app, field, parse_number(mapped[field]))
                
                session.add(app)
                added += 1
        
        session.commit()
        
        sync_log.records_processed = len(applications)
        sync_log.records_added = added
        sync_log.records_updated = updated
        sync_log.success = True
        sync_log.completed_at = datetime.utcnow()
        session.add(sync_log)
        session.commit()
        
        return {'processed': len(applications), 'added': added, 'updated': updated}
    
    except Exception as e:
        sync_log.success = False
        sync_log.error_message = str(e)
        sync_log.completed_at = datetime.utcnow()
        session.add(sync_log)
        session.commit()
        raise
    
    finally:
        session.close()


def import_from_skywalk_api() -> Dict[str, int]:
    """
    Import lease applications from Skywalk API (third-party AppFolio API).
    
    Requires SKYWALK_API_KEY and SKYWALK_COMPANY_ID environment variables.
    
    See: https://skywalkapi.com/docs
    """
    api_key = os.getenv('SKYWALK_API_KEY')
    company_id = os.getenv('SKYWALK_COMPANY_ID')
    
    if not api_key or not company_id:
        raise ValueError(
            "Missing Skywalk API credentials. Set SKYWALK_API_KEY and SKYWALK_COMPANY_ID "
            "environment variables. Sign up at https://skywalkapi.com"
        )
    
    Session = init_database()
    session = Session()
    
    sync_log = SyncLog(
        sync_type='skywalk_api',
        sync_source='skywalkapi.com',
        started_at=datetime.utcnow()
    )
    
    try:
        # Get leases/applications from Skywalk API
        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json'
        }
        
        response = requests.get(
            f'https://api.skywalkapi.com/v1/companies/{company_id}/leases',
            headers=headers,
            params={'status': 'pending,in_progress,approved'}
        )
        response.raise_for_status()
        
        data = response.json()
        leases = data.get('leases', data.get('data', []))
        
        added = 0
        updated = 0
        
        for lease in leases:
            app_id = str(lease.get('id', lease.get('application_id', '')))
            if not app_id:
                continue
            
            existing = session.query(Application).filter_by(application_id=app_id).first()
            
            if existing:
                existing.applicant_name = lease.get('tenant_name', existing.applicant_name)
                existing.property_address = lease.get('property_address', existing.property_address)
                existing.unit = lease.get('unit', existing.unit)
                existing.status = lease.get('status', existing.status)
                existing.rent_amount = lease.get('rent', existing.rent_amount)
                existing.last_synced_at = datetime.utcnow()
                updated += 1
            else:
                app = Application(
                    application_id=app_id,
                    applicant_name=lease.get('tenant_name'),
                    applicant_email=lease.get('email'),
                    property_address=lease.get('property_address'),
                    unit=lease.get('unit'),
                    status=lease.get('status'),
                    rent_amount=lease.get('rent'),
                )
                session.add(app)
                added += 1
        
        session.commit()
        
        sync_log.records_processed = len(leases)
        sync_log.records_added = added
        sync_log.records_updated = updated
        sync_log.success = True
        sync_log.completed_at = datetime.utcnow()
        session.add(sync_log)
        session.commit()
        
        return {'processed': len(leases), 'added': added, 'updated': updated}
    
    except Exception as e:
        sync_log.success = False
        sync_log.error_message = str(e)
        sync_log.completed_at = datetime.utcnow()
        session.add(sync_log)
        session.commit()
        raise
    
    finally:
        session.close()

