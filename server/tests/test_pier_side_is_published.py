"""The pier side, and the switch that pretended to guard it.

TWO DEFECTS, one in each direction.

1. ``safety.enforce_pier_limits`` was switched on by the operator on
   2026-09-11 and guarded NOTHING. ``engine._enforce_mount_floor`` gates the
   whole pier-collision check on ``tel.reports_destination_pier_side``, and of
   the drivers this rig can actually load, only the simulator and the Alpaca
   bridge ever set it. On the AM5N -- the only mount this rig owns -- the
   toggle was in the Settings panel, answered True over the API, and did
   nothing at all. A safety control that reports itself armed while inert is
   worse than an absent one, because it is the one people stop checking.

2. ``status.meridian.pier_side`` came from one ``:Gm#`` per poll wrapped in a
   bare except, so a single timed-out serial read turned a fact nobody disputes
   into "unknown" -- and took ``status`` and ``flip_enabled`` with it, because
   ``_is_gem`` reads the same variable. A tube does not change sides because a
   read timed out.

WHAT THE PREDICTION MUST NOT DO. The AM5 has no ``DestinationSideOfPier``
command, so the destination side is predicted from hour-angle geometry. A
prediction from a convention is worth exactly as much as the convention, and an
INVERTED one would hand the pre-slew guard a confident wrong answer -- the
failure mode that costs a tube. So the driver checks the rule against ``:Gm#``
before trusting it and answers UNKNOWN when they disagree.
"""
from __future__ import annotations

import time

import pytest

from astrodeck.catalog import coords
from astrodeck.devices.base import PierSide

# NOT applied at module level: half this file is pure synchronous geometry,
# and a blanket asyncio mark makes pytest warn on every one of those.
_async = pytest.mark.asyncio


# ------------------------------------------------------------- the geometry


class TestTheOneCopyOfTheRule:
    def test_east_of_the_meridian_means_the_tube_is_west(self):
        """ASCOM convention, measured on this rig's AM5N on both sides."""
        assert coords.pier_side_for_hour_angle(-2.0) == "west"
        assert coords.pier_side_for_hour_angle(+2.0) == "east"

    def test_the_crossing_itself_is_not_yet_flipped(self):
        """HA exactly 0 must fall on the side that does NOT assert a flip has
        already happened, or the invariant would read a mount mid-crossing as
        already done."""
        assert coords.pier_side_for_hour_angle(0.0) == "west"

    def test_the_simulator_reads_the_same_copy(self):
        """Two oracles that disagree about this is how the simulator ended up
        contradicting itself at 8 of 24 RA hours, at every sidereal time --
        which is what every meridian-flip decision taken in the simulator was
        then graded against."""
        import inspect

        from astrodeck.devices import sim
        src = inspect.getsource(sim.SimTelescope._side_for_ra)
        assert "pier_side_for_hour_angle" in src, (
            "the simulator has grown a private copy of the pier-side rule "
            "again")

    def test_the_schedule_hour_angle_is_the_same_number(self):
        """`schedule.hour_angle_h` delegates rather than duplicating: two
        hour-angle functions is how there came to be two pier-side oracles."""
        from astrodeck.sequence import schedule
        for ra in (0.0, 5.5, 12.0, 18.25, 23.9):
            assert schedule.hour_angle_h(ra, -110.0, 1780272000.0) == \
                coords.hour_angle_h(ra, -110.0, 1780272000.0)


# ------------------------------------------- the AM5 prediction, self-checked


class _Link:
    pass


def _am5(monkeypatch, *, measured: PierSide, ra_now: float, lon: float = 0.0):
    from astrodeck.config import AppConfig, config_store
    from astrodeck.devices.backends.zwo_am5 import ZwoAm5Telescope

    tel = ZwoAm5Telescope.__new__(ZwoAm5Telescope)
    tel.name = "AM5 under test"

    async def _pier():
        return measured

    async def _pos():
        return (ra_now, 20.0)

    tel.pier_side = _pier
    tel.get_position = _pos
    cfg = AppConfig()
    cfg.site.longitude = lon
    monkeypatch.setattr(config_store, "cfg", lambda: cfg)
    return tel


def _ra_at_hour_angle(ha: float, lon: float, now: float) -> float:
    """The RA whose hour angle is ``ha`` right now. Derived, never hand-written:
    an RA literal makes every assertion below depend on what time the suite
    happens to run at."""
    return (coords.lst_hours(lon, now) - ha) % 24.0


class TestTheAm5PredictsItsDestination:
    pytestmark = _async

    async def test_the_toggle_is_no_longer_inert(self):
        """THE DEFECT ITSELF. Without this flag the engine's pier guard never
        even asks, so `enforce_pier_limits` guards nothing on this rig."""
        from astrodeck.devices.backends.zwo_am5 import ZwoAm5Telescope
        assert ZwoAm5Telescope.reports_destination_pier_side is True

    async def test_it_predicts_the_far_side_of_the_meridian(self, monkeypatch):
        now = time.time()
        lon = -110.0
        ra_now = _ra_at_hour_angle(-2.0, lon, now)       # still east: tube west
        tel = _am5(monkeypatch, measured=PierSide.WEST, ra_now=ra_now, lon=lon)
        ra_dest = _ra_at_hour_angle(+2.0, lon, now)      # past: tube east
        assert await tel.destination_pier_side(ra_dest, 20.0) is PierSide.EAST

    async def test_it_predicts_the_same_side_for_a_near_target(self,
                                                               monkeypatch):
        """The control: a destination on the current side must not read as a
        flip, or every slew would trip the guard."""
        now = time.time()
        lon = -110.0
        ra_now = _ra_at_hour_angle(-2.0, lon, now)
        tel = _am5(monkeypatch, measured=PierSide.WEST, ra_now=ra_now, lon=lon)
        ra_dest = _ra_at_hour_angle(-4.0, lon, now)
        assert await tel.destination_pier_side(ra_dest, 20.0) is PierSide.WEST

    async def test_a_mount_that_contradicts_the_geometry_gets_UNKNOWN(
            self, monkeypatch):
        """THE SAFETY PROPERTY. Geometry says the tube should be west and the
        mount says east: one of the two is wrong and nothing here can say
        which. UNKNOWN is the honest answer, and the guard passes it.

        This is not hypothetical -- it is the state of a mount that crossed the
        meridian and did not flip, which is 2026-09-10/11 for four hours.
        """
        now = time.time()
        lon = -110.0
        ra_now = _ra_at_hour_angle(-2.0, lon, now)      # geometry: west
        tel = _am5(monkeypatch, measured=PierSide.EAST, ra_now=ra_now, lon=lon)
        ra_dest = _ra_at_hour_angle(+2.0, lon, now)
        assert await tel.destination_pier_side(ra_dest, 20.0) is \
            PierSide.UNKNOWN, (
            "the driver returned a confident prediction while its own mount "
            "was contradicting the rule the prediction is made from")

    async def test_a_mount_that_will_not_say_gets_UNKNOWN(self, monkeypatch):
        """No baseline, so no prediction. Never guess from a mount that
        declines to answer."""
        now = time.time()
        lon = -110.0
        ra_now = _ra_at_hour_angle(-2.0, lon, now)
        tel = _am5(monkeypatch, measured=PierSide.UNKNOWN, ra_now=ra_now,
                   lon=lon)
        assert await tel.destination_pier_side(ra_now, 20.0) is PierSide.UNKNOWN

    async def test_a_dead_link_gets_UNKNOWN_not_an_exception(self,
                                                             monkeypatch):
        """This is called from a pre-slew safety gate. It must degrade, not
        raise into it."""
        from astrodeck.devices.base import DeviceError
        now = time.time()
        tel = _am5(monkeypatch, measured=PierSide.WEST,
                   ra_now=_ra_at_hour_angle(-2.0, 0.0, now))

        async def _dead():
            raise DeviceError("serial link is wedged")

        tel.pier_side = _dead
        assert await tel.destination_pier_side(6.0, 20.0) is PierSide.UNKNOWN


# ------------------------------------------------- the published status block


class _Tel:
    def __init__(self, side, *, raises=False):
        self.connected = True
        self._side = side
        self._raises = raises
        self.reads = 0

    async def pier_side(self):
        self.reads += 1
        if self._raises:
            raise RuntimeError("serial read timed out")
        return self._side

    async def time_to_meridian_flip(self):
        return None


class _EngineWithFlipPlan:
    """Just enough engine for ``_plan_flip_enabled`` to answer True."""

    flip_owed = False

    def __init__(self):
        from astrodeck.sequence import SequencePlan
        self.plan = SequencePlan(targets=[], meridian_flip=True)


def _hub(monkeypatch):
    """A real ``Hub`` with only the fields ``_compute_meridian`` reads.

    ``Hub.site`` is a read-only property fed by the config store, so the site
    is set by pointing the store at a config rather than by assignment -- and
    emphatically not at the developer's own site.
    """
    from astrodeck.config import AppConfig, config_store
    from astrodeck.hub import Hub

    cfg = AppConfig()
    cfg.site.latitude, cfg.site.longitude = 45.0, -110.0
    monkeypatch.setattr(config_store, "cfg", lambda: cfg)

    h = Hub.__new__(Hub)
    h.engine = None
    h._pier_side_seen = None
    return h


class TestTheStatusBlockCarriesIt:
    pytestmark = _async

    async def test_a_fresh_read_is_reported_as_such(self, monkeypatch):
        h = _hub(monkeypatch)
        m = await h._compute_meridian(_Tel(PierSide.WEST), 5.0, 20.0)
        assert m["pier_side"] == "west"
        assert m["pier_side_source"] == "mount"
        assert m["pier_side_age_s"] == 0.0

    async def test_one_failed_read_does_not_erase_the_side(self, monkeypatch):
        """THE DEFECT. A serial hiccup used to answer 'unknown', which is a
        claim that nobody can say where a tube is that has not moved."""
        h = _hub(monkeypatch)
        await h._compute_meridian(_Tel(PierSide.WEST), 5.0, 20.0)
        m = await h._compute_meridian(_Tel(PierSide.WEST, raises=True),
                                      5.0, 20.0)
        assert m["pier_side"] == "west", (
            "a timed-out :Gm# erased a pier side the mount had just reported")
        assert m["pier_side_source"] == "cached"
        assert m["pier_side_age_s"] is not None

    async def test_the_cached_side_also_keeps_the_flip_status_honest(
            self, monkeypatch):
        """`_is_gem` reads the same value, so caching the side in one field
        and not the others would leave the block contradicting itself: a GEM
        that reads as a fork for one poll, with its flip countdown gone."""
        h = _hub(monkeypatch)
        h.engine = _EngineWithFlipPlan()

        good = await h._compute_meridian(_Tel(PierSide.WEST), 5.0, 20.0)
        assert good["flip_enabled"] is True, (
            "precondition: a GEM with a flip plan must read as flip_enabled on "
            "a GOOD read, or the assertion below proves nothing")

        m = await h._compute_meridian(_Tel(PierSide.WEST, raises=True),
                                      5.0, 20.0)
        assert m["flip_enabled"] is True, (
            "one failed serial read demoted a GEM to a fork and took the flip "
            "countdown with it")
        assert m["status"] != "n_a_fork"

    async def test_a_stale_cache_expires_to_none(self, monkeypatch):
        """Five minutes, because a link that has been quiet that long may well
        have had its mount flipped by hand underneath it."""
        import astrodeck.hub as hub_mod
        h = _hub(monkeypatch)
        await h._compute_meridian(_Tel(PierSide.WEST), 5.0, 20.0)
        h._pier_side_seen = ("west",
                             time.time() - hub_mod.PIER_SIDE_STALE_S - 1.0)
        m = await h._compute_meridian(_Tel(PierSide.WEST, raises=True),
                                      5.0, 20.0)
        assert m["pier_side"] == "unknown"
        assert m["pier_side_source"] == "none"
        assert m["pier_side_age_s"] is None

    async def test_a_mount_that_never_answered_reports_none(self, monkeypatch):
        h = _hub(monkeypatch)
        m = await h._compute_meridian(_Tel(PierSide.UNKNOWN), 5.0, 20.0)
        assert m["pier_side"] == "unknown"
        assert m["pier_side_source"] == "none"

    async def test_flip_owed_rides_the_block(self, monkeypatch):
        """The field the UI's PIER tile turns red on."""
        h = _hub(monkeypatch)

        class _Eng:
            flip_owed = True

        h.engine = _Eng()
        m = await h._compute_meridian(_Tel(PierSide.WEST), 5.0, 20.0)
        assert m["flip_owed"] is True

    async def test_flip_owed_is_false_with_no_engine(self, monkeypatch):
        h = _hub(monkeypatch)
        m = await h._compute_meridian(_Tel(PierSide.WEST), 5.0, 20.0)
        assert m["flip_owed"] is False


# --------------------------------------------------------- and it is redacted


class TestTheRedactorKnowsAboutIt:
    def test_flip_owed_is_withheld_from_a_non_holder(self):
        """It is true only once the target has crossed, so it is another
        reading of the SIGN of an hour angle -- the same reason `due` had to be
        collapsed. See redact.py's own argument."""
        from astrodeck.api.redact import _strip_meridian_derived
        m = {"status": "due", "hours_to_flip": -0.3, "flip_enabled": True,
             "pier_side": "west", "pier_side_source": "mount",
             "pier_side_age_s": 0.0, "flip_owed": True}
        _strip_meridian_derived(m)
        assert m["flip_owed"] is None, (
            "flip_owed survived redaction, and it is derived from the "
            "longitude just as `due` is")

    def test_it_goes_null_and_not_false(self):
        """False is a CLAIM that no flip is owed. This seam withholds; it must
        never answer a safety question on behalf of a caller it is withholding
        the answer from."""
        from astrodeck.api.redact import _strip_meridian_derived
        m = {"status": "due", "hours_to_flip": -0.3, "flip_owed": True}
        _strip_meridian_derived(m)
        assert m["flip_owed"] is not False

    def test_the_pier_side_and_its_provenance_stay(self):
        """They are properties of the mount and the link, not of where the
        observer is standing -- the existing rule for `pier_side`, extended to
        the two fields that describe it."""
        from astrodeck.api.redact import _strip_meridian_derived
        m = {"status": "due", "hours_to_flip": -0.3, "pier_side": "west",
             "pier_side_source": "cached", "pier_side_age_s": 42.0,
             "flip_owed": False}
        _strip_meridian_derived(m)
        assert m["pier_side"] == "west"
        assert m["pier_side_source"] == "cached"
        assert m["pier_side_age_s"] == 42.0
