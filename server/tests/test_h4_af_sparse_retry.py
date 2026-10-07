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
nothing tried one. A later sweep succeeded once the haze eased, and until
then the run imaged at the position the failed sweeps left, with nothing
owed to sweep again.

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
  started and says so in words, and a sweep is owed; "skip" and "abort"
  apply as they always did. The owed sweep's own failure owes nothing more.
* WHEN THE OWED SWEEP FALLS DUE (#558, option 3 of the issue). The ruling
  words it as "the first frame whose star count clears the sparse
  threshold", and as first built the engine compared a LIGHT frame's count
  with ``SPARSE_FIELD_WARN``, the line the native sweep applies to its short
  binned PROBE. A light frame is longer and finer-binned than a probe, so it
  cleared the line at about the first frame whatever the sky was doing. The
  fix compares like with like by measuring the probe itself. The owed sweep
  falls due on a CADENCE (``SPARSE_RESWEEP_EVERY_S``, 600 s, a light frame
  having just banked), and runs as a gated sweep: ``run_autofocus`` is given
  ``min_probe_stars=SPARSE_FIELD_WARN``, the native sweep takes its usual
  probe and, when it counts fewer stars on a frame that is not clipped,
  declines to sweep and says so (``AutofocusResult.gated``), the focuser put
  back. The engine then keeps the debt, schedules the next probe one cadence
  on, and neither retries at twice the exposure nor applies
  ``af_failure_action``: nothing was swept, so nothing failed. A probe that
  clears the line sweeps as any sweep does and clears the debt. A light
  frame's own star count decides nothing any more.
  The native sweep's cases (the next group) run the real sweep with the
  detector seam scripted. The engine's cases below run the real
  `run_autofocus` and the real native sweep on the simulator's camera and
  focuser, with the PROBE's count scripted per attempt (the old cases below
  could only script a light frame's count, which is why the mismatch could
  not show), and a few seam cases script ``run_autofocus`` itself.

THE ENGINE CASES run the real `_run_scheduled`, `_setup_target`, `_run_step`
and `_autofocus` on the clocked simulator (tests/_group_harness.py). The
harness takes the focuser off the rig, so each case gives it back a focuser
the engine can read (test_group_plain_stop_defers.py's shape) and scripts
``run_autofocus``, the one call below the engine, by the sweep's number: the
engine's retry, its escalation and its frame loop are what is graded. Each
light frame's star count is a script too, by the frame's number.

Every mutant of the H4 cases was applied in a private copy of ``server/``
(scratchpad ``H4-ENG-B-mut``), from a byte backup restored and sha256-checked
after each, never in the shared tree (#254). The #558 mutants (WP-102) were
applied in this worktree's own files from a byte backup of the file,
restored and sha256-compared after each, the mutant's text grepped gone;
the H4 cases whose observed output the #558 change moved were re-run the
same way and re-quoted. The observed failure is quoted verbatim (the first
assertion line, long lines wrapped).
"""
from __future__ import annotations

import pytest

import astrodeck.focus.native as N
import astrodeck.sequence.engine as engine_mod
from _group_harness import Night, group_hub, group_store, single  # noqa: F401
from _simhub import sim_hub  # noqa: F401 (fixture import)
from astrodeck import providers
from astrodeck.config import EscalationConfig, config_store
from astrodeck.devices.sim import SimFocuser, build_sim_rig
from astrodeck.events import bus
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
#: How often the owed sweep falls due (#558), as the engine sets it.
SPARSE_RESWEEP_EVERY_S = engine_mod.SPARSE_RESWEEP_EVERY_S


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


# ================================================ the probe gate (#558)

def _focus_events(q) -> list[dict]:
    """The ``focus`` events a subscriber has queued, oldest first."""
    out = []
    while not q.empty():
        ev = q.get_nowait()
        if ev.type == "focus":
            out.append(ev.data)
    return out


def _counting_metric(measured: list):
    """``native_sweep_metric`` scripted to a flat, thin point (nothing to
    fit), recording each call: a sweep that measured a point called it."""
    def metric(data):
        measured.append(1)
        return 5.0, 1
    return metric


@native_only
@pytest.mark.parametrize("probe", [SPARSE_FIELD_WARN - 1, 8, 2])
async def test_a_probe_under_the_gate_declines_to_sweep(monkeypatch, probe):
    """The sweep is given ``min_probe_stars`` (the owed re-sweep's line) and
    its probe counts fewer stars than that, on a frame that is not clipped:
    it does not sweep. The result says so (``gated``), the focuser is back
    where it started, no sweep point was measured, and the panel is told in
    a message that is not a failure (state "idle", never "failed"). Three
    probes: one under the line, a typical 8, and 2, which is under
    ``MIN_STARS_TO_SWEEP`` as well, so it would otherwise be the plain
    refusal the engine treats as any failed sweep (and retries at twice the
    exposure).

    RED under mutant "min_probe_stars ignored" (`run_native_autofocus`
    taking ``min_probe_stars = None`` first thing, so no line is applied),
    observed, for probes 14, 8 and 2 (the last is refused as the ordinary
    hopeless field, which is a plain failure and not a gated one):

        AssertionError: a probe of 14 stars under the line of 15 did not
        decline: gated=False, success=False, 6 sweep point(s) measured
        AssertionError: a probe of 8 stars under the line of 15 did not
        decline: gated=False, success=False, 6 sweep point(s) measured
        AssertionError: a probe of 2 stars under the line of 15 did not
        decline: gated=False, success=False, 0 sweep point(s) measured
    """
    cam, foc = await _connected_sim()
    start = await foc.get_position()
    measured: list[int] = []
    monkeypatch.setattr(N._native, "detect_and_measure",
                        _probe_then_points(probe))
    monkeypatch.setattr(N, "native_sweep_metric", _counting_metric(measured))
    q = bus.subscribe()
    try:
        res = await N.run_native_autofocus(
            cam, foc, exposure_s=0.05, gain=200, step=350, steps_each_side=4,
            binning=2, min_probe_stars=SPARSE_FIELD_WARN)
    finally:
        bus.unsubscribe(q)
    events = _focus_events(q)
    assert res.gated is True and res.success is False and not measured, (
        f"a probe of {probe} stars under the line of {SPARSE_FIELD_WARN} did "
        f"not decline: gated={res.gated}, success={res.success}, "
        f"{len(measured)} sweep point(s) measured")
    assert res.sparse_field is True and res.start_stars == probe, (
        res.sparse_field, res.start_stars)
    assert await foc.get_position() == start, (
        "a declined probe left the focuser away from where it started")
    assert res.message.startswith(f"only {probe} stars") and (
        str(SPARSE_FIELD_WARN) in res.message), res.message
    states = [e.get("state") for e in events]
    assert "failed" not in states, (
        f"a declined probe was published as a failure: {events}")
    assert events[-1].get("state") == "idle" and (
        events[-1].get("message") == res.message), events[-1]


@native_only
@pytest.mark.parametrize("min_stars, probe", [
    pytest.param(None, 8, id="no line asked: the initial autofocus"),
    pytest.param(SPARSE_FIELD_WARN, SPARSE_FIELD_WARN, id="at the line"),
    pytest.param(SPARSE_FIELD_WARN, 20, id="over the line"),
])
async def test_control_a_probe_that_clears_the_gate_sweeps(monkeypatch,
                                                           min_stars, probe):
    """CONTROL. The same sweep with no line asked (the initial autofocus and
    every plan refocus pass none: 8 stars is a sparse field it warns about
    and sweeps), with the probe AT the line (15, not under it), and over it:
    each goes on to measure its sweep points and is not gated. (They fail
    here, on the scripted thin points; that is not what is graded.)

    RED under mutant "the line one star high" (the gate's ``n0 <
    min_probe_stars`` made ``n0 <= min_probe_stars``), observed, the
    "at the line" case:

        AssertionError: a probe of 15 stars with min_probe_stars=15 was
        gated, or never measured a point: gated=True, 0 sweep point(s)
        measured
    """
    cam, foc = await _connected_sim()
    measured: list[int] = []
    monkeypatch.setattr(N._native, "detect_and_measure",
                        _probe_then_points(probe))
    monkeypatch.setattr(N, "native_sweep_metric", _counting_metric(measured))
    res = await N.run_native_autofocus(
        cam, foc, exposure_s=0.05, gain=200, step=350, steps_each_side=4,
        binning=2, min_probe_stars=min_stars)
    assert res.gated is False and measured, (
        f"a probe of {probe} stars with min_probe_stars={min_stars} was "
        f"gated, or never measured a point: gated={res.gated}, "
        f"{len(measured)} sweep point(s) measured")


@native_only
async def test_control_a_clipped_probe_is_never_gated(monkeypatch):
    """CONTROL. Eight stars on a frame that is overexposed: the "few stars"
    are merged ones, not missing ones, and declining to sweep would wait
    for a sky that is not the problem (a clipped probe is the operator's to
    fix, which the sweep's own warning already says). So the gate does not
    engage, and the sweep goes on exactly as it does with no line.

    RED under mutant "clipping ignored by the gate" (the gate's ``and
    probe_sat < OVEREXPOSED_FRAC`` taken out), observed:

        AssertionError: a clipped probe of 8 stars was gated: gated=True,
        0 sweep point(s) measured
    """
    cam, foc = await _connected_sim()
    measured: list[int] = []
    monkeypatch.setattr(N._native, "detect_and_measure",
                        _probe_then_points(8))
    monkeypatch.setattr(N, "native_sweep_metric", _counting_metric(measured))
    monkeypatch.setattr(N, "saturation_fraction", lambda data: 0.5)
    res = await N.run_native_autofocus(
        cam, foc, exposure_s=0.05, gain=200, step=350, steps_each_side=4,
        binning=2, min_probe_stars=SPARSE_FIELD_WARN)
    assert res.gated is False and measured, (
        f"a clipped probe of 8 stars was gated: gated={res.gated}, "
        f"{len(measured)} sweep point(s) measured")


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


def _plan(count: int = 6, exposure_s: float = 30.0) -> SequencePlan:
    """One target, focused as its setup ends (``autofocus_first``), then
    ``count`` frames of L, ``exposure_s`` each (the owed sweep falls due on
    a cadence of ``SPARSE_RESWEEP_EVERY_S``, so a case about it needs frames
    long enough to span some). Nothing else sweeps: ``autofocus_every`` 0 and
    no temperature trigger."""
    t = single(TARGET, count=count)
    t.steps[0].exposure_s = float(exposure_s)
    t.autofocus_first = True
    return SequencePlan(name="sparse field", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False,
                        park_when_done=False, warm_cooler_when_done=False,
                        recover_guiding=False, targets=[t])


class _Sweep(dict):
    """One scripted sweep's record, printed compactly: a failure message
    that lists five of them must stay readable."""

    def __repr__(self) -> str:
        gate = (f", gated on {self['min_probe_stars']}"
                if self["min_probe_stars"] is not None else "")
        return (f"<{self['detail']} at {self['exposure_s']:g} s after "
                f"{self['frames']} frame(s), t={self['at_s']:g}{gate}>")


async def _night(hub, monkeypatch, answers, *, stars, plan=None):
    """The night: sweep ``n`` (1-based) answers ``answers(n)``, and light
    frame ``k`` (1-based) reports ``stars(k)`` stars. Returns the night and
    every sweep as ``{"exposure_s", "frames", "detail", "at_s",
    "min_probe_stars"}``: its exposure, how many light frames had been shot
    when it began, the state's detail, which is the engine's label for it,
    the night's clock then (seconds from its start), and the line it was
    given for its probe, None for a sweep that was not gated. The scripted
    ``run_autofocus`` is a provider that CANNOT gate (it answers by sweep
    number and ignores the line), the case the engine bounds by "its own
    failure owes nothing more"."""
    sweeps: list[dict] = []
    night = Night(hub, monkeypatch,
                  stars=lambda target, filt: int(stars(len(night.captures))))
    hub.devices["focuser"] = _Focuser()

    async def run_autofocus(cam, foc, **kw):
        sweeps.append(_Sweep({"exposure_s": float(kw["exposure_s"]),
                              "frames": len(night.captures),
                              "detail": night.engine.state.get("detail"),
                              "at_s": night.rel(night.clock.t),
                              "min_probe_stars": kw.get("min_probe_stars")}))
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

        AssertionError: a failure on a rich field was swept 2 times; it is
        not retried: [<initial autofocus at 2 s after 0 frame(s), t=0>,
        <initial autofocus at 4 s after 0 frame(s), t=0>]

    (Retried, the failed pair owes a sweep too, which these 180 s of
    frames never reach.)
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


async def test_two_sparse_failures_owe_a_sweep_one_cadence_on(
        group_hub, monkeypatch):
    """The sweep and its retry both fail on a sparse field. Under "warn" the
    run carries on at the position the sweeps started from and says so in
    words. Every frame is rich (100 stars, as a light frame is against a
    probe), which used to make the owed sweep fall due at the first one
    (#558). It falls due on a CADENCE now, ``SPARSE_RESWEEP_EVERY_S`` after
    the failures: with 300 s frames the first owes nothing, and the next
    frame boundary after the second (600 s on) runs the owed sweep, once,
    gated on its probe. That sweep works, so the frames after it owe
    nothing more.

    RED under mutant "due on every frame" (`_note_sparse_resweep`'s ``if
    nxt is not None and time.monotonic() < nxt: return`` taken out, so the
    first light frame to bank makes the sweep due), observed:

        AssertionError: after two sparse-field failures the owed sweep was
        due after 600 s, the second 300 s frame, and not before: sweeps
        [<initial autofocus at 2 s after 0 frame(s), t=0>, <initial
        autofocus at 4 s after 0 frame(s), t=0>, <sparse-field re-sweep at
        2 s after 1 frame(s), t=300, gated on 15>]
        assert (3 == 3 and 1 == 2)

    RED under mutant "no re-sweep" (the frame loop's ``if
    self._sparse_resweep_due:`` branch made ``if False:``), observed:

        AssertionError: after two sparse-field failures the owed sweep was
        due after 600 s, the second 300 s frame, and not before: sweeps
        [<initial autofocus at 2 s after 0 frame(s), t=0>, <initial
        autofocus at 4 s after 0 frame(s), t=0>]
        assert (2 == 3)
    """
    night, sweeps = await _night(group_hub, monkeypatch,
                                 _by([sparse, sparse, good]),
                                 stars=lambda k: 100,
                                 plan=_plan(exposure_s=300.0))
    assert night.done, night.lines[-4:]
    assert [s["frames"] for s in sweeps[:2]] == [0, 0], (
        f"premise: the initial sweep and its retry, before any frame: "
        f"{sweeps}")
    carry = night.said("failed on a sparse field at both exposures")
    assert carry and f"focuser position {START_POS}" in carry[0] and (
        "no sweep has found focus yet this run" in carry[0]), (
        f"the run did not say where it carries on: {carry}")
    assert len(sweeps) == 3 and sweeps[2]["frames"] == 2 and (
        sweeps[2]["detail"] == "sparse-field re-sweep"), (
        f"after two sparse-field failures the owed sweep was due after "
        f"{SPARSE_RESWEEP_EVERY_S:g} s, the second 300 s frame, and not "
        f"before: sweeps {sweeps}")
    assert sweeps[2]["at_s"] - sweeps[1]["at_s"] >= SPARSE_RESWEEP_EVERY_S, (
        f"the owed sweep ran {sweeps[2]['at_s'] - sweeps[1]['at_s']:g} s "
        f"after the failures, under the {SPARSE_RESWEEP_EVERY_S:g} s "
        f"cadence: {sweeps}")
    assert [s["min_probe_stars"] for s in sweeps] == [
        None, None, SPARSE_FIELD_WARN], (
        f"only the owed sweep is gated, on the line the native sweep sets "
        f"for its probe: {sweeps}")
    assert len(night.said("will take a probe at the next frame boundary")) == 1
    assert len(night.captures) == 6, "premise: the run shot its frames"


async def test_a_light_frames_star_count_decides_nothing(group_hub,
                                                        monkeypatch):
    """CONTROL, and the heart of #558. Every frame finds 14 stars, one
    under the line, the count that used to keep the owed sweep from ever
    falling due, and the sweep falls due on the same cadence as it does on
    a rich sky: the light frame's count is not what the probe's line was
    set for, and the probe, not the frame, is what is asked.

    RED under mutant "the light frame's count restored" (`_note_sparse_
    resweep` given back its ``info`` and the old ``int(stars) <
    SPARSE_FIELD_WARN: return``, its caller passing ``info``; a two-line
    mutant, since the parameter is gone), observed:

        AssertionError: frames of 14 stars, one under the line, kept the
        owed sweep from falling due on its cadence: [<initial autofocus at
        2 s after 0 frame(s), t=0>, <initial autofocus at 4 s after 0
        frame(s), t=0>]
        assert (2 == 3)
    """
    night, sweeps = await _night(group_hub, monkeypatch,
                                 _by([sparse, sparse, good]),
                                 stars=lambda k: SPARSE_FIELD_WARN - 1,
                                 plan=_plan(exposure_s=300.0))
    assert night.done, night.lines[-4:]
    assert night.said("failed on a sparse field at both exposures"), (
        "premise: the run carried on after two sparse-field failures")
    assert len(sweeps) == 3 and sweeps[2]["frames"] == 2, (
        f"frames of {SPARSE_FIELD_WARN - 1} stars, one under the line, "
        f"kept the owed sweep from falling due on its cadence: {sweeps}")
    assert len(night.captures) == 6, "premise: the run shot its frames"


async def test_the_owed_sweep_failing_again_owes_nothing_more(group_hub,
                                                             monkeypatch):
    """Every sweep of the night fails on a sparse field, from a provider
    that cannot gate (a backend's own autofocus, the legacy sweep: the
    scripted ``run_autofocus`` ignores the line). The owed sweep falls due
    on its cadence, runs once, is retried at twice the exposure as any sweep
    is, fails, and says no further sweep is owed. Four sweeps in all, not a
    pair of failed sweeps every ten minutes until dawn.

    RED under mutant "the owed sweep owes again" (the ``if resweep:`` branch
    of `_carry_on_after_sparse_failures` taken out, so its failure owes the
    next one), observed:

        AssertionError: a night on a field every sweep fails on swept 6
        times; the owed sweep's failure owes nothing more
    """
    night, sweeps = await _night(group_hub, monkeypatch, _by([sparse]),
                                 stars=lambda k: 50,
                                 plan=_plan(exposure_s=300.0))
    assert night.done, night.lines[-4:]
    assert len(sweeps) == 4, (
        f"a night on a field every sweep fails on swept {len(sweeps)} times; "
        f"the owed sweep's failure owes nothing more")
    assert [s["frames"] for s in sweeps] == [0, 0, 2, 2], sweeps
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
        before the target was skipped; the retry comes first: [<initial
        autofocus at 2 s after 0 frame(s), t=0>]

    (The same mutant turns nine more cases red, most at their premise
    that the retry ran: 10 failed, 15 passed in this file.)
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
        focus yet this run), and probes the field again every 10 minutes
        (the focus scope's own exposure), sweeping at the first probe that
        finds at least 15 stars']
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
        run), and probes the field again every 10 minutes (the focus scope's
        own exposure), sweeping at the first probe that finds at least 15
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


# ======================= the owed sweep's probe, through the engine (#558)

async def _native_night(hub, monkeypatch, probes, *, plan, clears=(),
                        stars=lambda k: 100):
    """A night on the REAL ``run_autofocus`` and the real native sweep, on
    the simulator's camera and a simulator focuser of the hub's own rig,
    with the PROBE's star count scripted per attempt: attempt ``n``
    (1-based, the initial sweep, its retry, then every owed one) probes
    ``probes(n)`` stars. The old cases above script a light frame's count
    or the sweep's verdict, so they cannot say whether the engine asked the
    sweep the right question; these can, since the question is what reaches
    the sweep.

    An attempt in ``clears`` measures a clean V-curve at every point (focus
    150 steps above where it started), so a sweep that runs finds focus; any
    other measures one thin star a point, so a sweep that runs fails.
    Returns the night and every attempt as ``{"n", "exposure_s", "frames",
    "at_s", "min_probe_stars", "start", "end", "probe", "measured",
    "result"}``: the exposure it was given, the light frames shot and the
    night's clock when it began, the line it was given, where the focuser
    stood before and after, the probe's count, the positions of the sweep
    points it MEASURED (empty for a probe that declined), and its result.
    """
    foc = SimFocuser(hub.sim_rig)
    await foc.connect()
    hub.devices["focuser"] = foc
    real_run = engine_mod.run_autofocus
    attempts: list[dict] = []
    night = Night(hub, monkeypatch,
                  stars=lambda target, filt: int(stars(len(night.captures))))

    async def run_autofocus(cam, f, **kw):
        n = len(attempts) + 1
        start = int(await f.get_position())
        rec = {"n": n, "exposure_s": float(kw["exposure_s"]),
               "frames": len(night.captures),
               "at_s": night.rel(night.clock.t),
               "min_probe_stars": kw.get("min_probe_stars"),
               "start": start, "probe": int(probes(n)), "measured": []}
        attempts.append(rec)
        monkeypatch.setattr(N._native, "detect_and_measure",
                            _probe_then_points(rec["probe"]))

        def metric(frame):
            at = int(frame.focuser_position or 0)
            rec["measured"].append(at)
            if n in clears:
                return 2.0 + abs(at - (start + 150)) / 150.0, 40
            return 5.0, 1
        monkeypatch.setattr(N, "native_sweep_metric", metric)
        res = await real_run(cam, f, **kw)
        rec["end"] = int(await f.get_position())
        rec["result"] = res
        return res

    monkeypatch.setattr(engine_mod, "run_autofocus", run_autofocus)
    try:
        night.done = await night.run(plan)
    finally:
        await night.close()
    return night, attempts


@native_only
async def test_a_starved_probe_is_declined_once_a_cadence_and_never_swept(
        group_hub, monkeypatch):
    """THE NIGHT OF #558. Every light frame finds 100 stars, the sky the
    sparse-field sweeps failed under is as thin as it was (the probe counts
    5, then 8), and the run owes a sweep. No sweep runs: each probe is one
    exposure, declines, and puts the focuser back; one comes per
    ``SPARSE_RESWEEP_EVERY_S`` of the night, at the frame boundary after
    each ten minutes (300 s frames: after frames 2, 4, 6 and 8, none after
    the last); the debt stays owed; nothing is retried at twice the
    exposure. It used to sweep at the first light frame whatever the sky
    was doing.

    RED under mutant "min_probe_stars ignored" (`run_native_autofocus`
    taking ``min_probe_stars = None`` first thing, so the probe's line is
    never applied), observed:

        AssertionError: a field that counts 8 stars on its probe was swept
        by attempt(s) [3, 4]: [(3, 2), (4, 2)]

    RED under mutant "cadence ignored, due on every frame" (`_note_sparse_
    resweep`'s ``if nxt is not None and time.monotonic() < nxt: return``
    taken out), observed:

        AssertionError: one probe per 600 s of 300 s frames, at the
        boundary after frames 2, 4, 6 and 8; the probes ran after frames [1,
        2, 3, 4, 5, 6, 7, 8, 9] (9 probes in 3000 s)

    RED under mutant "the debt not kept" (the gated branch of `_autofocus`
    without its ``self._sparse_resweep_owed = True``), observed:

        AssertionError: one probe per 600 s of 300 s frames, at the
        boundary after frames 2, 4, 6 and 8; the probes ran after frames [2]
        (1 probes in 3000 s)

    RED under mutant "the next probe not scheduled" (the gated branch
    without its ``self._sparse_resweep_next = ...``, so the cadence stays
    where the last probe left it and every frame owes one), observed:

        AssertionError: one probe per 600 s of 300 s frames, at the
        boundary after frames 2, 4, 6 and 8; the probes ran after frames [2,
        3, 4, 5, 6, 7, 8, 9] (8 probes in 3000 s)

    RED under mutant "the line not passed" (`_autofocus`'s ``kw = (... if
    gate else {})`` made ``kw = {}``), observed:

        AssertionError: a field that counts 8 stars on its probe was swept
        by attempt(s) [3, 4]: [(3, 2), (4, 2)]

    RED under mutant "the line not threaded" (`run_autofocus`' call to
    `run_native_autofocus` without ``min_probe_stars=min_probe_stars``),
    observed:

        AssertionError: a field that counts 8 stars on its probe was swept
        by attempt(s) [3, 4]: [(3, 2), (4, 2)]

    RED under mutant "a probe that is declined is retried at twice the
    exposure" (the ``if getattr(result, "gated", False):`` branch of
    `_autofocus` made ``if False:``, so the declined probe goes the way of
    any sweep that failed on a sparse field), observed:

        AssertionError: a field that counts 8 stars on its probe was swept
        by attempt(s) [4]: [(3, 2), (4, 2)]

    (Attempt 4 is the retry at twice the exposure, ungated, and it sweeps.)
    """
    night, attempts = await _native_night(
        group_hub, monkeypatch, lambda n: 5 if n <= 2 else 8,
        plan=_plan(count=10, exposure_s=300.0), stars=lambda k: 100)
    assert night.done, night.lines[-4:]
    initial, probes = attempts[:2], attempts[2:]
    assert [(a["min_probe_stars"], a["frames"]) for a in initial] == [
        (None, 0), (None, 0)] and (
        initial[1]["exposure_s"] == 2 * initial[0]["exposure_s"]) and all(
        a["measured"] for a in initial), (
        f"premise: the initial sweep and its retry at twice the exposure "
        f"ran, ungated, and measured points: {initial}")
    swept = [a["n"] for a in probes if a["measured"]]
    assert not swept, (
        f"a field that counts {probes[0]['probe']} stars on its probe was "
        f"swept by attempt(s) {swept}: {[(a['n'], a['frames']) for a in probes]}")
    assert [a["frames"] for a in probes] == [2, 4, 6, 8], (
        f"one probe per {SPARSE_RESWEEP_EVERY_S:g} s of 300 s frames, at "
        f"the boundary after frames 2, 4, 6 and 8; the probes ran after "
        f"frames {[a['frames'] for a in probes]} "
        f"({len(probes)} probes in {len(night.captures) * 300} s)")
    held = [b["at_s"] - a["at_s"] for a, b in zip(attempts[1:], probes)]
    assert all(h >= SPARSE_RESWEEP_EVERY_S for h in held), (
        f"probes {held} s apart, under the {SPARSE_RESWEEP_EVERY_S:g} s "
        f"cadence")
    assert all(a["min_probe_stars"] == SPARSE_FIELD_WARN and a["end"] == (
        a["start"]) and a["result"].gated and (
        a["exposure_s"] == initial[0]["exposure_s"]) for a in probes), (
        f"every probe is gated on the sparse line, declines, puts the "
        f"focuser back and is not retried at twice the exposure: "
        f"{[(a['min_probe_stars'], a['start'], a['end'], a['exposure_s']) for a in probes]}")
    assert night.engine._sparse_resweep_owed is True, (
        "a probe that declined left the debt owed")
    assert not night.said("sparse-field re-sweep failed"), (
        "a declined probe is not a failure")
    said = night.said("the probe counted 8 stars")
    assert 1 <= len(said) < len(probes), (
        f"the verdict is said once and then at most every "
        f"{engine_mod.SPARSE_RESWEEP_LOG_EVERY_S / 60:g} min, not per "
        f"probe: {len(said)} lines for {len(probes)} probes")
    assert len(night.captures) == 10, "premise: the run shot its frames"


@native_only
async def test_a_probe_that_clears_the_gate_sweeps_and_clears_the_debt(
        group_hub, monkeypatch):
    """The same night, and the sky comes back: the first probe (the third
    attempt) counts 8 and declines, the second counts 20, which clears the
    line, so it SWEEPS, as any sweep does, finds focus and clears the debt.
    Four attempts in all, and none after it: ten more minutes and a fifth
    light frame owe nothing.

    RED under mutant "a probe that clears is still declined" (the gate's
    ``n0 < floor`` made ``n0 < floor + 100``), observed:

        AssertionError: the initial sweep and its retry, a probe that
        declined after frame 2 and one that cleared after frame 4, and
        nothing after: [(1, 0, 5), (2, 0, 5), (3, 2, 8), (4, 4, 20), (5, 6,
        20), (6, 8, 20)]
        assert [(0, None), (... 15), (8, 15)] == [(0, None), (... 15), (4, 15)]
    """
    night, attempts = await _native_night(
        group_hub, monkeypatch, lambda n: {1: 5, 2: 5, 3: 8}.get(n, 20),
        plan=_plan(count=10, exposure_s=300.0), clears={4})
    assert night.done, night.lines[-4:]
    assert [(a["frames"], a["min_probe_stars"]) for a in attempts] == [
        (0, None), (0, None), (2, SPARSE_FIELD_WARN), (4, SPARSE_FIELD_WARN)], (
        f"the initial sweep and its retry, a probe that declined after "
        f"frame 2 and one that cleared after frame 4, and nothing after: "
        f"{[(a['n'], a['frames'], a['probe']) for a in attempts]}")
    declined, swept = attempts[2], attempts[3]
    assert declined["result"].gated and not declined["measured"], (
        "premise: the probe of 8 declined")
    assert swept["measured"] and swept["result"].success and not (
        swept["result"].gated), (
        f"the probe of 20 cleared the line and swept: "
        f"{swept['result'].message!r}, {len(swept['measured'])} points")
    eng = night.engine
    assert eng._sparse_resweep_owed is False and eng._last_good_focus, (
        f"the sweep that ran answered the debt: owed="
        f"{eng._sparse_resweep_owed}, good focus {eng._last_good_focus}")
    assert not night.said("sparse-field re-sweep failed"), night.lines[-6:]
    assert len(night.captures) == 10, "premise: the run shot its frames"


# ------------------------------------------- a declined probe, at the seam

def _gated(stars: int = 8) -> AutofocusResult:
    """What the native sweep returns for a probe that did not clear the
    line it was given: not a success, sparse, ``gated``."""
    return AutofocusResult(False, START_POS, None, [],
                           f"only {stars} stars at the current focus, under "
                           f"the {SPARSE_FIELD_WARN} the re-sweep waits for; "
                           f"not sweeping yet",
                           sparse_field=True, start_stars=stars, gated=True)


class _Clock:
    """The engine's ``time`` for a case that moves it by hand: ``monotonic``
    and ``time`` read the real clock PLUS ``offset``, everything else is the
    real module. Real time keeps running, as a frozen clock would turn any
    wait the engine polls for (a wheel move, a focuser move) into a hang;
    a case sets ``offset`` to jump the night forward."""

    def __init__(self, real):
        self._real, self.offset = real, 0.0

    def monotonic(self) -> float:
        return self._real.monotonic() + self.offset

    def time(self) -> float:
        return self._real.time() + self.offset

    def __getattr__(self, name):
        return getattr(self._real, name)


async def test_a_declined_probe_is_not_a_failure(sim_hub, monkeypatch,
                                                 bus_lines):
    """The owed sweep's probe declines, under ``af_failure_action``
    "abort" and with the wheel on Ha (the sweep runs through luminance).
    Nothing was swept, so nothing failed: it returns False without
    raising, the sweep ran once and was given the sparse line, it is not
    retried at twice the exposure, the debt is owed again with the next
    probe one cadence on, none of the focus bookkeeping moved (`_last_
    focus_at`, `_frames_since_focus`), the cost of the probe stayed out of
    the ETA's autofocus cost, and Ha and its focus are back.

    RED under mutant "a declined probe treated as a failure" (the ``if
    getattr(result, "gated", False):`` branch of `_autofocus` made ``if
    False:``, so the result goes the way of any sweep that failed on a
    sparse field), observed:

        Failed: a declined probe was escalated through af_failure_action:
        autofocus failed: only 8 stars at the current focus, under the 15
        the re-sweep waits for; not sweeping yet

    RED under mutant "a declined probe costed as a sweep" (the gated
    branch's ``return False`` preceded by ``self._record_event_cost(
    "autofocus", 1.0)``), observed:

        AssertionError: a declined probe was costed as an autofocus:
        ['autofocus']

    RED under mutant "a declined probe resets the focus clock" (the gated
    branch preceded by ``self._frames_since_focus = 0``), observed:

        AssertionError: a declined probe moved the focus bookkeeping:
        (4321.0, 0)
    """
    fw = sim_hub.devices["filterwheel"]
    foc = sim_hub.devices["focuser"]
    fw.filter_narrowband = [False] * 4 + [True] * 3 + [False]
    await fw.set_position(4)                        # Ha
    before = await foc.get_position()
    clock = _Clock(engine_mod.time)
    monkeypatch.setattr(engine_mod, "time", clock)
    calls: list[dict] = []

    async def run_autofocus(cam, focuser, **kw):
        calls.append(kw)
        return _gated()

    monkeypatch.setattr(engine_mod, "run_autofocus", run_autofocus)
    e = SequenceEngine(sim_hub)
    e.plan = SequencePlan(apply_filter_offsets=True)
    # The real config, with the escalation turned up: the run reads the
    # rest of it (`_sweep_through_luminance` reads its focus section).
    config_store.set_escalation(EscalationConfig(af_failure_action="abort"))
    e._cfg = config_store.cfg()
    costs: list[str] = []
    monkeypatch.setattr(e, "_record_event_cost",
                        lambda kind, seconds: costs.append(kind))
    e._last_focus_at = 4321.0
    e._frames_since_focus = 7
    e._sparse_resweep_owed = True
    e._sparse_resweep_due = True

    try:
        ok = await e._autofocus("sparse-field re-sweep", resweep=True)
    except engine_mod.SafetyAbort as ex:
        pytest.fail(f"a declined probe was escalated through "
                    f"af_failure_action: {ex}")

    assert ok is False and len(calls) == 1, (
        f"a declined probe returned {ok} after {len(calls)} sweep(s); it "
        f"is neither retried nor escalated")
    assert calls[0].get("min_probe_stars") == SPARSE_FIELD_WARN, (
        f"the owed sweep was not given the sparse line: "
        f"{calls[0].get('min_probe_stars', 'absent')}")
    assert e._sparse_resweep_owed is True and e._sparse_resweep_due is False, (
        f"the debt is owed again and not yet due: owed="
        f"{e._sparse_resweep_owed}, due={e._sparse_resweep_due}")
    nxt = e._sparse_resweep_next
    ahead = None if nxt is None else nxt - clock.monotonic()
    assert ahead is not None and 0 <= SPARSE_RESWEEP_EVERY_S - ahead < 30, (
        f"the next probe is one cadence on, {SPARSE_RESWEEP_EVERY_S:g} s; "
        f"it is {ahead} s on")
    assert (e._last_focus_at, e._frames_since_focus) == (4321.0, 7), (
        f"a declined probe moved the focus bookkeeping: "
        f"{(e._last_focus_at, e._frames_since_focus)}")
    assert costs == [], (
        f"a declined probe was costed as an autofocus: {costs}")
    back = (await fw.get_position(), await foc.get_position())
    assert back == (4, before), (
        f"Ha and its focus were not put back after the declined probe: "
        f"{back}")
    said = [m for _lv, m, _src in bus_lines if "the probe counted" in m]
    assert len(said) == 1 and "8 stars" in said[0], said


async def test_the_gate_is_asked_of_the_owed_sweep_alone(sim_hub,
                                                         monkeypatch):
    """The initial autofocus and a plan refocus pass NO line: they must
    sweep a sparse field (that is what the retry at twice the exposure is
    for), and a stand-in for ``run_autofocus`` that predates the keyword
    must keep working. The owed sweep passes the line on its first attempt
    only; when a provider that cannot gate fails it on a sparse field, the
    retry at twice the exposure is a plain sweep.

    RED under mutant "every sweep gated" (`_autofocus`'s ``kw = (... if
    gate else {})`` made ``kw = {"min_probe_stars": SPARSE_FIELD_WARN}``),
    observed:

        AssertionError: the initial autofocus and its retry must pass no
        line: [15, 15]
        assert [15, 15] == ['absent', 'absent']
    """
    calls: list[dict] = []

    async def run_autofocus(cam, focuser, **kw):
        calls.append(kw)
        return sparse()

    monkeypatch.setattr(engine_mod, "run_autofocus", run_autofocus)
    e = SequenceEngine(sim_hub)
    e.plan = SequencePlan()
    e._cfg = None
    def lines() -> list:
        return [kw.get("min_probe_stars", "absent") for kw in calls]

    await e._autofocus("initial autofocus")
    assert lines() == ["absent", "absent"], (
        f"the initial autofocus and its retry must pass no line: {lines()}")
    calls.clear()
    await e._autofocus("refocus")
    assert lines() == ["absent", "absent"], (
        f"a plan refocus and its retry must pass no line: {lines()}")
    calls.clear()
    e._sparse_resweep_owed = True
    await e._autofocus("sparse-field re-sweep", resweep=True)
    assert lines() == [SPARSE_FIELD_WARN, "absent"], (
        f"the owed sweep passes the line on its first attempt and its "
        f"retry at twice the exposure passes none: {lines()}")


async def test_a_declined_probe_is_said_once_then_every_half_hour(
        sim_hub, monkeypatch, bus_lines):
    """The verdict on a probe that declined is said the first time and then
    at most every ``SPARSE_RESWEEP_LOG_EVERY_S``: a thin sky can hold for
    hours, and one sentence every ten minutes would bury it (the native
    sweep logs each probe's own count). Probes at 0, 10, 20 and 29 minutes
    say it once; the one at 30 minutes says it again.

    RED under mutant "every probe said" (`_say_probe_declined`'s ``if last
    is not None and now - last < SPARSE_RESWEEP_LOG_EVERY_S: return``
    taken out), observed:

        AssertionError: the verdict, counted after probes at 0, 10, 20, 29
        and 30 minutes: [1, 2, 3, 4, 5]
        assert [1, 2, 3, 4, 5] == [1, 1, 1, 1, 2]
    """
    clock = _Clock(engine_mod.time)
    monkeypatch.setattr(engine_mod, "time", clock)

    async def run_autofocus(cam, focuser, **kw):
        return _gated()

    monkeypatch.setattr(engine_mod, "run_autofocus", run_autofocus)
    e = SequenceEngine(sim_hub)
    e.plan = SequencePlan()
    e._cfg = None
    heard: list[int] = []
    for minute in (0, 10, 20, 29, 30):
        clock.offset = 60.0 * minute
        e._sparse_resweep_owed = True
        await e._autofocus("sparse-field re-sweep", resweep=True)
        heard.append(len([1 for _lv, m, _src in bus_lines
                          if "the probe counted" in m]))
    assert heard == [1, 1, 1, 1, 2], (
        f"the verdict, counted after probes at 0, 10, 20, 29 and 30 "
        f"minutes: {heard}")


async def test_a_dark_or_a_calibration_target_does_not_start_the_clock(
        sim_hub, monkeypatch):
    """A dark, a flat or a calibration target measures no sky, so its frame
    does not make the owed sweep due, however long ago the debt was made; a
    light frame does, once the cadence has passed and not before.

    RED under mutant "any frame starts the clock" (`_note_sparse_resweep`'s
    ``if not self._is_light(step) or getattr(target, "calibration",
    False): return`` taken out), observed:

        AssertionError: a dark started the clock of a sweep owed for the sky
        assert True is False
    """
    from astrodeck.sequence.models import ExposureStep, Target
    clock = _Clock(engine_mod.time)
    monkeypatch.setattr(engine_mod, "time", clock)
    e = SequenceEngine(sim_hub)
    light = ExposureStep(filter="L", exposure_s=30.0, count=1)
    dark = ExposureStep(filter="L", exposure_s=30.0, count=1,
                        frame_type="Dark")
    sky = Target(id="a", name="A", ra_hours=1.0, dec_deg=10.0,
                 steps=[light])
    flats = Target(id="f", name="Flats", ra_hours=1.0, dec_deg=10.0,
                   steps=[light], calibration=True)
    e._sparse_resweep_owed = True
    e._sparse_resweep_next = clock.monotonic() + SPARSE_RESWEEP_EVERY_S
    clock.offset += 3 * SPARSE_RESWEEP_EVERY_S
    for step, target, what in ((dark, sky, "a dark"),
                               (light, flats, "a calibration target's frame")):
        e._note_sparse_resweep(step, target)
        assert e._sparse_resweep_due is False, (
            f"{what} started the clock of a sweep owed for the sky")
    e._sparse_resweep_next = clock.monotonic() + SPARSE_RESWEEP_EVERY_S
    e._note_sparse_resweep(light, sky)
    assert e._sparse_resweep_due is False, (
        "a light frame made the owed sweep due before its cadence")
    clock.offset += SPARSE_RESWEEP_EVERY_S
    e._note_sparse_resweep(light, sky)
    assert e._sparse_resweep_due is True, (
        "a light frame did not make the owed sweep due once the cadence "
        "had passed")
