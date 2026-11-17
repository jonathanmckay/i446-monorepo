"""lib/form_detach.py: dtd status line + detached child for forms that save
in the background (2026-11-17)."""
import http.server
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import form_detach as fd  # noqa: E402


def _fzf(seen):
    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            seen.append((self.rfile.read(n).decode(), self.headers.get("X-API-Key")))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(("localhost", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_hdr_writes_the_file_and_asks_fzf_to_repaint(tmp_path, monkeypatch):
    """Nothing watches the header file: without the POST a detached saver's
    '✓ saved' sits unseen until the next keypress."""
    seen = []
    srv = _fzf(seen)
    port = tmp_path / "port"
    port.write_text("%d\n" % srv.server_address[1])
    hdr = tmp_path / "hdr"
    monkeypatch.setenv(fd.ENV_HDR, str(hdr))
    monkeypatch.setenv(fd.ENV_PORT, str(port))
    monkeypatch.setenv(fd.ENV_HDRGEN, "/tmp/dtd-1.hdrgen")
    monkeypatch.setenv("FZF_API_KEY", "k")
    try:
        assert fd.hdr("✓ xk887 saved\nline2") is True
    finally:
        srv.shutdown()
    assert hdr.read_text() == "✓ xk887 saved line2\n"
    assert seen == [("transform-header(/tmp/dtd-1.hdrgen)", "k")]


def test_hdr_without_poke_only_writes(tmp_path, monkeypatch):
    seen = []
    srv = _fzf(seen)
    port = tmp_path / "port"
    port.write_text(str(srv.server_address[1]))
    monkeypatch.setenv(fd.ENV_HDR, str(tmp_path / "hdr"))
    monkeypatch.setenv(fd.ENV_PORT, str(port))
    monkeypatch.setenv(fd.ENV_HDRGEN, "/tmp/x.hdrgen")
    try:
        assert fd.hdr("⏳ saving", poke=False) is True
        time.sleep(0.1)
    finally:
        srv.shutdown()
    assert seen == []


def test_everything_is_a_noop_outside_dtd(monkeypatch):
    for k in (fd.ENV_HDR, fd.ENV_PORT, fd.ENV_HDRGEN):
        monkeypatch.delenv(k, raising=False)
    assert fd.in_dtd() is False
    assert fd.hdr("x") is False
    assert fd.poke_dtd() is False


def test_poke_survives_a_dead_port(tmp_path, monkeypatch):
    port = tmp_path / "port"
    port.write_text("1")   # nothing listens
    monkeypatch.setenv(fd.ENV_PORT, str(port))
    monkeypatch.setenv(fd.ENV_HDRGEN, "/tmp/x.hdrgen")
    assert fd.poke_dtd(timeout=0.5) is False
    port.write_text("not a port")
    assert fd.poke_dtd() is False


def test_pid_alive():
    assert fd.pid_alive(os.getpid()) is True
    assert fd.pid_alive(0) is False        # kill(0, 0) would hit our own group
    assert isinstance(fd.pid_alive(None), bool)   # garbage never raises
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    assert fd.pid_alive(p.pid) is False


def test_spawn_detached_outlives_nothing_and_logs(tmp_path):
    log = tmp_path / "sub" / "child.log"
    p = fd.spawn_detached([sys.executable, "-c", "import os; print('sid', os.getsid(0) == os.getpid())"], log)
    p.wait(timeout=10)
    assert log.read_text().strip() == "sid True"   # own session: survives the form's tty


def test_spawned_child_knows_its_form_and_announce_waits_for_it(tmp_path, monkeypatch):
    log = tmp_path / "c.log"
    p = fd.spawn_detached([sys.executable, "-c",
                           "import os; print(os.environ['%s'])" % fd.ENV_PARENT], log)
    p.wait(timeout=10)
    assert log.read_text().strip() == str(os.getpid())
    # announce: a live parent is waited for (up to timeout), a dead one is not
    hdr = tmp_path / "hdr"
    monkeypatch.setenv(fd.ENV_HDR, str(hdr))
    monkeypatch.delenv(fd.ENV_PORT, raising=False)
    t = time.time()
    assert fd.announce("✓ saved", parent=os.getpid(), settle=0, timeout=0.4) is True
    assert 0.3 < time.time() - t < 2
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    t = time.time()
    assert fd.announce("✓ saved 2", parent=dead.pid, settle=0) is True
    assert time.time() - t < 0.3 and hdr.read_text() == "✓ saved 2\n"
