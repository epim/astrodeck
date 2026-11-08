# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A night run from a stored session publishes its stages (#1231, item 1).

WP 4.2 made the engine the one writer of what each flow stage is doing, and
it names the stage on ``ExposureStep.stage`` and the block on
``Target.block``: both EXCLUDED FROM EVERY DUMP, so no stored plan and no plan
hash moved. A plan read back from a session file therefore named nothing, and
the night the operator usually runs (an auto-resumed one, night two onward of
a multi-night session) published no ``stages`` at all.

The ruling (binding): the names do NOT go into the plan dump. The session
record carries them beside the plan, ``flow_blocks`` ``{target_id: block}``
and ``flow_stages`` ``{"target_id:step_id": stage}``. ``SessionStore.save``
writes them from the plan being saved; building a ``Session`` (every reader,
so every route and ResumeArm that runs a stored session) applies them to the
plan's in-memory targets and steps. A file without them runs as it always did.

The clocks: the resumed runs use the simulator's real clock with 50 ms
exposures and ResumeArm's window opened by hand; no test here reads the Sun, a
target's window or the time zone.

MUTANT (named in the brief), run from a byte backup of ``sequence/session.py``:

  M1  the map not applied when a stored session is built (the validator that
      names the plan's stages from the record returns without naming): the
      resumed night publishes nothing.
"""
from __future__ import annotations

import asyncio
import json
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
from astrodeck.sequence.session import (Session, SessionFrame,
                                        SessionStore, session_store)
from conftest import _sweep_config_store  # rootdir-relative, as _flow_night does

from _flow_night import FlowRig, flow_rig  # noqa: F401
from _group_harness import group_store  # noqa: F401
from test_w4_2_stage_status import run_lane_flow

BLOCK, STAGE_L, STAGE_R = "n2", "n3", "n4"


def staged_plan(name: str = "Staged", count: int = 3) -> SequencePlan:
    """A plan as the flow compile leaves it: one target under a block, two
    steps each under its own stage. The exposure is 50 ms so a run is quick on
    the simulator's real clock."""
    return SequencePlan(
        name=name, guide=False, dither_every=0, autofocus_every=0,
        meridian_flip=False,
        targets=[Target(name=name, ra_hours=5.5881, dec_deg=-5.3911,
                        center=False, autofocus_first=False, block=BLOCK,
                        steps=[ExposureStep(filter="L", exposure_s=0.05,
                                            count=count, stage=STAGE_L),
                               ExposureStep(filter="R", exposure_s=0.05,
                                            count=count, stage=STAGE_R)])])


def plain_plan(name: str = "Plain") -> SequencePlan:
    """The same plan with no stage or block named: a Plan-editor plan, or one
    read back from a file written before the names were kept."""
    plan = staged_plan(name)
    plan.targets[0].block = None
    for step in plan.targets[0].steps:
        step.stage = None
    return plan


def names_of(plan: SequencePlan) -> tuple[dict, dict]:
    return ({t.id: t.block for t in plan.targets},
            {f"{t.id}:{s.id}": s.stage for t in plan.targets for s in t.steps})


def stored_file(sid: str) -> dict:
    return json.loads(session_store._path(sid).read_text(encoding="utf-8"))


def banked(session: Session, plan: SequencePlan, n: int) -> None:
    """``n`` frames of the first step banked, so the session owes the rest."""
    step = plan.targets[0].steps[0]
    for _ in range(n):
        session.frames.append(SessionFrame(
            ts=1.0, night="n1", target_id=plan.targets[0].id, step_id=step.id,
            path="f.fits", metrics={}, auto_accepted=True))


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A session store over a throwaway captures directory."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    return session_store


# ------------------------------------------------------------- the record

def test_the_session_file_carries_the_names_and_the_plan_dump_does_not(store):
    """The names are written beside the plan, from the plan being saved, and
    the plan inside the file is byte for byte what it was before any name
    existed (so no plan hash and no frozen-plan identity moves)."""
    plan = staged_plan()
    s = Session(name="n", created_ts=1.0, status="dormant", plan=plan)
    store.save(s)
    raw = stored_file(s.id)
    blocks, stages = names_of(plan)
    assert raw["flow_blocks"] == blocks == {plan.targets[0].id: BLOCK}
    assert raw["flow_stages"] == stages
    assert set(stages.values()) == {STAGE_L, STAGE_R}
    plain = plain_plan("Staged")
    # Same ids on both so the dumps are comparable.
    plain.targets[0].id = plan.targets[0].id
    for a, b in zip(plain.targets[0].steps, plan.targets[0].steps):
        a.id = b.id
    assert raw["plan"] == json.loads(json.dumps(
        plain.model_dump(mode="json"))), (
        "the stored plan must dump exactly as a plan that names nothing")
    for t in raw["plan"]["targets"]:
        assert "block" not in t and all("stage" not in x for x in t["steps"])


def test_a_stored_session_is_built_with_its_plan_naming_the_stages(store):
    """Reading a session back names the plan's targets and steps again: the
    plan the run is built from is the one the first night ran."""
    plan = staged_plan()
    s = Session(name="n", created_ts=1.0, status="dormant", plan=plan)
    store.save(s)
    loaded = store.load(s.id)
    assert names_of(loaded.plan) == names_of(plan)
    assert loaded.plan.targets[0].block == BLOCK
    assert {x.stage for x in loaded.plan.targets[0].steps} == {STAGE_L, STAGE_R}


def test_a_session_file_without_the_names_runs_as_it_always_did(store):
    """A file saved before the names were kept has neither key: it loads, its
    plan names nothing, and saving it writes empty maps."""
    s = Session(name="n", created_ts=1.0, status="dormant", plan=plain_plan())
    store.save(s)
    path = store._path(s.id)
    raw = stored_file(s.id)
    for key in ("flow_blocks", "flow_stages"):
        del raw[key]
    path.write_text(json.dumps(raw), encoding="utf-8")
    loaded = store.load(s.id)
    assert names_of(loaded.plan) == ({loaded.plan.targets[0].id: None},
                                     {f"{loaded.plan.targets[0].id}:{x.id}": None
                                      for x in loaded.plan.targets[0].steps})
    store.save(loaded)
    raw = stored_file(s.id)
    assert raw["flow_blocks"] == {} and raw["flow_stages"] == {}


def test_a_file_with_keys_this_build_does_not_know_still_reads(store):
    """Forward and backward: the record has no ``extra="forbid"``, so a build
    that predates the two maps loads a newer file and ignores them, and this
    build loads a file carrying keys it does not know."""
    assert Session.model_config.get("extra") != "forbid"
    s = Session(name="n", created_ts=1.0, status="dormant", plan=staged_plan())
    store.save(s)
    raw = stored_file(s.id)
    raw["a_key_from_a_later_build"] = {"x": 1}
    store._path(s.id).write_text(json.dumps(raw), encoding="utf-8")
    assert names_of(store.load(s.id).plan) == names_of(s.plan)


def test_hand_edited_maps_cannot_make_the_session_unreadable(store):
    """A value that is not a name is ignored, as a hand edit of
    ``group_pier`` is: a file that fails validation vanishes from every scan,
    ledger and all."""
    s = Session(name="n", created_ts=1.0, status="dormant", plan=staged_plan())
    store.save(s)
    raw = stored_file(s.id)
    raw["flow_blocks"] = {s.plan.targets[0].id: 7}
    raw["flow_stages"] = {"nobody:nothing": "n9", "x": ["y"]}
    store._path(s.id).write_text(json.dumps(raw), encoding="utf-8")
    loaded = store.load(s.id)
    assert loaded.plan.targets[0].block is None
    assert all(x.stage is None for x in loaded.plan.targets[0].steps)


def test_a_plan_replaced_by_one_naming_nothing_forgets_the_names(store):
    """The Plan editor's PATCH replaces the stored plan with one no flow
    compiled: the old names must not be applied to the new plan's ids, so the
    record is rewritten from the plan it now holds."""
    s = Session(name="n", created_ts=1.0, status="dormant", plan=staged_plan())
    store.save(s)
    held = store.load(s.id)
    held.plan = plain_plan()
    store.save(held)
    raw = stored_file(s.id)
    assert raw["flow_blocks"] == {} and raw["flow_stages"] == {}
    again = store.load(s.id)
    assert again.plan.targets[0].block is None


# ---------------------------------------------------------- the resumed run

async def wait_for(predicate, timeout=30.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


class Rig:
    def __init__(self, hub: Hub, engine: SequenceEngine,
                 client: httpx.AsyncClient) -> None:
        self.hub = hub
        self.engine = engine
        self.client = client

    async def first_night(self, plan: SequencePlan) -> tuple[str, dict]:
        """Night one: start, bank one frame, stop by hand. Returns the
        session id and the stages the first night published."""
        self.engine.start(plan)
        sid = self.engine._session.id
        assert await wait_for(lambda: self.engine._frames_done >= 1)
        stages = dict(self.engine.state.get("stages") or {})
        await self.engine.abort()
        stopped = session_store.load(sid)
        assert stopped.status == "dormant"
        return sid, stages

    def next_engine(self) -> SequenceEngine:
        """Night two's engine: a fresh process's, so nothing it publishes is
        left over from night one, and the app's own."""
        engine = SequenceEngine(self.hub)
        app_module.engine = engine
        self.engine = engine
        return engine

    def resume_arm(self, engine: SequenceEngine) -> ResumeArm:
        """ResumeArm over ``engine``, with the one session armed: a stop by
        hand disarms it, and an operator who arms it again is the premise of
        an auto-resume."""
        for stored in session_store.load_all():
            stored.auto_resume = True
            session_store.save(stored)
        arm = ResumeArm(engine, self.hub, clock=time.time)
        arm._window_open = lambda s, t: True        # the tick never asks the sky
        return arm


@pytest.fixture
async def rig(tmp_path, monkeypatch):
    """A simulator hub and engine, the real app serving them, and a throwaway
    captures directory and config store."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    cfg = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(cfg.cfg().safety, "solar_avoidance", False)
    _sweep_config_store(monkeypatch, cfg)
    monkeypatch.delenv(app_module.AUTH_ENV_VAR, raising=False)
    reset_active_provider()
    hub = Hub()
    await hub.connect_sim()
    engine = SequenceEngine(hub)
    monkeypatch.setattr(app_module, "engine", engine)
    monkeypatch.setattr(app_module, "hub", hub)
    app = app_module.create_app()
    _sweep_config_store(monkeypatch, cfg)
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


async def test_a_resumed_night_publishes_its_stages(rig):
    """Night one runs a staged plan and is stopped by hand; night two is
    ResumeArm's auto-resume of the stored session, on a fresh engine. Night
    two publishes ``stages`` naming the same nodes night one did, and says
    each of them done at the end.

    RED under M1 (the map not applied when a stored session is built),
    observed:

        AssertionError: the resumed night published no stages
    """
    sid, first = await rig.first_night(staged_plan(count=4))
    assert set(first) == {BLOCK, STAGE_L}, (
        f"premise: night one names its block and its first stage: {first}")
    engine = rig.next_engine()
    arm = rig.resume_arm(engine)
    await arm.tick()
    assert await wait_for(
        lambda: session_store.load(sid).status == "complete"), (
        "premise: night two ran the plan to its end")
    assert await wait_for(lambda: not engine.running)
    stages = engine.state.get("stages")
    assert stages, "the resumed night published no stages"
    assert set(stages) >= set(first), (stages, first)
    assert stages == {BLOCK: "done", STAGE_L: "done", STAGE_R: "done"}


async def test_the_resume_route_publishes_its_stages_too(rig):
    """``POST /api/sessions/{id}/resume`` builds its run from the stored
    session by another door, and names the stages the same way."""
    sid, _ = await rig.first_night(staged_plan(count=4))
    engine = rig.next_engine()
    r = await rig.client.post(f"/api/sessions/{sid}/resume", json={})
    assert r.status_code == 200, r.text
    assert await wait_for(
        lambda: session_store.load(sid).status == "complete")
    assert await wait_for(lambda: not engine.running)
    assert engine.state.get("stages") == {
        BLOCK: "done", STAGE_L: "done", STAGE_R: "done"}


async def test_a_resumed_session_without_the_names_publishes_none(rig):
    """A session file written before the names were kept runs as it always
    did: it resumes, completes, and publishes no ``stages`` key."""
    plan = plain_plan()
    plan.targets[0].steps[0].count = 4
    sid, first = await rig.first_night(plan)
    assert first == {}, "premise: a plan naming nothing publishes nothing"
    raw = stored_file(sid)
    for key in ("flow_blocks", "flow_stages"):
        raw.pop(key, None)
    session_store._path(sid).write_text(json.dumps(raw), encoding="utf-8")
    engine = rig.next_engine()
    await rig.resume_arm(engine).tick()
    assert await wait_for(
        lambda: session_store.load(sid).status == "complete")
    assert await wait_for(lambda: not engine.running)
    assert "stages" not in engine.state


# --------------------------------------------------------- the real compile

async def test_a_flow_runs_session_file_names_what_the_compile_named(flow_rig):
    """The names the real compile put on a flow's plan reach the file the
    flow run's session is saved to, and a session read back from it names
    the same stages and blocks as the plan the run was started with."""
    rig: FlowRig = flow_rig
    night = await run_lane_flow(rig)
    (stored,) = session_store.load_all()
    ran = names_of(night.engine.plan)
    assert ran[0] == {t.id: "n2" for t in night.engine.plan.targets}
    assert set(ran[1].values()) == {"n3", "n4"}
    assert names_of(stored.plan) == ran
    raw = stored_file(stored.id)
    assert (raw["flow_blocks"], raw["flow_stages"]) == ran
