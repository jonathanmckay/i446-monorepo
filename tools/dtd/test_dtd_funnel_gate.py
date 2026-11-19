#!/usr/bin/env python3
"""Funnel gate (2026-11-19): dtd's API is published through Tailscale Funnel so
the iPhone app works with the VPN off. Everything arriving on that listener
must be /api/*, carry an allowed device's bearer token, and be rate-limited on
failures; the where beacon must credit the token's device."""
import io
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dtd  # noqa: E402

GOOD, OTHER = "a" * 64, "b" * 64
tmp = Path(tempfile.mkdtemp()) / "tokens.json"
tmp.write_text(json.dumps({"imago-c": GOOD, "straylight-dev": OTHER, "imago": ""}))
dtd.FUNNEL_TOKENS = tmp

fails = 0


def check(cond, name):
    global fails
    print(("ok   " if cond else "FAIL ") + name)
    fails += 0 if cond else 1


def call(gate, path="/api/ping", token=None, method="GET", body=b"", fwd="203.0.113.9"):
    env = {"REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": "", "SERVER_NAME": "x",
           "SERVER_PORT": "1", "wsgi.url_scheme": "http", "wsgi.input": io.BytesIO(body),
           "CONTENT_LENGTH": str(len(body)), "CONTENT_TYPE": "application/json",
           "REMOTE_ADDR": "127.0.0.1", "HTTP_X_FORWARDED_FOR": fwd, "wsgi.errors": sys.stderr,
           "wsgi.version": (1, 0), "wsgi.multithread": True, "wsgi.multiprocess": False,
           "wsgi.run_once": False, "SERVER_PROTOCOL": "HTTP/1.1"}
    if token is not None:
        env["HTTP_AUTHORIZATION"] = "Bearer " + token
    out = {}
    data = b"".join(gate(env, lambda status, headers, exc=None: out.update(status=status)))
    return int(out["status"].split()[0]), data, env


reached = []


def inner(environ, start_response):
    reached.append(environ.get("dtd.funnel_device"))
    start_response("200 OK", [("Content-Type", "application/json")])
    return [b'{"ok": true}']


g = dtd.FunnelGate(inner)
check(call(g)[0] == 401 and not reached, "no token -> 401, app not reached")
check(call(g, token="c" * 64)[0] == 401 and not reached, "wrong token -> 401")
check(call(g, token="")[0] == 401 and not reached, "empty token never matches an empty entry")
check(call(g, token=OTHER)[0] == 401 and not reached, "a valid vault token for a device not on the allowlist -> 401")
st, data, _ = call(g, token=GOOD)
check(st == 200 and reached == ["imago-c"], "phone token -> through, device attached")
check(call(g, path="/", token=GOOD)[0] == 404, "web page is not served through the Funnel")
check(call(g, path="/apix", token=GOOD)[0] == 404, "only /api/ paths")
check(json.loads(call(g)[1]) == {"ok": False, "error": "unauthorized"}, "401 body is the app's error JSON")

g2 = dtd.FunnelGate(inner)
codes = [call(g2, token="z", fwd="198.51.100.1")[0] for _ in range(dtd.FUNNEL_FAILS_PER_MIN + 2)]
check(codes[:dtd.FUNNEL_FAILS_PER_MIN] == [401] * dtd.FUNNEL_FAILS_PER_MIN and codes[-1] == 429, "failed attempts -> 429")
check(call(g2, token=GOOD, fwd="198.51.100.1")[0] == 429, "a locked-out client stays out even with a good token")
check(call(g2, token=GOOD, fwd="198.51.100.2")[0] == 200, "other clients are unaffected")

dtd.FUNNEL_TOKENS = tmp.parent / "missing.json"
check(call(dtd.FunnelGate(inner), token=GOOD)[0] == 401, "missing tokens file fails closed")
dtd.FUNNEL_TOKENS = tmp

# where beacon: device from the token, not the (public) peer IP
import where  # noqa: E402
rec = []
where.record = lambda dev, tz, now=None: rec.append((dev, tz)) or True
where.record_peer = lambda ip, tz: rec.append(("PEER", ip)) or None
st, data, _ = call(dtd.FunnelGate(dtd.app.wsgi_app), path="/api/where", token=GOOD, method="POST",
                   body=json.dumps({"tz": "Asia/Tokyo"}).encode())
check(st == 200 and rec == [("imago-c", "Asia/Tokyo")] and json.loads(data)["device"] == "imago-c",
      "where beacon through the Funnel records the token's device")
rec.clear()
c = dtd.app.test_client()
c.post("/api/where", json={"tz": "Asia/Tokyo"})
check(rec and rec[0][0] == "PEER", "direct requests still resolve the device by peer IP")

sys.exit(1 if fails else 0)
