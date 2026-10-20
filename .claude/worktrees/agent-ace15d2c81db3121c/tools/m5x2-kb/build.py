#!/usr/bin/env python3
"""
Scans m5x2 Google Drive SOPs (.gddoc/.gdsheet files), extracts Google Docs URLs,
categorizes them, and generates a self-contained HTML knowledge base.
"""

import json
import os
import html
from datetime import datetime
from pathlib import Path

SOPS_DIR = Path.home() / "vault/h335/m5x2/drive-main/SOPs"
DRIVE_DIR = Path.home() / "vault/h335/m5x2/drive-main"
OWNERS_FILE = SOPS_DIR / "sop-owners.json"

# Set this after deploying the Apps Script web app (step 2 in analytics-apps-script.js)
ANALYTICS_URL = ""  # e.g. "https://script.google.com/macros/s/XXXXX/exec"

# Manual categorization: maps (subfolder_prefix, filename_substring) → category
# Order matters — first match wins
CATEGORY_RULES = [
    # Leasing & Tenants (mostly R202)
    ("leasing", [
        ("A. R202", "Application Screening"),
        ("A. R202", "Lease Version Control"),
        ("A. R202", "Marketing of Vacant"),
        ("A. R202", "Move-in_out"),
        ("A. R202", "Move-In Checklist"),
        ("A. R202", "Tenant Communication"),
        ("A. R202", "Early Lease Termination"),
        ("A. R202", "Focus tenant"),
        ("A. R202", "rent deposits"),
        ("A. R202", "Pets"),
        ("A. R202", "Tenant Referral"),
        ("A. R202", "Inter-portfolio"),
        ("A. R202", "building manager transition"),
        ("A. R202", "Disposition"),
        ("A. R202", "Tenant Transfer"),
        ("", "AppFolio Lease"),
        ("", "Reasonable Accommodation"),
        ("Marketing", "Facebook Marketplace"),
    ]),
    # Work Orders & Maintenance (mostly R203)
    ("work_orders", [
        ("B. R203", "Work Order"),
        ("B. R203", "Urgent Work Order"),
        ("B. R203", "Renovation rework"),
        ("B. R203", "Contractor"),
        ("", "CompanyCam"),
    ]),
    # HR & People
    ("hr", [
        ("B. R203", "Employee training"),
        ("B. R203", "Feedback cycle"),
        ("B. R203", "Employee Handbook"),
        ("C. R888", "Recruiting"),
        ("C. R888", "Schedule"),
        ("C. R888", "Paid Sick"),
        ("C. R888", "General Pay"),
        ("C. R888", "Employee Relationships"),
        ("C. R888", "Resignation"),
        ("C. R888", "Remote-work"),
        ("C. R888", "Employee Handbook"),
        ("B. R203", "Schedule for Employment"),
    ]),
    # Safety & Compliance (mostly R888)
    ("safety", [
        ("C. R888", "Accident Prevention"),
        ("C. R888", "Hazard"),
        ("C. R888", "PPE"),
        ("C. R888", "Personal Protective"),
        ("C. R888", "Respiratory"),
        ("C. R888", "Hearing Conservation"),
        ("C. R888", "First aid"),
        ("C. R888", "Incident reporting"),
        ("C. R888", "Incident Investigation"),
        ("C. R888", "Respirator Fit"),
        ("C. R888", "Safety Meeting"),
        ("C. R888", "Drug and Alcohol"),
        ("C. R888", "Job site access"),
        ("C. R888", "Workplace Visitors"),
        ("C. R888", "Anti-harassment"),
        ("C. R888", "Equal Employment"),
        ("C. R888", "Non-retaliation"),
        ("C. R888", "Disciplinary"),
        ("A. R202", "Safety-Risk-Tenant"),
        ("", "Dealing with Threats"),
    ]),
    # Finance & Admin
    ("finance", [
        ("B. R203", "Purchasing"),
        ("B. R203", "Time tracking"),
        ("B. R203", "Mileage"),
        ("B. R203", "Company Equipment"),
        ("C. R888", "Weekly Updates"),
        ("C. R888", "External Contractor Walk"),
        ("A. R202", "Weekly Updates"),
        ("", "Time tracking"),
        ("", "Travel per diem"),
        ("", "Turn invoicing"),
        ("", "Document Management"),
    ]),
    # Guest & Property Ops (H5c7) — only actual SOPs, not legal/banking docs
    ("guest_ops", [
        ("D. H5c7", ""),
    ]),
    # Reference
    ("reference", [
        ("", "Internal Terms Library"),
    ]),
]

CATEGORIES = {
    "roadmaps": {"title": "Roadmaps & Goals", "icon": "🎯", "desc": "Current quarter roadmaps and strategic plans"},
    "leasing": {"title": "Leasing & Tenants", "icon": "🏠", "desc": "Marketing, screening, leases, move-in/out, tenant relations"},
    "work_orders": {"title": "Work Orders & Maintenance", "icon": "🔧", "desc": "Repairs, unit turns, contractors, inspections"},
    "hr": {"title": "HR & People", "icon": "👥", "desc": "Hiring, onboarding, pay, time off, performance, exits"},
    "safety": {"title": "Safety & Compliance", "icon": "⚠️", "desc": "Required reading for all field employees"},
    "finance": {"title": "Finance & Admin", "icon": "💰", "desc": "Purchasing, expenses, equipment, time tracking, documents"},
    "guest_ops": {"title": "Guest & Property Operations", "icon": "🏡", "desc": "H5c7 cohousing and special properties"},
    "reference": {"title": "Reference", "icon": "📚", "desc": "Glossary, terms, and templates"},
}

ENTITY_MAP = {
    "A. R202": "R202",
    "B. R203": "R203",
    "C. R888": "R888",
    "D. H5c7": "H5c7",
    "h5c7": "H5c7",
    "Marketing": "Cross",
}


def extract_entity(rel_path: str) -> str:
    parts = rel_path.split(os.sep)
    if len(parts) > 1:
        folder = parts[0]
        return ENTITY_MAP.get(folder, "")
    return ""


def categorize(rel_path: str, filename: str) -> str:
    for cat_key, rules in CATEGORY_RULES:
        for folder_prefix, name_substr in rules:
            if folder_prefix and not rel_path.startswith(folder_prefix):
                continue
            if name_substr and name_substr.lower() not in filename.lower():
                continue
            if not folder_prefix and not name_substr:
                continue
            return cat_key
    return "reference"  # default bucket


def clean_name(filename: str) -> str:
    name = filename
    for ext in [".gddoc", ".gdsheet", ".docx"]:
        name = name.replace(ext, "")
    # Remove "SOP:" or "SOP " prefix for cleaner display
    for prefix in ["SOP: ", "SOP:", "SOP-", "SOP "]:
        if name.startswith(prefix):
            name = name[len(prefix):]
    return name.strip()


def load_owners():
    """Load POC assignments from sop-owners.json."""
    if OWNERS_FILE.exists():
        try:
            with open(OWNERS_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_owners(owners):
    """Write owners file (creates it if missing)."""
    with open(OWNERS_FILE, "w") as f:
        json.dump(owners, f, indent=2, ensure_ascii=False)


def get_mtime(filepath):
    """Get file modification time as YYYY-MM-DD string."""
    try:
        ts = os.path.getmtime(filepath)
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except OSError:
        return ""


def read_gdoc(filepath):
    """Read a .gddoc/.gdsheet file and return the URL."""
    try:
        with open(filepath) as fh:
            data = json.load(fh)
        return data.get("url", "")
    except (json.JSONDecodeError, IOError):
        return None


def scan_roadmaps():
    """Scan Drive for current quarter roadmaps (2026.Q1)."""
    roadmaps = []
    current_q = "2026.Q1"
    alt_q = "26.Q1"

    for root, dirs, files in os.walk(DRIVE_DIR):
        # Skip archive folders
        dirs[:] = [d for d in dirs if d.lower() not in ("z. archive", "deprecated", "old")]

        for f in sorted(files):
            if not f.endswith((".gddoc", ".gdsheet")):
                continue
            if "copy of" in f.lower():
                continue
            # Only current quarter roadmaps
            fname_lower = f.lower()
            path_lower = root.lower()
            is_current = (current_q.lower() in fname_lower or alt_q.lower() in fname_lower
                          or current_q.lower() in path_lower or alt_q.lower() in path_lower)
            if not is_current or "roadmap" not in fname_lower:
                continue

            # Skip chart-only entries (these should be linked from their parent roadmap)
            if fname_lower.endswith("charts.gdsheet") or "- chart" in fname_lower:
                continue

            filepath = Path(root) / f
            url = read_gdoc(filepath)
            if url is None:
                continue

            # Determine entity from path
            rel = str(filepath.relative_to(DRIVE_DIR))
            entity = ""
            if "r202" in rel.lower():
                entity = "R202"
            elif "r203" in rel.lower():
                entity = "R203"
            elif "r204" in rel.lower():
                entity = "R204"
            elif "r888" in rel.lower():
                entity = "R888"
            elif "h5c7" in rel.lower():
                entity = "H5c7"
            elif "rl21" in rel.lower():
                entity = "RL21"
            elif "m5x2" in rel.lower():
                entity = "m5x2"
            elif "c12n" in rel.lower():
                entity = "C12N"
            elif "focus" in rel.lower():
                entity = "Cross"

            display_name = clean_name(f)
            roadmaps.append({
                "name": display_name,
                "url": url,
                "category": "roadmaps",
                "entity": entity,
                "doc_type": "sheet" if f.endswith(".gdsheet") else "doc",
                "path": rel,
                "last_edit": get_mtime(filepath),
                "poc": "",
            })

    return roadmaps


def scan_sops():
    sops = []
    skipped = []
    owners = load_owners()

    # Folders to exclude entirely (entity docs, not SOPs)
    SKIP_FOLDERS = {"z. archive", "deprecated", "old", "banking", "formation",
                    "f648", "l925", "purchase", "mortgage", "insurance"}

    # Files to skip (duplicates, non-SOPs, junk)
    SKIP_PATTERNS = ["copy of ", "untitled", "lease agreement"]

    for root, dirs, files in os.walk(SOPS_DIR):
        dirs[:] = [d for d in dirs if d.lower() not in SKIP_FOLDERS]

        for f in sorted(files):
            filepath = Path(root) / f
            rel_path = str(filepath.relative_to(SOPS_DIR))

            if f.startswith("."):
                continue

            # Skip non-SOP files
            if any(p in f.lower() for p in SKIP_PATTERNS):
                continue

            # Skip h5c7 (lowercase) folder entirely — entity docs, not SOPs
            if rel_path.startswith("h5c7/"):
                continue

            if f.endswith((".gddoc", ".gdsheet")):
                url = read_gdoc(filepath)
                if url is None:
                    skipped.append(rel_path)
                    continue
            elif f.endswith(".docx"):
                url = ""  # no direct link for .docx
            elif f.endswith(".pdf"):
                url = ""
            else:
                continue

            category = categorize(rel_path, f)
            entity = extract_entity(rel_path)
            display_name = clean_name(f)
            doc_type = "sheet" if f.endswith(".gdsheet") else "doc"
            last_edit = get_mtime(filepath)
            poc = owners.get(rel_path, "")

            sops.append({
                "name": display_name,
                "url": url,
                "category": category,
                "entity": entity,
                "doc_type": doc_type,
                "path": rel_path,
                "last_edit": last_edit,
                "poc": poc,
            })

    if skipped:
        print(f"Skipped {len(skipped)} files: {skipped}")

    # Generate/update owners file with all SOP paths
    new_owners = {s["path"]: owners.get(s["path"], "") for s in sops}
    save_owners(new_owners)

    return sops


def generate_html(sops):
    # Group by category
    grouped = {}
    for cat_key in CATEGORIES:
        grouped[cat_key] = [s for s in sops if s["category"] == cat_key]

    total = len(sops)
    linked = sum(1 for s in sops if s["url"])

    sections_html = []
    for cat_key, cat_info in CATEGORIES.items():
        items = grouped.get(cat_key, [])
        if not items:
            continue

        items_html = []
        for sop in items:
            escaped_name = html.escape(sop["name"])
            entity_badge = f'<span class="badge badge-{sop["entity"].lower()}">{sop["entity"]}</span>' if sop["entity"] else ""
            type_icon = "📊" if sop["doc_type"] == "sheet" else ""

            last_edit = sop.get("last_edit", "")
            edit_html = f'<span class="last-edit">{last_edit}</span>' if last_edit else '<span class="last-edit">-</span>'

            poc = html.escape(sop.get("poc", ""))
            poc_html = f'<span class="poc">{poc}</span>' if poc else '<span class="poc empty">-</span>'

            meta_html = f'<span class="sop-meta">{poc_html}{edit_html}</span>'

            if sop["url"]:
                items_html.append(
                    f'<a href="{sop["url"]}" target="_blank" class="sop-item" data-name="{escaped_name.lower()}" data-entity="{sop["entity"].lower()}">'
                    f'<span class="sop-name">{type_icon} {escaped_name}</span>'
                    f'{entity_badge}'
                    f'{meta_html}'
                    f'<span class="link-icon">↗</span>'
                    f'</a>'
                )
            else:
                items_html.append(
                    f'<div class="sop-item no-link" data-name="{escaped_name.lower()}" data-entity="{sop["entity"].lower()}">'
                    f'<span class="sop-name">{escaped_name}</span>'
                    f'{entity_badge}'
                    f'{meta_html}'
                    f'<span class="link-icon file-only">📎</span>'
                    f'</div>'
                )

        sections_html.append(f"""
        <section class="category" id="{cat_key}">
            <div class="category-header">
                <h2>{cat_info['icon']} {cat_info['title']}</h2>
                <p class="category-desc">{cat_info['desc']}</p>
                <span class="count">{len(items)}</span>
            </div>
            <div class="col-headers">
                <span class="col-name">Name</span>
                <span class="col-poc">Owner</span>
                <span class="col-edit">Last Edit</span>
            </div>
            <div class="sop-list">
                {''.join(items_html)}
            </div>
        </section>
        """)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>McKay Capital — Operations Knowledge Base</title>
    <style>
        :root {{
            --bg: #ffffff;
            --surface: #f5f6f8;
            --surface2: #eceef2;
            --border: #e0e2e8;
            --text: #1a1d27;
            --text-muted: #6b7080;
            --accent: #4a6cf7;
            --accent-hover: #3b5de6;
            --r202: #0d9488;
            --r203: #dc2626;
            --r888: #ca8a04;
            --h5c7: #7c3aed;
            --cross: #6b7080;
        }}

        * {{ margin: 0; padding: 0; box-sizing: border-box; }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg);
            color: var(--text);
            line-height: 1.6;
        }}

        .container {{
            max-width: 900px;
            margin: 0 auto;
            padding: 2rem 1.5rem;
        }}

        header {{
            text-align: center;
            margin-bottom: 2.5rem;
            padding-bottom: 2rem;
            border-bottom: 1px solid var(--border);
        }}

        header h1 {{
            font-size: 1.75rem;
            font-weight: 700;
            margin-bottom: 0.5rem;
            letter-spacing: -0.02em;
        }}

        header p {{
            color: var(--text-muted);
            font-size: 0.95rem;
        }}

        .search-container {{
            position: sticky;
            top: 0;
            background: var(--bg);
            padding: 1rem 0;
            z-index: 100;
            border-bottom: 1px solid var(--border);
            margin-bottom: 1.5rem;
        }}

        .search-bar {{
            display: flex;
            gap: 0.75rem;
            align-items: center;
        }}

        #search {{
            flex: 1;
            padding: 0.75rem 1rem;
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 8px;
            color: var(--text);
            font-size: 1rem;
            outline: none;
            transition: border-color 0.2s;
        }}

        #search:focus {{
            border-color: var(--accent);
        }}

        #search::placeholder {{
            color: var(--text-muted);
        }}

        .filter-pills {{
            display: flex;
            gap: 0.5rem;
            flex-wrap: wrap;
        }}

        .pill {{
            padding: 0.35rem 0.75rem;
            border-radius: 20px;
            border: 1px solid var(--border);
            background: transparent;
            color: var(--text-muted);
            font-size: 0.8rem;
            cursor: pointer;
            transition: all 0.2s;
        }}

        .pill:hover, .pill.active {{
            background: var(--accent);
            color: #fff;
            border-color: var(--accent);
        }}

        .category {{
            margin-bottom: 2rem;
        }}

        .category-header {{
            display: flex;
            align-items: baseline;
            gap: 0.75rem;
            flex-wrap: wrap;
            margin-bottom: 0.75rem;
            padding-bottom: 0.5rem;
            border-bottom: 1px solid var(--border);
        }}

        .category-header h2 {{
            font-size: 1.2rem;
            font-weight: 600;
        }}

        .category-desc {{
            color: var(--text-muted);
            font-size: 0.85rem;
            flex: 1;
        }}

        .count {{
            color: var(--text-muted);
            font-size: 0.8rem;
            background: var(--surface);
            padding: 0.2rem 0.6rem;
            border-radius: 12px;
        }}

        .sop-list {{
            display: flex;
            flex-direction: column;
            gap: 2px;
        }}

        .sop-item {{
            display: flex;
            align-items: center;
            gap: 0.75rem;
            padding: 0.65rem 1rem;
            border-radius: 6px;
            text-decoration: none;
            color: var(--text);
            transition: background 0.15s;
        }}

        a.sop-item:hover {{
            background: var(--surface2);
        }}

        .sop-name {{
            flex: 1;
            font-size: 0.95rem;
        }}

        .badge {{
            font-size: 0.7rem;
            font-weight: 600;
            padding: 0.15rem 0.5rem;
            border-radius: 4px;
            letter-spacing: 0.03em;
        }}

        .badge-r202 {{ background: rgba(13, 148, 136, 0.1); color: var(--r202); }}
        .badge-r203 {{ background: rgba(220, 38, 38, 0.1); color: var(--r203); }}
        .badge-r204 {{ background: rgba(234, 88, 12, 0.1); color: #ea580c; }}
        .badge-r888 {{ background: rgba(202, 138, 4, 0.1); color: var(--r888); }}
        .badge-h5c7 {{ background: rgba(124, 58, 237, 0.1); color: var(--h5c7); }}
        .badge-rl21 {{ background: rgba(37, 99, 235, 0.1); color: #2563eb; }}
        .badge-m5x2 {{ background: rgba(15, 23, 42, 0.1); color: #334155; }}
        .badge-c12n {{ background: rgba(219, 39, 119, 0.1); color: #db2777; }}
        .badge-cross {{ background: rgba(107, 112, 128, 0.1); color: var(--cross); }}

        .col-headers {{
            display: flex;
            align-items: center;
            gap: 0.75rem;
            padding: 0.35rem 1rem;
            font-size: 0.7rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-muted);
            border-bottom: 1px solid var(--border);
        }}

        .col-name {{ flex: 1; }}
        .col-poc {{ width: 100px; text-align: left; }}
        .col-edit {{ width: 90px; text-align: right; }}

        .sop-meta {{
            display: flex;
            align-items: center;
            gap: 0;
        }}

        .poc {{
            width: 100px;
            font-size: 0.8rem;
            color: var(--text-muted);
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}

        .poc.empty {{
            color: var(--border);
        }}

        .last-edit {{
            width: 90px;
            font-size: 0.75rem;
            color: var(--text-muted);
            text-align: right;
            font-variant-numeric: tabular-nums;
        }}

        .link-icon {{
            color: var(--accent);
            font-size: 0.85rem;
            opacity: 0;
            transition: opacity 0.15s;
            width: 20px;
            text-align: center;
        }}

        .sop-item:hover .link-icon {{
            opacity: 1;
        }}

        .link-icon.file-only {{
            opacity: 0.4;
        }}

        .no-link {{
            opacity: 0.6;
        }}

        .no-results {{
            text-align: center;
            padding: 3rem 1rem;
            color: var(--text-muted);
            display: none;
        }}

        .stats {{
            text-align: center;
            color: var(--text-muted);
            font-size: 0.8rem;
            margin-top: 2rem;
            padding-top: 1.5rem;
            border-top: 1px solid var(--border);
        }}

        .hidden {{ display: none !important; }}

        @media (max-width: 600px) {{
            .container {{ padding: 1rem; }}
            header h1 {{ font-size: 1.3rem; }}
            .category-desc {{ display: none; }}
            .sop-item {{ padding: 0.75rem; }}
            .link-icon {{ opacity: 1; }}
            .sop-meta, .col-poc, .col-edit {{ display: none; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>McKay Capital — Operations</h1>
            <p>{total} SOPs &middot; {linked} linked to Google Docs</p>
        </header>

        <div class="search-container">
            <div class="search-bar">
                <input type="text" id="search" placeholder="Search SOPs..." autocomplete="off">
            </div>
            <div class="filter-pills" style="margin-top: 0.5rem;">
                <button class="pill active" data-filter="all">All</button>
                <button class="pill" data-filter="r202">R202</button>
                <button class="pill" data-filter="r203">R203</button>
                <button class="pill" data-filter="r888">R888</button>
                <button class="pill" data-filter="h5c7">H5c7</button>
            </div>
        </div>

        {''.join(sections_html)}

        <div class="no-results" id="no-results">
            No SOPs match your search.
        </div>

        <div class="stats">
            McKay Capital &middot; Last built {get_build_date()}
        </div>
    </div>

    <script>
        const search = document.getElementById('search');
        const items = document.querySelectorAll('.sop-item');
        const sections = document.querySelectorAll('.category');
        const noResults = document.getElementById('no-results');
        const pills = document.querySelectorAll('.pill');
        let activeFilter = 'all';

        function applyFilters() {{
            const query = search.value.toLowerCase().trim();
            let visibleCount = 0;

            items.forEach(item => {{
                const name = item.dataset.name || '';
                const entity = item.dataset.entity || '';
                const matchesSearch = !query || name.includes(query);
                const matchesFilter = activeFilter === 'all' || entity === activeFilter;
                const visible = matchesSearch && matchesFilter;
                item.classList.toggle('hidden', !visible);
                if (visible) visibleCount++;
            }});

            sections.forEach(section => {{
                const hasVisible = section.querySelectorAll('.sop-item:not(.hidden)').length > 0;
                section.classList.toggle('hidden', !hasVisible);
            }});

            noResults.style.display = visibleCount === 0 ? 'block' : 'none';
        }}

        search.addEventListener('input', applyFilters);

        pills.forEach(pill => {{
            pill.addEventListener('click', () => {{
                pills.forEach(p => p.classList.remove('active'));
                pill.classList.add('active');
                activeFilter = pill.dataset.filter;
                applyFilters();
            }});
        }});

        // Keyboard shortcut: / to focus search
        document.addEventListener('keydown', (e) => {{
            if (e.key === '/' && document.activeElement !== search) {{
                e.preventDefault();
                search.focus();
            }}
            if (e.key === 'Escape') {{
                search.value = '';
                search.blur();
                applyFilters();
            }}
        }});
    </script>
    {get_analytics_snippet()}
</body>
</html>"""


def get_analytics_snippet():
    if not ANALYTICS_URL:
        return "<!-- Analytics: set ANALYTICS_URL in build.py to enable -->"
    return f"""<script>
    (function() {{
        var u = '{ANALYTICS_URL}';
        var s = screen.width + 'x' + screen.height;
        var h = 0;
        var email = (document.cookie.match(/email=([^;]+)/) || ['','anon'])[1];
        // Simple hash for pseudo-anonymous user tracking
        for (var i = 0; i < navigator.userAgent.length; i++) {{
            h = ((h << 5) - h) + navigator.userAgent.charCodeAt(i);
            h |= 0;
        }}
        new Image().src = u + '?u=' + Math.abs(h) + '&s=' + s + '&r=' + encodeURIComponent(document.referrer) + '&t=' + Date.now();
    }})();
    </script>"""


def get_build_date():
    return datetime.now().strftime("%Y-%m-%d")


def main():
    print(f"Scanning {SOPS_DIR}...")
    sops = scan_sops()

    print(f"Scanning {DRIVE_DIR} for roadmaps...")
    roadmaps = scan_roadmaps()

    all_items = roadmaps + sops
    print(f"Found {len(sops)} SOPs + {len(roadmaps)} roadmaps = {len(all_items)} total")

    for cat_key, cat_info in CATEGORIES.items():
        count = sum(1 for s in all_items if s["category"] == cat_key)
        if count:
            print(f"  {cat_info['icon']} {cat_info['title']}: {count}")

    html_content = generate_html(all_items)

    # Write to build dir and copy to Drive
    out_path = Path(__file__).parent / "index.html"
    drive_path = SOPS_DIR / "index.html"
    for p in [out_path, drive_path]:
        with open(p, "w") as f:
            f.write(html_content)

    print(f"\nGenerated {out_path} ({len(html_content):,} bytes)")
    print(f"Copied to {drive_path}")


if __name__ == "__main__":
    main()
