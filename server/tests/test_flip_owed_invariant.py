# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Do not open the shutter while a meridian flip is owed and has not happened.

2026-09-10/11. At the flip the mount refused to track and the flip was abandoned.
The park/unpark recovery re-acquired NGC 7331 and never flipped it: the field
rotation before and after reads 96.74 and 96.6 with the rotator provably
static, which is a re-slew to the same side. The run kept exposing on the wrong
side of the pier, walking 65 arcsec/min, and every one of those subs was thrown
away.

THE FLIP LATCH WAS LEFT ARMED SO A RETRY WOULD FIRE. Across two frames and
twenty-four minutes, no retry fired, and the condition inside that gate which
suppressed it was never found. That is the whole argument for writing this as
an INVARIANT rather than as a better retry: a retry has to understand the bug,
and nobody understood this one. An invariant asserts what must be true before a
photon is worth collecting and refuses when it is not, whatever the state
machine above it did.

WHY THE TEST IS "THE SIDE DID NOT CHANGE" AND NOT "THE SIDE SHOULD BE EAST".
The second form bakes in a pier-side convention. A mount whose east/west sense
is the opposite of ours would then be held forever after a flip that worked
perfectly -- the invariant turning into the outage. So the engine remembers the
side the mount ACTUALLY REPORTED while the target was still east of the
meridian and trips only when the side after the crossing is that same one.
`TestItReasonsFromMeasurementNotConvention` is the class that pins this down; it
runs the whole thing with the convention inverted and expects no hold.

THE RECORD IS THE PRE-FLIP SIDE, ALWAYS, AND ONE PER TARGET. A flip that
succeeds inside the hold used to overwrite it with the post-flip side, so the
next frame re-tripped (#136, 2026-09-22); and it was one engine-wide slot, so
another target near its meridian could overwrite it (I-19). The first is
pinned by `test_a_flip_that_arrives_releases_the_hold`, the second by
`TestTheRecordIsKeptPerTarget`. And it was refreshed on every frame east of
the meridian, so an early flip taken inside the lead window was recorded as
the side to leave (#222); it is now written once, at the first sighting east
of the meridian, pinned by `TestThePreFlipSideIsRecordedOnce`.

AND IT IS KEPT ALL RUN (#237, H3 orchestrator ruling 2, spec "Still waiting
on the owner" item 11). H2 cleared a target's record once the flip gate had
measured its flip, and wrote none again that run. A target re-acquired east
of its meridian after that, inside the flip-lead window where the AM5 picks
the pre-flip side from the hour angle, then had no record, and carried past
the meridian on that side it was let through. The side a target occupies
before its flip is a fixed property of its east side, so the record stays
true all run; `TestTheRecordOutlivesAMeasuredFlip` pins it.
"""
from __future__ import annotations

import asyncio
import time

import pytest

import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_mod
from astrodeck.config import AppConfig
from astrodeck.devices.base import PierSide
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.engine import StopTarget
from astrodeck.sequence.models import ExposureStep, Target
from astrodeck.sequence.session import SessionStore

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    # A REAL SITE, stated. Every case here ran at the 0,0 default until #24
    # taught the invariant to stand down without a site - which the invariant
    # now does, correctly, and which turned eight of these green-for-the-wrong-
    # reason cases red. The hour angle is stubbed below, so the numbers do not
    # matter; that there ARE numbers does.
    monkeypatch.setattr(Hub, "site", property(
        lambda self: {"latitude": 40.0, "longitude": -74.0,
                      "elevation_m": 10.0, "is_default": False}))
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _target(name="T", dec=20.0, ra=0.0, count=3):
    return Target(name=name, ra_hours=ra, dec_deg=dec, center=False,
                  autofocus_first=False,
                  steps=[ExposureStep(filter="L", exposure_s=0.05,
                                      count=count)])


def _engine(sim_hub, monkeypatch, *, side="west", ttf_h=-0.5,
            pre_flip="west", dec=20.0, hold_min=0.02, flip=True):
    """An engine whose mount reports ``side`` and whose target is ``ttf_h``
    hours from its flip point (negative = already across).

    ``hours_to_meridian_flip`` is stubbed rather than arranged through the
    target's RA and the clock: the invariant is about the RELATION between the
    countdown and the measured side, and driving it through sidereal time would
    make every assertion here depend on what hour the suite happens to run at.

    ``pre_flip`` is THIS target's record. The memory is keyed per target (I-19,
    extending #136), so None means no entry for it at all.
    """
    t = _target(dec=dec)
    e = SequenceEngine(sim_hub)
    e.plan = SequencePlan(targets=[t], meridian_flip=flip, guide=False)
    cfg = AppConfig()
    cfg.safety.flip_owed_hold_min = hold_min
    e._cfg = cfg
    e._pre_flip_side = {} if pre_flip is None else {t.id: pre_flip}

    async def _pier():
        return {"east": PierSide.EAST, "west": PierSide.WEST}.get(
            side, PierSide.UNKNOWN)

    monkeypatch.setattr(sim_hub.devices["telescope"], "pier_side", _pier)
    monkeypatch.setattr(engine_mod.schedule, "hours_to_meridian_flip",
                        lambda ra, lon, now=None: ttf_h)
    return e, t


async def _never_flips(e, monkeypatch):
    """The 2026-09-11 mount: the flip gate runs and nothing changes sides."""
    tried = []

    async def _flip(target, next_exposure_s=0.0):
        tried.append(time.time())

    monkeypatch.setattr(e, "_maybe_meridian_flip", _flip)
    monkeypatch.setattr(engine_mod, "FLIP_OWED_POLL_S", 0.01)
    return tried


# ------------------------------------------------------------- it refuses


class TestItRefusesTheFrame:
    async def test_the_night_itself(self, sim_hub, monkeypatch):
        """Past the flip point, the mount still on the side it was on before
        it -- the exact state that produced four hours of binned subs."""
        e, t = _engine(sim_hub, monkeypatch, side="west", ttf_h=-0.4,
                       pre_flip="west")
        tried = await _never_flips(e, monkeypatch)
        with pytest.raises(StopTarget) as got:
            await e._enforce_flip_owed(t)
        assert "flip" in str(got.value).lower()
        assert tried, (
            "the hold never gave the flip gate another attempt; it is meant to "
            "re-arm and retry while it waits, even though the refusal is what "
            "makes it safe")

    async def test_it_says_so_at_error_level(self, sim_hub, monkeypatch):
        """A run that has stopped taking frames must say why, loudly. Four
        hours of 2026-09-11 passed with the log saying nothing was wrong."""
        from astrodeck.events import bus
        lines = []
        monkeypatch.setattr(bus, "log",
                            lambda lvl, msg, src=None, **kw:
                            lines.append((lvl, msg)))
        e, t = _engine(sim_hub, monkeypatch, side="west", ttf_h=-0.4)
        await _never_flips(e, monkeypatch)
        with pytest.raises(StopTarget):
            await e._enforce_flip_owed(t)
        assert any(lvl == "error" and 'flip is required' in msg
                   for lvl, msg in lines), lines

    async def test_flip_owed_is_published_while_it_holds(self, sim_hub,
                                                         monkeypatch):
        """The status field the UI's PIER tile reads. It must be True DURING
        the hold, not merely settable -- a flag that is only ever observed
        after the hold ends is a flag nobody can act on."""
        e, t = _engine(sim_hub, monkeypatch, side="west", ttf_h=-0.4,
                       hold_min=0.05)
        seen = []

        async def _flip(target, next_exposure_s=0.0):
            seen.append(e.flip_owed)

        monkeypatch.setattr(e, "_maybe_meridian_flip", _flip)
        monkeypatch.setattr(engine_mod, "FLIP_OWED_POLL_S", 0.01)
        with pytest.raises(StopTarget):
            await e._enforce_flip_owed(t)
        assert seen and all(seen), (
            f"flip_owed was not True while the engine was refusing to expose: "
            f"{seen}")
        assert e.flip_owed is False, "flip_owed stayed latched after the hold"

    async def test_a_flip_that_arrives_releases_the_hold(self, sim_hub,
                                                         monkeypatch):
        """The control that keeps this from being a worse bug than the one it
        fixes: when the mount DOES flip, the run resumes rather than skipping
        a target that is now perfectly shootable - on this frame AND the next.

        THE NEXT FRAME IS THE POINT (#136). This case used to stop after one
        call and assert that the hold had recorded the post-flip side, which
        pinned the defect: comparing the next frame against the side the mount
        flipped TO is exactly what re-trips the invariant. On 2026-09-22 that
        cost 26 minutes, 16 re-slews and a set-aside target seven minutes
        after a good flip. So a second call follows, with the mount still
        east and the countdown still negative, and it must go straight
        through. The record stays the PRE-flip side.

        The second call's hold bound is cut to about a second only so that a
        regression fails in a second rather than after the full minute; the
        assertion is whether it holds at all.

        MUTATION "restore = now_side" (``self._pre_flip_side[key] = now_side``
        put back in `_hold_for_owed_flip` on success). Observed - the retry
        count is however many 0.01 s polls fit in the cut hold -
            Failed: the frame after a good flip was held again and the target
            set aside: T: a meridian flip has been owed for 0 min and the
            mount is still on the east side; moving on rather than exposing
            across the pier (the flip was re-attempted 78 more time(s))
        """
        e, t = _engine(sim_hub, monkeypatch, side="west", ttf_h=-0.4,
                       hold_min=1.0)
        state = {"side": "west"}
        flips: list[str] = []

        async def _pier():
            return {"east": PierSide.EAST,
                    "west": PierSide.WEST}[state["side"]]

        async def _flip(target, next_exposure_s=0.0):
            flips.append(state["side"])
            state["side"] = "east"          # this time the mount flips

        monkeypatch.setattr(sim_hub.devices["telescope"], "pier_side", _pier)
        monkeypatch.setattr(e, "_maybe_meridian_flip", _flip)
        monkeypatch.setattr(engine_mod, "FLIP_OWED_POLL_S", 0.01)
        await e._enforce_flip_owed(t)       # returns; does NOT raise
        assert e.flip_owed is False
        assert flips == ["west"], f"premise: one flip, taken from west: {flips}"

        # The next frame: still east, still past the meridian.
        e._cfg.safety.flip_owed_hold_min = 0.02
        try:
            await e._enforce_flip_owed(t)
        except StopTarget as exc:
            pytest.fail(
                f"the frame after a good flip was held again and the target "
                f"set aside: {exc} (the flip was re-attempted "
                f"{len(flips) - 1} more time(s))")
        assert flips == ["west"], (
            f"the frame after a good flip re-armed the flip gate: {flips}")
        assert e.flip_owed is False
        assert e._pre_flip_side == {t.id: "west"}, (
            f"the record is not the side the mount was on BEFORE the flip: "
            f"{e._pre_flip_side!r}")


# ------------------------------------------------- and it refuses nothing else


class TestItDoesNotFireOtherwise:
    async def test_before_the_meridian_it_only_watches(self, sim_hub,
                                                       monkeypatch):
        """East of the meridian nothing is owed, and this is where the
        pre-flip side gets recorded."""
        e, t = _engine(sim_hub, monkeypatch, side="west", ttf_h=+2.0,
                       pre_flip=None)
        await e._enforce_flip_owed(t)
        assert e._pre_flip_side == {t.id: "west"}, (
            f"the pre-flip side was not recorded: {e._pre_flip_side!r}")
        assert e.flip_owed is False

    async def test_a_flip_that_happened_passes(self, sim_hub, monkeypatch):
        """Side changed across the crossing. That is what a flip looks like
        from here, and it must cost nothing."""
        e, t = _engine(sim_hub, monkeypatch, side="east", ttf_h=-0.4,
                       pre_flip="west")
        await e._enforce_flip_owed(t)
        assert e.flip_owed is False

    async def test_a_target_acquired_already_west_is_not_guarded(
            self, sim_hub, monkeypatch):
        """No pre-flip reading exists, so there is nothing to compare against.
        Correct: a mount that slewed there landed on the side it chose."""
        e, t = _engine(sim_hub, monkeypatch, side="west", ttf_h=-3.0,
                       pre_flip=None)
        await e._enforce_flip_owed(t)
        assert e.flip_owed is False
        assert e._pre_flip_side == {}, (
            f"a target seen only west of the meridian got a pre-flip record: "
            f"{e._pre_flip_side!r}")

    async def test_a_plan_with_no_flip_is_not_guarded(self, sim_hub,
                                                      monkeypatch):
        """`meridian_flip=False` is an operator saying they will handle it.
        Holding their run hostage for a flip they switched off is not safety."""
        e, t = _engine(sim_hub, monkeypatch, side="west", ttf_h=-0.4,
                       flip=False)
        await e._enforce_flip_owed(t)
        assert e.flip_owed is False

    async def test_the_exemption_is_the_flip_gates_own_predicate(
            self, sim_hub, monkeypatch):
        """A target the flip gate itself would excuse must not be held here.

        `flip_can_be_skipped` returns False for EVERY mount today, on purpose:
        the over-pole optimisation was disconnected after it cost four nights,
        and its docstring says the geometry reconnects "here and nowhere else"
        if a driver ever reports a mount type. So there is no declination that
        can drive this branch, and a test that hunted for one would be asserting
        the disconnection rather than the exemption.

        What this grades instead is the SEAM: the invariant asks that one
        shared predicate, so whatever it starts answering, the flip gate and
        this invariant answer together. Two copies of "which targets are
        exempt" is how the flip machinery got into trouble the first time.
        """
        from astrodeck.sequence import schedule
        assert schedule.flip_can_be_skipped(89.0, 45.0, "west") is False, (
            "premise: the predicate is expected to excuse nothing today -- if "
            "that changed, this test should be reconsidered, not deleted")

        e, t = _engine(sim_hub, monkeypatch, side="west", ttf_h=-0.4)
        monkeypatch.setattr(engine_mod.schedule, "flip_can_be_skipped",
                            lambda dec, lat, side, **kw: True)
        await e._enforce_flip_owed(t)
        assert e.flip_owed is False, (
            "the invariant held a target the flip gate would have excused, so "
            "it is not reading the shared predicate")

    async def test_an_unreadable_mount_neither_records_nor_trips(
            self, sim_hub, monkeypatch):
        """UNREADABLE IS NOT A VERDICT, in both directions. A dropped serial
        link must not trip the invariant, and it must not be recorded as a
        pre-flip side either -- a bad reading laundered into the baseline is
        how a guard starts lying."""
        async def _dead():
            raise RuntimeError("serial link is wedged")

        e, t = _engine(sim_hub, monkeypatch, side="west", ttf_h=+2.0,
                       pre_flip=None)
        monkeypatch.setattr(sim_hub.devices["telescope"], "pier_side", _dead)
        await e._enforce_flip_owed(t)
        assert e._pre_flip_side == {}, (
            f"an unreadable mount set the baseline: {e._pre_flip_side!r}")
        assert e.flip_owed is False

    async def test_zero_disables_it(self, sim_hub, monkeypatch):
        """0 is off, as everywhere else in this config."""
        e, t = _engine(sim_hub, monkeypatch, side="west", ttf_h=-0.4,
                       hold_min=0.0)
        await e._enforce_flip_owed(t)
        assert e.flip_owed is False


# --------------------------------------- measurement, not convention


class TestItReasonsFromMeasurementNotConvention:
    async def test_an_inverted_mount_is_not_held_after_a_good_flip(
            self, sim_hub, monkeypatch):
        """THE TEST THAT RULES OUT THE TEMPTING IMPLEMENTATION.

        A mount whose east/west sense is the opposite of ASCOM's reports EAST
        while the target is still east of the meridian, and WEST after it
        flips. A rule written as "past the meridian, so it ought to be east"
        would hold this rig forever on a flip that worked. The rule written as
        "the side must have CHANGED" gets it right without knowing which
        convention the mount uses.
        """
        e, t = _engine(sim_hub, monkeypatch, side="east", ttf_h=+2.0,
                       pre_flip=None)
        await e._enforce_flip_owed(t)            # pre-meridian: records EAST
        assert e._pre_flip_side == {t.id: "east"}

        monkeypatch.setattr(engine_mod.schedule, "hours_to_meridian_flip",
                            lambda ra, lon, now=None: -0.4)

        async def _west():
            return PierSide.WEST                 # it flipped, to "west"

        monkeypatch.setattr(sim_hub.devices["telescope"], "pier_side", _west)
        await e._enforce_flip_owed(t)            # must NOT hold
        assert e.flip_owed is False

    async def test_the_same_inverted_mount_IS_held_when_it_does_not_flip(
            self, sim_hub, monkeypatch):
        """The other half, or the test above would pass on a rule that never
        fires at all."""
        e, t = _engine(sim_hub, monkeypatch, side="east", ttf_h=+2.0,
                       pre_flip=None)
        await e._enforce_flip_owed(t)
        assert e._pre_flip_side == {t.id: "east"}

        monkeypatch.setattr(engine_mod.schedule, "hours_to_meridian_flip",
                            lambda ra, lon, now=None: -0.4)
        await _never_flips(e, monkeypatch)
        with pytest.raises(StopTarget):
            await e._enforce_flip_owed(t)        # still east: owed


# ------------------------------------------------------ one record per target


class TestTheRecordIsKeptPerTarget:
    """I-19, extending #136: the pre-flip side is one record PER TARGET.

    It was one engine-wide slot, so a run that alternates targets near the
    meridian - which a rotating mosaic always does - overwrote one target's
    record with another's. The backstop was then disarmed exactly where it is
    needed: a target re-acquired past its meridian on the side it was on
    before, because the AM5 picks its side from the hour angle and a goto
    taken inside the flip-lead window stays on the pre-flip side.

    Two targets here, told apart by RA: the countdown stub answers per RA,
    and one pier side is shared, because it is one mount.
    """

    @staticmethod
    def _two(sim_hub, monkeypatch):
        a, b = _target("A", ra=1.0), _target("B", ra=2.0)
        e = SequenceEngine(sim_hub)
        e.plan = SequencePlan(targets=[a, b], meridian_flip=True, guide=False)
        cfg = AppConfig()
        cfg.safety.flip_owed_hold_min = 0.02
        e._cfg = cfg
        ttf = {}
        mount = {"side": "west"}

        async def _pier():
            return {"east": PierSide.EAST, "west": PierSide.WEST}.get(
                mount["side"], PierSide.UNKNOWN)

        monkeypatch.setattr(sim_hub.devices["telescope"], "pier_side", _pier)
        monkeypatch.setattr(engine_mod.schedule, "hours_to_meridian_flip",
                            lambda ra, lon, now=None: ttf[ra])
        return e, a, b, ttf, mount

    async def test_a_record_survives_another_target_near_the_meridian(
            self, sim_hub, monkeypatch):
        """A is shot inside its flip-lead window (6 min before the meridian,
        inside the default 10) on the west side. B, east of its meridian, is
        then shot on the east side. A is re-acquired after its crossing and
        the goto kept it west: the flip is owed, and the invariant must hold.

        MUTATION "engine-wide slot" (`_enforce_flip_owed` keys every target's
        record on one constant, ``key = "engine"``). Observed:
            AssertionError: A was re-acquired past its meridian on the side it
            was on before it and was let through to expose: the records read
            {'engine': 'east'}
        """
        e, a, b, ttf, mount = self._two(sim_hub, monkeypatch)
        ttf.update({a.ra_hours: +0.1, b.ra_hours: +2.0})
        await e._enforce_flip_owed(a)            # inside A's lead window
        mount["side"] = "east"
        await e._enforce_flip_owed(b)            # B runs on the other side
        ttf[a.ra_hours] = -0.05                  # A has crossed...
        mount["side"] = "west"                   # ...and the goto kept it west
        tried = await _never_flips(e, monkeypatch)
        held = None
        try:
            await e._enforce_flip_owed(a)
        except StopTarget as exc:
            held = exc
        assert held is not None, (
            f"A was re-acquired past its meridian on the side it was on "
            f"before it and was let through to expose: the records read "
            f"{e._pre_flip_side!r}")
        assert tried, "premise: the hold gives the flip gate its retries"
        assert e._pre_flip_side == {a.id: "west", b.id: "east"}, (
            f"A's record did not survive B: {e._pre_flip_side!r}")

    async def test_control_a_target_acquired_west_records_nothing_and_never_trips(
            self, sim_hub, monkeypatch):
        """CONTROL. A has a west record from before its crossing. B is
        acquired already west of its meridian, on the same west side: it owes
        nothing, gets no record, and is not held on A's.

        MUTATION "engine-wide slot" (as above). Observed:
            Failed: B, acquired west of its meridian, was held on another
            target's record: B: a meridian flip has been owed for 0 min and
            the mount is still on the west side; moving on rather than
            exposing across the pier
        """
        e, a, b, ttf, mount = self._two(sim_hub, monkeypatch)
        ttf.update({a.ra_hours: +0.5, b.ra_hours: -3.0})
        await e._enforce_flip_owed(a)
        assert list(e._pre_flip_side.values()) == ["west"], (
            f"premise: A's west side is on record: {e._pre_flip_side!r}")
        tried = await _never_flips(e, monkeypatch)
        try:
            await e._enforce_flip_owed(b)
        except StopTarget as exc:
            pytest.fail(f"B, acquired west of its meridian, was held on "
                        f"another target's record: {exc}")
        assert e.flip_owed is False and tried == []
        assert b.id not in e._pre_flip_side, e._pre_flip_side

    async def test_control_an_unreadable_side_records_nothing_for_that_target(
            self, sim_hub, monkeypatch):
        """CONTROL. B east of its meridian with the side unreadable: no record
        for B, and A's record is left exactly as it was."""
        e, a, b, ttf, mount = self._two(sim_hub, monkeypatch)
        ttf.update({a.ra_hours: +0.5, b.ra_hours: +2.0})
        await e._enforce_flip_owed(a)
        mount["side"] = "unknown"
        await e._enforce_flip_owed(b)
        assert e._pre_flip_side == {a.id: "west"}, e._pre_flip_side

    async def test_start_clears_every_record(self, sim_hub, monkeypatch):
        """A new run starts with no records: `start()` resets the memory, as
        it resets every other per-run flip latch.

        MUTATION "start keeps the records" (the reset line deleted). Observed:
            AssertionError: a new run inherited the last run's pre-flip
            records: {'4c98ebab46e64ca0891609adc8cb8640': 'west'}
        """
        e, a, b, ttf, mount = self._two(sim_hub, monkeypatch)
        e._pre_flip_side = {a.id: "west"}
        monkeypatch.setattr(SessionStore, "load_all", lambda self: [])
        monkeypatch.setattr(engine_mod.SequenceEngine, "_run",
                            lambda self: asyncio.sleep(0))
        e.start(SequencePlan(targets=[a], meridian_flip=True, guide=False))
        await asyncio.sleep(0)
        assert e._pre_flip_side == {}, (
            f"a new run inherited the last run's pre-flip records: "
            f"{e._pre_flip_side!r}")


# ------------------------------------------- one pre-flip record, written once


class TestThePreFlipSideIsRecordedOnce:
    """#222: a flip taken inside the lead window, before the meridian, used to
    be recorded as the pre-flip side.

    `_enforce_flip_owed` wrote the side on EVERY frame while the target was
    still east of its meridian. The flip gate fires at the plan's lead, ten
    minutes before transit, and on a mount that can flip early the flip then
    lands while the target is still east: the next frame recorded the side
    the mount had flipped TO, and past the meridian the side "had not
    changed", so the invariant held a target that had flipped correctly for
    `flip_owed_hold_min` and set it aside. The #136 class, by the route of an
    ordinary early flip instead of the flip-owed hold.

    Now the side is recorded only when there is no record, and nothing
    clears it within the run (#237, H3 orchestrator ruling 2): not the flip
    gate after a flip it MEASURED, which H2 made clear the record and close
    the target's "cycle", and not `_setup_target`. Clearing would not do in
    either place: the next frame before the meridian would find no record and
    write the post-flip side, which is #222 again, and a target re-acquired
    on its pre-flip side would have nothing to be compared with
    (`TestTheRecordOutlivesAMeasuredFlip`, `TestTheRecordIsKeptPerTarget`).

    The mount here flips early: its side changes at the flip gate's goto,
    minutes before transit, while the countdown is still positive. The
    countdown is stubbed per call, as everywhere in this file.
    """

    @staticmethod
    def _early_flipper(sim_hub, monkeypatch):
        """An engine whose mount reports ``mount["side"]`` (a read answers
        "unknown" for each count left in ``mount["unreadable"]``), whose
        target's countdown is ``ttf["h"]``, and whose flip, taken through the
        real `_maybe_meridian_flip`, moves the mount to ``mount["to"]``. The
        mount gives no countdown of its own, the safety gate is a no-op, and
        there is no focuser for a post-flip sweep."""
        t = _target(dec=20.0)
        e = SequenceEngine(sim_hub)
        e.plan = SequencePlan(targets=[t], meridian_flip=True, guide=False)
        cfg = AppConfig()
        cfg.safety.flip_owed_hold_min = 0.02
        e._cfg = cfg
        ttf = {"h": +0.15}
        mount = {"side": "west", "to": "east", "unreadable": 0}
        flips: list[str] = []
        tel = sim_hub.devices["telescope"]

        async def _pier():
            if mount["unreadable"] > 0:
                mount["unreadable"] -= 1
                return PierSide.UNKNOWN
            return {"east": PierSide.EAST, "west": PierSide.WEST}[mount["side"]]

        async def _no_countdown():
            raise RuntimeError("no countdown")

        async def _flip(ra, dec):
            flips.append(mount["side"])
            mount["side"] = mount["to"]
            return {}

        async def _noop_gate(context="", target=None):
            return None

        monkeypatch.setattr(tel, "pier_side", _pier)
        monkeypatch.setattr(tel, "time_to_meridian_flip", _no_countdown)
        monkeypatch.setattr(sim_hub, "meridian_flip", _flip)
        monkeypatch.delitem(sim_hub.devices, "focuser", raising=False)
        monkeypatch.setattr(e, "_safety_gate", _noop_gate)
        monkeypatch.setattr(engine_mod.schedule, "hours_to_meridian_flip",
                            lambda ra, lon, now=None: ttf["h"])
        monkeypatch.setattr(engine_mod, "FLIP_OWED_POLL_S", 0.01)
        return e, t, ttf, mount, flips

    @pytest.mark.parametrize("route", ["measured by the flip gate",
                                       "not measured"])
    async def test_an_early_flip_is_not_recorded_as_the_pre_flip_side(
            self, sim_hub, monkeypatch, route):
        """#222's reproduction, three frames and the flip between the first
        two:

        1. countdown +0.15 h, the mount west: west is the pre-flip side;
        the flip, at +0.14 h, inside the lead: the mount goes east;
        2. countdown +0.12 h, the mount east: the frame after the flip and
           before the meridian, where the post-flip side used to be recorded;
        3. countdown -0.05 h, the mount east: past the meridian on the side
           it flipped to. No hold, and no StopTarget.

        Two routes to the flip. MEASURED: the flip gate's own goto, with both
        side reads readable and different. NOT MEASURED: the gate's reads
        before its goto answer "unknown", so the flip is not evidence of
        anything. The record is kept on both, because it is written once and
        nothing clears it.

        Mutant "record on every frame" (``self._pre_flip_side[key] = side`` in
        place of ``setdefault`` in `_enforce_flip_owed`): RED, both routes
        (observed, the measured route) -
            Failed: a target that flipped early (measured by the flip gate) was
            held past the meridian on the side it flipped to and set aside: T: a
            meridian flip has been owed for 0 min and the mount is still on the
            east side; moving on rather than exposing across the pier; the
            records read {'a4c8c2c32a28494884a3717f6b5ffc28': 'east'}, the flip
            was re-attempted 77 more time(s)
        and the same text with "(not measured)" on the other route.
        Mutant "H2's clear and close on a measured flip" (H2's code put back: the
        flip gate pops the record and adds the target to a `_flip_cycle_closed`
        set after a measured flip, and `_enforce_flip_owed` records nothing for a
        target in it): RED, the measured route, at the record check right after
        the flip (observed) -
            AssertionError: after the measured by the flip gate flip the records
            read {}; the pre-flip side is kept after any flip
        Mutant "clear on a measured flip" (the flip gate's setdefault replaced by
        ``self._pre_flip_side.pop(key, None)``): RED, the same route, with the
        same text.
        (The retry count is however many 0.01 s polls fit in the cut hold, and
        the key is the target's fresh id.)
        """
        e, t, ttf, mount, flips = self._early_flipper(sim_hub, monkeypatch)
        await e._enforce_flip_owed(t)                       # 1
        assert e._pre_flip_side == {t.id: "west"}, (
            f"premise: frame 1 records west: {e._pre_flip_side!r}")
        ttf["h"] = +0.14
        e._flip_armed = True
        if route == "not measured":
            # The gate reads the side twice before its goto: once for the
            # exemption (unknown reads as a German mount, so it flips), once
            # as the side before. Both answer "unknown".
            mount["unreadable"] = 2
        await e._maybe_meridian_flip(t, 0.0)
        assert flips == ["west"] and mount["side"] == "east", (
            f"premise: the early flip was taken and the mount went east: "
            f"{flips}, now {mount['side']}")
        # BOTH FLIPS KEEP THE RECORD (#237, H3 orchestrator ruling 2). This
        # line used to pin the opposite, H2's clear after a measured flip,
        # and said that flipping it was an open question for the spec. The
        # ruling answered it: the side a target occupies before its flip is
        # a fixed property of its east side, so the record stays true all
        # run, and a later re-acquisition on that side is compared with it
        # (`TestTheRecordOutlivesAMeasuredFlip`). The StopTarget below
        # cannot see this half: a kept west record and no record both let
        # the mount through past the meridian on east.
        # Mutant "H2's clear and close on a measured flip" and mutant "clear
        # on a measured flip" go RED here, on the measured route (the observed
        # text is in the docstring).
        assert e._pre_flip_side == {t.id: "west"}, (
            f"after the {route} flip the records read {e._pre_flip_side!r}; "
            f"the pre-flip side is kept after any flip")
        ttf["h"] = +0.12
        await e._enforce_flip_owed(t)                       # 2
        ttf["h"] = -0.05
        try:
            await e._enforce_flip_owed(t)                   # 3
        except StopTarget as exc:
            pytest.fail(
                f"a target that flipped early ({route}) was held past the "
                f"meridian on the side it flipped to and set aside: {exc}; "
                f"the records read {e._pre_flip_side!r}, the flip was "
                f"re-attempted {len(flips) - 1} more time(s)")
        assert e.flip_owed is False
        assert flips == ["west"], f"the flip was taken again: {flips}"

    async def test_a_flip_before_the_first_sighting_records_the_side_it_left(
            self, sim_hub, monkeypatch):
        """The route to #222 that H2's closed cycle also covered, and that
        H3 orchestrator ruling 2's "setdefault only" would have reopened. In the frame loop
        the flip gate runs BEFORE `_enforce_flip_owed`, so a target whose
        first frame comes inside its flip-lead window, on a mount that can
        flip early, is flipped before anything has recorded its side. The
        first sighting then comes after the flip, still east of the
        meridian, on the side the mount flipped TO; setdefault keeps it, and
        past the meridian on that same side the target is held and set
        aside after a good flip.

        So a flip the gate MEASURED records the side it left, with
        setdefault: by definition the pre-flip side, and nothing is
        recorded over an earlier sighting. An unmeasured flip records
        nothing, as an unreadable side is no evidence of which side is
        which.

        Mutant "no record from the flip gate" (the gate's setdefault of
        ``side_before`` deleted, which is H3 orchestrator ruling 2 applied
        as written): RED
        (observed) -
            Failed: a target flipped before its first sighting was held past
            the meridian on the side it flipped to and set aside: T: a meridian
            flip has been owed for 0 min and the mount is still on the east
            side; moving on rather than exposing across the pier; the records
            read {'033d0625fc4b4019b8e7d540b1612402': 'east'}
        Mutant "H2's clear and close on a measured flip" (as above): RED at the
        last check, H2's closed cycle having let the target through with no
        record at all (observed) -
            AssertionError: the side the measured flip left is not on record: {}
        """
        e, t, ttf, mount, flips = self._early_flipper(sim_hub, monkeypatch)
        ttf["h"] = +0.14                          # first frame, in the lead
        e._flip_armed = True
        await e._maybe_meridian_flip(t, 0.0)
        assert flips == ["west"] and mount["side"] == "east", (
            f"premise: the early flip was taken first and the mount went "
            f"east: {flips}, now {mount['side']}")
        await e._enforce_flip_owed(t)             # the first sighting, east
        ttf["h"] = -0.05
        try:
            await e._enforce_flip_owed(t)
        except StopTarget as exc:
            pytest.fail(
                f"a target flipped before its first sighting was held past "
                f"the meridian on the side it flipped to and set aside: "
                f"{exc}; the records read {e._pre_flip_side!r}")
        assert e.flip_owed is False
        assert e._pre_flip_side == {t.id: "west"}, (
            f"the side the measured flip left is not on record: "
            f"{e._pre_flip_side!r}")

    async def test_control_an_unreadable_side_after_the_flip_keeps_the_record(
            self, sim_hub, monkeypatch):
        """CONTROL. The flip gate's goto, and the read after it answers
        "unknown": that is no evidence that anything moved, and here nothing
        did, the mount is still west. West stays on record, so past the
        meridian, still west, the invariant holds and sets the target aside,
        as it must for a flip that did not happen.

        Mutant "the invariant never holds" (the `_hold_for_owed_flip` call in
        `_enforce_flip_owed` replaced by ``self._flip_owed = False``): RED
        (observed) -
            AssertionError: after a flip whose side after the goto could not be
            read, the mount, still west past the meridian, was let through to
            expose: records {'acce7a3f0bcb4e898fecfe7c6bfaea4b': 'west'}
        H2 pinned this case against "an unreadable side closes the cycle". With
        the cycle gone (#237), a mutant that clears the record after an
        unmeasured flip is healed by the next sighting east of the meridian,
        which writes west again; so this case now guards the hold itself.
        """
        e, t, ttf, mount, flips = self._early_flipper(sim_hub, monkeypatch)
        await e._enforce_flip_owed(t)
        ttf["h"] = +0.14
        e._flip_armed = True

        async def _flip(ra, dec):
            flips.append(mount["side"])
            mount["unreadable"] = 1              # the read after the goto
            return {}

        monkeypatch.setattr(sim_hub, "meridian_flip", _flip)
        await e._maybe_meridian_flip(t, 0.0)
        assert flips == ["west"] and mount["unreadable"] == 0, (
            f"premise: the flip was taken and its read after the goto was "
            f"unreadable: {flips}, {mount}")
        ttf["h"] = +0.12
        await e._enforce_flip_owed(t)
        ttf["h"] = -0.05
        held = None
        try:
            await e._enforce_flip_owed(t)
        except StopTarget as exc:
            held = exc
        assert held is not None, (
            f"after a flip whose side after the goto could not be read, the "
            f"mount, still west past the meridian, was let through to "
            f"expose: records {e._pre_flip_side!r}")
        assert e._pre_flip_side == {t.id: "west"}, e._pre_flip_side


# ------------------------------------------- the record outlives a measured flip


class TestTheRecordOutlivesAMeasuredFlip:
    """#237, H3 orchestrator ruling 2 (spec, Still waiting on the owner, item
    11): nothing clears a target's pre-flip record within the run.

    H2 cleared it the moment the flip gate MEASURED the target's flip (both
    pier-side reads readable and different) and marked the target so that no
    record was written for it again that run. The side a target occupies
    before its flip is a fixed property of its east side, so the record was
    still true when it was dropped, and dropping it disarmed the backstop for
    the case I-19 keyed the record per target for: a target re-acquired east
    of its meridian, inside the flip-lead window, where the AM5 picks the
    pre-flip side from the hour angle. A rotating mosaic does exactly that at
    every hop back to a panel it flipped early. If the repeat flip then does
    not fire, the target is carried past the meridian on the pre-flip side
    with nothing to compare it with, which is the 2026-09-11 night.

    The engine is `TestThePreFlipSideIsRecordedOnce`'s early flipper, the
    flip taken through the real `_maybe_meridian_flip`. The re-acquisition is
    the mount reporting the pre-flip side again at a countdown still inside
    the lead, and "the repeat flip does not fire" is `_never_flips`: the hold
    gives the flip gate its retries and none of them moves the mount.
    """

    @staticmethod
    def _spy_hold(e, monkeypatch) -> list[str]:
        """The side each `_hold_for_owed_flip` was entered on."""
        entered: list[str] = []
        real = e._hold_for_owed_flip

        async def hold(target, side, hold_min):
            entered.append(side)
            return await real(target, side, hold_min)

        monkeypatch.setattr(e, "_hold_for_owed_flip", hold)
        return entered

    async def _measured_early_flip(self, sim_hub, monkeypatch):
        """Frame 1 at +0.15 h on the west side, then the flip gate's goto at
        +0.14 h, inside the lead, measured: both reads readable, west before
        and east after. Then the frame after it, at +0.12 h, on east."""
        e, t, ttf, mount, flips = TestThePreFlipSideIsRecordedOnce \
            ._early_flipper(sim_hub, monkeypatch)
        await e._enforce_flip_owed(t)
        ttf["h"] = +0.14
        e._flip_armed = True
        await e._maybe_meridian_flip(t, 0.0)
        assert flips == ["west"] and mount["side"] == "east", (
            f"premise: the early flip was taken and the mount went east: "
            f"{flips}, now {mount['side']}")
        assert mount["unreadable"] == 0 and e._flip_armed is False, (
            "premise: both reads were readable and the flip spent the latch, "
            "so the gate measured this flip")
        ttf["h"] = +0.12
        await e._enforce_flip_owed(t)
        return e, t, ttf, mount, flips

    async def test_a_target_re_acquired_on_its_pre_flip_side_is_held_past_the_meridian(
            self, sim_hub, monkeypatch):
        """The measured early flip, then a hop away and back: the target is
        re-acquired at +0.05 h, still east of its meridian and inside the
        lead, and the goto lands on west, the pre-flip side. The repeat flip
        does not fire, and the target is carried past the meridian (-0.05 h)
        still on west. `_hold_for_owed_flip` must trip, on west, and end in
        StopTarget rather than let the shutter open.

        Mutant "H2's clear and close on a measured flip" (H2's code put back:
        the gate pops the record and closes the target's cycle, and
        `_enforce_flip_owed` records nothing for a closed target): RED
        (observed) -
            AssertionError: the target re-acquired east of its meridian on its
            pre-flip side after a measured flip was carried past the meridian on
            that side and let through to expose: the hold was entered [], the
            records read {}
        Mutant "clear on a measured flip" (the pop without the closed cycle):
        RED, the next sighting having recorded the post-flip side (observed) -
            AssertionError: the target re-acquired east of its meridian on its
            pre-flip side after a measured flip was carried past the meridian on
            that side and let through to expose: the hold was entered [], the
            records read {'31a1726b46be4f35a0b73b7ad1c58bad': 'east'}
        """
        e, t, ttf, mount, flips = await self._measured_early_flip(
            sim_hub, monkeypatch)
        mount["side"] = "west"                   # re-acquired, pre-flip side
        ttf["h"] = +0.05
        await e._enforce_flip_owed(t)
        assert e.flip_owed is False, "premise: east of the meridian, no hold"
        tried = await _never_flips(e, monkeypatch)
        entered = self._spy_hold(e, monkeypatch)
        ttf["h"] = -0.05                         # carried past the meridian
        held = None
        try:
            await e._enforce_flip_owed(t)
        except StopTarget as exc:
            held = exc
        assert held is not None and entered == ["west"], (
            f"the target re-acquired east of its meridian on its pre-flip "
            f"side after a measured flip was carried past the meridian on "
            f"that side and let through to expose: the hold was entered "
            f"{entered!r}, the records read {e._pre_flip_side!r}")
        assert tried, "premise: the hold gave the flip gate its retries"
        assert mount["side"] == "west" and flips == ["west"], (
            f"premise: the repeat flip did not fire: {flips}, now "
            f"{mount['side']}")
        assert e._pre_flip_side == {t.id: "west"}, e._pre_flip_side

    async def test_control_a_correctly_flipped_target_passes(
            self, sim_hub, monkeypatch):
        """CONTROL. The same measured early flip, and the target carried
        past the meridian on east, the side it flipped to: a changed side,
        which is what a flip looks like from here. No hold, and the record
        is still west.

        Mutant "record on every frame" (as above): RED (observed) -
            Failed: a target that flipped correctly was held past the meridian
            and set aside: T: a meridian flip has been owed for 0 min and the
            mount is still on the east side; moving on rather than exposing
            across the pier; the records read
            {'1a5f491317384072b76ab0e5f5f15ceb': 'east'}
        """
        e, t, ttf, mount, flips = await self._measured_early_flip(
            sim_hub, monkeypatch)
        entered = self._spy_hold(e, monkeypatch)
        ttf["h"] = -0.05
        try:
            await e._enforce_flip_owed(t)
        except StopTarget as exc:
            pytest.fail(f"a target that flipped correctly was held past the "
                        f"meridian and set aside: {exc}; the records read "
                        f"{e._pre_flip_side!r}")
        assert entered == [] and e.flip_owed is False, entered
        assert e._pre_flip_side == {t.id: "west"}, e._pre_flip_side

    async def test_control_a_target_first_seen_west_is_never_recorded_or_held(
            self, sim_hub, monkeypatch):
        """CONTROL. A target first seen already west of its meridian, on
        west, and seen there again later on the same side: no reading was
        ever taken before its crossing, so there is no record and nothing to
        hold it on. A mount that slewed there landed on the side it chose.

        Mutant "record whatever the hour angle" (`_enforce_flip_owed` records
        the side before it asks which side of the meridian the target is on):
        RED (observed) -
            Failed: a target first seen west of its meridian was held at -0.05
            h: T: a meridian flip has been owed for 0 min and the mount is
            still on the west side; moving on rather than exposing across the
            pier; the records read {'c0340b3d972747e9ba7b4857c55bd203': 'west'}
        and with it the two older controls that pin the same rule,
        `test_a_target_acquired_already_west_is_not_guarded` and
        `test_control_a_target_acquired_west_records_nothing_and_never_trips`.
        """
        e, t, ttf, mount, flips = TestThePreFlipSideIsRecordedOnce \
            ._early_flipper(sim_hub, monkeypatch)
        await _never_flips(e, monkeypatch)
        entered = self._spy_hold(e, monkeypatch)
        for h in (-0.05, -0.2, -0.4):
            ttf["h"] = h
            try:
                await e._enforce_flip_owed(t)
            except StopTarget as exc:
                pytest.fail(f"a target first seen west of its meridian was "
                            f"held at {h:+.2f} h: {exc}; the records read "
                            f"{e._pre_flip_side!r}")
        assert entered == [] and e.flip_owed is False, entered
        assert e._pre_flip_side == {}, (
            f"a target seen only west of its meridian got a pre-flip record: "
            f"{e._pre_flip_side!r}")


# ----------------------------------------------------- the call site itself


class TestTheRealFrameLoopCallsIt:
    """THE CALL SITE, not the method.

    Every test above invokes `_enforce_flip_owed` directly, so all of them
    would pass with the call deleted from the frame loop -- a perfect
    implementation wired to nothing, which is the exact shape of the defect
    this file exists for. This one drives the real `_run_step`.
    """

    async def test_no_frame_is_taken_while_the_flip_is_owed(self, sim_hub,
                                                            monkeypatch):
        e, t = _engine(sim_hub, monkeypatch, side="west", ttf_h=-0.4,
                       pre_flip="west", hold_min=0.02)
        await _never_flips(e, monkeypatch)

        async def _noop_gate(context="", target=None):
            return None

        e._safety_gate = _noop_gate
        e._done = {}
        e._frames_done = 0
        captured = []
        real_begin = e._begin_frame

        def _spy(ti, si, exposure_s):
            captured.append((ti, si))
            return real_begin(ti, si, exposure_s)

        monkeypatch.setattr(e, "_begin_frame", _spy)

        with pytest.raises(StopTarget):
            await e._run_step(0, 0, t, t.steps[0])
        assert not captured, (
            f"the frame loop opened the shutter {len(captured)} time(s) with a "
            f"meridian flip owed and the mount still on the pre-flip side -- "
            f"the invariant is not wired into the loop")

    async def test_the_same_loop_shoots_normally_when_nothing_is_owed(
            self, sim_hub, monkeypatch):
        """The control. Without it, a call site that raised unconditionally --
        or a `_run_step` too broken to reach the shutter at all -- would pass
        the test above."""
        e, t = _engine(sim_hub, monkeypatch, side="east", ttf_h=-0.4,
                       pre_flip="west")

        async def _noop_gate(context="", target=None):
            return None

        e._safety_gate = _noop_gate
        e._done = {}
        e._frames_done = 0
        captured = []
        real_begin = e._begin_frame

        def _spy(ti, si, exposure_s):
            captured.append((ti, si))
            return real_begin(ti, si, exposure_s)

        monkeypatch.setattr(e, "_begin_frame", _spy)
        await asyncio.wait_for(e._run_step(0, 0, t, t.steps[0]), 120.0)
        assert captured, (
            "no frame was taken even with the flip performed, so the test "
            "above proves nothing about the invariant")
