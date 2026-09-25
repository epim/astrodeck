"""An abort that cannot save its disarm still answers, and says so (#189 item
10 (c); mosaic slice H3 task T1; spec 6.15 "Operator STOP").

``POST /api/sequence/abort`` stops ResumeArm's recovery ladder and disarms the
session it was recovering (#220), through ``ResumeArm._disarm_stopped``. That
write is best-effort on purpose: a session store that cannot be written must
not turn the operator's STOP into a 500, because the stop itself (the flag
and the cancel) has already been made and is what matters to the mount. But
an unsaved disarm lets the next tick run the ladder again, so the failure is
said loudly, in an error line that names the session.

Until this file nothing exercised that branch: every test of the abort ran
against a store that could be written.

THE HARNESS is test_resume_ladder_stops.py's (the real app over ASGI and a
real ResumeArm on a parked hub). The store's ``save`` is replaced, after the
ladder is parked, by one that raises: the only write this abort makes.

Each mutant was applied to a byte-for-byte backup of resume_arm.py and the
file was restored byte-identical (SHA-256 compared) afterwards. The failures
are quoted as observed (``--tb=short``).
"""
from __future__ import annotations

import pytest

from astrodeck.sequence.session import session_store
from test_flows_continue import rig  # noqa: F401 (fixture)
from test_resume_ladder_stops import (_finish, _park, _stood_down,  # noqa: F401
                                      _unwound, ladder)


def _said(bus_lines, level: str, fragment: str) -> list[str]:
    return [m for lvl, m, _src in bus_lines if lvl == level and fragment in m]


async def test_an_abort_answers_200_when_the_disarm_cannot_be_saved(
        ladder, bus_lines):
    """The store refuses the disarm's write. The abort still answers 200 and
    the ladder still stops before its next step, the error line names the
    session and says what an unsaved disarm means, and the reassuring info
    line ("auto-resume is disarmed for it") is NOT logged, because it would
    be false: the file on disk is still armed.

    RED under mutant "the exception propagates" (``_disarm_stopped``'s
    ``except Exception`` arm removed, so the store's error leaves
    ``stop_recovery`` and the route), observed verbatim:

        E   OSError: [Errno 28] No space left on device: 'the store refused this write'

    raised out of ``client.post`` (the ASGI transport re-raises the app's
    exception; a served app answers 500).
    """
    lad = ladder
    tick = await _park(lad, "solve")

    def refuse(session):
        raise OSError(28, "No space left on device",
                      "the store refused this write")

    # Its own context, not the test's ``monkeypatch``: undoing that would
    # undo the harness's isolation with it, and the store must take writes
    # again before the checks below read it.
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(session_store, "save", refuse)
            r = await lad.rig.client.post("/api/sequence/abort")
            unwound = await _unwound(tick)
    finally:
        await _finish(lad, tick)

    assert r.status_code == 200, r.text
    assert r.json() == {"aborted": True}
    errors = _said(bus_lines, "error", "could not disarm 'recover me'")
    assert len(errors) == 1, bus_lines
    assert "may start it again on its next tick" in errors[0], errors[0]
    assert "No space left on device" in errors[0], errors[0]
    assert _said(bus_lines, "info", "stopped by hand") == [], (
        "the abort claimed a disarm the store refused")
    assert unwound is True, "the ladder did not stop"
    assert lad.hub.calls == ["solve"], lad.hub.calls
    assert len(_stood_down(bus_lines)) == 1, bus_lines
    assert session_store.load(lad.session.id).auto_resume is True, (
        "premise: the refused write left the file armed")


async def test_control_a_working_store_disarms_and_says_so(ladder, bus_lines):
    """CONTROL: the store takes the write. The session is disarmed on disk,
    the info line says so, and there is no error line.

    RED under mutant "the info line is dropped" (``_disarm_stopped``'s
    closing ``bus.log("info", ...)`` removed), observed verbatim:

        E   AssertionError: [('info', "auto-resume stood down for 'recover me': the operator pressed Abort while it was re-centring the mount", 'sequence')]
        E   assert 0 == 1
    """
    lad = ladder
    tick = await _park(lad, "solve")
    try:
        r = await lad.rig.client.post("/api/sequence/abort")
        await _unwound(tick)
    finally:
        await _finish(lad, tick)

    assert r.status_code == 200, r.text
    said = _said(bus_lines, "info", "stopped by hand")
    assert len(said) == 1, bus_lines
    assert "'recover me'" in said[0] and "disarmed" in said[0], said
    assert _said(bus_lines, "error", "could not disarm") == []
    assert session_store.load(lad.session.id).auto_resume is False
