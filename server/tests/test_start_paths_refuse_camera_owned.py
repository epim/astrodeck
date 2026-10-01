"""Every HTTP start path refuses while the camera is someone else's (#323).

``_refuse_if_camera_owned`` answers 409 ``video_owns_camera`` while a .ser
recording runs and 409 ``dusk_connecting`` while dusk preparation connects
equipment. Its docstring names the hazard: a plan start is a SLEW, and one
made during a planetary recording "took the mount out from under the file and
left an hour of frames of empty sky". Until #323 only ``/api/sequence/start``
called it. ``/api/flows/{id}/run`` (fresh and CONTINUE),
``/api/sessions/{id}/resume`` and ``/api/sequence/recover`` did not, and
nothing below the route stood in for it: ``engine.start`` never looks at the
recorder, and the target setup slews before the first exposure meets the
hub's exposure guard.

Now one helper, ``_refuse_start_while_rig_is_held``, holds the guards every
start path must run, and all four routes call it FIRST, before any pre-flight,
and ``force`` does not reach it. This file holds that three ways:

* BEHAVIOUR: with a recording running, and separately with dusk connecting,
  each route answers its 409 and ``engine.start`` is never called; a
  CONTINUE writes nothing to the session it would have continued; ``force``
  changes nothing. CONTROL: with neither, each route starts as it did.
* STRUCTURE: the four route functions call the helper, with no argument,
  before anything that pre-flights the plan (parsed, not grepped), and the
  helper calls ``_refuse_if_camera_owned``.

THE HARNESS is ``test_flows_continue.py``'s ``rig``: the real app over ASGI on
the test's loop and a real ``SequenceEngine`` whose imaging loop alone is
replaced, with ``engine.start`` recorded. The recording is the recorder's own
``active`` property over a record in a non-terminal state; dusk is the
``DuskArm``'s own ``connecting`` flag.

Each mutation was applied to a byte-for-byte copy of ``api/app.py`` in a
private copy of ``server/`` (scratchpad ``s4-routes-mut``), never in the
shared tree, and only this file was run against it; failures are quoted as
observed, ids elided as "...".
"""
from __future__ import annotations

import ast
import inspect
import textwrap
from types import SimpleNamespace

import pytest

import astrodeck.api.app as app_module
from astrodeck.sequence.session import session_store
from test_flows_continue import (_bytes, _capture, _graph,
                                 rig)  # noqa: F401 (fixture)

#: L (4) then R (3), so a night that banks two leaves the session dormant
#: and owing: a session CONTINUE, resume and recover all have something for.
FLOW = _graph(_capture("c1", "L", count=4), _capture("c2", "R", count=3))

#: The refusals, as ``(code, sentence)``: the sentences are the helper's.
VIDEO = ("video_owns_camera", app_module._VIDEO_OWNS_CAMERA)
DUSK = ("dusk_connecting", "Dusk preparation is connecting equipment. Try "
        "again when it finishes.")


@pytest.fixture
def recording(monkeypatch):
    """A .ser recording in progress, as the recorder itself reports it: its
    ``active`` property is True for a record whose state is not terminal."""
    def start():
        monkeypatch.setattr(app_module.video_recorder, "_current",
                            SimpleNamespace(state="recording"))
        assert app_module.video_recorder.active, (
            "premise: the recorder reports a recording in progress")
    return start


@pytest.fixture
def dusk(monkeypatch):
    """Dusk preparation connecting equipment (``DuskArm.connecting``)."""
    def start():
        monkeypatch.setattr(app_module.dusk_arm, "connecting", True)
    return start


def _refusal(r) -> tuple[str | None, str]:
    """``(code, sentence)`` of a 409. A bare-string detail comes back with
    no code, so a refusal from somewhere other than the helper fails the
    comparison naming what it was, rather than as a TypeError: with dusk
    connecting and the guard dropped, ``engine.start``'s own backstop
    refuses AFTER the pre-flight (and after the preview loop was stopped),
    with a DeviceError's bare sentence."""
    detail = r.json().get("detail")
    if isinstance(detail, dict):
        return detail.get("code"), detail.get("detail")
    return None, detail


async def _dormant(rig) -> tuple[str, str]:
    """A saved flow and its dormant session, two subs banked through the
    route's own first night. Returns ``(flow id, session id)``."""
    fid = await rig.save_flow(FLOW)
    one = await rig.night_one(fid, [0, 1])
    assert one.status == "dormant" and len(one.frames) == 2, (
        f"premise: a dormant session with frames, got {one.status}")
    assert session_store.recoverable() is not None, (
        "premise: /recover has a session to recover")
    return fid, one.id


# Each route, as ``(name, request)``: ``request(rig, fid, sid, **flags)``.
async def _run_fresh(rig, fid, sid, **flags):
    return await rig.run(fid, fresh=True, **flags)


async def _run_continue(rig, fid, sid, **flags):
    return await rig.run(fid, **flags)


async def _resume(rig, fid, sid, **flags):
    return await rig.client.post(f"/api/sessions/{sid}/resume",
                                 json=flags or None)


async def _recover(rig, fid, sid, **flags):
    return await rig.client.post("/api/sequence/recover", json=flags or None)


async def _sequence_start(rig, fid, sid, **flags):
    return await rig.client.post("/api/sequence/start", json={
        "name": "t", "targets": [
            {"name": "M42", "ra_hours": 5.588, "dec_deg": -5.39,
             "steps": [{"filter": None, "exposure_s": 0.05, "count": 1}]}],
        **flags})


#: The three routes #323 found unguarded, CONTINUE counted as its own, and
#: ``/api/sequence/start``, which had the guard, as the fourth door.
ROUTES = [("run fresh", _run_fresh), ("run CONTINUE", _run_continue),
          ("resume", _resume), ("recover", _recover),
          ("sequence start", _sequence_start)]


@pytest.mark.parametrize("name,request_", ROUTES, ids=[n for n, _ in ROUTES])
@pytest.mark.parametrize("holder,expected", [("recording", VIDEO),
                                             ("dusk", DUSK)],
                         ids=["video", "dusk"])
async def test_a_held_rig_refuses_every_start(rig, request, name, request_,
                                              holder, expected):
    """The camera is held (a recording, or dusk connecting): each start path
    answers its 409, ``engine.start`` is never called, and the session the
    run would have continued or resumed is byte-for-byte as it was.

    RED under mutation "drop the guard from run_flow" (the helper's call
    removed from ``run_flow``), for run fresh and run CONTINUE, each holder,
    observed (video, fresh; CONTINUE the same with ``"night":2,
    "continued":true``):

        AssertionError: run fresh answered 200: {"started":true,"flow_id":
        "...","frames":7,"unmapped":[],"session":{"id":"...","night":1,
        "continued":false,"kept":0,"new":2,"dropped":0}}
        assert 200 == 409

    and (dusk) the engine's own backstop, which refuses only after the
    pre-flight, with no code:

        AssertionError: run fresh: {"detail":"dusk preparation is
        connecting equipment; wait before starting a sequence"}
        assert (None, 'dusk ...g a sequence') == ('dusk_connec...it
        finishes.')
          At index 0 diff: None != 'dusk_connecting'

    RED under mutation "drop the guard from resume_session", for resume,
    each holder, observed (video; dusk as above, "resume: ..."):

        AssertionError: resume answered 200: {"resumed":true,"remaining":5}
        assert 200 == 409

    RED under mutation "drop the guard from sequence_recover", for recover,
    each holder, observed (video; dusk as above, "recover: ..."):

        AssertionError: recover answered 200: {"resumed":true,
        "frames_remaining":5}
        assert 200 == 409
    """
    fid, sid = await _dormant(rig)
    starts_before = len(rig.starts)
    session_before = _bytes(sid)
    request.getfixturevalue(holder)()

    r = await request_(rig, fid, sid)

    assert r.status_code == 409, f"{name} answered {r.status_code}: {r.text}"
    assert _refusal(r) == expected, f"{name}: {r.text}"
    assert len(rig.starts) == starts_before, (
        f"{name} was refused and reached engine.start anyway")
    assert _bytes(sid) == session_before, f"{name}'s refusal wrote the session"


@pytest.mark.parametrize("name,request_", ROUTES, ids=[n for n, _ in ROUTES])
async def test_force_does_not_reach_it(rig, recording, name, request_):
    """``force`` overrides the horizon pre-flight and nothing that belongs to
    another lane's hardware: a forced start during a recording is refused
    the same way, on every route whose body takes ``force`` (all five). The
    structural test holds that the helper takes no argument; this holds that
    no route wraps the call in a test of ``force``, which the structural
    test cannot see.

    RED under "drop the guard from run_flow" for both run cases and under
    "drop the guard from resume_session" for resume, observed ``run fresh
    answered 200: {"started":true,...}``, ``resume answered 200:
    {"resumed":true,"remaining":5}``, each ``assert 200 == 409``.

    Recover was left out of this test until the S4-ROUTES verifier's second
    pass, and then mutation "force waives the guard on recover" (the call
    in ``sequence_recover`` wrapped in ``if not (body and body.force):``)
    left the whole file green. With recover here, observed:

        AssertionError: recover answered 200: {"resumed":true,
        "frames_remaining":5}
        assert 200 == 409
    """
    fid, sid = await _dormant(rig)
    starts_before = len(rig.starts)
    recording()

    r = await request_(rig, fid, sid, force=True)

    assert r.status_code == 409, f"{name} answered {r.status_code}: {r.text}"
    assert _refusal(r) == VIDEO
    assert len(rig.starts) == starts_before


@pytest.mark.parametrize("name,request_", ROUTES, ids=[n for n, _ in ROUTES])
async def test_control_a_free_rig_starts_as_it_did(rig, name, request_):
    """No recording, no dusk connecting: each route starts, through the
    engine's own ``start``, as it did before the guard. Without this the
    test above would pass against a helper that refused everything."""
    fid, sid = await _dormant(rig)
    assert not app_module.video_recorder.active
    assert not app_module.dusk_arm.connecting
    starts_before = len(rig.starts)

    r = await request_(rig, fid, sid)

    assert r.status_code == 200, f"{name} answered {r.status_code}: {r.text}"
    assert len(rig.starts) == starts_before + 1 and rig.starts[-1].won, (
        f"{name} did not start the engine")


# ---------------------------------------------------------------- structure

#: What pre-flights a plan, by the name each route calls it by. The helper
#: must be called before the first of these in every route.
PREFLIGHT = {"_refuse_plan_identity", "quota_unbounded", "_start_preflight",
             "compile_plan", "to_sequence_plan", "blocking_reasons",
             "validation_errors", "_refuse_while_resume_recovers"}
HELPER = "_refuse_start_while_rig_is_held"
START_ROUTES = ("sequence_start", "run_flow", "resume_session",
                "sequence_recover")


def _route_functions() -> dict[str, ast.AsyncFunctionDef]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(app_module)))
    return {n.name: n for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name in START_ROUTES}


def _calls(fn) -> list[tuple[int, int, str, ast.Call]]:
    out = []
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            f = n.func
            name = (f.id if isinstance(f, ast.Name)
                    else f.attr if isinstance(f, ast.Attribute) else "")
            out.append((n.lineno, n.col_offset, name, n))
    return sorted(out, key=lambda c: (c[0], c[1]))


def test_every_start_route_calls_the_helper_first():
    """Parsed from ``api/app.py``: each of the four start routes calls the
    helper exactly once, with no argument (so nothing, ``force`` included,
    can be handed to it), before any call that pre-flights the plan, and
    ``engine.start`` is reached only after it.

    RED under each of the three mutations, naming its route, observed
    ("drop the guard from run_flow"; resume_session and sequence_recover
    the same with their own names):

        AssertionError: run_flow never calls _refuse_start_while_rig_is_held
        assert 0 == 1
         +  where 0 = len([])
    """
    routes = _route_functions()
    assert set(routes) == set(START_ROUTES), (
        f"premise: every start route found, got {sorted(routes)}")
    for name, fn in routes.items():
        calls = _calls(fn)
        helper = [c for c in calls if c[2] == HELPER]
        assert len(helper) == 1, f"{name} never calls {HELPER}" \
            if not helper else f"{name} calls {HELPER} {len(helper)} times"
        line, col, _, node = helper[0]
        assert not node.args and not node.keywords, (
            f"{name} hands {HELPER} an argument; nothing may waive it")
        first_preflight = next((c for c in calls if c[2] in PREFLIGHT), None)
        assert first_preflight is not None, f"premise: {name} pre-flights"
        assert (line, col) < first_preflight[:2], (
            f"{name} calls {first_preflight[2]} (line {first_preflight[0]}) "
            f"before {HELPER} (line {line})")
        start = [c for c in calls if c[2] == "start"
                 and isinstance(c[3].func, ast.Attribute)
                 and isinstance(c[3].func.value, ast.Name)
                 and c[3].func.value.id == "engine"]
        assert start and all((line, col) < c[:2] for c in start), (
            f"{name} reaches engine.start before {HELPER}")


def test_the_helper_holds_the_camera_ownership_guard():
    """The helper is where the guard lives: it calls
    ``_refuse_if_camera_owned``, so a route that calls the helper refuses
    both holders. Paired with the behaviour tests, which are what grade the
    effect."""
    src = inspect.getsource(app_module._refuse_start_while_rig_is_held)
    calls = [c[2] for c in _calls(ast.parse(textwrap.dedent(src)))]
    assert "_refuse_if_camera_owned" in calls, calls
