"""JM 2026-11-09: "for Janus - can we make it so that DNS (Dns) blocks don't
show up?" Outlook 'Do Not Schedule' holds arrive titled "DNS" or "Dns - Work"
(from the Agency fetch and the MSFT Slow Sync ICS import alike). fetch_gcal
drops them before STATE.events, so no downstream site (timeline rows, gutter
busy-bar, block heads, reclaim) ever sees one. Real meetings that merely
start with DNS are kept."""
import datetime as dtm
import importlib.util
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_dns", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_dns"] = mod
    spec.loader.exec_module(mod)
    return mod


def _ev(title, h):
    start = dtm.datetime.now(TZ).replace(hour=h, minute=0, second=0, microsecond=0)
    return {"title": title, "start_dt": start, "end_dt": start + dtm.timedelta(minutes=60),
            "calendar": "Outlook", "all_day": False, "transparency": "opaque"}


def test_is_dns_hold_matches_holds_not_real_meetings():
    mod = _load_tui()
    assert mod._is_dns_hold("DNS") and mod._is_dns_hold("Dns - Work") and mod._is_dns_hold(" dns: focus ")
    assert not mod._is_dns_hold("DNS migration review") and not mod._is_dns_hold("") and not mod._is_dns_hold(None)


def test_fetch_gcal_drops_dns_holds_from_state_events(monkeypatch, tmp_path):
    mod = _load_tui()
    mod.HIDDEN_EVENTS = tmp_path / "hidden.json"
    monkeypatch.setattr(mod.gcal_client, "list_events",
                        lambda s, e, force=False: [_ev("Dns - Work", 8), _ev("Standup", 9)])
    monkeypatch.setattr(mod.outlook_client, "list_events",
                        lambda s, e, force=False: [_ev("DNS", 13), _ev("DNS migration review", 15)])
    monkeypatch.setattr(mod, "_shorten_events", lambda evs: None)
    mod.fetch_gcal()
    assert [e["title"] for e in mod.STATE.events] == ["Standup", "DNS migration review"]
