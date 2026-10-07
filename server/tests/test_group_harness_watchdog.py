# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The group harness fails a spin that never yields (#319).

`Night.run` bounds a night in real time by polling between
``await asyncio.sleep(0.01)`` calls, and a scheduler loop that never
reaches an await that suspends never gives that poll a turn. The S2 mutant
of that shape held two xdist workers at 100% of a core until an outer 900 s
timeout, and pytest printed nothing for either. `Night.run` now arms a spin
watchdog (tests/_group_harness.py, "THE SPIN WATCHDOG"): when the event loop
has been away ``spin_bound_s`` real seconds, it dumps every thread's stack
with faulthandler, breaks the spin, and the test fails with a message naming
the frame that spun.

THE SPIN HERE IS THE ENGINE'S OWN TASK. Each case replaces
`SequenceEngine._run_scheduled`, where #319's mutant spun, with a loop of the
shape the issue describes, so the spin runs where a real one would: inside
the engine's run task, under `_run`'s handlers and teardown, on the clocked
simulator hub. The spin also carries its own real-time limit, which is not
the watchdog's and is several times its bound: without it, the mutant that
removes the watchdog would hang this file exactly as #319 hung the suite,
and a test whose failure is a hang reports nothing.

#319's own mutant, run against the real engine, is recorded where the
watchdog is (the harness docstring's cases), and in the first case below.

Every mutant was applied to a private copy of ``server/`` under the session
scratchpad (scratchpad/s3-X-mut), never to the shared tree (#254), from a
byte backup, and restored and compared by SHA-256 after each run. The #702
cases' mutants were applied in that wave's own git worktree (WP-116), the
same way.
"""
from __future__ import annotations

import asyncio
import inspect
import threading
import time

import pytest

from _group_harness import (SPIN_BOUND_S, Night, SpinNeverYielded, group_hub,
                            group_store, single, watchdog_timeline)
from astrodeck.sequence import SequenceEngine
from astrodeck.sequence.models import SequencePlan

#: The watchdog's bound in these cases, in real seconds: short, so the file
#: stays quick, and still many loop turns long.
BOUND_S = 1.0
#: A spin's own limit: well past the bound, so only a missing or broken
#: watchdog ever lets a spin reach it.
SPIN_LIMIT_S = 8.0 * BOUND_S
#: The bound the NON-spin controls run under (#702). The spin case wants a SHORT
#: bound, so the file stays quick; a control that must see NO spin wants a bound
#: past anything a loaded box does to a night that is doing nothing wrong, which
#: the watchdog cannot tell from a spin. Measured under 40 CPU burners on 24
#: cores: a night that yields every few milliseconds was away 0.06 to 0.97 s at
#: worst (one run 0.03 s under the old 1 s bound), and a deliberate 1.5 s stall
#: inside one, 1.5 to 2.4 s. It is the #620/#683 class (a bound on a clock the
#: load stretches) and the cure is the same: the bound moves, not the thing it
#: bounds. The spin case keeps its own short bound, so what counts as a spin has
#: not moved, and what the controls assert about the loop yielding is untouched.
NONSPIN_BOUND_S = 4.0 * BOUND_S
#: How long a control keeps the watchdog's subject going to prove anything
#: about it: past the bound it runs under, so a watchdog that timed the RUN
#: rather than the loop, or one that outlived it, would have fired by then,
#: with half a bound to spare for the watcher's own tick and a late wake.
NONSPIN_SPAN_S = 1.5 * NONSPIN_BOUND_S


def _plan() -> SequencePlan:
    return SequencePlan(name="watchdog", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False,
                        park_when_done=False, warm_cooler_when_done=False,
                        recover_guiding=False,
                        targets=[single("Watchdog field")])


async def _run(night: Night) -> tuple[object, str | None, float]:
    """Run the plan to its end on ``night``: (what ``run`` returned, the
    failure text if the run failed the test, real seconds taken)."""
    t = time.monotonic()
    try:
        done: object = await night.run(_plan())
        failed = None
    except pytest.fail.Exception as exc:
        done, failed = None, str(exc)
    finally:
        await night.close()
    return done, failed, time.monotonic() - t


async def test_a_spin_that_never_yields_fails_the_night_and_names_the_spin(
        group_hub, group_store, monkeypatch):
    """The engine's scheduler spins on an await that never suspends. The
    night fails inside the bound (plus the watchdog's tick and the
    teardown), with a message that says what happened, names the spinning
    frame, and carries faulthandler's dump; the spin never reaches its own
    limit.

    MUTANT "no watchdog" (`_SpinWatchdog._watch` returns at once,
    _group_harness.py): nothing ends the spin but its own limit, and the
    night then finishes as if nothing had happened. RED (observed):
        E   AssertionError: the spin ran its full 8.0 s: nothing broke it,
        and the night then returned True
        E   assert not [8.0]

    MUTANT "the raise never lands" (`_raise_in_thread` returns 0 without
    raising): the watchdog writes its report, the spin runs on, and a bound
    later the last resort ends the process. Under ``-n0`` that is the whole
    run (exit 3), which is why the raise is the path and the exit only a
    last resort; the report is still printed (observed, with -n0):
        Night.run: the event loop did not come back for 1.1 s of real time,
        against a bound of 1 s (...)
        The loop's thread was spinning in nothing
        (test_group_harness_watchdog.py:102).
        ...
        The raise did not break the spin: the loop has now been away 2.2 s.
        Ending this process so the run can go on (#319).
    and under ``-n 2`` the crash is reported on this case, the report rides
    in the warnings summary, and the run goes on (observed):
        worker 'gw0' crashed while running 'tests/test_group_harness_watchdog
        .py::test_a_spin_that_never_yields_fails_the_night_and_names_the_spin'
        ============================== warnings summary ====================
        tests/test_group_harness_watchdog.py::test_a_spin_that_never_yields_
        fails_the_night_and_names_the_spin
          ...\\_group_harness.py:0: UserWarning: Night.run: the event loop did
          not come back ...
        1 failed, 2 passed, 1 warning in 8.45s

    #319's OWN MUTANT, on the real engine: `self._drop_from(remaining, t)`
    deleted in the skip arm of `SequenceEngine._close_group_pass`, and
    test_group_rotation.py run whole. Before the watchdog it hung: the
    skip case was still spinning, with nothing printed, when an outer
    100 s timeout killed the run. With the watchdog, under ``-n0`` (observed,
    long lines cut):
        10.57s call  tests/test_group_rotation.py::test_a_guider_that_fails_on_every_panel_is_the_rigs_fault[skip]
        10.40s call  tests/test_group_rotation.py::test_no_guider_connected_defers_the_pass_then_the_rigs_action_decides[skip]
        _______ test_a_guider_that_fails_on_every_panel_is_the_rigs_fault[skip] _______
        Night.run: the event loop did not come back for 10.1 s of real time,
        against a bound of 10 s (...). A spin that never yields (#319): ...
        The loop's thread was spinning in out (astrodeck\\sequence\\schedule.py:739).
        Its frames in astrodeck, outermost first: _run (...engine.py:2355) >
        _run_scheduled (...engine.py:2753) > _schedule_loop (...engine.py:2824)
        > gating_status (...schedule.py:778) > out (...schedule.py:739).
        Every thread's stack when the watchdog fired, from faulthandler: ...
        2 failed, 43 passed in 55.55s
    and under ``-n 12`` the same two, at 11.20 s and 10.22 s, each on its
    own worker ([gw7], [gw8]), no worker crashed, "2 failed, 43 passed in
    17.97s". The frames put the spin in `_schedule_loop`, which the skip
    arm returns to with the set-aside panels still in ``remaining``: once
    the frame was `_close_group_pass` (engine.py:4261) logging the set-aside
    again, which is the loop coming round.
    """
    spun_out: list[float] = []

    async def nothing() -> None:
        return None

    async def spin_without_yielding(self, plan):
        started = time.monotonic()
        while time.monotonic() - started < SPIN_LIMIT_S:
            await nothing()            # an await that never suspends
        spun_out.append(time.monotonic() - started)

    monkeypatch.setattr(SequenceEngine, "_run_scheduled",
                        spin_without_yielding)
    night = Night(group_hub, monkeypatch, spin_bound_s=BOUND_S)
    done, failed, took = await _run(night)

    assert not spun_out, (
        f"the spin ran its full {spun_out[0]:.1f} s: nothing broke it, and "
        f"the night then returned {done!r}")
    assert failed is not None, f"the night did not fail; run returned {done!r}"
    # #620: bound on the WATCHDOG'S OWN measurement, not this test's wall
    # clock. `took` spans the raise reaching the spinning thread AND
    # `night.close()`'s teardown, both of which a loaded box can stretch by
    # several seconds while the watchdog itself still fired right on time:
    # a wave-3 verifier run beside twelve other worktrees' suites, -n 0
    # (2026-10-01, seen twice, once against an unmodified engine.py) failed
    # this exact assertion with "the night failed 4.x s after it started,
    # for a bound of 1.0 s", a red full suite that was not a defect. The
    # watchdog's own report text carries its own timeline (the real-seconds
    # `away` it measured the instant it fired, `_SpinWatchdog._describe`'s
    # own words), so that is what the bound is read from.
    # (The pattern is `_group_harness.watchdog_timeline`'s, shared with the
    # two other tests that read it, so none of them drifts from the text.)
    away_s, reported_bound_s = watchdog_timeline(failed)
    assert reported_bound_s == BOUND_S, (
        f"the report's own bound ({reported_bound_s} s) does not match "
        f"this case's BOUND_S ({BOUND_S} s): {failed}")
    # The watcher polls every min(0.25, bound_s / 8) = 0.125 s here, so a
    # tick-sized overshoot past BOUND_S is normal; the rest of the margin is
    # for a watcher THREAD starved by load rather than the loop itself.
    # MEASURED beside a CPU burner (WP-68's proof for #620, not a rerun):
    # 40 CPU-bound processes saturating all 24 cores at 100%, 8 runs of
    # this case -- away_s stayed 1.0-1.1 s every time (the watcher's own
    # 0.125 s tick granularity, not the load), while `took` (the OLD bound)
    # ranged 1.17-3.02 s on the SAME runs: the wall clock is what the load
    # stretches, not the watchdog's own measurement.
    assert away_s < BOUND_S + 1.0, (
        f"the watchdog's OWN timeline measured {away_s:.2f} s away, for a "
        f"bound of {BOUND_S} s -- not this test's wall clock, which took "
        f"{took:.2f} s in total (raise delivery + night.close() teardown "
        f"included, both outside the watchdog's own measurement): {failed}")
    assert "the event loop did not come back" in failed, failed
    assert "#319" in failed, failed
    lines = failed.splitlines()
    spinning = next((ln for ln in lines if "spinning in " in ln), "")
    # The frame is read while the spin runs, so it is the loop or the
    # coroutine it awaits, whichever held the thread at that instant.
    assert ("spin_without_yielding (" in spinning
            or "nothing (" in spinning), (
        f"the message does not name the spinning frame:\n{failed}")
    chain = next((ln for ln in lines if ln.startswith("Its frames in")), "")
    assert "_run (astrodeck" in chain, (
        f"the engine's frames around the spin are not named:\n{failed}")
    # faulthandler's own dump, not a stand-in: its header for the thread
    # that was spinning, and this file's frame in its stack.
    assert "Thread 0x" in failed or "Current thread 0x" in failed, failed
    assert "test_group_harness_watchdog.py" in failed, failed


async def test_a_night_that_keeps_yielding_is_not_a_spin(
        group_hub, group_store, monkeypatch):
    """CONTROL. The scheduler runs for one and a half bounds of real time,
    but hands the loop back every few milliseconds, so the loop never stays
    away and the watchdog never fires: the night ends by itself. The
    watchdog times the LOOP, not the run.

    Run under ``NONSPIN_BOUND_S``, not the spin case's short bound (#702): a
    loop thread starved by a loaded box is away for a second at a time
    without spinning, and the long bound is what says so.

    MUTANT "the watchdog times the run" (`_SpinWatchdog.pet` no longer
    moves ``beat``): the night fails one bound in, with the loop idle in its
    poll, where the raise lands and leaves the loop. RED (observed):
        E   _group_harness.SpinNeverYielded: Night.run: the event loop did
        not come back for 4.2 s of real time, against a bound of 4 s (...)
        E   The loop's thread was spinning in _poll (windows_events.py:774).
        E   Its frames in astrodeck, outermost first: none.
    """
    async def busy_but_yielding(self, plan):
        started = time.monotonic()
        while time.monotonic() - started < NONSPIN_SPAN_S:
            await asyncio.sleep(0.005)     # the real one: the loop gets a turn

    monkeypatch.setattr(SequenceEngine, "_run_scheduled", busy_but_yielding)
    night = Night(group_hub, monkeypatch, spin_bound_s=NONSPIN_BOUND_S)
    done, failed, took = await _run(night)

    assert failed is None, failed
    assert done is True, done
    assert took >= NONSPIN_SPAN_S, (
        f"the night took {took:.2f} s, not the {NONSPIN_SPAN_S:g} s (one and "
        f"a half bounds) it was built to take, so it proves nothing about "
        f"the bound")
    assert night.watchdog is not None
    assert night.watchdog.max_away < NONSPIN_BOUND_S / 2, night.watchdog.max_away


async def test_a_starved_loop_is_not_a_spin_under_the_non_spin_bound(
        group_hub, group_store, monkeypatch):
    """CONTROL (#702), and the one that does not need a loaded box to mean
    something. The scheduler hands the loop back every few milliseconds,
    except once, where it holds the loop's thread for longer than the spin
    case's bound: what a starved thread looks like to the watchdog from
    outside, which cannot tell it from a spin. The stall is longer than
    ``BOUND_S`` and well inside ``NONSPIN_BOUND_S``, so the night must end by
    itself, and the watchdog must have SEEN the stall (``max_away``), or the
    case proved nothing about the bound it runs under.

    The two controls above run under the same bound and fail under load only
    by chance of the box; this one makes the load deterministic, so that
    putting their bound back to the spin case's is caught on an idle machine.

    MUTANT "the controls run under the spin bound" (``spin_bound_s=BOUND_S``
    in this case's ``Night(...)``, which is what the controls had): the
    watchdog fires on the stall. RED (observed, idle box):
        E   AssertionError: Night.run: the event loop did not come back for
        1.1 s of real time, against a bound of 1 s (...)
        E   The loop's thread was spinning in starved_once
        (test_group_harness_watchdog.py:293).
    Setting ``NONSPIN_BOUND_S = BOUND_S`` instead fails the first assertion
    below, which says the stall must be a spin by one bound and not the other.
    """
    stall_s = 1.5 * BOUND_S
    assert BOUND_S < stall_s < NONSPIN_BOUND_S, (
        "the stall must be a spin by the spin case's bound and not one by "
        "the controls'")

    async def starved_once(self, plan):
        for turn in range(40):
            if turn == 20:
                time.sleep(stall_s)        # the thread is not running
            await asyncio.sleep(0.005)

    monkeypatch.setattr(SequenceEngine, "_run_scheduled", starved_once)
    night = Night(group_hub, monkeypatch, spin_bound_s=NONSPIN_BOUND_S)
    done, failed, _took = await _run(night)

    assert failed is None, failed
    assert done is True, done
    assert night.watchdog is not None
    assert night.watchdog.max_away >= stall_s, (
        f"the watchdog saw the loop away {night.watchdog.max_away:.2f} s, "
        f"less than the {stall_s:g} s stall it was built around")
    assert night.watchdog.max_away < NONSPIN_BOUND_S, night.watchdog.max_away


async def test_the_watchdog_is_gone_when_the_run_returns(
        group_hub, group_store, monkeypatch):
    """CONTROL. A night that ends normally leaves no watchdog behind: after
    ``run`` returns, the test holds its thread for one and a half bounds
    without giving the loop a turn, and nothing is raised into it, and no
    watchdog thread is alive. A watchdog outliving its run would throw into
    whatever the test did next, and fail it for a spin that never happened.

    Run under ``NONSPIN_BOUND_S`` (#702), and over a scheduler that ends at
    once rather than the real one. This is the control that failed 6 of 6
    runs under 40 CPU burners with "the event loop did not come back for
    1.1 s" on a plan that had done nothing wrong, and the cause was not a
    starved loop but the REAL scheduler's first step: CPU-bound work with no
    await in it (lazy imports, the first sky computations), which holds the
    loop's thread about 1 s on an idle box and, measured cold under 40
    burners, 6 to 11 s. That is not a spin and no bound short enough to test
    anything would pass it. What this case asserts is the watchdog's
    lifecycle, which does not depend on what the night did, so the night is
    the shortest one that ends normally, and the hold below is what makes
    the bound matter: a watchdog left running has to outlast it to be caught.

    Also pins the default: a Night built without ``spin_bound_s`` arms the
    named ``SPIN_BOUND_S``.

    MUTANT "the watchdog is never stopped" (`_SpinWatchdog.stop` returns at
    once, _group_harness.py): the watchdog outlives its run and, a bound
    after the last pet, throws its report into the hold below. RED (observed):
        E   AssertionError: a finished run's watchdog raised: Night.run: the
        event loop did not come back for 4.0 s of real time, against a bound
        of 4 s (...)
        E   The loop's thread was spinning in
        test_the_watchdog_is_gone_when_the_run_returns
        (test_group_harness_watchdog.py:361).
    (Deleting only the ``dog.stop()`` in `Night.run`'s ``finally`` is NOT a
    mutant any more: `Night.close`, which `_run` always calls, disarms the
    watchdog too, so this case passed with it gone, observed 2026-10-07.)
    """
    default = inspect.signature(Night).parameters["spin_bound_s"].default
    assert default == SPIN_BOUND_S, default

    async def ends_at_once(self, plan):
        await asyncio.sleep(0.005)

    monkeypatch.setattr(SequenceEngine, "_run_scheduled", ends_at_once)
    night = Night(group_hub, monkeypatch, spin_bound_s=NONSPIN_BOUND_S)
    done, failed, _took = await _run(night)
    assert failed is None and done is True, (done, failed)

    stray: BaseException | None = None
    try:
        until = time.monotonic() + NONSPIN_SPAN_S
        while time.monotonic() < until:     # the loop gets no turn here
            time.sleep(0.01)
    except SpinNeverYielded as exc:
        stray = exc
    assert stray is None, f"a finished run's watchdog raised: {stray}"
    alive = [t.name for t in threading.enumerate()
             if t.name.startswith("spin watchdog")]
    assert alive == [], alive
