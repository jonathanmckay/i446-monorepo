"""wifi-portal against a local fake captive portal: picks the free connect
over a priced pass, fills the login, stops on 2FA, and only ever acts on
allowlisted airline hosts."""
import http.server
import importlib.util
import secrets
import threading
from pathlib import Path

import pytest

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("portal", HERE / "portal.py")
portal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(portal)

PAGES = {
    "/terms": """<html><body>
      <label><input type=checkbox id=t> I agree to the Terms of Use</label>
      <button onclick="location='/bought'">Buy 24-hour pass $19</button>
      <button onclick="if(document.getElementById('t').checked)location='/ok'">
        Connect for free</button></body></html>""",
    "/login": """<html><body><form action=/ok method=get>
      <input type=text name=mp placeholder="MileagePlus number">
      <input type=password name=pw>
      <button type=submit>Sign in</button></form></body></html>""",
    "/otp": """<html><body><input name=verification_code>
      <button onclick="location='/ok'">Continue</button></body></html>""",
    "/upsell": """<html><body><button>Subscribe: monthly plan</button>
      <a href=/bought>Purchase</a></body></html>""",
}


class _H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/ok", "/bought"):
            self.server.hits.append(self.path)
            if path == "/ok":
                self.server.online = True
        if path == "/hotspot":
            self.send_response(302)
            self.send_header("Location", "http://www.unitedwifi.com/portal")
            self.end_headers()
            return
        body = PAGES.get(path, "<html>done</html>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def srv():
    s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    s.online, s.hits = False, []
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s
    s.shutdown()


def _url(s, path):
    return f"http://127.0.0.1:{s.server_address[1]}{path}"


def test_ticks_terms_and_takes_free_over_paid(srv, tmp_path):
    r = portal.drive(_url(srv, "/terms"), None, tmp_path, lambda: srv.online)
    assert r == "online"
    assert not any(h.startswith("/bought") for h in srv.hits)
    assert (tmp_path / "00.png").exists() and (tmp_path / "00.html").exists()


def test_fills_login_from_creds(srv, tmp_path):
    # Random per run, never a literal pair: GitGuardian flagged the old
    # hardcoded ("123456789", "s3cret") tuple as an Authentication Tuple
    # (2026-10-27 false positive).
    fake = (secrets.token_hex(4), secrets.token_hex(8))
    r = portal.drive(_url(srv, "/login"), fake, tmp_path, lambda: srv.online)
    assert r == "online"
    assert any(f"mp={fake[0]}" in h and f"pw={fake[1]}" in h for h in srv.hits)


def test_stops_on_2fa(srv, tmp_path):
    assert portal.drive(_url(srv, "/otp"), None, tmp_path, lambda: srv.online) == "needs_2fa"
    assert srv.hits == []


def test_never_clicks_purchase_only_page(srv, tmp_path):
    assert portal.drive(_url(srv, "/upsell"), None, tmp_path, lambda: srv.online) == "stuck"
    assert srv.hits == []


def test_dry_run_clicks_nothing(srv, tmp_path):
    r = portal.drive(_url(srv, "/terms"), None, tmp_path, lambda: srv.online, dry_run=True)
    assert r == "dry_run" and srv.hits == []


def test_probe_reads_redirect_target(srv):
    online, url = portal.probe(_url(srv, "/hotspot"))
    assert not online and url == "http://www.unitedwifi.com/portal"


@pytest.mark.parametrize("url,want", [
    ("http://www.unitedwifi.com/portal", "wifi-portal-united"),
    ("https://alaskawifi.com/", "wifi-portal-alaska"),
    ("http://unitedwifi.com.evil.example/", None),
    ("http://hotel-portal.marriott.com/", None),
    (None, None),
])
def test_allowlist(url, want):
    assert portal.airline_for(url) == want


@pytest.mark.parametrize("label,ok", [
    ("Connect for free", True),
    ("Connect for $8", False),
    ("Get started", True),
    ("Buy a pass", False),
    ("Monthly plan", False),
    ("Sign in", True),
])
def test_button_rules(label, ok):
    assert (bool(portal.GO.search(label)) and not portal.BLOCK.search(label)) == ok


def test_non_allowlisted_portal_is_left_alone(monkeypatch):
    monkeypatch.setattr(portal, "DISABLED", Path("/nonexistent/x"))
    monkeypatch.setattr(portal, "probe", lambda *a, **k: (False, "http://hotel.example/login"))
    called = []
    monkeypatch.setattr(portal, "drive", lambda *a, **k: called.append(1))
    assert portal.run() == 0 and called == []
