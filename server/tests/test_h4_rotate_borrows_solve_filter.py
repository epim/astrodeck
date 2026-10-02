# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Every imaging-camera solve that exposes its own frame borrows the solve
filter, and a failed solve through a narrowband filter says so (#531).

2026-09-29, 0.3.36 on the rig: the first mosaic (NGC 1499, 3x2, Rotate
to PA 0) started straight after a run whose last frame was SII 180 s. The
rotate loop exposed its solve frames through whatever the wheel held, so each
read 238 ADU against a 237 ADU no-light reference, and the #251 light check
said "no light: the optic is capped, covered or obstructed". Every panel then
shot unrotated. 25 s later the centring solve, which DID borrow the solve
filter ("plate solve: filter 'S' -> 'L'"), read 294.

So ``_rotate_to_pa_attempts`` now exposes through ``_borrow_wheel_for_solve``
and ``_return_wheel_after_solve`` exactly as ``solve_and_sync`` does: a
luminance-class slot around the exposure only, the wheel put back in a
``finally``. The other two paths in hub.py that expose an imaging frame for a
solve, the rotator sync and the guide-scope offset's main frame, did not
borrow either, and now do; each has its own case and mutant. And when the
frame still went through a narrowband filter (a wheel with nothing broader,
or a solve scope set to that filter), the light check names the filter
instead of calling the optic capped.

The wheel cases run on the simulator's wheel, with its Ha, OIII and SII
slots marked narrowband as a rig's profile marks them (the sim marks none by
default). Every mutation named below was run in a private scratch copy of
``server/`` (issue #254, #475), never in the shared tree. The failure each
produced is recorded verbatim on the test that caught it.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from _simhub import sim_hub  # noqa: F401 (fixture import)

from astrodeck.calibration.matcher import MasterRecord
from astrodeck.devices.base import CameraFrame, DeviceError
from astrodeck.solve import light
from astrodeck.solve.base import SolveResult

L_SLOT, SII_SLOT = 0, 6
NARROWBAND = [False, False, False, False, True, True, True, False]


def _mark_narrowband(hub) -> None:
    hub.devices["filterwheel"].filter_narrowband = list(NARROWBAND)


def _record_exposures(hub, monkeypatch, role: str = "camera") -> list[int]:
    """The wheel's slot at the moment each exposure of ``role`` began: the
    slot the frame was shot through. Delegates to the real expose."""
    cam = hub.devices[role]
    real = cam.expose
    slots: list[int] = []

    async def expose(*a, **kw):
        slots.append(hub.sim_rig.filter_slot)
        return await real(*a, **kw)

    monkeypatch.setattr(cam, "expose", expose)
    return slots


def _record_wheel_moves(hub, monkeypatch) -> list[int]:
    fw = hub.devices["filterwheel"]
    real = fw.set_position
    moves: list[int] = []

    async def set_position(slot):
        moves.append(int(slot))
        return await real(slot)

    monkeypatch.setattr(fw, "set_position", set_position)
    return moves


def _camera_needs_one_move(hub) -> None:
    """The camera at PA 30, so a rotate to 40 solves, moves and solves."""
    rig = hub.sim_rig
    rig.rotator_pa_offset_deg = 20.0
    rig.rotator_mech_deg = 10.0


# ------------------------------------------------------ the rotate borrows


async def test_the_rotate_solves_through_the_solve_filter(sim_hub,
                                                          monkeypatch):
    """The wheel on SII (narrowband). ``rotate_to_pa`` solves twice (solve,
    move, verify), and both frames are exposed through L; the wheel reads SII
    afterwards, having gone to L and back once per exposure.

    Mutation 'the rotate path skips the borrow' (the ``_borrow_wheel_for_solve``
    call in ``_rotate_to_pa_attempts`` replaced with ``None``) went red here:

        >       assert slots == [L_SLOT, L_SLOT], slots
        E       AssertionError: [6, 6]
        E       assert [6, 6] == [0, 0]
    """
    _mark_narrowband(sim_hub)
    sim_hub.sim_rig.filter_slot = SII_SLOT
    _camera_needs_one_move(sim_hub)
    slots = _record_exposures(sim_hub, monkeypatch)
    moves = _record_wheel_moves(sim_hub, monkeypatch)

    result = await sim_hub.rotate_to_pa(40.0)

    assert result["rotated"] is True and result["attempts"] == 2, result
    assert slots == [L_SLOT, L_SLOT], slots
    assert sim_hub.sim_rig.filter_slot == SII_SLOT
    assert moves == [L_SLOT, SII_SLOT, L_SLOT, SII_SLOT], moves


async def test_a_wheel_already_on_l_does_not_move(sim_hub, monkeypatch):
    """Control: the wheel on L passes broad light, so the borrow leaves it
    alone and the rotate solves through it."""
    _mark_narrowband(sim_hub)
    sim_hub.sim_rig.filter_slot = L_SLOT
    _camera_needs_one_move(sim_hub)
    slots = _record_exposures(sim_hub, monkeypatch)
    moves = _record_wheel_moves(sim_hub, monkeypatch)

    result = await sim_hub.rotate_to_pa(40.0)

    assert result["rotated"] is True, result
    assert slots == [L_SLOT, L_SLOT], slots
    assert moves == [], moves
    assert sim_hub.sim_rig.filter_slot == L_SLOT


async def test_the_rotator_sync_solves_through_the_solve_filter(sim_hub,
                                                                monkeypatch):
    """The rotator sync (``sync_rotator_to_sky``) exposed without the borrow
    too. It is pressed between runs, when the wheel is wherever the last run
    left it.

    Mutation 'the rotator sync skips the borrow' (the
    ``_borrow_wheel_for_solve`` call in ``sync_rotator_to_sky`` replaced with
    ``None``) went red here:

        >       assert slots == [L_SLOT], slots
        E       AssertionError: [6]
        E       assert [6] == [0]
    """
    _mark_narrowband(sim_hub)
    sim_hub.sim_rig.filter_slot = SII_SLOT
    slots = _record_exposures(sim_hub, monkeypatch)
    moves = _record_wheel_moves(sim_hub, monkeypatch)

    out = await sim_hub.sync_rotator_to_sky()

    assert out["synced"] is True, out
    assert slots == [L_SLOT], slots
    assert moves == [L_SLOT, SII_SLOT], moves
    assert sim_hub.sim_rig.filter_slot == SII_SLOT


async def test_the_guide_offset_main_frame_solves_through_the_solve_filter(
        sim_hub, monkeypatch):
    """The guide-scope offset measurement solves an imaging frame and a guide
    frame. The imaging frame borrows the solve filter; the guide camera's
    does not, since the wheel is not in its light path, so the wheel moves
    once there and once back.

    Mutation 'the guide offset skips the borrow' (``_expose`` called with
    ``borrow=False`` for the main frame) went red here:

        >       assert slots == [L_SLOT], slots
        E       AssertionError: [6]
        E       assert [6] == [0]
    """
    _mark_narrowband(sim_hub)
    sim_hub.sim_rig.filter_slot = SII_SLOT
    slots = _record_exposures(sim_hub, monkeypatch)
    moves = _record_wheel_moves(sim_hub, monkeypatch)

    await sim_hub.measure_guide_offset(exposure_s=0.05, guide_exposure_s=0.05)

    assert slots == [L_SLOT], slots
    assert moves == [L_SLOT, SII_SLOT], moves
    assert sim_hub.sim_rig.filter_slot == SII_SLOT


# ------------------------------------------- the verdict names the filter

SHAPE = (240, 320)


def _bias_level_frame(level: float = 238.0, seed: int = 531) -> CameraFrame:
    """A 12 s solve frame at the no-light level, 238 ADU as on the night."""
    rng = np.random.default_rng(seed)
    data = np.clip(np.rint(rng.normal(level, 4.4, SHAPE)), 0,
                   65535).astype(np.uint16)
    return CameraFrame(data=data, exposure_s=12.0, gain=200, offset=30,
                       binning=2, bayer_pattern=None, temperature_c=18.5,
                       timestamp=0.0)


class _Library:
    def __init__(self, masters):
        self._masters = masters

    def list_masters(self):
        return list(self._masters)


def _dark_library(tmp_path: Path, level: float = 237.0) -> _Library:
    """One dark master at the frame's settings, 237 ADU as on the night."""
    path = tmp_path / "dark_237.fits"
    rng = np.random.default_rng(7)
    fits.PrimaryHDU((level + rng.normal(0, 0.5, (64, 64)))
                    .astype(np.float32)).writeto(path)
    return _Library([MasterRecord(
        id="dark_237", frame_type="DARK", exposure_s=12.0, gain=200,
        offset=30, temp_c=18.5, binning=2, filter="", frame_count=20,
        path=str(path), built_ts=1.0)])


class _Hub:
    """What ``failed_solve_error`` reads of a hub when the library has a
    master for the frame: only the library."""

    def __init__(self, library):
        self.master_library = library
        self.devices: dict = {}


_FAILED = SolveResult(False, message="Not enough stars.")


async def test_a_no_light_frame_through_a_narrowband_filter_names_it(
        tmp_path, bus_lines):
    """The night's numbers: 238 ADU against a 237 ADU dark master, through
    SII. The frame reads at the no-light level, and that is all it says: a
    short exposure through 3 nm reads it with the optic open. So the failure
    names the filter, makes no claim about a cap, and is not a
    ``NoLightError`` (which would send ResumeArm's cap alert). The evidence
    line keeps the numbers.

    Mutation 'verdict ignores the loaded filter' (the ``NARROWBAND``
    replacement in ``failed_solve_error`` made ``if False:``) went red here
    and on all three hub paths' narrowband cases below:

        >       assert not isinstance(e, light.NoLightError), e
        E       AssertionError: NoLightError('rotate: plate solve failed: no light: the optic is capped, covered or obstructed (the frame reads at the level this camera reads with no light on it; the solver said: Not enough stars.)')
        E       assert not True
    """
    e = await light.failed_solve_error(
        _bias_level_frame(), _FAILED, prefix="rotate: plate solve failed",
        hub=_Hub(_dark_library(tmp_path)), narrowband_filter="S")
    assert not isinstance(e, light.NoLightError), e
    assert isinstance(e, light.FailedSolveError) and isinstance(e, DeviceError)
    msg = str(e)
    assert msg.startswith(
        "rotate: plate solve failed: little light through the narrowband "
        "filter 'S'"), msg
    assert light.NO_LIGHT_WORDS not in msg and "capped" not in msg, msg
    assert "Not enough stars." in msg, msg
    assert e.verdict.kind == light.NARROWBAND, e.verdict
    evidence = [m for _, m, src in bus_lines
                if src == "solve" and "light check" in m]
    assert len(evidence) == 1, bus_lines
    assert "reference 237.0 ADU" in evidence[0] \
        and "narrowband filter 'S'" in evidence[0], evidence


async def test_a_broadband_frame_with_no_light_still_reads_capped(tmp_path):
    """Control: the same frame through a broadband filter (no narrowband
    filter named) is the #251 verdict, a ``NoLightError`` in its words."""
    e = await light.failed_solve_error(
        _bias_level_frame(), _FAILED, prefix="rotate: plate solve failed",
        hub=_Hub(_dark_library(tmp_path)), narrowband_filter=None)
    assert isinstance(e, light.NoLightError), e
    assert str(e).startswith(
        f"rotate: plate solve failed: {light.NO_LIGHT_WORDS}"), e


async def test_a_lit_frame_through_a_narrowband_filter_keeps_its_verdict(
        tmp_path):
    """Control: light reached the sensor (the frame is 60 ADU over the
    reference), so it is cloud whatever the filter, and the filter is not
    named: only a no-light verdict is re-read."""
    e = await light.failed_solve_error(
        _bias_level_frame(level=297.0), _FAILED,
        prefix="rotate: plate solve failed",
        hub=_Hub(_dark_library(tmp_path)), narrowband_filter="S")
    assert e.verdict.kind == light.CLOUD, e.verdict
    assert "narrowband" not in str(e), e


# ------------------------------------ every hub path passes the filter on

class _NoSolve:
    name = "failing"

    async def solve(self, path, **kw):
        return SolveResult(False, message="Not enough stars.")


def _nothing_broader(hub, monkeypatch, tmp_path, *, names, narrowband, slot):
    """A wheel of ``names``, flagged ``narrowband``, on ``slot``; the camera
    returns the night's bias-level frame whatever it is asked, every solver
    fails as ASTAP did, and the dark master is the night's."""
    import astrodeck.providers as providers
    fw = hub.devices["filterwheel"]
    fw.filter_names = list(names)
    fw.filter_narrowband = list(narrowband)
    fw.filter_opaque = [False] * len(names)
    hub.sim_rig.filter_slot = slot
    cam = hub.devices["camera"]

    async def expose(seconds, gain, offset, binning=1, **kw):
        return _bias_level_frame()

    monkeypatch.setattr(cam, "expose", expose)
    monkeypatch.setattr(providers, "pick_solver", lambda h: _NoSolve())
    hub.master_library = _dark_library(tmp_path)


_PATHS = {
    "solve": (lambda h: h.solve_and_sync(12.0), "plate solve failed"),
    "rotsync": (lambda h: h.sync_rotator_to_sky(exposure_s=12.0),
                "rotator sync: plate solve failed"),
    "rotate": (lambda h: h.rotate_to_pa(90.0, exposure_s=12.0),
               "rotate: plate solve failed"),
}


@pytest.mark.parametrize("path", sorted(_PATHS))
async def test_a_wheel_with_nothing_broader_names_its_filter(
        sim_hub, monkeypatch, tmp_path, path):
    """A wheel of Ha, OIII and S, all narrowband, on S: the borrow has
    nothing broader to move to (and warns), so the frame goes through S, and
    each of the three hub paths passes that on to the light check.

    Mutation 'rotate drops the loaded filter' (``narrowband_filter=through``
    removed from ``_rotate_to_pa_attempts``'s ``failed_solve_error`` call)
    went red on the rotate case alone:

        >       assert not isinstance(e.value, light.NoLightError), e.value
        E       AssertionError: NoLightError('rotate: plate solve failed: no light: the optic is capped, covered or obstructed (the frame reads at the level this camera reads with no light on it; the solver said: Not enough stars.)')
        E       assert not True

    and 'solve drops the loaded filter' and 'rotsync drops the loaded
    filter', the same removal in ``solve_and_sync`` and in
    ``sync_rotator_to_sky``, each went red on its own case alone, with the
    failure above under its own prefix:

        E       AssertionError: NoLightError('plate solve failed: no light: the optic is capped, covered or obstructed (the frame reads at the level this camera reads with no light on it; the solver said: Not enough stars.)')
        E       AssertionError: NoLightError('rotator sync: plate solve failed: no light: the optic is capped, covered or obstructed (the frame reads at the level this camera reads with no light on it; the solver said: Not enough stars.)')
    """
    _nothing_broader(sim_hub, monkeypatch, tmp_path,
                     names=["Ha", "OIII", "S"], narrowband=[True, True, True],
                     slot=2)
    call, prefix = _PATHS[path]
    with pytest.raises(light.FailedSolveError) as e:
        await call(sim_hub)
    assert not isinstance(e.value, light.NoLightError), e.value
    assert str(e.value).startswith(
        f"{prefix}: little light through the narrowband filter 'S'"), e.value


@pytest.mark.parametrize("path", sorted(_PATHS))
async def test_a_borrowed_frame_is_not_blamed_on_the_run_s_filter(
        sim_hub, monkeypatch, tmp_path, path):
    """The wheel on S with L beside it: the borrow moves to L, the frame goes
    through L, and the wheel is back on S by the time the solve fails. A
    frame through L at the no-light level IS the capped optic, so the
    failure is the #251 verdict, and S is not named. The filter must be read
    between the borrow and the return (``_narrowband_filter_loaded``'s
    docstring): read after the return, it names the run's filter for a frame
    that never went through it, and a capped optic reads as a narrowband
    passband.

    Added by the H4-HUB verifier: no case above could tell WHEN the filter
    was read, since in each of them the borrow either had nothing to move to
    or did not need to move. Mutation 'rotate reads the filter after the
    return' (the ``_narrowband_filter_loaded`` read in
    ``_rotate_to_pa_attempts`` moved below the ``finally`` that returns the
    wheel) left every case in this file green before this one, and went red
    here on the rotate case alone:

        E               astrodeck.solve.light.FailedSolveError: rotate: plate solve failed: little light through the narrowband filter 'S' (the frame reads at the level this camera reads with no light on it, which a solve exposure through a narrowband filter reads with the optic open, so it is no evidence of a cap; the solver said: Not enough stars.)

    'solve reads the filter after the return' and 'rotsync reads the filter
    after the return', the same move in ``solve_and_sync`` and in
    ``sync_rotator_to_sky``, each went red on its own case alone, with the
    failure above under its own prefix (the parenthesis, identical, cut
    here as "..."):

        E           astrodeck.solve.light.FailedSolveError: plate solve failed: little light through the narrowband filter 'S' (...)
        E           astrodeck.solve.light.FailedSolveError: rotator sync: plate solve failed: little light through the narrowband filter 'S' (...)
    """
    _nothing_broader(sim_hub, monkeypatch, tmp_path,
                     names=["L", "Ha", "OIII", "S"],
                     narrowband=[False, True, True, True], slot=3)
    call, prefix = _PATHS[path]
    with pytest.raises(light.NoLightError) as e:
        await call(sim_hub)
    assert str(e.value).startswith(f"{prefix}: {light.NO_LIGHT_WORDS}"), e
    assert "narrowband" not in str(e.value), e
    assert sim_hub.sim_rig.filter_slot == 3


@pytest.mark.parametrize("path", sorted(_PATHS))
async def test_a_broadband_slot_with_no_light_still_reads_capped(
        sim_hub, monkeypatch, tmp_path, path):
    """Control: the wheel on L, the same bias-level frame. Nothing is
    narrowband, so each path's failure is the #251 verdict."""
    _nothing_broader(sim_hub, monkeypatch, tmp_path, names=["L", "Ha"],
                     narrowband=[False, True], slot=0)
    call, prefix = _PATHS[path]
    with pytest.raises(light.NoLightError) as e:
        await call(sim_hub)
    assert str(e.value).startswith(f"{prefix}: {light.NO_LIGHT_WORDS}"), e
