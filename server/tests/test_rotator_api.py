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


class _LiveRunTask:
    """What ``SequenceEngine._task`` holds while a run is going: anything whose
    ``done()`` is False. ``engine.running`` is ``self._task is not None and not
    self._task.done()``, so setting this drives the REAL predicate the routes
    ask, which patching the ``running`` property would not."""

    def done(self) -> bool:
        return False


@pytest.fixture()
def run_is_live(monkeypatch):
    """A sequence run is live on the rig the ``rot`` fixture wires (a sim
    rotator and a sim camera), between two exposures: the shape of #698's
    operator who presses ROTATE TO PA while the night runs."""
    from astrodeck.api import app as app_module
    monkeypatch.setattr(app_module.engine, "_task", _LiveRunTask())
    assert app_module.engine.running
    yield


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

    The button is the operator's explicit retest (#697, backlog ruling for
    WP-114), so it passes ``retest_failed=True``: a FAILED self-test is
    measured again, keeping the learned sign. ``goto_and_center`` and the
    engine call the hub without it.

    Mutation 'route does not call the hub' (the ``_spawn`` argument replaced
    with ``asyncio.sleep(0)``) went red on the call count:

        >       assert called == [{"retest_failed": True}], called
        E       AssertionError: []
        E       assert [] == [{'retest_failed': True}]
        E         Right contains one more item: {'retest_failed': True}

    Mutation 'the route keeps the cached False' (``retest_failed=True``
    dropped from the call) went red on the same line:

        E       AssertionError: [{}]
        E       assert [{}] == [{'retest_failed': True}]
    """
    called = []

    async def instant(self, *a, **k):
        called.append(k)
        return {"sign": 1, "trusted": True, "ran": []}
    monkeypatch.setattr(type(hub), "ensure_rotator_ready", instant)

    r = client.post("/api/rotator/preflight", json={})

    assert r.status_code == 200
    assert r.json() == {"started": "rotate_to_pa"}
    assert called == [{"retest_failed": True}], called


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

    THE DETAIL IS CODED (#713, WP-114). It was a bare string, so a client
    could not tell this refusal from any other 409 (every 4xx on this server
    carries a machine code, ``test_capture_video_mirror.py``). A sequence is
    ``sequence_running``, the ``_lane_409`` shape the other lane refusals use,
    with the human sentence still at ``detail.detail``.

    Mutation 'bare string detail' (the sequence refusal made
    ``HTTPException(409, "camera is busy ...")`` again) went red on the next
    line:

        >       detail = r.json()["detail"]
        >       assert detail["code"] == "sequence_running", detail
        E       TypeError: string indices must be integers, not 'str'
    """
    from astrodeck.api import app as app_module
    monkeypatch.setattr(type(app_module.engine), "running",
                        property(lambda self: True))
    r = client.post("/api/rotator/preflight", json={})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "sequence_running", detail
    assert detail["lane"] == "rotate_to_pa", detail
    assert "sequence" in detail["detail"], detail


def test_preflight_is_refused_while_exposing(client, hub, rot, monkeypatch):
    """An exposure in progress with no sequence (the live loop's frame, a
    capture) is ``camera_busy``: coded, like the sequence's (#713).

    Mutation 'camera_busy code dropped' (the exposure refusal made a bare
    string) went red here:

        >       detail = r.json()["detail"]
        >       assert detail["code"] == "camera_busy", detail
        E       TypeError: string indices must be integers, not 'str'
    """
    class Locked:
        def locked(self):
            return True
    monkeypatch.setattr(hub, "_capture_lock", Locked())
    monkeypatch.setattr(hub, "_capture_busy", "sequence exposure", raising=False)
    r = client.post("/api/rotator/preflight", json={})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "camera_busy", detail
    assert detail["lane"] == "rotate_to_pa", detail
    assert "exposure" in detail["detail"], detail


def test_a_sequence_mid_exposure_is_named_a_sequence_not_a_busy_camera(
        client, hub, rot, run_is_live, monkeypatch):
    """A run holds the exposure guard for most of its night, so both
    conditions are true at once, and the sentence that helps is the one naming
    the run: stopping it is what frees the camera. The code the client reads is
    ``sequence_running``, not ``camera_busy``.

    Mutation 'preflight names an exposure before the sequence' (the
    capture-lock refusal moved above the shared sequence refusal) went red
    here:

        E       AssertionError: {"detail":{"detail":"camera is busy (an
        exposure is running); rotator preflight refused","code":"camera_busy",
        "lane":"rotate_to_pa"}}
        E       assert 'camera_busy' == 'sequence_running'
    """
    class Locked:
        def locked(self):
            return True
    monkeypatch.setattr(hub, "_capture_lock", Locked())
    r = client.post("/api/rotator/preflight", json={})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "sequence_running", r.text


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


# --------------------- the routes refuse while a sequence runs (#698, WP-114)
#
# ROTATE TO PA and SYNC TO SKY refused only while a video recording owned the
# camera, and MOVE only while an exposure held the capture lock. None asked
# whether a run was going, so an operator pressing ROTATE TO PA between a run's
# exposures turned the camera under it: a mosaic panel shot at the wrong angle,
# a rotating group's angle check failing mid-run, a flats/lights mismatch.
# Backlog ruling for WP-114: the three routes answer 409 with a coded detail
# (the ``_lane_409`` shape, code ``sequence_running``) while ``engine.running``.
# The engine's own rotations go through ``hub.rotate_to_pa`` directly and are
# not routes, so they are unaffected. Each case drives the REAL
# ``engine.running`` predicate through ``run_is_live`` with the sim rotator and
# camera wired, asserts the refusal's whole shape, and that the refused route
# ran nothing: the hub call was never made, the rotator never moved, no lane
# was spawned.


def _assert_sequence_running_409(r, lane: str) -> dict:
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert isinstance(detail, dict), r.text
    assert detail["code"] == "sequence_running", detail
    assert detail["lane"] == lane, detail
    assert detail["blocked_by"] == "sequence", detail
    assert "sequence is running" in detail["detail"], detail
    return detail


def test_rotate_to_pa_is_refused_while_a_sequence_runs(client, hub, rot,
                                                       run_is_live,
                                                       monkeypatch):
    """Mutation 'rotate-to-pa does not refuse a live run' (the
    ``_refuse_while_sequence_runs`` call deleted from ``rotator_rotate_to_pa``)
    went red here:

        >       assert r.status_code == 409, r.text
        E       AssertionError: {"started":"rotate_to_pa"}
        E       assert 200 == 409
    """
    called = []

    async def spy(self, *a, **k):
        called.append(a)
        return {"rotated": True}
    monkeypatch.setattr(type(hub), "rotate_to_pa", spy)

    r = client.post("/api/rotator/rotate-to-pa", json={"target_pa_deg": 120.0})

    _assert_sequence_running_409(r, "rotate_to_pa")
    assert called == [], "the refused route turned the camera anyway"
    assert hub._busy.get("rotate_to_pa") is None, "a lane was spawned"


def test_sync_to_sky_is_refused_while_a_sequence_runs(client, hub, rot,
                                                      run_is_live,
                                                      monkeypatch):
    """SYNC TO SKY moves nothing but it exposes and RE-CALIBRATES the
    rotator's sky offset, which a run's angle check and its next rotation
    read: not something to change between a run's frames.

    Mutation 'sync-to-sky does not refuse a live run' (the
    ``_refuse_while_sequence_runs`` call deleted from ``rotator_sync_to_sky``)
    went red here:

        >       assert r.status_code == 409, r.text
        E       AssertionError: {"started":"rotate_to_pa"}
        E       assert 200 == 409
    """
    called = []

    async def spy(self, *a, **k):
        called.append(a)
        return {"synced": True}
    monkeypatch.setattr(type(hub), "sync_rotator_to_sky", spy)

    r = client.post("/api/rotator/sync-to-sky", json={})

    _assert_sequence_running_409(r, "rotate_to_pa")
    assert called == [], "the refused route solved and re-calibrated anyway"
    assert hub._busy.get("rotate_to_pa") is None, "a lane was spawned"


def test_sync_to_sky_still_runs_with_no_sequence(client, hub, rot,
                                                 monkeypatch):
    """The control for the case above: nothing running, the route spawns."""
    async def instant(self, *a, **k):
        return {"synced": True}
    monkeypatch.setattr(type(hub), "sync_rotator_to_sky", instant)
    r = client.post("/api/rotator/sync-to-sky", json={})
    assert r.status_code == 200, r.text
    assert r.json() == {"started": "rotate_to_pa"}


def test_move_is_refused_while_a_sequence_runs(client, hub, rot, run_is_live):
    """Go and the one degree nudges post here too, so all three of the
    panels' move buttons are refused with it. Between two exposures the
    capture lock is free, which is exactly when the old check let it through.

    Mutation 'move does not refuse a live run' (the
    ``_refuse_while_sequence_runs`` call deleted from ``rotator_move``) went
    red here:

        >       assert r.status_code == 409, r.text
        E       AssertionError: {"started":"rotator","target_deg":90.0,"adjusted":false}
        E       assert 200 == 409
    """
    rot.moves.clear()

    r = client.post("/api/rotator/move", json={"position_deg": 90.0})

    _assert_sequence_running_409(r, "rotator")
    assert rot.moves == [], rot.moves
    assert hub._busy.get("rotator") is None, "a lane was spawned"


def test_a_direct_calibration_move_is_refused_while_a_sequence_runs_too(
        client, hub, rot, run_is_live):
    """``direct`` is the measurement script's flag (``scripts/
    rig_rotator_follow.py``): it changes the plan, not the guards, and a
    measurement of the train is no more welcome under a run than a framing
    move."""
    rot.moves.clear()
    r = client.post("/api/rotator/move",
                    json={"position_deg": 90.0, "direct": True})
    _assert_sequence_running_409(r, "rotator")
    assert rot.moves == [], rot.moves


def test_the_preflight_refuses_a_live_run_in_the_same_shape(
        client, hub, rot, run_is_live):
    """The same refusal on the third solving route, in the same shape as the
    other two (#713 pins its busy refusals to the ``_lane_409`` shape).

    Mutation 'the shared code is wrong' (``code="sequence_running"`` made
    ``code="lane_busy"`` in ``_refuse_while_sequence_runs``) went red here and
    on every other refusal case, 7 failed:

        E       AssertionError: {'blocked_by': 'sequence', 'code': 'lane_busy',
        'detail': "a sequence is running; rotator preflight refused, ...",
        'lane': 'rotate_to_pa'}
        E       assert 'lane_busy' == 'sequence_running'
    """
    r = client.post("/api/rotator/preflight", json={})
    _assert_sequence_running_409(r, "rotate_to_pa")


def test_halt_is_never_refused_by_a_live_sequence(client, hub, rot,
                                                  run_is_live):
    """STOP must always work: the guard is on the routes that MOVE the camera,
    not on the one that stops it.

    Mutation 'halt refuses a live run' (the shared refusal added to
    ``rotator_halt``) went red here:

        E       AssertionError: {"detail":{"detail":"a sequence is running;
        halt refused, ...","code":"sequence_running","lane":"rotator",
        "blocked_by":"sequence"}}
        E       assert 409 == 200
    """
    r = client.post("/api/rotator/halt")
    assert r.status_code == 200, r.text


# ----------------------- TEST ROTATOR re-tests a failed rotator (#697, WP-114)

from _simhub import sim_hub  # noqa: E402,F401 (fixture import)


@pytest.fixture()
def preflight_on_a_sim_hub(sim_hub, monkeypatch):
    """The preflight route's own endpoint, pointed at a fresh simulated hub
    (the routes read the module-global ``hub`` at call time), so the button
    runs the REAL ``Hub.ensure_rotator_ready`` against the sim rotator's
    physics. Awaited in the test's own loop, like ``rig_hub``'s endpoint."""
    import astrodeck.drivers as drv
    drv.invalidate()
    from astrodeck.api import app as app_module
    from astrodeck.auth.rbac import iter_app_routes
    app = app_module.create_app()
    route = next(r for r in iter_app_routes(app)
                 if getattr(r, "path", None) == "/api/rotator/preflight")
    monkeypatch.setattr(app_module, "hub", sim_hub)
    sim_hub.sim_rig.ra_hours, sim_hub.sim_rig.dec_deg = 5.0, 10.0
    return route.endpoint, sim_hub


async def test_the_button_re_tests_a_failed_rotator_and_a_passing_one_is_trusted(
        preflight_on_a_sim_hub):
    """#697, end to end through the route: a slipping coupling fails the
    self-test and takes rotation off; the owner re-seats it; TEST ROTATOR runs
    the self-test again (not 'measures after the rig reconnects') and the
    rotator is trusted. Before this the only way back was a whole-rig
    reconnect.

    Mutation 'the route keeps the cached False' (``retest_failed=True``
    dropped from the route's ``ensure_rotator_ready`` call) went red here:

        >       assert hub._rotation_trusted is True
        E       assert False is True
    """
    endpoint, hub = preflight_on_a_sim_hub
    rig = hub.sim_rig
    rig.rotator_backlash_deg, rig.rotator_slack_deg = 50.0, -25.0
    await hub.ensure_rotator_ready()
    assert hub._rotation_trusted is False
    rig.rotator_backlash_deg, rig.rotator_slack_deg = 0.0, 0.0

    result = await endpoint()
    await hub._busy["rotate_to_pa"]

    assert result == {"started": "rotate_to_pa"}
    assert hub._rotation_trusted is True
