"""No start while auto-resume re-centres the mount (#189 item A7, spec 5.9
"One starter per session").

ResumeArm's recovery ladder runs for minutes after a restart: a safety read,
perhaps an autofocus, a blind plate solve, then a re-centring slew. It moves
the focuser, takes the camera and slews the mount, and nothing about it is a
run, so ``engine.running`` is False the whole time. A manual start in that
time put a second motion source on the rig: the run's first slew and the
ladder's re-centring could be in flight together, and the ladder only notices
at its next step (``_a_run_took_over``), never in the middle of a slew. So
every HTTP start path now asks ``resume_arm.recovering`` immediately before
``engine.start``, with no await between, and answers 409
``resume_recovering``. ResumeArm raises the flag in the same synchronous
stretch as its own ``engine.running`` check (T4), so a route runs either
wholly before that check, and the tick then finds the engine running, or
wholly inside the flag.

THE HARNESS is ``test_flows_continue.py``'s: the real app over ASGI on this
loop and a real ``SequenceEngine`` whose imaging loop alone is replaced, with a
recorder on ``engine.start``. ResumeArm is real too, with the steps that read
the sky or move the mount pinned (``_window_open``, ``_devices_ready``,
``_recover``, the same pins as ``test_flows_continue_race.py``). ``_recover``
parks on an Event, so the tick is held inside the ladder, flag raised, for as
long as the test wants. The route reads ``app_module.resume_arm``, the one
instance production builds; the test puts its own ResumeArm there, on the
rig's engine, so the start that follows the release lands in the recorder.

Every armed session here is night one of a flow (``_night_one``): dormant,
armed by ``engine.start``, holding a frame. So ``/api/sessions/{id}/resume``
and ``/api/sequence/recover`` both find it, CONTINUE continues it, and it is
the session ResumeArm starts once the ladder is released. It is made by the
engine directly, not through ``/api/flows/{id}/run``, so a mutant in a start
route fails the route under test and never the setup.

Each mutant was applied to a byte-for-byte backup of the file it changes and
the file was restored byte-identical (SHA-256 compared) afterwards. The
failures are quoted as observed.
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.api.app as app_module
import astrodeck.hub as hub_module
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target
from astrodeck.sequence.resume_arm import ResumeArm
from astrodeck.sequence.session import Session, session_store
from test_flows_continue import LR, _compiled, rig  # noqa: F401 (fixture)

#: A deadlock guard for waits that a healthy run satisfies at once.
WAIT_TIMEOUT_S = 30.0

ROUTES = ("sequence_start", "flow_fresh", "flow_continue", "session_resume",
          "sequence_recover")


class _ArmHub:
    """What ``ResumeArm.tick`` reads of the hub once its sky and device steps
    are pinned: no dusk preparation, and a camera that is there."""
    site: dict = {}
    dusk_arm = None

    def require(self, role):
        return object()


class _Ladder:
    """Holds the pinned ``_recover`` until ``release`` is set."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()


@pytest.fixture
def arm(rig, monkeypatch):
    """A real ResumeArm on the rig's engine, installed as the app's
    ``resume_arm``, whose ladder parks until the test releases it."""
    ladder = _Ladder()

    async def recover(self, session):
        ladder.entered.set()
        await ladder.release.wait()
        return None

    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    monkeypatch.setattr(ResumeArm, "_devices_ready", lambda self: True)
    monkeypatch.setattr(ResumeArm, "_recover", recover)
    a = ResumeArm(rig.engine, _ArmHub())
    a.ladder = ladder
    monkeypatch.setattr(app_module, "resume_arm", a)
    return a


def _plan_body() -> dict:
    """A classic plan for ``/api/sequence/start``: one target, two frames."""
    plan = SequencePlan(name="classic", targets=[Target(
        name="M42", ra_hours=5.5881, dec_deg=-5.3911,
        steps=[ExposureStep(filter="L", exposure_s=1.0, count=2)])])
    return {**plan.model_dump(mode="json"), "force": False}


async def _start(rig, route: str, fid: str, session_id: str):
    """POST the start ``route`` names."""
    c = rig.client
    if route == "sequence_start":
        return await c.post("/api/sequence/start", json=_plan_body())
    if route == "flow_fresh":
        return await c.post(f"/api/flows/{fid}/run", json={"fresh": True})
    if route == "flow_continue":
        return await c.post(f"/api/flows/{fid}/run", json={})
    if route == "session_resume":
        return await c.post(f"/api/sessions/{session_id}/resume")
    if route == "sequence_recover":
        return await c.post("/api/sequence/recover")
    raise AssertionError(route)


def _session_files() -> dict[str, bytes]:
    """Every file under the sessions directory, by relative path."""
    root = hub_module.CAPTURE_DIR / "sessions"
    return {str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


async def _night_one(rig, fid: str) -> Session:
    """Night one of flow ``fid``, as ``run_flow`` would have started it: the
    same compile and ``flow_id`` (``_compiled``, so the same ids), one frame
    banked through the engine's ledger write, and the night ended through
    its own finalize. Dormant, armed, holding a frame."""
    rig.engine.start(_compiled(LR, fid), origin="flow", origin_id=fid)
    rig.bank([0])
    sid = rig.engine._session.id
    await rig.end_night("incomplete")
    return session_store.load(sid)


def _started_session(route: str, armed_id: str) -> str | None:
    """The session a successful start of ``route`` runs: None for a fresh one."""
    return None if route in ("sequence_start", "flow_fresh") else armed_id


@pytest.mark.parametrize("route", ROUTES)
async def test_a_start_while_the_ladder_recovers_is_refused(rig, arm, route):
    """ResumeArm is inside its recovery ladder, flag raised. Each start path
    must answer 409 ``resume_recovering``, never reach ``engine.start``, and
    leave every session file byte-identical. Released, the ladder finishes and
    ResumeArm starts the armed session itself: one motion source, and the one
    that was already moving.

    RED under mutant "no recovering check" on each route (the helper call
    removed from that route alone). ``flow_fresh`` and ``flow_continue`` share
    run_flow's one call, so its mutant turns both red, and in every run the
    other routes' cases stayed green: each call is pinned by its own case.
    Observed verbatim (``--tb=short``):

    ``/api/sequence/start``:

        ______ test_a_start_while_the_ladder_recovers_is_refused[sequence_start] ______
        E   AssertionError: {"started":true,"frames":2}
        E   assert 200 == 409

    ``/api/flows/{id}/run``:

        ________ test_a_start_while_the_ladder_recovers_is_refused[flow_fresh] ________
        E   AssertionError: {"started":true,"flow_id":"63ac70fc337541c4aac881d0ffe9ead8","frames":5,"unmapped":[],"session":{"id":"b7feb650a1b748a799b32a51be46d472","night":1,"continued":false,"kept":0,"new":2,"dropped":0}}
        E   assert 200 == 409
        ______ test_a_start_while_the_ladder_recovers_is_refused[flow_continue] _______
        E   AssertionError: {"started":true,"flow_id":"7855ac4fda024c3e88b4c74634324a1a","frames":5,"unmapped":[],"session":{"id":"4deff9a45fa5469687980f4730cdccb7","night":2,"continued":true,"kept":2,"new":0,"dropped":0}}
        E   assert 200 == 409

    ``/api/sessions/{id}/resume``:

        ______ test_a_start_while_the_ladder_recovers_is_refused[session_resume] ______
        E   AssertionError: {"resumed":true,"remaining":4}
        E   assert 200 == 409

    ``/api/sequence/recover``:

        _____ test_a_start_while_the_ladder_recovers_is_refused[sequence_recover] _____
        E   AssertionError: {"resumed":true,"frames_remaining":4}
        E   assert 200 == 409

    The two assertions behind the 409 each fail on their own. RED under
    mutant "check after the start" (``/resume``'s helper call moved below
    its ``engine.start``, so the answer is still 409):

        ______ test_a_start_while_the_ladder_recovers_is_refused[session_resume] ______
        E   AssertionError: a refused start reached engine.start: [Start(won=True, session_id='3654ea98f0234690acd54e889244e822', frames=[('7bf31fb5977f5ab4a6198c94f24d96c2', '1e7a6ef163bb576bb80ad6e48f4f9e93')], frame_ids=['63a637293c0a4fa181fdd3319dba66f4'], step_ids=['1e7a6ef163bb576bb80ad6e48f4f9e93', '441b2763353d562dbfe5855297e009f3'], count_mode='attempts', cool_to=None, error='')]
        E   assert [Start(won=Tr...ne, error='')] == []

    RED under mutant "the refused route saves first" (``session_store.save(s)``
    ahead of ``/resume``'s helper call):

        ______ test_a_start_while_the_ladder_recovers_is_refused[session_resume] ______
        E   AssertionError: a refused start changed a session file: ['e1e9845adec742a9bc07ea2d06dcb6a8.json']
    """
    fid = await rig.save_flow(LR)
    one = await _night_one(rig, fid)
    assert session_store.armed().id == one.id, "premise: armed for ResumeArm"
    before_files = _session_files()
    before_starts = len(rig.starts)

    tick = asyncio.create_task(arm.tick())
    try:
        await asyncio.wait_for(arm.ladder.entered.wait(), WAIT_TIMEOUT_S)
        assert arm.recovering, "premise: the ladder is running"
        r = await _start(rig, route, fid, one.id)
        after_files = _session_files()
        refused_starts = rig.starts[before_starts:]
    finally:
        arm.ladder.release.set()
        await asyncio.wait_for(tick, WAIT_TIMEOUT_S)

    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert isinstance(detail, dict), detail
    assert detail["code"] == "resume_recovering", detail
    words = detail["detail"].lower()
    assert "auto-resume" in words and "re-centring" in words, words
    assert "disarm" in words and "wait" in words, words
    assert refused_starts == [], (
        f"a refused start reached engine.start: {refused_starts}")
    assert after_files == before_files, (
        "a refused start changed a session file: "
        f"{sorted(k for k in after_files if after_files.get(k) != before_files.get(k))}")
    # Released, the ladder finishes and ResumeArm starts the armed session.
    assert [(s.won, s.session_id) for s in rig.starts[before_starts:]] == [
        (True, one.id)], rig.starts[before_starts:]
    assert arm.recovering is False


@pytest.mark.parametrize("route", ROUTES)
async def test_control_with_no_ladder_running_each_route_starts(rig, arm,
                                                                route):
    """CONTROL: the same rig, the same armed session, the same ResumeArm
    installed, and no ladder running. Each route starts exactly as it did
    before the check was added.

    RED under mutant "the helper always refuses" (``if resume_arm.recovering``
    -> ``if True``), every case, on the route under test and not in the setup
    (which is why ``_night_one`` makes the session without a route). Observed
    verbatim for the first; the other four are the same line:

        ____ test_control_with_no_ladder_running_each_route_starts[sequence_start] ____
        E   AssertionError: {"detail":{"detail":"Auto-resume is re-centring the mount after a restart and will start its armed session when that is done: wait for it, or disarm auto-resume and start again once the re-centring has finished.","code":"resume_recovering"}}
        E   assert 409 == 200
    """
    fid = await rig.save_flow(LR)
    one = await _night_one(rig, fid)
    assert arm.recovering is False
    before_starts = len(rig.starts)

    r = await _start(rig, route, fid, one.id)

    assert r.status_code == 200, r.text
    started = rig.starts[before_starts:]
    assert [(s.won, s.session_id) for s in started] == [
        (True, _started_session(route, one.id))], started


class _WatchedFlag:
    """Stands in for the app's ``resume_arm`` to see what runs between a
    route's read of ``recovering`` and its ``engine.start``. Each read answers
    False, marks itself unbroken, and puts a callback on the loop that breaks
    the mark. The loop runs that callback only once the route yields to it,
    so a start that still finds the mark unbroken had no turn of the loop
    after the check: nothing else, ResumeArm's tick included, ran between."""

    def __init__(self) -> None:
        self._mark: list[bool] | None = None

    @property
    def recovering(self) -> bool:
        mark = [True]
        self._mark = mark
        asyncio.get_running_loop().call_soon(mark.__setitem__, 0, False)
        return False

    def unbroken(self) -> bool | None:
        """None when ``recovering`` was never read, else whether the loop has
        not turned since the latest read."""
        return None if self._mark is None else self._mark[0]


@pytest.mark.parametrize("route", ROUTES)
async def test_no_turn_of_the_loop_between_the_check_and_the_start(
        rig, monkeypatch, route):
    """A flag is enough only because nothing runs between a route's check and
    its start. ResumeArm raises ``recovering`` in the same synchronous stretch
    as its own ``engine.running`` check, so a route that yields after its
    check can let a whole tick through: the route saw the flag down, the tick
    saw the engine idle and entered its ladder, and the route then starts a
    run on a mount the ladder is about to slew. So each route's
    ``engine.start`` must find that the loop has not turned since the route
    read ``recovering``.

    The parked-ladder test above cannot see this. It raises the flag BEFORE
    the request, so a check that sits above an await still finds it raised
    and refuses. Under mutant "check above the CONTINUE read" (run_flow's
    helper call moved above ``latest = await asyncio.to_thread(...)``) every
    one of its cases stayed green, and this one went red.

    RED under mutant "an await between the check and the start"
    (``await asyncio.sleep(0)`` inserted after the helper call) on each of the
    four call sites in turn, only on that site's cases, and under "check
    above the CONTINUE read" on ``flow_continue`` alone (``flow_fresh`` does
    not await that read, so no gap opens for it). Observed verbatim
    (``--tb=short``), the ``/resume`` site first:

        __ test_no_turn_of_the_loop_between_the_check_and_the_start[session_resume] ___
        E   AssertionError: the loop turned between the route's recovering check and its engine.start, so a ResumeArm tick could have entered its ladder in between
        E   assert False is True

    and the same two lines under ``[sequence_start]`` and
    ``[sequence_recover]`` for their sites, under ``[flow_fresh]`` and
    ``[flow_continue]`` for run_flow's one call, and under
    ``[flow_continue]`` alone for "check above the CONTINUE read".

    The "no recovering check" mutants above turn this test red as well, on
    the route they strip: ``the route never read resume_arm.recovering`` /
    ``assert None is not None``.
    """
    fid = await rig.save_flow(LR)
    one = await _night_one(rig, fid)
    watch = _WatchedFlag()
    monkeypatch.setattr(app_module, "resume_arm", watch)
    seen: list[bool | None] = []
    recorded = rig.engine.start

    def start(plan, **kw):
        seen.append(watch.unbroken())
        return recorded(plan, **kw)

    monkeypatch.setattr(rig.engine, "start", start)

    r = await _start(rig, route, fid, one.id)

    assert r.status_code == 200, r.text
    assert len(seen) == 1, f"premise: one start, not {len(seen)}"
    assert seen[0] is not None, "the route never read resume_arm.recovering"
    assert seen[0] is True, (
        "the loop turned between the route's recovering check and its "
        "engine.start, so a ResumeArm tick could have entered its ladder "
        "in between")
