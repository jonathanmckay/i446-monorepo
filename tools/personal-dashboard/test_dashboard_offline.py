"""Offline + mount-prefix shim and the 媒 reads card (2026-11-12: JM "jm dash
should work offline", "jm dash should have a good hcmc (ie jm reads").

The iPhone app loads the dashboard at https://ix.tail9c51d5.ts.net/personal/;
tailscale serve strips /personal before proxying, so the pages' absolute
'/api/...' fetches 404 at the tailnet root (the iOS shell rendered with no
data). Every page now fetches JSON through cachedJSON(), defined in the shared
<head>: it prepends the mount prefix, mirrors each body into localStorage and
serves that copy, badged "offline · data from HH:MM", when the network fails.
These tests pin the template contract and run the shim itself under node."""
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent


@pytest.fixture(scope="module")
def d():
    spec = importlib.util.spec_from_file_location("dashboard_offline", HERE / "dashboard.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dashboard_offline"] = mod
    spec.loader.exec_module(mod)
    return mod


def _pages(d):
    return {"/": d.HTML, "/slice": d.SLICE_HTML, "/more": d.MORE_HTML, "/1s": d.WEEKLY_HTML,
            "/jmreads": d.jmreads.page(d._SHARED_STYLE)}


def test_every_page_fetches_through_the_offline_helper(d):
    for name, html in _pages(d).items():
        head, body = html.split("</head>", 1)
        assert "const PREFIX" in head and "function cachedJSON" in head, name
        assert not re.search(r"""fetch\(['"`]/api""", body), f"{name}: bare /api fetch bypasses the offline copy"
        assert not re.search(r"""fetch\(['"`]/jmreads""", body), f"{name}: bare /jmreads fetch ignores the mount prefix"


def test_index_has_the_reads_card_in_hcmc_colour(d):
    with d.app.test_request_context("/"):
        html = d.index()
    assert d.POINTS_COLS[21] == {"label": "媒", "domain": "hcmc", "color": "#0d3b66"}
    assert ".reads-card { display: block; background: #0d3b66;" in html
    assert '<a class="card reads-card" id="readsCard" href="/jmreads">' in html
    # Sits right after the Cache · Q2 card, before the comms chart.
    assert html.index('id="cacheBars"') < html.index('id="readsCard"') < html.index('id="emailChart"')
    for el in ("readsChallenge", "readsBarFill", "readsNow", "readsLast", "offlineBadge"):
        assert f'id="{el}"' in html, el
    assert "cachedJSON('/api/reads')" in html
    # Containers are replaced, never appended: the offline web archive re-runs
    # the scripts on an already-rendered DOM.
    assert "cbEl.innerHTML = '';" in html


def _shim(d):
    m = re.search(r"<script>\n(// Offline \+ mount-prefix shim.*?)</script>", d._SHARED_STYLE, re.S)
    assert m, "shim script block not found in the shared head"
    return m.group(1)


NODE_HARNESS = r"""
const fs = require('fs');
const script = fs.readFileSync(process.argv[2], 'utf8');
function realm(pathname, fetchImpl, store = {}) {
  const el = { style: {}, textContent: '' };
  const document = { getElementById: () => el, body: { appendChild() {} }, createElement: () => el,
                     addEventListener() {}, querySelectorAll: () => [] };
  const localStorage = { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = v; } };
  const window = { AbortController }; window.top = window;
  const calls = [];
  const fetch = (u, o) => { calls.push(u); return fetchImpl(u, o); };
  const fn = new Function('location', 'document', 'localStorage', 'window', 'fetch', 'Chart',
                          script + '\nreturn { PREFIX, cachedJSON, _stale, Chart: window.Chart };');
  return Object.assign(fn({ pathname }, document, localStorage, window, fetch, undefined), { el, calls, store });
}
(async () => {
  const out = { prefix: {} };
  for (const p of ['/', '/personal/', '/personal', '/personal/slice', '/slice', '/personal/jmreads', '/1s', '/personal/1s', '/personal/f695'])
    out.prefix[p] = realm(p, () => Promise.reject(new Error('x'))).PREFIX;
  const ok = v => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(v) });
  let r = realm('/personal/', () => ok({ a: 1 }));
  out.chartStub = typeof r.Chart === 'function' && typeof new r.Chart().destroy === 'function';
  out.online = { data: await r.cachedJSON('/api/x'), called: r.calls, stored: JSON.parse(r.store['jmdash:/api/x']).d,
                 stale: r._stale.size, display: r.el.style.display };
  const t = new Date(2026, 10, 12, 9, 7).getTime();
  r = realm('/', () => Promise.reject(new TypeError('Load failed')), { 'jmdash:/api/x': JSON.stringify({ t, d: { a: 2 } }) });
  out.offline = { data: await r.cachedJSON('/api/x'), stale: [...r._stale.entries()], badge: r.el.textContent, display: r.el.style.display };
  r = realm('/', () => Promise.resolve({ ok: false, status: 503 }), { 'jmdash:/api/x': JSON.stringify({ t, d: { a: 3 } }) });
  out.http503 = await r.cachedJSON('/api/x');
  r = realm('/', () => Promise.reject(new Error('down')));
  out.nocopy = await r.cachedJSON('/api/x').then(() => 'resolved', e => 'rejected: ' + e.message);
  r = realm('/', () => ok({ a: 4 }), { 'jmdash:/api/x': JSON.stringify({ t, d: { a: 2 } }) });
  r._stale.set('/api/x', t);
  await r.cachedJSON('/api/x');
  out.recover = { stale: r._stale.size, display: r.el.style.display };
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_shim_prefix_and_offline_fallback_under_node(d, tmp_path):
    shim = tmp_path / "shim.js"
    shim.write_text(_shim(d))
    harness = tmp_path / "harness.js"
    harness.write_text(NODE_HARNESS)
    res = subprocess.run(["node", str(harness), str(shim)], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)

    assert out["prefix"] == {"/": "", "/personal/": "/personal", "/personal": "/personal",
                             "/personal/slice": "/personal", "/slice": "", "/personal/jmreads": "/personal",
                             "/1s": "", "/personal/1s": "/personal", "/personal/f695": "/personal"}
    assert out["chartStub"] is True                       # no Chart.js → blank canvases, not a thrown render
    assert out["online"] == {"data": {"a": 1}, "called": ["/personal/api/x"], "stored": {"a": 1},
                             "stale": 0, "display": "none"}
    assert out["offline"]["data"] == {"a": 2}             # network error → stored copy
    assert out["offline"]["display"] == ""
    assert re.fullmatch(r"offline · data from (11/12 )?09:07", out["offline"]["badge"])
    assert out["http503"] == {"a": 3}                     # non-2xx → stored copy
    assert out["nocopy"] == "rejected: down"              # nothing stored → caller's own catch
    assert out["recover"] == {"stale": 0, "display": "none"}   # back online clears the badge
