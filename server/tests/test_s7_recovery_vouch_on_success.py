# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The recovery ladder vouches for the focuser only after a sweep that found
focus (#457; spec 5.6 step 6, "As built, the recovery ladder's sweep
counts").

After a restart the fingerprint distrusts a focuser that no longer reads
the position recorded before the cut: the EAF forgets its count on a power
loss, so its readout is a default, not a measurement. The ladder
(`ResumeArm._recover`, step 1) then autofocuses, and records where the
sweep left the drawtube as MEASURED (`fingerprint.vouch`), so that a
refusal later in the ladder (a failed plate solve under cloud) does not
send every ten-minute retry back through a sweep. It vouched whatever the
sweep said. `run_native_autofocus` answers most failures with ``success``
False after putting the drawtube back where it started, which after a
restart is the position the focuser forgot, and that position was then
recorded as measured: the next tick trusted the focuser and did not sweep
again, and a plan whose targets have ``autofocus_first`` off imaged the
night on it with one warning.

NOW the vouch is made only when the sweep's result says ``success`` True,
the test `_note_recovery_sweep` already applies (#402). The ladder still
goes on after a failed sweep, with that warning, as it did.

THE RIG is the simulator at a fixture site (40 N 74 W, not anybody's), with
the REAL fingerprint under ``tmp_path``: a record written before the
"restart" (``reset_for_tests``) at a position the focuser no longer reads.
The sweep is faked at its one seam, the ladder's ``run_native_autofocus``.
A plate solver is configured and fails ("Not enough stars"), so every tick
refuses at step 2, after step 1. Between the ticks ten minutes pass: the
arm's backoff is cleared, which is all the passing of them changes.

Each case names the mutant it was shown RED under, with the failure
observed, verbatim. Every mutant was applied in a private scratch copy of
server/ (S7-ENG-SAFE-r2-mut, in the session scratchpad), never in the
shared tree (#254), and every quote is from that copy's run.
"""
from __future__ import annotations

import pytest

import astrodeck.focus.native as native_mod
import astrodeck.hub as hub_module
from astrodeck.devices import fingerprint as fp_mod
from astrodeck.focus.autofocus import AutofocusResult
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine
from astrodeck.sequence.resume_arm import ResumeArm
from astrodeck.sequence.session import session_store

from test_resume_recovery_focus_counts import _dormant_armed

#: Where the focuser stood when the record was written, before the cut.
RECORDED_AT = 9935
#: What it reads after the cut: an EAF that forgot its count reads 0.
FORGOT_AT = 0
#: Where a sweep that finds focus leaves it.
SWEEP_POSITION = 11022
#: The failed sweep's own reason, as the native engine words it.
NO_FOCUS = "only 3 stars at the current focus"


@pytest.fixture
async def rig(tmp_path, monkeypatch):
    """The simulator rig at a configured site, captures and sessions under
    ``tmp_path``, the solar cone off, the resume window open, autofocus and
    a plate solver available, and the solver failing. The fingerprint is
    the real module on a file under ``tmp_path``, holding a record from
    before the restart that the focuser no longer matches."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store, "_path",
                        tmp_path / "astrodeck.json")
    monkeypatch.setattr(hub_module.config_store, "_cfg", None)
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    monkeypatch.setattr(fp_mod, "_PATH", tmp_path / "fingerprint.json")
    fp_mod.reset_for_tests()
    fp_mod.record(focuser_position=RECORDED_AT, filter_slot=0, ra_hours=1.0,
                  dec_deg=2.0, parked=False, tracking=True)
    fp_mod.reset_for_tests()                # the restart
    h = Hub()
    await h.connect_sim()
    site = dict(h.site, name="Test", latitude=40.0, longitude=-74.0,
                is_default=False)
    monkeypatch.setattr(type(h), "site", property(lambda self: site))
    h.devices["focuser"].rig.focuser_pos = FORGOT_AT

    async def no_stars(*_a, **_kw):
        raise RuntimeError("Not enough stars")

    monkeypatch.setattr(h, "solve_and_sync", no_stars)
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    monkeypatch.setattr(ResumeArm, "_can_autofocus", lambda self: True)
    monkeypatch.setattr(ResumeArm, "_can_solve", lambda self: True)
    yield h
    await h.disconnect_all()
    fp_mod.reset_for_tests()


def _sweeps(hub, monkeypatch, *, finds_focus: bool) -> list[int]:
    """The ladder's sweep, faked at its seam. Returns the list of the
    positions each sweep began at. A sweep that finds focus leaves the
    drawtube at ``SWEEP_POSITION``; one that does not puts it back where it
    began, as the native engine does, and says why."""
    foc = hub.devices["focuser"]
    began: list[int] = []

    async def sweep(camera, focuser, **kw):
        start = int(await focuser.get_position())
        began.append(start)
        if not finds_focus:
            return AutofocusResult(False, start, None, [], NO_FOCUS)
        foc.rig.focuser_pos = SWEEP_POSITION
        return AutofocusResult(True, SWEEP_POSITION, 2.12, [])

    monkeypatch.setattr(native_mod, "run_native_autofocus", sweep)
    return began


def _refused_at_the_solve(bus_lines, arm: ResumeArm) -> list[str]:
    held = [m for lv, m, _s in bus_lines
            if lv == "warning" and m.startswith("auto-resume held")]
    assert arm._retry_at > 0.0 and held and all(
        "blind plate solve failed after restart" in m for m in held), (
            f"premise: the tick refused at the plate solve: retry at "
            f"{arm._retry_at}, held lines {held}")
    return held


async def test_a_failed_sweep_then_a_refused_solve_sweeps_again(
        rig, monkeypatch, bus_lines):
    """The focuser forgot its position, the ladder's sweep finds no focus
    and puts the drawtube back where it began, and the plate solve then
    refuses the tick. Nothing measured the focuser, so the fingerprint
    still distrusts it, and the next tick, ten minutes later, sweeps again,
    from the same forgotten position.

    Mutant "vouch unconditionally" (the ``success`` guard before the
    ladder's `fingerprint.vouch` taken out, as before #457): RED
    (observed) -
        AssertionError: the next tick trusted a focuser that no sweep had
        measured: sweeps began at [0], the fingerprint now trusts position
        0: True
    """
    began = _sweeps(rig, monkeypatch, finds_focus=False)
    s = _dormant_armed()
    arm = ResumeArm(SequenceEngine(rig), rig)
    await arm.tick()
    _refused_at_the_solve(bus_lines, arm)
    said = [m for lv, m, _s in bus_lines
            if lv == "warning" and "did not find focus" in m]
    assert began == [FORGOT_AT] and len(said) == 1, (
        f"premise: the first tick swept once and the sweep found no focus: "
        f"sweeps began at {began}, lines {said}")
    arm._retry_at = 0.0                     # ten minutes pass
    await arm.tick()
    trusted = fp_mod.verdict(focuser_position=FORGOT_AT).focus_trusted
    assert began == [FORGOT_AT, FORGOT_AT] and trusted is False, (
        f"the next tick trusted a focuser that no sweep had measured: "
        f"sweeps began at {began}, the fingerprint now trusts position "
        f"{FORGOT_AT}: {trusted}")
    _refused_at_the_solve(bus_lines, arm)
    assert session_store.load(s.id).status == "dormant", (
        "premise: a refused ladder leaves the session dormant")


async def test_control_a_sweep_that_found_focus_is_not_repeated(
        rig, monkeypatch, bus_lines):
    """CONTROL. The same ladder and the same refused solve, but the sweep
    finds focus and leaves the drawtube at ``SWEEP_POSITION``. That IS a
    measurement: the fingerprint vouches for it, and the next tick trusts
    the focuser and does not sweep again (the rule the vouch exists for,
    test_resume_after_restart's "not repeated on every retry").

    Mutant "never vouch" (the ladder's `fingerprint.vouch` call made
    unreachable, ``if getattr(result, "success", None) is True:`` made
    ``if False:``): RED (observed) -
        AssertionError: a sweep that found focus was not taken as a
        measurement: sweeps began at [0, 11022], the fingerprint trusts
        position 11022: False
    """
    began = _sweeps(rig, monkeypatch, finds_focus=True)
    _dormant_armed()
    arm = ResumeArm(SequenceEngine(rig), rig)
    await arm.tick()
    _refused_at_the_solve(bus_lines, arm)
    assert began == [FORGOT_AT], f"premise: the first tick swept: {began}"
    arm._retry_at = 0.0                     # ten minutes pass
    await arm.tick()
    trusted = fp_mod.verdict(focuser_position=SWEEP_POSITION).focus_trusted
    assert began == [FORGOT_AT] and trusted is True, (
        f"a sweep that found focus was not taken as a measurement: sweeps "
        f"began at {began}, the fingerprint trusts position "
        f"{SWEEP_POSITION}: {trusted}")
    _refused_at_the_solve(bus_lines, arm)
