"""#184 — a `filter_offsets` run had no way out but restarting the server.

Found on the rig 2026-08-08: autofocus re-exposed one unmeasurable position
fourteen times inside a per-filter offsets run. The focuser was stationary, the
run could neither finish nor fail, and NOTHING could cancel the lane — not the
Halt on the Focus screen, not a route, not anything short of killing the
process. The retry bound (MAX_DROPS_PER_POSITION) closed that one spin; these
tests are about the operator's exit from every other one.

Uses the in-process app + sim rig, pumping the background ``_spawn`` task via
status GETs (the test_app_route_concurrency idiom).
"""
import asyncio
import time

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.focus.autofocus as af_module
from astrodeck.config import ConfigStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv(app_module.NO_AUTOCONNECT_ENV_VAR, "1")
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    from astrodeck.profiles import profiles as profile_lib
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(config_mod, "FILTER_CONFIG_FILE",
                        tmp_path / "filter_names.json")
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(profile_lib, "_dir", tmp_path / "profiles")

    app = app_module.create_app()
    with TestClient(app) as c:
        try:
            yield c
        finally:
            try:
                c.post("/api/disconnect")
            except Exception:
                pass


def _pump(predicate, c, tries=300) -> bool:
    for _ in range(tries):
        if predicate():
            return True
        c.get("/api/status")     # pump the app loop so the bg _spawn task runs
        time.sleep(0.02)
    return predicate()


def _stuck_sweep(monkeypatch, state):
    """A sweep that moves the focuser and then never comes back.

    Shaped exactly like the real thing on the two points that matter: it leaves
    the focuser AWAY from where it started (a sweep parks it at whatever point
    it was measuring), and on cancel it restores that start position inside a
    SHIELDED move, which is what both real autofocus paths do
    (focus/autofocus.py, focus/native.py). The route's job is to let that
    finish before it halts the focuser — otherwise a cancelled run leaves the
    drawtube wherever the sweep abandoned it, which is the whole point of
    cancelling cleanly rather than pulling the plug.
    """
    async def stuck(cam, foc, **kw):
        start = await foc.get_position()
        state["start"] = start
        try:
            await foc.move_to(start + 3000)
            state["stuck"] = True
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await asyncio.shield(foc.move_to(start))
            raise
        raise AssertionError("unreachable")

    monkeypatch.setattr(af_module, "run_autofocus", stuck)


def _start_run(c):
    import astrodeck.hub as hub_mod
    fw = hub_mod.hub.devices["filterwheel"]
    fw.filter_names = ["L", "Ha", "OIII"]

    async def fast_set_position(slot):
        fw.rig.filter_slot = int(slot)
    fw.set_position = fast_set_position
    r = c.post("/api/filterwheel/learn-offsets",
               json={"ref_slot": 0, "exposure_s": 0.01})
    assert r.status_code == 200, r.text
    return hub_mod.hub


def test_a_stuck_offsets_run_can_be_cancelled(client, monkeypatch):
    state: dict = {}
    _stuck_sweep(monkeypatch, state)
    assert client.post("/api/connect/sim").status_code == 200
    hub = _start_run(client)

    assert _pump(lambda: state.get("stuck"), client), \
        "the run never reached the sweep — the fixture proves nothing"
    lane = hub._busy.get("filter_offsets")
    assert lane is not None and not lane.done(), \
        "precondition: no filter-offsets lane is in flight"

    r = client.post("/api/filterwheel/learn-offsets/cancel")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cancelled"] is True, body
    assert _pump(lambda: lane.done(), client), \
        "the lane survived its own cancel route"


def test_cancelling_leaves_the_focuser_where_the_sweep_started(client,
                                                               monkeypatch):
    """Not "wherever the sweep abandoned it".

    A drawtube left 3000 steps out shoots the rest of the night defocused, and
    the operator who pressed Stop has no way to know it happened. The route
    WAITS for the cancelled sweep's own restore before it halts the focuser,
    and the position it answers with is that restored one."""
    state: dict = {}
    _stuck_sweep(monkeypatch, state)
    assert client.post("/api/connect/sim").status_code == 200
    hub = _start_run(client)
    assert _pump(lambda: state.get("stuck"), client), "the run never swept"

    away = client.get("/api/status").json()["focuser"]["position"]
    assert abs(away - state["start"]) > 100, \
        f"the fixture never moved the focuser off {state['start']}"

    body = client.post("/api/filterwheel/learn-offsets/cancel").json()
    assert body["settled"] is True, body
    assert abs(body["position"] - state["start"]) <= 1, \
        (f"cancel answered with {body['position']}, the sweep started at "
         f"{state['start']} — the route did not wait for the restore")
    hub.devices  # keep the hub referenced for the fixture teardown


def test_cancelling_nothing_says_so_instead_of_reporting_success(client,
                                                                 monkeypatch):
    """A Stop pressed a second late must not answer "cancelled" about a run
    that finished on its own — that is how an operator learns to distrust the
    control."""
    assert client.post("/api/connect/sim").status_code == 200
    body = client.post("/api/filterwheel/learn-offsets/cancel").json()
    assert body["cancelled"] is False, body
    assert "no filter-offsets run" in body["reason"], body


def test_halt_stops_a_filter_offsets_run_too(client, monkeypatch):
    """The Focus screen's Halt is the operator's panic button for the focuser,
    and the one focus operation that can run for a quarter of an hour was the
    one it could not stop."""
    state: dict = {}
    _stuck_sweep(monkeypatch, state)
    assert client.post("/api/connect/sim").status_code == 200
    hub = _start_run(client)
    assert _pump(lambda: state.get("stuck"), client), "the run never swept"
    lane = hub._busy.get("filter_offsets")

    assert client.post("/api/focuser/halt").status_code == 200
    assert _pump(lambda: lane.done(), client), \
        "Halt left the filter-offsets sweep running"
