"""A session records where it came from, and a flow records that it ran.

Two findings from using the product over the relay on 2026-08-16:

  * every card in the Flows library said NEVER RUN, including two whose own
    descriptions read "First flow run on the real rig". `last_run` and
    `last_result` have been on FlowRecord since the library shipped and the
    cards render all four states - but nothing ever wrote them, and the API
    re-derives both from the stored record on every PUT so a client cannot
    supply them either. A closed loop with no producer.

  * asked "what is the status of the flow in progress", the UI could not
    answer, because a Session recorded nothing about which screen built it. A
    flow run and a Plan run compile to identical plans with identical names, so
    it cannot be inferred afterwards - it has to be stamped at the start.
"""
import asyncio
import time

import pytest

import astrodeck.hub as hub_module
from astrodeck.flows.store import FlowStore
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target
from astrodeck.sequence.session import session_store


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    _safety = hub_module.config_store.cfg().safety
    monkeypatch.setattr(_safety, "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


async def wait_for(predicate, timeout=30.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


def _plan(name="run"):
    return SequencePlan(
        name=name, guide=False, dither_every=0, autofocus_every=0,
        meridian_flip=False,
        targets=[Target(name="A", ra_hours=5.5881, dec_deg=-5.3911,
                        center=False, autofocus_first=False,
                        steps=[ExposureStep(filter="L", exposure_s=0.05,
                                            count=1)])])


# ------------------------------------------------------------- provenance

async def test_a_plan_run_says_it_came_from_the_plan_editor(sim_hub):
    eng = SequenceEngine(sim_hub)
    eng.start(_plan(), origin="plan")
    sid = eng._session.id
    assert await wait_for(lambda: eng.state.get("state") == "complete")
    s = session_store.load(sid)
    assert s.origin == "plan"
    assert s.origin_id == ""


async def test_a_flow_run_names_the_flow(sim_hub):
    eng = SequenceEngine(sim_hub)
    eng.start(_plan(), origin="flow", origin_id="flow-abc")
    sid = eng._session.id
    assert await wait_for(lambda: eng.state.get("state") == "complete")
    s = session_store.load(sid)
    assert s.origin == "flow"
    assert s.origin_id == "flow-abc"


async def test_an_unstamped_start_admits_it_does_not_know(sim_hub):
    """Default "" rather than "plan": a future start path that forgets the
    argument must record "unknown", not a confident lie about a screen the
    operator never opened."""
    eng = SequenceEngine(sim_hub)
    eng.start(_plan())
    sid = eng._session.id
    assert await wait_for(lambda: eng.state.get("state") == "complete")
    assert session_store.load(sid).origin == ""


async def test_a_resume_inherits_its_origin_and_never_restamps(sim_hub):
    """Night two of a flow campaign must not claim to have come from the Plan
    editor just because the resume path did not pass the argument."""
    eng = SequenceEngine(sim_hub)
    plan = SequencePlan(
        name="two-night", guide=False, dither_every=0, autofocus_every=0,
        meridian_flip=False,
        targets=[Target(name="A", ra_hours=5.5881, dec_deg=-5.3911,
                        center=False, autofocus_first=False,
                        steps=[ExposureStep(filter="L", exposure_s=0.05,
                                            count=4)])])
    eng.start(plan, origin="flow", origin_id="flow-xyz")
    sid = eng._session.id
    assert await wait_for(lambda: eng._frames_done >= 1)
    await eng.abort()

    s = session_store.load(sid)
    eng2 = SequenceEngine(sim_hub)
    eng2.start(s.plan, session=s)              # resume passes no origin
    assert await wait_for(lambda: eng2.state.get("state") == "complete")

    done = session_store.load(sid)
    assert done.origin == "flow"
    assert done.origin_id == "flow-xyz"


async def test_the_session_row_carries_it(sim_hub):
    """GET /api/sessions is what the UI reads; provenance has to be on the row
    or the screens still cannot answer the question."""
    eng = SequenceEngine(sim_hub)
    eng.start(_plan(), origin="flow", origin_id="flow-row")
    sid = eng._session.id
    assert await wait_for(lambda: eng.state.get("state") == "complete")
    row = next(r for r in session_store.list() if r["id"] == sid)
    assert row["origin"] == "flow"
    assert row["origin_id"] == "flow-row"
    assert row["owed"] == 0


# --------------------------------------------------------- flow run marks

def test_touch_run_records_when_it_ran(tmp_path):
    store = FlowStore(tmp_path)
    from astrodeck.flows.models import FlowGraph, FlowRecord
    rec = store.save(FlowRecord(name="mine", graph=FlowGraph()))
    assert rec.last_run is None

    assert store.touch_run(rec.id, ts=1234.0) is True
    assert store.get(rec.id).last_run == 1234.0


def test_touch_run_does_not_make_a_run_look_like_an_edit(tmp_path):
    """`save()` re-stamps updated_ts. If the run write-back went through it,
    running a flow would pollute every "recently changed" reading of the
    library."""
    store = FlowStore(tmp_path)
    from astrodeck.flows.models import FlowGraph, FlowRecord
    rec = store.save(FlowRecord(name="mine", graph=FlowGraph()))
    before = store.get(rec.id).updated_ts
    time.sleep(0.02)
    store.touch_run(rec.id, ts=time.time(), result="ok")
    assert store.get(rec.id).updated_ts == before


def test_touch_run_refuses_the_shipped_examples_without_raising(tmp_path):
    """The M16 example is required to run on the simulator. `save()` raises
    ReadOnlyFlow for examples, so an unguarded write-back would turn every
    example run into a 500 AFTER the engine had already started."""
    from astrodeck.flows.examples import examples
    store = FlowStore(tmp_path)
    ex = next(e for e in examples())
    assert store.touch_run(ex.id, ts=time.time()) is False


def test_touch_run_on_an_unknown_flow_is_a_no_op(tmp_path):
    assert FlowStore(tmp_path).touch_run("nope", ts=1.0) is False


async def test_the_result_lands_on_the_flow_when_the_night_ends(sim_hub, tmp_path,
                                                                monkeypatch):
    """The other half: `last_run` is stamped by the route at start, but how it
    WENT is only known at finalize - and is only reachable there because the
    session recorded which flow it came from."""
    import astrodeck.flows.store as store_module
    store = FlowStore(tmp_path / "flows")
    monkeypatch.setattr(store_module, "flow_store", store)
    from astrodeck.flows.models import FlowGraph, FlowRecord
    rec = store.save(FlowRecord(name="mine", graph=FlowGraph()))

    eng = SequenceEngine(sim_hub)
    eng.start(_plan(), origin="flow", origin_id=rec.id)
    assert await wait_for(lambda: eng.state.get("state") == "complete")

    assert store.get(rec.id).last_result == "ok"


async def test_a_plan_run_never_touches_a_flow(sim_hub, tmp_path, monkeypatch):
    import astrodeck.flows.store as store_module
    store = FlowStore(tmp_path / "flows")
    monkeypatch.setattr(store_module, "flow_store", store)
    from astrodeck.flows.models import FlowGraph, FlowRecord
    rec = store.save(FlowRecord(name="mine", graph=FlowGraph()))

    eng = SequenceEngine(sim_hub)
    eng.start(_plan(), origin="plan")
    assert await wait_for(lambda: eng.state.get("state") == "complete")

    assert store.get(rec.id).last_result == ""
    assert store.get(rec.id).last_run is None
