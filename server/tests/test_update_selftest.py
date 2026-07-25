"""End-to-end self-update selftest with REAL I/O (no mocked crypto/network).

Marked ``selftest`` so the release workflow runs it on a Windows+Linux matrix on
every release (spec section 8). Covers the security-critical apply pipeline
(real httpx download -> real Ed25519 verify -> real tar extraction -> pending
write -> supervisor signal) and the supervisor's real subprocess launcher +
``/healthz`` probe wiring that the deterministic unit tests stub out.
"""
import functools
import io
import json
import socket
import subprocess
import sys
import tarfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from astrodeck.config import UpdateConfig
from astrodeck.update import github, signing
from astrodeck.update.service import UpdateService
from astrodeck.update.state import update_state

pytestmark = [
    pytest.mark.selftest,
    # Fast/slow lane (pyproject markers): real HTTP server + real subprocesses
    # + real tar/Ed25519 work, so it is both wall-clock-bound and genuinely
    # out-of-process. `pytest -m 'not slow'` skips it for the inner loop; the
    # release workflow's `-m selftest` run and the FULL suite still include it.
    pytest.mark.slow,
    pytest.mark.integration,
]


@pytest.fixture(autouse=True)
def _reset_update_state():
    # this module mutates the global update_state; clean up so file order can't
    # leak `update_available` into other test files.
    yield
    update_state.current = "0.1.0"
    update_state.set_available(None, "")
    update_state.set_phase("idle")
    update_state.set_result(None)


# ----------------------------------------------------------------- helpers

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _serve_dir(directory: Path):
    """Serve ``directory`` over http on an ephemeral port; return (server, base_url)."""
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _make_real_tarball(out_dir: Path, version: str) -> Path:
    """A minimal but real release bundle (NO pyproject -> deps reconcile skips)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    top = f"astrodeck-{version}"
    tb = out_dir / f"astrodeck-{version}.tar.gz"
    with tarfile.open(tb, "w:gz") as tf:
        def add(name, content):
            data = content.encode()
            info = tarfile.TarInfo(f"{top}/{name}")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        add("manifest.json", json.dumps({"name": "astrodeck", "version": version}))
        add("server/astrodeck/__init__.py", f'__version__ = "{version}"')
        add("ui/dist/index.html", "<html><body>astrodeck</body></html>")
    return tb


class _IdleHub:
    @property
    def restart_blocker(self):
        return None


_FAKE_SERVER = '''
import sys, json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
ver, port = sys.argv[1], int(sys.argv[2])
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            body = json.dumps({"ok": True, "version": ver}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404); self.end_headers()
    def log_message(self, *a): pass
ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
'''


# ----------------------------------------------------------------- the tests

async def test_real_signed_release_apply_pipeline(tmp_path, monkeypatch):
    seed, pub = signing.generate_keypair()
    served = tmp_path / "served"
    artifact = _make_real_tarball(served, "9.9.9")
    sha_path, sig_path = signing.write_sidecars(artifact, seed)

    server, base = _serve_dir(served)
    try:
        info = github.ReleaseInfo(
            version="9.9.9", tag="v9.9.9", notes_md="real notes",
            artifact_url=f"{base}/{artifact.name}",
            sha256_url=f"{base}/{sha_path.name}",
            sig_url=f"{base}/{sig_path.name}", prerelease=False)

        async def fake_latest(*a, **k):
            return info
        monkeypatch.setattr(github, "latest_release", fake_latest)

        update_state.current = "0.1.0"
        update_state.set_available("9.9.9", "real notes")
        signalled = {"v": False}
        root = tmp_path / "root"
        svc = UpdateService(
            cfg_getter=lambda: UpdateConfig(signing_pubkey=pub),
            hub=_IdleHub(), install_root=root,
            server_signal=lambda: signalled.__setitem__("v", True))

        res = await svc.apply()   # REAL download + verify + extract
        assert res["applying"] == "9.9.9"
        assert signalled["v"] is True
        staged = root / "releases" / "9.9.9"
        assert (staged / "manifest.json").exists()
        assert (staged / "server" / "astrodeck" / "__init__.py").exists()
        assert (staged / "ui" / "dist" / "index.html").exists()
        pend = json.loads((root / "state" / "pending-update.json").read_text())
        assert pend["version"] == "9.9.9"
    finally:
        server.shutdown()


async def test_real_apply_rejects_tampered_artifact(tmp_path, monkeypatch):
    from astrodeck.update.service import UpdateError
    seed, pub = signing.generate_keypair()
    served = tmp_path / "served"
    artifact = _make_real_tarball(served, "9.9.9")
    sha_path, sig_path = signing.write_sidecars(artifact, seed)
    # tamper AFTER signing -> sha + signature must both fail
    artifact.write_bytes(artifact.read_bytes() + b"evil")

    server, base = _serve_dir(served)
    try:
        info = github.ReleaseInfo(
            "9.9.9", "v9.9.9", "n", f"{base}/{artifact.name}",
            f"{base}/{sha_path.name}", f"{base}/{sig_path.name}", False)
        monkeypatch.setattr(github, "latest_release",
                            lambda *a, **k: _aval(info))
        update_state.current = "0.1.0"
        update_state.set_available("9.9.9", "n")
        svc = UpdateService(cfg_getter=lambda: UpdateConfig(signing_pubkey=pub),
                            hub=_IdleHub(), install_root=tmp_path / "root")
        with pytest.raises(UpdateError) as ei:
            await svc.apply()
        assert "verification failed" in str(ei.value)
        assert not (tmp_path / "root" / "state" / "pending-update.json").exists()
    finally:
        server.shutdown()


async def _aval(v):
    return v


@pytest.mark.selftest
def test_real_launcher_health_probe(tmp_path):
    from supervisor.supervisor import _real_health
    script = tmp_path / "fakeserver.py"
    script.write_text(_FAKE_SERVER)
    port = _free_port()
    proc = subprocess.Popen([sys.executable, str(script), "1.2.3", str(port)])
    try:
        check = _real_health("127.0.0.1", port)
        up = False
        for _ in range(50):
            if check("1.2.3"):
                up = True
                break
            time.sleep(0.1)
        assert up, "fake server never became healthy"
        assert check("9.9.9") is False   # version mismatch is rejected
    finally:
        proc.terminate()
        proc.wait()
    assert check("1.2.3") is False       # down -> unhealthy
