"""ADOPT asks the catalogue off the event loop and outside the store's write
lock (#249, route half; spec 5.9, the CONTINUE critical section).

ADOPT matches a pre-S1 session's steps to tonight's compile, and to do it it
asks the catalogue what every target name is and, for a moving body, where
the body was at the instants its frames were taken (``adopt_evidence``). Each
answer is a full catalogue search, 10 to 35 ms, and some 700 ms for the first
body of the process. It used to be asked inside ``_continue_flow_session``,
the synchronous section that holds ``SessionStore._write_lock`` on the event
loop, so the safety poller, the relay, every route and every worker-thread
session write waited on it.

Now ``run_flow`` asks for the evidence with ``asyncio.to_thread`` before the
lock, from its first read of the session, and the locked section matches on
that answer alone. Frames a run banked between the first read and the lock
are on the re-read only; a step whose answer the evidence does not hold is
listed with a sentence asking the operator to press ADOPT again, and nothing
is written.

THE HARNESS is ``test_flows_continue.py``'s (the real app over ASGI on the
test's loop, a real engine whose imaging loop alone is replaced), with
``tonight.resolve_target`` replaced by ``_Catalogue``, which RAISES when it
is called while the write lock is held by any thread, or on the event loop.
``adopt_evidence`` looks the resolver up on the module at call time, so the
stand-in is what ADOPT asks. The flow's TARGET has typed coordinates, so the
compile never asks it.

Each mutant was run from a byte-for-byte backup of ``api/app.py`` and the
file was restored byte-identical (sha256 compared) afterwards. Failures are
quoted verbatim (``--tb=short``), wrapped to fit, with the Windows path
separator written as /.
"""
from __future__ import annotations

import asyncio
import threading

import pytest

import astrodeck.flows.tonight as tonight_module
from astrodeck.flows.tonight import NameResolution
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target
from astrodeck.sequence.session import (Session, SessionFrame, SessionStore,
                                        session_store)
from test_flows_continue import (_bak, _bytes, _capture, _graph, _stored,
                                 rig)  # noqa: F401 (fixture)

#: One TARGET named for a body, with typed coordinates: L (3) then R (2).
JUPITER = _graph(_capture("c1", "L"), _capture("c2", "R", count=2),
                 name="Jupiter")

#: Where the old plan pointed and where the stand-in places the body: the
#: flow's typed coordinates, so the old target is on the body at every
#: instant and a step maps whenever the evidence holds its instants.
RA, DEC = 5.588, -5.391

#: When the pre-S1 frames were taken (night n1), and the frame a run banks
#: between the route's first read and its lock (night n2).
T1, T2, T3 = 1_789_012_800.0, 1_789_013_400.0, 1_789_099_200.0

#: The sentence a step outran by the lookup is listed with (api/app.py).
ADOPT_AGAIN = ("frames were banked on this step after ADOPT looked it up in "
               "the catalogue, so it was not matched; press ADOPT again to "
               "include them")


def _write_lock_held() -> bool:
    """Whether ANY thread holds the store's write lock. Probed from a fresh
    thread, because the lock is re-entrant: asked from the thread that holds
    it, ``acquire(blocking=False)`` succeeds and says nothing."""
    got: list[bool] = []

    def probe() -> None:
        ok = SessionStore._write_lock.acquire(blocking=False)
        if ok:
            SessionStore._write_lock.release()
        got.append(ok)

    t = threading.Thread(target=probe)
    t.start()
    t.join()
    return not got[0]


def _on_the_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


class _Catalogue:
    """Stands in for ``tonight.resolve_target``. Every name is a body placed
    at (``RA``, ``DEC``) at every instant. Raises when asked under the write
    lock or on the event loop; ``hook`` runs once, on the first call, from
    the thread that made it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, float | None]] = []
        self.hook = None

    def __call__(self, name, when=None):
        where = [w for w, bad in (
            ("while the store's write lock was held", _write_lock_held()),
            ("on the event loop", _on_the_loop())) if bad]
        if where:
            raise AssertionError(f"ADOPT asked the catalogue about {name!r} "
                                 + " and ".join(where))
        self.calls.append((name, when))
        hook, self.hook = self.hook, None
        if hook is not None:
            hook()
        return NameResolution(ra_hours=RA, dec_deg=DEC, identity=str(name),
                              moves=True)


@pytest.fixture
def catalogue(monkeypatch):
    c = _Catalogue()
    monkeypatch.setattr(tonight_module, "resolve_target", c)
    return c


def _pre_s1(fid: str, times: tuple[float, ...]) -> tuple[Session, str]:
    """A dormant session saved before S1 (uuid4 ids) on the flow's recipe
    for L, one frame per entry of ``times`` on night n1. Returns it and its
    L step id.

    It counts every sub taken, as a pre-S1 flow session did, and the flow
    the tests save counts accepted subs (every save writes them since S3,
    Revision 2 ruling 2). Every frame here is accepted, so both totals are
    the same and the recount question is NOT ASKED (S4 orchestrator ruling
    2, #348): no request in this file passes ``accept_recount``.
    RE-PINNED IN S4-ROUTES. The integration of S3 added the flag to every
    request that should start, and to the refusals "even with every flag
    set", because the recount 409 then asked whenever the modes differed and
    answered after ADOPT and before the start this file grades; with equal
    totals it was a question about nothing, and the flag went with it.
    Under mutation "ask whenever the modes differ" the starts here are the
    recount 409 again: four tests red, each at its first start, observed
    (the lock test's):

        AssertionError: {"detail":{"code":"recount","detail":"this session
        counted every sub taken (2); counting accepted subs makes it
        2","before":2,"after":2,"session_id":"..."}}
        assert 409 == 200"""
    step = ExposureStep(filter="L", exposure_s=0.05, count=3)
    t = Target(name="Jupiter", ra_hours=RA, dec_deg=DEC, steps=[step])
    s = Session(name="pre-S1", created_ts=1.0, status="dormant",
                origin="flow", origin_id=fid,
                plan=SequencePlan(name="pre-S1", targets=[t]))
    for ts in times:
        s.frames.append(SessionFrame(ts=ts, night="n1", target_id=t.id,
                                     step_id=step.id))
    return _stored(s), step.id


def _bank(sid: str, ts: float) -> None:
    """A frame on the L step of session ``sid``, as a run in the gap would
    have banked it: read, append, save."""
    s = session_store.load(sid)
    t = s.plan.targets[0]
    s.frames.append(SessionFrame(ts=ts, night="n2", target_id=t.id,
                                 step_id=t.steps[0].id))
    session_store.save(s)


# ------------------------------------------------------------------ the lock

async def test_adopt_asks_the_catalogue_off_the_loop_and_outside_the_lock(
        rig, catalogue):
    """The question, then ADOPT, end to end: both requests ask the catalogue
    (names and the body's instants), and none of it under the write lock or
    on the event loop.

    RED under mutant "resolve inside the lock" (today's code before H3:
    ``run_flow`` asks for no evidence, and the locked section calls
    ``adopt_matches(s, plan)``, which asks the catalogue itself), observed:

        astrodeck/api/app.py:1132: in _continue_flow_session
        astrodeck/flows/continuation.py:444: in adopt_matches
        astrodeck/flows/continuation.py:250: in adopt_evidence
        E   AssertionError: ADOPT asked the catalogue about 'Jupiter' while
        the store's write lock was held and on the event loop

    RED under mutant "resolve on the loop" (``adopt_evidence(latest, plan)``
    called directly before the lock, no ``asyncio.to_thread``), observed:

        astrodeck/api/app.py:5838: in run_flow
        astrodeck/flows/continuation.py:250: in adopt_evidence
        E   AssertionError: ADOPT asked the catalogue about 'Jupiter' on the
        event loop
    """
    fid = await rig.save_flow(JUPITER)
    old, l_step = _pre_s1(fid, (T1, T2))

    r = await rig.run(fid)

    assert r.status_code == 409, r.text
    body = r.json()["detail"]
    assert (body["code"], body["adopt"]["matched"],
            body["adopt"]["unmatched"]) == ("adopt", 2, []), body
    asked = set(catalogue.calls)
    assert {("Jupiter", None), ("Jupiter", T1), ("Jupiter", T2)} <= asked, (
        f"premise: the question asked the catalogue nothing: {asked}")
    assert rig.starts == []

    catalogue.calls.clear()
    r = await rig.run(fid, adopt=True)

    assert r.status_code == 200, r.text
    assert r.json()["session"]["adopted"] == {"matched": 2, "unmatched": []}
    assert ("Jupiter", T1) in catalogue.calls, (
        "premise: the adopt asked the catalogue nothing")
    assert _bak(old.id).exists()
    start = rig.starts[-1]
    assert start.won and start.session_id == old.id
    assert l_step not in {step for _t, step in start.frames}


async def test_a_session_that_became_an_adopt_case_in_the_gap_asks_nothing(
        rig, catalogue, monkeypatch):
    """The route's first read holds no frames, so it asks no ADOPT question
    and no catalogue; a run banks two frames before the lock, so the re-read
    does. The locked section has no evidence and asks for none: every step
    that holds frames is listed to press ADOPT again, nothing is written,
    and the next press adopts them.

    RED under mutant "no evidence, asked inside the lock" (``held = evidence
    if evidence is not None else adopt_evidence(s, plan)``), observed:

        astrodeck/api/app.py:1131: in _continue_flow_session
        astrodeck/flows/continuation.py:250: in adopt_evidence
        E   AssertionError: ADOPT asked the catalogue about 'Jupiter' while
        the store's write lock was held and on the event loop

    RED under mutant "resolve inside the lock" (above), observed:

        the same four lines as in the test above.
    """
    fid = await rig.save_flow(JUPITER)
    old, l_step = _pre_s1(fid, ())
    real = SessionStore.current_for_flow
    once = {"armed": True}

    def first_read_then_a_run_banks(self, flow_id):
        got = real(self, flow_id)
        if once.pop("armed", False):
            _bank(old.id, T1)
            _bank(old.id, T2)
        return got

    monkeypatch.setattr(SessionStore, "current_for_flow",
                        first_read_then_a_run_banks)
    r = await rig.run(fid, adopt=True, accept_dropped=True)

    assert r.status_code == 409, r.text
    body = r.json()["detail"]
    assert body["code"] == "adopt", body
    assert [(u["step_id"], u["frames"], u["reason"])
            for u in body["adopt"]["unmatched"]] == [(l_step, 2, ADOPT_AGAIN)]
    assert body["adopt"]["matched"] == 0
    assert body["detail"].endswith(
        ". 1 step took frames after ADOPT looked this session up in the "
        "catalogue; press ADOPT again to include them"), body["detail"]
    assert catalogue.calls == [], "the first read asked the catalogue"
    assert len(session_store.load(old.id).frames) == 2, (
        "premise: the run in the gap banked nothing")
    assert rig.starts == [] and not _bak(old.id).exists()

    r = await rig.run(fid, adopt=True)

    assert r.status_code == 200, r.text
    assert r.json()["session"]["adopted"] == {"matched": 2, "unmatched": []}


# --------------------------------------------------- frames banked in the gap

async def test_frames_banked_after_the_lookup_are_listed_to_press_adopt_again(
        rig, catalogue):
    """The evidence is asked of the first read, which holds night n1; while
    it is being asked, a run banks a frame on night n2. The re-read's step
    has an instant the evidence never asked about, so it is listed with
    ``ADOPT_AGAIN`` and refused even with every flag set, since adopted now
    its frames would stay on a step the new plan does not have, with no
    ADOPT left to press. The next press adopts all three.

    RED under mutant "gaps not relisted" (``unasked = set()`` in place of
    ``_unasked(s, plan, held)``): the step is left as a body "the catalogue
    could not place", and ``accept_dropped`` then starts the run with its
    frames orphaned, observed:

        E   AssertionError: {"started":true,[...],"session":{"id":"6c2788d3513e47688a292196fd3d2f31",
        "night":1,"continued":true,"kept":0,"new":2,"dropped":1,"adopted":
        {"matched":0,"unmatched":[{"step_id":"794f620e17984ef69850c9619f35b873",
        [...],"frames":3,"reason":"the catalogue could not place Jupiter at a
        time these frames were taken, so nothing shows they are of it",
        "separation_arcmin":null}]}}}
        E   assert 200 == 409
    """
    fid = await rig.save_flow(JUPITER)
    old, l_step = _pre_s1(fid, (T1, T2))
    banked: list[bytes] = []

    def a_run_banks_in_the_gap() -> None:
        _bank(old.id, T3)
        banked.append(_bytes(old.id))

    catalogue.hook = a_run_banks_in_the_gap

    r = await rig.run(fid, adopt=True, accept_dropped=True)

    assert r.status_code == 409, r.text
    body = r.json()["detail"]
    assert body["code"] == "adopt", body
    assert [(u["step_id"], u["frames"], u["reason"])
            for u in body["adopt"]["unmatched"]] == [(l_step, 3, ADOPT_AGAIN)]
    assert ("Jupiter", T3) not in catalogue.calls, (
        "premise: the first lookup already knew the banked frame")
    assert len(banked) == 1, "premise: nothing was banked in the gap"
    assert _bytes(old.id) == banked[0], "the refusal wrote the session"
    assert rig.starts == [] and not _bak(old.id).exists()

    r = await rig.run(fid, adopt=True)

    assert r.status_code == 200, r.text
    assert r.json()["session"]["adopted"] == {"matched": 3, "unmatched": []}
    assert ("Jupiter", T3) in catalogue.calls


async def test_control_no_frames_in_the_gap_adopts_on_the_first_press(
        rig, catalogue):
    """CONTROL for the test above: the same session and flags with nothing
    banked in the gap adopts at once. The relisting fires on a step the
    evidence missed, never on every body step.

    RED under mutant "every body step relisted" (``_unasked``'s instant test
    ``(t.name, when) not in evidence.positions`` made ``True``), observed:

        E   AssertionError: {"detail":{"code":"adopt","detail":"this flow's
        session holds 2 subs under step ids no compile produces any more [...].
        1 step took frames after ADOPT looked this session up in the
        catalogue; press ADOPT again to include them","adopt":{[...]}}}
        E   assert 409 == 200

    and the lock test above goes red under it too (``matched`` 0, not 2).
    """
    fid = await rig.save_flow(JUPITER)
    old, _l_step = _pre_s1(fid, (T1, T2))

    r = await rig.run(fid, adopt=True, accept_dropped=True)

    assert r.status_code == 200, r.text
    assert r.json()["session"]["adopted"] == {"matched": 2, "unmatched": []}


# ------------------------------------------------------------------ controls

async def test_control_the_stand_in_sees_the_lock_and_the_loop(catalogue):
    """CONTROL. The stand-in's two probes fire: asked under the write lock
    (from this test, on the loop) it names the lock; asked on the loop
    outside it, it names the loop; asked on a worker thread outside it, it
    answers. Without this the lock test above could be green because the
    probe cannot see a held lock.
    """
    with session_store.write_locked():
        with pytest.raises(AssertionError, match="write lock was held"):
            catalogue("M42")
    with pytest.raises(AssertionError, match="on the event loop") as info:
        catalogue("M42")
    assert "write lock" not in str(info.value)
    hit = await asyncio.to_thread(catalogue, "M42")
    assert hit.identity == "M42" and catalogue.calls == [("M42", None)]


def test_control_the_probe_sees_a_lock_held_by_another_thread():
    """CONTROL. The lock probe reads a lock another thread holds as held,
    and a free lock as free: a worker thread's own ``acquire`` would
    succeed on a lock that thread already holds (re-entrant), which is why
    the probe asks from a thread of its own."""
    held, release = threading.Event(), threading.Event()

    def holder() -> None:
        with SessionStore._write_lock:
            held.set()
            release.wait(10)

    t = threading.Thread(target=holder)
    t.start()
    try:
        assert held.wait(10)
        assert _write_lock_held() is True
    finally:
        release.set()
        t.join(10)
    assert _write_lock_held() is False


# ------------------------------------------------------------- the helpers

def test_unasked_and_relisted_keep_their_stated_rules():
    """``_unasked`` and ``_relisted`` called directly, on the rules their
    docstrings state and the route cannot show on its own. Through
    ``run_flow`` the evidence is asked of the same compiled plan the lock
    matches against, so a name is missing from it only when the evidence is
    empty, and then the new-plan check and the old-name check each cover the
    other; and every relisted step makes the route refuse, so the mapping
    ``_relisted`` hands back is never applied. Each mutant below was green
    on this file, test_flows_continue.py and test_flows_adopt_separation.py
    (64 passed) before this test.

    RED under mutant "new-plan name check dropped" (``_unasked``'s ``if
    any(t.name not in evidence.names for t in plan.targets): return held``
    removed), observed:

        E   AssertionError: a name of the new plan the evidence never asked
        about left the steps that hold frames matched
        E   assert set() == {'7d1e1b0cf6f...251f577c877d'}
        E     Extra items in the right set:
        E     'ae9b63f24ca54409b21c251f577c877d'
        E     '7d1e1b0cf6fe408793ab97c86c47cbe9'

    RED under mutant "old-name check dropped" (its ``if t.name not in
    evidence.names`` clause removed, ``hit = evidence.names.get(t.name)``),
    observed:

        E   AssertionError: an old target the evidence never asked about
        was left matched
        E   assert set() == {'b6d197edffa...2f980c47decf'}
        E     Extra items in the right set:
        E     'b6d197edffa343eaabb62f980c47decf'

    RED under mutant "relisted leaves the mapping" (``_relisted`` passes
    ``matches.mapping`` through), observed:

        E   AssertionError: a relisted step is still in the mapping
        E   assert {'091317deb42... ('t2', 's2')} == {'091317deb42... ('t1', 's1')}
        E     Omitting 1 identical items, use -vv to show
        E     Left contains 1 more item:
        E     {'3b31efae775847d2a95354554c0c9615': ('t2', 's2')}
    """
    from astrodeck.api import app as app_module
    from astrodeck.flows.continuation import AdoptEvidence, AdoptMatches

    def target(name: str) -> Target:
        return Target(name=name, ra_hours=RA, dec_deg=DEC, steps=[
            ExposureStep(filter="L", exposure_s=0.05, count=3)])

    jupiter, m42 = target("Jupiter"), target("M42")
    s = Session(name="pre-S1", created_ts=1.0, status="dormant",
                plan=SequencePlan(name="pre-S1", targets=[jupiter, m42]))
    for t, ts in ((jupiter, T1), (jupiter, T2), (m42, T1)):
        s.frames.append(SessionFrame(ts=ts, night="n1", target_id=t.id,
                                     step_id=t.steps[0].id))
    j_step, m_step = jupiter.steps[0].id, m42.steps[0].id
    body = NameResolution(ra_hours=RA, dec_deg=DEC, identity="Jupiter",
                          moves=True)
    fixed = NameResolution(ra_hours=RA, dec_deg=DEC, identity="M42",
                           moves=False)
    full = AdoptEvidence(names={"Jupiter": body, "M42": fixed},
                         positions={("Jupiter", T1): body,
                                    ("Jupiter", T2): body})
    tonight = SequencePlan(name="tonight", targets=[target("Jupiter"),
                                                    target("M42")])

    # Control: evidence that holds every answer leaves nothing unasked.
    assert app_module._unasked(s, tonight, full) == set()
    # A new-plan name it never asked: every step that holds frames.
    wider = SequencePlan(name="tonight", targets=[
        *tonight.targets, target("Saturn")])
    assert app_module._unasked(s, wider, full) == {j_step, m_step}, (
        "a name of the new plan the evidence never asked about left the "
        "steps that hold frames matched")
    # An old name it never asked, the new plan's all asked: that target's.
    no_m42 = AdoptEvidence(names={"Jupiter": body},
                           positions=full.positions)
    only_j = SequencePlan(name="tonight", targets=[target("Jupiter")])
    assert app_module._unasked(s, only_j, no_m42) == {m_step}, (
        "an old target the evidence never asked about was left matched")

    matches = AdoptMatches(mapping={j_step: ("t1", "s1"),
                                    m_step: ("t2", "s2")},
                           frames_matched=3)
    again = app_module._relisted(s, matches, {m_step})
    assert again.mapping == {j_step: ("t1", "s1")}, (
        "a relisted step is still in the mapping")
    assert again.frames_matched == 2
    assert [(u["step_id"], u["frames"], u["reason"])
            for u in again.unmatched] == [(m_step, 1, ADOPT_AGAIN)]
