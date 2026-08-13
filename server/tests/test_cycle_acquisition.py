"""Round-robin acquisition: L R G B S Ha O3, forty-five times over.

WHAT IT IS FOR. Block acquisition shoots 45 L, then 45 R, then 45 G. Every
channel therefore carries a different hour of sky — different seeing, different
transparency, different altitude — and a night that ends early yields three
finished filters and four empty ones. Cycling visits each step in turn, so every
filter samples the same sky and 60 percent of a night is 60 percent of every
channel.

WHAT IT MUST NOT DO. Block mode is what every saved plan on disk uses and what
the rig ran tonight. `acquisition` defaults to "blocks" and the driver's
block branch is the original loop, unchanged — the tests below assert the
sequence of calls, not merely the totals, because "same frames in a different
order" would be a silent corruption of every existing plan.
"""
from __future__ import annotations

import pytest

from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target

LRGB_SHO = [("L", 60.0), ("R", 60.0), ("G", 60.0), ("B", 60.0),
            ("S", 180.0), ("Ha", 180.0), ("O3", 180.0)]


class _Hub:
    def __init__(self):
        self.devices = {}
        self.guider = None
        self.site = {}
        self.master_library = None


def _target(count=3, per_visit=1, acquisition="cycle"):
    return Target(
        name="NGC 6946", ra_hours=20.58, dec_deg=60.15,
        acquisition=acquisition,
        steps=[ExposureStep(filter=f, exposure_s=e, count=count,
                            per_visit=per_visit)
               for f, e in LRGB_SHO])


def _engine(target, count_mode="attempts"):
    """A engine whose _run_step only RECORDS the visit, so the order of visits
    is observable without a camera."""
    eng = SequenceEngine(_Hub())
    eng.plan = SequencePlan(name="n", targets=[target], count_mode=count_mode)
    eng.visits: list[tuple[str, int]] = []
    eng._frames_done = 0

    async def _fake_run_step(ti, si, tgt, step, max_frames=None):
        want = step.count - eng._done.get(f"{tgt.id}:{step.id}", 0)
        take = min(want, max_frames if max_frames is not None else want)
        if take <= 0:
            return
        eng.visits.append((step.filter, take))
        eng._done[f"{tgt.id}:{step.id}"] = \
            eng._done.get(f"{tgt.id}:{step.id}", 0) + take
        eng._frames_done += take

    eng._run_step = _fake_run_step
    return eng


class TestTheCycleOrder:
    async def test_it_goes_round_the_filters_not_through_them(self):
        eng = _engine(_target(count=3, per_visit=1))
        await eng._run_steps(0, eng.plan.targets[0])
        order = [f for f, _ in eng.visits]
        assert order == ["L", "R", "G", "B", "S", "Ha", "O3"] * 3, (
            f"not a round-robin: {order}")

    async def test_every_step_ends_with_its_full_count(self):
        t = _target(count=45, per_visit=1)
        eng = _engine(t)
        await eng._run_steps(0, t)
        for step in t.steps:
            assert eng._done[f"{t.id}:{step.id}"] == 45
        assert eng._frames_done == 45 * 7

    async def test_per_visit_greater_than_one_takes_a_run_of_frames(self):
        """Some imagers want 3 L then 3 R, to amortise filter moves."""
        t = _target(count=6, per_visit=3)
        eng = _engine(t)
        await eng._run_steps(0, t)
        assert eng.visits == [(f, 3) for f, _ in LRGB_SHO] * 2

    async def test_a_short_last_pass_does_not_overshoot_the_count(self):
        """count=5 with per_visit=2 is two full passes and a pass of one."""
        t = _target(count=5, per_visit=2)
        eng = _engine(t)
        await eng._run_steps(0, t)
        for step in t.steps:
            assert eng._done[f"{t.id}:{step.id}"] == 5, "overshot the count"
        assert [n for _, n in eng.visits[-7:]] == [1] * 7


class TestBlockModeIsUntouched:
    async def test_blocks_run_one_step_to_completion_before_the_next(self):
        t = _target(count=3, acquisition="blocks")
        eng = _engine(t)
        await eng._run_steps(0, t)
        assert [f for f, _ in eng.visits] == [f for f, _ in LRGB_SHO], (
            "block mode started interleaving")

    async def test_blocks_is_the_default_for_a_plain_target(self):
        t = Target(name="M31", ra_hours=0.71, dec_deg=41.27,
                   steps=[ExposureStep(filter="L", exposure_s=60, count=2)])
        assert t.acquisition == "blocks"

    async def test_a_block_step_is_never_given_a_visit_bound(self):
        """max_frames=None is what "run to completion" means. Passing a number
        here would silently turn every existing plan into a cycle of one."""
        t = _target(count=3, acquisition="blocks")
        eng = _engine(t)
        seen = []

        async def _spy(ti, si, tgt, step, max_frames=None):
            seen.append(max_frames)
            eng._done[f"{tgt.id}:{step.id}"] = step.count

        eng._run_step = _spy
        await eng._run_steps(0, t)
        assert seen == [None] * 7


class TestItCannotSpin:
    async def test_a_pass_that_takes_no_frames_stops_the_cycle(self):
        """The one failure a driver like this must not have. A step that can
        never complete — a filter the wheel does not have, a quota the night
        cannot reach — would otherwise loop between frames forever."""
        t = _target(count=3)
        eng = _engine(t)

        async def _never_shoots(ti, si, tgt, step, max_frames=None):
            return                      # completes nothing, takes nothing

        eng._run_step = _never_shoots
        await eng._run_steps(0, t)      # must RETURN, not hang
        assert eng._frames_done == 0

    async def test_one_stuck_step_does_not_block_the_others(self):
        """S cannot be shot; L R G B Ha O3 must still finish."""
        t = _target(count=2)
        eng = _engine(t)
        real = eng._run_step

        async def _s_is_stuck(ti, si, tgt, step, max_frames=None):
            if step.filter == "S":
                return
            await real(ti, si, tgt, step, max_frames)

        eng._run_step = _s_is_stuck
        await eng._run_steps(0, t)
        done = {s.filter: eng._done.get(f"{t.id}:{s.id}", 0) for s in t.steps}
        assert done["S"] == 0
        assert all(v == 2 for k, v in done.items() if k != "S"), done


class TestCompletionAgreesWithTheCountMode:
    async def test_attempts_mode_counts_frames_taken(self):
        t = _target(count=2)
        eng = _engine(t, count_mode="attempts")
        step = t.steps[0]
        assert eng._step_complete(t, step) is False
        eng._done[f"{t.id}:{step.id}"] = 2
        assert eng._step_complete(t, step) is True

    async def test_accepted_mode_asks_the_ledger_not_the_attempt_count(self):
        """In accepted mode attempts are unbounded — a step with 50 attempts
        and 2 accepted is NOT complete against a count of 3. Reading `_done`
        here would end the night early on a rough night, which is exactly the
        night the ledger exists for."""
        t = _target(count=3)
        eng = _engine(t, count_mode="accepted")
        step = t.steps[0]

        class _Ledger:
            def __init__(self, n): self.n = n
            def accepted(self, _sid): return self.n

        eng._session = _Ledger(2)
        eng._done[f"{t.id}:{step.id}"] = 50
        assert eng._step_complete(t, step) is False
        eng._session = _Ledger(3)
        assert eng._step_complete(t, step) is True
