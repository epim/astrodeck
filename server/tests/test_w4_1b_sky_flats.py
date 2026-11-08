# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Twilight-sky flats in the DUSK FLATS stage (#603 job C, #1111 C-05; wave 4,
WP 4.1b).

THE CLASS: a claim nothing keeps. The node offers three methods and the stage
ran one. The flat-panel method (job B) is a constant lamp. The twilight-sky
method meters a light source that changes by the minute, so the stage has to do
what a lamp never asked of it: wait for the Sun to reach the node's window,
open the cover (the cover is all that stands between the sky and the sensor),
meter, shoot at the metered exposure, RE-METER as the sky darkens, and stop,
saying why, when the sky is too dark for the longest exposure or the Sun has
left the window.

THE SIMULATOR IS CLOCKED (tests/_group_harness.py). The engine's clock, its
sleeps and the sim camera's exposure dwell are one fake clock, and the
camera's median is a function of that clock and of the Sun's altitude at the
fixture site (``SKY_MODEL``), so a twilight of an hour runs in a second and
the sky really does darken under a running set. The real ``_wait_until``,
``_solve_flat_exposure`` and ``_run_calibration`` run; the scheduler is
replaced by a marker so the first light is a point in the order of events.

What is held:

* the stage waits for the window's upper edge and starts there, and not before;
* it meters (unsaved trial exposures) and then shoots saved flats; the solved
  exposure grows, never shrinks, as the Sun falls (the clocked ramp);
* it stops when the sky is too dark for the longest exposure, mid-set, and says
  so, and shoots no set after;
* it starts no set once the Sun is below the window's lower edge, and finishes
  one it began inside;
* the cover is opened for the sky and shut again if it was found shut (left
  open if it was found open);
* every line whose moment is a Sun crossing is flagged ``site_derived`` and a
  viewer is served none of them.

Named mutant: the per-angle loop skipped, in ``test_w4_1b_flat_angles.py``
(this file holds the sky half and has its own, below). Mutant for this file:
``_run_calibration``'s sky re-meter break removed (a set goes on at the longest
exposure when the sky is too dark): ``test_the_stage_stops_when_the_sky_is_too_
dark`` shoots the whole count.
"""
from __future__ import annotations

import pytest

from _group_harness import (LAT, LON, T0, Night, close_night_hub,  # noqa: F401
                            group_store, night_hub)
from astrodeck.api.redact import _redact_log_rows_for
from astrodeck.auth import principal_for_role
from astrodeck.config import Site
from astrodeck.devices.base import CoverState
from astrodeck.sequence import schedule
from astrodeck.sequence.models import (DuskFlatsPlan, ExposureStep,
                                       SequencePlan, Target)

HI = -2.0

#: The sky's brightness through the camera, in ADU per second at the window's
#: upper edge, and how fast it falls: ``per_deg`` decades per degree of the
#: Sun's fall. Real twilight is about a quarter of a decade a degree; a test
#: that needs the sky to go too dark inside a short set uses a steeper one.
SKY_MODEL = {"r0": 20000.0, "per_deg": 0.25}


@pytest.fixture
async def sky_hub(group_store, monkeypatch):
    """The night harness's hub with the filter wheel put back: the stage shoots
    a set per filter, and a wheel is what names them."""
    h, popped = await night_hub(monkeypatch)
    for dev in popped:
        if getattr(dev, "kind", "") == "filterwheel":
            h.devices["filterwheel"] = dev
    yield h
    await close_night_hub(h, popped)


def _hi_crossing() -> float:
    """The instant the Sun last fell through the window's upper edge before
    ``T0`` (the fixture site's evening)."""
    t = schedule.prev_sun_event(LAT, LON, HI, T0, rising=False)
    assert t is not None
    return t


def _lights() -> Target:
    return Target(name="M31", ra_hours=0.7, dec_deg=41.0, center=False,
                  autofocus_first=False,
                  steps=[ExposureStep(filter=f, exposure_s=1.0, gain=100,
                                      offset=30, count=2)
                         for f in ("L", "R")])


def _plan(*, lo: float | None = -8.0, hi: float | None = HI, count=6,
          filters=("L", "R")) -> SequencePlan:
    return SequencePlan(
        name="Sky flats night", guide=False, dither_every=0,
        meridian_flip=False, park_when_done=False, warm_cooler_when_done=False,
        targets=[_lights()],
        dusk_flats=DuskFlatsPlan(method="sky", window_hi_deg=hi,
                                 window_lo_deg=lo, filters=list(filters),
                                 adu_target=8000, count=count))


class Shots:
    """What the camera was asked for, and the cover's state at the moments
    that matter, all on the night's clock."""

    def __init__(self, night: Night, hub, model: dict):
        self.night = night
        self.captures: list[dict] = []
        self.covers: list[tuple[float, str]] = []
        self.marker: dict | None = None
        #: The cover's moves up to the first light: the wind-down shuts it
        #: again after, which is not the stage's.
        self.covers_before_lights: list[str] = []
        real_capture = hub.capture
        cc = hub.devices["covercalibrator"]
        real_open, real_close = cc.open_cover, cc.close_cover

        async def capture(exposure_s, gain, offset, binning=1, **kw):
            t = night.clock.t
            info = await real_capture(exposure_s, gain, offset, binning, **kw)
            alt = schedule.sun_altitude(LAT, LON, t)
            rate = model["r0"] * 10 ** (model["per_deg"] * (alt - HI))
            info["stats"] = {"median": min(65535.0, rate * exposure_s)}
            self.captures.append({
                "t": t, "exposure_s": float(exposure_s),
                "saved": bool(kw.get("save", True)),
                "type": kw.get("frame_type", "Light"),
                "cover": cc._cover.value})
            return info

        async def open_cover():
            self.covers.append((night.clock.t, "open"))
            await real_open()

        async def close_cover():
            self.covers.append((night.clock.t, "closed"))
            await real_close()

        async def scheduled(plan):
            self.marker = {"t": night.clock.t, "cover": cc._cover.value,
                           "flats": len(self.saved_flats())}
            self.covers_before_lights = [state for _t, state in self.covers]

        night.mp.setattr(hub, "capture", capture)
        night.mp.setattr(cc, "open_cover", open_cover)
        night.mp.setattr(cc, "close_cover", close_cover)
        night.mp.setattr(night.engine, "_run_scheduled", scheduled)

    def saved_flats(self) -> list[dict]:
        return [c for c in self.captures if c["type"] == "Flat" and c["saved"]]

    def metering(self) -> list[dict]:
        return [c for c in self.captures
                if c["type"] == "Flat" and not c["saved"]]


async def _night(hub, monkeypatch, plan, *, t0: float, model=None,
                 cover_open=False, before_run=None) -> tuple[Night, Shots]:
    if cover_open:
        await hub.devices["covercalibrator"].open_cover()
    night = Night(hub, monkeypatch, t0=t0)
    shots = Shots(night, hub, model or SKY_MODEL)
    if before_run is not None:
        before_run(night, shots)
    ok = await night.run(plan)
    assert ok, night.trace[-3:]
    return night, shots


def _shot_line(night: Night) -> str:
    (line,) = night.said("skipped (the library had them)")
    return line


# -------------------------------------------------------------- the headline

async def test_the_stage_waits_for_the_window_then_meters_and_shoots(
        sky_hub, monkeypatch):
    """Twenty minutes before the Sun reaches the window's upper edge the stage
    holds; it starts there. It meters before shooting each set, shoots the
    count of each filter at the metered exposure, and the exposure it solves
    grows as the Sun falls: the sky is darkening under the running set.

    RED if the stage did not run a sky block at all (``df.method`` read as not
    run yet): no Flat frame at all."""
    t_hi = _hi_crossing()
    night, s = await _night(sky_hub, monkeypatch, _plan(count=30),
                            t0=t_hi - 1200.0,
                            model={"r0": 3000.0, "per_deg": 0.5})
    flats = s.saved_flats()
    assert len(flats) == 60, [c["exposure_s"] for c in flats]
    assert flats[0]["t"] >= t_hi, "the stage started before the Sun's window"
    assert flats[0]["t"] - t_hi < 120.0, (
        "the stage waited well past the window's upper edge")
    exposures = [c["exposure_s"] for c in flats]
    assert exposures == sorted(exposures), (
        f"the exposure shrank while the sky darkened: {exposures}")
    assert exposures[-1] > exposures[0], (
        "the sky darkened and the exposure never grew: it was not re-metered")
    assert s.metering(), "nothing was metered before the first flat"
    assert s.metering()[0]["t"] < flats[0]["t"]
    # the second filter's set is metered from where the first one ended, not
    # from the stage's own seed: the sky is as bright as that set left it
    first_trial = next(m for m in s.metering() if m["t"] > flats[29]["t"])
    assert first_trial["exposure_s"] == pytest.approx(flats[29]["exposure_s"]), (
        f"the second set was metered from {first_trial['exposure_s']:g} s, "
        f"not from the last set's {flats[29]['exposure_s']:g} s")
    assert s.marker is not None and s.marker["flats"] == 60, (
        "the first light was reached before the sets were done")
    assert "2 set(s) shot" in _shot_line(night)


async def test_the_cover_is_opened_for_the_sky_and_shut_again(
        sky_hub, monkeypatch):
    """A cover found shut is opened before the first exposure through it (a
    shut cover meters a black sky), and shut again after: the stage hands the
    cover back as it found it."""
    t_hi = _hi_crossing()
    _, s = await _night(sky_hub, monkeypatch, _plan(), t0=t_hi - 600.0)
    assert [c["cover"] for c in s.captures] and all(
        c["cover"] == "open" for c in s.captures), s.captures
    assert s.covers_before_lights == ["open", "closed"], s.covers
    assert s.marker["cover"] == "closed"


async def test_a_cover_found_open_is_left_open(sky_hub, monkeypatch):
    t_hi = _hi_crossing()
    _, s = await _night(sky_hub, monkeypatch, _plan(), t0=t_hi - 600.0,
                        cover_open=True)
    assert s.covers_before_lights == [], s.covers
    assert s.marker["cover"] == "open"


# ---------------------------------------------------------- when it stops

async def test_the_stage_stops_when_the_sky_is_too_dark(sky_hub, monkeypatch):
    """The sky falls ten-fold a degree, which this fixture's Sun does in about
    five minutes, so the exposure outruns the longest one (30 s) under a set
    of 100: the set ends where the re-meter finds it, the stage says why and
    shoots no set after it.

    Mutant "sky re-meter break removed" (``_run_calibration``'s ``break`` on a
    sky flat's too_dim_at_max): the set goes on at the longest exposure and
    shoots all 100."""
    t_hi = _hi_crossing()
    night, s = await _night(
        sky_hub, monkeypatch, _plan(lo=-12.0, count=100), t0=t_hi - 60.0,
        model={"r0": 2000.0, "per_deg": 1.0})
    flats = s.saved_flats()
    assert 0 < len(flats) < 100, len(flats)
    assert max(c["exposure_s"] for c in flats) < 31.0
    assert night.said("the sky is too dark for the longest"), night.lines[-8:]
    assert "stopped early: the sky is too dark" in _shot_line(night)
    assert s.marker is not None, "the night did not go on to its lights"
    assert s.marker["cover"] == "closed"
    # no second filter's set began: every flat is the first filter's, and the
    # trial exposures stop with it
    assert s.metering()[-1]["t"] <= flats[-1]["t"] + 60.0


async def test_the_sky_that_is_dark_at_the_start_shoots_nothing(
        sky_hub, monkeypatch):
    """A node with no window ("Now") run in the dark: the opening solve finds
    the sky too dark for the longest exposure and the stage says so and goes
    on, with no flats and the cover as it was."""
    night, s = await _night(sky_hub, monkeypatch,
                            _plan(lo=None, hi=None), t0=T0)
    assert s.saved_flats() == []
    assert night.said("the sky is too dark for the longest exposure"), (
        night.lines[-6:])
    assert s.marker is not None and s.marker["cover"] == "closed"


async def test_no_set_starts_once_the_sun_is_below_the_window(
        sky_hub, monkeypatch):
    """The window closes three minutes after it opens. The first set (60
    flats, longer than that) is begun inside it and finished; the second is
    not started."""
    t_hi = _hi_crossing()
    lo = schedule.sun_altitude(LAT, LON, t_hi + 180.0)
    night, s = await _night(
        sky_hub, monkeypatch, _plan(lo=lo, count=60), t0=t_hi - 30.0,
        model={"r0": 2000.0, "per_deg": 0.25})
    flats = s.saved_flats()
    assert len(flats) == 60, len(flats)
    assert night.said("the Sun has fallen below the window's lower edge"), (
        night.lines[-6:])
    assert "stopped early: the Sun has fallen below" in _shot_line(night)


async def test_a_night_that_starts_below_the_window_shoots_none(
        sky_hub, monkeypatch):
    t_hi = _hi_crossing()
    night, s = await _night(sky_hub, monkeypatch, _plan(lo=-8.0),
                            t0=t_hi + 3 * 3600.0)
    assert s.captures == [], s.captures
    assert night.said("the Sun is already below the window's lower edge")
    assert s.marker is not None and s.covers_before_lights == []


# ------------------------------------------------------------ no site, no band

async def test_with_no_saved_site_no_sky_flats_are_shot_and_it_says_so(
        sky_hub, group_store, monkeypatch):
    """The window is the Sun's and the flat field is opposite it: without a
    site nothing can say where either is, and a sky flat shot wherever the
    mount happens to point is not a flat. Said once, no flats, the night goes
    on."""
    group_store.cfg().site = Site(name="unset", latitude=0.0, longitude=0.0,
                                  is_default=True)
    night, s = await _night(sky_hub, monkeypatch, _plan(), t0=T0)
    assert night.said("no site is saved"), night.lines[-6:]
    assert s.captures == [], s.captures
    assert s.marker is not None


# -------------------------------------------------------------- the privacy

async def test_every_line_at_a_sun_crossing_is_flagged_and_unserved_to_a_viewer(
        sky_hub, monkeypatch):
    """The wait's own lines, the window-closed line and the too-dark line are
    said at moments the Sun sets: the site. Flagged ``site_derived``, they are
    withheld from a principal without the site view. None carries a figure of
    the Sun's altitude."""
    t_hi = _hi_crossing()
    night, _ = await _night(
        sky_hub, monkeypatch, _plan(lo=-12.0, count=100), t0=t_hi - 600.0,
        model={"r0": 2000.0, "per_deg": 1.0})
    needles = ("waiting for the Sun", "the sky is too dark for the longest "
               "exposure, so the stage stops")
    rows = [e for e in night.events if e["type"] == "log"]
    served = [(r["data"] or {}).get("message", "") for r in
              _redact_log_rows_for(rows, principal_for_role("viewer"))]
    for needle in needles:
        said = night.said(needle)
        assert said, f"premise: the night said {needle!r}"
        flagged = [m for _t, _l, m in night.flagged if needle in m]
        assert flagged == said, f"said but not flagged site_derived: {needle!r}"
        assert not [m for m in served if needle in m], (
            f"a viewer is served {needle!r}")
