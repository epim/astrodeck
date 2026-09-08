"""ONE MERIDIAN FLIP COST THIRTY-TWO MINUTES, AND MOST OF IT BOUGHT NOTHING.

Measured on astrotown, v0.3.25, the night of 2026-09-07/08:

    00:25  the LEAD-TIME attempt, ten minutes before transit. It stopped
           guiding, re-slewed via goto_and_center, discarded the guider
           calibration (GN-01), walked a fresh one for 4.7 minutes, and only
           THEN read the pier side - which had not changed. "re-slewed but the
           mount still reports pier side west - nothing flipped." It then ran a
           7.5 minute post-flip autofocus at 24 s a point, because the wheel
           was on Ha.
    00:37  the real flip at the meridian. Discard, recalibrate (7 min),
           post-flip autofocus again (~8 min, still through Ha) - thirty
           minutes after a temperature refocus, with no temperature change.

The AM5 picks its pier side from the HOUR ANGLE. A GoTo issued while the target
is still east of the meridian is answered by staying exactly where it is, so a
lead-time flip on this mount NEVER flips - the engine already knew that
(`_flip_no_op`) and spent the whole post-flip programme anyway.

Four things had to become true, and this file grades all four:

  1. the pier side is read INSIDE the flip, between the slew and the guider
     restart, so a no-op re-slew keeps the calibration it already has;
  2. a sweep runs through luminance when the offsets can put the focus back;
  3. the post-flip sweep is skipped when focus is fresh and cold;
  4. an initial sweep long enough to matter is followed by a re-centre, because
     it runs UNGUIDED and this rig drifts ~15"/min (2026-09-07: 0.08' at 22:12,
     2.0' off at 22:21).
"""
from __future__ import annotations

import time

import pytest

import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_mod
from astrodeck.devices.base import PierSide
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan, schedule
from astrodeck.sequence.engine import (FRESH_FOCUS_S,
                                       RECENTRE_AFTER_UNGUIDED_S)
from astrodeck.sequence.models import ExposureStep, Target


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


async def _noop_gate(context="", target=None):
    return None


class _FakeGuider:
    """A guider that records what the flip does to it.

    ``needs_calibration`` models GN-01's discard latch rather than restating
    the call list: the real ``NativeGuider.flip_calibration`` deletes the
    persisted calibration and latches the discard, so the next
    ``start_guiding`` MUST walk a fresh one. That consequence - minutes of
    mount pulsing - is what the no-op flip was paying for, so it is what the
    tests below assert on, not merely "a method was not called".
    """
    name = "fake guider"

    def __init__(self, active: bool = True):
        self.connected = True
        self._active = active
        self.calls: list[str] = []
        self._discarded = False

    async def is_active(self) -> bool:
        return self._active

    async def stop_guiding(self) -> None:
        self.calls.append("stop")
        self._active = False

    async def start_guiding(self) -> None:
        self.calls.append("start")
        self._active = True

    async def flip_calibration(self) -> bool:
        self.calls.append("flip_calibration")
        self._discarded = True
        return True

    async def needs_calibration(self) -> bool:
        return self._discarded

    def stats(self):
        return None


class _Result:
    success = True
    message = ""
    position = 1000


def _flip_engine(sim_hub, monkeypatch, *, ttf_min: float = 9.0,
                 the_slew_flips: bool = False, offsets: bool = True,
                 stub_autofocus: bool = True, temp_delta_c: float | None = None):
    """An engine armed ``ttf_min`` minutes before transit, on the REAL
    ``hub.meridian_flip``.

    Only ``goto_and_center`` is faked - it is the slew, and it is also the ONE
    place a mount decides its pier side, so ``the_slew_flips`` is how a test
    says "this mount really did swap sides". Everything between the slew and
    the guider restart is the shipping code.
    """
    tel = sim_hub.devices["telescope"]
    lon = sim_hub.site["longitude"]
    ra = (schedule.lst_hours(lon) + ttf_min / 60.0) % 24.0
    t = Target(name="T", ra_hours=ra, dec_deg=66.11, center=False,
               autofocus_first=False,
               steps=[ExposureStep(filter="L", exposure_s=0.05, count=1)])
    e = SequenceEngine(sim_hub)
    e.plan = SequencePlan(targets=[t], meridian_flip=True, guide=True,
                          apply_filter_offsets=offsets,
                          refocus_on_temp_delta_c=temp_delta_c)
    e._cfg = None
    e._flip_armed = True
    e._safety_gate = _noop_gate

    st = {"side": "west", "flips": the_slew_flips, "gotos": [], "holds": [],
          "af": []}

    async def _pier():
        return {"east": PierSide.EAST,
                "west": PierSide.WEST}.get(st["side"], PierSide.UNKNOWN)

    async def _no_countdown():
        return None

    async def _goto(ra_h, dec_d, **kw):
        st["gotos"].append((ra_h, dec_d))
        if st["flips"]:
            st["side"] = "east" if st["side"] == "west" else "west"
        return {"centered": True, "error_arcmin": 0.2}

    async def _hold(target, lead_s=0.0):
        st["holds"].append(lead_s)

    monkeypatch.setattr(tel, "pier_side", _pier)
    monkeypatch.setattr(tel, "time_to_meridian_flip", _no_countdown)
    monkeypatch.setattr(sim_hub, "goto_and_center", _goto)
    e._wait_for_flip_point = _hold

    if stub_autofocus:
        async def _af(label, **kw):
            st["af"].append(label)
            return True
        e._autofocus = _af

    guider = _FakeGuider()
    sim_hub.guider = guider
    st["guider"] = guider
    return e, t, st


# ---------------------------------------------------------------- 1. the no-op

class TestAFlipThatDidNotFlipPaysForNothing:
    async def test_the_calibration_survives_a_re_slew_that_did_not_flip(
            self, sim_hub, monkeypatch):
        """4.7 MINUTES, 2026-09-08 00:25. The lead-time GoTo left the mount
        exactly where it was and the calibration for that side was still
        perfectly valid - and it was thrown away and re-walked anyway."""
        e, t, st = _flip_engine(sim_hub, monkeypatch, the_slew_flips=False)
        await e._maybe_meridian_flip(t, next_exposure_s=180.0)
        g = st["guider"]
        assert st["gotos"], "premise: the flip must have re-slewed"
        assert "flip_calibration" not in g.calls, (
            f"the re-slew did not change the pier side and the calibration was "
            f"discarded anyway: {g.calls}")
        assert await g.needs_calibration() is False, (
            "the restart will walk a fresh calibration for a side it never left")

    async def test_guiding_still_restarts(self, sim_hub, monkeypatch):
        """The control that keeps the fix from becoming a worse bug: not
        flipping must not mean not guiding."""
        e, t, st = _flip_engine(sim_hub, monkeypatch, the_slew_flips=False)
        await e._maybe_meridian_flip(t, next_exposure_s=180.0)
        assert st["guider"].calls == ["stop", "start"], st["guider"].calls

    async def test_the_post_flip_autofocus_does_not_run(self, sim_hub,
                                                        monkeypatch):
        """7.5 minutes at 24 s a point, after a slew that moved nothing."""
        e, t, st = _flip_engine(sim_hub, monkeypatch, the_slew_flips=False)
        await e._maybe_meridian_flip(t, next_exposure_s=180.0)
        assert st["af"] == [], (
            f"a re-slew that changed nothing still bought a sweep: {st['af']}")

    async def test_the_flip_stays_armed_with_the_lead_dropped(
            self, sim_hub, monkeypatch):
        """Unchanged behaviour, asserted here because the cheaper no-op path
        must not accidentally spend the crossing's one flip."""
        e, t, st = _flip_engine(sim_hub, monkeypatch, the_slew_flips=False)
        await e._maybe_meridian_flip(t, next_exposure_s=180.0)
        assert e._flip_armed is True
        assert e._flip_lead_s(t) == 0.0

    async def test_the_real_flip_that_follows_pays_in_full(self, sim_hub,
                                                           monkeypatch):
        """THE OTHER HALF. Cheapening the no-op must not cheapen the flip that
        actually happens: the second attempt, at the meridian, has to discard,
        recalibrate and focus exactly as it always did."""
        e, t, st = _flip_engine(sim_hub, monkeypatch, the_slew_flips=False)
        await e._maybe_meridian_flip(t, next_exposure_s=180.0)
        assert e._flip_armed is True, "premise: the retry must still be owed"
        g = st["guider"]
        g.calls.clear()
        st["af"].clear()
        # ...and this time the mount really swaps sides.
        st["flips"] = True
        await e._maybe_meridian_flip(t, next_exposure_s=100000.0)
        assert "flip_calibration" in g.calls, (
            f"the REAL flip reused a calibration measured on the other side of "
            f"the pier - that is the runaway: {g.calls}")
        assert await g.needs_calibration() is True
        assert st["af"] == ["post-flip autofocus"], st["af"]
        assert e._flip_armed is False

    async def test_an_unreadable_pier_side_keeps_the_conservative_behaviour(
            self, sim_hub, monkeypatch):
        """Two failures to READ are not evidence that nothing moved. A mount
        that will not answer must still get the discard and the sweep."""
        e, t, st = _flip_engine(sim_hub, monkeypatch, the_slew_flips=False)

        async def _blind():
            raise RuntimeError("pier side unreadable")

        monkeypatch.setattr(sim_hub.devices["telescope"], "pier_side", _blind)
        await e._maybe_meridian_flip(t, next_exposure_s=180.0)
        assert "flip_calibration" in st["guider"].calls, st["guider"].calls
        assert st["af"] == ["post-flip autofocus"], st["af"]
        assert e._flip_armed is False

    async def test_the_hub_says_whether_anything_flipped(self, sim_hub,
                                                         monkeypatch):
        """The engine reads `flipped` off the flip's own result, so the flip
        has to report it - and report it from the reads it took either side of
        its own slew, not from the caller's."""
        _e, _t, st = _flip_engine(sim_hub, monkeypatch, the_slew_flips=False)
        res = await sim_hub.meridian_flip(3.0, 60.0)
        assert res["flipped"] is False
        assert res["pier_side_before"] == "west" == res["pier_side_after"]
        st["flips"] = True
        res = await sim_hub.meridian_flip(3.0, 60.0)
        assert res["flipped"] is True
        assert res["pier_side_before"] != res["pier_side_after"]
        # ...and it still carries what goto_and_center returned.
        assert res["centered"] is True

    async def test_the_engine_believes_the_flips_own_answer(self, sim_hub,
                                                            monkeypatch):
        """WHERE THE ANSWER COMES FROM. The flip reads the side from INSIDE
        itself, between the slew and the guider restart; the engine's own
        before/after pair is a cross-check taken from further away. When the
        two disagree the engine takes "nothing flipped" - the cheap answer -
        and says so, rather than silently preferring its own reading.
        """
        from astrodeck.events import bus
        e, t, st = _flip_engine(sim_hub, monkeypatch, the_slew_flips=True)

        async def _flip(ra_h, dec_d):
            # the mount really did swap sides, and the flip's own reads missed it
            st["side"] = "east" if st["side"] == "west" else "west"
            return {"centered": True, "flipped": False}

        monkeypatch.setattr(sim_hub, "meridian_flip", _flip)
        q = bus.subscribe()
        try:
            await e._maybe_meridian_flip(t, next_exposure_s=180.0)
            msgs: list[str] = []
            while not q.empty():
                ev = q.get_nowait()
                if ev.type == "log":
                    msgs.append(str((ev.data or {}).get("message", "")))
        finally:
            bus.unsubscribe(q)
        assert st["af"] == [], (
            "the engine ran the post-flip programme past its own flip saying "
            "nothing moved")
        assert e._flip_armed is True, "the crossing's one flip was spent anyway"
        assert any("says the opposite" in m for m in msgs), msgs


# ------------------------------------------------- 2. the sweep through L

class TestTheSweepGoesThroughLuminance:
    @staticmethod
    def _mark_narrowband(sim_hub):
        fw = sim_hub.devices["filterwheel"]
        # sim wheel: L R G B Ha OIII SII Dark, offsets 0/12/10/15/120/110/115/0
        fw.filter_narrowband = [False] * 4 + [True] * 3 + [False]
        return fw

    @pytest.fixture
    def sweeps(self, monkeypatch, sim_hub):
        """Record the slot and focuser position the sweep actually ran at."""
        seen: list[dict] = []
        fw = sim_hub.devices["filterwheel"]
        foc = sim_hub.devices["focuser"]

        async def _fake(cam, focuser, **kw):
            seen.append({"slot": await fw.get_position(),
                         "focus": await foc.get_position(),
                         "exposure_s": kw.get("exposure_s"),
                         "gain": kw.get("gain")})
            return _Result()

        monkeypatch.setattr(engine_mod, "run_autofocus", _fake)
        return seen

    async def test_the_post_flip_sweep_runs_through_luminance(
            self, sim_hub, monkeypatch, sweeps):
        """24 s a point through Ha, twice in twelve minutes, for a sweep that
        reads 1277 stars through L and 34 through narrowband."""
        fw = self._mark_narrowband(sim_hub)
        await fw.set_position(4)                    # Ha
        e, t, st = _flip_engine(sim_hub, monkeypatch, the_slew_flips=True,
                                offsets=True, stub_autofocus=False)
        await e._maybe_meridian_flip(t, next_exposure_s=180.0)
        assert sweeps, "the post-flip sweep did not run at all"
        assert sweeps[0]["slot"] == 0, (
            f"the post-flip sweep still ran through the narrowband filter in "
            f"the beam: slot {sweeps[0]['slot']}")

    async def test_the_offset_is_applied_out_and_undone_coming_back(
            self, sim_hub, monkeypatch, sweeps):
        """THE REASON THIS IS ONLY LEGAL WITH OFFSETS ON. Ha sits 120 steps from
        L on this wheel, so the sweep must be measured 120 steps away and the
        filter change must put those 120 steps back."""
        fw = self._mark_narrowband(sim_hub)
        foc = sim_hub.devices["focuser"]
        await fw.set_position(4)
        before = await foc.get_position()
        e, t, st = _flip_engine(sim_hub, monkeypatch, the_slew_flips=True,
                                offsets=True, stub_autofocus=False)
        await e._maybe_meridian_flip(t, next_exposure_s=180.0)
        assert sweeps[0]["focus"] == before - 120, (
            f"the wheel moved to L without applying the offset: focuser at "
            f"{sweeps[0]['focus']}, was {before}")
        assert await fw.get_position() == 4, "the wheel was left on luminance"
        assert await foc.get_position() == before, (
            "the offset was applied on the way out and not undone coming back")

    async def test_without_the_offsets_the_sweep_stays_where_it_is(
            self, sim_hub, monkeypatch, sweeps):
        """A focus found through L is the WRONG focus for Ha unless something
        puts the offset back. With `apply_filter_offsets` off, nothing does -
        so the sweep stays in the beam it is in and pays the 4x exposure."""
        fw = self._mark_narrowband(sim_hub)
        await fw.set_position(4)
        e, t, st = _flip_engine(sim_hub, monkeypatch, the_slew_flips=True,
                                offsets=False, stub_autofocus=False)
        await e._maybe_meridian_flip(t, next_exposure_s=180.0)
        assert sweeps[0]["slot"] == 4, (
            "the sweep moved to luminance with no offset to bring the focus "
            "back for Ha")

    async def test_a_wheel_with_no_measured_offset_stays_where_it_is(
            self, sim_hub, monkeypatch, sweeps):
        """Offsets ON but never LEARNED is the same hazard wearing a tick box:
        an all-zero (or short) offset list cannot bring the focus back."""
        fw = self._mark_narrowband(sim_hub)
        fw.filter_offsets = []
        await fw.set_position(4)
        e, t, st = _flip_engine(sim_hub, monkeypatch, the_slew_flips=True,
                                offsets=True, stub_autofocus=False)
        await e._maybe_meridian_flip(t, next_exposure_s=180.0)
        assert sweeps[0]["slot"] == 4, sweeps[0]

    async def test_a_broadband_slot_is_left_alone(self, sim_hub, monkeypatch,
                                                  sweeps):
        """The positive control: R already passes broad light, so buying two
        wheel moves and two focuser moves for it would be pure cost.
        `solve_filter_slot` is the rule, and it says stay."""
        self._mark_narrowband(sim_hub)
        fw = sim_hub.devices["filterwheel"]
        await fw.set_position(1)                    # R
        e, t, st = _flip_engine(sim_hub, monkeypatch, the_slew_flips=True,
                                offsets=True, stub_autofocus=False)
        await e._maybe_meridian_flip(t, next_exposure_s=180.0)
        assert sweeps[0]["slot"] == 1, sweeps[0]

    async def test_the_swap_is_announced(self, sim_hub, monkeypatch, sweeps):
        """A sweep that silently changes what it is looking through is a sweep
        nobody can diagnose in the morning."""
        from astrodeck.events import bus
        fw = self._mark_narrowband(sim_hub)
        await fw.set_position(4)
        e, t, st = _flip_engine(sim_hub, monkeypatch, the_slew_flips=True,
                                offsets=True, stub_autofocus=False)
        q = bus.subscribe()
        try:
            await e._maybe_meridian_flip(t, next_exposure_s=180.0)
            msgs = []
            while not q.empty():
                ev = q.get_nowait()
                if ev.type == "log":
                    msgs.append(str((ev.data or {}).get("message", "")))
        finally:
            bus.unsubscribe(q)
        assert any("sweeping through 'L'" in m for m in msgs), msgs

    async def test_a_failed_sweep_still_puts_the_filter_back(
            self, sim_hub, monkeypatch):
        """The restore is in a `finally` for this: a sweep that fails - which is
        the common narrowband case - must not leave the run shooting Ha frames
        through L at L's focus."""
        fw = self._mark_narrowband(sim_hub)
        foc = sim_hub.devices["focuser"]
        await fw.set_position(4)
        before = await foc.get_position()

        async def _boom(cam, focuser, **kw):
            raise RuntimeError("sweep exploded")

        monkeypatch.setattr(engine_mod, "run_autofocus", _boom)
        e = SequenceEngine(sim_hub)
        e.plan = SequencePlan(apply_filter_offsets=True)
        await e._autofocus("initial autofocus")
        assert await fw.get_position() == 4
        assert await foc.get_position() == before


# -------------------------------------------- 3. the sweep the flip does not need

class TestAFreshFocusIsNotReFound:
    async def test_a_fresh_cold_focus_skips_the_post_flip_sweep(
            self, sim_hub, monkeypatch):
        """Eight minutes, 2026-09-08 00:37, thirty minutes after a temperature
        refocus with no temperature change. A flip does not move the focuser."""
        e, t, st = _flip_engine(sim_hub, monkeypatch, the_slew_flips=True)
        e._last_focus_at = time.monotonic() - 300.0
        await e._maybe_meridian_flip(t, next_exposure_s=180.0)
        assert st["af"] == [], st["af"]

    async def test_a_stale_focus_still_sweeps(self, sim_hub, monkeypatch):
        e, t, st = _flip_engine(sim_hub, monkeypatch, the_slew_flips=True)
        e._last_focus_at = time.monotonic() - (FRESH_FOCUS_S + 60.0)
        await e._maybe_meridian_flip(t, next_exposure_s=180.0)
        assert st["af"] == ["post-flip autofocus"], st["af"]

    async def test_a_temperature_that_moved_still_sweeps(self, sim_hub,
                                                         monkeypatch):
        """The sim focuser reads 4.2 C; the last focus was taken at 0.0 with a
        1 C refocus delta set, so the focus is fresh and WRONG."""
        e, t, st = _flip_engine(sim_hub, monkeypatch, the_slew_flips=True,
                                temp_delta_c=1.0)
        e._last_focus_at = time.monotonic() - 60.0
        e._last_focus_temp = 0.0
        await e._maybe_meridian_flip(t, next_exposure_s=180.0)
        assert st["af"] == ["post-flip autofocus"], st["af"]

    async def test_a_temperature_that_held_skips(self, sim_hub, monkeypatch):
        """...and the control, with the same delta armed."""
        e, t, st = _flip_engine(sim_hub, monkeypatch, the_slew_flips=True,
                                temp_delta_c=1.0)
        e._last_focus_at = time.monotonic() - 60.0
        e._last_focus_temp = await sim_hub.devices["focuser"].get_temperature()
        await e._maybe_meridian_flip(t, next_exposure_s=180.0)
        assert st["af"] == [], st["af"]

    async def test_a_run_that_has_never_focused_sweeps(self, sim_hub,
                                                       monkeypatch):
        e, t, st = _flip_engine(sim_hub, monkeypatch, the_slew_flips=True)
        assert e._last_focus_at is None, "premise"
        await e._maybe_meridian_flip(t, next_exposure_s=180.0)
        assert st["af"] == ["post-flip autofocus"], st["af"]

    async def test_a_failed_last_sweep_sweeps_again(self, sim_hub, monkeypatch):
        """A run standing on a focus that was never found has nothing to stand
        on, however recently it tried."""
        e, t, st = _flip_engine(sim_hub, monkeypatch, the_slew_flips=True)
        e._last_focus_at = None            # what a failed sweep leaves behind
        await e._maybe_meridian_flip(t, next_exposure_s=180.0)
        assert st["af"] == ["post-flip autofocus"], st["af"]

    async def test_a_failed_sweep_is_what_clears_the_stamp(self, sim_hub,
                                                           monkeypatch):
        """...and the two halves are connected: it is `_autofocus` itself that
        clears the stamp when it does not find focus."""
        class _Failed:
            success = False
            message = "not_enough_spread"
            position = 0

        calls = {"n": 0}

        async def _fake(cam, foc, **kw):
            calls["n"] += 1
            return _Result() if calls["n"] == 1 else _Failed()

        monkeypatch.setattr(engine_mod, "run_autofocus", _fake)
        e = SequenceEngine(sim_hub)
        e.plan = SequencePlan()
        assert await e._autofocus("initial autofocus") is True
        assert e._last_focus_at is not None
        assert await e._autofocus("refocus") is False
        assert e._last_focus_at is None

    async def test_only_the_post_flip_sweep_is_gated(self, sim_hub, monkeypatch):
        """THE SCOPE OF THE SKIP. A refocus the operator's own rules asked for -
        frames since focus, a temperature drift, an instruction - is a decision
        already taken, and freshness must not quietly overrule it."""
        seen = []

        async def _fake(cam, foc, **kw):
            seen.append(kw)
            return _Result()

        monkeypatch.setattr(engine_mod, "run_autofocus", _fake)
        e = SequenceEngine(sim_hub)
        e.plan = SequencePlan()
        e._last_focus_at = time.monotonic()      # as fresh as it gets
        await e._autofocus("refocus")
        assert seen, "a freshness gate meant for the flip suppressed a refocus"


# ------------------------------------------- 4. the centring the sweep spends

class _Clock:
    """Fakes only `monotonic`; everything else is the real `time` module."""

    def __init__(self, real):
        self._real = real
        self.t = real.monotonic()

    def monotonic(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s

    def __getattr__(self, name):
        return getattr(self._real, name)


class TestTheCentringSurvivesTheSweep:
    def _target(self):
        return Target(name="NGC 7331", ra_hours=22.6, dec_deg=34.4,
                      center=True, autofocus_first=True,
                      steps=[ExposureStep(filter="L", exposure_s=0.05,
                                          count=1)])

    def _engine(self, sim_hub, monkeypatch, *, sweep_s: float,
                goto_fails_on: int | None = None):
        clock = _Clock(time)
        monkeypatch.setattr(engine_mod, "time", clock)
        t = self._target()
        e = SequenceEngine(sim_hub)
        e.plan = SequencePlan(targets=[t], meridian_flip=False, guide=False)
        e._cfg = None
        e._safety_gate = _noop_gate
        sim_hub.guider = None               # the sweep is unguided, as it is today
        st = {"gotos": []}

        async def _goto(ra_h, dec_d, **kw):
            st["gotos"].append((ra_h, dec_d))
            if goto_fails_on is not None and len(st["gotos"]) == goto_fails_on:
                raise RuntimeError("solve failed")
            return {"centered": True, "error_arcmin": 0.08}

        async def _af(label, **kw):
            clock.advance(sweep_s)
            return True

        monkeypatch.setattr(sim_hub, "goto_and_center", _goto)
        e._autofocus = _af
        return e, t, st

    async def test_a_long_unguided_sweep_is_followed_by_a_re_centre(
            self, sim_hub, monkeypatch):
        """2026-09-07: centred to 0.08' at 22:12, first light 2.0' off at 22:21.
        The sweep sits in that gap with nothing holding the field."""
        e, t, st = self._engine(sim_hub, monkeypatch, sweep_s=150.0)
        await e._setup_target(0, t)
        assert len(st["gotos"]) == 2, (
            f"the field drifted through a 2.5 min unguided sweep and guiding "
            f"started wherever it landed: {st['gotos']}")

    async def test_a_quick_sweep_does_not_buy_a_re_slew(self, sim_hub,
                                                        monkeypatch):
        """The control. A re-slew per target that cost more than the drift it
        corrects would be a worse bug than the one being fixed."""
        e, t, st = self._engine(sim_hub, monkeypatch,
                                sweep_s=RECENTRE_AFTER_UNGUIDED_S / 2)
        await e._setup_target(0, t)
        assert len(st["gotos"]) == 1, st["gotos"]

    async def test_a_failed_re_centre_does_not_end_the_target(
            self, sim_hub, monkeypatch):
        """Non-fatal like the resume path's re-centre: a failed one leaves the
        mount exactly where it would have been without this at all."""
        e, t, st = self._engine(sim_hub, monkeypatch, sweep_s=150.0,
                                goto_fails_on=2)
        await e._setup_target(0, t)          # must not raise
        assert len(st["gotos"]) == 2

    async def test_a_target_that_opted_out_of_centring_still_does(
            self, sim_hub, monkeypatch):
        """`target.center` is the existing statement of intent and this path
        honours it exactly as the recovery and re-lock re-centres do."""
        e, t, st = self._engine(sim_hub, monkeypatch, sweep_s=150.0)
        t.center = False
        await e._setup_target(0, t)
        assert st["gotos"] == [], st["gotos"]

    async def test_a_field_guided_through_the_sweep_is_not_re_centred(
            self, sim_hub, monkeypatch):
        """It never is today - guiding starts after the sweep - and that is
        exactly why the condition is written down rather than assumed: the day
        the order changes, this must not cost a slew per target."""
        e, t, st = self._engine(sim_hub, monkeypatch, sweep_s=150.0)
        sim_hub.guider = _FakeGuider(active=True)
        await e._setup_target(0, t)
        assert len(st["gotos"]) == 1, st["gotos"]
