"""A forced whole-rig connect validates its body before it touches anything
(#257; spec 6.15 "Operator STOP"; mosaic slice S2 task T17).

``POST /api/connect/rig`` with ``force`` ends whatever is using the rig before
it builds the new one: since #238 it stops auto-resume's recovery ladder and
disarms the session the ladder was recovering, waits for the ladder to
return, then aborts a live run. The body's ``primary`` and each of its
``roles`` overrides were checked against the backend registry only AFTER all
of that. So a forced request with a typo in one role override ended the
night, switched the armed session's auto-resume off, and then answered 422
and connected nothing: a 422 that read as "nothing happened", over a rig left
connected, idle and not tracking under any run, with nothing to restart it.

Now the whole body is validated first, before the busy refusal, the ladder
stop, the disarm, the wait and ``engine.abort``, so a 422 from this route
means nothing was touched.

THE HARNESS is test_teardown_routes_stop_the_ladder.py's: the real app over
ASGI on the test's own loop, a real ``SequenceEngine`` whose imaging loop
alone is replaced (test_flows_continue.py's ``rig``), and a real ResumeArm
running its real ``tick`` and ``_recover`` on a parked hub, installed as
``app_module.resume_arm`` (test_resume_ladder_stops.py's ``ladder``).
``hub.connect_rigspec`` is a recorder.

THE STATE is the issue's worst case, all three things a forced connect ends
at once: the ladder parked in its solve for the armed session it is
recovering, and a run live on that same session, started through the
engine's own ``start``. Both are real states: the start routes refuse while
the ladder runs, but a run can still get in, which is why the ladder asks
``_a_run_took_over`` before each step and why Abort stops both. The control
sends a VALID forced connect into the same state and shows that each of the
three observables moves when the destructive steps do run, so the main case
cannot pass on a harness that cannot see them.

MUTATIONS. Each was applied to a byte-for-byte copy of ``api/app.py`` in a
private copy of ``server/`` under the session scratchpad; only this file (and
test_connect_rig_guard.py) was run there, and the copy was restored and
SHA-256 compared after every mutant. The shared tree was never written.
Failures are quoted from ``--tb=short``.
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.api.app as app_module
from astrodeck.sequence.session import session_store
from test_flows_continue import rig  # noqa: F401 (fixture)
from test_resume_ladder_stops import (DEADLOCK_GUARD_S, TURN_CAP,  # noqa: F401
                                      _finish, _park, ladder)

#: Bodies the registry refuses, each with the 422 detail the route has always
#: given for it. The third names a GOOD override first: every override is
#: validated, not the first one. It is the issue's own case, an override a
#: backend cannot fill (NINA fills no safety monitor).
BAD_BODIES = {
    "unknown primary": (
        {"primary": "nosuch"},
        "unknown primary backend 'nosuch'"),
    "unknown override backend": (
        {"primary": "sim", "roles": {"camera": {"backend": "nosuch"}}},
        "unknown backend 'nosuch' for role 'camera'"),
    "override the backend cannot fill": (
        {"primary": "sim", "roles": {"camera": {"backend": "sim"},
                                     "safety": {"backend": "nina"}}},
        "backend 'nina' cannot fill role 'safety'"),
}


async def _spin() -> None:
    """Turns of the loop in which a stop the route sent would reach the
    parked step, and a stopped ladder would unwind."""
    for _ in range(TURN_CAP // 10):
        await asyncio.sleep(0)


@pytest.fixture
def night(ladder, monkeypatch):
    """The ladder fixture with a recorder on the app hub's
    ``connect_rigspec``; ``_live_on_the_recovered_session`` then parks the
    ladder and starts the run. Returns ``(lad, torn)``; each ``torn`` record
    is ``(method, ladder_recovering)``, taken when the connect began."""
    lad = ladder
    torn: list[tuple[str, bool]] = []

    async def connect_rigspec(spec):
        torn.append(("connect_rigspec", lad.arm.recovering))
        return {"recorded": "connect_rigspec"}

    monkeypatch.setattr(app_module.hub, "connect_rigspec", connect_rigspec)
    return lad, torn


async def _live_on_the_recovered_session(lad) -> asyncio.Task:
    """Park the ladder in its solve, then start a run on the session it is
    recovering. Returns the parked tick."""
    tick = await _park(lad, "solve")
    lad.rig.engine.start(lad.session.plan,
                         session=session_store.load(lad.session.id))
    assert lad.rig.engine.running, "premise: a run is live"
    assert lad.arm.recovering, "premise: the ladder is still parked"
    stored = session_store.load(lad.session.id)
    assert (stored.status, stored.auto_resume) == ("active", True), (
        "premise: the run's session is active and armed")
    return tick


@pytest.mark.parametrize("case", list(BAD_BODIES))
async def test_a_forced_connect_with_a_bad_body_touches_nothing(
        night, bus_lines, case):
    """A forced connect whose body the registry refuses answers 422 with the
    same detail as ever, and touches nothing: the run is still running, its
    session is still armed, the ladder was not asked to stop and is still
    parked in its solve, and nothing was connected.

    RED under mutant "today's order" (the primary and roles checks moved back
    below ``if body.force and engine.running: await engine.abort()``, where
    they were), every case: the route stops the ladder, disarms its session,
    waits for it, aborts the run, and only then answers 422. Observed
    verbatim:

        [unknown primary]
        E   AssertionError: a request that answered 422 ended the run
        E   assert False is True
        [unknown override backend] [override the backend cannot fill]
        the same two lines

    Peeled under the same mutant, in the scratch copy: with the run
    assertion deleted, every case fails next on the ladder,

        E   AssertionError: the ladder was asked to stop by a request that answered 422: solve ended 'cancelled'
        E   assert ('cancelled' is None)

    and with the ladder assertion deleted as well, on the session, which the
    stop disarmed and the abort left dormant:

        E   AssertionError: a request that answered 422 disarmed the armed session
        E   assert ('dormant', False) == ('active', True)
    """
    lad, torn = night
    body, said = BAD_BODIES[case]
    before_starts = len(lad.rig.starts)
    tick = await _live_on_the_recovered_session(lad)
    try:
        r = await asyncio.wait_for(
            lad.rig.client.post("/api/connect/rig",
                                json={**body, "force": True}),
            DEADLOCK_GUARD_S)
        await _spin()
        running = lad.rig.engine.running
        solve_ended = lad.hub.steps["solve"].ended
        still_parked = lad.arm.recovering and not tick.done()
        stored = session_store.load(lad.session.id)
        lines = list(bus_lines)
    finally:
        await _finish(lad, tick)

    assert r.status_code == 422, (
        f"premise: the registry refuses this body: {r.status_code} {r.text}")
    assert r.json()["detail"] == said, r.json()
    assert running is True, "a request that answered 422 ended the run"
    assert solve_ended is None and still_parked, (
        f"the ladder was asked to stop by a request that answered 422: "
        f"solve ended {solve_ended!r}")
    assert (stored.status, stored.auto_resume) == ("active", True), (
        "a request that answered 422 disarmed the armed session")
    assert torn == [], f"a refused body connected a rig: {torn}"
    assert lad.rig.starts[before_starts + 1:] == [], (
        "a start followed the refused connect")
    assert [m for _lvl, m, _src in lines if "stopped by hand" in m] == [], (
        f"a refused connect said it stopped something by hand: {lines}")


async def test_control_a_valid_forced_connect_still_ends_all_three(night):
    """CONTROL, the same state and a valid body: a forced connect behaves as
    it did. It stops the ladder (the parked solve is cancelled) and disarms
    its session, waits until the ladder has returned, aborts the run, and
    connects the new rig once, with no ladder left running. Green on the
    code and under every mutant in this file: it is what shows that each
    thing the main case says was left alone is something this harness sees
    move."""
    lad, torn = night
    tick = await _live_on_the_recovered_session(lad)
    try:
        r = await asyncio.wait_for(
            lad.rig.client.post("/api/connect/rig",
                                json={"primary": "sim", "force": True}),
            DEADLOCK_GUARD_S)
        await _spin()
        running = lad.rig.engine.running
        solve_ended = lad.hub.steps["solve"].ended
        stored = session_store.load(lad.session.id)
    finally:
        await _finish(lad, tick)

    assert r.status_code == 200, r.text
    assert solve_ended == "cancelled", solve_ended
    assert running is False, "a forced connect left the run going"
    assert stored.auto_resume is False, "a forced connect left it armed"
    assert torn == [("connect_rigspec", False)], torn


async def test_unforced_a_bad_body_is_refused_as_a_bad_body_first(
        night, bus_lines):
    """Without ``force``, while a run is live and the ladder runs, a body
    the registry refuses answers 422, not 409 ``running``. The validation
    comes before the busy refusal too, so the operator is never offered
    "force" for a request that would fail whatever the rig was doing; the
    422 is what a forced press would get. Touches nothing, as the forced
    case.

    RED under mutant "validate after the busy refusal" (the primary and
    roles checks moved to just below the unforced 409, above the forced
    ladder stop), and the same under "today's order", observed verbatim:

        E   AssertionError: a bad body was refused as busy, not as bad: 409 {"detail":{"detail":"a sequence, capture loop or polar alignment is running, and auto-resume is re-centring the mount after a restart (GET /api/sequence/resume-arm reports the step it is on); force stops the re-centring before its next step and turns that session's auto-resume off, as Abort does, then goes ahead","code":"running"}}
        E   assert 409 == 422
        E    +  where 409 = <Response [409 Conflict]>.status_code

    "Validate after the busy refusal" keeps the forced case above green:
    the ordering that matters for #257 is against the destructive steps,
    and this case pins the rest of "validates first".
    """
    lad, torn = night
    body, said = BAD_BODIES["override the backend cannot fill"]
    tick = await _live_on_the_recovered_session(lad)
    try:
        r = await asyncio.wait_for(
            lad.rig.client.post("/api/connect/rig", json=body),
            DEADLOCK_GUARD_S)
        await _spin()
        running = lad.rig.engine.running
        still_parked = lad.arm.recovering and not tick.done()
        stored = session_store.load(lad.session.id)
    finally:
        await _finish(lad, tick)

    assert r.status_code == 422, (
        f"a bad body was refused as busy, not as bad: {r.status_code} "
        f"{r.text}")
    assert r.json()["detail"] == said, r.json()
    assert running is True and still_parked
    assert stored.auto_resume is True
    assert torn == []
