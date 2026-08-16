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

    Every test above calls `flip_unnecessary_over_pole` directly, so all of them
    would still pass with the gate deleted from `_maybe_meridian_flip` - a
    correct rule wired to nothing. These drive the engine's own flip method and
    watch whether `hub.meridian_flip` is reached.
    """

    def _engine_at_the_meridian(self, sim_hub, monkeypatch, dec_deg: float):
        """An engine whose target is crossing the meridian RIGHT NOW, so the
        only thing left to decide the flip is the geometry."""
        lon = sim_hub.site["longitude"]
        ra_now = schedule.lst_hours(lon)          # hour angle 0 => flip is due
        t = Target(name="T", ra_hours=ra_now, dec_deg=dec_deg, center=False,
                   autofocus_first=False,
                   steps=[ExposureStep(filter="L", exposure_s=0.05, count=1)])
        e = SequenceEngine(sim_hub)
        e.plan = SequencePlan(targets=[t], meridian_flip=True)
        e._flip_armed = True

        flips: list[tuple] = []

        async def _flip(ra, dec):
            flips.append((ra, dec))

        monkeypatch.setattr(sim_hub, "meridian_flip", _flip)
        return e, t, flips

    async def test_a_high_dec_target_is_not_flipped(self, sim_hub, monkeypatch):
        """dec +80 from any real latitude: the tube never points down, so the
        flip buys nothing and must not happen."""
        monkeypatch.setattr(hub_module.config_store.cfg().site, "latitude", 37.35)
        e, t, flips = self._engine_at_the_meridian(sim_hub, monkeypatch, 80.0)
        await e._maybe_meridian_flip(t, next_exposure_s=0.05)
        assert not flips, (
            "the mount flipped for a target whose tube never swings below the "
            "horizon - the exact unnecessary flip this change removes")

    async def test_a_low_dec_target_is_still_flipped(self, sim_hub, monkeypatch):
        """THE POSITIVE CONTROL, and the one that matters. dec +20 from lat 37
        sweeps 33 degrees below the horizon; this is the collision the flip
        exists for. Without this assertion the change could disable flipping
        outright and every other test here would still pass."""
        monkeypatch.setattr(hub_module.config_store.cfg().site, "latitude", 37.35)
        e, t, flips = self._engine_at_the_meridian(sim_hub, monkeypatch, 20.0)
        await e._maybe_meridian_flip(t, next_exposure_s=0.05)
        assert flips, (
            "a target that sets was NOT flipped at the meridian - the mount "
            "would track counterweight-up into the pier")

    async def test_the_rig_case_from_2026_08_16(self, sim_hub, monkeypatch):
        """NGC 7129, dec +66.11, the flip actually watched on the rig."""
        monkeypatch.setattr(hub_module.config_store.cfg().site, "latitude", 37.35)
        e, t, flips = self._engine_at_the_meridian(sim_hub, monkeypatch, 66.11)
        await e._maybe_meridian_flip(t, next_exposure_s=60.0)
        assert not flips

    async def test_an_unreadable_latitude_still_flips(self, sim_hub, monkeypatch):
        """Honest absence fails safe at the call site too, not just in the
        helper: an unknown site means unknown geometry means take the flip."""
        e, t, flips = self._engine_at_the_meridian(sim_hub, monkeypatch, 80.0)
        monkeypatch.setattr(hub_module.config_store.cfg().site, "latitude", float("nan"))
        await e._maybe_meridian_flip(t, next_exposure_s=0.05)
        assert flips, "an unreadable latitude skipped the flip instead of taking it"

    async def test_it_says_why_it_declined(self, sim_hub, monkeypatch):
        """A declined flip that leaves no trace is indistinguishable from a flip
        that failed. This is the one direction of the decision that can put a
        tube into a pier, so it has to be auditable."""
        from astrodeck.events import bus
        monkeypatch.setattr(hub_module.config_store.cfg().site, "latitude", 37.35)
        e, t, flips = self._engine_at_the_meridian(sim_hub, monkeypatch, 80.0)
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
        not repeat this line on every frame."""
        from astrodeck.events import bus
        monkeypatch.setattr(hub_module.config_store.cfg().site, "latitude", 37.35)
        e, t, flips = self._engine_at_the_meridian(sim_hub, monkeypatch, 80.0)
        await e._maybe_meridian_flip(t, next_exposure_s=0.05)
        q = bus.subscribe()
        try:
            await e._maybe_meridian_flip(t, next_exposure_s=0.05)
            second = [ev for ev in iter(lambda: q.get_nowait() if not q.empty()
                                        else None, None)
                      if ev.type == "log"
                      and "no meridian flip" in str((ev.data or {}).get("message", ""))]
        finally:
            bus.unsubscribe(q)
        assert not second, "the declined-flip line repeats on every frame"
