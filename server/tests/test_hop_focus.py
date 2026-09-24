"""A HOP DOES NOT MOVE THE FOCUSER, SO A MOSAIC PANEL REUSES A GOOD FOCUS
(#189 U-05, spec 5.6 step 6, A.3, risk 15, section 8 S1 item 5).

Every target with ``autofocus_first`` sweeps at every setup, 7-9 minutes on
this rig, and a mosaic hops between panels all night. The first rule drafted
for hops copied `_post_flip_focus_is_owed`, whose 30-minute age rule fires
BEFORE it looks at the temperature. A.3 computed what that costs a rotating
mosaic: one visit plus its hop is about 16.7 min, so an 8-minute sweep falls
due every second hop, 19.4% of the night.

`_hop_focus_is_owed` has no age rule. It applies only to a target with
``autofocus_skip_if_fresh``, and a sweep is owed when:

* no sweep has succeeded this run, or the last one failed
  (``_last_focus_at`` is None);
* `_refocus_due()` is true now (``autofocus_every`` reached, or the armed
  temperature delta exceeded), so the hop sweeps exactly when the frame loop
  would have refocused anyway;
* or this is the first acquisition this run of the target's group, keyed on
  ``mosaic_group`` or, for a target with none, its own id, and forgotten at
  `start()`.

A skip is logged as "focus reused: swept N min ago, X C since".

THE CLOCK IS VIRTUAL, THE REST IS THE SIMULATOR. `engine.time` is a proxy
whose ``monotonic`` only moves when a test or the stubbed sweep moves it, so
"35 minutes after a good sweep" is exact. The rig is the real sim hub: the
focuser temperature, the real `_autofocus` (which stamps and clears
``_last_focus_at`` and re-anchors the temperature baseline), the real
`_refocus_due` and the real `_setup_target`. Only the sweep's measurement
(`run_autofocus`), the centring (`goto_and_center`, because the sim slew
dwells in real time, #207), the safety gate and the sun cone are stubbed.
"""
from __future__ import annotations

import asyncio
import time as _realtime

import pytest

from _simhub import sim_hub  # noqa: F401 (fixture import)

import astrodeck.sequence.engine as engine_mod
from astrodeck.events import bus
from astrodeck.sequence import ExposureStep, SequenceEngine, SequencePlan, Target
from astrodeck.sequence.engine import FRESH_FOCUS_S, RECENTRE_AFTER_UNGUIDED_S

#: A.3's sweep: 8 minutes. Longer than RECENTRE_AFTER_UNGUIDED_S, so every
#: sweep here also earns the re-centre that follows an unguided sweep.
SWEEP_S = 8 * 60.0
#: The spec's hop: 35 minutes after a good sweep, past the 30-minute age rule
#: the post-flip gate uses.
HOP_35_MIN_S = 35 * 60.0
DELTA_C = 1.0

assert SWEEP_S > RECENTRE_AFTER_UNGUIDED_S, "premise: a sweep earns a re-centre"
assert HOP_35_MIN_S > FRESH_FOCUS_S, "premise: the 35 min hop is past the age rule"


class _Clock:
    """Stand-in for the ``time`` module inside the engine: ``monotonic`` is
    virtual, everything else is the real module."""

    def __init__(self) -> None:
        self.mono = 10_000.0

    def monotonic(self) -> float:
        return self.mono

    def advance(self, s: float) -> None:
        self.mono += s

    def __getattr__(self, name):
        return getattr(_realtime, name)


class _Found:
    success = True
    message = ""
    position = 11218


class _NotFound:
    success = False
    message = "not_enough_spread"
    position = 0


async def _noop_gate(context="", target=None):
    return None


def _target(name: str, group: str | None = "M31 mosaic", *,
            skip: bool = True) -> Target:
    return Target(name=name, ra_hours=0.71, dec_deg=41.27, center=True,
                  autofocus_first=True, autofocus_skip_if_fresh=skip,
                  mosaic_group=group,
                  steps=[ExposureStep(filter="L", exposure_s=0.05, count=1)])


class _Rig:
    """The sim hub with a virtual clock and a counting sweep."""

    def __init__(self, sim_hub, monkeypatch, targets, *,
                 temp_delta_c: float = DELTA_C, autofocus_every: int = 0,
                 outcomes=()):
        self.clock = _Clock()
        monkeypatch.setattr(engine_mod, "time", self.clock)
        self.hub = sim_hub
        self.focuser = sim_hub.devices["focuser"]
        self.targets = list(targets)
        self.sweeps = 0
        self.gotos: list[tuple[float, float]] = []
        self.logs: list[tuple[str, str]] = []
        self._outcomes = list(outcomes)

        e = SequenceEngine(sim_hub)
        e.plan = SequencePlan(targets=self.targets, meridian_flip=False,
                              guide=False, refocus_on_temp_delta_c=temp_delta_c,
                              autofocus_every=autofocus_every)
        e._cfg = None
        e._safety_gate = _noop_gate
        self.engine = e
        sim_hub.guider = None          # every sweep is unguided, as it is today
        monkeypatch.setattr(sim_hub, "_check_solar", lambda *a, **kw: None)

        async def _goto(ra_h, dec_d, **kw):
            self.gotos.append((ra_h, dec_d))
            return {"centered": True, "error_arcmin": 0.1}

        async def _run_autofocus(cam, foc, **kw):
            self.clock.advance(SWEEP_S)
            self.sweeps += 1
            ok = self._outcomes.pop(0) if self._outcomes else True
            return _Found() if ok else _NotFound()

        monkeypatch.setattr(sim_hub, "goto_and_center", _goto)
        monkeypatch.setattr(engine_mod, "run_autofocus", _run_autofocus)
        monkeypatch.setattr(bus, "log",
                            lambda lvl, msg, src=None, **kw:
                            self.logs.append((lvl, msg)))

    async def hop(self, target: Target, after_s: float = 0.0) -> bool:
        """Acquire ``target`` ``after_s`` seconds from now; True if it swept."""
        self.clock.advance(after_s)
        before = self.sweeps
        await self.engine._setup_target(self.targets.index(target), target)
        return self.sweeps > before

    def reused(self) -> list[str]:
        return [m for _lvl, m in self.logs if "focus reused" in m]


# ------------------------------------------------ the hop that owes nothing


class TestAHopReusesAGoodFocus:
    async def test_a_hop_35_minutes_after_a_good_sweep_reuses_it(
            self, sim_hub, monkeypatch):
        """THE SPEC'S CASE. A sweeps (the group's first acquisition), B of the
        same mosaic is acquired 35 minutes after that sweep ended, with the
        temperature delta armed and the focuser exactly where it was.

        MUTATION "30-minute age rule" (`_post_flip_focus_is_owed`'s
        ``age_s >= FRESH_FOCUS_S`` clause added to `_hop_focus_is_owed`).
        Observed:
            AssertionError: B swept 35 min after a good sweep with the
            temperature unmoved inside a 1.0 C delta: a hop does not move the
            focuser (sweeps 2)

        MUTATION "always sweep" (`_hop_focus_is_owed` returns True). This is
        also what the engine did before the rule existed: the flag was read
        by nothing. Observed:
            AssertionError: B swept 35 min after a good sweep with the
            temperature unmoved inside a 1.0 C delta: a hop does not move the
            focuser (sweeps 2)

        MUTATION "key on target id only" (``mosaic_group`` ignored, so every
        panel is its own group). Observed: the same line, (sweeps 2).
        """
        a, b = _target("M31 1-1"), _target("M31 1-2")
        rig = _Rig(sim_hub, monkeypatch, [a, b])
        assert await rig.hop(a) is True, "premise: the first panel sweeps"
        swept_b = await rig.hop(b, after_s=HOP_35_MIN_S)
        assert swept_b is False, (
            f"B swept 35 min after a good sweep with the temperature unmoved "
            f"inside a {DELTA_C} C delta: a hop does not move the focuser "
            f"(sweeps {rig.sweeps})")
        assert rig.reused() == [
            "M31 1-2: focus reused: swept 35 min ago, 0.0 C since"], rig.logs

    async def test_no_age_rule_even_hours_later_with_no_trigger_armed(
            self, sim_hub, monkeypatch):
        """RISK 15, WRITTEN DOWN. With neither ``autofocus_every`` nor a
        temperature delta armed, a mosaic focuses once per run, which is what
        one long single target does on that rig today. Three hours on.

        MUTATION "30-minute age rule". Observed:
            AssertionError: B swept three hours after a good sweep with no
            refocus trigger armed: a hop has no age rule (sweeps 2)
        """
        a, b = _target("M31 1-1"), _target("M31 1-2")
        rig = _Rig(sim_hub, monkeypatch, [a, b], temp_delta_c=0.0)
        assert await rig.hop(a) is True, "premise: the first panel sweeps"
        assert await rig.hop(b, after_s=3 * 3600.0) is False, (
            f"B swept three hours after a good sweep with no refocus trigger "
            f"armed: a hop has no age rule (sweeps {rig.sweeps})")
        assert rig.reused() == [
            "M31 1-2: focus reused: swept 180 min ago, 0.0 C since"], rig.logs

    async def test_a_temperature_move_inside_the_delta_still_reuses(
            self, sim_hub, monkeypatch):
        """CONTROL for the temperature clause: 0.4 C inside a 1.0 C delta is
        the frame loop's own "not yet", and the log says how far it moved.

        MUTATION "always sweep". Observed:
            AssertionError: B swept with the focuser 0.4 C from its last
            focus, inside the 1.0 C delta (sweeps 2)
        """
        a, b = _target("M31 1-1"), _target("M31 1-2")
        rig = _Rig(sim_hub, monkeypatch, [a, b])
        assert await rig.hop(a) is True
        t0 = await rig.focuser.get_temperature()
        rig.focuser.set_temperature(t0 - 0.4)
        assert await rig.hop(b, after_s=14 * 60.0) is False, (
            f"B swept with the focuser 0.4 C from its last focus, inside the "
            f"{DELTA_C} C delta (sweeps {rig.sweeps})")
        assert rig.reused() == [
            "M31 1-2: focus reused: swept 14 min ago, 0.4 C since"], rig.logs


# ------------------------------------------------ the hop that owes a sweep


class TestAHopSweepsWhenFocusIsOwed:
    async def test_a_temperature_move_past_the_delta_sweeps(
            self, sim_hub, monkeypatch):
        """The focus is five minutes old and wrong: the tube cooled 1.5 C
        against a 1.0 C delta, which the frame loop would have refocused on.

        MUTATION "refocus_due not consulted" (the `_refocus_due()` clause
        dropped from `_hop_focus_is_owed`). Observed:
            AssertionError: the focuser cooled 1.5 C past a 1.0 C delta and
            the hop reused the focus
        """
        a, b = _target("M31 1-1"), _target("M31 1-2")
        rig = _Rig(sim_hub, monkeypatch, [a, b])
        assert await rig.hop(a) is True
        t0 = await rig.focuser.get_temperature()
        rig.focuser.set_temperature(t0 - 1.5)
        assert await rig.hop(b, after_s=5 * 60.0) is True, (
            "the focuser cooled 1.5 C past a 1.0 C delta and the hop reused "
            "the focus")
        assert rig.reused() == [], rig.logs

    async def test_autofocus_every_reached_sweeps(self, sim_hub, monkeypatch):
        """``autofocus_every`` = 3 and A banked 3 frames since its sweep. The
        frame loop checks `_refocus_due` BEFORE a frame, so a panel that ended
        on the third frame leaves the count due for whoever is next.

        MUTATION "refocus_due not consulted". Observed:
            AssertionError: autofocus_every was reached and the hop reused
            the focus
        """
        a, b = _target("M31 1-1"), _target("M31 1-2")
        rig = _Rig(sim_hub, monkeypatch, [a, b], autofocus_every=3)
        assert await rig.hop(a) is True
        rig.engine._frames_since_focus = 3   # what A's frame loop leaves
        assert await rig.hop(b, after_s=5 * 60.0) is True, (
            "autofocus_every was reached and the hop reused the focus")

    async def test_control_autofocus_every_not_reached_reuses(
            self, sim_hub, monkeypatch):
        """CONTROL. Two frames of three: not due, so reused.

        MUTATION "always sweep". Observed:
            AssertionError: B swept with 2 of autofocus_every's 3 frames
            banked since the last sweep (sweeps 2)
        """
        a, b = _target("M31 1-1"), _target("M31 1-2")
        rig = _Rig(sim_hub, monkeypatch, [a, b], autofocus_every=3)
        assert await rig.hop(a) is True
        rig.engine._frames_since_focus = 2
        assert await rig.hop(b, after_s=5 * 60.0) is False, (
            f"B swept with 2 of autofocus_every's 3 frames banked since the "
            f"last sweep (sweeps {rig.sweeps})")

    async def test_the_groups_first_acquisition_sweeps(self, sim_hub,
                                                       monkeypatch):
        """A second mosaic's first panel sweeps, five minutes after the first
        mosaic's good sweep and with nothing else owing one. Its panel after
        that reuses.

        MUTATION "no first-acquisition rule" (the group clause dropped).
        Observed:
            AssertionError: the first acquisition of the M33 mosaic reused
            M31's focus

        MUTATION "key on target id only". Observed:
            AssertionError: M33's second panel swept 5 min after M33's own
            good sweep (sweeps 3)
        """
        a = _target("M31 1-1", "M31 mosaic")
        c, d = _target("M33 1-1", "M33 mosaic"), _target("M33 1-2", "M33 mosaic")
        rig = _Rig(sim_hub, monkeypatch, [a, c, d])
        assert await rig.hop(a) is True
        assert await rig.hop(c, after_s=5 * 60.0) is True, (
            "the first acquisition of the M33 mosaic reused M31's focus")
        assert await rig.hop(d, after_s=5 * 60.0) is False, (
            f"M33's second panel swept 5 min after M33's own good sweep "
            f"(sweeps {rig.sweeps})")

    async def test_a_target_with_no_group_is_its_own_group(self, sim_hub,
                                                           monkeypatch):
        """Two single targets with the flag and no ``mosaic_group``: each one's
        first acquisition sweeps, because the key falls back to the target's
        own id. The first one re-acquired later (a cloud hold returns through
        `_setup_target`) is not a first acquisition and reuses.

        MUTATION "key on mosaic_group only" (a target with no group keys as
        None, so every ungrouped target shares one key). Observed:
            AssertionError: NGC 891's first acquisition reused NGC 7331's
            focus

        MUTATION "always sweep". Observed:
            AssertionError: NGC 7331, re-acquired 5 min after a good sweep,
            swept again: it is not a first acquisition (sweeps 3)
        """
        x, y = _target("NGC 7331", None), _target("NGC 891", None)
        rig = _Rig(sim_hub, monkeypatch, [x, y])
        assert await rig.hop(x) is True
        assert await rig.hop(y, after_s=5 * 60.0) is True, (
            "NGC 891's first acquisition reused NGC 7331's focus")
        assert await rig.hop(x, after_s=5 * 60.0) is False, (
            f"NGC 7331, re-acquired 5 min after a good sweep, swept again: it "
            f"is not a first acquisition (sweeps {rig.sweeps})")

    async def test_a_failed_sweep_is_swept_again(self, sim_hub, monkeypatch):
        """A's sweep did not find focus. B, five minutes later and of the same
        mosaic, has nothing to stand on however recent the attempt was.

        MUTATION "a failed sweep stamps the clock" (`_autofocus`'s failure
        branch sets ``_last_focus_at = time.monotonic()`` instead of None).
        Observed:
            AssertionError: the last sweep failed and the hop reused a focus
            never found

        MUTATION "no-good-sweep clause dropped" (the ``_last_focus_at is
        None`` clause removed from `_hop_focus_is_owed`). Observed, a crash
        at ``age_s = time.monotonic() - self._last_focus_at``, because the
        skip's own log line needs the stamp the clause guards:
            TypeError: unsupported operand type(s) for -: 'float' and
            'NoneType'
        """
        a, b = _target("M31 1-1"), _target("M31 1-2")
        rig = _Rig(sim_hub, monkeypatch, [a, b], outcomes=[False, True])
        assert await rig.hop(a) is True
        # The premise is read from what `_autofocus` SAID, not from the stamp,
        # so the stamp mutant above is caught by the behaviour below.
        assert ("warning", "initial autofocus failed: not_enough_spread") \
            in rig.logs, "premise: the sweep failed"
        assert await rig.hop(b, after_s=5 * 60.0) is True, (
            "the last sweep failed and the hop reused a focus never found")
        assert rig.reused() == [], rig.logs

    async def test_an_unreadable_temperature_decides_nothing(self, sim_hub,
                                                             monkeypatch):
        """The probe stops answering after A's sweep. `_refocus_due` takes no
        decision on a temperature it cannot read, so neither does the hop:
        it reuses, and says the temperature was not read. (The post-flip gate
        sweeps instead; that gate is left as it is.)

        MUTATION "an unreadable temperature owes a sweep" (the post-flip
        gate's ``t is None`` -> sweep copied into `_hop_focus_is_owed`).
        Observed:
            AssertionError: B swept because the temperature could not be
            read, which decides nothing in _refocus_due (sweeps 2)
        """
        a, b = _target("M31 1-1"), _target("M31 1-2")
        rig = _Rig(sim_hub, monkeypatch, [a, b])
        assert await rig.hop(a) is True
        assert rig.engine._last_focus_temp is not None, "premise: read at A"

        async def _no_probe():
            raise RuntimeError("temperature probe not answering")

        monkeypatch.setattr(rig.focuser, "get_temperature", _no_probe)
        assert await rig.hop(b, after_s=5 * 60.0) is False, (
            f"B swept because the temperature could not be read, which "
            f"decides nothing in _refocus_due (sweeps {rig.sweeps})")
        assert rig.reused() == [
            "M31 1-2: focus reused: swept 5 min ago, the focuser temperature "
            "could not be read"], rig.logs

    @pytest.mark.parametrize("delta_c", [DELTA_C, 0.0],
                             ids=["delta-armed", "delta-off"])
    async def test_a_sweep_with_no_temperature_is_not_reported_as_unmoved(
            self, sim_hub, monkeypatch, delta_c):
        """The probe answered nothing while A was swept (the Alpaca focuser
        returns None on a DeviceError) and answers again by B. The hop still
        reuses: with the delta armed, `_refocus_due` seeds its baseline from
        the reading it makes at B, which is its own rule and decides nothing.
        But the log may not report a temperature change it never measured.

        MUTATION "baseline read after _refocus_due" (the skip log compares
        against ``_last_focus_temp`` as it stands after `_refocus_due` has
        seeded it, the rule as first written). Observed, delta armed, the
        hop's own seed reported as the change since the sweep:
            AssertionError: ['M31 1-2: focus reused: swept 20 min ago, 0.0 C
            since']
        and delta off, where nothing seeds it, a probe that has just answered
        called unreadable:
            AssertionError: ['M31 1-2: focus reused: swept 20 min ago, the
            focuser temperature could not be read']

        MUTATION "snapshot after the seed" (only the ``baseline =`` line moved
        below the `_refocus_due` call). Delta armed, observed:
            AssertionError: ['M31 1-2: focus reused: swept 20 min ago, 0.0 C
            since']
        Delta off passes under it, because nothing seeds the baseline; that
        is why both cases are run.
        """
        a, b = _target("M31 1-1"), _target("M31 1-2")
        rig = _Rig(sim_hub, monkeypatch, [a, b], temp_delta_c=delta_c)
        real = rig.focuser.get_temperature
        probe = {"answers": False}

        async def _flaky():
            return await real() if probe["answers"] else None

        monkeypatch.setattr(rig.focuser, "get_temperature", _flaky)
        assert await rig.hop(a) is True
        assert rig.engine._last_focus_temp is None, (
            "premise: nothing was read at A's sweep")
        probe["answers"] = True
        assert await rig.hop(b, after_s=20 * 60.0) is False, (
            f"B swept: an unknown baseline decides nothing (sweeps "
            f"{rig.sweeps})")
        assert rig.reused() == [
            "M31 1-2: focus reused: swept 20 min ago, no focuser temperature "
            "was read at that sweep"], rig.reused()

    async def test_a_new_run_forgets_the_groups(self, sim_hub, monkeypatch):
        """`start()` resets the set. Run 2 focuses on the M33 mosaic first,
        then reaches the M31 mosaic, whose first acquisition this run must
        sweep although run 1 had acquired it and M33's focus is fresh.

        MUTATION "the set survives start()" (the reset line deleted).
        Observed:
            AssertionError: run 2's first acquisition of the M31 mosaic
            reused a focus, because run 1 had acquired it
        """
        a, c = _target("M31 1-1", "M31 mosaic"), _target("M33 1-1", "M33 mosaic")
        rig = _Rig(sim_hub, monkeypatch, [a, c])
        assert await rig.hop(a) is True
        monkeypatch.setattr(engine_mod.session_store, "load_all", lambda: [])
        monkeypatch.setattr(engine_mod.SequenceEngine, "_run",
                            lambda self: asyncio.sleep(0))
        e = rig.engine
        e.start(SequencePlan(targets=rig.targets, meridian_flip=False,
                             guide=False, refocus_on_temp_delta_c=DELTA_C))
        await asyncio.sleep(0)
        e._cfg = None
        e._safety_gate = _noop_gate
        assert await rig.hop(c, after_s=3600.0) is True, "premise"
        assert await rig.hop(a, after_s=5 * 60.0) is True, (
            "run 2's first acquisition of the M31 mosaic reused a focus, "
            "because run 1 had acquired it")


# ------------------------------------------------------------------ controls


class TestControls:
    async def test_a_target_without_the_flag_sweeps_at_every_setup(
            self, sim_hub, monkeypatch):
        """AS TODAY. Without ``autofocus_skip_if_fresh`` every setup sweeps,
        however fresh the focus and whatever the group.

        MUTATION "flag ignored" (every target takes the hop rule). Observed:
            AssertionError: a target that never asked to reuse focus reused
            it
        """
        a, b = _target("M31 1-1", skip=False), _target("M31 1-2", skip=False)
        rig = _Rig(sim_hub, monkeypatch, [a, b])
        assert await rig.hop(a) is True
        assert await rig.hop(b, after_s=60.0) is True, (
            "a target that never asked to reuse focus reused it")
        assert await rig.hop(a, after_s=60.0) is True
        assert rig.reused() == [], rig.logs

    async def test_the_re_centre_follows_only_a_sweep(self, sim_hub,
                                                      monkeypatch):
        """`_recentre_after_unguided_focus` answers the drift of an unguided
        sweep. A reused focus spent no time unguided, so it is not called:
        one centring for B, against A's centring and re-centre.

        MUTATION "the skip gates only the sweep call" (the re-centre left
        outside the owed branch, reached with the elapsed time of no sweep).
        The re-centre then declines on its own 60 s threshold, so the goto
        count alone would have stayed at 3 and passed; the spy is what sees
        it. Observed:
            AssertionError: the re-centre after an unguided sweep ran on a
            hop that did not sweep: ['M31 1-1', 'M31 1-2']
            assert ['M31 1-1', 'M31 1-2'] == ['M31 1-1']
        """
        a, b = _target("M31 1-1"), _target("M31 1-2")
        rig = _Rig(sim_hub, monkeypatch, [a, b])
        calls: list[str] = []
        real = rig.engine._recentre_after_unguided_focus

        async def _spy(target, elapsed_s, *, guided):
            calls.append(target.name)
            await real(target, elapsed_s, guided=guided)

        rig.engine._recentre_after_unguided_focus = _spy
        assert await rig.hop(a) is True
        assert calls == ["M31 1-1"] and len(rig.gotos) == 2, (calls, rig.gotos)
        assert await rig.hop(b, after_s=HOP_35_MIN_S) is False, (
            f"premise: B reuses the focus (sweeps {rig.sweeps})")
        # The goto count is asserted FIRST: under the mutant it passes, which
        # is the evidence that it could not have caught it alone.
        assert len(rig.gotos) == 3, rig.gotos
        assert calls == ["M31 1-1"], (
            f"the re-centre after an unguided sweep ran on a hop that did not "
            f"sweep: {calls}")
