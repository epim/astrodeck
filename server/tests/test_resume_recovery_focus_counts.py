# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The recovery ladder's sweep counts as the resumed run's good sweep (#402).

2026-09-27, the 0.3.35 deploy restarted the server mid-run and auto-resume
brought the run back. The focuser had lost its position, so the recovery
ladder swept (bin 2, to 11022); the run's own initial autofocus then swept
again 71 s later (bin 1, around 11022) and found the focus the
first sweep had found. About two minutes of sky, on every auto-resume after
a restart.

Now the ladder keeps its successful sweep in process memory, with its
position, temperature, binning and time, and hands it to the start it makes.
The resumed run's first acquisition asks the hop rule (spec 5.6 step 6,
U-05) of that sweep: it sweeps again only if the sweep failed, a refocus is
due, or the focuser temperature moved past the delta.

Each case runs a real `ResumeArm.tick` on the simulator rig and a real
`SequenceEngine`, with the two sweeps faked at their seams: the ladder's
``run_native_autofocus`` and the engine's ``run_autofocus``. They are the
only two places the night swept.

Each case names the mutants it was shown RED under, with the failure
observed, verbatim. Every mutant was applied in a private scratch copy of
server/, never in the shared tree (#254).
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterator

import pytest

import astrodeck.focus.native as native_mod
import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_mod
from astrodeck.devices import fingerprint as fp_mod
from astrodeck.focus.autofocus import AutofocusResult
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target
from astrodeck.sequence.resume_arm import RECOVERY_AF_BINNING, ResumeArm
from astrodeck.sequence.session import Session, session_store

from _deadline import wait_until

#: The focuser temperature the ladder's sweep ends at: a number nothing else
#: in the rig produces, so a file that holds it holds the sweep.
SWEEP_TEMP_C = 7.125
#: Where the ladder's fake sweep leaves the drawtube.
SWEEP_POSITION = 11022


def _numbers(obj) -> Iterator[int | float]:
    """Every number in a parsed JSON document, whatever it is nested in. A
    string is not one, and neither is a boolean (``True`` is an ``int`` to
    Python and a literal to JSON)."""
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        yield obj
    elif isinstance(obj, dict):
        for value in obj.values():
            yield from _numbers(value)
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            yield from _numbers(value)


def _holds_the_sweep_temperature(text: str) -> bool:
    """Whether the JSON ``text`` carries the sweep's temperature as a NUMBER
    (#615). Not as digits: the session file is full of timestamps, floats of
    ten digits and a fraction, and one of them contains the digits of
    ``SWEEP_TEMP_C`` about one run in a few thousand. ``session_text`` writes a
    float as its shortest round-trip spelling, so the sweep's own value reads
    back equal, and ``json.loads`` reads the ``NaN`` and ``Infinity`` constants
    it writes for the rest."""
    return SWEEP_TEMP_C in list(_numbers(json.loads(text)))


#: The reused line's age, ", 0 min ago,", for the age to be set aside.
_AGE = re.compile(r", \d+ min ago,")


def _ageless(line: str) -> str:
    """``line`` with its age in minutes replaced by ``N``, so two lines that
    differ only in how long the run took to reach them compare equal (#615).
    ``_reused_line("N")`` is the text it compares to."""
    return _AGE.sub(", N min ago,", line)


def _reused_line(age: str, since: str = "0.0 C since") -> str:
    """The engine's "focus reused" line for the ladder's sweep after the
    restart, with ``age`` as the minutes it says the sweep is old."""
    return (f"M42: focus reused: the recovery ladder's sweep after the restart "
            f"(at bin 2, {SWEEP_TEMP_C:.1f} C, left at {SWEEP_POSITION}), "
            f"{age} min ago, {since}")


@pytest.fixture
async def rig(tmp_path, monkeypatch):
    """The simulator rig at a configured site (40 N 74 W, not anybody's),
    captures and sessions under ``tmp_path``, the solar cone off. The
    focuser forgets its position across the "restart" (the fingerprint
    distrusts it), autofocus is available, the resume window is open, and
    no plate solver is configured, so the ladder goes from its sweep to its
    re-centre on the mount's own position, with its warning.

    The config store is the test's own, under ``tmp_path``: a run's sweep
    anchors the temperature compensation, which saves its reference to
    the config (#227)."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store, "_path",
                        tmp_path / "astrodeck.json")
    monkeypatch.setattr(hub_module.config_store, "_cfg", None)
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    site = dict(h.site, name="Test", latitude=40.0, longitude=-74.0,
                is_default=False)
    monkeypatch.setattr(type(h), "site", property(lambda self: site))
    monkeypatch.setattr(fp_mod, "verdict",
                        lambda **kw: fp_mod.Verdict(focus_trusted=False))
    monkeypatch.setattr(fp_mod, "vouch", lambda **kw: None)
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    monkeypatch.setattr(ResumeArm, "_can_autofocus", lambda self: True)
    monkeypatch.setattr(ResumeArm, "_can_solve", lambda self: False)
    h.devices["focuser"].set_temperature(SWEEP_TEMP_C)
    yield h
    await h.disconnect_all()


class Sweeps:
    """Both sweeps of the night, faked at their seams and counted.

    ``ladder_ok`` decides whether the ladder's sweep finds focus. The
    engine's always does: this file is about whether it is asked for.
    ``labels`` is what the run called each of its sweeps
    (`SequenceEngine._autofocus`'s label), so a first acquisition's
    "initial autofocus" is told from the frame loop's "refocus"."""

    def __init__(self, hub, monkeypatch, *, ladder_ok: bool = True):
        self.ladder: list[dict] = []
        self.run: list[dict] = []
        self.labels: list[str] = []
        foc = hub.devices["focuser"]
        real_autofocus = SequenceEngine._autofocus

        async def labelled(engine, label, **kw):
            self.labels.append(label)
            return await real_autofocus(engine, label, **kw)

        monkeypatch.setattr(SequenceEngine, "_autofocus", labelled)

        async def ladder_sweep(camera, focuser, **kw):
            self.ladder.append(dict(kw))
            if not ladder_ok:
                return AutofocusResult(False, int(await focuser.get_position()),
                                       None, [], "only 3 stars at the "
                                       "current focus")
            foc.rig.focuser_pos = SWEEP_POSITION
            return AutofocusResult(True, SWEEP_POSITION, 2.12, [])

        async def run_sweep(cam, focuser, **kw):
            self.run.append(dict(kw))
            return AutofocusResult(True, int(await focuser.get_position()),
                                   2.0, [])

        monkeypatch.setattr(native_mod, "run_native_autofocus", ladder_sweep)
        monkeypatch.setattr(engine_mod, "run_autofocus", run_sweep)


def _dormant_armed(*, temp_delta_c: float | None = None,
                   names: tuple[str, ...] = ("M42",)) -> Session:
    """A dormant, armed session whose targets (``names``, one by default)
    each sweep at their setup (``autofocus_first``, and not a mosaic panel,
    so before #402 each swept at every setup whatever came before). Each is
    its own focus group, at the same place on the sky."""
    plan = SequencePlan(name="resume", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False,
                        refocus_on_temp_delta_c=temp_delta_c,
                        targets=[Target(
                            name=name, ra_hours=5.5881, dec_deg=-5.3911,
                            center=False, autofocus_first=True,
                            steps=[ExposureStep(filter="L", exposure_s=0.05,
                                                count=2)])
                            for name in names])
    s = Session(name="resume", status="dormant", plan=plan, auto_resume=True)
    session_store.save(s)
    return s


async def _resume(hub, session: Session, bus_lines,
                  sweeps: Sweeps | None = None) -> ResumeArm:
    """One auto-resume tick, and the run it starts, to its end. Returns the
    arm that made the start (its engine is ``arm.engine``).

    #615: this wait failed once under a full xdist run on the dev box and
    passed on every rerun, so the run's own history is lost by the time a
    timeout is even seen twice. If it times out again, the assertion below
    prints what a rerun cannot recover: the recovery ladder's own state
    (whether it is still recovering, and its step) and the sweep count each
    of the two sweep seams made so far (when the caller hands over its
    ``Sweeps`` double) -- so the failure explains itself the FIRST time."""
    engine = SequenceEngine(hub)
    arm = ResumeArm(engine, hub)
    await arm.tick()
    assert arm._retry_at == 0.0, (
        "premise: the tick resumed. Every bus line it emitted:\n"
        + "\n".join(f"  [{lv}] {m}" for lv, m, _s in bus_lines))
    # A wall-clock deadline (#610), not a round count: 600 x sleep(0.05) is
    # 30 s on both platforms already (Windows' 15.6 ms rounding is
    # negligible against a 50 ms ask), but every OTHER loop of this shape in
    # the suite moved to the shared helper, so this one does too rather than
    # being the one instance left as a round count for the guard to miss.
    ok = await wait_until(lambda: engine.state.get("state") == "complete",
                          timeout_s=30.0, interval_s=0.05)
    if not ok:
        sweep_detail = "no Sweeps double was handed to _resume"
        if sweeps is not None:
            sweep_detail = (f"ladder sweeps={len(sweeps.ladder)} "
                            f"run sweeps={len(sweeps.run)} "
                            f"labels={sweeps.labels!r}")
        pytest.fail(
            f"the run never reached 'complete': {engine.state}. "
            f"Recovery ladder: recovering={arm.recovering!r} "
            f"recovery={arm.recovery!r}. Sweeps: {sweep_detail}.")
    assert session_store.load(session.id).status == "complete"
    return arm


async def test_an_auto_resume_after_a_restart_sweeps_once_not_twice(
        rig, monkeypatch, bus_lines):
    """The ladder sweeps the focuser that lost its position, and the run it
    starts reuses that sweep: one sweep in all, and the run's line names
    the sweep it reused, with its binning and temperature. The session file
    the resume leaves on disk holds nothing of the sweep: it lived in
    memory.

    #615: the two checks in this case that depended on chance or load no longer
    do. The reused line's age in minutes is set aside (``_ageless``: a loaded
    box reaches the first acquisition more than 30 s after the sweep and the
    line then says 1), and the session file is searched for the sweep's
    temperature as a NUMBER (``_holds_the_sweep_temperature``), not as digits
    that a timestamp can contain. Each has its own forced-input case at the end
    of the file.

    MUTANT "the run ignores the recovery sweep" (`SequenceEngine.start`
    dropping ``focus_sweep``, as before #402): RED (observed):
        AssertionError: the run swept again after the ladder's sweep:
        ['initial autofocus']
        assert ['initial autofocus'] == []
    MUTANT "the recovery sweep is not the group's first"
    (`_hop_focus_is_owed` owing the group's first acquisition whatever
    ``recovery`` says): RED, the same failure verbatim (observed).

    The start TAKES the sweep: the arm has none left to hand a later start
    of this process, which would otherwise stand on it again.
    MUTANT "a handed sweep is kept" (``tick`` no longer clearing
    ``_recovery_sweep`` once the start took it): RED (observed, S5-ENG-FLIP
    verifier):
        AssertionError: the start took the ladder's sweep and the arm would
        hand it to the next start too: RecoverySweep(position=11022,
        temp_c=7.125, binning=2, age_s=1.8894686698913574)
    """
    sweeps = Sweeps(rig, monkeypatch)
    s = _dormant_armed()
    arm = await _resume(rig, s, bus_lines, sweeps)
    again = arm._sweep_for_start()
    assert again is None, (
        f"the start took the ladder's sweep and the arm would hand it to the "
        f"next start too: {again}")
    assert len(sweeps.ladder) == 1, "premise: the ladder swept"
    assert sweeps.ladder[0].get("binning") == RECOVERY_AF_BINNING
    assert sweeps.labels == [], (
        f"the run swept again after the ladder's sweep: {sweeps.labels}")
    assert sweeps.run == [], "premise: no sweep outside a labelled one"
    reused = [m for _lv, m, _s in bus_lines if "focus reused" in m]
    assert [_ageless(m) for m in reused] == [_reused_line("N")], reused
    text = (hub_module.CAPTURE_DIR / "sessions" / f"{s.id}.json").read_text(
        encoding="utf-8")
    assert not _holds_the_sweep_temperature(text), (
        "the sweep's temperature reached the session file")


async def test_the_recovery_sweep_is_the_first_acquisitions_only(
        rig, monkeypatch, bus_lines):
    """The ladder's sweep stands in for the focus of the target the run
    opened on, and for no other. A resumed run of two targets, each its own
    focus group, each sweeping at its setup: the first reuses the ladder's
    sweep, and the second makes the initial autofocus its setup always
    made, because the first setup took the sweep (`_setup_target`) and the
    second target's own rule is to sweep.

    MUTANT "the recovery sweep is never taken" (`_setup_target` reading
    ``_recovery_sweep`` without clearing it, so every later acquisition asks
    the hop rule of the ladder's sweep as its group's first): RED (observed,
    S5-ENG-FLIP verifier), both targets reusing it:
        AssertionError: the second target stood on the ladder's sweep too:
        [] / ["M42: focus reused: the recovery ladder's sweep after the
        restart (at bin 2, 7.1 C, left at 11022), 0 min ago, 0.0 C since",
        "M43: focus reused: the recovery ladder's sweep after the restart
        (at bin 2, 7.1 C, left at 11022), 0 min ago, 0.0 C since"]
    """
    sweeps = Sweeps(rig, monkeypatch)
    s = _dormant_armed(names=("M42", "M43"))
    await _resume(rig, s, bus_lines, sweeps)
    assert len(sweeps.ladder) == 1, "premise: the ladder swept"
    reused = [m for _lv, m, _s in bus_lines if "focus reused" in m]
    assert sweeps.labels == ["initial autofocus"], (
        f"the second target stood on the ladder's sweep too: "
        f"{sweeps.labels} / {reused}")
    assert len(reused) == 1 and reused[0].startswith("M42: "), reused


async def test_a_failed_recovery_sweep_does_not_count(rig, monkeypatch,
                                                      bus_lines):
    """A ladder sweep that finds no focus (the native sweep answers
    ``success`` False and puts the drawtube back) is no sweep: the ladder
    says the run will not count it, and the run sweeps at its setup.

    MUTANT "a failed recovery sweep counts" (`_note_recovery_sweep`
    keeping a sweep whatever its ``success``): RED (observed):
        AssertionError: the run stood on a sweep that found no focus: []
        assert [] == ['initial autofocus']
    """
    sweeps = Sweeps(rig, monkeypatch, ladder_ok=False)
    s = _dormant_armed()
    await _resume(rig, s, bus_lines, sweeps)
    assert len(sweeps.ladder) == 1, "premise: the ladder swept"
    assert sweeps.labels == ["initial autofocus"], (
        f"the run stood on a sweep that found no focus: {sweeps.labels}")
    assert len(sweeps.run) == 1, "premise: the run's sweep was made"
    said = [m for lv, m, _s in bus_lines
            if lv == "warning" and "did not find focus" in m]
    assert said == ["the autofocus after the restart did not find focus "
                    "(only 3 stars at the current focus); the run will not "
                    "count it as tonight's sweep"], said


async def test_a_temperature_moved_past_the_delta_sweeps_again(
        rig, monkeypatch, bus_lines):
    """CONTROL. The ladder's sweep is good, but the focuser has cooled 2 C
    by the run's first acquisition against a 1 C delta: the frame loop
    would refocus, so the hop rule owes a sweep and the run makes one,
    after the temperature trigger's own line, measured against the
    ladder's sweep.

    Counted by label, not by sweep: with the first acquisition's sweep
    skipped, the frame loop's temperature trigger sweeps at the first frame
    boundary instead, and a count of sweeps read the same either way (seen
    under the mutant below before the labels were read).

    MUTANT "the recovery sweep ignores the temperature" (`_hop_focus_is_owed`
    not asking `_refocus_due` for a recovery sweep): RED, the first case
    green (observed):
        AssertionError: the run's first acquisition reused a sweep the
        temperature had moved past: ['refocus']
        assert ['refocus'] == ['initial autofocus']
    Under both mutants of the first case the run sweeps at its first
    acquisition here too, but this case goes RED at its premise, since the
    run then never asks the temperature question there (observed, both):
        AssertionError: premise: the temperature trigger fired
    """
    sweeps = Sweeps(rig, monkeypatch)
    real = ResumeArm._note_recovery_sweep

    async def then_it_cools(self, foc, result):
        await real(self, foc, result)
        rig.devices["focuser"].set_temperature(SWEEP_TEMP_C - 2.0)

    monkeypatch.setattr(ResumeArm, "_note_recovery_sweep", then_it_cools)
    s = _dormant_armed(temp_delta_c=1.0)
    await _resume(rig, s, bus_lines, sweeps)
    assert len(sweeps.ladder) == 1, "premise: the ladder swept"
    assert sweeps.labels == ["initial autofocus"], (
        f"the run's first acquisition reused a sweep the temperature had "
        f"moved past: {sweeps.labels}")
    assert len(sweeps.run) == 1, "premise: the run's sweep was made"
    assert any(m.startswith(f"focuser temp drifted to "
                            f"{SWEEP_TEMP_C - 2.0:.1f}")
               for _lv, m, _s in bus_lines), (
        "premise: the temperature trigger fired")
    assert not [m for _lv, m, _s in bus_lines if "focus reused" in m]


# ------------------------------------------------ the two helpers' own cases


def test_the_leak_check_reads_numbers_not_digits():
    """#615: the session file holds the run's timestamps as floats, and a
    timestamp such as 1790000007.1253 CONTAINS the digits of the sweep's
    temperature, so a check for ``str(SWEEP_TEMP_C)`` as text would call the
    file a leak about one run in a few thousand, by the clock alone. The check
    reads the file as JSON and compares the NUMBERS in it.

    Forced inputs, not reruns: the timestamp below is the one that matches.

    MUTANT "the leak check is a substring again" (``_holds_the_sweep_
    temperature`` back to ``str(SWEEP_TEMP_C) in text``) -- RED, observed:
        AssertionError: a timestamp that merely contains the digits is not the
        sweep's temperature: {"updated_ts": 1790000007.1253, "metrics": {}}
    """
    stamp = 1790000007.1253
    assert str(SWEEP_TEMP_C) in str(stamp), (
        "premise: the old substring check WOULD match this timestamp")
    stamped = json.dumps({"updated_ts": stamp, "metrics": {}})
    assert not _holds_the_sweep_temperature(stamped), (
        f"a timestamp that merely contains the digits is not the sweep's "
        f"temperature: {stamped}")
    assert _holds_the_sweep_temperature(json.dumps({"focus_temp": 7.125}))
    # Wherever the number is nested, and beside the constants
    # `session_text` writes for a NaN or an infinity, which `json.loads`
    # reads.
    assert _holds_the_sweep_temperature(
        '{"frames": [{"metrics": {"hfr": NaN, "t": 7.125}}], "x": Infinity}')
    assert not _holds_the_sweep_temperature(
        '{"frames": [{"metrics": {"hfr": NaN}}], "x": Infinity, "n": 7}')
    assert not _holds_the_sweep_temperature(
        '{"note": "7.125", "ok": true, "none": null}'), (
        "a string is not a number, and neither is a boolean")


def test_the_reused_line_compares_whatever_its_age():
    """#615: the line's "N min ago" is the sweep's age rounded to a minute
    (``age_s / 60:.0f``), so it reads 0 only while the run reaches its first
    acquisition inside 30 s of the ladder's sweep; a loaded box takes longer,
    and the line then says 1. The age is not what the case is about, so the
    comparison treats it as a number of minutes and nothing more. The
    temperature half stays exact: the simulated focuser is frozen, so "0.0 C
    since" is the sweep's own and a different figure is a real difference.

    Forced inputs: a line aged 0 minutes and one aged 1 (a sweep 45 s old
    prints 1), each compared with the expected text.

    MUTANT "the age is pinned again" (``_ageless`` returning the line
    unchanged, so the comparison holds only for the one age it was written
    for) -- RED, observed (this case, and the first case's own comparison):
        AssertionError: a line aged 0 min does not compare equal to the
        expected text: "M42: focus reused: the recovery ladder's sweep after
        the restart (at bin 2, 7.1 C, left at 11022), 0 min ago, 0.0 C since"
    A mutant that pinned the age to 0 in the EXPECTED text instead (the
    comparison before #615) would pass the 0 line and fail the 1 and 12 ones,
    which is the loaded run's line.
    """
    for age in ("0", "1", "12"):
        line = _reused_line(age)
        assert _ageless(line) == _reused_line("N"), (
            f"a line aged {age} min does not compare equal to the expected "
            f"text: {_ageless(line)!r}")
    assert (_ageless(_reused_line("1", since="0.3 C since"))
            != _reused_line("N")), (
        "the temperature half is exact: a focuser that moved is a difference")
