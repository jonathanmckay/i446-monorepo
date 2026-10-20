"""
Command-line interface for the leasing report sync tool.
"""
import os
import sys
from datetime import datetime
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from dotenv import load_dotenv

from .models import Application, ApplicationNote, SyncLog, init_database
from .appfolio_import import import_from_csv, import_from_appfolio_api, import_from_skywalk_api
from .sheets_sync import sync_to_sheets, sync_from_sheets, full_sync

load_dotenv()
console = Console()


@click.group()
def cli():
    """AppFolio Leasing Report - Sync to Google Sheets with notes."""
    pass


@cli.command()
@click.argument('csv_file', type=click.Path(exists=True))
@click.option('--status-filter', '-s', multiple=True, 
              help='Only import applications with these statuses')
def import_csv(csv_file: str, status_filter: tuple):
    """Import lease applications from an AppFolio CSV export."""
    console.print(f"\n[bold blue]Importing from:[/] {csv_file}\n")
    
    try:
        status_list = list(status_filter) if status_filter else None
        result = import_from_csv(Path(csv_file), status_list)
        
        console.print(Panel(
            f"[green]✓ Import complete![/]\n\n"
            f"  Processed: {result['processed']}\n"
            f"  Added:     {result['added']}\n"
            f"  Updated:   {result['updated']}",
            title="Import Results"
        ))
    except Exception as e:
        console.print(f"[red]Error:[/] {e}")
        sys.exit(1)


@cli.command()
def import_api():
    """Import lease applications from AppFolio API (direct access)."""
    console.print("\n[bold blue]Importing from AppFolio API...[/]\n")
    
    try:
        result = import_from_appfolio_api()
        
        console.print(Panel(
            f"[green]✓ Import complete![/]\n\n"
            f"  Processed: {result['processed']}\n"
            f"  Added:     {result['added']}\n"
            f"  Updated:   {result['updated']}",
            title="AppFolio API Import Results"
        ))
    except Exception as e:
        console.print(f"[red]Error:[/] {e}")
        sys.exit(1)


@cli.command()
def import_skywalk():
    """Import lease applications from Skywalk API (third-party)."""
    console.print("\n[bold blue]Importing from Skywalk API...[/]\n")
    
    try:
        result = import_from_skywalk_api()
        
        console.print(Panel(
            f"[green]✓ Import complete![/]\n\n"
            f"  Processed: {result['processed']}\n"
            f"  Added:     {result['added']}\n"
            f"  Updated:   {result['updated']}",
            title="Skywalk API Import Results"
        ))
    except Exception as e:
        console.print(f"[red]Error:[/] {e}")
        sys.exit(1)


@cli.command()
@click.option('--sheet-id', '-s', help='Google Sheet ID (or set GOOGLE_SHEET_ID env var)')
@click.option('--sheet-name', '-n', default='Leasing Pipeline', help='Worksheet name')
def push(sheet_id: str, sheet_name: str):
    """Push application data to Google Sheets."""
    console.print("\n[bold blue]Pushing to Google Sheets...[/]\n")
    
    try:
        result = sync_to_sheets(sheet_id, sheet_name)
        
        console.print(Panel(
            f"[green]✓ Push complete![/]\n\n"
            f"  Rows written: {result['rows_written']}\n"
            f"  Sheet URL:    {result['spreadsheet_url']}",
            title="Push Results"
        ))
    except Exception as e:
        console.print(f"[red]Error:[/] {e}")
        sys.exit(1)


@cli.command()
@click.option('--sheet-id', '-s', help='Google Sheet ID')
@click.option('--sheet-name', '-n', default='Leasing Pipeline', help='Worksheet name')
def pull(sheet_id: str, sheet_name: str):
    """Pull notes and custom fields from Google Sheets."""
    console.print("\n[bold blue]Pulling from Google Sheets...[/]\n")
    
    try:
        result = sync_from_sheets(sheet_id, sheet_name)
        
        console.print(Panel(
            f"[green]✓ Pull complete![/]\n\n"
            f"  Notes updated:  {result['notes_updated']}\n"
            f"  Fields updated: {result['fields_updated']}",
            title="Pull Results"
        ))
    except Exception as e:
        console.print(f"[red]Error:[/] {e}")
        sys.exit(1)


@cli.command()
@click.option('--sheet-id', '-s', help='Google Sheet ID')
@click.option('--sheet-name', '-n', default='Leasing Pipeline', help='Worksheet name')
def sync(sheet_id: str, sheet_name: str):
    """Full two-way sync with Google Sheets (preserves notes)."""
    console.print("\n[bold blue]Full sync with Google Sheets...[/]\n")
    
    try:
        result = full_sync(sheet_id, sheet_name)
        
        console.print(Panel(
            f"[green]✓ Sync complete![/]\n\n"
            f"  [bold]Pull (from sheets):[/]\n"
            f"    Notes updated: {result['pull'].get('notes_updated', 0)}\n"
            f"    Fields updated: {result['pull'].get('fields_updated', 0)}\n\n"
            f"  [bold]Push (to sheets):[/]\n"
            f"    Rows written: {result['push'].get('rows_written', 0)}",
            title="Sync Results"
        ))
    except Exception as e:
        console.print(f"[red]Error:[/] {e}")
        sys.exit(1)


@cli.command()
@click.option('--limit', '-l', default=20, help='Number of applications to show')
def list_apps(limit: int):
    """List applications in the database."""
    Session = init_database()
    session = Session()
    
    try:
        apps = session.query(Application).order_by(
            Application.application_date.desc()
        ).limit(limit).all()
        
        if not apps:
            console.print("[yellow]No applications in database. Run 'import-csv' first.[/]")
            return
        
        table = Table(title=f"Lease Applications (showing {len(apps)})")
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Status", style="magenta")
        table.add_column("Applicant", style="green")
        table.add_column("Property")
        table.add_column("Unit")
        table.add_column("Rent", justify="right")
        table.add_column("Applied", style="dim")
        table.add_column("Notes", style="yellow")
        
        for app in apps:
            notes = app.notes
            note_preview = notes[0].note[:30] + "..." if notes and notes[0].note else ""
            
            table.add_row(
                app.application_id[:12] if app.application_id else "",
                app.status or "",
                (app.applicant_name or "")[:20],
                (app.property_address or "")[:25],
                app.unit or "",
                f"${app.rent_amount:,.0f}" if app.rent_amount else "",
                app.application_date.strftime("%m/%d") if app.application_date else "",
                note_preview
            )
        
        console.print(table)
    
    finally:
        session.close()


@cli.command()
@click.argument('application_id')
@click.argument('note_text')
def add_note(application_id: str, note_text: str):
    """Add a note to an application."""
    Session = init_database()
    session = Session()
    
    try:
        app = session.query(Application).filter_by(application_id=application_id).first()
        
        if not app:
            # Try partial match
            app = session.query(Application).filter(
                Application.application_id.like(f"%{application_id}%")
            ).first()
        
        if not app:
            console.print(f"[red]Application not found:[/] {application_id}")
            sys.exit(1)
        
        note = ApplicationNote(
            application_id=app.application_id,
            note=note_text,
            note_type='general'
        )
        session.add(note)
        session.commit()
        
        console.print(f"[green]✓ Note added to {app.application_id}[/]")
    
    finally:
        session.close()


@cli.command()
def status():
    """Show sync status and database stats."""
    Session = init_database()
    session = Session()
    
    try:
        # Count applications by status
        apps = session.query(Application).all()
        status_counts = {}
        for app in apps:
            status = app.status or 'Unknown'
            status_counts[status] = status_counts.get(status, 0) + 1
        
        # Get recent sync logs
        recent_syncs = session.query(SyncLog).order_by(
            SyncLog.started_at.desc()
        ).limit(5).all()
        
        # Build status table
        console.print("\n[bold]Database Status[/]\n")
        
        table = Table(title="Applications by Status")
        table.add_column("Status", style="cyan")
        table.add_column("Count", justify="right")
        
        for status, count in sorted(status_counts.items(), key=lambda x: -x[1]):
            table.add_row(status, str(count))
        
        table.add_row("[bold]Total[/]", f"[bold]{len(apps)}[/]")
        console.print(table)
        
        # Sync history
        if recent_syncs:
            console.print("\n[bold]Recent Syncs[/]\n")
            
            sync_table = Table()
            sync_table.add_column("Type")
            sync_table.add_column("Time")
            sync_table.add_column("Records")
            sync_table.add_column("Status")
            
            for log in recent_syncs:
                status_str = "[green]✓[/]" if log.success else f"[red]✗[/] {log.error_message[:30] if log.error_message else ''}"
                sync_table.add_row(
                    log.sync_type,
                    log.started_at.strftime("%Y-%m-%d %H:%M") if log.started_at else "",
                    str(log.records_processed or 0),
                    status_str
                )
            
            console.print(sync_table)
    
    finally:
        session.close()


@cli.command()
def init():
    """Initialize the database and show setup instructions."""
    console.print("\n[bold blue]Initializing Leasing Report System...[/]\n")
    
    # Initialize database
    Session = init_database()
    
    console.print("[green]✓ Database initialized[/]\n")
    
    # Check for credentials
    creds_path = Path(__file__).parent.parent / 'credentials' / 'service_account.json'
    sheet_id = os.getenv('GOOGLE_SHEET_ID')
    
    console.print("[bold]Setup Checklist:[/]\n")
    
    if creds_path.exists():
        console.print("  [green]✓[/] Google credentials found")
    else:
        console.print("  [yellow]○[/] Google credentials not found")
        console.print(f"    → Place service_account.json in: {creds_path.parent}")
    
    if sheet_id:
        console.print(f"  [green]✓[/] Google Sheet ID configured: {sheet_id[:20]}...")
    else:
        console.print("  [yellow]○[/] Google Sheet ID not set")
        console.print("    → Add GOOGLE_SHEET_ID to .env file")
    
    console.print("\n[bold]Quick Start:[/]\n")
    console.print("  1. Export CSV from AppFolio (Leasing → Metrics → Export)")
    console.print("  2. python -m src.cli import-csv path/to/export.csv")
    console.print("  3. python -m src.cli push")
    console.print("  4. View and edit in Google Sheets")
    console.print("  5. python -m src.cli sync  # to preserve notes\n")


if __name__ == '__main__':
    cli()

