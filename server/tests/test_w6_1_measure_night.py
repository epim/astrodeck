# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Measure at the first solve, on the clocked simulator (WP 6.1, #187).

A 2x2 grid whose block is set to "Measure at the first solve" runs the real
scheduler, hop, angle check and ledger (tests/_group_harness.py). The camera
is fixed: the harness scripts the sky angle each centring solve measures, as
test_group_angle_check.py does. The flow is a real one in a store of the
test's own, and the engine's callback is the real ``relay_layout`` bound to
it, exactly as ``api/app.py`` binds it, so what the engine banks is judged
against what a compile of the stored flow makes.

THE CLOCK AND THE SITE are the harness's: every night starts at ``T0`` at the
fixture site (made up), and no assertion reads the wall clock or the PC's time
zone.

Each case names the mutant it was shown RED under.
"""
from __future__ import annotations

import functools

import pytest

from _group_harness import (T0, Night, group_hub, group_store,  # noqa: F401
                            ra_at)
from astrodeck.catalog import framing
from astrodeck.events import night_key
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.continuation import plan_replace_report
from astrodeck.flows.models import FlowGraph, FlowRecord
from astrodeck.flows.store import FlowStore
from astrodeck.flows.to_plan import relay_layout, to_sequence_plan
from astrodeck.sequence.models import LayoutRelayRefused
from astrodeck.sequence.session import Session, SessionFrame, session_store

MEASURE = "Measure at the first solve"
FLOW_ID = "f-measure-night"
DEC = 40.0


def _sexa_ra(hours: float) -> str:
    h = int(hours)
    m = int((hours - h) * 60)
    s = ((hours - h) * 60 - m) * 60
    return f"{h:02d}h {m:02d}m {s:06.3f}s"


def _graph(*, rotation: float, angle: str = MEASURE, cycles: int = 2,
           order: str = "Grid order", lane: str = "L 30, R 30") -> FlowGraph:
    target = {"name": "M31", "ra": _sexa_ra(ra_at(-2.0)), "dec": "+40 00 00",
              "rotation": rotation, "angle": angle, "rows": 2, "cols": 2,
              "overlap": 25, "fovX": 2.0, "fovY": 1.33, "order": order,
              "passes": 1}
    return FlowGraph.model_validate({
        "nodes": [
            {"id": "t", "type": "target", "x": 0, "y": 0, "params": target},
            {"id": "c", "type": "cycle", "x": 230, "y": 0,
             "params": {"plan": lane, "cycles": cycles}}],
        "edges": [
            {"from": "t", "fromPort": "target", "to": "c", "toPort": "run"},
            {"from": "c", "fromPort": "pass", "to": "t", "toPort": "next"}]})


def _plan_of(store: FlowStore, when: float = T0):
    rec = store.get(FLOW_ID)
    plan, _ = to_sequence_plan(compile_plan(rec.graph, rec.name), rec.graph,
                               flow_id=FLOW_ID, when=when)
    plan.guide = False
    plan.dither_every = 0
    plan.autofocus_every = 0
    plan.meridian_flip = False
    plan.park_when_done = False
    plan.warm_cooler_when_done = False
    plan.recover_guiding = False
    for t in plan.targets:
        t.autofocus_first = False
    return plan


@pytest.fixture
def flows(tmp_path):
    return FlowStore(tmp_path / "flows")


def _saved(flows: FlowStore, **kw) -> FlowStore:
    flows.save(FlowRecord(id=FLOW_ID, name="measure", graph=_graph(**kw)))
    return flows


async def _night(hub, monkeypatch, flows, plan, *, sky, relay=True,
                 session=None, origin="flow", **kw) -> Night:
    night = Night(hub, monkeypatch, sky=sky, **kw)
    if relay:
        night.engine.layout_relay = functools.partial(relay_layout, flows)
    start = {"origin": origin, "origin_id": FLOW_ID}
    if session is not None:
        start["session"] = session
        start.pop("origin")
        start.pop("origin_id")
    try:
        night.done = await night.run(plan, wall_s=60.0, **start)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night


def _slews(night: Night) -> list[tuple[float, float]]:
    return [(e[2], e[3]) for e in night.trace if e[1] == "slew"]


def _expected_centres(rotation: float) -> list[tuple[float, float]]:
    """The four panel centres of the grid laid out at ``rotation``, in the
    grid's own order (the "Grid order" policy snakes the rows)."""
    spec = dict(ra_hours=ra_at(-2.0), dec_deg=DEC, rows=2, cols=2,
                overlap=0.25, rotation_deg=rotation, fov_x_deg=2.0,
                fov_y_deg=1.33)
    return [(round(p["ra_hours"], 6), round(p["dec_deg"], 6))
            for p in framing.compute_mosaic(spec)["panels"]]


def _stored_ids(flows: FlowStore) -> set[str]:
    plan = _plan_of(flows)
    return ({g.id for g in plan.groups} | {t.id for t in plan.targets}
            | {s.id for t in plan.targets for s in t.steps})


def _warnings(night: Night, needle: str) -> list[str]:
    return [m for _t, lvl, m in night.lines
            if lvl == "warning" and needle in m]


# ------------------------------------------------------------- the issue's

async def test_a_camera_fixed_at_37_lays_the_grid_out_at_37_and_sets_nothing_aside(
        group_hub, monkeypatch, flows):
    """THE ISSUE'S TEST. The block is laid out at 0 (nothing measured yet);
    the camera reads PA 37.0 on every solve. The first hop measures it, the
    grid is re-laid at 37 before any frame, and no panel is set aside for
    angle: every panel is shot at its 37-degree centre.

    MUTANT "the re-lay ignores the measurement" (``_relay_group`` writes the
    flow at the stored angle, so the layout stays at 0): the group's second
    check reads 37 against a layout of 0 and sets it aside, or the slews are
    the 0-degree centres. RED: the slews assertion."""
    _saved(flows, rotation=-1)
    plan = _plan_of(flows)
    assert plan.groups[0].pa_deg == 0.0
    night = await _night(group_hub, monkeypatch, flows, plan,
                         sky=lambda who, n: 37.0)
    assert night.done, night.trace[-3:]
    assert night.stored.set_aside == [], night.stored.set_aside
    assert not _warnings(night, "set aside for tonight"), night.lines
    centres = _expected_centres(37.0)
    slews = _slews(night)
    assert slews[0] == _expected_centres(0.0)[0]          # the measuring hop
    assert slews[1:5] == centres, (slews, centres)        # then the 37 grid
    for _t, _f in night.shots():
        assert _t.startswith("M31 ")
    assert len(night.shots()) == 4 * 2 * 2
    node = next(n for n in flows.get(FLOW_ID).graph.nodes if n.id == "t")
    assert node.params["rotation"] == 37.0
    group = night.stored.plan.groups[0]
    assert group.pa_deg == 37.0 and group.measured["pa_deg"] == 37.0
    assert group.measured["night"] == night_key(T0)
    assert group.measured["carried"] is False


async def test_night_two_finds_night_ones_frames_under_a_restarted_anchor(
        group_hub, monkeypatch, flows):
    """The ids the engine banked under tonight are the ids a compile of the
    saved flow makes: CONTINUE's replace report keeps every step and drops
    none. MUTANT "the engine keeps the ids it was compiled with": the first
    night's frames sit on ids the next compile does not make. RED:
    ``assert ... dropped == []``."""
    _saved(flows, rotation=-1)
    night = await _night(group_hub, monkeypatch, flows, _plan_of(flows),
                         sky=lambda who, n: 37.0)
    assert night.done
    assert {f.step_id for f in night.stored.frames} <= _stored_ids(flows)
    report = plan_replace_report(night.stored, _plan_of(flows))
    assert report.dropped == [] and report.new == []
    assert len(report.kept) == 8


# ------------------------------------------------------------- ruling 3

async def test_a_turn_under_the_threshold_keeps_every_id_and_says_it_carries(
        group_hub, monkeypatch, flows):
    """The counts started at 30; the camera reads 31. Under the carry
    threshold: the ids are the anchor's, so night one's frames count."""
    _saved(flows, rotation=30)
    plan = _plan_of(flows)
    before = {t.id for t in plan.targets}
    night = await _night(group_hub, monkeypatch, flows, plan,
                         sky=lambda who, n: 31.0)
    assert night.done and night.stored.set_aside == []
    assert {t.id for t in night.stored.plan.targets} == before
    assert _stored_ids(flows) >= before
    group = night.stored.plan.groups[0]
    assert group.measured["carried"] is True
    assert group.pa_deg == 31.0
    assert night.said("counts carry"), night.lines
    assert not _warnings(night, "counts restart")
    assert _slews(night)[1:5] == _expected_centres(31.0)


async def _banked(flows: FlowStore, plan) -> Session:
    """A dormant session of ``plan`` holding two accepted frames on each of
    its first two panels' first step: counts banked at the old layout."""
    session = Session(name=plan.name, created_ts=T0 - 86400.0,
                      status="dormant", plan=plan, origin="flow",
                      origin_id=FLOW_ID)
    for t in plan.targets[:2]:
        for _ in range(2):
            session.frames.append(SessionFrame(
                ts=T0 - 3600.0, night="n0", target_id=t.id,
                step_id=t.steps[0].id, auto_accepted=True))
    return session


async def test_a_turn_over_the_threshold_with_frames_banked_restarts_and_says_so(
        group_hub, monkeypatch, flows):
    """Four frames banked at PA 30; the camera now reads 37, past the
    threshold. The counts restart: the panels are new ids, the old frames
    stay in the ledger on steps no plan holds, and one warning says the
    turn, the limit and how many subs stay on disk.

    MUTANT "the alert is info" (or never said): the warning list is empty."""
    _saved(flows, rotation=30)
    plan = _plan_of(flows)
    old_ids = {t.id for t in plan.targets}
    session = await _banked(flows, plan)
    night = await _night(group_hub, monkeypatch, flows, plan,
                         sky=lambda who, n: 37.0, session=session)
    assert night.done and night.stored.set_aside == []
    new_ids = {t.id for t in night.stored.plan.targets}
    assert not old_ids & new_ids
    assert new_ids == {t.id for t in _plan_of(flows).targets}
    said = _warnings(night, "counts restart")
    assert len(said) == 1, night.lines
    assert "PA 37.0" in said[0] and "was 30.0" in said[0], said[0]
    assert "4 subs" in said[0] and "stay on disk" in said[0], said[0]
    assert len(night.shots()) == 4 * 2 * 2          # every panel from zero
    assert night.stored.owed() == 0
    group = night.stored.plan.groups[0]
    assert group.measured["carried"] is False
    assert group.measured["orphaned_frames"] == 4
    assert sum(1 for f in night.stored.frames
               if f.target_id in old_ids) == 4


async def test_the_restart_leaves_no_engine_state_on_an_old_id(
        group_hub, monkeypatch, flows):
    """Every id the engine held for the group before the relay is gone from
    everything it holds after it: the maps keyed by target, step or group id
    are re-keyed, or dropped. Scanned generically, so a map nobody thought of
    fails here. MUTANT "re-key only the group runs": the `_done` map (or
    another) still names an old id. RED: the leftover list."""
    _saved(flows, rotation=30)
    plan = _plan_of(flows)
    old = ({g.id for g in plan.groups} | {t.id for t in plan.targets}
           | {s.id for t in plan.targets for s in t.steps})
    session = await _banked(flows, plan)
    night = Night(group_hub, monkeypatch, sky=lambda who, n: 37.0)
    night.engine.layout_relay = functools.partial(relay_layout, flows)
    try:
        night.done = await night.run(plan, wall_s=60.0, session=session)
        left = _holders_of(night.engine, old)
    finally:
        await night.close()
    assert night.done
    assert left == [], left


#: Engine attributes the scan does not enter: the session (its ledger keeps the
#: old frames on old ids by design), the devices and the callbacks, and the
#: run's own tasks (their frames are not state).
_NOT_SCANNED = frozenset({"_session", "hub", "dispatcher", "layout_relay",
                          "_task", "_idle_task", "_thumb_tasks"})

#: Objects of these modules are machinery, not engine state, and are not
#: entered (an event loop, a lock, a logger).
_MACHINERY = ("asyncio", "_asyncio", "threading", "_thread", "logging",
              "concurrent", "weakref", "typing", "enum")


def _holders_of(obj, ids: set[str], *, path: str = "engine",
                seen: set[int] | None = None, depth: int = 0) -> list[str]:
    """Where in ``obj`` an id of ``ids`` still stands: a string (or an ``a:b``
    pair holding one), as a dict key or value, a set, frozenset, list, tuple
    or deque member, or an attribute of ANY object reachable from the engine
    that has a ``__dict__`` or dataclass fields, whatever module it is from.
    Only machinery (``_MACHINERY``), functions and classes are not entered,
    and the attributes in ``_NOT_SCANNED``."""
    import collections
    import dataclasses
    import types as pytypes
    seen = set() if seen is None else seen
    if isinstance(obj, str):
        return [path] if any(part in ids for part in obj.split(":")) else []
    if isinstance(obj, (int, float, bool, type(None), bytes, type,
                        pytypes.FunctionType, pytypes.MethodType,
                        pytypes.BuiltinFunctionType, pytypes.ModuleType)):
        return []
    if depth > 10 or id(obj) in seen:
        return []
    seen.add(id(obj))
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out += _holders_of(k, ids, path=f"{path}[key]", seen=seen,
                               depth=depth + 1)
            out += _holders_of(v, ids, path=f"{path}[{k!r}]", seen=seen,
                               depth=depth + 1)
    elif isinstance(obj, (list, tuple, set, frozenset, collections.deque)):
        for v in obj:
            out += _holders_of(v, ids, path=f"{path}[]", seen=seen,
                               depth=depth + 1)
    elif type(obj).__module__.split(".")[0] in _MACHINERY:
        return []
    else:
        fields = {}
        if hasattr(obj, "__dict__"):
            fields.update(vars(obj))
        if dataclasses.is_dataclass(obj):
            for f in dataclasses.fields(obj):
                if hasattr(obj, f.name):
                    fields.setdefault(f.name, getattr(obj, f.name))
        for k, v in fields.items():
            if path == "engine" and k in _NOT_SCANNED:
                continue
            out += _holders_of(v, ids, path=f"{path}.{k}", seen=seen,
                               depth=depth + 1)
    return out


async def test_no_frame_banked_means_a_silent_restart_and_new_ids(
        group_hub, monkeypatch, flows):
    """The first night of a new measure block: the layout moves from the
    provisional 0 to the measured 37, past the threshold, but nothing was
    ever shot, so nothing restarts and nobody is told it does. MUTANT "always
    alert": a warning on a restart that lost nothing."""
    _saved(flows, rotation=30)
    plan = _plan_of(flows)
    night = await _night(group_hub, monkeypatch, flows, plan,
                         sky=lambda who, n: 37.0)
    assert night.done and night.stored.set_aside == []
    assert not _warnings(night, "counts restart"), night.lines
    assert night.said("nothing had been shot"), night.lines
    assert night.stored.plan.groups[0].measured["orphaned_frames"] == 0


# ---------------------------------------------------------- the failure paths

async def test_a_mid_night_bump_after_the_measurement_still_sets_the_group_aside(
        group_hub, monkeypatch, flows):
    """The relay is the first thing a night does; after it the group is a
    fixed camera at its measured angle. A later hop that reads 50 is a bump,
    not a measurement: the group is set aside, with the existing words."""
    _saved(flows, rotation=-1)

    def sky(who, n):
        return 50.0 if who.endswith("1-2") else 37.0

    night = await _night(group_hub, monkeypatch, flows, _plan_of(flows),
                         sky=sky)
    assert night.done
    alerts = _warnings(night, "set aside for tonight")
    assert len(alerts) == 1, night.lines
    assert "turn the camera or re-frame at the measured angle" in alerts[0]
    assert {r["target_id"] for r in night.stored.set_aside}


async def test_a_flow_that_cannot_be_written_sets_the_group_aside_in_words(
        group_hub, monkeypatch, flows):
    """The flow was deleted during the night: nothing can be laid out, so no
    panel is shot, and the alert says the measured angle and why. MUTANT
    "shoot on the old layout anyway": frames are shot at 0."""
    _saved(flows, rotation=-1)
    plan = _plan_of(flows)
    flows.delete(FLOW_ID)
    night = await _night(group_hub, monkeypatch, flows, plan,
                         sky=lambda who, n: 37.0)
    assert night.done and night.shots() == []
    alerts = _warnings(night, "set aside for tonight")
    assert len(alerts) == 1, night.lines
    assert "PA 37.0" in alerts[0] and "no longer exists" in alerts[0], alerts


async def test_an_unexpected_failure_names_its_type_and_never_its_text(
        group_hub, monkeypatch, flows):
    _saved(flows, rotation=-1)

    def boom(*a, **k):
        raise OSError("C:/secret/path/flows/f.json is locked")

    night = Night(group_hub, monkeypatch, sky=lambda who, n: 37.0)
    night.engine.layout_relay = boom
    try:
        night.done = await night.run(_plan_of(flows), wall_s=60.0,
                                     origin="flow", origin_id=FLOW_ID)
    finally:
        await night.close()
    assert night.done and night.shots() == []
    alerts = _warnings(night, "set aside for tonight")
    assert len(alerts) == 1 and "OSError" in alerts[0], night.lines
    assert "secret" not in alerts[0] and "locked" not in alerts[0]


async def test_a_plan_with_no_flow_behind_it_is_set_aside_saying_so(
        group_hub, monkeypatch, flows):
    """A plan posted straight to /api/sequence/start with the group's
    ``measure_angle`` set has no flow to write the angle to. Never a crash:
    the group is set aside and the alert says the mode needs a saved flow."""
    _saved(flows, rotation=-1)
    night = await _night(group_hub, monkeypatch, flows, _plan_of(flows),
                         sky=lambda who, n: 37.0, origin="", relay=False)
    assert night.done and night.shots() == []
    alerts = _warnings(night, "set aside for tonight")
    assert len(alerts) == 1, night.lines
    assert "needs a saved flow" in alerts[0], alerts


async def test_a_lane_edited_during_the_night_is_a_mismatch_and_sets_aside(
        group_hub, monkeypatch, flows):
    """The plan the engine runs is frozen; a compile of the flow as saved now
    has other steps. Adopting it would bank frames under steps the run never
    planned: refused, in words, nothing shot."""
    _saved(flows, rotation=-1)
    plan = _plan_of(flows)
    flows.save(FlowRecord(id=FLOW_ID, name="measure",
                          graph=_graph(rotation=-1, lane="L 45, R 30")))
    night = await _night(group_hub, monkeypatch, flows, plan,
                         sky=lambda who, n: 37.0)
    assert night.done and night.shots() == []
    alerts = _warnings(night, "set aside for tonight")
    assert len(alerts) == 1 and "edited since this run started" in alerts[0], (
        night.lines)


async def test_no_solve_measures_nothing_and_the_panel_waits_a_pass(
        group_hub, monkeypatch, flows):
    """The first hop's centring left no sky angle: tiles are never laid
    blind, so the panel is deferred, never shot and never relaid on nothing.
    The next hop that measures one relays the grid."""
    _saved(flows, rotation=-1)

    def sky(who, n):
        return None if (who.endswith("1-1") and n == 1) else 37.0

    night = await _night(group_hub, monkeypatch, flows, _plan_of(flows),
                         sky=sky)
    assert night.done
    first = night.shots()[0][0]
    assert not first.endswith("1-1") or len(_slews(night)) > 1
    assert night.stored.set_aside == []
    assert night.stored.plan.groups[0].pa_deg == 37.0


async def test_a_same_night_restart_does_not_measure_or_relay_again(
        group_hub, monkeypatch, flows):
    """The plan the session froze says it was measured tonight, so the
    restart makes ordinary hops, one goto a visit, no second relay. MUTANT
    "relay every run": the second run's first hop is a measuring hop again
    and the relay callback is called."""
    _saved(flows, rotation=-1)
    plan = _plan_of(flows)
    plan.groups[0].measured = {"pa_deg": 0.0, "from_pa_deg": 0.0,
                               "night": night_key(T0), "carried": True,
                               "max_move_deg": 0.0, "threshold_deg": 0.1,
                               "reason": "unchanged", "orphaned_frames": 0}
    calls = []

    def relay(*a, **k):
        calls.append(a)
        raise AssertionError("a same-night run must not relay again")

    night = Night(group_hub, monkeypatch, sky=lambda who, n: 0.0)
    night.engine.layout_relay = relay
    try:
        night.done = await night.run(plan, wall_s=60.0, origin="flow",
                                     origin_id=FLOW_ID)
    finally:
        await night.close()
    assert night.done and calls == []
    assert len(night.shots()) == 4 * 2 * 2


# ------------------------------------------- the relay's save is not an edit

async def test_the_relays_own_save_does_not_make_the_session_look_stale(
        group_hub, monkeypatch, flows):
    """The relay saves the flow, which moves its ``updated_ts``; the editor
    says "the armed session will replay the version from <date>" whenever the
    record is newer than the version the session froze (`replayNotice`). The
    session's frozen time follows the relay's save when it was the saved
    version before it, and stays behind an operator's edit when it was not.
    MUTANT "no sync": the stored time stays behind the record's. RED:
    ``assert 1788313.0 == ...``."""
    _saved(flows, rotation=30)
    plan = _plan_of(flows)
    frozen = flows.get(FLOW_ID).updated_ts
    session = Session(name=plan.name, created_ts=T0 - 86400.0,
                      status="dormant", plan=plan, origin="flow",
                      origin_id=FLOW_ID, plan_saved_ts=frozen)
    night = await _night(group_hub, monkeypatch, flows, plan,
                         sky=lambda who, n: 31.0, session=session)
    assert night.done
    assert night.stored.plan_saved_ts == flows.get(FLOW_ID).updated_ts
    assert night.stored.plan_saved_ts > frozen


async def test_a_session_already_behind_an_operators_edit_stays_behind_it(
        group_hub, monkeypatch, flows):
    _saved(flows, rotation=30)
    plan = _plan_of(flows)
    frozen = flows.get(FLOW_ID).updated_ts - 3600.0     # the operator saved since
    session = Session(name=plan.name, created_ts=T0 - 86400.0,
                      status="dormant", plan=plan, origin="flow",
                      origin_id=FLOW_ID, plan_saved_ts=frozen)
    night = await _night(group_hub, monkeypatch, flows, plan,
                         sky=lambda who, n: 31.0, session=session)
    assert night.done
    assert night.stored.plan_saved_ts == frozen


def test_the_leftover_scan_finds_an_id_in_every_shape_that_can_hold_one():
    """The scan is the guard on the re-key, so it is itself tried: a planted id
    is found in a deque, a frozenset, a tuple key, an object's attribute, a
    dataclass's field and an "a:b" pair, and a clean object has none."""
    import collections
    import dataclasses

    @dataclasses.dataclass(frozen=True)
    class Held:
        who: str

    class Plain:
        def __init__(self, x):
            self.x = x

    old = {"OLD"}
    for what, holder in [
            ("deque", collections.deque(["OLD"])),
            ("frozenset", frozenset({"OLD"})),
            ("tuple key", {("OLD", 1): 1}),
            ("attribute", Plain("OLD")),
            ("dataclass", Held("OLD")),
            ("nested object", Plain(Plain([Held("x:OLD")]))),
            ("pair", {"OLD:other": 1})]:
        assert _holders_of(holder, old), what
    assert _holders_of(Plain({"a": collections.deque(["NEW"])}), old) == []
