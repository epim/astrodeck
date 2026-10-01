"""WP-30a (a): ``hub.last_sky_angle`` keeps the record with the newest
``exposed_at``, not the record from whichever solve happened to finish last
(#292).

The per-frame WCS stamp solves a saved light in the background, seconds
behind the shutter (``Hub._solve_and_stamp``). A later centring solve's own
record can therefore land in ``hub.last_sky_angle`` first, and the WCS
stamp's solve -- of an OLDER exposure -- can still be running. When it
finishes it used to overwrite the newer record unconditionally, because
``note_solved_rotation`` stored whatever solve completed last. The mosaic
angle check and the angle lock (#189 S2/T15) both read ``last_sky_angle`` to
ask "has THIS hop's camera been measured yet", and a stale answer there
reads as "no measurement" for a camera that just proved itself.

Calibration is untouched by this: the rotator is still synced from every
solve that is provably safe to sync from (see test_solved_sky_angle.py for
that contract in full). Only the BOOKKEEPING -- which record ``hub`` holds
as "the current angle" -- is keyed on the exposure instead of on completion
order.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from _simhub import sim_hub  # noqa: F401 (fixture import)
from astrodeck import sky_angle
from astrodeck.solve.base import SolveResult

pytestmark = pytest.mark.asyncio


async def _ctx(sim_hub, at: float):
    """An exposure context for the hub's real imaging camera, timestamped
    ``at``. No rotator attached to the context, so the calibration refusal
    ("the rotator's angle was not read") fires every time and the rotator is
    never touched -- this test is about storage order, not calibration."""
    cam = sim_hub.require("camera")
    base = await sky_angle.exposure_context(sim_hub, cam)
    return replace(base, at=at, rotator=None, mech_deg=None, moving=None)


async def test_a_late_finishing_solve_of_an_older_exposure_does_not_overwrite_the_newer_record(
        sim_hub):
    """Two solves land out of order: the newer exposure's solve finishes
    first (as a fast centring solve would), then the older exposure's solve
    finishes after it (as the background WCS stamp can). The held record
    must stay the newer EXPOSURE's, not whichever solve finished last.

    MUTATION: in ``note_solved_rotation``, replace the exposed_at-gated
    assignment with the unconditional ``hub.last_sky_angle = rec`` it used to
    be. Observed: "AssertionError: a solve of an OLDER exposure overwrote the
    newer one: pa_deg=77.0 exposed_at=100.0".
    """
    newer_ctx = await _ctx(sim_hub, at=200.0)
    older_ctx = await _ctx(sim_hub, at=100.0)

    await sky_angle.note_solved_rotation(
        sim_hub, SolveResult(True, rotation_deg=55.0), source="centring",
        context=newer_ctx)
    held = sim_hub.last_sky_angle
    assert held is not None and held["exposed_at"] == 200.0, held

    # The OLDER exposure's solve lands AFTER the newer one's, as the
    # background WCS stamp's solve can finish behind a later centring solve.
    out = await sky_angle.note_solved_rotation(
        sim_hub, SolveResult(True, rotation_deg=77.0), source="saved-frame WCS",
        context=older_ctx)
    assert out is not None and out["pa_deg"] == pytest.approx(77.0), (
        "the late solve itself must still be recorded as a return value "
        "(published and logged) -- only the HELD record is guarded")

    held = sim_hub.last_sky_angle
    assert held["exposed_at"] == 200.0 and held["pa_deg"] == pytest.approx(55.0), (
        f"a solve of an OLDER exposure overwrote the newer one: "
        f"pa_deg={held['pa_deg']} exposed_at={held['exposed_at']}")


async def test_a_solve_of_a_genuinely_newer_exposure_does_replace_the_held_record(
        sim_hub):
    """Control for the test above: in this exact state (a held record from an
    earlier exposure), a solve of a REAL later exposure must still win. If
    this failed too, the guard above would not be proof of anything -- it
    would mean nothing can ever replace the first record.

    MUTATION: in ``note_solved_rotation``, compare with ``>`` the held
    record's exposed_at both ways frozen (e.g. always keep the FIRST record,
    never updating). Observed: "AssertionError: a genuinely newer exposure's
    solve did not update the held record".
    """
    first_ctx = await _ctx(sim_hub, at=100.0)
    later_ctx = await _ctx(sim_hub, at=300.0)

    await sky_angle.note_solved_rotation(
        sim_hub, SolveResult(True, rotation_deg=10.0), source="centring",
        context=first_ctx)
    await sky_angle.note_solved_rotation(
        sim_hub, SolveResult(True, rotation_deg=99.0), source="centring",
        context=later_ctx)

    held = sim_hub.last_sky_angle
    assert held["exposed_at"] == 300.0 and held["pa_deg"] == pytest.approx(99.0), (
        "a genuinely newer exposure's solve did not update the held record")


async def test_an_equal_exposed_at_still_updates_the_held_record(sim_hub):
    """Two solves of the SAME exposure (the capture path's own context object
    can be handed to more than one solver in principle) must not deadlock
    each other out: "at or after", not "strictly after". Guards against an
    overcorrection that turns the fix into "first solve of an exposure wins"
    instead of "newest exposure wins".

    MUTATION: change the fix's ``>=`` to ``>``. Observed: "AssertionError: a
    second solve of the SAME exposure was refused".
    """
    ctx = await _ctx(sim_hub, at=150.0)

    await sky_angle.note_solved_rotation(
        sim_hub, SolveResult(True, rotation_deg=10.0), source="centring",
        context=ctx)
    await sky_angle.note_solved_rotation(
        sim_hub, SolveResult(True, rotation_deg=20.0), source="saved-frame WCS",
        context=ctx)

    held = sim_hub.last_sky_angle
    assert held["pa_deg"] == pytest.approx(20.0), (
        "a second solve of the SAME exposure was refused")
