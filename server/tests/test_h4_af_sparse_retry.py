# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A sparse-field autofocus failure is retried once at twice the exposure,
and a run that carries on after the retry fails too sweeps again at the
first frame rich enough to focus on (#507, H4 orchestrator ruling 4).

THE NIGHT IT COMES FROM. 2026-09-28 on astrotown, NGC 7331, a moon two days
past full and haze: the initial autofocus failed twice in twenty minutes
("5 stars is a sparse field", ten points at 6 s with 5 to 19 stars each,
``r_squared_below_threshold``), and each time the run carried on at the
previous focuser position. The warning's own advice named a longer exposure;
nothing tried one. A later sweep, at 21:24, succeeded once the haze eased,
and from 20:33 until then the run imaged at the position the failed sweeps
left, with nothing owed to sweep again.

THE RULING, AS BUILT.

* ``focus.native`` says on the result whether a failure was on a sparse field
  (``AutofocusResult.sparse_field``: fewer than ``SPARSE_FIELD_WARN`` stars at
  the start position, on a probe that was not clipped). Its own cases are
  the first three below, on the real native sweep with the detector seam
  scripted, as test_autofocus_advice.py scripts it.
* `SequenceEngine._autofocus` retries such a failure once, at twice the
  exposure, before ``af_failure_action`` is asked. Any other failure is not
  retried.
* When the retry fails too: under "warn" the run carries on where the sweeps
  started and says so in words, and a sweep is owed at the first light frame
  whose star count reaches ``SPARSE_FIELD_WARN``; "skip" and "abort" apply as
  they always did. The owed sweep's own failure owes nothing more.
* The count compared is the light frame's and the line the probe's, as the
  ruling words it. A light frame is longer and finer-binned than a sweep's
  probe, so in practice the owed sweep comes due at about the first light
  frame; #558 is open on comparing like with like. These cases script the
  light frames' counts, so they grade the rule, not that calibration.

THE ENGINE CASES run the real `_run_scheduled`, `_setup_target`, `_run_step`
and `_autofocus` on the clocked simulator (tests/_group_harness.py). The
harness takes the focuser off the rig, so each case gives it back a focuser
the engine can read (test_group_plain_stop_defers.py's shape) and scripts
``run_autofocus``, the one call below the engine, by the sweep's number: the
engine's retry, its escalation and its frame loop are what is graded. Each
light frame's star count is a script too, by the frame's number.

Every mutant was applied in a private copy of ``server/`` (scratchpad
``H4-ENG-B-mut``), from a byte backup restored and sha256-checked after each,
never in the shared tree (#254). The observed failure is quoted verbatim
(the first assertion line, long lines wrapped).
"""
from __future__ import annotations

import pytest

import astrodeck.focus.native as N
import astrodeck.sequence.engine as engine_mod
from _group_harness import Night, group_hub, group_store, single  # noqa: F401
from _simhub import sim_hub  # noqa: F401 (fixture import)
from astrodeck import providers
from astrodeck.config import EscalationConfig
from astrodeck.devices.sim import build_sim_rig
from astrodeck.focus.autofocus import AutofocusResult
from astrodeck.focus.native import SPARSE_FIELD_WARN
from astrodeck.sequence import SequenceEngine
from astrodeck.sequence.models import SequencePlan

native_only = pytest.mark.skipif(
    not providers.NATIVE_AVAILABLE, reason="astrodeck_native wheel not installed")

TARGET = "NGC 7331"
#: Where the scripted sweeps start and, failing, leave the focuser: the
#: position the rig's failed sweeps left on 2026-09-28.
START_POS = 11044
#: Where a scripted sweep that works puts it.
GOOD_POS = 11064


# ============================================== the native sweep's verdict

async def _connected_sim():
    parts = build_sim_rig()
    cam, foc = parts["camera"], parts["focuser"]
    await cam.connect()
    await foc.connect()
    return cam, foc


def _probe_then_points(probe_stars: int, point_stars: int = 1):
    """The Rust detector, scripted: ``probe_stars`` on the pre-sweep probe
    at the start position, ``point_stars`` on anything after it."""
    seen = {"calls": 0}

    def detector(data, params):
        seen["calls"] += 1
        n = probe_stars if seen["calls"] == 1 else point_stars
        return [], {"star_count": n, "hfr_median": 3.4, "hfr_mad": 0.30}
    return detector


@native_only
@pytest.mark.parametrize("probe", [SPARSE_FIELD_WARN - 1, 3])
async def test_a_failure_on_a_sparse_field_says_so(monkeypatch, probe):
    """A field under ``SPARSE_FIELD_WARN`` at the start position, nothing
    measurable once defocused (the 2026-07-31 and 2026-09-28 shape): the
    failed result says it failed on a sparse field, with the start count.
    One case sweeps and fails (14 stars, one under the line); the other has
    too few stars to sweep at all (3, under ``MIN_STARS_TO_SWEEP``), which a
    longer exposure is exactly as likely to cure.

    RED under mutant "no sparse flag" (``_failed`` in ``focus/native.py``
    passing ``sparse_field=False``), both cases, observed:

        AssertionError: a failure on a field of 14 stars did not say it was
        sparse: sparse_field=False, start_stars=14
        AssertionError: a failure on a field of 3 stars did not say it was
        sparse: sparse_field=False, start_stars=3
    """
    cam, foc = await _connected_sim()
    monkeypatch.setattr(N._native, "detect_and_measure",
                        _probe_then_points(probe))
    monkeypatch.setattr(N, "native_sweep_metric", lambda data: (5.0, 1))
    res = await N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                       step=350, steps_each_side=4, binning=2)
    assert res.success is False, "premise: the sweep failed"
    assert res.sparse_field is True and res.start_stars == probe, (
        f"a failure on a field of {probe} stars did not say it was sparse: "
        f"sparse_field={res.sparse_field}, start_stars={res.start_stars}")


@native_only
async def test_control_a_failure_on_a_rich_field_is_not_sparse(monkeypatch):
    """CONTROL. The same failure on a field AT the line (15 stars at the
    start position, nothing measurable once defocused): not sparse, so the
    engine will not spend a second sweep on a longer exposure.

    RED under mutant "the line one star high" (``_failed``'s ``n0 <
    SPARSE_FIELD_WARN`` made ``n0 <= SPARSE_FIELD_WARN``), observed:

        AssertionError: a failure on a field of 15 stars, at the line, was
        called sparse
    """
    cam, foc = await _connected_sim()
    monkeypatch.setattr(N._native, "detect_and_measure",
                        _probe_then_points(SPARSE_FIELD_WARN))
    monkeypatch.setattr(N, "native_sweep_metric", lambda data: (5.0, 1))
    res = await N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                       step=350, steps_each_side=4, binning=2)
    assert res.success is False, "premise: the sweep failed"
    assert res.start_stars == SPARSE_FIELD_WARN, res.start_stars
    assert res.sparse_field is False, (
        f"a failure on a field of {SPARSE_FIELD_WARN} stars, at the line, was "
        f"called sparse")


@native_only
async def test_control_a_clipped_probe_is_not_sparse(monkeypatch):
    """CONTROL. Three stars at the start position on a frame that is
    overexposed: the sweep refuses, and says the stars merged into saturated
    blobs. Not sparse, whatever the count, because the engine's remedy for a
    sparse field is a longer exposure, the one change the refusal's own
    advice says makes this worse.

    RED under mutant "clipping ignored" (``_failed`` without its ``and
    probe_sat < OVEREXPOSED_FRAC``), observed:

        AssertionError: a refusal on a clipped frame was called sparse: 'only
        3 measurable stars because the frame is overexposed — 5'
    """
    cam, foc = await _connected_sim()
    monkeypatch.setattr(N._native, "detect_and_measure",
                        _probe_then_points(3))
    monkeypatch.setattr(N, "saturation_fraction", lambda data: 0.5)
    res = await N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                       step=350, steps_each_side=4, binning=2)
    assert res.success is False and "overexposed" in res.message, (
        f"premise: the clipped probe was refused: {res.message!r}")
    assert res.sparse_field is False, (
        f"a refusal on a clipped frame was called sparse: "
        f"{res.message[:60]!r}")


# ================================================= the engine's answer

class _Focuser:
    """A focuser the engine can read around a sweep. The sweep itself is
    ``run_autofocus``, which each case scripts."""

    name = "scripted focuser"
    connected = True
    max_position = 60000

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def get_position(self) -> int:
        return START_POS

    async def get_temperature(self):
        return None


def sparse(stars: int = 5) -> AutofocusResult:
    """A sweep that failed on a sparse field, as the native sweep returns
    one: the focuser put back where it started."""
    return AutofocusResult(False, START_POS, None, [],
                           "r_squared_below_threshold", sparse_field=True,
                           start_stars=stars)


def flat() -> AutofocusResult:
    """A sweep that failed on a rich field: not sparse."""
    return AutofocusResult(False, START_POS, None, [], "not_enough_spread",
                           sparse_field=False, start_stars=400)


def good() -> AutofocusResult:
    return AutofocusResult(True, GOOD_POS, 2.53, [], "ok")


def _plan(count: int = 6) -> SequencePlan:
    """One target, focused as its setup ends (``autofocus_first``), then
    ``count`` frames of L. Nothing else sweeps: ``autofocus_every`` 0 and no
    temperature trigger."""
    t = single(TARGET, count=count)
    t.autofocus_first = True
    return SequencePlan(name="sparse field", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False,
                        park_when_done=False, warm_cooler_when_done=False,
                        recover_guiding=False, targets=[t])


async def _night(hub, monkeypatch, answers, *, stars, plan=None):
    """The night: sweep ``n`` (1-based) answers ``answers(n)``, and light
    frame ``k`` (1-based) reports ``stars(k)`` stars. Returns the night and
    every sweep as ``{"exposure_s", "frames", "detail"}``: its exposure, how
    many light frames had been shot when it began, and the state's detail,
    which is the engine's label for it."""
    sweeps: list[dict] = []
    night = Night(hub, monkeypatch,
                  stars=lambda target, filt: int(stars(len(night.captures))))
    hub.devices["focuser"] = _Focuser()

    async def run_autofocus(cam, foc, **kw):
        sweeps.append({"exposure_s": float(kw["exposure_s"]),
                       "frames": len(night.captures),
                       "detail": night.engine.state.get("detail")})
        return answers(len(sweeps))

    monkeypatch.setattr(engine_mod, "run_autofocus", run_autofocus)
    try:
        night.done = await night.run(plan or _plan())
    finally:
        await night.close()
    return night, sweeps


def _by(results):
    """Sweep ``n`` answers ``results[n - 1]``, and the last one after that."""
    return lambda n: results[min(n, len(results)) - 1]()


async def test_the_retry_doubles_the_exposure(group_hub, monkeypatch):
    """The initial sweep fails on a sparse field and is retried once, at
    twice its exposure, before anything else; the retry works, so the run
    shoots its frames on the focus it found and owes nothing.

    RED under mutant "same exposure" (the retry's ``_sweep(2 *
    exposure_s)`` made ``_sweep(exposure_s)``), observed:

        AssertionError: the retry ran at 2 s after a sweep at 2 s; it
        should run at twice that
        assert 2.0 == (2 * 2.0)

    (The same mutant also turns the owed-sweep case below red, at its
    premise that every sweep was retried at twice its exposure.)
    """
    night, sweeps = await _night(group_hub, monkeypatch, _by([sparse, good]),
                                 stars=lambda k: 50)
    assert night.done, night.lines[-4:]
    assert len(sweeps) == 2 and sweeps[1]["frames"] == 0, (
        f"premise: one retry, before the first frame: {sweeps}")
    first, retry = sweeps[0]["exposure_s"], sweeps[1]["exposure_s"]
    assert retry == 2 * first, (
        f"the retry ran at {retry:g} s after a sweep at {first:g} s; it "
        f"should run at twice that")
    assert night.said("Trying once more at"), night.lines[:6]
    assert len(night.captures) == 6, "premise: the run shot its frames"


async def test_control_a_failure_that_is_not_sparse_is_not_retried(
        group_hub, monkeypatch):
    """CONTROL. The initial sweep fails on a rich field (a flat curve from
    400 stars, a step-size or focuser fault): a longer exposure answers
    nothing there, so it is not retried, the run carries on under "warn" as
    it always did, and no sweep is owed however rich the frames.

    RED under mutant "every failure retried" (the retry's ``and
    getattr(result, "sparse_field", False)`` taken out), observed:

        AssertionError: a failure on a rich field was swept 4 times; it is
        not retried: [{'exposure_s': 2.0, 'frames': 0, 'detail': 'initial
        autofocus'}, {'exposure_s': 4.0, 'frames': 0, 'detail': 'initial
        autofocus'}, {'exposure_s': 2.0, 'frames': 1, 'detail':
        'sparse-field re-sweep'}, {'exposure_s': 4.0, 'frames': 1, 'detail':
        'sparse-field re-sweep'}]

    (Retried, the failed pair then owed a sweep at the first rich frame too,
    and that one was retried in turn.)
    """
    night, sweeps = await _night(group_hub, monkeypatch, _by([flat]),
                                 stars=lambda k: 50)
    assert night.done, night.lines[-4:]
    assert len(sweeps) == 1, (
        f"a failure on a rich field was swept {len(sweeps)} times; it is not "
        f"retried: {sweeps}")
    assert night.said("initial autofocus failed: not_enough_spread")
    assert not night.said("Trying once more at"), night.said("Trying")
    assert len(night.captures) == 6, "premise: the run carried on"


async def test_two_sparse_failures_owe_a_sweep_at_the_first_rich_frame(
        group_hub, monkeypatch):
    """The sweep and its retry both fail on a sparse field. Under "warn" the
    run carries on at the position the sweeps started from and says so in
    words; its first two frames find 14 stars, one under the line, and owe
    nothing yet; the third finds 15, and the next frame boundary sweeps,
    once. That sweep works, so the last three frames, rich as they are, owe
    nothing more.

    RED under mutant "no re-sweep" (the frame loop's ``if
    self._sparse_resweep_due:`` branch made ``if False:``), observed:

        AssertionError: after two sparse-field failures the frame with 15
        stars owed one sweep before the next frame: sweeps [{'exposure_s':
        2.0, 'frames': 0, 'detail': 'initial autofocus'}, {'exposure_s':
        4.0, 'frames': 0, 'detail': 'initial autofocus'}]

    (The same mutant turns the owed-sweep case below red too: "swept 2
    times". And "the line one star low", the next case's mutant, turns this
    one red as well, the sweep coming at the first frame of 14 stars:
    ``assert (3 == 3 and 1 == 3)``.)
    """
    seq = [SPARSE_FIELD_WARN - 1, SPARSE_FIELD_WARN - 1, SPARSE_FIELD_WARN,
           50, 50, 50]
    night, sweeps = await _night(group_hub, monkeypatch,
                                 _by([sparse, sparse, good]),
                                 stars=lambda k: seq[k - 1])
    assert night.done, night.lines[-4:]
    assert [s["frames"] for s in sweeps[:2]] == [0, 0], (
        f"premise: the initial sweep and its retry, before any frame: "
        f"{sweeps}")
    carry = night.said("failed on a sparse field at both exposures")
    assert carry and f"focuser position {START_POS}" in carry[0] and (
        "no sweep has found focus yet this run" in carry[0]), (
        f"the run did not say where it carries on: {carry}")
    assert len(sweeps) == 3 and sweeps[2]["frames"] == 3 and (
        sweeps[2]["detail"] == "sparse-field re-sweep"), (
        f"after two sparse-field failures the frame with "
        f"{SPARSE_FIELD_WARN} stars owed one sweep before the next frame: "
        f"sweeps {sweeps}")
    assert len(night.said("will run at the next frame boundary")) == 1
    assert len(night.captures) == 6, "premise: the run shot its frames"


async def test_control_frames_one_star_short_owe_no_sweep(group_hub,
                                                         monkeypatch):
    """CONTROL. The same two sparse-field failures, and every frame after
    finds 14 stars, one under the line: the sky never shows itself rich
    enough to focus on, so no sweep comes due, and the run shoots its six
    frames at the position it carried on at.

    RED under mutant "the line one star low" (`_note_sparse_resweep`'s
    ``int(stars) < SPARSE_FIELD_WARN`` made ``int(stars) < SPARSE_FIELD_WARN
    - 1``), observed:

        AssertionError: frames of 14 stars, one under the line, owed a
        sweep: [{'exposure_s': 2.0, 'frames': 0, 'detail': 'initial
        autofocus'}, {'exposure_s': 4.0, 'frames': 0, 'detail': 'initial
        autofocus'}, {'exposure_s': 2.0, 'frames': 1, 'detail':
        'sparse-field re-sweep'}]
    """
    night, sweeps = await _night(group_hub, monkeypatch,
                                 _by([sparse, sparse, good]),
                                 stars=lambda k: SPARSE_FIELD_WARN - 1)
    assert night.done, night.lines[-4:]
    assert night.said("failed on a sparse field at both exposures"), (
        "premise: the run carried on after two sparse-field failures")
    assert len(sweeps) == 2, (
        f"frames of {SPARSE_FIELD_WARN - 1} stars, one under the line, owed "
        f"a sweep: {sweeps}")
    assert len(night.captures) == 6, "premise: the run shot its frames"


async def test_the_owed_sweep_failing_again_owes_nothing_more(group_hub,
                                                             monkeypatch):
    """Every sweep of the night fails on a sparse field, and every frame is
    rich: the light frames are longer and finer than a sweep's. The owed
    sweep runs once, at the first frame, is retried at twice the exposure
    as any sweep is, fails, and says no further sweep is owed. Four sweeps
    in all, not a pair of failed sweeps between every two frames until dawn.

    RED under mutant "the owed sweep owes again" (the ``if resweep:`` branch
    of `_carry_on_after_sparse_failures` taken out, so its failure owes the
    next one), observed:

        AssertionError: a night of rich frames on a field every sweep fails
        on swept 12 times; the owed sweep's failure owes nothing more
    """
    night, sweeps = await _night(group_hub, monkeypatch, _by([sparse]),
                                 stars=lambda k: 50)
    assert night.done, night.lines[-4:]
    assert len(sweeps) == 4, (
        f"a night of rich frames on a field every sweep fails on swept "
        f"{len(sweeps)} times; the owed sweep's failure owes nothing more")
    assert [s["frames"] for s in sweeps] == [0, 0, 1, 1], sweeps
    assert [s["exposure_s"] / sweeps[0]["exposure_s"] for s in sweeps] == [
        1, 2, 1, 2], "premise: each sweep was retried at twice its exposure"
    assert night.said("No further sweep is scheduled"), night.lines[-8:]
    assert len(night.captures) == 6, "premise: the run shot its frames"


async def test_under_skip_the_retry_comes_first_then_the_skip(
        group_hub, group_store, monkeypatch):
    """``af_failure_action`` "skip": the sparse-field failure is still
    retried at twice the exposure, and only when the retry fails too does
    the escalation apply, as it always did: the target is skipped unshot,
    and nothing is owed (the run is not carrying on at that position).

    RED under mutant "no retry" (the retry's condition made ``if False:``,
    so the first failure goes straight to the escalation), observed:

        AssertionError: under skip the sparse failure was swept 1 time(s)
        before the target was skipped; the retry comes first: [{'exposure_s':
        2.0, 'frames': 0, 'detail': 'initial autofocus'}]

    (The same mutant turns four more cases here red, each at its premise
    that the retry ran: 5 failed, 5 passed.)
    """
    group_store.set_escalation(EscalationConfig(af_failure_action="skip"))
    night, sweeps = await _night(group_hub, monkeypatch, _by([sparse]),
                                 stars=lambda k: 50)
    assert night.done, night.lines[-4:]
    assert len(sweeps) == 2, (
        f"under skip the sparse failure was swept {len(sweeps)} time(s) "
        f"before the target was skipped; the retry comes first: {sweeps}")
    assert night.said(f"{TARGET}: skipped — autofocus failed"), (
        night.lines[-6:])
    assert night.captures == [], "premise: the skipped target shot nothing"
    assert not night.said("carries on at focuser position"), (
        "a skipped target is not carried on at any position")


async def test_the_carry_on_line_names_the_last_sweep_that_found_focus(
        group_hub, monkeypatch):
    """The initial sweep finds focus at ``GOOD_POS``; the plan's refocus two
    frames later fails on a sparse field, and so does its retry. The line
    says where the run carries on and, beside it, where the last sweep that
    found focus left the focuser, the second number the morning needs.
    Every frame finds 5 stars, so no sweep comes due after.

    Added by the H4-ENG-B verifier: this half of the line was graded by no
    case, since every case above fails its first sweep.

    RED under mutant "no last good focus named" (`_carry_on_after_sparse_
    failures` reading ``good = None`` in place of ``self._last_good_focus``;
    applied in the verifier's own private copy, scratchpad
    ``H4-ENG-B-verify-mut``), observed:

        AssertionError: the run carried on after two sparse-field failures
        without naming the last sweep that found focus, at 11064: ['refocus
        failed on a sparse field at both exposures: the run carries on at
        focuser position 11044, where the sweeps started (no sweep has found
        focus yet this run), and sweeps again at the first frame that finds
        at least 15 stars']
    """
    plan = _plan(count=4)
    plan.autofocus_every = 2
    night, sweeps = await _night(group_hub, monkeypatch,
                                 _by([good, sparse, sparse, good]),
                                 stars=lambda k: 5, plan=plan)
    assert night.done, night.lines[-4:]
    assert [s["frames"] for s in sweeps] == [0, 2, 2], (
        f"premise: the initial sweep, then the refocus and its retry after "
        f"two frames: {sweeps}")
    carry = night.said("failed on a sparse field at both exposures")
    assert len(carry) == 1 and (
        f"the last sweep that found focus left it at {GOOD_POS}"
        in carry[0]), (
        f"the run carried on after two sparse-field failures without naming "
        f"the last sweep that found focus, at {GOOD_POS}: {carry}")
    assert len(night.captures) == 4, "premise: the run shot its frames"


# ============================================ a sweep through luminance

async def test_through_luminance_the_line_names_where_it_carries_on(
        sim_hub, monkeypatch, bus_lines):
    """A run on Ha sweeps through luminance (`_sweep_through_luminance`; on
    the simulator's wheel Ha sits 120 steps from L), and the sweep and its
    retry both fail on a sparse field. Each put the focuser back where it
    started, at L's focus, and putting Ha back afterwards undid the offset:
    the run carries on at Ha's focus, 120 steps from where the sweeps
    started, and the line must name that one.

    Found by the H4-ENG-B verifier: the line named the failed result's
    ``best_position``, the sweeps' start, and so said "carries on at
    focuser position 19080" with the focuser at 19200. This case calls
    `_autofocus` on the real simulator rig, its wheel and focuser, as
    test_the_flip_stops_paying_for_itself.py's luminance cases do; the
    clocked harness above takes both off the rig.

    RED under mutant "the sweeps' start named" (`_carry_on_after_sparse_
    failures`'s ``if restored is not None:`` made ``if False:``, so no
    position is read once the filter is back; applied in the verifier's own
    private copy, scratchpad ``H4-ENG-B-verify-mut``), observed:

        AssertionError: the run carries on at focuser position 19200, Ha's
        focus, and the line said: ['initial autofocus failed on a sparse
        field at both exposures: the run carries on at focuser position
        19080, where the sweeps started (no sweep has found focus yet this
        run), and sweeps again at the first frame that finds at least 15
        stars']
    """
    fw = sim_hub.devices["filterwheel"]
    foc = sim_hub.devices["focuser"]
    fw.filter_narrowband = [False] * 4 + [True] * 3 + [False]
    await fw.set_position(4)                        # Ha
    before = await foc.get_position()
    starts: list[int] = []

    async def run_autofocus(cam, focuser, **kw):
        # A failed native sweep leaves the focuser where it started, and
        # names that position as its result's.
        here = await focuser.get_position()
        starts.append(here)
        return AutofocusResult(False, here, None, [],
                               "r_squared_below_threshold", sparse_field=True,
                               start_stars=5)

    monkeypatch.setattr(engine_mod, "run_autofocus", run_autofocus)
    e = SequenceEngine(sim_hub)
    e.plan = SequencePlan(apply_filter_offsets=True)
    e._cfg = None                                   # af_failure_action "warn"
    assert await e._autofocus("initial autofocus") is False
    assert len(starts) == 2 and starts[0] != before, (
        f"premise: both sweeps ran through luminance, away from Ha's focus "
        f"{before}: {starts}")
    back = (await fw.get_position(), await foc.get_position())
    assert back == (4, before), (
        f"premise: Ha and its focus were put back after the sweeps: {back}")
    carry = [m for _lv, m, _src in bus_lines
             if "failed on a sparse field at both exposures" in m]
    assert len(carry) == 1 and f"focuser position {before}" in carry[0], (
        f"the run carries on at focuser position {before}, Ha's focus, and "
        f"the line said: {carry}")
    assert "ran through luminance" in carry[0], carry[0]
