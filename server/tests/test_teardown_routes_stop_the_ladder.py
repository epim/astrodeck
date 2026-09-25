"""Every route that tears the rig down stops auto-resume's recovery ladder
first, and waits until it has returned (#238; mosaic slice H3 task T1; spec
6.15 "Operator STOP", 5.9 "One starter per session").

After a restart ResumeArm's ladder reads the safety monitor, may autofocus,
blind-solves and re-centres the mount, for minutes, with no run behind it.
H2 (#220) made Abort and a disarm stop it. The other callers of
``engine.abort`` did not: ``POST /api/disconnect`` pulled every device out
from under a ladder still awaiting a solve or a slew, and a forced profile
apply, profile activate or rig connect did the same and then built a new rig
the ladder would go on to use. Unforced, those three counted a run, a capture
loop and a polar alignment as "running" and not the ladder, so they tore down
under it without being asked to confirm.

Now:

* ``POST /api/disconnect`` calls ``resume_arm.stop_recovery(..., disarm=True)``
  as Abort does (6.15), then waits on ``ResumeArm.wait_stopped`` until the
  ladder has returned, bounded by ``LADDER_STOP_WAIT_S``. When the bound
  expires it answers 409 and tears nothing down. Only then does it abort a run
  and disconnect.
* ``/api/profiles/{id}/apply``, ``/api/profiles/{id}/activate`` and
  ``/api/connect/rig``, unforced, answer 409 with the existing code
  ``running`` while ``resume_arm.recovering``, and a detail that names
  auto-resume's re-centring.
* The same three, forced, stop the ladder with disarm, wait, then proceed.

THE HARNESS is test_resume_ladder_stops.py's: the real app over ASGI, a real
ResumeArm running its real ``tick`` and ``_recover`` on a parked hub whose
steps each wait on an ``asyncio.Event``, installed as ``app_module.resume_arm``.
The teardown itself is the app hub's ``disconnect_all``, ``apply_profile``,
``connect_profile_id`` and ``connect_rigspec``, each replaced by a recorder
that notes whether the ladder was still running when the teardown began. The
ladder is parked in a STUBBORN solve (it swallows the cancel and runs to its
end when released), so that after the stop its step is still being awaited
for as long as the test holds it: a route that tore down in that time tore
down under a live ladder. Waits are counted in turns of the loop; the one
timeout is a deadlock guard.

Each mutant was applied to a byte-for-byte backup of the file it changes and
the file was restored byte-identical (SHA-256 compared) afterwards. The
failures are quoted as observed (``--tb=short``).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import astrodeck.api.app as app_module
import astrodeck.sequence.resume_arm as resume_arm_module
from astrodeck.profiles import Profile, profiles as profile_lib
from astrodeck.sequence.session import session_store
from test_flows_continue import rig  # noqa: F401 (fixture)
from test_resume_ladder_stops import (DEADLOCK_GUARD_S, TURN_CAP,  # noqa: F401
                                      _finish, _park, _stood_down, ladder)

#: route -> the app hub method its teardown runs through.
TEARDOWN = {"disconnect": "disconnect_all", "apply": "apply_profile",
            "activate": "connect_profile_id", "connect": "connect_rigspec"}

#: The three routes that take ``force`` and refuse without it.
FORCEABLE = ("apply", "activate", "connect")


@pytest.fixture
def teardown(ladder, tmp_path, monkeypatch):
    """The ladder harness, a stored profile for the two profile routes, and
    a recorder on each teardown method of the app's hub. Each record is
    ``(method, ladder_running)``, taken when the teardown BEGINS to run:
    for the two profile routes that is inside the connect task
    ``_spawn_connect`` starts, which is where the real teardown runs."""
    monkeypatch.setattr(profile_lib, "_dir", tmp_path / "profiles")
    prof = Profile(name="teardown rig", primary_backend="sim")
    profile_lib.save(prof)
    torn: list[tuple[str, bool]] = []

    def recorder(method: str):
        async def record(*a, **kw):
            torn.append((method, ladder.arm.recovering))
            return {"recorded": method}
        return record

    for method in TEARDOWN.values():
        monkeypatch.setattr(app_module.hub, method, recorder(method))
    yield SimpleNamespace(lad=ladder, torn=torn, profile_id=prof.id)


def _post(t, route: str, *, force: bool):
    c = t.lad.rig.client
    body = {"force": True} if force else {}
    if route == "disconnect":
        return c.post("/api/disconnect")
    if route == "apply":
        return c.post(f"/api/profiles/{t.profile_id}/apply", json=body)
    if route == "activate":
        return c.post(f"/api/profiles/{t.profile_id}/activate", json=body)
    if route == "connect":
        return c.post("/api/connect/rig", json={"primary": "sim", **body})
    raise AssertionError(route)


async def _connect_task_done() -> None:
    """The two profile routes run their teardown on the task
    ``_spawn_connect`` starts; let it finish."""
    task = app_module._connect_task
    if task is not None:
        await asyncio.wait_for(task, DEADLOCK_GUARD_S)


async def _spin() -> None:
    """Turns of the loop in which a route that does not wait would reach its
    teardown, and the connect task it spawned would run."""
    for _ in range(TURN_CAP // 10):
        await asyncio.sleep(0)


def _stopped_by_hand(bus_lines) -> list[str]:
    return [m for lvl, m, _src in bus_lines
            if lvl == "info" and "stopped by hand" in m]


@pytest.mark.parametrize("route", list(TEARDOWN))
async def test_a_teardown_route_stops_the_ladder_and_waits_for_it(
        teardown, bus_lines, route):
    """The ladder is parked in a stubborn solve and the route is sent
    (``disconnect``, or ``force`` for the other three). While the solve is
    held, the route must not have torn anything down, and it must not have
    answered. Released, the solve runs to its end, the ladder finds the stop
    and returns without its next step (no goto), and only then does the
    route tear down. The session it was recovering is disarmed, as Abort
    disarms it (6.15), and no start follows.

    RED under mutant "disconnect ignores the ladder" (``disconnect`` without
    its ``stop_recovery`` call and its wait), ``[disconnect]`` only,
    observed verbatim:

        E   AssertionError: disconnect tore down while the ladder's step was still awaited: [('disconnect_all', True)]
        E   assert [('disconnect_all', True)] == []

    RED under mutant "teardown before the ladder has returned" (each
    route's ``await _wait_for_the_ladder()`` removed, the stop kept), every
    case, observed verbatim:

        [disconnect]
        E   AssertionError: disconnect tore down while the ladder's step was still awaited: [('disconnect_all', True)]
        [apply]
        E   AssertionError: apply tore down while the ladder's step was still awaited: [('apply_profile', True)]
        [activate]
        E   AssertionError: activate tore down while the ladder's step was still awaited: [('connect_profile_id', True)]
        [connect]
        E   AssertionError: connect tore down while the ladder's step was still awaited: [('connect_rigspec', True)]

    RED under mutant "force does not stop the ladder" (the forced
    ``stop_recovery`` call removed from ``apply_profile``, the wait kept),
    ``[apply]`` only: nothing asked the ladder to stop, so released it went
    on to its goto and started the session, observed verbatim:

        E   AssertionError: the ladder went on after apply: ['solve', 'goto']
        E   assert ['solve', 'goto'] == ['solve']
    """
    t = teardown
    lad = t.lad
    solve = lad.hub.steps["solve"]
    solve.stubborn = True
    before_starts = len(lad.rig.starts)
    tick = await _park(lad, "solve")
    sent = asyncio.ensure_future(_post(t, route, force=True))
    try:
        await _spin()
        during = list(t.torn)
        answered_early = sent.done()
        still_recovering = lad.arm.recovering
        solve.release.set()
        r = await asyncio.wait_for(sent, DEADLOCK_GUARD_S)
        await _connect_task_done()
    finally:
        await _finish(lad, tick)
        if not sent.done():
            sent.cancel()

    assert during == [], (
        f"{route} tore down while the ladder's step was still awaited: "
        f"{during}")
    assert answered_early is False, f"{route} answered before the ladder returned"
    assert still_recovering is True, "premise: the stubborn solve held the ladder"
    assert r.status_code == 200, r.text
    assert lad.hub.calls == ["solve"], (
        f"the ladder went on after {route}: {lad.hub.calls}")
    assert solve.ended == "ran to its end", "premise: the solve swallowed the cancel"
    assert t.torn == [(TEARDOWN[route], False)], (
        f"{route} did not tear down once, after the ladder: {t.torn}")
    assert lad.rig.starts[before_starts:] == [], (
        f"a start followed {route}: {lad.rig.starts[before_starts:]}")
    stored = session_store.load(lad.session.id)
    assert stored.auto_resume is False, f"{route} left the session armed"
    assert (stored.status, stored.crash_resumes) == ("dormant", 0)
    assert len(_stopped_by_hand(bus_lines)) == 1, bus_lines
    assert len(_stood_down(bus_lines)) == 1, bus_lines


@pytest.mark.parametrize("route", FORCEABLE)
async def test_unforced_the_three_refuse_while_the_ladder_runs(
        teardown, bus_lines, route):
    """Without ``force``, apply, activate and connect answer 409 with the
    code a run gets (``running``, so the clients that offer "force" on it
    need nothing new), and a detail that names auto-resume's re-centring.
    They touch nothing: no teardown, the ladder is not stopped, the session
    stays armed, and once released the ladder re-centres and ResumeArm
    starts the session as it would have.

    RED under mutant "apply's guard ignores recovering" (``apply_profile``'s
    guard reverts to ``engine.running or hub.looping or hub.polar.running``),
    ``[apply]`` only, observed verbatim:

        E   AssertionError: apply was not refused while the ladder ran: 200 {"started":"profile"}
        E   assert 200 == 409

    and the same line for ``[activate]`` and ``[connect]`` under the same
    mutation of their own guard (``connect`` answering
    ``{"recorded":"connect_rigspec"}``).
    """
    t = teardown
    lad = t.lad
    before_starts = len(lad.rig.starts)
    tick = await _park(lad, "solve")
    try:
        r = await _post(t, route, force=False)
        await _connect_task_done()
        await _spin()
        still_parked = not tick.done()
        stop_asked = lad.arm._stop_why
    finally:
        await _finish(lad, tick)

    assert r.status_code == 409, (
        f"{route} was not refused while the ladder ran: {r.status_code} "
        f"{r.text}")
    detail = r.json()["detail"]
    assert detail["code"] == "running", detail
    assert "auto-resume" in detail["detail"] and "re-centring" in \
        detail["detail"], detail
    assert t.torn == [], f"{route} tore down while refusing: {t.torn}"
    assert still_parked and stop_asked is None, (
        f"an unforced {route} stopped the ladder")
    assert lad.hub.calls == ["solve", "goto"], lad.hub.calls
    assert [(s.won, s.session_id) for s in lad.rig.starts[before_starts:]] \
        == [(True, lad.session.id)], lad.rig.starts[before_starts:]
    assert _stopped_by_hand(bus_lines) == [] and _stood_down(bus_lines) == []


@pytest.mark.parametrize("route", list(TEARDOWN))
async def test_when_the_ladder_will_not_return_in_time_nothing_is_torn_down(
        teardown, bus_lines, monkeypatch, route):
    """The bound. The stubborn solve is held past ``LADDER_STOP_WAIT_S``
    (shrunk to 10 ms here; the solve is not released until the route has
    answered, so the bound expiring is certain, not a race). The route
    answers 409 ``running`` with a detail that says the re-centring has not
    stopped, and tears nothing down. The stop and the disarm stand: the
    operator asked for them, and once released the ladder stands down with
    no goto and no start.

    RED under mutant "teardown when the bound expires" (the wait's False
    ignored: ``_wait_for_the_ladder`` returns instead of raising), every
    case, observed verbatim:

        [disconnect]
        E   AssertionError: disconnect did not refuse when the ladder outlived the bound: 200 {"ok":true}
        E   assert 200 == 409
        [apply] [activate] [connect] the same line, with each route's body

    The same lines under "teardown before the ladder has returned" (every
    case) and "disconnect ignores the ladder" (``[disconnect]``).
    """
    monkeypatch.setattr(resume_arm_module, "LADDER_STOP_WAIT_S", 0.01)
    t = teardown
    lad = t.lad
    solve = lad.hub.steps["solve"]
    solve.stubborn = True
    before_starts = len(lad.rig.starts)
    tick = await _park(lad, "solve")
    try:
        r = await asyncio.wait_for(_post(t, route, force=True),
                                   DEADLOCK_GUARD_S)
        await _connect_task_done()
        torn_before_release = list(t.torn)
    finally:
        await _finish(lad, tick)

    assert r.status_code == 409, (
        f"{route} did not refuse when the ladder outlived the bound: "
        f"{r.status_code} {r.text}")
    detail = r.json()["detail"]
    assert detail["code"] == "running", detail
    assert "has not stopped" in detail["detail"], detail
    assert torn_before_release == [] and t.torn == [], (
        f"{route} tore down although the ladder had not returned: {t.torn}")
    assert lad.hub.calls == ["solve"], lad.hub.calls
    assert lad.rig.starts[before_starts:] == []
    assert session_store.load(lad.session.id).auto_resume is False


@pytest.mark.parametrize("route", list(TEARDOWN))
async def test_control_with_no_ladder_a_teardown_route_disarms_nothing(
        teardown, bus_lines, route):
    """CONTROL: no ladder running. Each route tears down as it always did
    (``disconnect`` as sent; the other three forced, the case that now asks
    the ladder to stop), and the armed session stays armed: a teardown
    disarms the ladder's session when it stops one, not whatever happens to
    be armed. The next tick then recovers and starts it.

    RED under mutant "stop ignores recovering" (``stop_recovery`` takes
    ``self._ladder_session or session_store.armed()`` and no longer asks
    ``self._recovering``), every case, observed verbatim:

        E   AssertionError: disconnect with no ladder disarmed the armed session
        E   assert False is True
        [apply] [activate] [connect] the same line, naming the route
    """
    t = teardown
    lad = t.lad
    r = await asyncio.wait_for(_post(t, route, force=True), DEADLOCK_GUARD_S)
    await _connect_task_done()
    assert r.status_code == 200, r.text
    assert t.torn == [(TEARDOWN[route], False)], t.torn
    assert session_store.load(lad.session.id).auto_resume is True, (
        f"{route} with no ladder disarmed the armed session")
    assert _stopped_by_hand(bus_lines) == [] and _stood_down(bus_lines) == []


@pytest.mark.parametrize("busy,recovering,expected", [
    (False, False, None),
    (True, False, app_module._TEARDOWN_BUSY),
    (False, True, app_module._TEARDOWN_WHILE_RECOVERING),
    (True, True, f"{app_module._TEARDOWN_BUSY}, and "
                 f"{app_module._TEARDOWN_WHILE_RECOVERING}"),
])
def test_the_refusal_names_everything_that_is_running(
        monkeypatch, busy, recovering, expected):
    """``_teardown_busy_detail`` in its four states. Nothing running: no
    refusal. A run (or a loop, or an alignment) alone: the sentence clients
    and test_connect_rig_guard.py already know, unchanged. The ladder alone:
    the sentence naming auto-resume's re-centring. Both: both, so an
    operator told only about the ladder does not force through a run they
    were never told about.

    RED under mutant "the ladder's sentence hides the run" (the recovering
    branch returns ``_TEARDOWN_WHILE_RECOVERING`` alone), ``[True-True-...]``
    only, observed verbatim:

        E   AssertionError: assert 'auto-resume ...en goes ahead' == 'a sequence, ...en goes ahead'
        E
        E     - a sequence, capture loop or polar alignment is running, and auto-resume is re-centring the mount after a restart (GET /api/sequence/resume-arm reports the step it is on); force stops the re-centring before its next step and turns that session's auto-resume off, as Abort does, then goes ahead
        E     + auto-resume is re-centring the mount after a restart (GET /api/sequence/resume-arm reports the step it is on); force stops the re-centring before its next step and turns that session's auto-resume off, as Abort does, then goes ahead
    """
    monkeypatch.setattr(app_module, "engine", SimpleNamespace(running=busy))
    monkeypatch.setattr(app_module, "hub", SimpleNamespace(
        looping=False, polar=SimpleNamespace(running=False)))
    monkeypatch.setattr(app_module, "resume_arm",
                        SimpleNamespace(recovering=recovering))
    assert app_module._teardown_busy_detail() == expected


@pytest.mark.parametrize("route", FORCEABLE)
async def test_control_unforced_with_no_ladder_the_three_proceed(
        teardown, route):
    """CONTROL: nothing running and no ladder, no ``force``. The guard that
    now counts the ladder refuses nothing it did not refuse before."""
    t = teardown
    r = await asyncio.wait_for(_post(t, route, force=False), DEADLOCK_GUARD_S)
    await _connect_task_done()
    assert r.status_code == 200, r.text
    assert t.torn == [(TEARDOWN[route], False)], t.torn
