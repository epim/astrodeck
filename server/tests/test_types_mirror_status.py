# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""``ui/src/types.ts`` mirrors a mosaic panel and the status frame's sky angle
(#174, spec 3.7's last bullet, I-25).

Two payloads reached the UI with nothing comparing them to its types:

* A panel of ``POST /api/framing/mosaic``. The route has put
  ``transit_alt_error`` on a panel it could not find an altitude for since
  the silent-panel fix, and ``MosaicPanel`` never gained it, so
  ``mosaicNightSummary.ts`` declared the field itself and said so in a
  comment. It now reads ``MosaicPanel``'s own (``PanelNight`` is a ``Pick``
  of it), so this file is what keeps that field honest.
* ``status.sky_angle``, the record ``sky_angle.note_solved_rotation`` stores
  on ``hub.last_sky_angle`` and ``poll_status`` publishes. Nothing in
  ``ui/src`` typed it, and the mosaic modal's USE MEASURED chip (spec 2.4)
  has to read it.

Both are compared with what the server really sends, both ways round, in the
style of test_types_mirror_groups.py (whose parser and JSON-type check this
file imports; the parser's own guards live there): a key the server sends
that types.ts lacks is data no screen can read, and a key types.ts declares
that the server never sends reads undefined for ever. Neither payload is a
pydantic model, so the server side is always a real answer, never a list
copied here:

* the panels of three real route answers, one for each shape a panel takes
  (no night asked for, an altitude, and a stated reason why there is none),
  so a key present in only some of them is exactly a key types.ts must mark
  optional;
* two real records off the status frame, from the centring solve
  (``Hub.solve_and_sync``) on the sim rig, one that calibrated the rotator
  and one taken with no rotator connected on a mount that cannot say its
  pier side, so every field the record can carry as null is seen null once
  and seen as a value once.

S5 (#189, U-07) added two more, held the same way, and both had drifted
before it (#431):

* ``SequenceProgress``, the ``progress`` of a published sequence state,
  against every ``progress`` a clocked-simulator night published
  (``_group_harness``) and every answer ``compute_eta`` gave on it: the
  finish clock's keys, ``hops_costed`` among them, are exactly the optional
  ones, and the null ``frame_started_at_ms`` carries between frames is
  admitted.
* flowsApi.ts's ``FlowProgressBlock`` and ``FlowProgressPanel``, against a
  real ``flow_progress`` answer holding a locked single target, a pool and
  a mosaic: ``group_id``, ``grid``, ``skipped`` and ``locked_angle`` are
  exactly the optional ones.

S7 (#473, #430; S7 orchestrator rulings 1 and 7) added a third, held the same
way (#431's "worth the same test when next touched"):

* flowsApi.ts's ``FlowProgressSession``, against the progress ROUTE's own
  answers (the route, not ``flow_progress``, adds ``armed`` and
  ``plan_saved_ts``): a live session, a dormant armed one frozen at a saved
  version, and a dormant armed one older than S7 whose ``plan_saved_ts`` is
  null, so ``armed`` is seen both ways and ``plan_saved_ts`` as a number and
  as null.

Every test that guards a branch names the mutant it kills and quotes the
failure that mutant produced, observed in a private copy of the tree (a copy
of ``server/`` beside a copy of ``ui/src/types.ts``), from a byte-for-byte
backup of the file under test, with the copy's sha256 compared against the
backup afterwards.
"""
from __future__ import annotations

import json
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from _group_harness import (Night, grid_plan, group_hub,  # noqa: F401
                            group_store)
from _simhub import sim_hub  # noqa: F401 (fixture import)
from astrodeck.catalog import framing
from astrodeck.devices.base import DeviceError
from test_flows_continue import LR, _compiled, rig  # noqa: F401 (fixture)
from test_types_mirror_groups import (TYPES_TS, _interface, _mask_strings,
                                      _optional, _ts_admits)

pytestmark = pytest.mark.skipif(
    not TYPES_TS.exists(), reason="ui/ not present (server-only checkout)")

#: A 1x2 at the fixture's M31 centre: two panels, so each answer has more
#: than one panel to disagree about, and cheap enough to ask for tonight.
SPEC = {"ra_hours": 0.71, "dec_deg": 41.27, "rows": 1, "cols": 2,
        "overlap": 0.2, "rotation_deg": 0.0,
        "fov_x_deg": 1.2, "fov_y_deg": 0.8}


def _drift(what: str, sent: set[str], declared: set[str]) -> str:
    return (f"{what} drifted: the server sends {sorted(sent - declared)}, "
            f"which types.ts does not declare, and types.ts declares "
            f"{sorted(declared - sent)}, which the server never sends")


def _wrong(record: dict, ts: dict[str, str]) -> dict:
    """The members of ``record`` whose value its TS type does not admit."""
    return {k: (v, ts[k]) for k, v in record.items()
            if k in ts and not _ts_admits(v, ts[k])}


def _words(ts_type: str) -> set[str]:
    return set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", _mask_strings(ts_type)))


# ------------------------------------------------------------ MosaicPanel

@pytest.fixture
def client(monkeypatch):
    """The route alone, on a saved site overlaid on whatever the store holds
    (test_framing.py's fixture, and its reason: the visibility module refuses
    the 0,0 default since #24). 40 N 74 W, and it is not anybody's rig."""
    from astrodeck.hub import Hub
    real = Hub.site
    monkeypatch.setattr(Hub, "site", property(lambda self: {
        **real.fget(self), "latitude": 40.0, "longitude": -74.0,
        "is_default": False}))
    app = FastAPI()
    app.include_router(framing.router)
    with TestClient(app) as c:
        yield c


def _panels(client, **extra) -> list[dict]:
    r = client.post("/api/framing/mosaic", json={**SPEC, **extra})
    assert r.status_code == 200, r.text
    return r.json()["panels"]


def test_the_mosaic_panel_type_is_what_the_route_answers(client, monkeypatch):
    """Every key a real panel carries is declared on ``MosaicPanel`` and
    every declared key is carried by some real panel; the keys only some
    panels carry are exactly the ones types.ts marks optional; and every
    value is one its TS type admits.

    RED against types.ts as it was at 812fcf9e (``MosaicPanel`` without the
    field), and under the named mutation "drop transit_alt_error from
    types.ts" (its line deleted from ``MosaicPanel`` in a scratch copy of
    ui/src/types.ts), observed:

        E   AssertionError: MosaicPanel drifted: the server sends
            ['transit_alt_error'], which types.ts does not declare, and
            types.ts declares [], which the server never sends

    RED under the named mutation "add a TS field the server never sends"
    (``transit_alt_at?: number;`` added to ``MosaicPanel``), observed:

        E   AssertionError: MosaicPanel drifted: the server sends [], which
            types.ts does not declare, and types.ts declares
            ['transit_alt_at'], which the server never sends

    RED under "transit_alt_error required in types.ts" (``transit_alt_error:
    string;``), observed:

        E   AssertionError: types.ts must mark optional exactly the keys a
            panel sends only sometimes
        E   assert {'transit_alt'} == {'transit_alt...it_alt_error'}
        E     Extra items in the right set:
        E     'transit_alt_error'

    RED under "transit_alt_error typed number in types.ts", observed:

        E   AssertionError: types.ts types these otherwise:
            {'transit_alt_error': ('OSError: ephemeris table unreadable',
            'number')}

    The premise, RED under the route mutant "no reason on a lost panel"
    (``_stamp_transit_alt`` leaving ``transit_alt_error`` off, in a scratch
    copy of server/): the three shapes must really be three, or the
    comparison grades fewer than the route sends. Observed:

        E   AssertionError: premise: a panel the route could not answer for
            carries its reason: [{'row': 0, 'col': 0, 'ra_hours':
            0.6674264689814583, 'dec_deg': 41.268235608920776,
            'rotation_deg': 0.0}, {'row': 0, 'col': 1, ...}]
    """
    plain = _panels(client)
    tonight = _panels(client, transit_alt=True)

    from astrodeck.catalog import visibility

    def _unreadable(ra_hours, dec_deg, *, date=None, site=None):
        raise OSError("ephemeris table unreadable")

    monkeypatch.setattr(visibility, "transit_alt_for", _unreadable)
    lost = _panels(client, transit_alt=True)

    assert plain and all("transit_alt" not in p and "transit_alt_error"
                         not in p for p in plain), (
        f"premise: a mosaic nobody asked a night about carries neither: "
        f"{plain}")
    assert tonight and all(isinstance(p.get("transit_alt"), float)
                           for p in tonight), (
        f"premise: tonight's panels carry an altitude: {tonight}")
    assert lost and all(isinstance(p.get("transit_alt_error"), str)
                        for p in lost), (
        f"premise: a panel the route could not answer for carries its "
        f"reason: {lost}")

    panels = plain + tonight + lost
    sent = set().union(*map(set, panels))
    always = set.intersection(*map(set, panels))
    ts = _interface("MosaicPanel")
    assert sent == set(ts), _drift("MosaicPanel", sent, set(ts))
    assert _optional("MosaicPanel") == sent - always, (
        "types.ts must mark optional exactly the keys a panel sends only "
        "sometimes")
    for p in panels:
        wrong = _wrong(p, ts)
        assert not wrong, f"types.ts types these otherwise: {wrong}"


# ------------------------------------------------------- status.sky_angle

async def _status_record(hub) -> dict | None:
    """``sky_angle`` off the status frame, as JSON carries it to the UI."""
    status = await hub.poll_status()
    assert "sky_angle" in status, "premise: the status frame names sky_angle"
    return json.loads(json.dumps(status["sky_angle"]))


async def test_the_sky_angle_type_is_the_record_the_status_frame_carries(
        sim_hub, monkeypatch):
    """Two real records off the status frame, each with exactly the keys of
    types.ts's ``SkyAngleRecord`` both ways round, none of them optional (the
    recorder writes every key every time, null where it has no value), and
    every value one its TS type admits. The first calibrated the rotator on
    a mount that reported its side; the second was taken with no rotator
    connected, on a mount that cannot say which side it is on (a fork, or a
    link that stopped answering), so every field that can be null is null in
    one record and a value in the other, and a field typed without ``| null``
    goes red on one of the two.

    RED against types.ts as it was at 812fcf9e, observed:

        E   AssertionError: types.ts has no 'export interface SkyAngleRecord'

    RED under "drop a key from the TS record" (``mechanical_deg`` deleted
    from ``SkyAngleRecord`` in a scratch copy of ui/src/types.ts), observed:

        E   AssertionError: SkyAngleRecord drifted: the server sends
            ['mechanical_deg'], which types.ts does not declare, and types.ts
            declares [], which the server never sends

    RED under "add a TS field the hub never sends" (``fresh: boolean;``
    added to ``SkyAngleRecord``), observed:

        E   AssertionError: SkyAngleRecord drifted: the server sends [], which
            types.ts does not declare, and types.ts declares ['fresh'], which
            the server never sends

    RED under "offset_deg not nullable in types.ts" (``offset_deg:
    number;``), observed:

        E   AssertionError: types.ts types these otherwise: {'offset_deg':
            (None, 'number')}

    RED under "pier_side not nullable in types.ts" (``pier_side: "east" |
    "west";``), which only the second record can catch (the sim's mount
    reports east on the first), observed:

        E   AssertionError: types.ts types these otherwise: {'pier_side':
            (None, '"east" | "west"')}

    RED under "reason optional in types.ts" (``reason?: string | null;``),
    observed:

        E   AssertionError: every key of the record is always sent
        E   assert {'reason'} == set()

    RED under the engine mutant "the record gains a key" (``"night": None``
    added to the dict ``note_solved_rotation`` builds, in a scratch copy of
    server/), observed:

        E   AssertionError: SkyAngleRecord drifted: the server sends
            ['night'], which types.ts does not declare, and types.ts declares
            [], which the server never sends
    """
    rot = sim_hub.require("rotator")
    await sim_hub.solve_and_sync(0.05)
    calibrated = await _status_record(sim_hub)

    await rot.disconnect()
    tel = sim_hub.require("telescope")

    async def no_side():
        raise DeviceError("this mount reports no pier side")

    monkeypatch.setattr(tel, "pier_side", no_side)
    # And no earlier answer to repeat: the cache `pier_side_cached` reads is
    # what a solve with no rotator falls back to (sky_angle._pier_side).
    monkeypatch.setattr(sim_hub, "_pier_side_seen", None)
    await sim_hub.solve_and_sync(0.05)
    bare = await _status_record(sim_hub)

    assert calibrated and calibrated["calibrated"] is True and isinstance(
        calibrated["pier_side"], str), (
        f"premise: the first solve calibrated the rotator on a mount that "
        f"reported its side: {calibrated}")
    assert bare and bare["calibrated"] is False and all(
        bare[k] is None for k in ("pier_side", "mechanical_deg",
                                  "rotator_before_deg", "offset_deg")), (
        f"premise: the second solve had no rotator to calibrate and no pier "
        f"side: {bare}")

    ts = _interface("SkyAngleRecord")
    for record in (calibrated, bare):
        assert set(record) == set(ts), _drift("SkyAngleRecord", set(record),
                                              set(ts))
        wrong = _wrong(record, ts)
        assert not wrong, f"types.ts types these otherwise: {wrong}"
    assert _optional("SkyAngleRecord") == set(), (
        "every key of the record is always sent")


async def test_the_status_type_carries_the_record_and_its_null(sim_hub):
    """``RigStatus.sky_angle`` is the record type, and admits the null the
    frame carries until the first solve of the night.

    RED against types.ts as it was at 812fcf9e, observed:

        E   KeyError: 'sky_angle'

    RED under "sky_angle not nullable in types.ts" (``sky_angle?:
    SkyAngleRecord;`` on ``RigStatus``), observed:

        E   AssertionError: the status frame carries sky_angle None before
            any solve, and types.ts says 'SkyAngleRecord'
    """
    before = await _status_record(sim_hub)
    assert before is None, f"premise: no solve yet, no record: {before}"
    member = _interface("RigStatus")["sky_angle"]
    assert "SkyAngleRecord" in _words(member), member
    assert _ts_admits(before, member), (
        f"the status frame carries sky_angle None before any solve, and "
        f"types.ts says {member!r}")


# ------------------------------------------------ SequenceProgress (S5, U-07)

def _eta_night_plan():
    """The fixture 2x2 of L and R, one frame a filter: short, and it ends,
    so the finish clock is asked with hops still to make and with none."""
    return grid_plan(panel_kw={"count": 1})


async def _progress_of_a_night(hub, monkeypatch) -> tuple[list[dict],
                                                          list[dict]]:
    """``(published, eta)``: every ``progress`` the engine published over one
    clocked night (``_group_harness``), as JSON carries it, and every answer
    ``compute_eta`` itself gave, asked of the live engine at each exposure.

    Real answers, never a list copied here: ``progress`` is a dict
    `_set_state` builds, with `compute_eta`'s answer merged in only while a
    run is live, so no model says what its keys are."""
    night = Night(hub, monkeypatch)
    eta: list[dict] = []
    night.on_capture = lambda rec: eta.append(
        json.loads(json.dumps(night.engine.compute_eta())))
    try:
        done = await night.run(_eta_night_plan())
    finally:
        await night.close()
    assert done, f"premise: the night ended: {night.trace[-3:]}"
    published = [json.loads(json.dumps(e["data"]["progress"]))
                 for e in night.events if e["type"] == "sequence"
                 and isinstance(e["data"].get("progress"), dict)]
    return published, eta


async def test_the_progress_type_is_what_the_engine_publishes(
        group_hub, monkeypatch):
    """Every ``progress`` key the engine publishes is declared on
    ``SequenceProgress`` and every declared key is published; the keys
    `compute_eta` answers are exactly the ones types.ts marks optional (the
    finish clock rides only a live run's publishes, so a terminal one has
    none of them); and every value is one its TS type admits.
    ``hops_costed`` is seen both ways (hops still to make and none measured,
    then no hop left to cost), so a type that admitted only one would fail.

    RED against types.ts as it was before S5, observed:

        E   AssertionError: SequenceProgress drifted: the engine publishes
            ['hops_costed'], which types.ts does not declare, and types.ts
            declares [], which the engine never publishes

    Each mutant below was run in scratchpad ``s5-feed-mut`` (a copy of
    ``server/`` beside a copy of ui/src/types.ts), from a byte backup of the
    file it changed, restored and hash-compared after.

    RED under "drop hops_costed from types.ts" (its line deleted from
    ``SequenceProgress``), observed: the message above, verbatim.

    RED under "hops_costed required in types.ts" (``hops_costed:
    boolean;``), observed:

        E   AssertionError: types.ts must mark optional exactly the keys the
            finish clock adds
            Extra items in the right set:
            'hops_costed'

    RED under "hops_costed typed number in types.ts", observed:

        E   AssertionError: types.ts types these otherwise: {'hops_costed':
            (False, 'number')}

    RED under "frame_started_at_ms not nullable" (``frame_started_at_ms?:
    number;``, the type before S5, which the engine's null between frames
    never matched), observed:

        E   AssertionError: types.ts types these otherwise:
            {'frame_started_at_ms': (None, 'number')}

    RED under the engine mutant "the finish clock gains a key"
    (``compute_eta`` answering ``"hops_s"`` as well), observed:

        E   AssertionError: SequenceProgress drifted: the engine publishes
            ['hops_s'], which types.ts does not declare, and types.ts
            declares [], which the engine never publishes
    """
    from astrodeck.sequence import SequenceEngine
    assert SequenceEngine(group_hub).compute_eta() == {}, (
        "premise: with no plan the finish clock answers nothing, so its keys "
        "can be absent")
    published, eta = await _progress_of_a_night(group_hub, monkeypatch)
    live = [p for p in published if "eta_s" in p]
    assert live, "premise: the night published live progress"
    costed = {p["hops_costed"] for p in live}
    assert costed == {True, False}, (
        f"premise: hops_costed was seen both ways: {costed}")
    assert any(p.get("frame_started_at_ms") is None for p in live) and any(
        p.get("frame_started_at_ms") is not None for p in live), (
        "premise: frame_started_at_ms was seen null and set")

    ts = _interface("SequenceProgress")
    sent = set().union(*map(set, published))
    assert sent == set(ts), (
        f"SequenceProgress drifted: the engine publishes "
        f"{sorted(sent - set(ts))}, which types.ts does not declare, and "
        f"types.ts declares {sorted(set(ts) - sent)}, which the engine "
        f"never publishes")
    clock = set().union(*map(set, eta))
    assert clock and clock <= sent, (
        f"premise: compute_eta's keys are published: {sorted(clock)}")
    assert _optional("SequenceProgress") == clock, (
        "types.ts must mark optional exactly the keys the finish clock adds")
    for p in published:
        wrong = _wrong(p, ts)
        assert not wrong, f"types.ts types these otherwise: {wrong}"


# ------------------------------------------ FlowProgressBlock (flowsApi.ts)

FLOWS_API_TS = TYPES_TS.parent / "lib" / "flowsApi.ts"


def _flows_api(name: str) -> dict[str, str]:
    return _interface(name, FLOWS_API_TS.read_text(encoding="utf-8"))


def _flows_api_optional(name: str) -> set[str]:
    return _optional(name, FLOWS_API_TS.read_text(encoding="utf-8"))


def _progress_blocks() -> list[dict]:
    """The blocks of one real ``flow_progress`` answer with every shape a
    block takes: a single TARGET locked to an angle, a POOL, and a 2x2
    mosaic with a panel skipped. JSON-rendered, as the route serves it."""
    from astrodeck.flows.compile import compile_plan
    from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
    from astrodeck.flows.progress import flow_progress
    from astrodeck.flows.to_plan import to_sequence_plan
    from astrodeck.sequence.session import Session

    def n(nid, ntype, x, **params):
        return FlowNode(id=nid, type=ntype, x=x, y=0.0, params=params)

    def e(a, ap, b, bp):
        return FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})

    graph = FlowGraph(
        nodes=[n("t", "target", 0, name="M31", ra="00h 42m 44s",
                 dec="+41 16 09", rotation=-1),
               n("c", "capture", 50, filter="L", exposure=60, gain=100,
                 bin="1", count=5, goal=0),
               n("p", "pool", 100, members="M42, M13", minAlt=0, moonSep=0,
                 maxHA=0),
               n("q", "capture", 150, filter="L", exposure=60, gain=100,
                 bin="1", count=5, goal=0),
               n("m", "target", 200, name="M16", ra="18h 18m 48s",
                 dec="-13 49 00", rotation=30, angle="Rotate to PA", rows=2,
                 cols=2, overlap=25, fovX=2.0, fovY=1.33, skip="2-2"),
               n("k", "capture", 250, filter="Ha", exposure=300, gain=100,
                 bin="1", count=2, goal=0)],
        edges=[e("t", "target", "c", "run"), e("c", "complete", "p", "arm"),
               e("p", "target", "q", "run"), e("q", "complete", "m", "arm"),
               e("m", "target", "k", "run")])
    flow = "flow-mirror-progress"
    compiled = compile_plan(graph, "n")
    plan, _ = to_sequence_plan(compiled, graph, flow_id=flow)
    single = plan.targets[0]
    session = Session(id="s", status="dormant", nights=["n1"],
                      plan=plan, frames=[], origin="flow", origin_id=flow,
                      locked_angles={single.id: {
                          "pa_deg": 12.5, "solved_at": 1.0,
                          "exposed_at": 1.0, "source": "plate solve"}})
    got = flow_progress(compiled, plan, session, flow_id=flow)
    return json.loads(json.dumps(got))["blocks"]


def test_the_progress_block_type_is_what_the_route_answers():
    """Every key a real block carries is declared on flowsApi.ts's
    ``FlowProgressBlock``, and every declared key is carried by some block;
    the keys only some blocks carry (a mosaic's ``grid``, ``skipped`` and
    ``group_id``, a lock's ``locked_angle``) are exactly the ones marked
    optional; and every value is one its TS type admits, ``group_id``'s
    string included. The same for a block's panels (``FlowProgressPanel``).

    RED against flowsApi.ts as it was before S5 (no ``group_id``, and no
    ``locked_angle`` on either type, though the route has sent it since the
    locked-angle work), observed with the shared ``_drift`` wording this
    test used then:

        E   AssertionError: FlowProgressBlock drifted: the server sends
            ['group_id', 'locked_angle'], which types.ts does not declare,
            and types.ts declares [], which the server never sends

    Each mutant below was run in scratchpad ``s5-feed-mut`` on a copy of
    ui/src/lib/flowsApi.ts, from a byte backup, restored and hash-compared.

    RED under "drop group_id from flowsApi.ts", observed:

        E   AssertionError: FlowProgressBlock drifted: the route sends
            ['group_id'], which flowsApi.ts does not declare, and
            flowsApi.ts declares [], which the route never sends

    RED under "group_id required" (``group_id: string | null;``),
    observed:

        E   AssertionError: flowsApi.ts must mark optional exactly the keys
            a FlowProgressBlock carries only sometimes
            Extra items in the right set:
            'group_id'

    RED under "group_id typed number" (``group_id?: number | null;``),
    observed:

        E   AssertionError: flowsApi.ts types these otherwise: {'group_id':
            ('35c3487368d45f18b739bf637d515e93', 'number | null')}

    RED under "drop the panel's locked_angle" (re-run with this test's
    current wording in scratchpad ``s5-feed-verify-mut``), observed:

        E   AssertionError: FlowProgressPanel drifted: the route sends
            ['locked_angle'], which flowsApi.ts does not declare, and
            flowsApi.ts declares [], which the route never sends

    The null ``group_id`` of a block whose every panel is skipped is graded by
    test_flows_progress_mosaic.py; every block here has a group.
    """
    blocks = _progress_blocks()
    kinds = [(b["kind"], "grid" in b, "locked_angle" in b) for b in blocks]
    assert kinds == [("target", False, True), ("pool", False, False),
                     ("target", True, False)], (
        f"premise: a locked single target, a pool and a mosaic: {kinds}")

    for what, records in (("FlowProgressBlock", blocks),
                          ("FlowProgressPanel",
                           [p for b in blocks for p in b["panels"]])):
        ts = _flows_api(what)
        sent = set().union(*map(set, records))
        always = set.intersection(*map(set, records))
        assert sent == set(ts), (
            f"{what} drifted: the route sends {sorted(sent - set(ts))}, "
            f"which flowsApi.ts does not declare, and flowsApi.ts declares "
            f"{sorted(set(ts) - sent)}, which the route never sends")
        assert _flows_api_optional(what) == sent - always, (
            f"flowsApi.ts must mark optional exactly the keys a {what} "
            f"carries only sometimes")
        for r in records:
            wrong = _wrong(r, ts)
            assert not wrong, f"flowsApi.ts types these otherwise: {wrong}"


# ---------------------------------------- FlowProgressSession (flowsApi.ts)

async def _route_sessions(rig) -> list[dict]:
    """Three ``session`` records the progress ROUTE answers, JSON as served:
    a live run's (``armed`` false: ``engine.start`` arms every run, and a
    live session is not one auto-resume will start), the same session
    dormant after an incomplete night (armed, frozen at the flow's saved
    time), and a dormant armed session written before S7, whose file has no
    ``plan_saved_ts`` (null: the route never guesses a time). The keys the
    route adds (``replay_facts``) are exactly what ``flow_progress`` alone
    does not answer, so only the route can be the other side of this."""
    from astrodeck.persist import write_json_atomic
    from astrodeck.sequence.session import Session, session_store

    async def session_of(fid: str) -> dict:
        r = await rig.client.get(f"/api/flows/{fid}/progress")
        assert r.status_code == 200, r.text
        return json.loads(r.content)["session"]

    fid = await rig.save_flow(LR)
    r = await rig.run(fid)
    assert r.status_code == 200, r.text
    live = await session_of(fid)
    await rig.end_night("incomplete")
    dormant = await session_of(fid)

    old = await rig.save_flow(LR, name="saved before S7")
    body = Session(name="saved before S7", created_ts=100.0, updated_ts=100.0,
                   status="dormant", plan=_compiled(LR, old),
                   auto_resume=True, origin="flow",
                   origin_id=old).model_dump()
    body.pop("plan_saved_ts", None)
    path = session_store._path(body["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, body, backup=False)
    before = await session_of(old)
    return [live, dormant, before]


async def test_the_progress_session_type_is_what_the_route_answers(rig):
    """Every key a real ``session`` of the progress route carries is declared
    on flowsApi.ts's ``FlowProgressSession``, and every declared key is
    carried; every value is one its TS type admits: ``armed`` true and
    false, ``plan_saved_ts`` a number and null, and the status choices
    (#431, #473).

    OPTIONAL EXACTLY THE REPLAY FACTS. This route sends all six keys on
    every record, but a server older than S7 sends neither of the two the
    route adds (``progress.replay_facts``), and the UI must read that answer
    too, so flowsApi.ts marks those two optional, the way it marks
    ``FlowRunResult.session`` for a server older than S1. The optional set
    is held to the keys the carried-sometimes rule gives plus the keys
    ``replay_facts`` itself answers, read off the server, not copied here.

    Each mutant below was run in the private copy scratchpad
    ``S7-URUN-mut`` (a copy of ``server/`` beside copies of
    ``ui/src/types.ts`` and ``ui/src/lib/flowsApi.ts``), from a byte backup of
    flowsApi.ts, restored and SHA-256 compared after each run.

    RED under "flowsApi drops armed" (the ``armed?: boolean;`` member deleted
    from ``FlowProgressSession``), observed:

        E   AssertionError: FlowProgressSession drifted: the route sends
            ['armed'], which flowsApi.ts does not declare, and flowsApi.ts
            declares [], which the route never sends

    RED under "plan_saved_ts never null" (``plan_saved_ts?: number;``),
    observed:

        E   AssertionError: flowsApi.ts types these otherwise:
            {'plan_saved_ts': (None, 'number')}

    RED under "armed required" (``armed: boolean;``, so a reader may trust
    a key an older server never sends), observed:

        E   AssertionError: flowsApi.ts must mark optional exactly the keys
            a FlowProgressSession carries only sometimes, and the replay
            facts a server older than S7 does not send
        E   assert {'plan_saved_ts'} == {'armed', 'plan_saved_ts'}
    """
    from astrodeck.flows.progress import replay_facts
    from astrodeck.sequence.session import Session

    records = await _route_sessions(rig)
    shapes = [(s["status"], s["armed"], s["plan_saved_ts"] is None)
              for s in records]
    assert shapes == [("active", False, False), ("dormant", True, False),
                      ("dormant", True, True)], (
        f"premise: a live session, an armed one frozen at a saved version, "
        f"and an armed one older than S7: {shapes}")

    ts = _flows_api("FlowProgressSession")
    sent = set().union(*map(set, records))
    always = set.intersection(*map(set, records))
    assert sent == set(ts), (
        f"FlowProgressSession drifted: the route sends "
        f"{sorted(sent - set(ts))}, which flowsApi.ts does not declare, and "
        f"flowsApi.ts declares {sorted(set(ts) - sent)}, which the route "
        f"never sends")
    replay = set(replay_facts(Session()))
    assert replay == {"armed", "plan_saved_ts"} and replay <= always, (
        f"premise: the route adds the replay facts to every record: "
        f"{sorted(replay)}")
    assert _flows_api_optional("FlowProgressSession") == (
        (sent - always) | replay), (
        "flowsApi.ts must mark optional exactly the keys a "
        "FlowProgressSession carries only sometimes, and the replay facts "
        "a server older than S7 does not send")
    for r in records:
        wrong = _wrong(r, ts)
        assert not wrong, f"flowsApi.ts types these otherwise: {wrong}"
