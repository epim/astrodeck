"""COM-T6: ComHostManager spawns/adopts the sidecar, health-checks it, and
never orphans (loop-kills a stale pidfile process before spawning). The spawn
is injected with an in-process serve() (no real subprocess)."""
import json

import httpx
import pytest

import astrodeck.comhost.server as server
from astrodeck.comhost.manager import ComHostManager
from astrodeck.devices.ascom_registry import AscomDriver


def _fake_spawn(portfile):
    # Start an in-process comhost and write the portfile the manager reads.
    srv = server.serve(port=0, portfile=portfile,
                       drivers=[AscomDriver("Camera", "camera", "Fake.Cam", "C", 0)])
    class _Proc:
        pid = 4242
        _srv = srv
        def poll(self): return None
        def terminate(self): srv.com_host.close(); srv.shutdown()
        def wait(self, timeout=None): return 0
    return _Proc()


@pytest.mark.asyncio
async def test_ensure_spawns_and_healthchecks(tmp_path):
    pf = str(tmp_path / "comhost.json")
    mgr = ComHostManager(spawn=lambda: _fake_spawn(pf), portfile=pf)
    port = await mgr.ensure()
    # the port is live: management API answers
    async with httpx.AsyncClient() as c:
        r = await c.get(f"http://127.0.0.1:{port}/management/v1/configureddevices")
    assert r.status_code == 200
    # ensure() is idempotent — same port, no second spawn
    assert await mgr.ensure() == port
    mgr.stop()


@pytest.mark.asyncio
async def test_no_orphan_kills_stale_pidfile(tmp_path, monkeypatch):
    pf = str(tmp_path / "comhost.json")
    # Pre-seed a stale pidfile as if a previous server crashed leaving a child.
    with open(pf, "w") as f:
        json.dump({"pid": 999999, "port": 1}, f)
    killed = []
    monkeypatch.setattr("astrodeck.comhost.manager._kill_pid",
                        lambda pid: killed.append(pid))
    mgr = ComHostManager(spawn=lambda: _fake_spawn(pf), portfile=pf)
    await mgr.ensure()
    assert 999999 in killed  # the stale child was loop-killed before spawning
    mgr.stop()
