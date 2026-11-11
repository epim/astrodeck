# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP 10.4 (#1182): the hold classifier judges the frame the failed solve ran
on, not whatever ``hub.last_frame`` holds by the time the engine asks.

THE DEFECT. ``SequenceEngine._classify_failed_solve`` read ``hub.last_frame``.
A live preview, a guide loop or a later capture replaces that attribute at any
moment, so a hold could name an obstruction, cloud or a capped optic from a
frame of some other field.

THE FIX. The hub classifies the solve's own frame at the failure and carries
the verdict in the centring result (``solve_frame_class``, the class's value:
a short word, never pixels). The engine reads the key; a result without one
(a refused or aborted goto, a solve of a file on disk, a result built by a
stub) has no frame to judge and the classifier answers None, which every
caller already reads as "keep the generic wording": a hold never claims more
than the pixels showed.

MUTANT, 2026-11-11, from a byte backup of ``astrodeck/sequence/engine.py``,
restored and sha256-verified: ``_classify_failed_solve`` reading
``hub.last_frame`` again. ``test_a_newer_preview_frame_does_not_change_the_
verdict`` then fails with the verdict of the preview (``FrameClass.STARS``)
where ``FrameClass.OBSTRUCTED`` was expected.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pytest

import astrodeck.providers as providers
from _simhub import sim_hub  # noqa: F401 (fixture import)
from astrodeck.devices.base import CameraFrame
from astrodeck.hub import Hub
from astrodeck.imaging.frame_class import FrameClass
from astrodeck.sequence import SequenceEngine
from astrodeck.solve.base import SolveResult

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "frame_class"
if str(_FIXTURES) not in sys.path:
    sys.path.append(str(_FIXTURES))
import synth_frames as synth  # noqa: E402

RA, DEC = 5.0, 10.0


@pytest.fixture(autouse=True)
def _the_sun_is_not_the_subject(monkeypatch):
    monkeypatch.setattr(Hub, "_check_solar",
                        lambda self, ra, dec, *, force=False: None)


def _frame(data: np.ndarray) -> CameraFrame:
    return CameraFrame(data=data, exposure_s=4.0, gain=200, offset=30,
                       binning=2, bayer_pattern=None, temperature_c=-5.0,
                       timestamp=time.time())


def _rig_that_cannot_solve(hub, monkeypatch, shot: np.ndarray):
    """Every exposure reads ``shot`` and every solve fails as ASTAP does. No
    master library, so no self-reference is shot and the light check is
    quiet."""
    cam = hub.require("camera")

    async def expose(seconds, gain, offset, binning=1, **kw):
        return _frame(shot)

    class _NoSolve:
        name = "failing"

        async def solve(self, path, **kw):
            return SolveResult(False, message="Not enough stars.")

    monkeypatch.setattr(cam, "expose", expose)
    monkeypatch.setattr(providers, "pick_solver", lambda h: _NoSolve())
    hub.master_library = None
    hub.sim_rig.ra_hours = RA
    hub.sim_rig.dec_deg = DEC


async def test_a_newer_preview_frame_does_not_change_the_verdict(
        sim_hub, monkeypatch):
    """A centring solve fails on a frame with a treeline across it. Before
    the engine asks why, a preview replaces ``hub.last_frame`` with a clear
    star field. The verdict is still the obstruction: it is the frame the
    solve ran on that is judged.

    RED under mutant "read hub.last_frame again" (``frame = getattr(self.hub,
    "last_frame", None)`` back in ``_classify_failed_solve``), observed:

        assert <FrameClass.STARS: 'stars'> is <FrameClass.OBSTRUCTED: ...>
    """
    _rig_that_cannot_solve(sim_hub, monkeypatch, synth.obstructed_frame())
    result = await sim_hub.goto_and_center(RA, DEC)
    assert result["solve_failed"] is True, result

    sim_hub.last_frame = _frame(synth.stars_frame())

    cls = await SequenceEngine(sim_hub)._classify_failed_solve(result)
    assert cls is FrameClass.OBSTRUCTED


async def test_the_result_carries_a_word_and_never_a_frame(
        sim_hub, monkeypatch):
    """The carried verdict is the class's value, so the result stays small,
    serialisable and free of pixels (a full frame held twice costs memory on
    the Orange Pi)."""
    _rig_that_cannot_solve(sim_hub, monkeypatch, synth.obstructed_frame())
    result = await sim_hub.goto_and_center(RA, DEC)

    assert result["solve_frame_class"] == FrameClass.OBSTRUCTED.value
    assert not any(isinstance(v, (np.ndarray, CameraFrame))
                   for v in result.values()), result


async def test_a_result_without_the_solves_frame_is_not_judged_from_the_hub(
        sim_hub):
    """A failed solve whose frame was not carried (a stub, a solve of a file
    on disk) is not judged from whatever the hub last held: that would blame
    another field. None keeps every hold's generic wording."""
    sim_hub.last_frame = _frame(synth.obstructed_frame())
    bare = {"centered": False, "error_arcmin": None, "attempts": 1,
            "solve_failed": True}

    assert await SequenceEngine(sim_hub)._classify_failed_solve(bare) is None


async def test_a_dark_solve_is_still_no_light_from_the_carried_class(
        sim_hub):
    """The hub's own no-light verdict still replaces a cloud or unknown
    reading, now read from the carried class."""
    cloud = {"centered": False, "error_arcmin": None, "attempts": 1,
             "solve_failed": True,
             "solve_frame_class": FrameClass.CLOUD.value,
             "solve_light": "dark"}

    assert (await SequenceEngine(sim_hub)._classify_failed_solve(cloud)
            is FrameClass.NO_LIGHT)


async def test_a_refused_goto_carries_no_class(sim_hub, monkeypatch):
    """A centred goto carries no ``solve_frame_class``: only a failed solve
    has a frame to name."""
    result = await sim_hub.goto_and_center(RA, DEC)
    assert "solve_frame_class" not in result, result
