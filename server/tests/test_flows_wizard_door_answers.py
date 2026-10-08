# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The door's answers to POST /api/flows/wizard (#196, the server half; #412
item 2; #189 spec Revision 2 ruling 4, 1.4, 1.5, 1.8, S3 item 4, S6).

Send to Flow Wizard opens the wizard pre-filled with what the Sky FRAME or
the Atlas already knows, and the stepped sheet asks for the rest. Since S6
the route takes those answers beside the three the sheet has always sent:

* ``ra`` and ``dec``, the coordinates as typed: the framing's centre, which
  is not the catalogue's row for the name. Written onto the TARGET as they
  came, and the name is not looked up behind them (#190).
* ``skip``, the panels the framing took out, read as the TARGET's own
  ``skip`` param is read (``compile.parse_skip``) and written as given.
* ``cycle_plan`` and ``cycles``, the filter rows: a FILTER CYCLE's slot table
  and its pass count, checked against the connected wheel as the quick flow
  checks its filters (the assumed seven when no wheel is connected).
* ``guiding``, on or off.

The camera field, the measured angle and the wheel are rig facts the route
injects; a client sends none of them.

What this file holds, by the task's acceptance items:

  (2) A body with only the three original answers generates today's answer,
      byte for byte, across the whole option matrix, and the route answers
      what the generator does (``TestThreeAnswersAreTodays``).
  (3) A mosaic answer with coordinates, skip and filters is one TARGET block:
      the coordinates as typed, the skip as given, the loop wire from the
      tail of its panel lane, a FILTER CYCLE carrying the rows, and no Plan
      targets (``TestTheMosaicDoorAnswer``).
  (4) The doctor bar, across the matrix with the new answers
      (``TestTheDoctorBarWithTheDoorAnswers``).
  (5) Refusals are 422s that name the answer (``TestRefusals``).
  (6) ``fixtures/wizard_mosaic_answer.json``, recorded here and graded byte
      for byte (``TestTheRecordedAnswer``).
  (7) #412 item 2: ``KIND_MOSAIC``'s comment points at S6 (#196).

Every mutant named below was applied in a private scratch copy of
``server/`` (scratchpad ``s6-wiz-srv-mut``), each file restored from a byte
backup between mutants and never in the shared tree (#254); the failure each
produced is quoted.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.config import Optics
from astrodeck.flows import tonight, wizard
from astrodeck.flows.compile import compile_plan, lane_tail, loop_wires
from astrodeck.flows.doctor import check
from astrodeck.flows.models import FlowGraph
from astrodeck.flows.rig import RigFacts
from astrodeck.flows.store import flow_store
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.flows.wizard import (
    AUTOMATION_OPTIONS, CAMERA_FIXED_AT_PA, CYCLES_MAX, KIND_DEEP_SKY,
    KIND_EAA, KIND_MOSAIC, KIND_POOL, KINDS, OPT_GUIDING, OPT_WATCHDOG,
    ROTATE_TO_PA, UNGUIDED_EXPOSURE_DEFAULT, checked_skip, generate_answer)
from astrodeck.plans import plan_library

#: The three target inputs the sheet's placeholder advertises, as
#: test_flows_wizard.py enumerates them.
TARGET_INPUTS = ("", "M16", "M16, M17, M8, NGC 6946")

#: The spec's compact 2x2 field (A.4): 0.9 x 0.6 deg at bin 1.
FIELD = (0.9, 0.6)
WITH_ROTATOR = RigFacts(fov_deg=FIELD, has_rotator=True)
NO_ROTATOR = RigFacts(fov_deg=FIELD, has_rotator=False)

#: An IMX571 at 1000 mm: a bin-1 field of 1.346 x 0.900 deg, as
#: test_flows_wizard_route.py sets it.
OPTICS = Optics(focal_length_mm=1000.0, pixel_size_um=3.76,
                sensor_width_px=6248, sensor_height_px=4176)

NAME = "M31"
#: Where a framing put the grid's centre: half a minute of RA east and 14
#: arcmin north of the catalogue's M31, the offset a 3x2 laid out around the
#: galaxy takes. NOT the catalogue's row, so a TARGET that took the name's
#: coordinates instead of these is caught.
RA, DEC = "00h 43m 15.0s", "+41° 30' 00\""
#: A guided lane's filter rows, and an unguided one's: an unguided lane is
#: held to ``UNGUIDED_EXPOSURE_DEFAULT`` (30 s) a row.
ROWS = "L 60, R 60, G 60, B 60"
ROWS_UNGUIDED = "L 30, R 30, G 30, B 30"

#: The door's answers for a 3x2 of M31 with its south-east corner panel
#: (row 2, column 3) left out, at Rotate to PA 30, ten passes of LRGB, guided.
DOOR = dict(rows=2, cols=3, angle_mode=ROTATE_TO_PA, pa_deg=30.0, skip="2-3",
            ra=RA, dec=DEC, cycle_plan=ROWS, cycles=10, guiding=True)

#: The same answer as the sheet sends it, and the one the fixture records.
#: ``options`` leaves the Guiding chip off: the ``guiding`` answer is what
#: asks for the guider here.
BODY = {"kind": KIND_MOSAIC, "options": [OPT_WATCHDOG], "target": NAME,
        "ra": RA, "dec": DEC, "rows": 2, "cols": 3,
        "angle_mode": ROTATE_TO_PA, "pa_deg": 30, "skip": "2-3",
        "cycle_plan": ROWS, "cycles": 10, "guiding": True}

#: Any fixed instant; nothing here depends on the sky at it.
WHEN = 1788313689.0


def _subsets(items):
    for mask in range(1 << len(items)):
        yield frozenset(x for i, x in enumerate(items) if mask & (1 << i))


@pytest.fixture(autouse=True)
def _one_catalogue_search_per_name(monkeypatch):
    """The catalogue search behind ``tonight.resolve_target`` costs about 20
    ms a call and the matrices below ask it the same names hundreds of
    times. The REAL resolver answers each (name, instant) once per test and
    its answer is replayed; nothing is stubbed, and a test that replaces the
    resolver itself still can."""
    real, memo = tonight.resolve_target, {}

    def once(name, when=None):
        if (name, when) not in memo:
            memo[(name, when)] = real(name, when)
        return memo[(name, when)]

    monkeypatch.setattr(tonight, "resolve_target", once)


@pytest.fixture
def client(isolated_config, tmp_path, monkeypatch):
    """test_flows_wizard_route.py's client, with the plan library moved under
    the test's own directory too, so "no Plan targets" is asked of a library
    this test owns.

    AND NO HOP MEASURED (#445), set here rather than assumed, as the other
    compile route fixtures set it (test_flows_readouts.py,
    test_flows_routes.py): the module's engine outlives every test in the
    process, and the recording below carries the compile's ``hop_s`` and
    ``hop_measured``, which read
    ``engine.measured_cost("hop")``. Without this line, an earlier test in
    the same worker that left a measured hop on that engine turns the byte
    grade red on a correct server, observed verbatim in the S6-WIZ-SRV
    verifier's private copy (a first test setting
    ``engine._event_costs["hop"] = [90.0]`` and not restoring it):

        E   AssertionError: the recorded answer is not the route's;
            re-record it only after a deliberate change
        E     At index 4869 diff: b'1' != b'9'

    and green, the same leak in place, with it."""
    monkeypatch.setattr(flow_store, "_dir", tmp_path / "flows", raising=False)
    monkeypatch.setattr(plan_library, "_dir", tmp_path / "plans")
    monkeypatch.setattr(app_module.engine, "_event_costs", {})
    app = app_module.create_app()
    isolated_config.sweep()
    with TestClient(app) as c:
        c.store = isolated_config.store
        yield c


def _post(c, **body):
    return c.post("/api/flows/wizard", json=body)


def _one(graph: FlowGraph, node_type: str):
    found = [n for n in graph.nodes if n.type == node_type]
    assert len(found) == 1, f"expected one {node_type}, got {len(found)}"
    return found[0]


def _types(graph: FlowGraph) -> list[str]:
    return [n.type for n in graph.nodes]


class _Wheel:
    """A connected filter wheel as ``_rig_wheel`` reads one, the quick-flow
    test's shape: slot 2 is a blackout, and the wheel spells OIII "Oiii"."""
    connected = True
    filter_names = ["L", "Dark", "Oiii", "R", "G", "B"]

    def is_opaque(self, i):
        return i == 1


# ============================================ (2) the three answers, today's

#: Today's answer for every three-answer body: ``generate_answer``'s record
#: (less ``id``, ``created_ts`` and ``updated_ts``, which a record mints) and
#: its notes, for KINDS x all 64 chip subsets x TARGET_INPUTS, in that order,
#: each dumped with sorted keys, one per line, hashed. COMPUTED ON THE CODE AS
#: IT STOOD BEFORE S6 (HEAD 029914bb, wizard.py sha256 0d00d95c...), before a
#: line of the door answers was written, so it is today's and not a rebuild.
#: A deliberate change to what the three answers make (a node's Created-as
#: column, the lane) moves it, and is re-pinned in the commit that makes it.
#:
#: RE-PINNED IN BACKLOG W1 (NODE_DEFS gained DUSK WINDOW's clock params,
#: ``startClock``/``stopClock``, backlog WP-09 #191, 2026-09-30): every
#: generated DUSK node now carries both at "", moving every one of the 768
#: blobs by the same two new params and nothing else. Regenerated from the
#: SAME code path against the fixed code.
#:
#: RE-PINNED IN BACKLOG WP-85 (#195, wave 14, 2026-10-07): DUSK WINDOW's
#: Repeat row became "Automatic resume on subsequent nights until capture
#: quota is fulfilled", and NODE_DEFS gained its ``autoResume`` param at
#: "On", so every generated DUSK node now carries it and every one of the
#: 768 blobs moves by that one param and nothing else. PROVEN, not assumed:
#: with ``autoResume`` popped from each generated DUSK node the digest is
#: the previous pin, 2a1d2378..., byte for byte. Regenerated from the SAME
#: code path against the fixed code.
#:
#: RE-PINNED IN BACKLOG WP-118 (#195, wave 16 integration): the generated
#: DUSK WINDOW no longer carries ``repeat`` (the select was retired, and
#: ``generate()`` writes none), so every one of the 768 blobs loses the
#: ``"repeat": "Single night"`` line and nothing else. PROVEN, not assumed:
#: with ``repeat: "Single night"`` put back on each generated DUSK node (the
#: key absent on every one first) the digest is the previous pin,
#: 92aa6664..., byte for byte. Regenerated from the SAME code path.
TODAYS_ANSWERS_SHA256 = \
    "fc691c0b0e31789c330e9f67f9968b99c042d10cb718ff6d91f0386734757af3"

#: The chip subsets the route is asked, one of each shape a new answer's
#: default could move: none, the Guiding chip alone, every chip but it, and
#: every chip. A ``guiding`` that defaulted to True moves the first and the
#: third, one that defaulted to False refuses the second and the fourth, and
#: a default filter row, skip or coordinate moves all four.
SPAN = (frozenset(), frozenset({OPT_GUIDING}),
        frozenset(AUTOMATION_OPTIONS) - {OPT_GUIDING},
        frozenset(AUTOMATION_OPTIONS))


def _answer_blob(ans) -> str:
    rec = ans.record.model_dump(mode="json", by_alias=True)
    for minted in ("id", "created_ts", "updated_ts"):
        rec.pop(minted)
    return json.dumps({"record": rec, "notes": list(ans.notes)},
                      sort_keys=True)


class TestThreeAnswersAreTodays:
    """Revision 2 ruling 4: "a body with only the three original answers
    generates exactly today's graph"; #196's test: "byte-identical to
    today's. Mutant: default a new field to a value." """

    def test_every_three_answer_body_generates_todays_answer(self):
        """The whole option matrix, graph, name, tagline and notes, against
        the digest of the code before S6.

        RED under mutant "a new answer defaults to a value", in the
        generator (``generate_answer``'s ``guiding: bool | None = True``),
        observed verbatim (the matrix reaches EAA, which refuses it; every
        other answer would have come back guided):

            E   ValueError: guiding is true, but an 'EAA quick look' never
                guides: its short subs need no guider, and the settle time
                would make the live view stutter

        and with ``cycle_plan`` defaulting to "L 30" and ``cycles`` to 1:

            E   ValueError: cycle_plan belongs to a night of filter rows; an
                'EAA quick look' shoots its short subs through one CAPTURE
                LOOP
        """
        blobs = [_answer_blob(generate_answer(kind, opts, target))
                 for kind in KINDS for opts in _subsets(AUTOMATION_OPTIONS)
                 for target in TARGET_INPUTS]
        assert len(blobs) == 4 * 64 * 3
        digest = hashlib.sha256("\n".join(blobs).encode("utf-8")).hexdigest()
        assert digest == TODAYS_ANSWERS_SHA256

    @pytest.mark.parametrize("kind", KINDS)
    def test_the_route_answers_what_the_generator_does(self, client,
                                                       monkeypatch, kind):
        """THROUGH THE ROUTE, with every rig fact present that a three-answer
        body must not be moved by: a connected wheel, a measured angle. The
        saved graph (less the anchor the save writes), name, tagline and
        notes are the generator's for the same three answers, which the case
        above pins to today's. The whole matrix through the route costs
        about 0.75 s a post (measured: 580 s for 768, the library growing
        with every save, #433), so it is asked the ``SPAN`` of it that
        reaches every new answer's default.

        RED under mutant "a new answer defaults to a value", at the door
        (``FlowWizardBody.guiding: bool | None = True``), for all four kinds.
        Observed verbatim for three of them ([Deep-sky target] shown, the
        first subset, no chips, come back guided):

            E   AssertionError: []: the route's graph is not the generator's
            E   assert {'edges': [{'...settings': {}} == {'edges':
                [{'...settings': {}}

        and for [EAA quick look], which refuses a guiding it never does
        (re-observed by the S6-WIZ-SRV verifier):

            E   AssertionError: ([], '{"detail":{"detail":"guiding is true,
                but an \\'EAA quick look\\' never guides: its short subs need
                no guider, and the settle time would make the live view
                stutter"
            E   assert 422 == 200
        """
        monkeypatch.setitem(app_module.hub.devices, "filterwheel", _Wheel())
        monkeypatch.setattr(app_module.hub, "last_sky_angle",
                            {"pa_deg": 42.0, "source": "centring"},
                            raising=False)
        for opts in SPAN:
            r = _post(client, kind=kind, options=sorted(opts), target="M16")
            assert r.status_code == 200, (sorted(opts), r.text)
            got = r.json()
            want = generate_answer(kind, opts, "M16")
            graph = FlowGraph.model_validate(got["graph"])
            for n in (*graph.nodes, *want.record.graph.nodes):
                n.params.pop("frameAnchor", None)
            assert (graph.model_dump(by_alias=True)
                    == want.record.graph.model_dump(by_alias=True)), (
                f"{sorted(opts)}: the route's graph is not the generator's")
            assert (got["name"], got["tagline"], got["notes"]) == (
                want.record.name, want.record.tagline, list(want.notes))

    def test_with_optics_a_three_answer_mosaic_is_refused_as_today(
            self, client):
        """The control on the other side: with a camera field the Mosaic
        kind needs its grid, and three answers are refused exactly as they
        were before S6, in the generator's own words."""
        client.store.set_optics(OPTICS)
        r = _post(client, kind=KIND_MOSAIC, options=[], target="M16")
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["detail"] == (
            "a mosaic's rows is a whole number from 1 to 10, not None")


# ================================================= (3) the mosaic door answer

class TestTheMosaicDoorAnswer:
    def test_one_target_block_as_typed_and_as_given_with_the_loop(self):
        """Exactly one TARGET, and no POOL: the coordinates as typed, the
        skip as given, the grid laid out from the rig's field; the dashed
        "pass done" wire from the tail of its panel lane (``lane_tail``, the
        compile's own reading) into "next panel"; the tail a FILTER CYCLE
        carrying the rows, ten passes of one sub each; a GUIDE in the lane
        because ``guiding`` asked for one. The compile reads one rotating
        block with panel 2-3 skipped, and the plan is its five live panels
        in one group and nothing else.

        RED under mutant "loop wire omitted" (``_generate`` no longer calls
        ``_wire_the_loop``), observed verbatim:

            E   AssertionError: assert [] == [('n5', 'pass', 'n2', 'next')]
            E     Right contains one more item: ('n5', 'pass', 'n2', 'next')

        RED under mutant "skip dropped" (``_mosaic_params`` no longer writes
        the checked skip onto the TARGET), observed verbatim:

            E   AssertionError: assert '' == '2-3'
            E     - 2-3

        RED under mutant "coordinates ignored for the name"
        (``generate_answer`` hands ``_generate`` ``coords=None``, so the name
        is resolved through the catalogue), observed verbatim:

            E   assert ('M31', '00h ...41° 16\\' 08"') == ('M31', '00h
                ...41° 30\\' 00"')
            E     At index 1 diff: '00h 42m 44.3s' != '00h 43m 15.0s'
        """
        ans = generate_answer(KIND_MOSAIC, {OPT_WATCHDOG}, NAME,
                              rig=WITH_ROTATOR, **DOOR)
        g = ans.record.graph
        t = _one(g, "target")
        assert (t.params["name"], t.params["ra"], t.params["dec"]) == (
            NAME, RA, DEC)
        assert t.params["skip"] == "2-3"
        tail = lane_tail(g, t)
        assert [(e.from_, e.fromPort, e.to, e.toPort) for e in g.edges
                if e.fromPort == "pass"] == [(tail.id, "pass", t.id, "next")]
        assert ans.notes == ()
        assert "pool" not in _types(g)
        assert (t.params["rows"], t.params["cols"], t.params["fovX"],
                t.params["fovY"]) == (2, 3, *FIELD)
        assert tail.type == "cycle"
        assert (tail.params["plan"], tail.params["cycles"],
                tail.params["perCycle"]) == (ROWS, 10, 1)
        assert "capture" not in _types(g), "the rows replace the CAPTURE LOOP"
        assert len(loop_wires(g, t)) == 1
        assert "guide" in _types(g)
        assert not g.validation_errors()
        [entry] = compile_plan(g, "m")["targets"]
        assert (entry["mosaic"]["skip"], entry["loop"]) == ([[2, 3]], True)
        plan, _ = to_sequence_plan(compile_plan(g, "m"), g, flow_id="door",
                                   when=WHEN)
        [group] = plan.groups
        assert group.mode == "rotate" and len(group.skipped_ids) == 1
        assert [p.mosaic_group for p in plan.targets] == [group.id] * 5
        assert sorted((p.panel_row, p.panel_col) for p in plan.targets) == [
            (0, 0), (0, 1), (0, 2), (1, 0), (1, 1)]

    def test_through_the_route_one_target_block_and_no_plan_targets(
            self, client):
        """The same answer as the sheet sends it, with the rig's own field.
        The saved flow is one TARGET block laid out as asked, and nothing
        went to the Plan: the plan library this test owns is as empty after
        the answer as before it (the side channel S6 retires, spec S6), and
        the flow's compiled plan is the block's live panels alone.

        RED under the route's half of mutant "skip dropped" (the route's
        ``generate_answer`` call passes no ``skip``), observed verbatim:

            E   AssertionError: assert '' == '2-3'

        RED under the route's half of mutant "coordinates ignored for the
        name" (the call passes no ``ra`` and no ``dec``), observed verbatim:

            E   assert ('M31', '00h ...41° 16\\' 08"') == ('M31', '00h
                ...41° 30\\' 00"')
            E     At index 1 diff: '00h 42m 44.3s' != '00h 43m 15.0s'

        and under "loop wire omitted":

            E   AssertionError: assert [] == [('n5', 'n2', 'next')]
        """
        client.store.set_optics(OPTICS)
        assert client.get("/api/plans").json() == []
        r = client.post("/api/flows/wizard", json=BODY)
        assert r.status_code == 200, r.text
        rec = r.json()
        assert client.get("/api/plans").json() == [], (
            "the wizard put targets in the Plan")
        g = FlowGraph.model_validate(
            client.get(f"/api/flows/{rec['id']}").json()["graph"])
        t = _one(g, "target")
        assert (t.params["name"], t.params["ra"], t.params["dec"]) == (
            NAME, RA, DEC)
        assert t.params["skip"] == "2-3"
        assert "pool" not in _types(g)
        tail = lane_tail(g, t)
        assert tail.type == "cycle" and tail.params["plan"] == ROWS
        assert [(e.from_, e.to, e.toPort) for e in g.edges
                if e.fromPort == "pass"] == [(tail.id, t.id, "next")]
        out = client.post(f"/api/flows/{rec['id']}/compile").json()
        assert out["structural"] == []
        [block] = out["readouts"].values()
        assert (block["node_id"], block["panels"], block["mode"]) == (
            t.id, 5, "rotate")


# ============================================ each door answer, and its reach

class TestEachDoorAnswer:
    @pytest.mark.parametrize("kind", [KIND_DEEP_SKY, KIND_EAA, KIND_MOSAIC])
    def test_typed_coordinates_are_written_and_the_catalogue_not_asked(
            self, monkeypatch, kind):
        """Every kind with a TARGET, the Mosaic kind answered as one target
        (no camera field here) included. Given coordinates are the runnable
        part, so no catalogue row stands behind them, and a name the
        catalogue does not know draws no note.

        RED under mutant "coordinates ignored for the name", observed
        verbatim (all three kinds):

            E   AssertionError: the catalogue was asked despite coordinates
        """
        def _no(*a, **k):
            raise AssertionError("the catalogue was asked despite coordinates")
        monkeypatch.setattr(tonight, "resolve_target", _no)
        ans = generate_answer(kind, set(), "My framing of M31", ra=RA, dec=DEC)
        t = _one(ans.record.graph, "target")
        assert (t.params["ra"], t.params["dec"]) == (RA, DEC)
        assert not [n for n in ans.notes if "catalogue" in n]

    def test_filter_rows_make_the_capture_stage_a_filter_cycle(self):
        """A deep-sky night with rows is the quick flow's lane: a FILTER
        CYCLE where the CAPTURE LOOP would be, carrying the rows as given,
        and the HFR watchdog wired to it.

        RED under mutant "the rows are dropped" (``generate_answer`` hands
        ``_generate`` ``cycle_plan=""``), observed verbatim (as are the two
        cases after the next, which read the cycle's plan the same way):

            E   AssertionError: expected one cycle, got 0
            E   assert 0 == 1
        """
        g = generate_answer(KIND_DEEP_SKY, {OPT_GUIDING, OPT_WATCHDOG}, NAME,
                            ra=RA, dec=DEC, cycle_plan=ROWS,
                            cycles=12).record.graph
        cyc = _one(g, "cycle")
        assert (cyc.params["plan"], cyc.params["cycles"]) == (ROWS, 12)
        assert "capture" not in _types(g)
        cond = _one(g, "condition")
        assert any(e.from_ == cyc.id and e.fromPort == "frame"
                   and e.to == cond.id for e in g.edges)

    @pytest.mark.parametrize("kind", [KIND_DEEP_SKY, KIND_POOL, KIND_MOSAIC])
    def test_guiding_on_is_the_guiding_chip(self, kind):
        """``guiding: true`` with no chip lit makes exactly the graph the
        Guiding chip makes; ``false`` with none lit, exactly the unguided
        one (the control).

        RED under mutant "the guiding answer is ignored" (``generate_answer``
        calls ``_door_guiding`` and drops its answer), observed verbatim
        for all three kinds:

            E   AssertionError: guiding on did not guide
            E   assert {'edges': [{'...settings': {}} == {'edges':
                [{'...settings': {}}
        """
        target = "M16, M17" if kind == KIND_POOL else "M16"
        on = generate_answer(kind, set(), target, guiding=True).record.graph
        chip = generate_answer(kind, {OPT_GUIDING}, target).record.graph
        off = generate_answer(kind, set(), target, guiding=False).record.graph
        bare = generate_answer(kind, set(), target).record.graph
        assert on.model_dump() == chip.model_dump(), "guiding on did not guide"
        assert off.model_dump() == bare.model_dump()
        assert "guide" in _types(on) and "guide" not in _types(off)

    def test_the_wheel_the_rows_are_checked_against_is_the_one_given(self):
        """``wheel`` is the connected wheel's slot names (a rig fact); None
        is no wheel, and the assumed seven apply, as for the quick flow."""
        with pytest.raises(ValueError,
                           match=r"unknown filter\(s\) \['Oiii'\]"):
            generate_answer(KIND_DEEP_SKY, {OPT_GUIDING}, NAME,
                            cycle_plan="L 60, Oiii 180", cycles=3)
        g = generate_answer(KIND_DEEP_SKY, {OPT_GUIDING}, NAME,
                            cycle_plan="L 60, Oiii 180", cycles=3,
                            wheel=["L", "Oiii"]).record.graph
        assert _one(g, "cycle").params["plan"] == "L 60, Oiii 180"
        with pytest.raises(ValueError,
                           match=r"unknown filter\(s\) \['OIII'\]"):
            generate_answer(KIND_DEEP_SKY, {OPT_GUIDING}, NAME,
                            cycle_plan="L 60, OIII 180", cycles=3,
                            wheel=["L", "Oiii"])

    def test_an_unguided_lane_takes_rows_up_to_its_cap(self):
        """The unguided sub cap the sheet shows (``UNGUIDED_EXPOSURE_DEFAULT``,
        or the answer's ``unguided_exposure_s``) holds a filter row as it
        holds a CAPTURE LOOP's sub: a row at the cap is taken, and a longer
        cap takes a longer row. The refusal past it is in ``TestRefusals``."""
        cap = UNGUIDED_EXPOSURE_DEFAULT
        g = generate_answer(KIND_DEEP_SKY, set(), NAME, cycle_plan=f"L {cap}",
                            cycles=4).record.graph
        assert _one(g, "cycle").params["plan"] == f"L {cap}"
        g = generate_answer(KIND_DEEP_SKY, set(), NAME, 60,
                            cycle_plan="L 60", cycles=4).record.graph
        assert _one(g, "cycle").params["plan"] == "L 60"

    def test_a_blank_answer_is_no_answer(self):
        """An empty skip, filter table or coordinate is the answer not given:
        the same graph as leaving it out (a door that always sends its
        fields sends empty ones for a single target)."""
        grid = dict(rows=2, cols=2, angle_mode=ROTATE_TO_PA, pa_deg=10.0)
        bare = generate_answer(KIND_MOSAIC, set(), NAME, rig=WITH_ROTATOR,
                               **grid)
        blank = generate_answer(KIND_MOSAIC, set(), NAME, rig=WITH_ROTATOR,
                                skip=" ", cycle_plan="", ra="", dec="",
                                **grid)
        assert _answer_blob(blank) == _answer_blob(bare)
        assert (generate_answer(KIND_DEEP_SKY, set(), NAME, skip="")
                .record.graph.model_dump()
                == generate_answer(KIND_DEEP_SKY, set(), NAME)
                .record.graph.model_dump())

    def test_checked_skip_is_the_targets_reading(self):
        """``checked_skip`` reads a skip as ``compile.parse_skip`` does
        (commas or semicolons, each "row-col" once) and hands it back as
        given; no skip is None."""
        assert checked_skip("2-3; 1-1", 2, 3) == "2-3; 1-1"
        assert checked_skip(None, 2, 3) is None
        assert checked_skip("  ", 2, 3) is None


# ========================================= (4) the doctor bar, new answers too

#: The matrix's door cases: the kind, the target typed, the door answers,
#: and the rig facts the route would hand over. ``rows_`` asks for filter
#: rows, the guided or the unguided ones as the lane turns out.
DOOR_CASES = {
    "deepsky": (KIND_DEEP_SKY, NAME, dict(ra=RA, dec=DEC, rows_=True), None),
    "eaa": (KIND_EAA, NAME, dict(ra=RA, dec=DEC), None),
    "pool": (KIND_POOL, "M16, M17", dict(rows_=True), None),
    "mosaic-rotate": (KIND_MOSAIC, NAME, dict(
        ra=RA, dec=DEC, rows_=True, rows=2, cols=3, skip="2-3",
        angle_mode=ROTATE_TO_PA, pa_deg=30.0), WITH_ROTATOR),
    "mosaic-fixed": (KIND_MOSAIC, NAME, dict(
        ra=RA, dec=DEC, rows_=True, rows=3, cols=2, skip="1-1, 3-2",
        angle_mode=CAMERA_FIXED_AT_PA, use_measured=True,
        measured_pa_deg=-4.8), NO_ROTATOR),
}


#: Every door case with every ``guiding`` answer it can take: an EAA never
#: takes ``on`` (refused, in ``TestRefusals``).
MATRIX = [pytest.param(case, guiding, id=f"{case}-{word}")
          for case in sorted(DOOR_CASES)
          for guiding, word in ((None, "chips"), (True, "on"), (False, "off"))
          if not (DOOR_CASES[case][0] == KIND_EAA and guiding)]


class TestTheDoctorBarWithTheDoorAnswers:
    @pytest.mark.parametrize("case, guiding", MATRIX)
    def test_every_answer_passes_the_doctor(self, case, guiding):
        """Spec 1.8's bar, "at note level or better across its option
        matrix", with the door's answers: every chip subset for every door
        case and every ``guiding`` answer the case can take (``off`` beside
        the Guiding chip is a refusal, and an EAA never takes ``on``; both
        are graded in ``TestRefusals``). The rows are the guided ones when
        the lane guides and the unguided ones when it does not. Asked of
        each graph: the doctor has nothing above a note with the rig facts
        it was made from, the graph is valid, the lane guides exactly when
        the answer says it does, and a mosaic's only pass wire is its loop
        from the tail.

        RED under mutant "loop wire omitted", in all six mosaic cases,
        the doctor's M3 first, observed verbatim ([mosaic-rotate-chips]
        shown):

            E   AssertionError: []: doctor says ["\\u25b8 TARGET M31 - panels
                are shot one after another: a night cut short leaves the
                last panels empty. Wire FILTER CYCLE 'pass done' to TARGET
                'next panel' to rotate panels every pass."]
            E     []: pass wires []

        RED under mutant "the guiding answer is ignored", in the four
        ``on`` cases: the lane stays unguided, so its guided rows meet the
        unguided cap, observed verbatim ([deepsky-on] shown):

            E   ValueError: cycle_plan row(s) ['L 60', 'R 60', 'G 60', 'B
                60'] run longer than the 30 s an unguided lane is held to:
                turn guiding on, or shorten them

        (Mutant "the cap is not checked" leaves it green, as it should:
        the matrix's unguided rows are within the cap. ``TestRefusals``
        holds the cap.)
        """
        kind, target, answers, rig = DOOR_CASES[case]
        answers = dict(answers)
        with_rows = answers.pop("rows_", False)
        problems: list[str] = []
        for opts in _subsets(AUTOMATION_OPTIONS):
            if guiding is False and OPT_GUIDING in opts:
                continue
            guided = kind != KIND_EAA and bool(guiding or OPT_GUIDING in opts)
            kw = dict(answers)
            if with_rows:
                kw.update(cycle_plan=ROWS if guided else ROWS_UNGUIDED,
                          cycles=5)
            ans = generate_answer(kind, opts, target, guiding=guiding,
                                  rig=rig, **kw)
            g = ans.record.graph
            where = f"{sorted(opts)}"
            loud = [i.text for i in check(g, rig=rig)
                    if i.level in ("warn", "danger")]
            if loud:
                problems.append(f"{where}: doctor says {loud}")
            errs = g.validation_errors()
            if errs:
                problems.append(f"{where}: invalid graph {errs}")
            if kind == KIND_MOSAIC:
                t = _one(g, "target")
                tail = lane_tail(g, t)
                passes = [(e.from_, e.to, e.toPort) for e in g.edges
                          if e.fromPort == "pass"]
                if passes != [(tail.id, t.id, "next")]:
                    problems.append(f"{where}: pass wires {passes}")
            if ("guide" in _types(g)) != guided:
                problems.append(f"{where}: guided={guided} but the lane is "
                                f"{_types(g)}")
        assert not problems, "\n".join(problems[:6])


# ========================================================= (5) the refusals

#: The Mosaic kind's grid and angle, which every Mosaic refusal carries.
MOSAIC_DOOR = dict(rows=2, cols=3, angle_mode=ROTATE_TO_PA, pa_deg=30.0)
#: ``(kind, answers, what the refusal says)``. Each is refused by the
#: generator whether or not the rig has a camera field, and each message
#: names the answer it refuses.
REFUSED = {
    "skip-names-no-cell": (KIND_MOSAIC, dict(MOSAIC_DOOR, skip="3-1"),
                           "skip '3-1' names no panel of this grid of 3 "
                           "columns by 2 rows"),
    "skip-not-a-cell": (KIND_MOSAIC, dict(MOSAIC_DOOR, skip="2-3, top"),
                        "skip 'top' names no panel"),
    "skip-every-panel": (KIND_MOSAIC, dict(
        MOSAIC_DOOR, skip="1-1, 1-2, 1-3, 2-1, 2-2, 2-3"),
        "skip names every panel of this grid"),
    "skip-not-text": (KIND_MOSAIC, dict(MOSAIC_DOOR, skip=23), "skip is"),
    "skip-another-kind": (KIND_DEEP_SKY, dict(skip="2-3"),
                          "skip belong to the 'Mosaic' kind"),
    "filter-not-on-the-wheel": (KIND_DEEP_SKY, dict(
        cycle_plan="L 60, Purple 60", cycles=3, guiding=True),
        "cycle_plan: unknown filter(s) ['Purple']; this wheel has L, R, G, "
        "B, Ha, OIII, SII"),
    "row-unreadable": (KIND_DEEP_SKY, dict(
        cycle_plan="L sixty", cycles=3, guiding=True),
        "cycle_plan row 'L sixty' is not '<filter> <seconds>'"),
    "row-not-whole": (KIND_DEEP_SKY, dict(
        cycle_plan="L 0.5", cycles=3, guiding=True),
        "cycle_plan row 'L 0.5' is not '<filter> <seconds>'"),
    "row-zero": (KIND_DEEP_SKY, dict(
        cycle_plan="L 60, R 0", cycles=3, guiding=True),
        "cycle_plan row 'R 0' is 0 seconds"),
    "rows-without-cycles": (KIND_DEEP_SKY, dict(
        cycle_plan="L 60", guiding=True), "cycle_plan needs cycles"),
    "cycles-without-rows": (KIND_DEEP_SKY, dict(cycles=3),
                            "cycles is how many subs of each filter row, "
                            "and this answer has no cycle_plan"),
    "cycles-zero": (KIND_DEEP_SKY, dict(cycle_plan="L 60", cycles=0,
                                        guiding=True),
                    f"cycles is a whole number of passes from 1 to "
                    f"{CYCLES_MAX}"),
    "cycles-bool": (KIND_DEEP_SKY, dict(cycle_plan="L 60", cycles=True,
                                        guiding=True), "cycles is a whole"),
    "cycles-text": (KIND_DEEP_SKY, dict(cycle_plan="L 60", cycles="3",
                                        guiding=True), "cycles is a whole"),
    "cycles-over": (KIND_DEEP_SKY, dict(cycle_plan="L 60",
                                        cycles=CYCLES_MAX + 1, guiding=True),
                    "cycles is a whole"),
    "rows-with-eaa": (KIND_EAA, dict(cycle_plan="L 4", cycles=3),
                      "cycle_plan belongs to a night of filter rows"),
    "rows-past-the-unguided-cap": (KIND_DEEP_SKY, dict(
        cycle_plan="L 30, Ha 180", cycles=3),
        "cycle_plan row(s) ['Ha 180'] run longer than the 30 s an unguided "
        "lane is held to"),
    "ra-without-dec": (KIND_DEEP_SKY, dict(ra=RA),
                       "this answer has an ra and no dec"),
    "dec-without-ra": (KIND_DEEP_SKY, dict(dec=DEC),
                       "this answer has a dec and no ra"),
    "ra-unreadable": (KIND_DEEP_SKY, dict(ra="sixty", dec=DEC),
                      "cannot read the ra 'sixty'"),
    "dec-unreadable": (KIND_DEEP_SKY, dict(ra=RA, dec="north"),
                       "cannot read the dec 'north'"),
    "ra-out-of-range": (KIND_DEEP_SKY, dict(ra="25h 00m 00s", dec=DEC),
                        "the ra '25h 00m 00s' is 25 hours"),
    "ra-nan": (KIND_DEEP_SKY, dict(ra="nan", dec=DEC), "the ra 'nan' is"),
    "dec-out-of-range": (KIND_DEEP_SKY, dict(ra=RA, dec="+95 00 00"),
                         "the dec '+95 00 00' is 95 degrees"),
    "coords-with-a-pool": (KIND_POOL, dict(ra=RA, dec=DEC),
                           "ra and dec place a TARGET, and 'Best of "
                           "several' has none"),
    "coords-with-no-name": (KIND_DEEP_SKY, dict(ra=RA, dec=DEC, target=""),
                            "ra and dec need the target's name"),
    "guiding-off-beside-the-chip": (KIND_DEEP_SKY, dict(
        guiding=False, options={OPT_GUIDING}),
        "guiding is false, but the options light the 'Guiding' chip"),
    "guiding-on-for-eaa": (KIND_EAA, dict(guiding=True),
                           "guiding is true, but an 'EAA quick look' never "
                           "guides"),
    "guiding-not-a-bool": (KIND_DEEP_SKY, dict(guiding=1),
                           "guiding is true or false, not 1"),
}


class TestRefusals:
    @pytest.mark.parametrize("rig", [None, WITH_ROTATOR],
                             ids=["no-optics", "optics"])
    @pytest.mark.parametrize("case", sorted(REFUSED))
    def test_the_generator_refuses_and_names_the_answer(self, case, rig):
        """Refused rather than dropped (``_checked``'s reason), with or
        without a camera field. With none the Mosaic kind answers one target
        and never lays the grid out, which is why the route's door reads the
        skip itself (``test_the_route_answers_422_naming_the_answer``); here
        the generator is asked with a field and without, and must refuse
        every case it reads either way.

        RED under mutant "a skip naming no cell is read as no skip"
        (``checked_skip``'s ``if unread:`` made ``if False:``), observed
        verbatim for [skip-names-no-cell-optics] and
        [skip-not-a-cell-optics]:

            E   Failed: DID NOT RAISE <class 'ValueError'>

        RED under mutant "no wheel check" (``_door_rows``'s unknown-filter
        refusal made ``if False:``), the same line for both
        [filter-not-on-the-wheel] cases. RED under mutant "the cap is not
        checked" (``generate_answer`` no longer calls
        ``_within_the_unguided_cap``), the same line for both
        [rows-past-the-unguided-cap] cases.
        """
        kind, answers, says = REFUSED[case]
        answers = dict(answers)
        options = answers.pop("options", set())
        target = answers.pop("target", NAME)
        if rig is None and kind == KIND_MOSAIC and "skip" in answers:
            # With no field the grid is not read, so neither is a skip of
            # it, as a malformed grid is not: the door refuses these (the
            # route cases below), and the generator answers one target.
            ans = generate_answer(kind, options, target, rig=rig, **answers)
            assert ans.notes == (wizard.NO_OPTICS_REASON,)
            return
        with pytest.raises(ValueError) as e:
            generate_answer(kind, options, target, rig=rig, **answers)
        assert says in str(e.value), str(e.value)

    @pytest.mark.parametrize("body, answer", [
        ({"skip": "3-1"}, "skip"),
        ({"skip": "2-3", "rows": None}, "skip"),
        ({"skip": "1-1, 1-2, 1-3, 2-1, 2-2, 2-3"}, "skip"),
        ({"cycles": True}, "cycles"),
        ({"cycles": "10"}, "cycles"),
        ({"cycles": 2.5}, "cycles"),
        ({"guiding": 1}, "guiding"),
        ({"guiding": "true"}, "guiding"),
        ({"ra": 10.5}, "ra"),
        ({"cycle_plan": "L 60, OIII 180"}, "cycle_plan"),
        ({"cycle_plan": "L 60, Dark 60"}, "cycle_plan"),
        ({"dec": None}, "dec"),
        ({"guiding": False, "options": [OPT_GUIDING]}, "guiding"),
    ], ids=["skip-no-cell", "skip-no-grid", "skip-every-panel",
            "cycles-bool", "cycles-text", "cycles-half", "guiding-int",
            "guiding-text", "ra-number", "filter-off-the-wheel",
            "blackout-slot", "ra-without-dec",
            "guiding-off-beside-the-chip"])
    def test_the_route_answers_422_naming_the_answer(self, client,
                                                     monkeypatch, body,
                                                     answer):
        """Whether the door refuses it (pydantic's 422 names the field in
        ``loc``) or the generator does (``invalid_wizard_answer``, whose
        detail names it in words), the 422 names the answer. With no camera
        field, so the skip cases are the door's own: the generator does not
        read the grid then. The wheel is the connected one (``_Wheel``),
        which spells OIII "Oiii" and has a blackout slot. A ``None`` in a
        case leaves that answer out of the body.

        Each mutant below came back 200 as a flow where a 422 was due,
        observed verbatim (the id elided, the tagline's em dash written as a
        hyphen):

            E   AssertionError: {"id":"...","name":"M31","folder":"My
                flows","tagline":"Generated by the wizard - mosaic, planned
                as one target: set the camera and focal length in Settings >
                Optics to plan a mosaic","graph":{"nodes":[{"id":"n1", ...
            E   assert 200 == 422

        * "the door takes coercions" (``FlowWizardBody``'s ``cycles`` and
          ``guiding`` validators switched from ``mode="before"`` to after):
          [cycles-bool], [cycles-text], [guiding-int], [guiding-text];
        * "no door for the skip" (its validator returns the value first):
          [skip-no-cell], [skip-no-grid], [skip-every-panel];
        * "no wheel injected" (the route passes ``wheel=None``, so the
          assumed seven, which spell it OIII, are asked):
          [filter-off-the-wheel];
        * "a skip naming no cell is read as no skip": [skip-no-cell];
        * "no wheel check": [filter-off-the-wheel], [blackout-slot].
        """
        monkeypatch.setitem(app_module.hub.devices, "filterwheel", _Wheel())
        sent = {k: v for k, v in {**BODY, **body}.items() if v is not None}
        r = client.post("/api/flows/wizard", json=sent)
        assert r.status_code == 422, r.text
        detail = r.json()["detail"]
        if isinstance(detail, list):        # pydantic's, from the door
            assert answer in [e["loc"][-1] for e in detail], detail
        else:                               # the generator's
            assert detail["code"] == "invalid_wizard_answer"
            assert answer in detail["detail"], detail

    def test_control_the_connected_wheel_takes_its_own_spelling(
            self, client, monkeypatch):
        """The same rows spelled the connected wheel's way are taken, in the
        order given (``filter-off-the-wheel`` above is the refusal).

        RED under mutant "no wheel injected", observed verbatim:

            E   AssertionError: {"detail":{"detail":"cycle_plan: unknown
                filter(s) ['Oiii']; this wheel has L, R, G, B, Ha, OIII,
                SII","code":"invalid_wizard_answer"}}
            E   assert 422 == 200
        """
        monkeypatch.setitem(app_module.hub.devices, "filterwheel", _Wheel())
        r = client.post("/api/flows/wizard", json={
            **BODY, "cycle_plan": "Oiii 180, L 60"})
        assert r.status_code == 200, r.text
        g = FlowGraph.model_validate(r.json()["graph"])
        assert _one(g, "cycle").params["plan"] == "Oiii 180, L 60"


# ============================================= (6) the recorded mosaic answer

FIXTURE = Path(__file__).parent / "fixtures" / "wizard_mosaic_answer.json"
REWRITE = os.environ.get("ASTRODECK_REWRITE_WIZARD_FIXTURE") == "1"

#: The date the route's rig facts put in ``fovFrom`` ("the rig's optics,
#: matched <today>"), pinned so the recording is the same bytes every day.
RECORDED_ON = "2026-09-28"
#: The three values the save mints, written as these stand-ins: a record's
#: ``id`` is a uuid4 and its two timestamps are the clock at the save. The
#: test checks the live answer's own values before it writes these.
MINTED = {"id": "minted-by-the-save", "created_ts": 0.0, "updated_ts": 0.0}

ABOUT = ("POST /api/flows/wizard's answer for `request` (a 3x2 of M31 with "
         "panel 2-3 skipped, typed coordinates, LRGB rows, guided), on a rig "
         "whose camera field is an IMX571 at 1000 mm (1.346 x 0.900 deg), "
         "with no active profile, no wheel connected and no measured hop; "
         "and POST /api/flows/compile's `readouts` and `issues` for the "
         "saved graph. The answer's `id`, `created_ts` and `updated_ts` are "
         "stand-ins for the values the save mints, and `fovFrom`'s date is "
         "pinned. Recorded and graded byte for byte by server/tests/"
         "test_flows_wizard_door_answers.py (ASTRODECK_REWRITE_WIZARD_FIXTURE"
         "=1 re-records it); read by S6-WIZ-UI and S6-DOORS. Never edit it "
         "by hand.")


class _PinnedDate:
    """``time`` as the route sees it, with one change: the date ``fovFrom``
    carries is ``RECORDED_ON``. Everything else is the real module's."""

    def __getattr__(self, name):
        return getattr(time, name)

    @staticmethod
    def strftime(fmt, *args):
        if fmt == "%Y-%m-%d" and not args:
            return RECORDED_ON
        return time.strftime(fmt, *args)


def _recording(client, monkeypatch) -> str:
    """The fixture's text as the code makes it now."""
    client.store.set_optics(OPTICS)
    monkeypatch.setattr(app_module, "time", _PinnedDate())
    t0 = time.time()
    r = client.post("/api/flows/wizard", json=BODY)
    t1 = time.time()
    assert r.status_code == 200, r.text
    answer = r.json()
    assert re.fullmatch(r"[0-9a-f]{32}", answer["id"]), answer["id"]
    assert all(t0 <= answer[k] <= t1 for k in ("created_ts", "updated_ts"))
    stored = client.get(f"/api/flows/{answer['id']}").json()
    compiled = client.post("/api/flows/compile", json={
        "graph": stored["graph"], "name": stored["name"]})
    assert compiled.status_code == 200, compiled.text
    out = compiled.json()
    doc = {"about": ABOUT, "request": BODY, "answer": {**answer, **MINTED},
           "compile": {"readouts": out["readouts"], "issues": out["issues"]}}
    return json.dumps(doc, indent=1, ensure_ascii=True) + "\n"


class TestTheRecordedAnswer:
    def test_the_file_is_the_routes_answer_byte_for_byte(self, client,
                                                         monkeypatch):
        """The file's bytes are the recording the code makes now. Every
        number in it is exact on any C library: the field is rounded by
        ``effective_optics`` (3 places), the PA and overlap are the
        answer's, the anchor is text, and the readouts are rounded for the
        wire (``readouts._r``), so CI's two libraries write the same bytes.

        RED under mutant "fixture hand-edited" (the answer's recorded skip
        changed to "2-2" in the copy's file), observed verbatim:

            E   AssertionError: the recorded answer is not the route's;
                re-record it only after a deliberate change
            E   assert b'{\\n "about"...: []\\n }\\n}\\n' == b'{\\n
                "about"...: []\\n }\\n}\\n'
            E     At index 1754 diff: b'2' != b'3'

        RED the same way, the file as recorded and the code mutated, under
        "skip dropped" and its route half (``At index 1752 diff: b'2' !=
        b'"'``), "coordinates ignored for the name" and its route half
        (``At index 1515 diff: b'3' != b'2'``) and "loop wire omitted".

        LINE ENDINGS ARE THE CHECKOUT'S, not the recording's (#445). The
        file is written with LF, and this repository runs with
        ``core.autocrlf=true`` (the root .gitattributes says so over the
        photosphere fixtures), so a Windows clone or worktree checks it out
        with CRLF; every other byte is graded as recorded. Under mutant "the
        grade reads the checkout's line endings" (the ``replace`` below
        removed), the file re-written with CRLF as such a checkout writes it
        and nothing else changed, it went red on a correct server, observed
        verbatim in the S6-WIZ-SRV verifier's private copy:

            E   AssertionError: the recorded answer is not the route's;
                re-record it only after a deliberate change
            E   assert b'{\\r\\n "abou...\\n }\\r\\n}\\r\\n' == b'{\\n
                "about"...: []\\n }\\n}\\n'
            E     At index 1 diff: b'\\r' != b'\\n'

        and green with it, while "fixture hand-edited" stayed red with it
        (the control: the normalising does not blunt the grade).

        RE-RECORDED IN BACKLOG W1 (``ASTRODECK_REWRITE_WIZARD_FIXTURE=1``,
        NODE_DEFS gained DUSK WINDOW's clock params, backlog WP-09 #191,
        2026-09-30): the recorded DUSK node gained ``startClock``/
        ``stopClock`` at ``""``, nothing else moved.

        RE-RECORDED AGAIN IN WAVE 14 (``ASTRODECK_REWRITE_WIZARD_FIXTURE=1``,
        backlog WP-85 #195, 2026-10-07): the recorded DUSK node gained
        ``"autoResume": "On"`` beside ``"repeat": "Single night"``, and
        nothing else moved (the byte diff of the file is that one line).

        RE-RECORDED IN BACKLOG WP-118 (#195, wave 16): the recorded DUSK node
        lost the retired ``repeat`` line and nothing else moved.

        RE-RECORDED AGAIN AT THE WAVE 16 INTEGRATION
        (``ASTRODECK_REWRITE_WIZARD_FIXTURE=1``, backlog WP-123 #177):
        the answer's ``issues`` gained the one ``note`` row a mosaic that runs
        with ``solve_saved_lights`` off now carries (``coverage.stamping_note``
        through the compile route; ``test_w16_stamping_note_in_compile.py``).
        The byte diff of the file is that one entry; the recorded session
        leaves the setting at its default, off.
        """
        text = _recording(client, monkeypatch)
        if REWRITE:
            FIXTURE.write_bytes(text.encode("utf-8"))
        on_disk = FIXTURE.read_bytes().replace(b"\r\n", b"\n")
        assert on_disk == text.encode("utf-8"), (
            "the recorded answer is not the route's; re-record it only after "
            "a deliberate change")

    def test_the_file_holds_the_answer_it_says_it_holds(self):
        """BOUNDED FROM THE OTHER SIDE, by facts that do not come from the
        recording, so a re-recording from a broken server cannot wave a
        wrong answer through: the request is ``BODY``; the answer is one
        TARGET at the typed coordinates with the skip as given, laid out
        from the rig's field, its loop wire leaving the tail, a FILTER CYCLE
        carrying the rows, no notes; the compile reads five live panels of
        forty subs each (ten passes of four rows), in one rotating block,
        and the doctor has nothing above a note to say.

        RED under mutant "loop wire omitted", the fixture re-recorded from
        the mutated server (the byte grade above then passed on the new
        file: "1 failed, 1 passed"), observed verbatim:

            E   AssertionError: assert [] == [('n5', 'n2', 'next')]
            E     Right contains one more item: ('n5', 'n2', 'next')

        RED under "fixture hand-edited" too:

            E   assert ('M31', '00h ...' 00"', '2-2') == ('M31', '00h ...'
                00"', '2-3')
            E     At index 3 diff: '2-2' != '2-3'
        """
        doc = json.loads(FIXTURE.read_bytes().decode("utf-8"))
        assert doc["request"] == BODY
        answer = doc["answer"]
        g = FlowGraph.model_validate(answer["graph"])
        t = _one(g, "target")
        tail = lane_tail(g, t)
        assert [(e.from_, e.to, e.toPort) for e in g.edges
                if e.fromPort == "pass"] == [(tail.id, t.id, "next")]
        assert (t.params["name"], t.params["ra"], t.params["dec"],
                t.params["skip"]) == (NAME, RA, DEC, "2-3")
        assert (t.params["rows"], t.params["cols"], t.params["fovX"],
                t.params["fovY"], t.params["angle"], t.params["rotation"]) \
            == (2, 3, 1.346, 0.9, ROTATE_TO_PA, 30.0)
        assert tail.type == "cycle"
        assert (tail.params["plan"], tail.params["cycles"]) == (ROWS, 10)
        assert "guide" in _types(g) and "pool" not in _types(g)
        assert answer["notes"] == [] and answer["folder"] == "My flows"
        [block] = doc["compile"]["readouts"].values()
        assert (block["node_id"], block["mode"], block["panels"],
                block["subs_per_panel"], block["subs_total"]) == (
            t.id, "rotate", 5, 40, 200)
        assert [i for i in doc["compile"]["issues"]
                if i["level"] != "note"] == []


# ============================================== (7) #412 item 2, the comment

def test_kind_mosaics_comment_points_at_s6():
    """#412 item 2: the comment on ``KIND_MOSAIC`` said "Its stepped sheet
    lands with the framing modal (S4)", and S4 built the modal and not the
    sheet (spec section 8, S4's "Not built"). It points at S6 (#196) now,
    the slice that builds the sheet and moves the doors.

    RED under mutant "the S4 comment restored" (the comment as it stood at
    HEAD 029914bb), observed verbatim:

        E   AssertionError: the stepped sheet is placed in S4 again: The
            generator behind "Send to Flow Wizard" (#196, spec S3 item 4):
            one TARGET block laid out as a grid, with the panel loop wired.
            Its stepped sheet lands with the framing modal (S4) and its
            doors move in S6; until then it is reached t...
    """
    lines = Path(wizard.__file__).read_text(encoding="utf-8").splitlines()
    at = next(i for i, line in enumerate(lines)
              if line.startswith("KIND_MOSAIC = "))
    above: list[str] = []
    for line in reversed(lines[:at]):
        if not line.startswith("#:"):
            break
        above.insert(0, line[2:].strip())
    comment = " ".join(above)
    assert "S6" in comment and "#196" in comment, comment
    assert not re.search(r"lands with the framing modal \(S4\)", comment), (
        f"the stepped sheet is placed in S4 again: {comment}")
