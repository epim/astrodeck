"""API-route regression tests for apply-profile self-cancel, spawn-replace, park-supersedes-goto fixes.

  * apply route no longer self-cancels (routed through _spawn_connect, not _spawn)
  * _spawn(replace=True) cancels an existing same-named task instead of 409-ing
  * park supersedes an in-flight goto instead of 409-ing after fencing it
"""
from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.config import ConfigStore
from astrodeck.profiles import Profile


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv(app_module.NO_AUTOCONNECT_ENV_VAR, "1")
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    from astrodeck.profiles import profiles as profile_lib
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(profile_lib, "_dir", tmp_path / "profiles")

    app = app_module.create_app()
    with TestClient(app) as c:
        try:
            yield c, temp_store, profile_lib
        finally:
            try:
                c.post("/api/disconnect")
            except Exception:
                pass


def _wait(predicate, c, tries=300) -> bool:
    for _ in range(tries):
        if predicate():
            return True
        c.get("/api/status")     # give the app loop a chance to run bg tasks
        time.sleep(0.02)
    return predicate()


# ----------------------------------------------------- apply self-cancel fix

def test_apply_profile_route_does_not_self_cancel(client):
    """With a rig connected, applying a profile used to cancel its own driver
    task the instant its first step (disconnect_all) ran, so the active pointer
    was never set. Routed through _spawn_connect it now completes."""
    c, store, lib = client
    # a live sim rig so apply's teardown does real (suspending) disconnect work.
    assert c.post("/api/connect/sim").status_code == 200
    prof = Profile(name="empty", primary_backend="sim")
    lib.save(prof)

    r = c.post(f"/api/profiles/{prof.id}/apply")
    assert r.status_code == 200, r.text
    assert r.json().get("started") == "profile"

    # apply_profile sets the active pointer as its LAST step — it can only appear
    # if the driver ran to completion (i.e. did not self-cancel mid-teardown).
    assert _wait(lambda: store.cfg().active_profile_id == prof.id, c), \
        "apply self-cancelled: active pointer was never set"


# ------------------------------------------------------ _spawn replace / park

async def test_spawn_replace_cancels_existing_task():
    hub = app_module.hub
    started = asyncio.Event()

    async def long_task():
        started.set()
        await asyncio.sleep(100)

    hub._busy["goto"] = asyncio.create_task(long_task())
    await started.wait()
    old = hub._busy["goto"]

    # without replace: a live same-named task is a 409.
    dead = long_task()
    with pytest.raises(app_module.HTTPException) as ei:
        app_module._spawn("goto", dead)
    assert ei.value.status_code == 409
    dead.close()                                   # no "never awaited" warning

    # with replace: the existing task is cancelled and the new one starts.
    ran = asyncio.Event()

    async def newcoro():
        ran.set()

    res = app_module._spawn("goto", newcoro(), replace=True)
    assert res == {"started": "goto"}
    for _ in range(50):
        if old.done() and ran.is_set():
            break
        await asyncio.sleep(0.01)
    assert old.cancelled() or old.done()
    assert ran.is_set()

    t = hub._busy.pop("goto", None)
    if t and not t.done():
        t.cancel()


def test_park_supersedes_inflight_goto(client, monkeypatch):
    """Park must not 409 against an in-flight goto (the old bug bumped the motion
    fence, sabotaging the goto, then 409'd so the mount never parked).

    REAL SIM PACING, and the goto shown in flight before the park. Since #207
    the fast path (conftest's ``ASTRODECK_FAST_TEST``) zeroes the sim slew's
    dwell as well, so the goto below finished before the park arrived as
    often as not, and a park that met no goto answered 200 whatever the route
    did with one. Under mutant "park 409s a busy goto lane" (``_spawn("goto",
    _park(), replace=True)`` -> ``replace=False``) this test then went RED in
    8 runs of 10 and GREEN in 2 (observed 2026-09-24): a guard that let the
    regression through one time in five. With the pacing restored and the
    precondition below, the same mutant is RED every run, observed verbatim:

        E   AssertionError: {"detail":{"detail":"'goto' is already running","code":"lane_busy","lane":"goto"}}
        E   assert 409 == 200
    """
    monkeypatch.delenv("ASTRODECK_FAST_TEST", raising=False)
    c, _store, _lib = client
    assert c.post("/api/connect/sim").status_code == 200

    # start a plain (un-centered) goto to a far target: at the sim's real 4
    # deg/s the slew takes seconds, so it is still in flight when park arrives.
    g = c.post("/api/mount/goto",
               json={"ra_hours": 2.0, "dec_deg": 80.0, "center": False})
    assert g.status_code == 200, g.text
    assert "goto" in c.get("/api/status").json().get("busy_lanes", []), (
        "precondition: the goto is still in flight when the park arrives; "
        "without one there is nothing for the park to supersede")

    r = c.post("/api/mount/park")
    # the key regression: 200 (park accepted + goto superseded), never 409.
    assert r.status_code == 200, r.text
    assert r.json().get("started") == "goto"
