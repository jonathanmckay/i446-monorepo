#!/usr/bin/env python3
"""
K-1 Mail Merge Script for Fund III
Sends personalized K-1 links via Gmail to investors
"""

import os
import sys
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import pickle
import base64
from email.mime.text import MIMEText

# Gmail API scopes
SCOPES = ['https://www.googleapis.com/auth/gmail.send',
          'https://www.googleapis.com/auth/spreadsheets.readonly']

# Configuration
SPREADSHEET_ID = '1XXMQ3bjRZGD4Ifl12VhnSXIbrrmnaboF19Jj1NYYL7U'
SHEET_NAME = 'K-1 Mail Merge'
YOUR_EMAIL = 'mckay@m5c7.com'  # Your email for testing

def get_credentials():
    """Get or refresh Gmail and Sheets API credentials"""
    creds = None
    token_path = os.path.expanduser('~/.config/google-calendar-mcp/gcp-oauth.keys.json')

    # Try to use existing credentials
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)

    # If no valid credentials, let user log in
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(token_path):
                print(f"Error: OAuth credentials not found at {token_path}")
                print("Please set up OAuth credentials first.")
                sys.exit(1)

            flow = InstalledAppFlow.from_client_secrets_file(token_path, SCOPES)
            creds = flow.run_local_server(port=0)

        # Save credentials for next run
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)

    return creds

def get_sheet_data():
    """Read the K-1 Mail Merge sheet"""
    creds = get_credentials()
    service = build('sheets', 'v4', credentials=creds)

    result = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f'{SHEET_NAME}!A2:D100'  # Skip header, read data rows
    ).execute()

    return result.get('values', [])

def create_email_message(to, subject, body):
    """Create email message for Gmail API"""
    message = MIMEText(body)
    message['to'] = to
    message['subject'] = subject
    message['from'] = YOUR_EMAIL

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    return {'raw': raw}

def send_email(service, to, subject, body):
    """Send email via Gmail API"""
    try:
        message = create_email_message(to, subject, body)
        sent = service.users().messages().send(userId='me', body=message).execute()
        print(f"✓ Email sent to {to} (Message ID: {sent['id']})")
        return True
    except Exception as e:
        print(f"✗ Error sending to {to}: {e}")
        return False

def create_email_body(investor_name, k1_link, email):
    """Generate personalized email body"""
    # Clean up investor name (remove parenthetical info if needed)
    name = investor_name.split('(')[0].strip()

    return f"""{name},

Thank you for your continued partnership in m5x2 Fund III.

Your 2025 Schedule K-1 is ready. You can access it here:
{k1_link}

Access is scoped to this email address ({email}).

If you have questions or need anything for your tax preparer, just reply to this email.

Best,
Jonathan"""

def update_sent_status(row_number, timestamp):
    """Mark email as sent in the spreadsheet"""
    creds = get_credentials()
    service = build('sheets', 'v4', credentials=creds)

    # Row number is 1-indexed in Sheets, +1 for header row
    range_name = f'{SHEET_NAME}!D{row_number + 2}'

    service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=range_name,
        valueInputOption='USER_ENTERED',
        body={'values': [[timestamp]]}
    ).execute()

def send_test_email():
    """Send one test email to yourself with first investor's data"""
    print("=" * 60)
    print("TEST MODE: Sending test email to yourself")
    print("=" * 60)

    data = get_sheet_data()
    if not data:
        print("Error: No data found in sheet")
        return

    # Use first investor's data for test
    row = data[0]
    investor_name = row[0] if len(row) > 0 else ""
    k1_link = row[2] if len(row) > 2 else ""
    original_email = row[1] if len(row) > 1 else ""

    print(f"\nTest data:")
    print(f"  Investor: {investor_name}")
    print(f"  Original email: {original_email}")
    print(f"  K-1 Link: {k1_link}")
    print(f"\nSending to: {YOUR_EMAIL}")

    subject = "m5x2 Fund III - Your 2025 K-1 is Ready"
    body = create_email_body(investor_name, k1_link, original_email)

    creds = get_credentials()
    gmail = build('gmail', 'v1', credentials=creds)

    if send_email(gmail, YOUR_EMAIL, subject, body):
        print("\n✓ Test email sent successfully!")
        print("Check your inbox and verify the formatting before sending to all investors.")
    else:
        print("\n✗ Test email failed.")

def send_all_emails():
    """Send emails to all investors (except W. Scott Mitchell & Barb Bryan)"""
    print("=" * 60)
    print("SENDING TO ALL INVESTORS")
    print("=" * 60)

    confirm = input("\nAre you sure you want to send to ALL investors? (yes/no): ")
    if confirm.lower() != 'yes':
        print("Cancelled.")
        return

    data = get_sheet_data()
    creds = get_credentials()
    gmail = build('gmail', 'v1', credentials=creds)

    subject = "m5x2 Fund III - Your 2025 K-1 is Ready"

    sent_count = 0
    skipped_count = 0
    error_count = 0

    for i, row in enumerate(data):
        if len(row) < 3:
            skipped_count += 1
            continue

        investor_name = row[0]
        emails = row[1] if len(row) > 1 else ""
        k1_link = row[2]
        already_sent = row[3] if len(row) > 3 else ""

        # Skip conditions
        if already_sent:
            print(f"↷ Skipping {investor_name} (already sent)")
            skipped_count += 1
            continue

        if not emails or not k1_link:
            print(f"↷ Skipping {investor_name} (missing email or K-1 link)")
            skipped_count += 1
            continue

        # Skip W. Scott Mitchell & Barb Bryan
        if "W. Scott Mitchell" in investor_name or "Barb Bryan" in investor_name:
            print(f"↷ Skipping {investor_name} (excluded per request)")
            skipped_count += 1
            continue

        # Skip orphan K-1 entries
        if investor_name.startswith("[K-1 only]"):
            print(f"↷ Skipping {investor_name} (orphan K-1)")
            skipped_count += 1
            continue

        # Send email
        print(f"\n[{i+1}/{len(data)}] Sending to {investor_name}...")
        body = create_email_body(investor_name, k1_link, emails)

        if send_email(gmail, emails, subject, body):
            # Mark as sent with timestamp
            from datetime import datetime
            timestamp = datetime.now().isoformat()
            update_sent_status(i, timestamp)
            sent_count += 1
        else:
            error_count += 1

        # Rate limiting - wait 1 second between emails
        import time
        time.sleep(1)

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"✓ Sent: {sent_count}")
    print(f"↷ Skipped: {skipped_count}")
    print(f"✗ Errors: {error_count}")

def main():
    if len(sys.argv) < 2:
        print("K-1 Mail Merge Script")
        print("\nUsage:")
        print("  python3 k1_mail_merge.py test    # Send test email to yourself")
        print("  python3 k1_mail_merge.py send    # Send to all investors")
        sys.exit(1)

    mode = sys.argv[1].lower()

    if mode == 'test':
        send_test_email()
    elif mode == 'send':
        send_all_emails()
    else:
        print(f"Unknown mode: {mode}")
        print("Use 'test' or 'send'")
        sys.exit(1)

if __name__ == '__main__':
    main()
