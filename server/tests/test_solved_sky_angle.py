# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Every imaging-camera plate solve records its sky angle and calibrates the
rotator from it (owner request, 2026-09-23).

The rotator's reported sky PA is ``mechanical - sync_offset``, and the offset
used to be set in exactly two places: ``sync_rotator_to_sky`` and the
``rotate_to_pa`` loop. Every other solve of the same camera -- the goto
centring, the resume re-centre, polar alignment, the guide-scope offset, the
per-frame WCS stamp -- measured the angle and dropped it, so the rotator was
only as fresh as the last rotator button somebody pressed, and after a
reconnect it reported its bare mechanical angle as a sky PA all night.

Pinned here, each against a named mutation of ``astrodeck/sky_angle.py`` or of
the caller in ``hub.py`` / ``polar/native.py`` (observed failures recorded per
test):

* a solve updates the recorded PA and re-syncs the rotator, on every routed path;
* a guide-camera solve records and calibrates nothing;
* a failed solve, or one with no finite rotation, records and calibrates nothing;
* a rotator that moved during the exposure is recorded but not calibrated;
* a solve after a meridian flip calibrates to what it measured (no 180-degree
  error), and a PRE-flip frame whose solve lands after the flip is refused;
* the night log gets the line;
* the two callers whose whole purpose is the calibration raise instead of
  carrying on through a stale offset.

The controls matter as much as the positives: every "nothing changes" case
first shows the rotator IS calibratable in that state, so the refusal is not
passing because nothing could have happened.
"""
from __future__ import annotations

import asyncio

import pytest

from _simhub import sim_hub  # noqa: F401 (fixture import)
from astrodeck import sky_angle
from astrodeck.devices.base import DeviceError, PierSide
from astrodeck.events import NightLogWriter, bus, night_key
from astrodeck.solve.base import SolveResult, WcsSolution

pytestmark = pytest.mark.asyncio


def _wrapped(a: float) -> float:
    """Signed smallest difference, so 359.9 vs 0.1 reads as 0.2."""
    return ((a + 180.0) % 360.0) - 180.0


def near(a: float, b: float, tol: float = 0.01) -> bool:
    return abs(_wrapped(a - b)) <= tol


def _wcs() -> WcsSolution:
    return WcsSolution(crval1=83.8, crval2=-5.4, crpix1=50.0, crpix2=50.0,
                       cd11=-4.3e-4, cd12=0.0, cd21=0.0, cd22=4.3e-4)


def _pier(sim_hub, monkeypatch, side: str) -> dict:
    """Make the sim mount report ``side`` (changeable through the returned
    holder), and seed the hub's pier-side cache to agree, as the 2 s status
    poll would. The sim derives its side from the hour angle, which a test must
    not depend on."""
    holder = {"v": side}
    tel = sim_hub.require("telescope")

    async def pier_side():
        return PierSide(holder["v"])

    monkeypatch.setattr(tel, "pier_side", pier_side)
    sim_hub._note_pier_side(side)
    return holder


def _flip(sim_hub, holder: dict, side: str) -> None:
    holder["v"] = side
    sim_hub._note_pier_side(side)


def _move_rotator_during_exposure(sim_hub, monkeypatch, by_deg: float) -> None:
    """The rotator turns ``by_deg`` once the frame is taken and before its
    solve lands, and the solver reports what the FRAME shows (the angle at the
    exposure), as a real one would. The sim's own solver reads the rig live at
    solve time, which would hand back the post-move angle and make a wrong
    calibration look right."""
    from astrodeck.solve.simsolver import SimSolver

    cam = sim_hub.require("camera")
    orig = cam.expose
    rig = sim_hub.sim_rig
    seen: dict = {}

    async def expose(*a, **k):
        frame = await orig(*a, **k)
        seen["pa"] = (rig.rotator_mech_deg + rig.rotator_pa_offset_deg) % 360.0
        rig.rotator_mech_deg = (rig.rotator_mech_deg + by_deg) % 360.0
        return frame

    real = SimSolver.solve

    async def solve(self, path, **kw):
        r = await real(self, path, **kw)
        if r.success and "pa" in seen:
            r.rotation_deg = seen["pa"]
        return r

    monkeypatch.setattr(cam, "expose", expose)
    monkeypatch.setattr(SimSolver, "solve", solve)


# ------------------------------------------------------------ the positive

async def test_a_centring_solve_records_the_angle_and_calibrates_the_rotator(
        sim_hub):
    """The goto centring loop, the resume re-centre and the bare solve-and-sync
    route all solve through ``solve_and_sync``: the one path that used to drop
    the angle most often.

    MUTATION: delete the ``note_solved_rotation`` call in ``Hub.solve_and_sync``.
    Observed: "AssertionError: the solve recorded no sky angle". The static
    audit fails too (test_every_solve_records_the_sky_angle).
    """
    rig = sim_hub.sim_rig
    rig.rotator_mech_deg = 35.0
    rig.rotator_pa_offset_deg = 20.0          # truth: the camera is at PA 55
    rot = sim_hub.require("rotator")
    assert near(await rot.get_position(), 35.0), "precondition: unsynced"

    await sim_hub.solve_and_sync(0.05)

    rec = sim_hub.last_sky_angle
    assert rec is not None, "the solve recorded no sky angle"
    assert near(rec["pa_deg"], 55.0), rec
    assert rec["source"] == "plate solve + sync"
    assert rec["calibrated"] is True and rec["reason"] is None, rec
    assert near(await rot.get_position(), 55.0), (
        "the rotator still reports the stale angle after a solve measured it")
    assert rig.rotator_mech_deg == pytest.approx(35.0), "calibrating moved it"
    st = await sim_hub.poll_status()
    assert near(st["sky_angle"]["pa_deg"], 55.0), st["sky_angle"]
    assert near(st["rotator"]["sky_deg"], 55.0), st["rotator"]


async def test_a_polar_alignment_solve_records_and_calibrates(sim_hub):
    """Polar alignment solves the imaging camera dozens of times a session.

    MUTATION: delete the ``note_solved_rotation`` call in
    ``polar/native.py:_capture_and_solve``. Observed: "AssertionError: the
    polar solve recorded no sky angle".
    """
    from astrodeck import providers
    from astrodeck.polar.native import _capture_and_solve

    rig = sim_hub.sim_rig
    rig.rotator_mech_deg = 100.0
    rig.rotator_pa_offset_deg = -40.0         # truth: PA 60
    await _capture_and_solve(sim_hub, providers.pick_solver(sim_hub), None)

    rec = sim_hub.last_sky_angle
    assert rec is not None, "the polar solve recorded no sky angle"
    assert rec["source"] == "polar alignment" and rec["calibrated"] is True, rec
    assert near(await sim_hub.require("rotator").get_position(), 60.0)


async def test_a_saved_light_s_wcs_solve_records_and_calibrates(sim_hub):
    """The per-frame WCS stamp: the solve lands seconds after the capture
    returned, checked against the rotator and pier side the SNAPSHOT froze.

    Control for the flip case below: same path, nothing changed, calibrates.

    MUTATION: delete the ``note_solved_rotation`` call in
    ``Hub._solve_and_stamp``. Observed: "AssertionError: the WCS solve
    recorded no sky angle".
    """
    from astrodeck.config import config_store

    config_store.cfg().solve_saved_lights = True
    rig = sim_hub.sim_rig
    rig.rotator_mech_deg = 10.0
    rig.rotator_pa_offset_deg = 30.0          # truth: PA 40
    rot = sim_hub.require("rotator")
    await rot.sync(99.0)                       # a stale calibration to replace

    await sim_hub.capture(0.05, 100, 30, 1, save=True, target="M42")
    await asyncio.wait_for(sim_hub._wcs_queue.join(), timeout=30)

    rec = sim_hub.last_sky_angle
    assert rec is not None, "the WCS solve recorded no sky angle"
    assert rec["source"] == "saved-frame WCS", rec
    assert rec["calibrated"] is True, rec
    assert near(await rot.get_position(), 40.0)


async def test_the_guide_offset_records_the_main_camera_only(sim_hub,
                                                             monkeypatch):
    """The guide-scope offset solves BOTH cameras. Only the imaging camera's
    angle is the rotator's; the guide camera's is the guide train's.

    MUTATION: in ``Hub.measure_guide_offset``, pass the guide result through
    ``note_solved_rotation`` too, with the main frame's context (the copy-paste
    of the line above it). Observed: "AssertionError: the guide camera's angle
    reached the rotator: 200.0".
    """
    class _Scripted:
        name = "scripted"

        async def solve(self, path, **kw):
            pa = 200.0 if "guide_offset_guide" in str(path) else 55.0
            return SolveResult(True, ra_hours=5.5, dec_deg=-5.0,
                               rotation_deg=pa, pixel_scale_arcsec=1.5,
                               message="scripted")

    import astrodeck.providers as providers
    monkeypatch.setattr(providers, "pick_solver", lambda hub: _Scripted())
    rig = sim_hub.sim_rig
    rig.rotator_mech_deg = 0.0
    rot = sim_hub.require("rotator")

    await sim_hub.measure_guide_offset(exposure_s=0.05, guide_exposure_s=0.05)

    rec = sim_hub.last_sky_angle
    assert rec is not None and rec["source"] == "guide-scope offset", rec
    assert near(rec["pa_deg"], 55.0), (
        f"the guide camera's angle reached the rotator: {rec['pa_deg']}")
    assert near(await rot.get_position(), 55.0)


# ------------------------------------------------ the cases that change nothing

async def test_a_guide_camera_solve_records_and_calibrates_nothing(sim_hub):
    """MUTATION: delete the ``context.camera is not imaging`` refusal in
    ``note_solved_rotation``. Observed: "AssertionError: a guide-camera solve
    was recorded".
    """
    rot = sim_hub.require("rotator")
    await rot.sync(10.0)
    # The positive control first: from the IMAGING camera, in this exact
    # state, the same result would calibrate. So a refusal below is the guard.
    cam = sim_hub.require("camera")
    ok = await sky_angle.note_solved_rotation(
        sim_hub, SolveResult(True, rotation_deg=70.0), source="control",
        context=await sky_angle.exposure_context(sim_hub, cam))
    assert ok is not None and ok["calibrated"] is True, ok
    await rot.sync(10.0)
    before = sim_hub.last_sky_angle

    guide = sim_hub.devices["guide_camera"]
    out = await sky_angle.note_solved_rotation(
        sim_hub, SolveResult(True, rotation_deg=200.0), source="guide",
        context=await sky_angle.exposure_context(sim_hub, guide))

    assert out is None, "a guide-camera solve was recorded"
    assert sim_hub.last_sky_angle is before
    assert near(await rot.get_position(), 10.0)


@pytest.mark.parametrize("result,mutation", [
    (SolveResult(False, rotation_deg=0.0, message="no stars"),
     "success"),
    (SolveResult(True, rotation_deg=float("nan")), "finite"),
    (SolveResult(True, rotation_deg=None), "finite"),  # type: ignore[arg-type]
    (SolveResult(True, rotation_deg=0.0, rotation_known=False), "known"),
])
async def test_a_failed_or_angleless_solve_records_and_calibrates_nothing(
        sim_hub, result, mutation):
    """A failed ASTAP result still carries ``rotation_deg=0.0`` (the dataclass
    default), so a recorder that trusted it would calibrate every rotator to
    PA 0 on every cloud.

    MUTATIONS, per case:
    * "success": delete the ``not getattr(result, "success", False)`` refusal.
      Observed: "AssertionError: a failed solve was recorded".
    * "finite", NaN: delete the ``math.isfinite`` refusal. Observed:
      "AssertionError: a solve with no finite rotation was recorded".
    * "finite", None: the ``float(None)`` TypeError refusal is the guard; it
      cannot be deleted without also deleting the conversion, and deleting the
      ``except (TypeError, ValueError)`` makes the call raise instead:
      observed "TypeError: float() argument must be a string or a real number,
      not 'NoneType'".
    * "known" (#146): ASTAP reported no CROTA2, so rotation_deg is a 0.0
      placeholder. Delete the ``rotation_known`` refusal. Observed:
      "AssertionError: a solve with no reported rotation was recorded" - the
      rotator re-synced to PA 0.
    """
    rot = sim_hub.require("rotator")
    await rot.sync(123.0)
    cam = sim_hub.require("camera")
    ctx = await sky_angle.exposure_context(sim_hub, cam)
    out = await sky_angle.note_solved_rotation(sim_hub, result, source="t",
                                               context=ctx)
    what = {"success": "a failed solve", "known": "a solve with no reported rotation"}.get(
        mutation, "a solve with no finite rotation")
    assert out is None, f"{what} was recorded"
    assert sim_hub.last_sky_angle is None
    assert near(await rot.get_position(), 123.0)


async def test_a_failed_centring_solve_leaves_the_rotator_alone(sim_hub,
                                                                monkeypatch):
    """The same, through the caller: a centring solve that fails raises and
    touches nothing. Guarded by ``solve_and_sync`` raising before the recorder
    is reached AND by the recorder's own refusal.

    MUTATION: in ``Hub.solve_and_sync`` move the recorder call above the
    ``if not result.success: raise``, AND delete the recorder's success
    refusal. Observed: "AssertionError: a failed solve calibrated the rotator
    to 0.0". Either guard alone holds this test: the move alone was run and
    stayed green, because the recorder then refuses the failed result itself.
    """
    class _NoSolve:
        name = "broken"

        async def solve(self, path, **kw):
            return SolveResult(False, message="no stars")

    import astrodeck.providers as providers
    monkeypatch.setattr(providers, "pick_solver", lambda hub: _NoSolve())
    rot = sim_hub.require("rotator")
    await rot.sync(77.0)
    with pytest.raises(DeviceError, match="plate solve failed"):
        await sim_hub.solve_and_sync(0.05)
    got = await rot.get_position()
    assert near(got, 77.0), f"a failed solve calibrated the rotator to {got:.1f}"
    assert sim_hub.last_sky_angle is None


async def test_a_rotator_that_moved_during_the_exposure_is_recorded_not_calibrated(
        sim_hub, monkeypatch):
    """The frame was taken at one mechanical angle and the rotator is now at
    another: syncing "current mechanical == solved PA" would be off by exactly
    the move. Record it (the angle is real), refuse the calibration, say why.

    MUTATION: delete the ``moved > MOVED_TOL_DEG`` refusal in
    ``_calibration_refusal``. Observed: "AssertionError: calibrated across a
    10 degree move". (Had it got further: the frame shows PA 50, the camera is
    now at 60, and the rotator would have been told it is at 50.)
    """
    rig = sim_hub.sim_rig
    rig.rotator_mech_deg = 20.0
    rig.rotator_pa_offset_deg = 30.0          # the frame is exposed at PA 50
    rot = sim_hub.require("rotator")
    await rot.sync(111.0)
    _move_rotator_during_exposure(sim_hub, monkeypatch, 10.0)

    await sim_hub.solve_and_sync(0.05)

    rec = sim_hub.last_sky_angle
    assert rec is not None, "the angle itself was not recorded"
    assert rec["calibrated"] is False, "calibrated across a 10 degree move"
    assert near(rec["pa_deg"], 50.0), rec    # recorded: what the frame showed
    assert "moved 10.00°" in rec["reason"], rec["reason"]
    assert near(await rot.get_position(), 111.0 + 10.0), (
        "the offset changed although the calibration was refused")


@pytest.mark.parametrize("case,why", [
    ("moving_at_start", "moving when the exposure started"),
    ("moving_now", "the rotator is moving"),
    ("reconnected", "reconnected"),
    ("unread", "was not read"),
])
async def test_every_other_reason_to_refuse_refuses(sim_hub, monkeypatch, case,
                                                    why):
    """The remaining refusals of ``_calibration_refusal``, each against the
    positive control that the same result calibrates when the condition is
    absent (asserted first, in the same state).

    MUTATIONS, per case; the first three each observed to fail with
    "AssertionError: calibrated although <case>":
    * moving_at_start: delete ``if context.moving: return ...``.
    * moving_now: delete ``if moving_now: return ...``.
    * reconnected: delete ``if context.rotator is not rot: return ...``.
    * unread: delete the ``context.mech_deg is None`` refusal. The recorder's
      catch-all still refuses (the move check then subtracts None), so the
      calibration assertion holds and the REASON is what goes red. Observed:
      "AssertionError: the calibration check failed (unsupported operand
      type(s) for -: 'float' and 'NoneType')".
    """
    from dataclasses import replace

    rot = sim_hub.require("rotator")
    cam = sim_hub.require("camera")
    result = SolveResult(True, rotation_deg=45.0)
    ctx = await sky_angle.exposure_context(sim_hub, cam)
    await rot.sync(5.0)
    ok = await sky_angle.note_solved_rotation(sim_hub, result, source="control",
                                              context=ctx)
    assert ok["calibrated"] is True, ok
    await rot.sync(5.0)

    if case == "moving_at_start":
        ctx = replace(ctx, moving=True)
    elif case == "moving_now":
        async def moving():
            return True
        monkeypatch.setattr(rot, "is_moving", moving)
    elif case == "reconnected":
        ctx = replace(ctx, rotator=object())
    elif case == "unread":
        ctx = replace(ctx, mech_deg=None)
    rec = await sky_angle.note_solved_rotation(sim_hub, result, source="t",
                                               context=ctx)
    assert rec is not None and rec["calibrated"] is False, (
        f"calibrated although {case}")
    # The reason is part of the contract: it is what the night log says, and
    # the only way a refusal is distinguishable from the recorder's catch-all.
    assert why in rec["reason"], rec["reason"]
    assert near(await rot.get_position(), 5.0)


async def test_the_capture_path_reads_the_rotator_only_when_a_solve_will_use_it(
        sim_hub):
    """The snapshot's rotator read exists for the background WCS solve alone,
    and runs on every frame of the live loop, so it is bought only while saved
    lights are being solved. Positive and control in one: off, no read; on,
    the angle is there with the rotator's mechanical position.

    MUTATION: drop the ``solve_saved_lights`` condition from the angle read in
    ``Hub._capture_snapshot``. Observed: "AssertionError: the capture path
    read the rotator for a solve that will never run".
    """
    from astrodeck.config import config_store

    sim_hub.sim_rig.rotator_mech_deg = 42.0
    config_store.cfg().solve_saved_lights = False
    await sim_hub.capture(0.05, 100, 30, 1, save=False)
    assert sim_hub._promotable["camera"].snap.angle is None, (
        "the capture path read the rotator for a solve that will never run")

    config_store.cfg().solve_saved_lights = True
    await sim_hub.capture(0.05, 100, 30, 1, save=False)
    angle = sim_hub._promotable["camera"].snap.angle
    assert angle is not None and near(angle.mech_deg, 42.0), angle
    assert angle.camera is sim_hub.require("camera")


# ------------------------------------------------------------ meridian flip

async def test_a_solve_after_a_meridian_flip_calibrates_to_what_it_measured(
        sim_hub, monkeypatch):
    """A German-equatorial flip turns the field 180 degrees under a rotator
    that never moved. The post-flip solve measures the real angle, and the
    rotator must report THAT -- not the pre-flip angle, and not a value folded
    back by pier side.

    MUTATION: fold the PA by pier side in ``note_solved_rotation``
    (``pa = mod360(raw + (180.0 if context.pier_side == "west" else 0.0))``,
    the naive "compensate for the flip"). Observed: "AssertionError: after the
    flip the rotator reports 30.0 for a camera at 210.0".
    """
    rig = sim_hub.sim_rig
    rig.rotator_mech_deg = 30.0
    rig.rotator_pa_offset_deg = 0.0
    holder = _pier(sim_hub, monkeypatch, "east")
    rot = sim_hub.require("rotator")

    await sim_hub.solve_and_sync(0.05)
    assert near(await rot.get_position(), 30.0)
    assert sim_hub.last_sky_angle["pier_side"] == "east"

    _flip(sim_hub, holder, "west")
    rig.rotator_pa_offset_deg = 180.0          # the field turned; the rotator did not
    await sim_hub.solve_and_sync(0.05)

    rec = sim_hub.last_sky_angle
    assert rec["calibrated"] is True and rec["pier_side"] == "west", rec
    got = await rot.get_position()
    assert near(got, 210.0), (
        f"after the flip the rotator reports {got:.1f} for a camera at 210.0")
    assert rig.rotator_mech_deg == pytest.approx(30.0)


async def test_a_pre_flip_frame_solved_after_the_flip_is_refused(sim_hub,
                                                                 monkeypatch):
    """The WCS worker solves in the background, seconds behind the capture. A
    light exposed on the east side whose solve lands after the flip carries
    the pre-flip angle; applied then, it would leave the rotator reporting a
    PA exactly 180 degrees wrong for the rest of the night.

    MUTATION: delete the pier-side comparison in ``_calibration_refusal``.
    Observed: "AssertionError: a pre-flip solve overwrote the post-flip
    calibration: the rotator reports 30.0 for a camera at 210.0".
    """
    from astrodeck.config import config_store

    config_store.cfg().solve_saved_lights = True
    rig = sim_hub.sim_rig
    rig.rotator_mech_deg = 30.0
    rig.rotator_pa_offset_deg = 0.0
    holder = _pier(sim_hub, monkeypatch, "east")
    rot = sim_hub.require("rotator")

    release = asyncio.Event()
    started = asyncio.Event()

    class _Held:
        name = "held"

        async def solve(self, path, **kw):
            started.set()
            await release.wait()
            # What the frame shows: the pre-flip angle.
            return SolveResult(True, ra_hours=5.5, dec_deg=-5.4,
                               rotation_deg=30.0, pixel_scale_arcsec=1.5,
                               wcs=_wcs(), message="held")

    import astrodeck.providers as providers
    monkeypatch.setattr(providers, "pick_solver", lambda hub: _Held())

    await sim_hub.capture(0.05, 100, 30, 1, save=True, target="M42")
    await asyncio.wait_for(started.wait(), timeout=10)
    # The flip happens while that solve is still running; afterwards the rig
    # knows the truth (calibrated by the post-flip centring, here directly).
    _flip(sim_hub, holder, "west")
    rig.rotator_pa_offset_deg = 180.0
    await rot.sync(210.0)
    release.set()
    await asyncio.wait_for(sim_hub._wcs_queue.join(), timeout=30)

    got = await rot.get_position()
    assert near(got, 210.0), (
        f"a pre-flip solve overwrote the post-flip calibration: the rotator "
        f"reports {got:.1f} for a camera at 210.0")
    rec = sim_hub.last_sky_angle
    assert rec is not None and rec["source"] == "saved-frame WCS", rec
    assert near(rec["pa_deg"], 30.0) and rec["pier_side"] == "east", rec
    assert rec["calibrated"] is False, rec
    assert "east -> west" in rec["reason"], rec["reason"]


# --------------------------------------------------------------- the log line

async def test_the_night_log_gets_one_line_per_solve(sim_hub, monkeypatch):
    """The durable record is ``captures/logs/<night>.jsonl``: the 200-entry
    ring has rolled over by morning.

    MUTATION: delete the ``bus.log(...)`` call in ``note_solved_rotation``.
    Observed: "AssertionError: no sky-angle line in the night log". (Deleting
    the moved-rotator refusal also fails it, on the second line, which then
    reads "... rotator calibrated: ...".)
    """
    monkeypatch.setattr(bus, "night_log", NightLogWriter())
    rig = sim_hub.sim_rig
    rig.rotator_mech_deg = 35.0
    rig.rotator_pa_offset_deg = 20.0
    await sim_hub.solve_and_sync(0.05)
    _move_rotator_during_exposure(sim_hub, monkeypatch, 10.0)
    await sim_hub.solve_and_sync(0.05)

    lines = [r["data"]["message"] for r in bus.night_log.read(night_key())
             if r.get("type") == "log"
             and str(r["data"].get("message", "")).startswith("sky angle PA")]
    assert lines, "no sky-angle line in the night log"
    assert len(lines) == 2, lines
    assert lines[0].startswith("sky angle PA 55.0° from the plate solve + sync "
                               "solve"), lines[0]
    assert "rotator calibrated" in lines[0], lines[0]
    assert "rotator NOT calibrated: the rotator moved 10.00°" in lines[1], lines[1]


# ------------------------------------- the callers that exist to calibrate

async def test_a_rotate_whose_solve_cannot_calibrate_stops_instead_of_moving(
        sim_hub, monkeypatch):
    """``rotate_to_pa`` computes each move through the rotator's offset. An
    attempt whose solve could not calibrate it (here: the mount reported a
    different pier side after the exposure than before it) must stop, because
    a move through a stale offset is a rotation to the wrong angle.

    MUTATION: delete ``if not rec["calibrated"]: raise ...`` in
    ``Hub._rotate_to_pa_attempts``. Observed: "Failed: DID NOT RAISE <class
    'astrodeck.devices.base.DeviceError'>": the loop carried on and rotated
    through the uncalibrated offset (the case took ~20 s of simulated rotation
    instead of well under one). Deleting the recorder's pier-side comparison
    fails it the same way.
    """
    rig = sim_hub.sim_rig
    rig.rotator_mech_deg = 0.0
    holder = _pier(sim_hub, monkeypatch, "east")
    cam = sim_hub.require("camera")
    orig = cam.expose

    async def expose(*a, **k):
        frame = await orig(*a, **k)
        _flip(sim_hub, holder, "west" if holder["v"] == "east" else "east")
        return frame

    monkeypatch.setattr(cam, "expose", expose)
    with pytest.raises(DeviceError, match="could not calibrate the rotator"):
        await sim_hub.rotate_to_pa(90.0, exposure_s=0.05)
    assert rig.rotator_mech_deg == pytest.approx(0.0), "the rotator moved"


async def test_a_rotator_sync_that_cannot_calibrate_says_so(sim_hub,
                                                            monkeypatch):
    """``sync_rotator_to_sky`` is now a thin caller of the recorder; the one
    thing it adds is that a refused calibration is an error, not a log line.

    MUTATION: delete ``if not rec["calibrated"]: raise ...`` in
    ``Hub.sync_rotator_to_sky``. Observed: "Failed: DID NOT RAISE <class
    'astrodeck.devices.base.DeviceError'>". Deleting the recorder's moved-
    rotator refusal fails it the same way.
    """
    _move_rotator_during_exposure(sim_hub, monkeypatch, 10.0)
    with pytest.raises(DeviceError, match="not synced: the rotator moved"):
        await sim_hub.sync_rotator_to_sky(exposure_s=0.05)



def test_astap_without_crota2_reports_the_rotation_unknown():
    """#146 at the source: ASTAP's .ini with no CROTA2 used to become
    ``rotation_deg=0.0``, indistinguishable from a real PA 0.

    MUTATION: in ``_result_from_ini`` pass ``rotation_known=True``
    unconditionally. Observed: "AssertionError: a missing CROTA2 was reported
    as a known rotation".
    """
    from astrodeck.solve.astap import _result_from_ini
    base = {"PLTSOLVD": "T", "CRVAL1": "150.0", "CRVAL2": "20.0", "CDELT2": "0.0003"}
    missing = _result_from_ini(base, None)
    assert missing.success and missing.rotation_known is False, (
        "a missing CROTA2 was reported as a known rotation")
    present = _result_from_ini({**base, "CROTA2": "0.0"}, None)
    assert present.rotation_known is True and present.rotation_deg == 0.0, (
        "a real PA 0 must stay a known rotation")
