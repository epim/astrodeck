"""COM-T6: ComHostManager spawns/adopts the sidecar, health-checks it, and
never orphans (loop-kills a stale pidfile process before spawning). The spawn
is injected with an in-process serve() (no real subprocess)."""
import json
import sys

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
    # Identity guard says "yes, PID 999999 is genuinely our comhost" so the reap
    # proceeds; confirm-gone returns fast (the fake never really existed).
    monkeypatch.setattr("astrodeck.comhost.manager._pid_is_comhost",
                        lambda pid: True)
    monkeypatch.setattr("astrodeck.comhost.manager._kill_pid",
                        lambda pid: killed.append(pid))
    monkeypatch.setattr("astrodeck.comhost.manager._pid_alive", lambda pid: False)
    mgr = ComHostManager(spawn=lambda: _fake_spawn(pf), portfile=pf)
    await mgr.ensure()
    assert 999999 in killed  # the stale child was killed before spawning
    mgr.stop()


@pytest.mark.asyncio
async def test_no_orphan_skips_kill_when_identity_mismatch(tmp_path, monkeypatch):
    """PID-reuse guard (fix-round, Medium): a stale portfile PID that does NOT
    resolve to our comhost (e.g. the OS recycled it to an innocent process) must
    NOT be killed — we treat the portfile as stale and spawn anyway."""
    pf = str(tmp_path / "comhost.json")
    with open(pf, "w") as f:
        json.dump({"pid": 999999, "port": 1}, f)
    killed = []
    # Identity check says "that PID is NOT our comhost" -> no kill may be issued.
    monkeypatch.setattr("astrodeck.comhost.manager._pid_is_comhost",
                        lambda pid: False)
    monkeypatch.setattr("astrodeck.comhost.manager._kill_pid",
                        lambda pid: killed.append(pid))
    mgr = ComHostManager(spawn=lambda: _fake_spawn(pf), portfile=pf)
    port = await mgr.ensure()             # still spawns + heals cleanly
    assert killed == []                   # the innocent recycled PID was spared
    async with httpx.AsyncClient() as c:
        r = await c.get(f"http://127.0.0.1:{port}/management/v1/configureddevices")
    assert r.status_code == 200
    mgr.stop()


def test_kill_pid_never_raises_on_missing_pid(monkeypatch):
    """Cover the real _kill_pid body (fix-round, Low): it issues the platform kill
    command and NEVER raises, even for a PID that does not exist."""
    from astrodeck.comhost import manager as mgr_mod
    calls = {}

    def _fake_run(cmd, **kw):
        calls["cmd"] = cmd
        class _R:  # a benign CompletedProcess stand-in
            returncode = 128
        return _R()

    monkeypatch.setattr(mgr_mod.subprocess, "run", _fake_run)
    monkeypatch.setattr(mgr_mod.os, "kill", lambda pid, sig: (_ for _ in ()).throw(
        ProcessLookupError()))
    mgr_mod._kill_pid(999999)             # must not raise on a missing PID
    if sys.platform == "win32":
        assert calls["cmd"][0] == "taskkill" and "999999" in calls["cmd"]


@pytest.mark.asyncio
async def test_spawn_backoff_throttles_repeated_failures(tmp_path):
    """Lazy-restart throttle (fix-round, Low): a failed spawn is not retried again
    until the backoff elapses — a caller retry loop cannot thrash-spawn."""
    pf = str(tmp_path / "comhost.json")
    calls = {"n": 0}

    def _bad_spawn():
        calls["n"] += 1
        class _Dead:  # a child that has already exited -> health wait raises
            def poll(self): return 1
            def terminate(self): pass
            def wait(self, timeout=None): return 1
        return _Dead()

    mgr = ComHostManager(spawn=_bad_spawn, portfile=pf)
    with pytest.raises(RuntimeError):
        await mgr.ensure()                # first attempt spawns + fails
    assert calls["n"] == 1
    with pytest.raises(RuntimeError):
        await mgr.ensure()                # immediate retry is throttled...
    assert calls["n"] == 1                # ...so no second spawn was attempted
