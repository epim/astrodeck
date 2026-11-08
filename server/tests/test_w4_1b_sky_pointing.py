# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Where the twilight-sky flats point, and under what roof (#603 job C, the
pointing and the roof; wave 4, WP 4.1b, fix round 1).

THE CLASS: a claim nothing keeps. A sky flat shot wherever the mount happens
to point (on the real rig, the park view is obstructed) is not a flat. The sky
stage now slews to the usual twilight flat spot, high in the sky opposite the
Sun, before it meters; it never shoots under a roof that does not read open.

What is held, on the clocked simulator of ``test_w4_1b_sky_flats.py``:

* the slew happens BEFORE the first metering exposure, without centring (no
  ``goto_and_center``, so no solve first and no home solve), from park as from
  tracking;
* the point is opposite the Sun in azimuth and high in altitude;
* tracking is off for every flat, and the tracking the stage found is back
  after the sets (and a mount found untracked is left untracked);
* the sun-avoidance gate can refuse the point, and a refusal skips the sky
  flats, saying so, with no slew and no exposure;
* the mount's own limits can leave no sky to point at, and that skips the
  stage the same way;
* a roof that is closed, in error or unreadable refuses the stage, with no
  slew and no exposure, and nothing opens it;
* nothing the stage says carries the point or the Sun's altitude.

These run the STAGE ALONE: the run start's ``_open_the_observatory`` (WP
4.4, #601) is stubbed, so the stage meets the roof and the cover as each test
left them. The whole run, with it in, is the last two tests.

Named mutant: the pointing slew skipped (``_dusk_flats``'s ``if point is not
None:`` made ``if False:``): ``test_the_mount_slews_to_the_flat_field_before_
metering_without_centring`` finds no slew.
"""
from __future__ import annotations

import pytest

from _group_harness import LAT, LON, group_store  # noqa: F401
from astrodeck.catalog import altaz
from astrodeck.catalog.coords import sun_altaz
from astrodeck.devices.base import DeviceError, DomeShutterState
from test_w4_1b_sky_flats import (Shots, _hi_crossing, _night,  # noqa: F401
                                  _plan, sky_hub)


class Mount:
    """The mount's slews and tracking commands, the exposures with the
    tracking they were taken under, and the tracking at the first light, all
    in order."""

    def __init__(self, night, shots, hub):
        self.events: list[tuple] = []
        self.tracking_at_lights: bool | None = None
        tel = hub.devices["telescope"]
        real_slew, real_set = tel.slew, tel.set_tracking
        real_capture = hub.capture
        real_scheduled = night.engine._run_scheduled

        async def slew(ra_hours, dec_deg):
            self.events.append(("slew", ra_hours, dec_deg, night.clock.t))
            await real_slew(ra_hours, dec_deg)

        async def set_tracking(on):
            self.events.append(("tracking", bool(on)))
            await real_set(on)

        async def capture(*a, **kw):
            self.events.append(("capture", kw.get("frame_type", "Light"),
                                bool(kw.get("save", True)),
                                await tel.get_tracking()))
            return await real_capture(*a, **kw)

        async def scheduled(plan):
            self.tracking_at_lights = await tel.get_tracking()
            await real_scheduled(plan)

        night.mp.setattr(tel, "slew", slew)
        night.mp.setattr(tel, "set_tracking", set_tracking)
        night.mp.setattr(hub, "capture", capture)
        night.mp.setattr(night.engine, "_run_scheduled", scheduled)

    def slews(self) -> list[tuple]:
        return [e for e in self.events if e[0] == "slew"]

    def flats(self) -> list[tuple]:
        return [e for e in self.events if e[:2] == ("capture", "Flat")]


async def _run(hub, monkeypatch, *, tracking=True, parked=False,
               before=None, plan=None, cover_open=False, stage_alone=True):
    tel = hub.devices["telescope"]
    if parked:
        await tel.park()
    else:
        await tel.unpark()
        await tel.set_tracking(tracking)
    box: dict = {}

    def setup(night, shots):
        box["mount"] = Mount(night, shots, hub)
        if before is not None:
            before(night)

    t_hi = _hi_crossing()
    night, shots = await _night(hub, monkeypatch, plan or _plan(),
                                t0=t_hi - 120.0, before_run=setup,
                                cover_open=cover_open,
                                stage_alone=stage_alone)
    return night, shots, box["mount"]


def _apart(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


# -------------------------------------------------------------- the headline

@pytest.mark.asyncio
async def test_the_mount_slews_to_the_flat_field_before_metering_without_centring(
        sky_hub, monkeypatch):
    """One slew, ahead of the first metering exposure, to the spot opposite
    the Sun in azimuth and high in altitude, with no centring (no solve, so no
    home solve first). RED under the named mutant: no slew at all."""
    night, _s, m = await _run(sky_hub, monkeypatch)
    (_, ra, dec, t) = m.slews()[0] if len(m.slews()) == 1 else (None,) * 4
    assert ra is not None, f"expected one slew: {m.events[:6]}"
    first_capture = next(i for i, e in enumerate(m.events)
                         if e[0] == "capture")
    assert m.events.index(m.slews()[0]) < first_capture, (
        "the sky was metered before the mount pointed at it")
    assert night.goto_calls == [], "the flat field was centred on"
    alt, az = altaz(ra, dec, LAT, LON, t)
    assert alt == pytest.approx(75.0, abs=0.5)
    assert _apart(az, sun_altaz(LAT, LON, t)[1] + 180.0) < 1.0


@pytest.mark.asyncio
async def test_a_mount_parked_at_home_is_slewed_with_no_solve_first(
        sky_hub, monkeypatch):
    """The park view is obstructed on the real rig: the slew goes from park,
    unparking itself, and nothing asks for a home solve first."""
    night, s, m = await _run(sky_hub, monkeypatch, parked=True)
    assert len(m.slews()) == 1 and m.flats(), m.events[:6]
    assert night.goto_calls == []
    assert s.marker is not None


# ----------------------------------------------------------------- tracking

@pytest.mark.asyncio
async def test_tracking_is_off_for_the_set_and_back_after_it(
        sky_hub, monkeypatch):
    """Tracking is turned off right after the slew, every flat is taken under
    it (the stars move between frames), and the tracking the stage found is
    on again when the first light comes."""
    _n, _s, m = await _run(sky_hub, monkeypatch, tracking=True)
    assert m.flats() and all(e[3] is False for e in m.flats()), m.flats()
    commands = [e[1] for e in m.events if e[0] == "tracking"]
    assert commands[-2:] == [False, True], commands
    assert m.tracking_at_lights is True


@pytest.mark.asyncio
async def test_a_mount_found_untracked_is_left_untracked(sky_hub, monkeypatch):
    _n, _s, m = await _run(sky_hub, monkeypatch, tracking=False)
    assert m.flats()
    assert [e for e in m.events if e == ("tracking", True)] == []
    assert m.tracking_at_lights is False


# -------------------------------------------------------------- the refusals

def _nothing_was_done(s, m, night, why: str) -> None:
    assert m.slews() == [] and s.captures == [], (m.events[:6], s.captures[:3])
    assert night.said(why), night.lines[-6:]
    assert s.marker is not None, "the night did not go on to its lights"
    assert s.covers_before_lights == [], "the cover was moved for no flats"


@pytest.mark.asyncio
async def test_the_sun_avoidance_gate_can_refuse_the_point(sky_hub, monkeypatch):
    """``hub._check_solar`` raising skips the sky flats, said in words."""
    def refuse(_ra, _dec, *, force=False):
        raise DeviceError("test: inside the exclusion cone")

    def before(night):
        night.mp.setattr(sky_hub, "_check_solar", refuse)

    night, s, m = await _run(sky_hub, monkeypatch, before=before)
    _nothing_was_done(s, m, night, "the sun-avoidance gate refuses")


@pytest.mark.asyncio
@pytest.mark.parametrize("why", ["closed", "error", "unreadable"])
async def test_a_roof_that_does_not_read_open_refuses_the_stage(
        sky_hub, monkeypatch, why):
    """Never a sky flat under a closed roof, and nothing opens it: no slew, no
    exposure, the roof as it was."""
    dome = sky_hub.devices["dome"]

    def before(night):
        if why == "unreadable":
            async def broken():
                raise DeviceError("test: no answer")
            night.mp.setattr(dome, "shutter_state", broken)
        else:
            dome._state = {"closed": DomeShutterState.CLOSED,
                           "error": DomeShutterState.ERROR}[why]

    opened: list[int] = []
    real_open = dome.open_shutter

    async def open_shutter():
        opened.append(1)
        await real_open()

    monkeypatch.setattr(dome, "open_shutter", open_shutter)
    night, s, m = await _run(sky_hub, monkeypatch, before=before)
    _nothing_was_done(s, m, night, "the roof")
    assert opened == [], "the stage opened the roof"


@pytest.mark.asyncio
async def test_an_open_roof_does_not_refuse_it(sky_hub, monkeypatch):
    """The control for the three above: the same night under an open roof
    shoots."""
    _n, s, m = await _run(sky_hub, monkeypatch)
    assert s.saved_flats() and len(m.slews()) == 1


@pytest.mark.asyncio
async def test_the_mounts_floor_and_ceiling_can_leave_no_sky(
        sky_hub, group_store, monkeypatch):
    """A floor raised to 80 degrees under a zenith keep-out at 82 leaves no
    altitude that clears both by the margin."""
    safety = group_store.cfg().safety
    monkeypatch.setattr(safety, "min_alt_deg", 80.0)
    monkeypatch.setattr(safety, "max_alt_deg", 82.0)
    night, s, m = await _run(sky_hub, monkeypatch)
    _nothing_was_done(s, m, night, "leave no sky to point at")


@pytest.mark.asyncio
async def test_a_mount_that_does_not_know_where_it_is_is_not_slewed(
        sky_hub, monkeypatch):
    def before(night):
        night.mp.setattr(night.engine, "_position_unknown", lambda: True)

    night, s, m = await _run(sky_hub, monkeypatch, before=before)
    _nothing_was_done(s, m, night, "position is unknown")


# ---------------------------------------------------------------- the privacy

@pytest.mark.asyncio
async def test_nothing_the_stage_says_carries_the_point_or_the_sun(
        sky_hub, monkeypatch):
    """The point opposite the Sun at a known time is the site. No line of the
    stage carries the slew's coordinates or any altitude or azimuth."""
    night, _s, m = await _run(sky_hub, monkeypatch)
    _, ra, dec, _t = m.slews()[0]
    lines = [msg for _t2, _lv, msg in night.lines if "DUSK FLATS" in msg]
    assert lines
    for msg in lines:
        low = msg.lower()
        assert "azimuth" not in low and "altitude" not in low, msg
        assert f"{ra:.2f}" not in msg and f"{dec:.2f}" not in msg, msg


# ------------------------------------------------- the whole run (WP 4.4, #601)

@pytest.mark.asyncio
async def test_the_run_opens_a_shut_roof_and_cover_and_the_sky_stage_uses_them(
        sky_hub, monkeypatch):
    """The merged tree, nothing stubbed: a rig whose roof is closed and whose
    cover is shut. The run start opens both (``_open_the_observatory``), the
    sky stage finds them open, and so moves neither: it slews, meters and
    shoots through an open cover under an open roof and leaves the cover open
    for the lights."""
    dome = sky_hub.devices["dome"]
    seen: dict = {}

    def before(night):
        dome._state = DomeShutterState.CLOSED
        real = night.engine._run_scheduled

        async def scheduled(plan):
            seen["roof"] = dome._state
            await real(plan)

        night.mp.setattr(night.engine, "_run_scheduled", scheduled)

    night, s, m = await _run(sky_hub, monkeypatch, before=before,
                             stage_alone=False)
    assert s.covers_before_lights == ["open"], (
        f"the cover was moved by more than the run start: {s.covers}")
    assert seen["roof"] is DomeShutterState.OPEN
    assert s.marker is not None and s.marker["cover"] == "open", s.marker
    assert len(m.slews()) == 1 and s.saved_flats(), m.events[:6]
    assert all(c["cover"] == "open" for c in s.captures), s.captures[:3]


@pytest.mark.asyncio
async def test_a_roof_that_will_not_open_ends_the_run_before_the_stage(
        sky_hub, monkeypatch):
    """The run start refuses the night when the roof will not open (an error
    ending naming the device), so the sky stage is never reached: no slew, no
    exposure, no first light."""
    dome = sky_hub.devices["dome"]

    async def jammed():
        raise DeviceError("test: the roof is jammed")

    def before(night):
        dome._state = DomeShutterState.CLOSED
        night.mp.setattr(dome, "open_shutter", jammed)

    night, s, m = await _run(sky_hub, monkeypatch, before=before,
                             stage_alone=False)
    assert m.slews() == [] and s.captures == [], (m.events[:6], s.captures[:3])
    assert s.marker is None, "the night went on to its lights"
    assert night.engine.state.get("state") == "error", night.engine.state
    assert night.said("DUSK FLATS") == []
