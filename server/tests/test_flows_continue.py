"""Run CONTINUE: night two banks on night one's ledger (#189 S1, spec 5.9, D6;
task S1-13).

One ledger per flow. ``POST /api/flows/{id}/run`` compiles with the flow's id,
so every night names the same targets and steps (spec 3.3), and when the
newest session the flow started is dormant it hands THAT session to
``engine.start(session=)`` with tonight's compile as its plan - the call a
resume makes. The ledger counts frames by step id alone, so the frames banked
on night one count toward night two with no second ledger and no schema
change.

Continuing can change what the ledger counts, so three things are asked
first, each as a 409 the next request answers with a flag:

* ``adopt`` - a pre-S1 session holds frames under uuid4 step ids no compile
  produces again. Continuing it silently would count none of them, and a
  silent fresh start would disarm it. ADOPT re-keys the unique matches after
  a ``.bak``; the rest are listed.
* ``recount`` - the ledger is counted by its frozen plan's ``count_mode``.
* ``dropped_steps`` - steps holding frames are gone from the flow.

The race with ResumeArm is ``test_flows_continue_race.py``.

THE HARNESS. The real app over ``httpx.ASGITransport`` on the test's own event
loop (no lifespan, so no background service is ticking), with a real
``SequenceEngine``: ``start`` - its "already running" refusal, the status flip,
the auto-resume singleton, the night appended and the save - is the engine's
own, and so are the ledger write (``_record_session_frame``) and the night's
ending (``_finalize_report``). Only ``_run``, the imaging loop, is replaced
(``_Night``), because nothing here is about what a night shoots. The one test
the spec runs "on the simulator" uses the whole engine on the sim rig.

Every test that guards a branch names the mutant it kills and quotes the
failure that mutant produced. Each mutant was applied to a byte-for-byte
backup of the file it changes, in a copy of ``server/`` so no other suite saw
it, and the file was restored byte-identical (SHA-256 compared) afterwards.
"""
from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from uuid import uuid4

import httpx
import pytest

import astrodeck.api.app as app_module
import astrodeck.config as config_mod
import astrodeck.flows.store as flow_store_module
import astrodeck.hub as hub_module
from astrodeck.auth.deps import reset_active_provider
from astrodeck.config import ConfigStore
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.continuation import (AMBIGUOUS, NO_MATCH, adopt_matches,
                                          apply_adoption, dropped_detail,
                                          plan_replace_report, recount,
                                          recount_detail)
from astrodeck.flows.models import FlowGraph
from astrodeck.flows.store import FlowStore
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target
from astrodeck.sequence.session import (SESSION_STATUSES, Session, SessionFrame,
                                        SessionStore, session_store)


# ------------------------------------------------------------------ graphs

def _capture(node_id: str, filt: str, *, exposure: float = 0.05,
             count: int = 3) -> dict:
    """A CAPTURE LOOP with no integration goal, so the compile reports no
    loss and ``/run`` needs no ``accept_unmapped``."""
    return {"id": node_id, "type": "capture", "x": 0, "y": 0,
            "params": {"filter": filt, "exposure": exposure, "gain": 100,
                       "bin": "1", "count": count, "goal": 0}}


def _graph(*captures: dict, name: str = "M42") -> dict:
    """One TARGET feeding the captures in a chain."""
    nodes = [{"id": "t", "type": "target", "x": 0, "y": 0,
              "params": {"name": name, "ra": "05h 35m 17s",
                         "dec": "-05 23 28"}}, *captures]
    edges, prev, port = [], "t", "target"
    for c in captures:
        edges.append({"from": prev, "fromPort": port, "to": c["id"],
                      "toPort": "run"})
        prev, port = c["id"], "complete"
    return {"nodes": nodes, "edges": edges}


#: L then R on one target: two steps, so one can be dropped while one is kept.
LR = _graph(_capture("c1", "L"), _capture("c2", "R", count=2))
#: The same flow with the R capture removed.
L_ONLY = _graph(_capture("c1", "L"))


def _reframed(graph: dict, *, ra: str) -> dict:
    """``graph`` with its TARGET moved to ``ra``: the same node, name and
    recipes, another field. A single target is keyed on the geometry it is at
    (spec 3.3), so every step id changes."""
    nodes = [{**n, "params": {**n["params"], "ra": ra}}
             if n["type"] == "target" else n for n in graph["nodes"]]
    return {"nodes": nodes, "edges": graph["edges"]}


def _compiled(graph: dict, flow_id: str) -> SequencePlan:
    """The plan ``run_flow`` compiles for this graph: the same compile and the
    same ``flow_id``, so the same ids."""
    g = FlowGraph.model_validate(graph)
    plan, _ = to_sequence_plan(compile_plan(g, "x"), g, flow_id=flow_id)
    return plan


# ------------------------------------------------------------------ harness

@dataclass
class Start:
    """One call to ``engine.start``, as it arrived."""
    won: bool
    session_id: str | None          # None = a fresh start
    frames: list[tuple[str, str]]   # (target_id, step_id), before the start
    frame_ids: list[str]
    step_ids: list[str]             # the plan's
    count_mode: str
    error: str = ""


class _Night:
    """Stands in for ``SequenceEngine._run``, the imaging loop, and for nothing
    else. A night lasts until the test ends it, and ends through the engine's
    own ``_finalize_report``, which is what sets a session dormant or
    complete and saves it."""

    def __init__(self, engine: SequenceEngine) -> None:
        self.engine = engine
        self._release: asyncio.Event | None = None
        self._reason = "incomplete"

    def __call__(self):
        self._release = asyncio.Event()
        return self._body(self._release)

    async def _body(self, release: asyncio.Event) -> None:
        await release.wait()
        self.engine._finalize_report(self._reason)

    def end(self, reason: str) -> None:
        self._reason = reason
        assert self._release is not None, "no night is running"
        self._release.set()


def _record_starts(engine: SequenceEngine, monkeypatch) -> list[Start]:
    """Wrap the engine's real ``start`` to record what each call received."""
    starts: list[Start] = []
    real = engine.start

    def start(plan, **kw):
        s = kw.get("session")
        rec = Start(won=False, session_id=s.id if s is not None else None,
                    frames=[(f.target_id, f.step_id) for f in s.frames]
                    if s is not None else [],
                    frame_ids=[f.id for f in s.frames] if s is not None else [],
                    step_ids=[st.id for t in plan.targets for st in t.steps],
                    count_mode=plan.count_mode)
        try:
            real(plan, **kw)
        except Exception as e:
            rec.error = str(e)
            starts.append(rec)
            raise
        rec.won = True
        starts.append(rec)

    monkeypatch.setattr(engine, "start", start)
    return starts


def _isolate(tmp_path, monkeypatch) -> None:
    """test_plan_identity's ``api`` isolation: a throwaway config store, flow
    library and captures directory, the camera and the Sun check stubbed on
    the app's hub. A default site never blocks the horizon pre-flight."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "config_store", store)
    monkeypatch.setattr(app_module, "config_store", store)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.delenv(app_module.AUTH_ENV_VAR, raising=False)
    flows = FlowStore(tmp_path / "flows")
    # BOTH names: the route reads app_module's, and the engine's finalize
    # imports the module's at call time to write the card's last result.
    monkeypatch.setattr(app_module, "flow_store", flows)
    monkeypatch.setattr(flow_store_module, "flow_store", flows)
    monkeypatch.setattr(app_module.hub, "_check_solar", lambda *a, **kw: None)
    monkeypatch.setattr(app_module.hub, "require", lambda role: object())
    monkeypatch.setattr(app_module.hub, "last_frame", None, raising=False)
    reset_active_provider()


class Rig:
    def __init__(self, client: httpx.AsyncClient, engine: SequenceEngine,
                 night: _Night | None, starts: list[Start]) -> None:
        self.client = client
        self.engine = engine
        self.night = night
        self.starts = starts

    async def save_flow(self, graph: dict, name: str = "continue me") -> str:
        r = await self.client.post("/api/flows", json={
            "flow": {"name": name, "graph": graph}})
        assert r.status_code == 200, r.text
        return r.json()["id"]

    async def put_flow(self, fid: str, graph: dict,
                       name: str = "continue me") -> None:
        r = await self.client.put(f"/api/flows/{fid}", json={
            "flow": {"name": name, "graph": graph}})
        assert r.status_code == 200, r.text

    async def run(self, fid: str, **flags) -> httpx.Response:
        return await self.client.post(f"/api/flows/{fid}/run", json=flags)

    def bank(self, steps, *, rejected: tuple[int, ...] = ()) -> list[SessionFrame]:
        """One frame per entry of ``steps`` (a step index on the live run's
        first target), through the engine's own ledger write. Entries whose
        POSITION is in ``rejected`` bank as auto-rejected."""
        t = self.engine.plan.targets[0]
        out = []
        for n, i in enumerate(steps):
            sf = self.engine._record_session_frame(
                t, t.steps[i], {}, auto_accepted=n not in rejected)
            assert sf is not None, "the ledger write banked nothing"
            out.append(sf)
        return out

    async def end_night(self, reason: str = "incomplete") -> None:
        self.night.end(reason)
        await self.engine._task

    async def night_one(self, fid: str, steps=(), *,
                        rejected: tuple[int, ...] = (),
                        reason: str = "incomplete") -> Session:
        """A fresh first night through the route: bank ``steps``, then end."""
        r = await self.run(fid)
        assert r.status_code == 200, r.text
        assert r.json()["session"]["continued"] is False, r.json()
        self.bank(steps, rejected=rejected)
        sid = self.engine._session.id
        await self.end_night(reason)
        return session_store.load(sid)


def _app_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_module.create_app()),
        base_url="http://testserver")


@pytest.fixture
async def rig(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    engine = SequenceEngine(app_module.hub)
    night = _Night(engine)
    monkeypatch.setattr(engine, "_run", night)
    starts = _record_starts(engine, monkeypatch)
    monkeypatch.setattr(app_module, "engine", engine)
    async with _app_client() as client:
        yield Rig(client, engine, night, starts)
    if engine.running:
        night.end("aborted")
        await engine._task


def _stored(s: Session) -> Session:
    session_store.save(s)
    return s


def _bytes(session_id: str) -> bytes:
    return session_store._path(session_id).read_bytes()


def _file_frames(session_id: str) -> list[tuple[str, str, str]]:
    return [(f.id, f.target_id, f.step_id)
            for f in session_store.load(session_id).frames]


def _bak(session_id: str):
    path = session_store._path(session_id)
    return path.with_suffix(path.suffix + ".bak")


# ================================================================ the pure half

def _pure_session(steps: list[ExposureStep], frames_on: dict[int, int], *,
                  name: str = "M42", count_mode: str = "attempts",
                  rejected: int = 0) -> Session:
    """A session whose one target carries ``steps``; ``frames_on`` maps a step
    index to how many frames it holds. The first ``rejected`` frames are
    auto-rejected."""
    t = Target(name=name, ra_hours=5.5, dec_deg=-5.0, center=False,
               autofocus_first=False, steps=steps)
    s = Session(status="dormant",
                plan=SequencePlan(name="p", targets=[t], count_mode=count_mode))
    n = 0
    for i, k in frames_on.items():
        for _ in range(k):
            s.frames.append(SessionFrame(target_id=t.id, step_id=steps[i].id,
                                         auto_accepted=n >= rejected))
            n += 1
    return s


def _step(filt: str | None = "L", exposure: float = 60.0, **kw) -> ExposureStep:
    return ExposureStep(filter=filt, exposure_s=exposure, count=5, **kw)


class TestPlanReplaceReport:
    def test_kept_new_and_only_the_dropped_steps_that_hold_frames(self):
        """The rule ``patch_session`` computed inline, now one function both
        callers use.

        RED under mutant "dropped ignores frames" (``& with_frames`` removed
        from ``plan_replace_report``):

            AssertionError: a dropped step with no frames loses nothing and
            must not be reported
            assert ['b7517511621...39700e3090bc'] == ['b7517511621...de9175459abf']
              Left contains one more item: 'ca983d6f89704853875239700e3090bc'

        That mutant leaves ``test_sessions_api.py::
        test_patch_plan_dormant_only_with_id_merge`` GREEN - its session has
        no frameless step to drop - which is why the PATCH answer is pinned
        again below.
        """
        keep, gone_with, gone_without = _step("L"), _step("R"), _step("G")
        s = _pure_session([keep, gone_with, gone_without], {0: 2, 1: 3})
        fresh = _step("B")
        new = SequencePlan(targets=[Target(name="M42", ra_hours=5.5,
                                           dec_deg=-5.0,
                                           steps=[keep.model_copy(), fresh])])
        r = plan_replace_report(s, new)
        assert r.kept == [keep.id]
        assert r.new == [fresh.id]
        assert r.dropped == [gone_with.id], (
            "a dropped step with no frames loses nothing and must not be "
            "reported")
        assert r.dropped_frames == 3
        assert r.merge() == {"kept": [keep.id], "new": [fresh.id],
                             "dropped": [gone_with.id]}

    def test_control_an_identical_plan_keeps_everything(self):
        steps = [_step("L"), _step("R")]
        s = _pure_session(steps, {0: 1, 1: 1})
        r = plan_replace_report(s, s.plan.model_copy(deep=True))
        assert r.kept == sorted(st.id for st in steps)
        assert r.new == [] and r.dropped == [] and r.dropped_frames == 0

    async def test_patch_session_answers_through_the_same_rule(self, rig):
        """``PATCH /api/sessions/{id}`` with a plan reports ``merge`` from
        ``plan_replace_report``: a dropped step with frames is named, a
        dropped step without them is not, and nothing is refused.

        RED under mutant "dropped ignores frames":

            AssertionError: assert {'dropped': [...'], 'new': []} == {'dropped': [...'], 'new': []}
              Differing items:
              {'dropped': ['3c76acf063c84d6680364f587d1534de', '56fe98ef0b4b49fca96aebdfae354c57']} != {'dropped': ['3c76acf063c84d6680364f587d1534de']}
        """
        keep, gone_with, gone_without = _step("L"), _step("R"), _step("G")
        s = _stored(_pure_session([keep, gone_with, gone_without],
                                  {0: 1, 1: 2}))
        edited = s.plan.model_copy(deep=True)
        edited.targets[0].steps = edited.targets[0].steps[:1]
        r = await rig.client.patch(f"/api/sessions/{s.id}", json={
            "plan": edited.model_dump(mode="json")})
        assert r.status_code == 200, r.text
        assert r.json()["merge"] == {"kept": [keep.id], "new": [],
                                     "dropped": [gone_with.id]}
        assert len(session_store.load(s.id).frames) == 3


class TestAdoptMatches:
    def _new_plan(self, *steps: ExposureStep, name: str = "M42") -> SequencePlan:
        return SequencePlan(targets=[Target(name=name, ra_hours=5.5,
                                            dec_deg=-5.0, steps=list(steps))])

    def test_unique_matches_map_and_the_rest_are_listed_with_reasons(self):
        """RED under mutant "ambiguous counts as a match" (the first of several
        candidates is taken, ``if len(olds) == 1 and len(news) == 1`` ->
        ``if news``) - both R steps map onto the one new R:

            AssertionError: assert {'86afedebd0c...492c583a471')} == {'c653e1a2311...492c583a471')}
              Left contains 2 more items:
              {'86afedebd0c7405aad9a17b0b96c5db1': ('66a9748c1f164b2880b2baffb8a2c3ef',
                                                  'a96ae6ee177d4a88b68a8869fb995213'),
               'a116974560b04c3a96800080a3656d01': ('66a9748c1f164b2880b2baffb8a2c3ef',
                                                  'a96ae6ee177d4a88b68a8869fb995213')}

            and at the route, ``test_a_pre_s1_session_gets_the_adopt_answer_then_adopts``:

            assert (5 == 5 and 4 == 2)
        """
        old_l, old_r1, old_r2, old_ha = (_step("L"), _step("R"), _step("R"),
                                         _step("Ha"))
        s = _pure_session([old_l, old_r1, old_r2, old_ha],
                          {0: 2, 1: 1, 2: 1, 3: 1})
        new_l, new_r = _step("L"), _step("R")
        plan = self._new_plan(new_l, new_r)
        m = adopt_matches(s, plan)
        assert m.mapping == {old_l.id: (plan.targets[0].id, new_l.id)}
        assert m.frames_matched == 2
        assert sorted(r["step_id"] for r in m.ambiguous) == sorted(
            [old_r1.id, old_r2.id])
        assert all(r["reason"] == AMBIGUOUS for r in m.ambiguous)
        assert [(r["step_id"], r["reason"], r["frames"])
                for r in m.unmatched] == [(old_ha.id, NO_MATCH, 1)]
        assert [r["step_id"] for r in m.rest()] == [
            *(r["step_id"] for r in m.ambiguous), old_ha.id]

    def test_two_new_steps_with_one_key_match_nothing(self):
        """Uniqueness is on BOTH sides: one old step and two new ones sharing
        its key is as ambiguous as the reverse.

        RED under mutant "one-side uniqueness" (``len(news) == 1`` ->
        ``news``):

            AssertionError: assert ({'bb6a70b4be2...484de747d23')} == {}
              Left contains 1 more item:
              {'bb6a70b4be254ccba33f7d49b9f432ea': (
                  'e57b8ecd2b6b42ec985cb1c9b5444de3',
                  '5eb35f502cd9426ebe5e9484de747d23')}
        """
        old = _step("L")
        s = _pure_session([old], {0: 1})
        m = adopt_matches(s, self._new_plan(_step("L"), _step("L")))
        assert m.mapping == {} and m.frames_matched == 0
        assert [r["reason"] for r in m.ambiguous] == [AMBIGUOUS]

    def test_every_field_of_the_key_must_agree(self):
        """Exposure, gain, binning, frame type and target name each keep a
        step from matching; a None filter and "" are the same key.

        RED under mutant "key ignores gain" (``int(step.gain)`` dropped from
        ``_step_key``):

            AssertionError: ExposureStep(id='867b4b27a0954f66af3d435d1b8b68d4',
            filter='L', exposure_s=60.0, gain=200, ...)
            assert {'dab6afeeccc...35d1b8b68d4')} == {}

        RED under mutant "None filter is its own key" (``step.filter or ""``
        -> ``step.filter``):

            AssertionError: assert 0 == 1
             +  where 0 = AdoptMatches(mapping={}, unmatched=[{... 'reason':
             'no step in this flow matches it'}], ...).frames_matched
        """
        s = _pure_session([_step(None)], {0: 1})
        assert adopt_matches(s, self._new_plan(_step(""))).frames_matched == 1
        base = _pure_session([_step("L")], {0: 1})
        for other in (_step("L", exposure=120.0), _step("L", gain=200),
                      _step("L", binning=2), _step("L", frame_type="Dark")):
            m = adopt_matches(base, self._new_plan(other))
            assert m.mapping == {}, other
        assert adopt_matches(base, self._new_plan(_step("L"),
                                                  name="M43")).mapping == {}

    def test_control_frameless_steps_are_not_listed(self):
        """A step with no frames has nothing to carry, so it is not put in
        front of the operator as "left as it is".

        RED under mutant "frameless steps listed" (``elif n:`` -> ``else:``):

            AssertionError: assert ([{'binning': ...'Light', ...}] == []
              Left contains one more item: {'binning': 1, 'exposure_s': 60.0,
              'filter': 'Ha', 'frame_type': 'Light', ...}
        """
        s = _pure_session([_step("L"), _step("Ha")], {0: 1})
        m = adopt_matches(s, self._new_plan(_step("L")))
        assert m.unmatched == [] and m.ambiguous == []

    def test_apply_rekeys_the_matched_frames_and_steps_only(self):
        """RED under mutant "apply leaves the plan ids" (the old plan's step
        ids not rewritten), which makes the report after an adoption call the
        adopted step dropped:

            AssertionError: assert [] == ['f522be8869a...a87daeb2a084']
              Right contains one more item: 'f522be8869a944998075a87daeb2a084'

        and at the route (``test_a_pre_s1_session_gets_the_adopt_answer_then_
        adopts``): ``assert (0, 2, 3) == (1, 1, 3)``.
        """
        old_l, old_ha = _step("L"), _step("Ha")
        s = _pure_session([old_l, old_ha], {0: 2, 1: 1})
        new_l = _step("L")
        plan = self._new_plan(new_l)
        m = adopt_matches(s, plan)
        assert apply_adoption(s, m) == 2
        assert [f.step_id for f in s.frames] == [new_l.id, new_l.id, old_ha.id]
        assert {f.target_id for f in s.frames[:2]} == {plan.targets[0].id}
        # ...and the old plan's matched step now reads as kept.
        assert plan_replace_report(s, plan).kept == [new_l.id]


class TestRecount:
    def test_both_totals_in_both_directions(self):
        """RED under mutant "recount ignores the mode" (``_counted`` always
        ``len(session.frames)``):

            assert (5, 5) == (3, 5)
              At index 0 diff: 5 != 3

            and at the route, the 409 names the wrong total:

            {'before': 3} != {'before': 2}
        """
        s = _pure_session([_step("L")], {0: 5}, count_mode="accepted",
                          rejected=2)
        attempts = s.plan.model_copy(update={"count_mode": "attempts"})
        assert recount(s, attempts) == (3, 5)
        s.plan = attempts
        accepted = attempts.model_copy(update={"count_mode": "accepted"})
        assert recount(s, accepted) == (5, 3)
        assert recount_detail("attempts", "accepted", 412, 371) == (
            "this session counted every sub taken (412); counting accepted "
            "subs makes it 371")

    def test_the_dropped_sentence(self):
        assert dropped_detail(212) == (
            "212 subs belong to steps this flow no longer has; they stay on "
            "disk")
        assert dropped_detail(1).startswith("1 sub belongs to a step")


# ================================================================ the store

def _flow_session(flow_id: str, *, created: float, status: str = "dormant",
                  origin: str = "flow") -> Session:
    return Session(name=f"{flow_id}@{created}", created_ts=created,
                   status=status, origin=origin, origin_id=flow_id)


class TestNewestForFlow:
    @pytest.fixture(autouse=True)
    def _captures(self, tmp_path, monkeypatch):
        monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)

    def test_created_not_updated_decides(self):
        """``engine.start``'s singleton disarm saves every other armed session,
        and ``save`` stamps ``updated_ts``, so the OLDER ledger is routinely
        the more recently updated file.

        RED under mutant "order by updated_ts" (``raw.get("created_ts")`` ->
        ``raw.get("updated_ts")`` in ``newest_for_flow``):

            AssertionError: assert (Session(id='934554d81e874bae884c3f44c556cf12', schema_version=1,
            name='f@100.0', created_ts=100.0, ...) is not None and
            '934554d81e87...c3f44c556cf12' == 'e76a618883e3...0ad758a4403fd'
        """
        older = _stored(_flow_session("f", created=100.0))
        newer = _stored(_flow_session("f", created=200.0))
        older.auto_resume = False
        session_store.save(older)             # re-timestamped, as a disarm does
        assert session_store.load(older.id).updated_ts > \
            session_store.load(newer.id).updated_ts, "premise: older is fresher"
        got = session_store.newest_for_flow("f", ("dormant",))
        assert got is not None and got.id == newer.id

    def test_only_this_flows_sessions_in_the_asked_statuses(self):
        """RED under mutant "lookup ignores the flow" (the ``origin_id`` test
        removed from ``newest_for_flow``), where flow g's newer session wins:

            AssertionError: assert '3e7510b69b99...ce5a39c91e63a' ==
            'ebb13f909aa4...e7b90dc64ea5a'
        """
        mine = _stored(_flow_session("f", created=100.0))
        _stored(_flow_session("g", created=300.0))                # other flow
        _stored(_flow_session("f", created=400.0, origin="plan"))  # not a flow
        done = _stored(_flow_session("f", created=500.0, status="complete"))
        assert session_store.newest_for_flow("f", ("dormant",)).id == mine.id
        assert session_store.newest_for_flow("f", SESSION_STATUSES).id == done.id
        assert session_store.newest_for_flow("nope", SESSION_STATUSES) is None

    def test_an_unreadable_newest_is_skipped(self):
        """The rule ``active`` and ``load_all`` keep: a file that no longer
        validates must not hide the next one.

        RED under mutant "unreadable newest raises" (the ``try``/``except
        Exception: continue`` around the validation removed):

            pydantic_core._pydantic_core.ValidationError: 1 validation error
            for Session
            frames
              Input should be a valid list [type=list_type, input_value=7,
              input_type=int]
        """
        good = _stored(_flow_session("f", created=100.0))
        bad = session_store._path(uuid4().hex)
        bad.write_text('{"origin": "flow", "origin_id": "f", "status": '
                       '"dormant", "created_ts": 900.0, "frames": 7}',
                       encoding="utf-8")
        assert session_store.newest_for_flow("f", ("dormant",)).id == good.id


class TestWriteLockedAndBackup:
    @pytest.fixture(autouse=True)
    def _captures(self, tmp_path, monkeypatch):
        monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)

    def test_write_locked_holds_the_lock_every_write_takes(self):
        """Another thread cannot take the store's write lock - the one
        ``save`` and ``save_run_state`` take - while a caller holds
        ``write_locked``; and ``save`` inside it does not deadlock.

        RED under mutant "write_locked without the lock" (``with
        self._write_lock: yield self`` -> ``yield self``):

            AssertionError: another thread took the lock mid-section
            assert [True] == [False]
        """
        seen: list[bool] = []

        def try_lock() -> None:
            got = SessionStore._write_lock.acquire(blocking=False)
            seen.append(got)
            if got:
                SessionStore._write_lock.release()

        s = _flow_session("f", created=1.0)
        with session_store.write_locked() as store:
            store.save(s)                    # re-entrant: no deadlock
            t = threading.Thread(target=try_lock)
            t.start()
            t.join()
        assert seen == [False], "another thread took the lock mid-section"
        t = threading.Thread(target=try_lock)
        t.start()
        t.join()
        assert seen == [False, True], "control: the lock is free afterwards"

    def test_backup_is_a_copy_nothing_lists_and_delete_removes(self):
        """RED under mutant "delete leaves the .bak" (the ``bak.unlink()`` in
        ``delete`` removed):

            AssertionError: a deleted session's ledger copy was left
            assert not True
             +  where True = exists()
        """
        s = _stored(_flow_session("f", created=1.0))
        before = _bytes(s.id)
        bak = session_store.backup(s.id)
        assert bak == _bak(s.id) and bak.read_bytes() == before
        assert _bytes(s.id) == before, "the live file must not move"
        assert [x.id for x in session_store.load_all()] == [s.id], (
            "the .bak was read as a second session")
        session_store.delete(s.id)
        assert not bak.exists(), "a deleted session's ledger copy was left"

    def test_the_backup_is_hardened_like_the_file_it_copies(self, monkeypatch):
        """``shutil.copy2`` does not carry a Windows DACL, so the copy is
        hardened on its own, through the same ``harden_private_file`` every
        session write uses.

        RED under mutant "backup not hardened" (``harden_private_file(bak)``
        removed from ``backup``), observed by the verifier:

            AssertionError: the .bak was never hardened
            assert WindowsPath('C:/Users/bear/AppData/Local/Temp/pytest-of-
            bear/pytest-12236/test_the_backup_is_hardened_li0/sessions/
            0aee7b4399b144b7a640826bebe94bdd.json.bak') in []
        """
        import astrodeck.sequence.session as session_mod
        hardened: list = []
        real = session_mod.harden_private_file

        def spy(path):
            hardened.append(path)
            return real(path)

        monkeypatch.setattr(session_mod, "harden_private_file", spy)
        s = _stored(_flow_session("f", created=1.0))
        bak = session_store.backup(s.id)
        assert bak in hardened, "the .bak was never hardened"


# ================================================================ the route

class TestContinue:
    async def test_the_second_run_hands_night_ones_ledger_to_the_engine(self, rig):
        """The ids are the deterministic compile's, the same session goes to
        ``engine.start`` carrying the frames night one banked, and their step
        ids are the plan's.

        RED under mutant "run_flow compiles without flow_id" (``flow_id=
        flow_id,`` removed from ``run_flow``'s ``to_sequence_plan``): the ids
        are uuid4 again, night two shares none with night one, and the answer
        is the adopt question. The first assertion to see it is the premise
        that night one compiled with the flow's id:

            AssertionError: night one did not compile with the flow's id
            assert ['a4d705e66e0...5844befd1214'] == ['dd26263bf97...f8befad40861']

            and the simulator test below shows the adopt answer itself.

        RED under mutant "never continue" (``latest = None`` kept whatever the
        lookup found):

            AssertionError: night two did not continue night one's session:
            Start(won=True, session_id=None, frames=[], frame_ids=[],
            step_ids=['7bdd8ab4034550a086bb293aa6253eb6', '496e80226e8c572ea2b929b193f95fa6'],
            count_mode='attempts', error='')
        """
        fid = await rig.save_flow(LR)
        one = await rig.night_one(fid, [0, 0, 1])
        assert one.status == "dormant" and len(one.frames) == 3
        expected = _compiled(LR, fid)
        assert rig.starts[0].step_ids == [
            st.id for t in expected.targets for st in t.steps], (
            "night one did not compile with the flow's id")

        r = await rig.run(fid)

        assert r.status_code == 200, r.text
        two = rig.starts[-1]
        assert two.won and two.session_id == one.id, (
            f"night two did not continue night one's session: {two}")
        assert two.frame_ids == [f.id for f in one.frames]
        assert {step for _t, step in two.frames} <= set(two.step_ids), (
            "the frames handed over sit on steps the plan does not have")
        assert r.json()["session"] == {
            "id": one.id, "night": 2, "continued": True,
            "kept": 2, "new": 0, "dropped": 0}
        stored = session_store.load(one.id)
        assert stored.status == "active" and len(stored.nights) == 2
        assert [f.id for f in stored.frames] == [f.id for f in one.frames]
        # ...and tonight banks on the SAME ledger entries.
        rig.bank([0])
        assert session_store.load(one.id).accepted_by_step() == {
            two.step_ids[0]: 3, two.step_ids[1]: 1}

    async def test_dropped_steps_are_asked_about_then_accepted(self, rig):
        """RED under mutant "no dropped check" (the ``dropped_steps`` refusal
        removed from ``_continue_flow_session``):

            AssertionError: {"started":true,...,"frames":3,"unmapped":[],
            "session":{"id":"0b0072c1c0804005857d9c13c820b314","night":2,
            "continued":true,"kept":1,"new":0,"dropped":1}}
            assert 200 == 409
        """
        fid = await rig.save_flow(LR)
        one = await rig.night_one(fid, [0, 1, 1])
        await rig.put_flow(fid, L_ONLY)
        before = _bytes(one.id)

        r = await rig.run(fid)

        assert r.status_code == 409, r.text
        assert r.json()["detail"] == {
            "code": "dropped_steps",
            "detail": "2 subs belong to steps this flow no longer has; they "
                      "stay on disk",
            "dropped_frames": 2, "session_id": one.id}
        assert _bytes(one.id) == before, "a refusal wrote the session"
        assert len(rig.starts) == 1, "a refusal reached engine.start"

        r = await rig.run(fid, accept_dropped=True)

        assert r.status_code == 200, r.text
        assert rig.starts[-1].session_id == one.id
        assert rig.starts[-1].frame_ids == [f.id for f in one.frames], (
            "frames on the dropped step must stay in the ledger")
        assert r.json()["session"]["dropped"] == 1
        assert r.json()["session"]["kept"] == 1

    async def test_fresh_starts_over_and_leaves_the_old_session_alone(self, rig):
        """RED under mutant "fresh ignored" (``if not body.fresh:`` ->
        ``if True:``):

            AssertionError: START OVER handed the old session to the engine
            assert (True and 'fcef1abd3a5a4808bb9ed7cdee19b910' is None)
        """
        fid = await rig.save_flow(LR)
        one = await rig.night_one(fid, [0, 1])

        r = await rig.run(fid, fresh=True)

        assert r.status_code == 200, r.text
        assert rig.starts[-1].won and rig.starts[-1].session_id is None, (
            "START OVER handed the old session to the engine")
        new_id = r.json()["session"]["id"]
        assert new_id not in (None, one.id)
        assert r.json()["session"]["continued"] is False
        old = session_store.load(one.id)
        assert old.status == "dormant"
        assert [f.id for f in old.frames] == [f.id for f in one.frames]
        assert session_store.load(new_id).frames == []

    async def test_a_pre_s1_session_gets_the_adopt_answer_then_adopts(self, rig):
        """A session saved before S1: uuid4 ids, the flow's recipe, frames.

        RED under mutant "fresh on no shared ids" (the adopt refusal replaced
        by ``engine.start(plan, origin="flow", origin_id=s.origin_id)`` and a
        fresh answer): the request succeeds as a fresh start, which disarms
        the old session:

            AssertionError: {"started":true,...,"frames":5,"unmapped":[],
            "session":{"id":null,"night":1,"continued":false,"kept":0,
            "new":0,"dropped":0}}
            assert 200 == 409

        RED under mutant "adopt without a .bak" (``session_store.backup(s.id)``
        removed):

            FileNotFoundError: [Errno 2] No such file or directory: '...\\
            captures\\sessions\\051cec105e2e4a6ba19c92892978de49.json.bak'

        RED under mutant "saved_before_s1 never true" (its ``return not
        any(...)`` short-circuited to False), the control for the re-frame
        case below: a uuid4 session must still be asked, or ADOPT is gone:

            AssertionError: {'code': 'dropped_steps', 'detail': '5 subs belong
            to steps this flow no longer has; they stay on disk',
            'dropped_frames': 5, 'session_id': '6b35aa6cd17748a491324734dd267c04'}
            assert 'dropped_steps' == 'adopt'
        """
        fid = await rig.save_flow(LR)
        old_l, old_r1, old_r2, old_ha = (
            ExposureStep(filter="L", exposure_s=0.05, count=3),
            ExposureStep(filter="R", exposure_s=0.05, count=2),
            ExposureStep(filter="R", exposure_s=0.05, count=2),
            ExposureStep(filter="Ha", exposure_s=0.05, count=2))
        t = Target(name="M42", ra_hours=5.588, dec_deg=-5.39,
                   steps=[old_l, old_r1, old_r2, old_ha])
        old = Session(name="pre-S1", created_ts=1.0, status="dormant",
                      auto_resume=True, origin="flow", origin_id=fid,
                      plan=SequencePlan(name="pre-S1", targets=[t]))
        for st, n in ((old_l, 2), (old_r1, 1), (old_r2, 1), (old_ha, 1)):
            for _ in range(n):
                old.frames.append(SessionFrame(target_id=t.id, step_id=st.id))
        _stored(old)
        original = _bytes(old.id)

        r = await rig.run(fid)

        assert r.status_code == 409, r.text
        body = r.json()["detail"]
        assert body["code"] == "adopt", body
        assert body["adopt"]["session_id"] == old.id
        assert body["adopt"]["frames"] == 5 and body["adopt"]["matched"] == 2
        assert sorted((u["step_id"], u["reason"])
                      for u in body["adopt"]["unmatched"]) == sorted([
            (old_r1.id, AMBIGUOUS), (old_r2.id, AMBIGUOUS),
            (old_ha.id, NO_MATCH)])
        assert rig.starts == [], "the adopt question reached engine.start"
        assert _bytes(old.id) == original
        assert session_store.load(old.id).auto_resume is True, (
            "the pre-S1 session was disarmed")

        # ADOPT alone: what did not match still holds frames -> rule (c).
        r = await rig.run(fid, adopt=True)
        assert r.status_code == 409 and \
            r.json()["detail"]["code"] == "dropped_steps", r.text
        assert r.json()["detail"]["dropped_frames"] == 3
        assert not _bak(old.id).exists(), "a refused adopt wrote a backup"
        assert _bytes(old.id) == original

        r = await rig.run(fid, adopt=True, accept_dropped=True)

        assert r.status_code == 200, r.text
        assert _bak(old.id).read_bytes() == original, (
            "ADOPT rewrote the ledger without a byte copy of it")
        plan = _compiled(LR, fid)
        new_t, new_l = plan.targets[0].id, plan.targets[0].steps[0].id
        start = rig.starts[-1]
        assert start.won and start.session_id == old.id
        assert start.frames == [(new_t, new_l), (new_t, new_l),
                                (t.id, old_r1.id), (t.id, old_r2.id),
                                (t.id, old_ha.id)], start.frames
        assert [(f.target_id, f.step_id)
                for f in session_store.load(old.id).frames] == start.frames
        out = r.json()["session"]
        assert (out["kept"], out["new"], out["dropped"]) == (1, 1, 3)
        assert out["adopted"]["matched"] == 2
        assert len(out["adopted"]["unmatched"]) == 3

    async def test_no_backup_means_no_adoption(self, rig, monkeypatch):
        """ADOPT rewrites a ledger only with a byte copy of it on disk: when
        the ``.bak`` cannot be written, the request fails before
        ``engine.start`` and the ledger is exactly as it was.

        RED under mutant "backup failure swallowed" (``session_store.
        backup(s.id)`` wrapped in ``try: ... except OSError: pass``), observed
        by the verifier:

            Failed: DID NOT RAISE <class 'OSError'>
        """
        fid = await rig.save_flow(LR)
        t = Target(name="M42", ra_hours=5.588, dec_deg=-5.39,
                   steps=[ExposureStep(filter="L", exposure_s=0.05, count=3)])
        old = Session(name="pre-S1", created_ts=1.0, status="dormant",
                      origin="flow", origin_id=fid,
                      plan=SequencePlan(name="pre-S1", targets=[t]))
        old.frames.append(SessionFrame(target_id=t.id, step_id=t.steps[0].id))
        _stored(old)
        original = _bytes(old.id)

        def no_room(session_id):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(session_store, "backup", no_room)
        with pytest.raises(OSError):
            await rig.run(fid, adopt=True, accept_dropped=True)
        assert rig.starts == [], "ADOPT started without its backup"
        assert _bytes(old.id) == original

    async def test_a_reframed_target_is_the_dropped_question_never_adopt(
            self, rig):
        """A session compiled SINCE S1, whose TARGET has since moved.

        A single target is keyed on the geometry it is at (spec 3.3), so a
        re-frame re-keys every step and tonight's compile shares no step id
        with the session: the shape the adopt gate used to read as "saved
        before S1". It is a re-frame, and spec 5.9 puts a re-frame in the
        dropped-steps row (D5: its counts restart). The adopt question would
        say "saved before flows kept their ids", which is false, and ADOPT
        matches on target NAME and recipe, so pressing it credited all three
        of the old field's frames to the field 2.5 deg away - the flaw D5
        exists to remove. So: the dropped question, an ``adopt`` flag re-keys
        nothing, no ``.bak`` is taken, and once accepted the frames stay on
        their own step ids and count toward nothing tonight.

        RED under mutant "adopt on any session sharing no ids" (``and
        saved_before_s1(s)`` removed from the adopt gate in
        ``_continue_flow_session``), observed:

            AssertionError: a re-framed post-S1 session was offered ADOPT:
            {'code': 'adopt', 'detail': "this flow's session holds 3 subs
            under step ids no compile produces any more (it was saved before
            flows kept their ids). ADOPT re-keys the 3 that match exactly one
            step of this flow and leaves 0 as they are; START OVER begins a new
            session and leaves this one on disk", 'adopt': {...,
            'frames': 3, 'matched': 3, 'unmatched': []}}
            assert 'adopt' == 'dropped_steps'
        """
        fid = await rig.save_flow(LR)
        one = await rig.night_one(fid, [0, 0, 1])
        night_one_steps = {f.step_id for f in one.frames}
        await rig.put_flow(fid, _reframed(LR, ra="05h 45m 17s"))
        tonight = _compiled(_reframed(LR, ra="05h 45m 17s"), fid)
        tonight_steps = {st.id for t in tonight.targets for st in t.steps}
        assert not night_one_steps & tonight_steps, (
            "premise: the re-frame did not re-key the steps")
        before = _bytes(one.id)

        r = await rig.run(fid)

        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "dropped_steps", (
            f"a re-framed post-S1 session was offered ADOPT: {detail}")
        assert detail["dropped_frames"] == 3
        assert _bytes(one.id) == before, "a refusal wrote the session"

        # The flag is not a way round it: there is nothing to adopt.
        r = await rig.run(fid, adopt=True)
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "dropped_steps", r.text
        assert not _bak(one.id).exists(), "a re-frame took an ADOPT backup"

        r = await rig.run(fid, adopt=True, accept_dropped=True)

        assert r.status_code == 200, r.text
        start = rig.starts[-1]
        assert start.won and start.session_id == one.id
        assert {step for _t, step in start.frames} == night_one_steps, (
            f"a re-frame re-keyed night one's frames: {start.frames}")
        assert "adopted" not in r.json()["session"], r.json()["session"]
        assert not set(session_store.load(one.id).accepted_by_step()) \
            & tonight_steps, "the old field's frames count toward the new one"
        assert not _bak(one.id).exists()

    async def test_a_count_mode_change_asks_with_both_totals(self, rig):
        """The session counted accepted subs (it was started while the flow
        asked for them); tonight's compile counts every sub taken.

        RED under mutant "no recount check" (the ``recount`` refusal removed
        from ``_continue_flow_session``):

            AssertionError: {"started":true,...,"frames":5,"unmapped":[],
            "session":{"id":"d93614e055bf4970bd0990677b66cc7e","night":2,
            "continued":true,"kept":2,"new":0,"dropped":0}}
            assert 200 == 409
        """
        fid = await rig.save_flow(LR)
        one = await rig.night_one(fid, [0, 0, 0], rejected=(1,))
        one.plan.count_mode = "accepted"
        session_store.save(one)
        before = _bytes(one.id)

        r = await rig.run(fid)

        assert r.status_code == 409, r.text
        assert r.json()["detail"] == {
            "code": "recount",
            "detail": "this session counted accepted subs (2); counting every "
                      "sub taken makes it 3",
            "before": 2, "after": 3, "session_id": one.id}
        assert _bytes(one.id) == before and len(rig.starts) == 1

        r = await rig.run(fid, accept_recount=True)

        assert r.status_code == 200, r.text
        assert rig.starts[-1].session_id == one.id
        assert rig.starts[-1].count_mode == "attempts"


class TestControls:
    async def test_no_dormant_session_starts_fresh_and_names_it(self, rig):
        """CONTROL for the lookup, and the fresh answer's session id is the
        session the engine made.

        RED under mutant "no read-back" (``made = None``):

            AssertionError: assert {'continued':...kept': 0, ...} == {'continued':...kept': 0, ...}
              Differing items:
              {'id': None} != {'id': '547df9b00f6f4cc1baa37c1ede2e9e4c'}
        """
        fid = await rig.save_flow(LR)
        r = await rig.run(fid)
        assert r.status_code == 200, r.text
        assert rig.starts[0].session_id is None
        assert r.json()["session"] == {
            "id": rig.engine._session.id, "night": 1, "continued": False,
            "kept": 0, "new": 2, "dropped": 0}

    async def test_another_flows_session_is_never_touched(self, rig):
        """Flow A's dormant, unarmed session - unarmed so the engine's own
        auto-resume singleton has nothing to write to it - is byte-identical
        after flow B runs, and flow B starts fresh.

        RED under mutant "lookup ignores the flow" (the ``origin_id`` test
        removed from ``newest_for_flow``) - flow B finds flow A's
        session and asks to adopt it:

            AssertionError: {"detail":{"code":"adopt","detail":"this flow's session holds
            1 sub under step ids no compile produces any more ...","adopt":
            {"session_id":"cf0162942e404a9db8f7739a89e166eb","frames":1,
            "matched":1,"unmatched":[]}}}
            assert 409 == 200
        """
        fa = await rig.save_flow(LR, name="flow A")
        a = await rig.night_one(fa, [0])
        a.auto_resume = False
        session_store.save(a)
        before = _bytes(a.id)
        fb = await rig.save_flow(LR, name="flow B")
        r = await rig.run(fb)
        assert r.status_code == 200, r.text
        assert rig.starts[-1].session_id is None
        assert _bytes(a.id) == before

    async def test_a_dormant_session_with_no_frames_is_continued(self, rig):
        """RED under mutant "continue only with frames" (``and latest.frames``
        added to the continue condition):

            AssertionError: assert None == '2d29ecdb523644cfb3140c32adc86f88'
             +  where None = Start(won=True, session_id=None, frames=[], ...).session_id
        """
        fid = await rig.save_flow(LR)
        one = await rig.night_one(fid, [])
        assert one.status == "dormant" and one.frames == []
        r = await rig.run(fid)
        assert r.status_code == 200, r.text
        assert rig.starts[-1].session_id == one.id
        assert r.json()["session"]["continued"] is True

    async def test_a_frameless_session_with_no_shared_ids_is_continued(
            self, rig):
        """The adopt question is for a ledger that HOLDS FRAMES (spec 5.9 (a):
        "no shared step ids, and frames exist"). A pre-S1 session that never
        banked a frame has nothing to re-key and nothing to lose, so it is
        continued like any other dormant session, and no ``.bak`` is taken.

        The control above continues a frameless session that SHARES ids, so
        it cannot see the frames half of the adopt gate. This one can.

        RED under mutant "adopt gate ignores frames" (``if not report.kept
        and s.frames:`` -> ``if not report.kept:``), observed by the verifier:

            AssertionError: {"detail":{"code":"adopt","detail":"this flow's
            session holds 0 subs under step ids no compile produces any more
            (it was saved before flows kept their ids). ADOPT re-keys the 0
            that match exactly one step of this flow and leaves 0 as they
            are; START OVER begins a new session and leaves this one on
            disk","adopt":{"session_id":"8920c3c6a0ed4bffbb523aad4c61e36c",
            "frames":0,"matched":0,"unmatched":[]}}}
            assert 409 == 200
             +  where 409 = <Response [409 Conflict]>.status_code
        """
        fid = await rig.save_flow(LR)
        t = Target(name="M42", ra_hours=5.588, dec_deg=-5.39,
                   steps=[ExposureStep(filter="L", exposure_s=0.05, count=3),
                          ExposureStep(filter="R", exposure_s=0.05, count=2)])
        old = _stored(Session(
            name="pre-S1, never shot", created_ts=1.0, status="dormant",
            origin="flow", origin_id=fid,
            plan=SequencePlan(name="pre-S1", targets=[t],
                              count_mode=_compiled(LR, fid).count_mode)))
        assert not ({st.id for st in t.steps}
                    & {st.id for tt in _compiled(LR, fid).targets
                       for st in tt.steps}), (
            "premise: the old plan shares no step id with the compile")

        r = await rig.run(fid)

        assert r.status_code == 200, r.text
        assert rig.starts[-1].won and rig.starts[-1].session_id == old.id
        assert r.json()["session"] == {
            "id": old.id, "night": 1, "continued": True,
            "kept": 0, "new": 2, "dropped": 0}
        assert not _bak(old.id).exists(), "nothing was adopted, yet a .bak"

    async def test_a_start_the_engine_refuses_writes_nothing(self, rig):
        """The session is never saved before ``engine.start`` (spec 5.9): the
        start is the first and only write. So when the engine itself refuses
        - here "already running", because flow B's night is on - flow A's
        dormant ledger is byte-identical afterwards: not its plan, not its
        name, not its ``updated_ts``.

        No other test reaches a refused ``engine.start`` inside the section:
        every other refusal is one of the section's own 409s, raised before
        anything is replaced.

        RED under mutant "save inside the lock before start" (``session_store.
        save(s)`` after ``s.plan = plan`` / ``s.name = ...``), observed by the
        verifier:

            AssertionError: a refused start rewrote flow A's session
            assert b'{\\r\\n  "id"...umes": 0\\r\\n}' == b'{\\r\\n  "id"...umes": 0\\r\\n}'
              At index 156 diff: b'3' != b'2'
        """
        fa = await rig.save_flow(LR, name="flow A")
        a = await rig.night_one(fa, [0])
        fb = await rig.save_flow(LR, name="flow B")
        r = await rig.run(fb)
        assert r.status_code == 200, r.text
        assert rig.engine.running, "premise: flow B's night is on"
        before = _bytes(a.id)       # after B's start disarmed A

        r = await rig.run(fa)

        assert r.status_code == 409, r.text
        assert "already running" in r.json()["detail"], r.text
        assert (rig.starts[-1].won, rig.starts[-1].session_id) == (False, a.id), (
            "premise: the continue reached engine.start and was refused there")
        assert _bytes(a.id) == before, "a refused start rewrote flow A's session"

    async def test_a_complete_session_starts_fresh(self, rig):
        """RED under mutant "continue any status" (``latest.status ==
        "dormant"`` removed from the condition) - the complete session
        reaches the lock and is refused there:

            AssertionError: {"detail":{"code":"session_changed",
            "session_id":"487fd81e15a942d399cf738d2c930169","status":"complete",
            "detail":"this flow's session changed while the run was being prepared:
            it is now complete, so it was not continued and nothing was written.
            Press Run again."}}
            assert 409 == 200
        """
        fid = await rig.save_flow(LR)
        one = await rig.night_one(fid, [0, 0, 0, 1, 1], reason="complete")
        assert one.status == "complete"
        r = await rig.run(fid)
        assert r.status_code == 200, r.text
        assert rig.starts[-1].session_id is None
        assert session_store.load(one.id).status == "complete"

    async def test_after_start_over_the_older_dormant_ledger_stays_closed(
            self, rig):
        """START OVER leaves the old session dormant (unarmed) for good. Once
        the new one completes, Run must start fresh, not reopen the ledger
        the operator chose to leave.

        RED under mutant "newest dormant only" (``SESSION_STATUSES`` ->
        ``("dormant",)`` in ``run_flow``'s lookup):

            AssertionError: Run reopened the session START OVER left behind
            assert '9055c591cdcd412891bc5167deacfabd' is None
        """
        fid = await rig.save_flow(LR)
        old = await rig.night_one(fid, [0])
        r = await rig.run(fid, fresh=True)
        assert r.status_code == 200, r.text
        rig.bank([0, 0, 0, 1, 1])
        await rig.end_night("complete")
        r = await rig.run(fid)
        assert r.status_code == 200, r.text
        assert rig.starts[-1].session_id is None, (
            "Run reopened the session START OVER left behind")
        assert session_store.load(old.id).status == "dormant"

    async def test_every_guard_still_applies_to_a_continue(self, rig,
                                                           monkeypatch):
        """A continue is still a start: the Sun refuses it, and it refuses
        before any session is read under the lock or handed over.

        RED under mutant "continue before the guards" (the Sun loop moved
        below the start):

            AssertionError: the continue reached engine.start before the Sun refused it
            assert [Start(won=Tr...s', error='')] == []
              Left contains one more item: Start(won=True,
              session_id='5aedd8ccea28433b871be6629fbab748', ...)
        """
        from astrodeck.devices.base import DeviceError
        fid = await rig.save_flow(LR)
        one = await rig.night_one(fid, [0])
        before = _bytes(one.id)
        monkeypatch.setattr(
            app_module.hub, "_check_solar",
            lambda ra, dec: (_ for _ in ()).throw(
                DeviceError("target is 3.1 deg from the Sun")))
        r = await rig.run(fid, force=True)
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "sun_exclusion"
        assert rig.starts[1:] == [], (
            "the continue reached engine.start before the Sun refused it")
        assert _bytes(one.id) == before


class TestCompileCarriesTheFlowId:
    async def test_the_stored_flows_preview_compiles_with_its_id(self, rig,
                                                                 monkeypatch):
        """Spec 3.3: both call sites pass ``flow_id``. The draft route has no
        id, and passes "".

        RED under mutant "compile route drops flow_id" (``flow_id=flow_id``
        removed from ``compile_flow``'s call):

            AssertionError: assert ['', ''] == ['47422e0c119...a073f517', '']
              At index 0 diff: '' != '47422e0c11934505818db868a073f517'
        """
        seen: list[str] = []
        real = app_module.to_sequence_plan

        def spy(*a, **kw):
            seen.append(kw.get("flow_id", "<absent>"))
            return real(*a, **kw)

        monkeypatch.setattr(app_module, "to_sequence_plan", spy)
        fid = await rig.save_flow(LR)
        r = await rig.client.post(f"/api/flows/{fid}/compile")
        assert r.status_code == 200, r.text
        r = await rig.client.post("/api/flows/compile", json={"graph": LR})
        assert r.status_code == 200, r.text
        assert seen == [fid, ""]


# ================================================================ the simulator

async def _wait(predicate, timeout: float = 40.0) -> bool:
    """test_campaign_across_nights' poll: the sim engine runs on the loop."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


@pytest.fixture
async def sim_rig(tmp_path, monkeypatch):
    """The whole engine on the sim rig behind the real route. The compile is
    the route's own; only what costs sim time and is no part of any id is
    switched off on the plan it returns: centring, focus, the flip, park and
    the warm ramp."""
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(config_mod.config_store.cfg().safety,
                        "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    engine = SequenceEngine(h)
    starts = _record_starts(engine, monkeypatch)
    monkeypatch.setattr(app_module, "engine", engine)
    real = app_module.to_sequence_plan

    def quick(*a, **kw):
        plan, unmapped = real(*a, **kw)
        plan = plan.model_copy(update={
            "meridian_flip": False, "park_when_done": False,
            "warm_cooler_when_done": False, "dither_every": 0})
        for t in plan.targets:
            t.center = False
            t.autofocus_first = False
        return plan, unmapped

    monkeypatch.setattr(app_module, "to_sequence_plan", quick)
    async with _app_client() as client:
        yield Rig(client, engine, None, starts)
    if engine.running:
        await engine.abort()
    await h.disconnect_all()


async def test_on_the_simulator_night_two_banks_on_night_ones_ledger(
        sim_rig, monkeypatch):
    """Spec 8, S1 tests: "A second /run on the simulator banks on the same
    ledger entries." Night one is stopped by hand after its first frame (an
    abort: dormant, and disarmed); night two continues it and finishes it.

    RED under mutant "run_flow compiles without flow_id": night one's
    uuid4 ids are not night two's, so the ledger looks pre-S1:

        AssertionError: {"detail":{"code":"adopt","detail":"this flow's session holds
        1 sub under step ids no compile produces any more (it was saved before
        flows kept their ids). ADOPT re-keys the 1 that match exactly one step of
        this flow and leaves 0 as they are; START OVER begins a new session and
        leaves this one on disk","adopt":{"session_id":
        "ae9846cb33914f0a85e36450423e4432","frames":1,"matched":1,
        "unmatched":[]}}}
        assert 409 == 200
    """
    fid = await sim_rig.save_flow(_graph(_capture("c1", "L", count=4)))
    engine = sim_rig.engine
    real_record = engine._record_session_frame
    pause_after_first = {"on": True}

    def record(*a, **kw):
        sf = real_record(*a, **kw)
        if pause_after_first["on"]:
            engine.pause()
        return sf

    monkeypatch.setattr(engine, "_record_session_frame", record)

    r = await sim_rig.run(fid)
    assert r.status_code == 200, r.text
    assert await _wait(lambda: engine.paused and engine._session is not None
                       and engine._session.frames), "night one banked nothing"
    sid = engine._session.id
    await engine.abort()
    one = session_store.load(sid)
    assert one.status == "dormant" and 1 <= len(one.frames) < 4, one.status
    pause_after_first["on"] = False

    r = await sim_rig.run(fid)

    assert r.status_code == 200, r.text
    assert r.json()["session"]["continued"] is True
    assert r.json()["session"]["id"] == sid
    two = sim_rig.starts[-1]
    assert two.won and two.session_id == sid
    assert two.frame_ids == [f.id for f in one.frames]
    assert {step for _t, step in two.frames} == set(two.step_ids)
    assert await _wait(lambda: engine.state.get("state") == "complete"), (
        f"night two did not finish: {engine.state}")
    done = session_store.load(sid)
    assert done.status == "complete" and len(done.nights) == 2
    assert len(done.frames) == 4
    assert {f.step_id for f in done.frames} == set(two.step_ids), (
        "night two banked on different ledger entries from night one")
    assert [f.id for f in done.frames[:len(one.frames)]] == [
        f.id for f in one.frames]
