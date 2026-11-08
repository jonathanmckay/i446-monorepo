"""lease_policy — decide which AppFolio countersign forwards may auto-sign.

Policy (2026-11-08, after the daemon countersigned a rent-correction addendum
and a renewal on 2026-11-07): ONLY new leases auto-sign. Renewals, addenda,
amendments, rent changes/corrections, MTM conversions, re-signs, unknown
AppFolio templates and anything whose unit can't be parsed are HELD for JM.

Why the forwarder's note matters: AppFolio sends an addendum through the
*new-lease* template ("Lease ready to be countersigned for ...", heading
"A New Lease Document Is Ready To Be Countersigned"). The only signal that
it was an addendum was the forwarder's own line above the forwarded block.

Pure module (no Playwright / Gmail imports) so it is unit-testable.
"""
from __future__ import annotations

import html as _html
import re

# Subject of AppFolio's new-lease template, with any number of Re:/Fwd: prefixes.
_PREFIX = r"^\s*(?:(?:re|fwd?|fw)\s*:\s*)*"
_NEW_LEASE_SUBJECT_RE = re.compile(_PREFIX + r"lease ready to be countersigned for\b", re.I)
_SUBJECT_UNIT_RE = re.compile(r"countersigned for\s+(.+?)\s*$", re.I)
_BODY_UNIT_RE = re.compile(r"^[ \t>*]*Unit:?[ \t]+(.+?)[ \t]*$", re.I | re.M)
_PROPERTY_RE = re.compile(r"^([a-z]+\d+)", re.I)
# AppFolio's template heading, e.g. "A New Lease Renewal Document Is Ready To Be Countersigned".
_HEADING_RE = re.compile(r"^[ \t>*]*(A New .*?Document Is Ready To Be Countersigned)", re.I | re.M)
_NEW_LEASE_HEADING_RE = re.compile(r"^A New Lease Document Is Ready To Be Countersigned$", re.I)
_FWD_MARKER_RE = re.compile(r"-{5,}\s*Forwarded message\s*-{5,}|Begin forwarded message:", re.I)
_URL_RE = re.compile(r"<?https?://\S+>?")
_TAG_RE = re.compile(r"<[^>]+>")

# Word-bounded so URL tokens / footers ("notification settings") can't trip them.
HOLD_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("renewal", re.compile(r"\brenew\w*", re.I)),
    ("addendum", re.compile(r"\baddend\w*", re.I)),
    ("amendment", re.compile(r"\bamend\w*", re.I)),
    ("rent change", re.compile(r"\brent\s+(?:change|correction|increase|decrease|adjust\w*|reduction)\b", re.I)),
    ("correction", re.compile(r"\bcorrect(?:ion|ed|ing)\b", re.I)),
    ("month-to-month", re.compile(r"\bmonth[\s-]*to[\s-]*month\b|\bmtm\b", re.I)),
    ("re-sign", re.compile(r"\bre-?sign(?:ed|ing)?\b|\bresign(?:ed|ing)?\b", re.I)),
    ("extension", re.compile(r"\bextension\b|\bextend(?:ed|ing)?\b", re.I)),
    ("modification", re.compile(r"\bmodification\b", re.I)),
]


def _clean(text: str) -> str:
    return _URL_RE.sub(" ", text or "")


def html_to_text(html_body: str) -> str:
    if not html_body:
        return ""
    t = re.sub(r"(?i)<br\s*/?>|</(?:p|div|tr|li|h\d)>", "\n", html_body)
    return _html.unescape(_TAG_RE.sub(" ", t))


def forwarder_note(body: str) -> str:
    """Text the forwarder typed above the forwarded AppFolio message."""
    m = _FWD_MARKER_RE.search(body or "")
    return (body or "")[: m.start()] if m else ""


def parse_unit(subject: str, body: str = "") -> str:
    """'c702 - K102', 'o155-Elm - F', 'kn47 K1 - L102' ... or '' if unparseable."""
    m = _SUBJECT_UNIT_RE.search(subject or "")
    unit = m.group(1).strip() if m else ""
    if not unit:
        m = _BODY_UNIT_RE.search(body or "")
        unit = m.group(1).strip() if m else ""
    # A unit must start with a property code (letters+digits) and name a unit.
    if not _PROPERTY_RE.match(unit) or not re.search(r"-\s*\S", unit):
        return ""
    return unit


def parse_property(unit: str) -> str:
    m = _PROPERTY_RE.match(unit or "")
    return m.group(1).lower() if m else ""


def classify(subject: str, body: str = "", html_body: str = "") -> dict:
    """Return {"doc_type", "unit", "property", "hold_reason"}.

    hold_reason is None only for a parseable new lease with no hold signal
    in the subject, the forwarder's note or the AppFolio template heading.
    """
    subject = subject or ""
    texts = [t for t in (body or "", html_to_text(html_body)) if t]
    unit = parse_unit(subject, "\n".join(texts))

    # Scan only short, human/template-authored regions — never whole bodies
    # (tracking URLs, tenant names, footers).
    headings = []
    scan = [subject]
    for t in texts:
        scan.append(forwarder_note(t))
        headings += [h.strip() for h in _HEADING_RE.findall(t)]
    scan += headings
    scan_text = _clean("\n".join(scan))

    signals = [label for label, rx in HOLD_PATTERNS if rx.search(scan_text)]
    if signals:
        doc_type = signals[0]
    elif not _NEW_LEASE_SUBJECT_RE.search(subject):
        doc_type = "unknown"
    elif headings and not all(_NEW_LEASE_HEADING_RE.match(h) for h in headings):
        doc_type = "unknown"
    else:
        doc_type = "new"

    if doc_type != "new":
        reason = f"not a new lease ({', '.join(signals) or 'unrecognised AppFolio template'})"
    elif not unit:
        reason = "unit could not be parsed"
    else:
        reason = None

    return {
        "doc_type": doc_type,
        "unit": unit,
        "property": parse_property(unit),
        "hold_reason": reason,
    }
