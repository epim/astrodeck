# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""An accepted-mode cycle visit is bounded by ATTEMPTS (#147, spec 5.3).

`_run_step` has always said its visit was "BOUNDED BY ATTEMPTS", and in
accepted (quota) mode it was not: the reject branch `continue`d without
counting the frame toward `taken_this_visit`. One filter that kept rejecting
(a wrong focus offset, a field too sparse for `min_stars`) held the whole cycle
for up to `max_consecutive_rejects` subs per visit, every round, while every
other channel waited. With that guard off, the same visit ran until the night
guard's 20 consecutive rejects and ended the night although the other filters
were being accepted.

Two other things only worked BECAUSE of that defect, so the fix has three
parts and each has a case here:

1. a reject counts toward the visit, so a visit is `per_visit` attempts;
2. the per-step consecutive-reject counter lives on the engine, keyed like
   `_done`, and survives the visit; when it trips the step is set aside for
   the night (a local counter could never reach the threshold again in a
   one-attempt visit; since S2, #208, the set-aside is persisted with its
   night key, so a restart the same night keeps it and the next night does
   not);
3. the accepted-mode anti-spin compares exposures, not accepted frames (after
   part 1, one reject per step would otherwise take the target off the night).

EVERY CASE THAT GRADES A VISIT DRIVES THE REAL `_run_step` on the simulator
hub, with only the quality verdict scripted (per filter, the
`test_session_quota._script_gate` pattern). The fake `_run_step` in
test_cycle_acquisition would hide every line this change touches. The one
exception grades `_run_steps`' block-mode SELECTION, where a recording spy is
the observation itself and the real `_run_step` has its own case beside it.
Every case carries a night guard or a wall bound, so a mutant fails in finite
time instead of hanging the suite.

The plan is B, Ha and OIII with Ha in the MIDDLE, so "the other filters
interleave" is visible on both sides of it.

THE SIMULATOR'S FILTER WHEEL IS TAKEN OFF THE HUB here, and only for cost:
measured on this machine, nine frames took 10.5 s with it and 4.5 s without,
the difference being the wheel's 0.4 s per slot of simulated travel on every
visit. `_apply_filter` then returns at its "no wheel" check. It runs before
the frame loop and touches none of the counters under test; the verdict script
reads the step from `_active_step`, not from the wheel.
"""
from __future__ import annotations

import asyncio
import time

import pytest

import astrodeck.hub as hub_module
from astrodeck.config import config_store
from astrodeck.events import night_key
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target
from astrodeck.sequence.session import session_store

FILTERS = ("B", "Ha", "OIII")
TERMINAL = ("complete", "aborted", "error")


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    _safety = hub_module.config_store.cfg().safety
    monkeypatch.setattr(_safety, "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    # The wheel is still connected; it just is not registered, so nothing
    # moves it and disconnect_all() below does not reach it either.
    wheel = h.devices.pop("filterwheel", None)
    yield h
    await h.disconnect_all()
    if wheel is not None:
        await wheel.disconnect()


def _cycle_plan(counts, *, count_mode="accepted", per_visit=1,
                **plan_kw) -> SequencePlan:
    """One cycle target, one step per filter in FILTERS, `per_visit` 1 unless
    a case needs a longer visit.

    `counts` is a dict filter -> count, or one int for every filter."""
    if isinstance(counts, int):
        counts = {f: counts for f in FILTERS}
    return SequencePlan(
        name="visit-bound", guide=False, dither_every=0, autofocus_every=0,
        meridian_flip=False, count_mode=count_mode, **plan_kw,
        targets=[Target(
            name="M42", ra_hours=5.5881, dec_deg=-5.3911, center=False,
            autofocus_first=False, acquisition="cycle",
            steps=[ExposureStep(filter=f, exposure_s=0.05, count=counts[f],
                                per_visit=per_visit) for f in FILTERS])])


def _grade_by_filter(monkeypatch, rule):
    """Script the quality verdict per filter.

    ``rule(filter, n)`` answers for the n-th exposure (from 0) of that filter.
    Returns the list of ``(filter, verdict)`` in capture order: every science
    exposure `_run_step` takes passes through `_check_quality` exactly once,
    so this is the exposure sequence, rejects and discards included (an
    attempts-mode discard never reaches the ledger)."""
    shots: list[tuple[str, bool]] = []
    seen: dict[str, int] = {}

    def fake(self, info, *, record=True, calibration=False):
        ti, si = self._active_step
        f = self.plan.targets[ti].steps[si].filter
        n = seen.get(f, 0)
        seen[f] = n + 1
        verdict = bool(rule(f, n))
        shots.append((f, verdict))
        return verdict

    monkeypatch.setattr(SequenceEngine, "_check_quality", fake)
    return shots


async def _finish(eng: SequenceEngine, timeout: float) -> bool:
    """Wait for a terminal state. THE WALL BOUND: on timeout, abort the run
    (so the fixture can tear the hub down) and answer False."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if eng.state.get("state") in TERMINAL:
            return True
        await asyncio.sleep(0.05)
    await eng.abort()
    return False


async def _settled(eng: SequenceEngine, timeout: float = 30.0) -> None:
    """Let the run task finish its wind-down, so a second start() is legal."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while eng.running and loop.time() < deadline:
        await asyncio.sleep(0.05)
    assert not eng.running, "the first run never finished winding down"


def _ha_rounds(shots) -> list[int]:
    """Index of each Ha exposure in the capture sequence."""
    return [i for i, (f, _) in enumerate(shots) if f == "Ha"]


class TestAVisitIsBoundedByAttempts:
    async def test_a_rejecting_filter_gets_one_attempt_per_round(
            self, sim_hub, monkeypatch):
        """Ha always rejects, B and OIII always accept, `per_visit` 1.

        Rounds 1-3 are B, Ha, OIII: ONE Ha attempt each, the others
        interleaved. B and OIII finish at round 3; Ha then has the cycle to
        itself, one attempt per round, until its carried counter reaches
        `max_consecutive_rejects` (10) and it is set aside. The night guard
        (20) is not reached, because it never sees more than 7 in a row.

        MUTATION "no increment in the quota branch" (drop the reject's
        `taken_this_visit += 1`). Observed:
            AssertionError: rounds 1-3 are not one Ha attempt each between
            B and OIII: [('B', True), ('Ha', False), ('Ha', False),
            ('Ha', False), ('Ha', False), ('Ha', False), ('Ha', False),
            ('Ha', False), ('Ha', False)]
        i.e. round 1's Ha visit kept shooting (to the step guard's 10) while
        OIII waited.
        """
        plan = _cycle_plan(3, max_consecutive_rejects=10,
                           max_consecutive_rejects_night=20)
        shots = _grade_by_filter(monkeypatch, lambda f, n: f != "Ha")
        eng = SequenceEngine(sim_hub)
        eng.start(plan)
        sid = eng._session.id
        assert await _finish(eng, 90), f"the run never ended: {shots}"

        want = [("B", True), ("Ha", False), ("OIII", True)] * 3
        assert shots[:9] == want, (
            f"rounds 1-3 are not one Ha attempt each between B and OIII: "
            f"{shots[:9]}")
        assert shots[9:] == [("Ha", False)] * 7, (
            f"after B and OIII finished, Ha was not one reject per round up "
            f"to the step guard: {shots[9:]}")
        assert eng.state.get("end_reason") == "incomplete", eng.state
        s = session_store.load(sid)
        ids = {st.filter: st.id for st in plan.targets[0].steps}
        assert (s.accepted(ids["B"]), s.accepted(ids["OIII"]),
                s.accepted(ids["Ha"])) == (3, 3, 0)
        assert s.status == "dormant"

    async def test_with_the_step_guard_off_the_others_finish_before_the_night_guard(
            self, sim_hub, monkeypatch):
        """`max_consecutive_rejects` 0, night guard 20. B and OIII reach their
        count; only then, with Ha alone in the cycle, do 20 Ha rejects in a row
        end the night. One bad filter no longer ends the night while the
        others are being accepted.

        MUTATION "no increment in the quota branch". Observed:
            AssertionError: NightQualityStop arrived before the accepting
            filters finished: B 1/2, OIII 0/2 (Ha attempts: 20)
        i.e. round 1's Ha visit ran straight into the night guard.
        """
        plan = _cycle_plan(2, max_consecutive_rejects=0,
                           max_consecutive_rejects_night=20)
        shots = _grade_by_filter(monkeypatch, lambda f, n: f != "Ha")
        eng = SequenceEngine(sim_hub)
        eng.start(plan)
        sid = eng._session.id
        assert await _finish(eng, 90), f"the run never ended: {shots}"

        assert eng.state.get("end_reason") == "quality", eng.state
        s = session_store.load(sid)
        ids = {st.filter: st.id for st in plan.targets[0].steps}
        b, o = s.accepted(ids["B"]), s.accepted(ids["OIII"])
        assert (b, o) == (2, 2), (
            f"NightQualityStop arrived before the accepting filters finished: "
            f"B {b}/2, OIII {o}/2 (Ha attempts: {len(_ha_rounds(shots))})")
        # ...and the 20 that ended it were Ha alone, after the last accept.
        last_accept = max(i for i, (_, v) in enumerate(shots) if v)
        assert shots[last_accept + 1:] == [("Ha", False)] * 20, shots
        assert len(_ha_rounds(shots)) == 2 + 20

    async def test_a_longer_visit_is_per_visit_attempts_not_one_reject(
            self, sim_hub, monkeypatch):
        """`per_visit` 2, Ha always rejects, B and OIII always accept, counts
        2, step guard 6. A visit is TWO attempts whatever they grade: round 1
        is B, B, Ha, Ha, OIII, OIII, and Ha then takes two rejects a round
        until its carried counter reaches 6 in round 3.

        WHY A SECOND CASE. At `per_visit` 1, which every other case here uses,
        "a reject counts one attempt" and "a reject ends the visit" shoot the
        same sequence, so nothing above can tell the fix from a cruder one that
        abandons the channel on its first reject. Only a visit longer than one
        attempt separates them.

        MUTATION "a reject ends the visit" (the reject branch's
        ``taken_this_visit += 1`` made ``taken_this_visit = max_frames or
        taken_this_visit + 1``). Observed:
            AssertionError: a visit is 2 attempts, rejects included: [('B',
            True), ('B', True), ('Ha', False), ('OIII', True), ('OIII', True),
            ('Ha', False), ('Ha', False), ('Ha', False), ('Ha', False), ('Ha',
            False)]
        MUTATION "no increment in the quota branch" (the pre-#147 code).
        Observed:
            AssertionError: a visit is 2 attempts, rejects included: [('B',
            True), ('B', True), ('Ha', False), ('Ha', False), ('Ha', False),
            ('Ha', False), ('Ha', False), ('Ha', False), ('OIII', True),
            ('OIII', True)]
        """
        plan = _cycle_plan(2, per_visit=2, max_consecutive_rejects=6,
                           max_consecutive_rejects_night=20)
        shots = _grade_by_filter(monkeypatch, lambda f, n: f != "Ha")
        eng = SequenceEngine(sim_hub)
        eng.start(plan)
        sid = eng._session.id
        assert await _finish(eng, 90), f"the run never ended: {shots}"

        want = ([("B", True)] * 2 + [("Ha", False)] * 2 + [("OIII", True)] * 2
                + [("Ha", False)] * 4)
        assert shots == want, (
            f"a visit is 2 attempts, rejects included: {shots}")
        assert eng.state.get("end_reason") == "incomplete", eng.state
        s = session_store.load(sid)
        ids = {st.filter: st.id for st in plan.targets[0].steps}
        assert (s.accepted(ids["B"]), s.accepted(ids["OIII"]),
                s.accepted(ids["Ha"])) == (2, 2, 0)
        assert s.status == "dormant"


class TestTheStepGuardSpansVisits:
    async def test_the_step_is_set_aside_after_its_rejects_across_visits(
            self, sim_hub, monkeypatch, bus_lines):
        """`max_consecutive_rejects` 3, night guard 12, counts 5. Ha rejects in
        rounds 1, 2 and 3 (one per visit) and is set aside on the third; B and
        OIII finish their five; the target's visit loop ends because every
        step still owed is set aside, and the session is dormant with Ha owed.

        MUTATION "local counter" (the counter restarts at every `_run_step`
        call, as it did before). Observed:
            AssertionError: Ha was not set aside after exactly 3 rejects:
            17 Ha attempts, end_reason 'quality'
        i.e. a one-attempt visit never reaches 3, so the guard never trips
        and the night guard fires at 12 once Ha is alone.

        MUTATION "counter cleared by ANY accepted frame" (clear every step's
        counter where the night counter is reset). Observed:
            AssertionError: Ha was not set aside after exactly 3 rejects:
            8 Ha attempts, end_reason 'incomplete'
        i.e. B's and OIII's accepts between the Ha visits wiped Ha's count
        every round; it only tripped once Ha was alone in the cycle.

        MUTATION "cycle pending list keeps set-aside steps" (drop the
        exclusion in `_run_steps`' cycle branch; `_run_step` still returns at
        once). Observed:
            AssertionError: the visit loop did not end on 'every step still
            owed is set aside'; the cycle ended by: ['M42: a full pass took no
            frames with 1 step(s) still short (Ha) — stopping the cycle
            rather than spinning']
        """
        plan = _cycle_plan(5, max_consecutive_rejects=3,
                           max_consecutive_rejects_night=12)
        shots = _grade_by_filter(monkeypatch, lambda f, n: f != "Ha")
        eng = SequenceEngine(sim_hub)
        eng.start(plan)
        sid = eng._session.id
        assert await _finish(eng, 90), f"the run never ended: {shots}"

        ha = _ha_rounds(shots)
        assert len(ha) == 3, (
            f"Ha was not set aside after exactly 3 rejects: {len(ha)} Ha "
            f"attempts, end_reason {eng.state.get('end_reason')!r}")
        # three VISITS, not one: other filters' frames between each pair
        assert all(b - a > 1 for a, b in zip(ha, ha[1:])), shots
        assert shots == ([("B", True), ("Ha", False), ("OIII", True)] * 3
                         + [("B", True), ("OIII", True)] * 2), shots
        assert eng.state.get("end_reason") == "incomplete", eng.state

        s = session_store.load(sid)
        ids = {st.filter: st.id for st in plan.targets[0].steps}
        assert s.status == "dormant"
        assert s.accepted(ids["Ha"]) == 0
        assert s.remaining()[ids["Ha"]] == 5, "Ha is no longer owed"
        assert (s.accepted(ids["B"]), s.accepted(ids["OIII"])) == (5, 5)

        seq = [m for lvl, m, src in bus_lines if src == "sequence"]
        tripped = [m for m in seq if "set aside" in m and "Ha" in m
                   and "3 consecutive rejects" in m]
        assert len(tripped) == 1, (
            f"the set-aside was not logged in words, once: {tripped}")
        ended = [m for m in seq if "every step with remaining frames is set aside" in m]
        spun = [m for m in seq if "took no frames" in m]
        assert ended and not spun, (
            f"the visit loop did not end on 'every step still owed is set "
            f"aside'; the cycle ended by: {spun or ended}")

    async def test_an_accepted_frame_resets_that_steps_counter(
            self, sim_hub, monkeypatch):
        """Ha goes reject, reject, accept, reject, reject, accept against a
        guard of 3: never three in a row, so it is never set aside and the
        target completes. The control for the case above: the counter is
        CONSECUTIVE rejects, not rejects.

        MUTATION "no reset on an accepted frame" (drop the per-step reset
        in the accepted branch). Observed:
            AssertionError: Ha was set aside although it never rejected 3
            in a row: [('Ha', False), ('Ha', False), ('Ha', True),
            ('Ha', False)] -> end_reason 'incomplete'
        """
        plan = _cycle_plan({"B": 1, "Ha": 2, "OIII": 1},
                           max_consecutive_rejects=3,
                           max_consecutive_rejects_night=12)
        shots = _grade_by_filter(
            monkeypatch, lambda f, n: f != "Ha" or n in (2, 5))
        eng = SequenceEngine(sim_hub)
        eng.start(plan)
        sid = eng._session.id
        assert await _finish(eng, 90), f"the run never ended: {shots}"

        ha_shots = [x for x in shots if x[0] == "Ha"]
        assert eng.state.get("end_reason") != "incomplete", (
            f"Ha was set aside although it never rejected 3 in a row: "
            f"{ha_shots} -> end_reason {eng.state.get('end_reason')!r}")
        assert ha_shots == [("Ha", False), ("Ha", False), ("Ha", True),
                            ("Ha", False), ("Ha", False), ("Ha", True)]
        assert session_store.load(sid).status == "complete"

    async def test_start_clears_the_counter_and_the_set_aside(
            self, sim_hub, monkeypatch):
        """Night one sets Ha aside after 3 rejects; B and OIII complete. The
        run the NEXT night must shoot Ha again and give it the full 3 before
        setting it aside again: the count is for THIS run, and the set-aside
        is for the night it was made on.

        UPDATED FOR S2 (#208, spec 3.4 and 6.7), which changed the rule on
        purpose. In S0 the set-aside was not persisted, so every restart,
        the same night's included, retried the step. Now it is a
        ``Session.set_aside`` record carrying its night key: a restart the
        same night does not retry it
        (test_group_set_aside_persisted.py::
        test_step_set_aside_not_retried_same_night), and the next night does.
        So the second start here is the next night's: night one's record is
        moved to the night before, as a day passing leaves it. Before the
        update the case restarted the same night and failed on the new rule,
        observed:
            AssertionError: the resumed run gave Ha 0 attempts, not a fresh 3

        MUTATION "start() reads every night's records" (``start`` loads
        ``session.set_aside`` whole instead of ``set_aside_on(tonight)``).
        Observed:
            AssertionError: the resumed run gave Ha 0 attempts, not a fresh 3
        MUTATION "start() keeps the counter" (``self._step_rejects = {}``
        removed from ``start``). Observed:
            AssertionError: the resumed run gave Ha 1 attempts, not a fresh 3
        """
        plan = _cycle_plan(3, max_consecutive_rejects=3,
                           max_consecutive_rejects_night=12)
        shots = _grade_by_filter(monkeypatch, lambda f, n: f != "Ha")
        eng = SequenceEngine(sim_hub)
        eng.start(plan)
        sid = eng._session.id
        assert await _finish(eng, 90), f"night one never ended: {shots}"
        assert len(_ha_rounds(shots)) == 3, shots
        await _settled(eng)

        session = session_store.load(sid)
        assert session.status == "dormant"
        tonight = night_key(time.time())
        ha = next(s for s in session.plan.targets[0].steps if s.filter == "Ha")
        assert [(r["step_id"], r["night"]) for r in session.set_aside] == [
            (ha.id, tonight)], session.set_aside
        # A day passes: the record is last night's now.
        last_night = night_key(time.time() - 24 * 3600)
        assert last_night != tonight
        for rec in session.set_aside:
            rec["night"] = last_night
        mark = len(shots)
        eng.start(session.plan, session=session)
        assert await _finish(eng, 90), f"night two never ended: {shots}"
        again = [x for x in shots[mark:] if x[0] == "Ha"]
        assert len(again) == 3, (
            f"the resumed run gave Ha {len(again)} attempts, not a fresh 3")
        assert shots[mark:] == [("Ha", False)] * 3, shots[mark:]


class TestASetAsideStepIsNotShot:
    """The two layers that keep a set-aside step off the shutter, each on
    its own: `_run_steps` leaves it out of the pending list in BLOCK mode too,
    and `_run_step` returns at once for it whoever calls."""

    @staticmethod
    def _engine(sim_hub):
        plan = _cycle_plan(2)
        t = plan.targets[0]
        t.acquisition = "blocks"
        eng = SequenceEngine(sim_hub)
        eng.plan = plan
        ha = next(s for s in t.steps if s.filter == "Ha")
        eng._set_aside[f"{t.id}:{ha.id}"] = "set aside by the test"
        return eng, t, ha

    async def test_block_mode_leaves_it_out_of_the_pending_list(self, sim_hub):
        """MUTATION "blocks branch ignores the set-aside". Observed:
            AssertionError: blocks mode visited a set-aside step:
            ['B', 'Ha', 'OIII']
        """
        eng, t, ha = self._engine(sim_hub)
        visited: list[str] = []

        async def _spy(ti, si, tgt, step, max_frames=None):
            visited.append(step.filter)     # record only: no shutter here

        eng._run_step = _spy
        await asyncio.wait_for(eng._run_steps(0, t), 10.0)
        assert visited == ["B", "OIII"], (
            f"blocks mode visited a set-aside step: {visited}")

    async def test_run_step_returns_at_once_for_it(self, sim_hub, monkeypatch):
        """No filter move, no exposure. The tripwires raise, so a mutant
        fails at once rather than shooting.

        MUTATION "no early return in `_run_step`". Observed:
            AssertionError: _run_step reached the filter wheel for a
            set-aside step
        """
        eng, t, ha = self._engine(sim_hub)

        async def _wheel(step):
            raise AssertionError(
                "_run_step reached the filter wheel for a set-aside step")

        async def _shutter(*a, **k):
            raise AssertionError("_run_step exposed a set-aside step")

        monkeypatch.setattr(eng, "_apply_filter", _wheel)
        monkeypatch.setattr(eng, "_capture", _shutter)
        si = t.steps.index(ha)
        await asyncio.wait_for(eng._run_step(0, si, t, ha, max_frames=1), 10.0)

    async def test_the_control_step_beside_it_still_reaches_the_wheel(
            self, sim_hub, monkeypatch):
        """Without this, a `_run_step` that returned at once for EVERY step
        would pass the case above.

        MUTATION "early return for every step". Observed:
            Failed: DID NOT RAISE <class 'RuntimeError'>
        """
        eng, t, ha = self._engine(sim_hub)
        reached: list[str] = []

        async def _wheel(step):
            reached.append(step.filter)
            raise RuntimeError("stop here")

        monkeypatch.setattr(eng, "_apply_filter", _wheel)
        b = next(s for s in t.steps if s.filter == "B")
        with pytest.raises(RuntimeError, match="stop here"):
            await asyncio.wait_for(
                eng._run_step(0, t.steps.index(b), t, b, max_frames=1), 10.0)
        assert reached == ["B"]


class TestTheAntiSpinCountsExposures:
    async def test_a_pass_of_all_rejects_does_not_end_the_cycle(
            self, sim_hub, monkeypatch, bus_lines):
        """Every filter rejects its first exposure, then accepts. Pass 1 took
        three exposures and accepted none; that is a rough pass, not a spin,
        and the cycle carries on to complete every filter.

        MUTATION "compare `_frames_done`" in accepted mode too. Observed:
            AssertionError: the cycle stopped after pass 1: end_reason
            'incomplete', shots [('B', False), ('Ha', False),
            ('OIII', False)]
        MUTATION "exposure counter not incremented" (drop the
        `_exposures_taken += 1` after the capture). Observed: the same
        message, word for word.
        """
        plan = _cycle_plan(2, max_consecutive_rejects=10,
                           max_consecutive_rejects_night=20)
        shots = _grade_by_filter(monkeypatch, lambda f, n: n > 0)
        eng = SequenceEngine(sim_hub)
        eng.start(plan)
        sid = eng._session.id
        assert await _finish(eng, 90), f"the run never ended: {shots}"

        assert session_store.load(sid).status == "complete", (
            f"the cycle stopped after pass 1: end_reason "
            f"{eng.state.get('end_reason')!r}, shots {shots}")
        assert shots == ([(f, False) for f in FILTERS]
                         + [(f, True) for f in FILTERS] * 2), shots
        assert not [m for _, m, _ in bus_lines if "took no frames" in m]

    async def test_attempts_mode_a_pass_of_discards_still_ends_the_cycle(
            self, sim_hub, monkeypatch, bus_lines):
        """THE CONTROL. Attempts mode with `hfr_reject_action` discard, every
        frame rejected: a discard never advances `_done`, and no reject guard
        runs in that branch, so `_frames_done` is the ONLY thing that can see
        a pass that banked nothing. One pass of three discards ends the cycle.

        MUTATION "exposure counter in attempts mode too". Observed:
            AssertionError: attempts mode spun on discards until the wall
            bound: 48 exposures
        (the count depends on the machine; the cycle never ends by itself,
        and the 20 s wall bound is what stops it).
        """
        monkeypatch.setattr(config_store.cfg().escalation,
                            "hfr_reject_action", "discard")
        plan = _cycle_plan(2, count_mode="attempts")
        shots = _grade_by_filter(monkeypatch, lambda f, n: False)
        eng = SequenceEngine(sim_hub)
        eng.start(plan)
        sid = eng._session.id
        ended = await _finish(eng, 20)
        assert ended, (
            f"attempts mode spun on discards until the wall bound: "
            f"{len(shots)} exposures")
        assert shots == [(f, False) for f in FILTERS], shots
        assert [m for _, m, _ in bus_lines if "took no frames" in m]
        assert session_store.load(sid).frames == []     # discards: no ledger
