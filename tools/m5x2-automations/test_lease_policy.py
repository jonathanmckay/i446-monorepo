"""Regression for the 2026-11-07 auto-signs: lease_signerd countersigned a
rent-correction ADDENDUM (sent through AppFolio's new-lease template) and a
renewal whose unit logged blank. Policy since 2026-11-08: only parseable new
leases auto-sign; everything else is held for JM.

Fixtures are synthetic: unit codes follow the real formats, tenant names and
URLs are fake (repo is public)."""
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lease_policy as P  # noqa: E402

FWD = "\r\n\r\n---------- Forwarded message ---------\r\nFrom: Example Homes (Do Not Reply) <donotreply@appfolio.com>\r\n"


def appfolio_body(heading: str, unit: str, note: str = "", colon: bool = False) -> str:
    sep = ":" if colon else ""
    return (
        f"{note}{FWD}Subject: x\r\n[image: AppFolio Property Manager]\r\n{heading}\r\nDetails\r\n"
        f"Tenants{sep} Alex Example, Sam Sample\r\nUnit{sep} {unit}\r\n"
        "View and Countersign Lease\r\n<https://sg.appfolio.com/ls/click?upn=u001.RENEWxAMENDxMTMxresign-3D>\r\n"
        "If you would like to stop receiving these emails, please click here\r\nto change your notification settings.\r\n"
    )


NEW_HEAD = "A New Lease Document Is Ready To Be Countersigned"
REN_HEAD = "A New Lease Renewal Document Is Ready To Be Countersigned"


@pytest.mark.parametrize("unit", [
    "c702 - K102", "o155-Elm - F", "k104- LeFevre - 503-3", "kn47-k3 - K104",
    "kn47 K1 - L102", "w117 - 5", "ps25 - D28",
])
def test_unit_formats_parse(unit):
    assert P.parse_unit(f"Fwd: Lease ready to be countersigned for {unit}") == unit


def test_property_code():
    assert P.parse_property("k104- LeFevre - 503-3") == "k104"
    assert P.parse_property("o155-Elm - F") == "o155"
    assert P.parse_property("kn47 K1 - L102") == "kn47"


def test_new_lease_auto_signs():
    v = P.classify("Fwd: Lease ready to be countersigned for c702 - K102",
                   appfolio_body(NEW_HEAD, "c702 - K102"))
    assert v["doc_type"] == "new" and v["hold_reason"] is None
    assert v["unit"] == "c702 - K102" and v["property"] == "c702"


def test_reply_chain_new_lease_still_signs():
    v = P.classify("Re: Fwd: Lease ready to be countersigned for rl16 - C20",
                   appfolio_body(NEW_HEAD, "rl16 - C20", "Re-sent link below."))
    assert v["hold_reason"] is None


def test_rent_correction_addendum_in_new_lease_template_is_held():
    # The 2026-11-07 kn47 K1 L102 case: subject + heading say "new lease".
    note = "This rent correction addendum is ready to countersign.\r\nIt sets rent at $1,000 for 1/1/27 - 12/31/27."
    v = P.classify("Fwd: Lease ready to be countersigned for kn47 K1 - L102",
                   appfolio_body(NEW_HEAD, "kn47 K1 - L102", note))
    assert v["hold_reason"] and v["doc_type"] == "addendum"


def test_addendum_note_only_in_html_is_held():
    html = ("<div>This rent correction addendum is ready.</div>"
            "<div>---------- Forwarded message ---------</div><p>A New Lease Document Is Ready To Be Countersigned</p>")
    v = P.classify("Fwd: Lease ready to be countersigned for c702 - B101",
                   appfolio_body(NEW_HEAD, "c702 - B101"), html)
    assert v["hold_reason"]


def test_renewal_is_held():
    v = P.classify("Fwd: Lease renewal ready to be countersigned for rl16 - C09",
                   appfolio_body(REN_HEAD, "rl16 - C09", "This renewal is ready to countersign."))
    assert v["doc_type"] == "renewal" and v["hold_reason"]
    assert v["unit"] == "rl16 - C09"  # the unit that logged blank on 11-07


@pytest.mark.parametrize("note,label", [
    ("Converting to month-to-month, please countersign.", "month-to-month"),
    ("MTM conversion for this unit.", "month-to-month"),
    ("Tenant had to re-sign after a typo.", "re-sign"),
    ("Rent increase per notice.", "rent change"),
    ("Lease amendment adding a pet.", "amendment"),
    ("Lease extension through June.", "extension"),
])
def test_other_non_new_docs_are_held(note, label):
    v = P.classify("Fwd: Lease ready to be countersigned for tc68 - A15",
                   appfolio_body(NEW_HEAD, "tc68 - A15", note))
    assert v["doc_type"] == label and v["hold_reason"]


def test_unknown_template_is_held():
    v = P.classify("Fwd: Document ready to be countersigned for c313 - 7",
                   appfolio_body("A New Notice Document Is Ready To Be Countersigned", "c313 - 7"))
    assert v["doc_type"] == "unknown" and v["hold_reason"]


def test_blank_unit_is_held():
    v = P.classify("Fwd: Lease ready to be countersigned for ",
                   appfolio_body(NEW_HEAD, "").replace("Unit ", "Unit"))
    assert v["unit"] == "" and v["hold_reason"] == "unit could not be parsed"


def test_unit_falls_back_to_body_line():
    v = P.classify("Fwd: Lease ready to be countersigned",
                   appfolio_body(NEW_HEAD, "kn47 K2 - C203", colon=True))
    assert v["unit"] == "kn47 K2 - C203"


def test_url_and_footer_tokens_do_not_trigger_holds():
    # Tracking URLs above contain RENEW/AMEND/MTM/resign; footer says "settings".
    v = P.classify("Fwd: Lease ready to be countersigned for c702 - G202",
                   appfolio_body(NEW_HEAD, "c702 - G202"))
    assert v["hold_reason"] is None


# ---- daemon wiring: a held doc never reaches sign_lease -------------------

@pytest.fixture
def daemon(monkeypatch):
    pytest.importorskip("playwright")
    stubs = {
        "ibx": types.SimpleNamespace(get_or_create_label=lambda s, n: "L1", archive=lambda s, i: None),
        "todoist": types.SimpleNamespace(create_task=lambda *a, **k: {}, close_task=lambda *a: None),
        "automations_db": types.SimpleNamespace(),
    }
    for name, ns in stubs.items():
        mod = types.ModuleType(name)
        mod.__dict__.update(vars(ns))
        monkeypatch.setitem(sys.modules, name, mod)
    calls = {"log": [], "sign": 0, "sent": [], "modify": []}
    sys.modules["automations_db"].log_signing = lambda db, **kw: calls["log"].append(kw)
    sys.modules["automations_db"].count_successful = lambda db: 1
    sys.path.insert(0, str(HERE.parent / "ibx"))
    monkeypatch.delitem(sys.modules, "lease_signerd", raising=False)
    import lease_signerd as d

    def fake_sign(url, headless=True):
        calls["sign"] += 1
        return {"status": "success"}
    monkeypatch.setattr(d._signer, "sign_lease", fake_sign)
    monkeypatch.setattr(d, "_clear_auth_alert", lambda: None)

    class Svc:
        def users(self): return self
        def messages(self): return self
        def modify(self, **kw): calls["modify"].append(kw); return self
        def send(self, **kw): calls["sent"].append(kw); return self
        def execute(self): return {}
    return d, Svc(), calls


def _item(subject, body):
    return {"type": "email", "from": "Staff <staff@example.com>", "preview": subject, "body": body,
            "_data": {"email": {"id": "m1", "html_body": ""}}}


def test_daemon_holds_renewal_without_signing(daemon):
    d, svc, calls = daemon
    s = "Fwd: Lease renewal ready to be countersigned for rl16 - C09"
    assert d.process_email(svc, _item(s, appfolio_body(REN_HEAD, "rl16 - C09"))) == "held"
    assert calls["sign"] == 0
    assert calls["log"][0]["status"] == "held"
    assert calls["modify"] and calls["sent"]  # labelled for review + JM alerted


def test_daemon_signs_new_lease(daemon):
    d, svc, calls = daemon
    s = "Fwd: Lease ready to be countersigned for c702 - K102"
    assert d.process_email(svc, _item(s, appfolio_body(NEW_HEAD, "c702 - K102"))) == "success"
    assert calls["sign"] == 1 and calls["log"][0]["unit"] == "c702 - K102"
