# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#615's proof: a forced timeout, not a rerun, shows the enriched message.

test_resume_recovery_focus_counts.py::test_an_auto_resume_after_a_restart
_sweeps_once_not_twice failed ONCE in a full xdist run on the dev box
(2026-10-01) and passed on every rerun since, including three in a row run
alone. The failure text was not captured, because only the summary line
survived -- so a rerun could not even prove the fix touched the right
thing, let alone diagnose it. ``_resume``'s wait on the run reaching
"complete" is now a wall-clock deadline (#610) instead of a round count,
and a timeout's message now carries the recovery ladder's own state
(``arm.recovering``, ``arm.recovery``) and the sweep count of both sweep
seams (``Sweeps.ladder``/``Sweeps.run``/``Sweeps.labels``), not just the
bare engine state dict #615 actually got.

This file does not reproduce the intermittent timing itself (nothing here
claims to know why it took longer that one time, on that one box, under
that one xdist load -- the cause may yet turn out to be in production
code, in which case #615 stays open). It forces ``_resume``'s TIMEOUT
BRANCH directly, by making its wait always report "not yet", so the
message is proven on every run rather than on however-many-it-takes."""
from __future__ import annotations

import pytest

import test_resume_recovery_focus_counts as trrfc
from test_resume_recovery_focus_counts import (  # noqa: F401 (fixture import)
    Sweeps, _dormant_armed, _resume, rig)


async def test_a_forced_timeout_names_the_recovery_ladder_and_the_sweeps(
        rig, monkeypatch, bus_lines):
    """Force ``_resume``'s deadline to always read as expired (never
    reproducing the real intermittent timing, only its failure PATH), and
    check the message it raises names what #615 asked for: the recovery
    ladder's own state and the sweep count, not just ``engine.state``.

    MUTANT "the enriched message is dropped" (the ``pytest.fail`` call in
    ``_resume`` replaced by the old bare ``assert ok, engine.state``): RED,
    observed verbatim -- the plain ``AssertionError`` is not even the type
    ``pytest.raises(pytest.fail.Exception)`` is watching for, so it escapes
    uncaught and fails this test outright:
        E   AssertionError: {'progress': {'frames_done': 0}, 'state': 'idle'}
        E   assert False
    (this test's OWN failure, not the one it is probing for -- which is
    exactly the point: losing the enriched message also loses the exception
    TYPE a caller might filter on, not just its text)."""
    async def always_times_out(cond, *, timeout_s, interval_s=0.01):
        # The real helper would keep polling `cond` for `timeout_s`; this
        # double skips straight to "timed out" so the test proves the
        # TIMEOUT BRANCH's message without waiting out a real deadline.
        return False

    monkeypatch.setattr(trrfc, "wait_until", always_times_out)

    sweeps = Sweeps(rig, monkeypatch)
    s = _dormant_armed()
    with pytest.raises(pytest.fail.Exception) as ei:
        await _resume(rig, s, bus_lines, sweeps)
    message = str(ei.value)
    assert "the run never reached 'complete'" in message, message
    assert "Recovery ladder: recovering=" in message, message
    assert "recovery=" in message, message
    assert "Sweeps: ladder sweeps=" in message, message
    assert "run sweeps=" in message, message
    assert "labels=" in message, message
    # The premise the ladder actually swept, so the sweep count this
    # message reports is not vacuously zero-and-zero.
    assert len(sweeps.ladder) == 1, "premise: the ladder swept"
    assert "ladder sweeps=1" in message, message


async def test_a_forced_timeout_without_a_sweeps_double_says_so(
        rig, monkeypatch, bus_lines):
    """A caller that cannot hand over a ``Sweeps`` double (there is only
    one production caller of ``ResumeArm.tick``, but a future test might
    call ``_resume`` before building one) still gets a message that SAYS
    it has no sweep count, rather than a silent KeyError or a blank."""
    async def always_times_out(cond, *, timeout_s, interval_s=0.01):
        return False

    monkeypatch.setattr(trrfc, "wait_until", always_times_out)

    # The sweep doubles are installed at their seams so the tick resumes,
    # but the object is withheld from `_resume`: the withholding is what
    # this test is about. Without them the real ladder sweep ran, and it
    # resumed only where the native wheel is installed; the Linux runner
    # has none, so the arm held and the premise failed first (#661). The
    # wheel is forced absent here so every box is that runner.
    #
    # MUTANT "the doubles are not installed" (the `Sweeps(...)` line
    # removed): RED on any box, observed verbatim:
    #     AssertionError: premise: the tick resumed. Every bus line it
    #     emitted: ... auto-resume held: autofocus after restart failed:
    #     native engine not installed
    monkeypatch.setattr(trrfc.native_mod, "NATIVE_AVAILABLE", False)
    Sweeps(rig, monkeypatch)

    s = _dormant_armed()
    with pytest.raises(pytest.fail.Exception) as ei:
        await _resume(rig, s, bus_lines)   # no sweeps argument
    message = str(ei.value)
    assert "no Sweeps double was handed to _resume" in message, message
