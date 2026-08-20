"""A flip that protects against a collision which cannot happen is pure cost.

`_maybe_meridian_flip` decided entirely from
``hours_to_meridian_flip(target.ra_hours, lon)`` — RA and longitude, no
declination anywhere in the path. The hub's countdown (`_meridian`) does the
same, `lx200.py` implements neither `time_to_meridian_flip` nor `pier_side`, and
the engine folds the device value in only when it reports a SOONER positive
countdown. So on this rig nothing could veto a flip, and every target got one at
hour angle zero regardless of where it actually crosses the sky.

WATCHED IT HAPPEN 2026-08-16 01:12, NGC 7129 at dec +66.1 from latitude ~37.3:
stop guiding, re-slew, re-solve, re-centre, restart guiding, and a 180-degree
field rotation in the middle of the stack — for a target that transits ABOVE the
pole, where the tube swings over the mount head with nothing in its way. It also
cost that flip's post-slew autofocus (see the narrowband defect filed beside
this), so the real bill was closer to four minutes.

THE CRITERION, AND WHY IT IS NOT "dec > latitude".

The tube sits ``90 - dec`` degrees off the polar axis, so as the mount turns it
sweeps a cone of that half-angle about an axis pointing at the pole, elevation
``lat``. The LOWEST altitude the tube can ever reach on that sweep is therefore

    lat - (90 - dec)  ==  lat + dec - 90

which is exactly the target's LOWER CULMINATION. If that stays comfortably above
the horizon the tube never points down at all, anywhere in the rotation — so it
cannot be pointing at the pier, the tripod or the ground, and the meridian
crossing at the top of the circle is just the top of a circle the mount can
follow the whole way round.

"dec > latitude" only says the target crosses north of the zenith, which is a
weaker and sometimes wrong thing. At latitude 0 a dec +15 target culminates
north of zenith and would pass that test, while its tube sweeps a 75-degree cone
reaching 75 degrees BELOW the horizon — straight into the ground. The lower
culmination form gives the right answer there (needs dec >= 100, impossible) and
degrades sensibly at every latitude.

FAIL-SAFE DIRECTION. Today's behaviour flips more often than it needs to, which
is wasteful and always safe; this makes it flip less, which is the direction that
can hurt. So the margin is real clearance, not a rounding allowance, and every
declined flip is announced in the log rather than silently skipped.
"""
from __future__ import annotations

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence import schedule
from astrodeck.sequence.models import ExposureStep, Target
from astrodeck.sequence.schedule import (MERIDIAN_POLE_CLEARANCE_DEG,
                                         flip_unnecessary_over_pole,
                                         lower_culmination_deg)


class TestLowerCulmination:
    """The whole rule rests on this one number being the tube's floor."""

    @pytest.mark.parametrize("lat,dec,expected", [
        (37.35, 66.11, 13.46),    # NGC 7129 from this rig: circumpolar, clears
        (37.0, 90.0, 37.0),       # the pole itself sits at the latitude
        (37.0, 20.0, -33.0),      # sets: tube would sweep 33 deg below horizon
        (0.0, 15.0, -75.0),       # the equator case "dec > lat" gets wrong
        (-33.9, -60.0, 3.9),      # southern hemisphere, marginal
    ])
    def test_it_is_lat_plus_dec_minus_90(self, lat, dec, expected):
        assert lower_culmination_deg(dec, lat) == pytest.approx(expected, abs=0.01)


class TestWhenTheFlipIsUnnecessary:
    def test_the_case_this_was_written_for(self):
        """NGC 7129 from the rig: lower culmination 13.5 deg, clears the 10 deg
        margin, so the tube never dips and the flip buys nothing."""
        assert flip_unnecessary_over_pole(66.11, 37.35) is True

    def test_a_target_that_sets_always_flips(self):
        """dec 20 from lat 37 sweeps 33 deg below the horizon. This is the
        collision the flip exists for and it must never be skipped."""
        assert flip_unnecessary_over_pole(20.0, 37.35) is False

    def test_the_equator_never_skips(self):
        """The case that kills the naive `dec > lat` rule: culminating north of
        the zenith is not the same as clearing the ground."""
        assert flip_unnecessary_over_pole(15.0, 0.0) is False
        assert flip_unnecessary_over_pole(80.0, 0.0) is False

    def test_barely_circumpolar_is_not_enough(self):
        """Lower culmination of +2 deg means the tube grazes the horizon, which
        is where a pier and a tripod leg live. Circumpolar is not the test;
        clearance is."""
        assert flip_unnecessary_over_pole(54.0, 38.0) is False   # lower = 2.0

    def test_the_margin_is_enforced_not_decorative(self):
        """Straddle the margin by half a degree either side. The knife edge
        itself is not worth asserting - `asin(-cos(100 deg))` lands at
        9.999999999999998, so an exact-boundary test would be measuring float
        arithmetic rather than the rule."""
        lat = 37.0
        at_margin = 90.0 - lat + MERIDIAN_POLE_CLEARANCE_DEG      # lower == margin
        assert flip_unnecessary_over_pole(at_margin + 0.5, lat) is True
        assert flip_unnecessary_over_pole(at_margin - 0.5, lat) is False

    def test_a_bigger_margin_refuses_more(self):
        """The margin is a real dial, not a constant baked into the comparison:
        NGC 7129 clears 10 degrees and does not clear 15."""
        assert flip_unnecessary_over_pole(66.11, 37.35, clearance_deg=10.0) is True
        assert flip_unnecessary_over_pole(66.11, 37.35, clearance_deg=15.0) is False

    def test_the_pole_star_never_needs_a_flip(self):
        assert flip_unnecessary_over_pole(89.3, 37.35) is True


class TestHemispheresAndJunk:
    def test_the_southern_hemisphere_mirrors(self):
        """A southern observer's circumpolar targets are at NEGATIVE dec, and
        the same clearance applies."""
        assert flip_unnecessary_over_pole(-75.0, -33.9) is True     # lower 8.9? check
        assert flip_unnecessary_over_pole(-40.0, -33.9) is False

    def test_a_northern_target_from_the_south_never_skips(self):
        """Opposite hemispheres: the target rises and sets, so the tube sweeps
        low and the flip is owed."""
        assert flip_unnecessary_over_pole(66.11, -33.9) is False

    @pytest.mark.parametrize("dec,lat", [(None, 37.0), (66.0, None),
                                         (float("nan"), 37.0),
                                         (66.0, float("nan"))])
    def test_an_unreadable_input_flips(self, dec, lat):
        """HONEST ABSENCE FAILS SAFE. If either number cannot be read we do not
        know the geometry, and not knowing must mean taking the flip - the same
        tri-state discipline as the altitude floor and the cloud probe."""
        assert flip_unnecessary_over_pole(dec, lat) is False


# --------------------------------------------------------------- the call site

@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


class TestTheEngineActuallyConsultsIt:
    """THE CALL SITE, not the helper.

    Every geometry test above calls `flip_unnecessary_over_pole` directly, so all
    of them would still pass with the gate deleted from `_maybe_meridian_flip` -
    a correct rule wired to nothing. These drive the engine flip method itself
    and watch whether `hub.meridian_flip` is reached.

    REVISED 2026-08-20. The geometry above is still right and still tested; what
    changed is that clearing the ground is only HALF the question. A German
    equatorial stops at its own meridian limit whatever the tube is doing, so the
    engine now asks the mount what it is before letting the geometry excuse a
    flip. See `schedule.flip_can_be_skipped` for the two nights that proved it.
    """

    def _engine_at_the_meridian(self, sim_hub, monkeypatch, dec_deg: float,
                                pier_side: str = "unknown"):
        """An engine whose target is crossing the meridian RIGHT NOW.

        `pier_side` is what the mount will report: east/west for a German
        equatorial, unknown for a fork - the difference the engine now turns on.
        The sim reports a REAL side derived from its pointing, so it is pinned
        here rather than left to the fixture geometry.
        """
        from astrodeck.devices.base import PierSide
        lon = sim_hub.site["longitude"]
        ra_now = schedule.lst_hours(lon)          # hour angle 0 => flip is due
        t = Target(name="T", ra_hours=ra_now, dec_deg=dec_deg, center=False,
                   autofocus_first=False,
                   steps=[ExposureStep(filter="L", exposure_s=0.05, count=1)])
        e = SequenceEngine(sim_hub)
        e.plan = SequencePlan(targets=[t], meridian_flip=True)
        e._flip_armed = True

        async def _pier():
            return {"east": PierSide.EAST, "west": PierSide.WEST}.get(
                pier_side, PierSide.UNKNOWN)

        monkeypatch.setattr(sim_hub.devices["telescope"], "pier_side", _pier)

        flips: list[tuple] = []

        async def _flip(ra, dec):
            flips.append((ra, dec))

        monkeypatch.setattr(sim_hub, "meridian_flip", _flip)
        return e, t, flips

    # ----------------------------------------------- the regression, twice over

    async def test_a_GEM_flips_at_high_dec_because_the_MOUNT_stops(
            self, sim_hub, monkeypatch):
        """THE TEST THIS WHOLE CHANGE EXISTS FOR.

        NGC 7129, dec +66.11 from lat 37.35: lower culmination 13.5 deg, so the
        tube comfortably clears the ground and the old rule declined the flip.
        The ZWO AM5 then stopped tracking anyway - four minutes past the meridian
        on 2026-08-19, five on 2026-08-20 - because a GEM limit is an hour angle,
        not an opinion about the tube.

        The mount here reports pier side west and reports NOTHING for
        `time_to_meridian_flip`, which is exactly the AM5. An earlier attempt at
        this fix keyed off that countdown and was inert on the real rig for
        precisely that reason; this test would have caught it.
        """
        monkeypatch.setattr(hub_module.config_store.cfg().site, "latitude", 37.35)
        e, t, flips = self._engine_at_the_meridian(
            sim_hub, monkeypatch, 66.11, pier_side="west")

        async def _no_countdown():
            return None                       # the AM5: no device value at all

        monkeypatch.setattr(sim_hub.devices["telescope"],
                            "time_to_meridian_flip", _no_countdown)
        await e._maybe_meridian_flip(t, next_exposure_s=60.0)
        assert flips, (
            "a German equatorial crossed the meridian without flipping - this "
            "is the decision that ended the nights of 2026-08-19 and 08-20")

    async def test_a_GEM_flips_at_dec_80_too(self, sim_hub, monkeypatch):
        """Not a special case for one declination: the limit is an hour angle,
        so it binds at every declination a GEM can reach."""
        monkeypatch.setattr(hub_module.config_store.cfg().site, "latitude", 37.35)
        e, t, flips = self._engine_at_the_meridian(
            sim_hub, monkeypatch, 80.0, pier_side="east")
        await e._maybe_meridian_flip(t, next_exposure_s=0.05)
        assert flips, "a GEM at dec +80 skipped the flip and will stop tracking"

    # --------------------------------------------- the optimisation, preserved

    async def test_a_FORK_is_still_not_flipped_at_high_dec(
            self, sim_hub, monkeypatch):
        """The over-pole optimisation survives where it is actually true. A fork
        has no pier to be on a side of and no meridian limit, so it tracks
        straight through and a flip would buy nothing."""
        monkeypatch.setattr(hub_module.config_store.cfg().site, "latitude", 37.35)
        e, t, flips = self._engine_at_the_meridian(
            sim_hub, monkeypatch, 80.0, pier_side="unknown")
        await e._maybe_meridian_flip(t, next_exposure_s=0.05)
        assert not flips, (
            "flipped a mount that reports no pier side - there is nothing to "
            "flip and the tube never swings low")

    async def test_a_low_dec_target_is_still_flipped(self, sim_hub, monkeypatch):
        """THE POSITIVE CONTROL. dec +20 from lat 37 sweeps 33 degrees below the
        horizon; this is the collision the flip exists for. Without this the
        change could disable flipping outright and other tests would still
        pass."""
        monkeypatch.setattr(hub_module.config_store.cfg().site, "latitude", 37.35)
        e, t, flips = self._engine_at_the_meridian(
            sim_hub, monkeypatch, 20.0, pier_side="west")
        await e._maybe_meridian_flip(t, next_exposure_s=0.05)
        assert flips, (
            "a target that sets was NOT flipped at the meridian - the mount "
            "would track counterweight-up into the pier")

    async def test_an_unreadable_latitude_still_flips(self, sim_hub, monkeypatch):
        """Honest absence fails safe at the call site too: an unknown site means
        unknown geometry means take the flip - even on a fork, where the flip is
        merely wasted rather than wrong."""
        e, t, flips = self._engine_at_the_meridian(
            sim_hub, monkeypatch, 80.0, pier_side="unknown")
        monkeypatch.setattr(hub_module.config_store.cfg().site, "latitude", float("nan"))
        await e._maybe_meridian_flip(t, next_exposure_s=0.05)
        assert flips, "an unreadable latitude skipped the flip instead of taking it"

    async def test_an_unreadable_pier_side_falls_back_to_the_geometry(
            self, sim_hub, monkeypatch):
        """A mount that raises when asked must not change the answer. Unknown is
        ambiguous - a fork and a quiet GEM look identical - so it keeps the
        behaviour that shipped rather than guessing in either direction."""
        monkeypatch.setattr(hub_module.config_store.cfg().site, "latitude", 37.35)
        e, t, flips = self._engine_at_the_meridian(
            sim_hub, monkeypatch, 80.0, pier_side="unknown")

        async def _boom():
            raise RuntimeError("serial timeout")

        monkeypatch.setattr(sim_hub.devices["telescope"], "pier_side", _boom)
        await e._maybe_meridian_flip(t, next_exposure_s=0.05)
        assert not flips, "a pier-side read error changed the flip decision"

    # ---------------------------------------------------------- the audit trail

    async def test_it_says_why_it_declined(self, sim_hub, monkeypatch):
        """A declined flip that leaves no trace is indistinguishable from a flip
        that failed. This is the one direction of the decision that can put a
        tube into a pier, so it has to be auditable."""
        from astrodeck.events import bus
        monkeypatch.setattr(hub_module.config_store.cfg().site, "latitude", 37.35)
        e, t, flips = self._engine_at_the_meridian(
            sim_hub, monkeypatch, 80.0, pier_side="unknown")
        q = bus.subscribe()
        try:
            await e._maybe_meridian_flip(t, next_exposure_s=0.05)
            msgs = []
            while not q.empty():
                ev = q.get_nowait()
                if ev.type == "log":
                    msgs.append(str((ev.data or {}).get("message", "")))
        finally:
            bus.unsubscribe(q)
        assert any("no meridian flip needed" in m for m in msgs), msgs
        assert any("above the horizon" in m for m in msgs), msgs

    async def test_it_is_said_once_not_every_frame(self, sim_hub, monkeypatch):
        """`_flip_armed` is cleared with the decision, so a 25-cycle night does
        not repeat this line on every frame.

        PINNED ON A FORK. This used to run at dec +80 on the sim own pier side,
        which is now read as a GEM and therefore flips - so the declined-flip
        line never appeared and the test passed by asserting the absence of
        something that could no longer happen. It has to decline first for
        "said once" to mean anything.
        """
        from astrodeck.events import bus
        monkeypatch.setattr(hub_module.config_store.cfg().site, "latitude", 37.35)
        e, t, flips = self._engine_at_the_meridian(
            sim_hub, monkeypatch, 80.0, pier_side="unknown")

        def _drain(q):
            out = []
            while not q.empty():
                ev = q.get_nowait()
                if ev.type == "log":
                    m = str((ev.data or {}).get("message", ""))
                    if "no meridian flip" in m:
                        out.append(m)
            return out

        q = bus.subscribe()
        try:
            await e._maybe_meridian_flip(t, next_exposure_s=0.05)
            first = _drain(q)
            await e._maybe_meridian_flip(t, next_exposure_s=0.05)
            second = _drain(q)
        finally:
            bus.unsubscribe(q)
        assert first, "premise: the first pass must actually decline"
        assert not second, "the declined-flip line repeats on every frame"
