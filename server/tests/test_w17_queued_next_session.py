# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#598 (WP-133, backlog ruling D-04, owner-approved 2026-09-30): queue a
'next' session behind a live or armed run.

THE PROBLEM. The armed session is a singleton, so "run A now, then armed B"
leaves one of the two unprotected whichever way it is arranged: arming B
disarms A's restart resume (#595), and starting A disarms B. On 2026-09-29 the
owner wanted the nightly NGC 7331 run and then the armed NGC 1499 mosaic.

THE FIX, AS THIS FILE HOLDS IT. A dormant session B can WAIT BEHIND the run
that is live, else the session that is armed (``PATCH {queue_next: true}``):

* the marker (``Session.queued_behind``) is NOT arming, and touches no
  ``auto_resume``: A keeps its restart protection, B is unarmed until
  promoted;
* B is armed by exactly one event, A COMPLETING (``SequenceEngine.
  _promote_queued``, called from ``_finalize_report``), and ResumeArm then
  starts it on its next tick the ordinary way. Never after a safety stop, an
  operator stop, a dawn cut-off or an error: those leave A owing frames and
  armed (or disarmed on purpose), and B stays queued;
* a queue of one per session: a second session queued behind the same one
  replaces the first, and says so; abandoning, starting or un-queueing B
  clears it;
* the singleton still holds at promotion: whatever else is armed then loses
  it, named.

HARNESS. The route half drives the REAL app (``create_app`` over
``httpx.ASGITransport`` on the test's own loop, no lifespan, the isolation
``_flow_night.FlowRig`` uses) with the test's engine put in ``app.engine``'s
place, over a real simulator hub. The engine half is the harness of
``test_w14_autoresume_option.py``: a real run, ``ResumeArm`` with an injected
clock and ``_window_open`` answered True so the tick never asks the sky, and
``_finalize_report`` called directly over a stored session for the endings a
run cannot be made to reach on demand.

NAMED MUTANTS (each run from a byte backup inside this worktree, restored and
sha256-compared, the mutant text grepped out afterwards; the failing assertion
is quoted in the docstring of the test that catches it):

* "queue disarms the live run": the queue branch also disarms the session it
  waits behind;
* "promote on any end reason": `_finalize_report` drops its
  ``status == "complete"`` guard;
* "marker survives start": `engine.start` no longer clears the marker;
* "second queue keeps the first": the replacement loop is dropped;
* "abandon keeps the marker": the abandoned-session clear is dropped;
* "promote an abandoned session": the promotion drops its dormant filter;
* "promotion skips the singleton": `_arm_exclusively` leaves the others armed.
"""
from __future__ import annotations

import asyncio
import time

import httpx
import pytest

import astrodeck.api.app as app_module
import astrodeck.hub as hub_module
from astrodeck.auth import reset_active_provider
from astrodeck.config import ConfigStore
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target
from astrodeck.sequence.resume_arm import ResumeArm
from astrodeck.sequence.session import Session, SessionFrame, session_store
from conftest import _sweep_config_store  # rootdir-relative, as _flow_night does


async def wait_for(predicate, timeout=30.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


def _plan(name: str, count: int = 3) -> SequencePlan:
    return SequencePlan(
        name=name, guide=False, dither_every=0, autofocus_every=0,
        meridian_flip=False,
        targets=[Target(name=name, ra_hours=5.5881, dec_deg=-5.3911,
                        center=False, autofocus_first=False,
                        steps=[ExposureStep(filter="L", exposure_s=0.05,
                                            count=count)])])


def _stored(name: str, *, status: str = "dormant", count: int = 3,
            done: int = 1, auto_resume: bool = False,
            queued_behind: str | None = None) -> Session:
    """A session written straight to the store with ``done`` banked frames:
    the endings a run cannot be made to reach on demand are graded over it."""
    plan = _plan(name, count)
    s = Session(name=name, created_ts=1.0, status=status, plan=plan,
                auto_resume=auto_resume, queued_behind=queued_behind)
    step = plan.targets[0].steps[0]
    for _ in range(done):
        s.frames.append(SessionFrame(ts=1.0, night="n1",
                                     target_id=plan.targets[0].id,
                                     step_id=step.id, path="f.fits",
                                     metrics={}, auto_accepted=True))
    session_store.save(s)
    return s


class Rig:
    def __init__(self, hub: Hub, engine: SequenceEngine,
                 client: httpx.AsyncClient) -> None:
        self.hub = hub
        self.engine = engine
        self.client = client

    async def patch(self, sid: str, **body):
        return await self.client.patch(f"/api/sessions/{sid}", json=body)

    async def dormant_by_a_real_run(self, name: str, count: int = 6) -> str:
        """A real dormant session with one frame banked and one night
        recorded: start, bank a frame, abort (which disarms it, as an abort
        by hand does)."""
        self.engine.start(_plan(name, count))
        sid = self.engine._session.id
        assert await wait_for(lambda: self.engine._frames_done >= 1)
        await self.engine.abort()
        s = session_store.load(sid)
        assert s.status == "dormant" and s.observing_nights()
        return sid

    def resume_arm(self) -> ResumeArm:
        arm = ResumeArm(self.engine, self.hub, clock=time.time)
        arm._window_open = lambda s, t: True        # the tick never asks the sky
        return arm


@pytest.fixture
async def rig(tmp_path, monkeypatch):
    """A simulator hub and engine, the real app serving them, and a throwaway
    captures directory and config store (``_flow_night.FlowRig.open``'s
    isolation, without the clocked night)."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(store.cfg().safety, "solar_avoidance", False)
    _sweep_config_store(monkeypatch, store)
    monkeypatch.delenv(app_module.AUTH_ENV_VAR, raising=False)
    reset_active_provider()
    hub = Hub()
    await hub.connect_sim()
    engine = SequenceEngine(hub)
    monkeypatch.setattr(app_module, "engine", engine)
    app = app_module.create_app()
    _sweep_config_store(monkeypatch, store)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                               base_url="http://testserver")
    try:
        yield Rig(hub, engine, client)
    finally:
        if engine.running:
            await engine.abort()
        await client.aclose()
        reset_active_provider()
        await hub.disconnect_all()


def _lines(bus_lines) -> list[str]:
    return [m for _lvl, m, _src in bus_lines]


# ------------------------------------------------------------------ the route

async def test_queueing_waits_behind_the_armed_session_and_touches_no_arming(
        rig, bus_lines):
    """B is queued behind the armed A: the marker is set, A keeps its
    auto-resume, B is not armed, and ``armed()`` still answers A. That is the
    whole point of the feature: the old way (arming B) disarmed A.

    MUTANT "queue disarms the live run" (the queue branch also saves the
    session it waits behind with its auto_resume off) turned this red, run
    from a byte backup and restored and sha256-verified afterwards:

        AssertionError: queueing must leave the session it waits behind armed:
        arming B instead is the singleton disarm this exists to avoid (#595)
        assert False is True
    """
    a = _stored("tonight", auto_resume=True)
    b = _stored("mosaic")
    r = await rig.patch(b.id, queue_next=True)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["queued_behind"] == {"id": a.id, "name": "tonight"}
    assert body["auto_resume"] is False
    assert "disarmed" not in body and "dequeued" not in body
    assert session_store.load(b.id).queued_behind == a.id
    assert session_store.load(b.id).auto_resume is False
    assert session_store.load(a.id).auto_resume is True, (
        "queueing must leave the session it waits behind armed: arming B "
        "instead is the singleton disarm this exists to avoid (#595)")
    armed = session_store.armed()
    assert armed is not None and armed.id == a.id
    assert any("waits behind 'tonight'" in m for m in _lines(bus_lines))


async def test_a_session_waits_behind_the_live_run_in_preference_to_the_armed(
        rig):
    """With a run live, the session to wait behind is the RUN, whatever
    ``armed()`` says (it answers the dormant armed one only, never the live
    one): queueing behind the live A is the case the feature is for."""
    sid_b = await rig.dormant_by_a_real_run("mosaic")
    rig.engine.start(_plan("tonight", count=40))
    a_id = rig.engine._session.id
    assert session_store.armed() is None, "premise: a live run is not 'armed()'"
    r = await rig.patch(sid_b, queue_next=True)
    assert r.status_code == 200, r.text
    assert r.json()["queued_behind"] == {"id": a_id, "name": "tonight"}
    assert session_store.load(sid_b).queued_behind == a_id
    assert session_store.load(a_id).auto_resume is True


async def test_queueing_with_nothing_to_wait_behind_is_refused(rig):
    b = _stored("mosaic")
    r = await rig.patch(b.id, queue_next=True)
    assert r.status_code == 409
    assert "nothing is running or armed to wait behind" in r.text
    assert "arm it instead" in r.text
    assert session_store.load(b.id).queued_behind is None


async def test_a_session_cannot_wait_behind_itself(rig):
    """B is the only armed session, so the one to wait behind would be B."""
    b = _stored("mosaic", auto_resume=True)
    r = await rig.patch(b.id, queue_next=True)
    assert r.status_code == 409
    assert "behind itself" in r.text
    assert session_store.load(b.id).queued_behind is None


@pytest.mark.parametrize("status", ["active", "complete", "abandoned"])
async def test_only_a_dormant_session_can_be_queued(rig, status):
    _stored("tonight", auto_resume=True)
    b = _stored("mosaic", status=status)
    r = await rig.patch(b.id, queue_next=True)
    assert r.status_code == 409
    assert "only a dormant session" in r.text
    assert session_store.load(b.id).queued_behind is None


async def test_queueing_and_arming_in_one_request_is_refused_before_it_writes(
        rig):
    """Arming disarms the others, saved one by one before the queue is looked
    at: so asking for both would write the very disarm the queue exists to
    avoid, and the 422 must come before any of it."""
    a = _stored("tonight", auto_resume=True)
    b = _stored("mosaic")
    r = await rig.patch(b.id, queue_next=True, auto_resume=True)
    assert r.status_code == 422
    assert session_store.load(a.id).auto_resume is True
    assert session_store.load(b.id).auto_resume is False
    assert session_store.load(b.id).queued_behind is None


async def test_a_second_queue_replaces_the_first_and_says_so(rig, bus_lines):
    """A queue of one per session. The replaced session is cleared, NAMED in
    a warning (the D-04 visibility rule: a silent replacement is the silent
    disarm over again) and in the response.

    MUTANT "second queue keeps the first" (the replacement loop dropped)
    turned this red, run from a byte backup and restored and sha256-verified
    afterwards:

        AssertionError: the replaced session must be named in the response
        assert None == [{'id': '<first session id>', 'name': 'first'}]
    """
    a = _stored("tonight", auto_resume=True)
    first = _stored("first")
    second = _stored("second")
    assert (await rig.patch(first.id, queue_next=True)).status_code == 200
    r = await rig.patch(second.id, queue_next=True)
    assert r.status_code == 200, r.text
    assert r.json()["queued_behind"] == {"id": a.id, "name": "tonight"}
    assert r.json().get("dequeued") == [{"id": first.id, "name": "first"}], (
        "the replaced session must be named in the response")
    assert session_store.load(first.id).queued_behind is None, (
        "the session that lost its place must be cleared, not left waiting "
        "beside the new one")
    assert session_store.load(second.id).queued_behind == a.id
    warnings = [m for lvl, m, _ in bus_lines if lvl == "warning"]
    assert any("'second' now waits behind 'tonight' in place of: first" in m
               for m in warnings), warnings


async def test_queueing_the_same_session_again_replaces_nothing(rig):
    a = _stored("tonight", auto_resume=True)
    b = _stored("mosaic")
    assert (await rig.patch(b.id, queue_next=True)).status_code == 200
    r = await rig.patch(b.id, queue_next=True)
    assert r.status_code == 200
    assert "dequeued" not in r.json()
    assert session_store.load(b.id).queued_behind == a.id


async def test_queue_next_false_clears_the_marker(rig):
    a = _stored("tonight", auto_resume=True)
    b = _stored("mosaic", queued_behind=a.id)
    r = await rig.patch(b.id, queue_next=False)
    assert r.status_code == 200
    assert r.json()["queued_behind"] is None
    assert session_store.load(b.id).queued_behind is None
    # The answer to a request that did not ask about the queue is unchanged.
    plain = await rig.patch(b.id, auto_resume=False)
    assert "queued_behind" not in plain.json()


async def test_abandoning_a_queued_session_clears_the_marker(rig):
    """An abandoned session is withdrawn from the shelf and waits for nothing.

    MUTANT "abandon keeps the marker" (the abandoned-session clear dropped)
    turned this red, run from a byte backup and restored and sha256-verified
    afterwards:

        AssertionError: an abandoned session waits for nothing
        assert '<the session it waited behind>' is None
    """
    a = _stored("tonight", auto_resume=True)
    b = _stored("mosaic", queued_behind=a.id)
    r = await rig.patch(b.id, status="abandoned")
    assert r.status_code == 200, r.text
    after = session_store.load(b.id)
    assert after.status == "abandoned"
    assert after.queued_behind is None, "an abandoned session waits for nothing"


async def test_editing_a_queued_session_from_the_plan_keeps_its_place(rig):
    """UPDATE FROM PLAN is a plan replacement, not a withdrawal."""
    a = _stored("tonight", auto_resume=True)
    b = _stored("mosaic", queued_behind=a.id)
    r = await rig.patch(b.id, plan=_plan("mosaic", count=5).model_dump())
    assert r.status_code == 200, r.text
    assert session_store.load(b.id).queued_behind == a.id


async def test_the_session_list_carries_the_marker(rig):
    a = _stored("tonight", auto_resume=True)
    b = _stored("mosaic", queued_behind=a.id)
    rows = {r["id"]: r for r in
            (await rig.client.get("/api/sessions")).json()["sessions"]}
    assert rows[b.id]["queued_behind"] == a.id
    assert rows[a.id]["queued_behind"] is None


# ----------------------------------------------------------------- the engine

async def test_a_completed_run_arms_the_session_queued_behind_it(rig, bus_lines):
    """THE FEATURE END TO END, on the real app and engine: A runs, B is queued
    behind it by the route, A COMPLETES, B is armed (and only then), the
    marker is gone, the line says so, and ResumeArm's next tick starts B.

    MUTANT "marker survives start" is graded by the test after this one;
    MUTANT "promote on any end reason" by `test_no_other_ending_arms_it`.
    """
    b_id = await rig.dormant_by_a_real_run("mosaic", count=4)
    rig.engine.start(_plan("tonight", count=3))
    a_id = rig.engine._session.id
    r = await rig.patch(b_id, queue_next=True)
    assert r.status_code == 200, r.text
    # While A runs, B is queued and unarmed and A is the one that is armed.
    b = session_store.load(b_id)
    assert b.queued_behind == a_id and b.auto_resume is False
    assert session_store.load(a_id).auto_resume is True

    assert await wait_for(
        lambda: session_store.load(a_id).status == "complete")
    b = session_store.load(b_id)
    assert b.auto_resume is True and b.queued_behind is None
    assert b.status == "dormant"
    armed = session_store.armed()
    assert armed is not None and armed.id == b_id
    assert any("'tonight' is complete, so 'mosaic' is armed and starts when "
               "its window opens" in m for m in _lines(bus_lines)), \
        _lines(bus_lines)

    # The run task is still winding down (park, warm) after the finalize that
    # promoted B, and a tick passes over a live engine: wait for it to end.
    assert await wait_for(lambda: not rig.engine.running)
    await rig.resume_arm().tick()
    assert await wait_for(lambda: session_store.load(b_id).status == "complete")
    assert session_store.load(b_id).queued_behind is None


async def test_a_session_started_by_hand_stops_waiting(rig):
    """The marker is cleared by the START, whichever path started the session
    (CONTINUE, a flow run, /resume, ResumeArm): a session that has outrun the
    run it waited behind must not be armed behind it afterwards.

    MUTANT "marker survives start" (`engine.start` no longer clears it)
    turned this red, run from a byte backup and restored and sha256-verified
    afterwards:

        AssertionError: starting a session ends its wait, on disk
        assert '<the session it waited behind>' is None
    """
    a = _stored("tonight", auto_resume=True)
    b = _stored("mosaic", count=5, queued_behind=a.id)
    rig.engine.start(b.plan, session=session_store.load(b.id))
    assert session_store.load(b.id).queued_behind is None, (
        "starting a session ends its wait, on disk")
    await rig.engine.abort()
    assert session_store.load(b.id).queued_behind is None


async def test_an_operator_abort_leaves_the_queue_waiting(rig):
    """A stopped by hand: A is dormant and DISARMED (an abort is a decision),
    B stays queued and unarmed, nothing is armed, and no tick starts B."""
    b_id = await rig.dormant_by_a_real_run("mosaic", count=4)
    rig.engine.start(_plan("tonight", count=60))
    a_id = rig.engine._session.id
    assert (await rig.patch(b_id, queue_next=True)).status_code == 200
    assert await wait_for(lambda: rig.engine._frames_done >= 1)
    await rig.engine.abort()
    a = session_store.load(a_id)
    assert a.status == "dormant" and a.auto_resume is False
    b = session_store.load(b_id)
    assert b.queued_behind == a_id and b.auto_resume is False
    assert session_store.armed() is None
    await rig.resume_arm().tick()
    assert not rig.engine.running
    assert session_store.load(b_id).status == "dormant"


def _finish(rig, session: Session, reason: str) -> None:
    """End ``session`` the way a run ends it: a fresh engine over the stored
    session, finalized with ``reason`` (the harness of
    ``test_w14_autoresume_option``)."""
    eng = SequenceEngine(rig.hub)
    eng._session = session_store.load(session.id)
    eng._report_finalized = False
    eng.reporter = None
    eng._finalize_report(reason)


async def test_a_completion_arms_the_session_queued_behind_it(rig):
    """The positive control for the endings below, over the same direct
    harness: a session with nothing owed, finalized 'complete', promotes. If
    this harness could not reach the promotion, the refusals below would pass
    for nothing."""
    a = _stored("tonight", count=3, done=3, auto_resume=True)
    b = _stored("mosaic", queued_behind=a.id)
    _finish(rig, a, "complete")
    assert session_store.load(a.id).status == "complete"
    after = session_store.load(b.id)
    assert after.auto_resume is True and after.queued_behind is None


@pytest.mark.parametrize("reason, done", [
    ("complete", 1),            # the run finished its pass but frames are owed
    ("dawn_cutoff", 1), ("incomplete", 1), ("unsafe", 1), ("aborted", 1),
    ("error", 1), ("quality", 1), ("cooling_skip", 1), ("shutdown", 1),
    # Nothing owed, but the run did not END 'complete': it is dormant by the
    # engine's own rule, and the queue follows the status, not the reason.
    ("dawn_cutoff", 3),
])
async def test_no_other_ending_arms_it(rig, reason, done):
    """A safety stop, an operator stop, a dawn cut-off, an error and the rest
    leave A owing frames and armed (or disarmed on purpose): B stays queued
    and UNARMED, and ``armed()`` is still A.

    MUTANT "promote on any end reason" (`_finalize_report` dropping its
    ``status == "complete"`` guard) turned all ten cases red (and left the
    positive control green), run from a byte backup and restored and
    sha256-verified afterwards:

        AssertionError: a 'unsafe' ending must not arm the session queued
        behind it
        assert True is False
    """
    a = _stored("tonight", count=3, done=done, auto_resume=True)
    b = _stored("mosaic", queued_behind=a.id)
    _finish(rig, a, reason)
    assert session_store.load(a.id).status == "dormant"
    after = session_store.load(b.id)
    assert after.auto_resume is False, (
        f"a {reason!r} ending must not arm the session queued behind it")
    assert after.queued_behind == a.id
    armed = session_store.armed()
    assert armed is not None and armed.id == a.id


async def test_promotion_leaves_a_session_that_is_not_dormant_alone(rig):
    """B was abandoned (or completed) after it was queued, by a path that left
    the marker: it is not waiting any more, and arming it would resurrect it.

    MUTANT "promote an abandoned session" (the promotion's dormant filter
    dropped) turned this red, run from a byte backup and restored and
    sha256-verified afterwards:

        AssertionError: an abandoned session is not promoted
        assert True is False
    """
    a = _stored("tonight", count=3, done=3, auto_resume=True)
    b = _stored("mosaic", status="abandoned", queued_behind=a.id)
    _finish(rig, a, "complete")
    after = session_store.load(b.id)
    assert after.status == "abandoned"
    assert after.auto_resume is False, "an abandoned session is not promoted"


async def test_promotion_keeps_the_singleton(rig, bus_lines):
    """Whatever else is armed when A completes loses it, NAMED: C was armed
    while B waited (which disarmed A on the route, but a hand edit or a
    restore can leave two), and promoting B beside C would be two armed.

    MUTANT "promotion skips the singleton" (`_arm_exclusively` leaves the
    others armed) turned this red, run from a byte backup and restored and
    sha256-verified afterwards:

        AssertionError: promotion arms exactly one session
        assert True is False
    """
    a = _stored("tonight", count=3, done=3, auto_resume=True)
    b = _stored("mosaic", queued_behind=a.id)
    c = _stored("other", auto_resume=True)
    _finish(rig, a, "complete")
    assert session_store.load(b.id).auto_resume is True
    assert session_store.load(c.id).auto_resume is False, (
        "promotion arms exactly one session")
    armed = session_store.armed()
    assert armed is not None and armed.id == b.id
    assert any(lvl == "warning" and "'mosaic'" in m and "other" in m
               and "disarmed auto-resume" in m
               for lvl, m, _ in bus_lines), bus_lines


async def test_a_second_session_found_waiting_is_left_queued_and_named(
        rig, bus_lines):
    """A queue of one is enforced where it is set. A hand edit that leaves two
    behind the same A arms ONE (the newest) and names the other: arming both
    would be the singleton broken, and arming neither would strand the queue."""
    a = _stored("tonight", count=3, done=3, auto_resume=True)
    older = _stored("older", queued_behind=a.id)
    time.sleep(0.02)
    newer = _stored("newer", queued_behind=a.id)
    _finish(rig, a, "complete")
    assert session_store.load(newer.id).auto_resume is True
    assert session_store.load(older.id).auto_resume is False
    assert session_store.load(older.id).queued_behind == a.id
    assert any("'newer' is armed" in m and "older" in m
               for m in _lines(bus_lines)), _lines(bus_lines)
