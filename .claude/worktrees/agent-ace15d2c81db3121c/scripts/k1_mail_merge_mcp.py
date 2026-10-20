#!/usr/bin/env python3
"""
K-1 Mail Merge Script for Fund III using Google Workspace MCP
Sends personalized K-1 links via Gmail to investors
"""

import subprocess
import json
import sys

# Configuration
YOUR_EMAIL = 'mckay@m5c7.com'

def run_mcp_command(tool, params):
    """Run an MCP tool via workspace-mcp CLI"""
    cmd = ['uvx', 'workspace-mcp']

    # Build the command
    full_cmd = cmd + [tool] + [f"--{k}={v}" for k, v in params.items()]

    result = subprocess.run(full_cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"Error: {result.stderr}")
        return None

    return result.stdout

def get_sheet_data():
    """Read the K-1 Mail Merge sheet"""
    print("Reading spreadsheet data...")

    params = {
        'user-google-email': 'mckay@m5c7.com',
        'spreadsheet-id': '1XXMQ3bjRZGD4Ifl12VhnSXIbrrmnaboF19Jj1NYYL7U',
        'range-name': 'K-1 Mail Merge!A2:D100'
    }

    result = run_mcp_command('read_sheet_values', params)
    if not result:
        return []

    # Parse the output
    lines = result.strip().split('\n')
    data = []
    for line in lines:
        if line.startswith('Row '):
            # Extract the row data
            try:
                row_data = eval(line.split(': ', 1)[1])
                data.append(row_data)
            except:
                continue

    return data

def send_email_via_mcp(to, subject, body):
    """Send email via Google Workspace MCP"""
    params = {
        'user-google-email': 'mckay@m5c7.com',
        'to': to,
        'subject': subject,
        'body': body
    }

    result = run_mcp_command('send_gmail_message', params)
    return result is not None

def create_email_body(investor_name, k1_link, email):
    """Generate personalized email body"""
    # Clean up investor name
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
    params = {
        'user-google-email': 'mckay@m5c7.com',
        'spreadsheet-id': '1XXMQ3bjRZGD4Ifl12VhnSXIbrrmnaboF19Jj1NYYL7U',
        'range-name': f'K-1 Mail Merge!D{row_number + 2}',
        'values': json.dumps([[timestamp]])
    }

    run_mcp_command('modify_sheet_values', params)

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

    print(f"\n--- Email Preview ---")
    print(f"To: {YOUR_EMAIL}")
    print(f"Subject: {subject}")
    print(f"Body:\n{body}")
    print("--- End Preview ---\n")

    if send_email_via_mcp(YOUR_EMAIL, subject, body):
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

        if send_email_via_mcp(emails, subject, body):
            # Mark as sent with timestamp
            from datetime import datetime
            timestamp = datetime.now().isoformat()
            update_sent_status(i, timestamp)
            sent_count += 1
            print(f"  ✓ Sent to {emails}")
        else:
            error_count += 1
            print(f"  ✗ Failed to send")

        # Rate limiting
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
        print("K-1 Mail Merge Script (MCP Version)")
        print("\nUsage:")
        print("  python3 k1_mail_merge_mcp.py test    # Send test email to yourself")
        print("  python3 k1_mail_merge_mcp.py send    # Send to all investors")
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
