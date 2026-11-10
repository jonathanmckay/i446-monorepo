#!/usr/bin/env python3
"""lease_signer: unit parsing from AppFolio countersign emails, and the
countersign success check (2026-11-09: both were wrong in the same way, trusting
loose patterns; see the module comments)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lease_signer as ls  # noqa: E402


@pytest.mark.parametrize("subject,unit,prop", [
    ("Fwd: Lease ready to be countersigned for k104- LeFevre - 512-3", "k104- LeFevre - 512-3", "k104"),
    ("Fwd: Lease renewal ready to be countersigned for rl16 - C09", "rl16 - C09", "rl16"),
    ("Fwd: Lease ready to be countersigned for kn47 K1 - L102", "kn47 K1 - L102", "kn47"),
    ("Fwd: Lease renewal ready to be countersigned for m608 - 7", "m608 - 7", "m608"),
])
def test_unit_and_property_from_subject(subject, unit, prop):
    meta = ls.parse_email_metadata({"preview": subject, "body": "Tenant Mark A. Buck\r\nUnit k104- LeFevre - 512-3\r\nView and Countersign Lease"})
    assert meta["unit"] == unit and meta["property"] == prop


def test_unit_falls_back_to_body_and_tenants_parse():
    body = "Details\r\nTenants Brian McMillin, Arthur McMillin\r\nUnit rl16 - C09\r\nView and Countersign Lease Renewal\r\n"
    meta = ls.parse_email_metadata({"preview": "Fwd: a renewal", "body": body})
    assert meta["unit"] == "rl16 - C09" and meta["property"] == "rl16"
    assert meta["tenants"] == "Brian McMillin, Arthur McMillin"
    assert meta["lease_type"] == "renewal"


LOGIN = "Log in to your account Email Password Forgot password? Trying to log in as a resident or owner? Complete the form. Confirmed devices"
EXECUTED = "Lease Preview for Mark A. Buck Lease Status: Fully Executed Sent on 11/06/2026 at 04:51 pm Signed on 11/09/2026 at 05:14 pm"


def test_success_requires_executed_status_on_tenant_host():
    assert ls._success_from_text(EXECUTED, "https://mckay.appfolio.com/lease_documents/6924#28")
    assert ls._success_from_text("Signed on 11/07/2026 at 09:38 am", "https://mckay.appfolio.com/lease_documents/6911")
    # the Keycloak login page contains "complete" and "confirmed": not a signature
    assert not ls._success_from_text(LOGIN, "https://account.appfolio.com/realms/property/protocol/openid-connect/auth?x=1")
    assert not ls._success_from_text(LOGIN + " signed", "https://mckay.appfolio.com/")
    assert not ls._success_from_text("Thank you! Your request is complete.", "https://mckay.appfolio.com/lease_documents/1")
    assert not ls._success_from_text(EXECUTED, "https://evil.example.com/")
