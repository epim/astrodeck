# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP 3.5 (#599, #1093), fix round 1: the engine feeds the horizon learner and
the hold line says what to do about a missing horizon.

``site_horizon`` collects obstructed solve frames and offers a horizon, but it
learns nothing unless the engine hands it the frames. Three seams do:

  * ``_hold_for_light`` files every frame it classifies (an obstruction is a
    refusal in the direction the target stood);
  * ``_note_pass_frame_class`` does the same for a mosaic panel's failed
    centring;
  * ``_note_centring_evidence`` files a ``STARS`` frame when a centring was
    good, which is the evidence the other way.

The hold line then carries ``site_horizon.hold_hint``: that no horizon is
drawn and where to draw it, or that a suggestion is waiting.

The site is made up and is nowhere in the world. The frames are the synthetic
ones of ``test_w2_frame_class.py``.

MUTANT (named in the work package, fix round): the filing call deleted from
``_hold_for_light``. ``test_an_obstructed_frame_in_a_hold_reaches_the_learner``
fails.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

import astrodeck.sequence.engine as engine_mod
from astrodeck import site_horizon
from astrodeck.catalog.coords import altaz
from astrodeck.imaging.frame_class import FrameClass
from astrodeck.sequence import SequenceEngine
from test_w2_holds_classified import (FAILED, _judged, _lines,  # noqa: F401
                                      _scripted, _target, sim_hub, synth)

LAT, LON = 12.5, 34.5
SITE = {"name": "Test field", "latitude": LAT, "longitude": LON,
        "elevation_m": 10.0, "is_default": False, "horizon_min_deg": 0.0}


def _radec_now(alt: float, az: float) -> tuple[float, float]:
    """The (ra_hours, dec_deg) standing at (alt, az) from the made-up site now."""
    now = time.time()
    best = None
    for ra10 in range(0, 240):
        for dec in range(-60, 90):
            a, z = altaz(ra10 / 10.0, float(dec), LAT, LON, now)
            err = abs(a - alt) + abs(((z - az) + 180) % 360 - 180)
            if best is None or err < best[0]:
                best = (err, ra10 / 10.0, float(dec))
    assert best[0] < 6.0
    return best[1], best[2]


@pytest.fixture(autouse=True)
def _clean_learner():
    site_horizon.LEARNER.clear()
    yield
    site_horizon.LEARNER.clear()


@pytest.fixture
def sited(sim_hub, monkeypatch):
    monkeypatch.setattr(type(sim_hub), "site", property(lambda self: dict(SITE)))
    monkeypatch.setattr(engine_mod, "CENTRING_HOLD_RETRY_S", 0.01)
    # The hold retries only inside the target's own window, which the real
    # `target_own_window` computes from the made-up site and the wall clock:
    # in that site's daytime the window has closed and the hold never loops,
    # so these tests passed only at the hours of the day they were written in.
    # Here the window is open for an hour either side of now, whatever the hour.
    monkeypatch.setattr(
        "astrodeck.flows.tonight.target_own_window",
        lambda *a, **k: {"start_unix": time.time() - 3600.0,
                         "end_unix": time.time() + 3600.0})
    return sim_hub


def _engine(hub, monkeypatch, horizon=None):
    engine = SequenceEngine(hub)
    monkeypatch.setattr(engine_mod.config_store.cfg().safety, "horizon", horizon)
    return engine


async def test_an_obstructed_frame_in_a_hold_reaches_the_learner(
        sited, monkeypatch):
    engine = _engine(sited, monkeypatch)
    _scripted(sited, monkeypatch, FAILED, synth.obstructed_frame())
    ra, dec = _radec_now(35.0, 130.0)

    await engine._hold_for_light(
        _target(name="learn-tree", ra_hours=ra, dec_deg=dec), None, _judged(FAILED, synth.obstructed_frame()))

    offer = site_horizon.LEARNER.suggestion(None)
    assert offer is not None, "six obstructed frames in a hold taught nothing"
    assert offer.frames >= site_horizon.MIN_REGION_FRAMES


async def test_a_cloud_frame_in_a_hold_teaches_nothing(sited, monkeypatch):
    engine = _engine(sited, monkeypatch)
    _scripted(sited, monkeypatch, FAILED, synth.cloud_frame())
    ra, dec = _radec_now(35.0, 130.0)

    await engine._hold_for_light(
        _target(name="learn-cloud", ra_hours=ra, dec_deg=dec), None, _judged(FAILED, synth.cloud_frame()))

    assert site_horizon.LEARNER.suggestion(None) is None


async def test_the_default_site_files_nothing(sim_hub, monkeypatch):
    """A rig with no site configured has no sky to file a frame under."""
    monkeypatch.setattr(engine_mod, "CENTRING_HOLD_RETRY_S", 0.01)
    filed = []
    monkeypatch.setattr(site_horizon, "note_solve_frame",
                        lambda *a, **k: filed.append(a))
    engine = _engine(sim_hub, monkeypatch)
    _scripted(sim_hub, monkeypatch, FAILED, synth.obstructed_frame())

    await engine._hold_for_light(_target(name="no-site"), None, _judged(FAILED, synth.obstructed_frame()))

    assert filed == []


async def test_a_filing_failure_never_breaks_the_hold(sited, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("learner down")

    monkeypatch.setattr(site_horizon, "note_solve_frame", boom)
    engine = _engine(sited, monkeypatch)
    calls = _scripted(sited, monkeypatch, FAILED, synth.obstructed_frame())

    result = await engine._hold_for_light(_target(name="learner-down"), None,
                                          _judged(FAILED, synth.obstructed_frame()))

    assert not result["centered"]
    assert calls["n"] == engine._NO_LIGHT_MAX_RETRIES


async def test_a_panels_failed_centring_reaches_the_learner(sited, monkeypatch):
    filed = []
    monkeypatch.setattr(site_horizon, "note_solve_frame",
                        lambda cls, *a, **k: filed.append(cls))
    engine = _engine(sited, monkeypatch)
    _scripted(sited, monkeypatch, FAILED, synth.obstructed_frame())
    group = SimpleNamespace(id="g1")

    await engine._note_pass_frame_class(group, _judged(FAILED, synth.obstructed_frame()),
                                        _target(name="panel"))

    assert filed == [FrameClass.OBSTRUCTED]


def test_a_good_centring_files_a_clear_frame(sited, monkeypatch):
    filed = []
    monkeypatch.setattr(site_horizon, "note_solve_frame",
                        lambda cls, *a, **k: filed.append(cls))
    engine = _engine(sited, monkeypatch)

    engine._note_centring_evidence({"centered": True, "error_arcmin": 0.4},
                                   _target(name="good"))
    engine._note_centring_evidence({"centered": False, "error_arcmin": None},
                                   _target(name="bad"))

    assert filed == [FrameClass.STARS]


async def test_a_hold_with_no_horizon_says_what_to_do(sited, monkeypatch):
    engine = _engine(sited, monkeypatch, horizon=None)
    _scripted(sited, monkeypatch, FAILED, synth.obstructed_frame())

    await engine._hold_for_light(_target(name="bare-site"), None, _judged(FAILED, synth.obstructed_frame()))

    lines = _lines("bare-site", "obstruction")
    assert len(lines) == 1, lines
    assert "no horizon is drawn" in lines[0] and "Site page" in lines[0]


async def test_a_generic_hold_with_no_horizon_says_it_too(sited, monkeypatch):
    engine = _engine(sited, monkeypatch, horizon=None)
    _scripted(sited, monkeypatch, FAILED, None)

    await engine._hold_for_light(_target(name="bare-generic"), None,
                                 _judged(FAILED, None))

    lines = _lines("bare-generic", "holding for light")
    assert len(lines) == 1, lines
    assert "no horizon is drawn" in lines[0]


async def test_a_hold_with_a_horizon_says_nothing_about_one(sited, monkeypatch):
    engine = _engine(sited, monkeypatch, horizon=[(0.0, 5.0), (180.0, 5.0)])
    _scripted(sited, monkeypatch, FAILED, synth.obstructed_frame())

    await engine._hold_for_light(_target(name="drawn-site"), None, _judged(FAILED, synth.obstructed_frame()))

    lines = _lines("drawn-site", "obstruction")
    assert len(lines) == 1, lines
    assert "horizon" not in lines[0]


async def test_a_waiting_suggestion_is_named_in_the_hold(sited, monkeypatch):
    engine = _engine(sited, monkeypatch, horizon=[(0.0, 5.0), (180.0, 5.0)])
    _scripted(sited, monkeypatch, FAILED, synth.obstructed_frame())
    for az in (121.0, 126.0, 131.0, 136.0, 141.0, 146.0):
        site_horizon.LEARNER.note(FrameClass.OBSTRUCTED, 37.0, az, time.time())

    await engine._hold_for_light(_target(name="learned-site"), None,
                                 _judged(FAILED, synth.obstructed_frame()))

    lines = _lines("learned-site", "obstruction")
    assert len(lines) == 1, lines
    assert "suggestion" in lines[0] and "Site page" in lines[0]
