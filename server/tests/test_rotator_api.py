# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Rotator route contracts: 409 without device, exposure refusal, reverse
gating, range-mapped move response. Uses the drivers-api client fixture and
manipulates the app module's hub directly (routes read the module-global hub
at call time)."""
import pytest
from fastapi.testclient import TestClient

from astrodeck.devices.sim import SimCamera, SimRig, SimRotator


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from astrodeck.config import config_store
    monkeypatch.setattr(config_store, "_path", tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_store, "_cfg", None)
    import astrodeck.drivers as drv
    drv.invalidate()
    from astrodeck.api import app as app_module
    app = app_module.create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def hub():
    from astrodeck.api import app as app_module
    return app_module.hub   # adjust to the module's actual hub symbol if named differently


@pytest.fixture()
def rot(hub):
    rig = SimRig()
    r = SimRotator(rig)
    r.connected = True          # SimRotator.connect() only flips this flag
    hub.devices["rotator"] = r
    # rotate-to-pa requires BOTH rotator and camera (it solves to discover PA),
    # so a connected sim camera must be present too or hub.require("camera")
    # 409s before the route ever reaches hub.rotate_to_pa.
    cam = SimCamera(rig)
    cam.connected = True
    hub.devices["camera"] = cam
    yield r
    hub.devices.pop("rotator", None)
    hub.devices.pop("camera", None)
    hub._busy.pop("rotator", None)
    hub._busy.pop("rotate_to_pa", None)


def test_move_409_without_rotator(client):
    r = client.post("/api/rotator/move", json={"position_deg": 90.0})
    assert r.status_code == 409
    assert "rotator" in r.json()["detail"]


def test_move_refused_while_exposing(client, hub, rot, monkeypatch):
    class Locked:
        def locked(self):
            return True
    monkeypatch.setattr(hub, "_capture_lock", Locked())
    monkeypatch.setattr(hub, "_capture_busy", "sequence exposure", raising=False)
    r = client.post("/api/rotator/move", json={"position_deg": 90.0})
    assert r.status_code == 409
    assert "busy" in r.json()["detail"]


def test_move_returns_range_mapped_target(client, hub, rot):
    from astrodeck.config import RotatorConfig, config_store
    config_store.set_rotator(RotatorConfig(range_type="quarter",
                                           range_start_deg=0.0,
                                           tolerance_deg=1.0))
    r = client.post("/api/rotator/move", json={"position_deg": 100.0})
    assert r.status_code == 200
    body = r.json()
    assert body["adjusted"] is True
    assert body["target_deg"] == pytest.approx(10.0)   # QUARTER map: 100→10
    assert body["started"] == "rotator"


def test_reverse_400_when_unsupported(client, hub, rot):
    r = client.post("/api/rotator/reverse", json={"reverse": True})
    assert r.status_code == 400


def test_halt_ok(client, hub, rot):
    r = client.post("/api/rotator/halt")
    assert r.status_code == 200


def test_rotate_to_pa_spawns(client, hub, rot, monkeypatch):
    async def instant(self, *a, **k):
        return {"rotated": True}
    monkeypatch.setattr(type(hub), "rotate_to_pa", instant)
    r = client.post("/api/rotator/rotate-to-pa", json={"target_pa_deg": 120.0})
    assert r.status_code == 200
    assert r.json() == {"started": "rotate_to_pa"}


def test_rotate_to_pa_body_rejects_nan():
    """NaN bounds (post-review hardening): a NaN/inf target_pa_deg must 422 at
    the model, never sail into rotate_to_pa's mod-360 math."""
    from pydantic import ValidationError

    from astrodeck.api.app import RotateToPaBody
    with pytest.raises(ValidationError):
        RotateToPaBody(target_pa_deg=float("nan"))
    with pytest.raises(ValidationError):
        RotateToPaBody(target_pa_deg=float("-inf"))


# ------------------------------------------------ direct move (WP-88, #594)
#
# ``scripts/rig_rotator_follow.py`` measures whether the camera follows the
# rotator, so its moves must be the raw move: a move against the approach
# direction otherwise goes ROTATOR_BACKLASH_DEG past its target and comes back,
# which adds 10 degrees of travel to the very thing under measurement (and #594
# measured the train slipping on long moves). ``direct`` plans the move as the
# single mechanical target and says in the log it is a calibration move; it is
# False by default, so the two rotator panels' Go and nudge buttons keep the
# one-sided approach.
#
# Called through the route's own endpoint against a bare hub with a sim rotator
# (``rig_hub``, the test_w8_manual_rotator_approach.py fixture), awaited in the
# test's own loop so the ``_spawn``ed task can be awaited to completion.

from test_w8_manual_rotator_approach import (  # noqa: E402,F401
    MECH, _approx, _rig_at, rig_hub)


async def _move_with(endpoint, hub, position_deg: float, **kw) -> dict:
    from astrodeck.api.app import RotatorMoveBody

    result = await endpoint(RotatorMoveBody(position_deg=position_deg, **kw))
    await hub._busy["rotator"]
    return result


def test_the_move_body_is_not_direct_by_default():
    from astrodeck.api.app import RotatorMoveBody
    assert RotatorMoveBody(position_deg=10.0).direct is False
    assert RotatorMoveBody(position_deg=10.0, direct=True).direct is True


async def test_a_direct_move_against_the_approach_is_one_move(rig_hub,
                                                              bus_lines):
    """-5.7 degrees from 137.53 runs against the approach direction. Without
    ``direct`` it is two moves (5 past, and back); with it, exactly one
    ``move_mechanical`` call, to the target, and the log names it a
    calibration move.

    Mutation 'direct ignored' (``_approach_rotator_mechanical``'s ``if direct:``
    made ``if False:``) went red on the one-move assertion, and so did the
    twin 'route ignores body.direct' (app.py's ``if body.direct else`` made
    ``if False else``), 1 failed each:

        >       assert rot.moves == _approx([MECH - 5.7]), rot.moves
        E       AssertionError: [126.83000000000001, 131.83]
        E       assert [126.83000000000001, 131.83] == [131.83 +/- 1.0e-09]
        E         Left contains one more item: 131.83
    """
    endpoint, hub, rot, rig = rig_hub
    _rig_at(rig, play=0.0, slack=0.0)
    rot.moves.clear()

    result = await _move_with(endpoint, hub, MECH - 5.7, direct=True)

    assert rot.moves == _approx([MECH - 5.7]), rot.moves
    assert result["target_deg"] == pytest.approx(MECH - 5.7, abs=1e-6)
    said = [m for _, m, src in bus_lines
            if src == "rotator" and "calibration move" in m]
    assert len(said) == 1 and "direct" in said[0], bus_lines


async def test_the_same_move_without_direct_overshoots_and_returns(rig_hub,
                                                                   bus_lines):
    """The control: ``direct`` false is today's behaviour, unchanged, and says
    nothing about a calibration move."""
    endpoint, hub, rot, rig = rig_hub
    _rig_at(rig, play=0.0, slack=0.0)
    rot.moves.clear()

    await _move_with(endpoint, hub, MECH - 5.7)

    assert rot.moves == _approx([MECH - 10.7, MECH - 5.7]), rot.moves
    assert not any("calibration move" in m for _, m, _ in bus_lines), bus_lines


async def test_a_direct_move_in_the_approach_direction_is_one_move_too(
        rig_hub):
    endpoint, hub, rot, rig = rig_hub
    _rig_at(rig, play=0.0, slack=0.0)
    rot.moves.clear()

    await _move_with(endpoint, hub, MECH + 5.7, direct=True)

    assert rot.moves == _approx([MECH + 5.7]), rot.moves


async def test_a_stop_before_a_direct_move_sends_nothing(rig_hub):
    """The direct plan goes through the SAME epoch-checked leg loop: a STOP
    landing after the route read the motion fence abandons the move. The bump
    is made inside the position read the route does right after reading the
    fence, which is where an operator's STOP would land."""
    endpoint, hub, rot, rig = rig_hub
    _rig_at(rig, play=0.0, slack=0.0)
    rot.moves.clear()
    real_read = type(rot).get_mechanical_position

    async def read_then_stop(self):
        value = await real_read(self)
        hub.bump_motion_epoch()
        return value

    import astrodeck.devices.sim as sim_mod
    orig = sim_mod.SimRotator.get_mechanical_position
    sim_mod.SimRotator.get_mechanical_position = read_then_stop
    try:
        await _move_with(endpoint, hub, MECH - 5.7, direct=True)
    finally:
        sim_mod.SimRotator.get_mechanical_position = orig

    assert rot.moves == [], rot.moves


def test_a_direct_move_is_still_refused_while_exposing(client, hub, rot,
                                                       monkeypatch):
    """``direct`` changes the plan, not the guards: a rotation mid-exposure
    ruins the frame, and the capture-lock 409 is the route's first check."""
    class Locked:
        def locked(self):
            return True
    monkeypatch.setattr(hub, "_capture_lock", Locked())
    monkeypatch.setattr(hub, "_capture_busy", "sequence exposure", raising=False)
    r = client.post("/api/rotator/move",
                    json={"position_deg": 90.0, "direct": True})
    assert r.status_code == 409
    assert "busy" in r.json()["detail"]


# ----------------------------------------- the preflight route (WP-88, #145)


def test_preflight_409_without_a_rotator(client):
    r = client.post("/api/rotator/preflight", json={})
    assert r.status_code == 409
    assert "rotator" in r.json()["detail"]


def test_preflight_409_without_a_camera(client, hub, rot):
    """It solves through the imaging camera, so it needs one, as sync-to-sky
    and rotate-to-pa do."""
    hub.devices.pop("camera", None)
    r = client.post("/api/rotator/preflight", json={})
    assert r.status_code == 409
    assert "camera" in r.json()["detail"]


def test_preflight_spawns_ensure_rotator_ready_on_the_solve_lane(
        client, hub, rot, monkeypatch):
    """The button runs ``Hub.ensure_rotator_ready`` on the ``rotate_to_pa``
    lane (the lane the other two solving routes use, so a second press while
    one is running is the lane's own 409 rather than two rotator turns).

    Mutation 'route does not call the hub' (the ``_spawn`` argument replaced
    with ``asyncio.sleep(0)``) went red on the call count:

        >       assert called == [{}], called
        E       AssertionError: []
        E       assert [] == [{}]
        E         Right contains one more item: {}
    """
    called = []

    async def instant(self, *a, **k):
        called.append(k)
        return {"sign": 1, "trusted": True, "ran": []}
    monkeypatch.setattr(type(hub), "ensure_rotator_ready", instant)

    r = client.post("/api/rotator/preflight", json={})

    assert r.status_code == 200
    assert r.json() == {"started": "rotate_to_pa"}
    assert called == [{}], called


def test_preflight_accepts_a_post_with_no_body(client, hub, rot, monkeypatch):
    async def instant(self, *a, **k):
        return {"sign": 1, "trusted": True, "ran": []}
    monkeypatch.setattr(type(hub), "ensure_rotator_ready", instant)
    assert client.post("/api/rotator/preflight").status_code == 200


def test_preflight_is_refused_while_a_sequence_runs(client, hub, rot,
                                                    monkeypatch):
    """It turns the rotator about 22 degrees and exposes four times: a run's
    frames would be ruined and its field mis-registered. A 409 up front, the
    rig does nothing.

    Mutation 'busy guard removed' (``if engine.running or hub._capture_lock.
    locked():`` made ``if False:``) went red here and on the exposing case,
    2 failed:

        >       assert r.status_code == 409, r.text
        E       AssertionError: {"started":"rotate_to_pa"}
        E       assert 200 == 409
    """
    from astrodeck.api import app as app_module
    monkeypatch.setattr(type(app_module.engine), "running",
                        property(lambda self: True))
    r = client.post("/api/rotator/preflight", json={})
    assert r.status_code == 409, r.text
    assert "busy" in r.json()["detail"]


def test_preflight_is_refused_while_exposing(client, hub, rot, monkeypatch):
    class Locked:
        def locked(self):
            return True
    monkeypatch.setattr(hub, "_capture_lock", Locked())
    monkeypatch.setattr(hub, "_capture_busy", "sequence exposure", raising=False)
    r = client.post("/api/rotator/preflight", json={})
    assert r.status_code == 409
    assert "busy" in r.json()["detail"]


def test_preflight_enforces_the_capture_capability():
    """The same gate as the other rotator routes, graded on what the route
    ENFORCES (its ``require(...)`` dependency), not on its ``@declare`` label:
    a viewer must not be able to turn the rotator 22 degrees."""
    from astrodeck.api import app as app_module
    from astrodeck.auth.capabilities import CAP_CONTROL_CAPTURE
    from astrodeck.auth.rbac import _dependency_caps, iter_app_routes
    app = app_module.create_app()
    routes = {r.path: r for r in iter_app_routes(app)
              if getattr(r, "path", "").startswith("/api/rotator/")}
    assert _dependency_caps(routes["/api/rotator/preflight"]) == frozenset(
        {CAP_CONTROL_CAPTURE})


def test_preflight_is_refused_while_a_video_recording_owns_the_camera(
        client, hub, rot, monkeypatch):
    """The preflight exposes four times, and a .ser recording holds the exposure
    guard for the whole file: refused HERE, with the lane never spawned, not
    inside the lane after a 200 has gone back (``_refuse_if_camera_owned``'s own
    reason, and ``test_capture_video_mirror.py``'s list of camera routes, which
    this route is not in because that file is not this package's).

    Verifier-found gap: nothing pinned the route's ``_refuse_if_camera_owned()``
    call. Mutation 'video guard removed' (the call deleted from
    ``rotator_preflight``) went red here:

        >       assert r.status_code == 409, r.text
        E       AssertionError: {"started":"rotate_to_pa"}
        E       assert 200 == 409
    """
    from astrodeck.api import app as app_module
    called = []

    async def instant(self, *a, **k):
        called.append(k)
        return {"sign": 1, "trusted": True, "ran": []}
    monkeypatch.setattr(type(hub), "ensure_rotator_ready", instant)
    monkeypatch.setattr(type(app_module.video_recorder), "active",
                        property(lambda self: True))

    r = client.post("/api/rotator/preflight", json={})

    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "video_owns_camera", r.text
    assert called == [], "the refused preflight ran anyway"
    assert "rotate_to_pa" not in hub._busy or hub._busy["rotate_to_pa"].done()
