# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""``compile_plan`` never raises (#328; spec 1.4's "the compile routes compile
half-built graphs" class).

An editor's graph is half-built by nature, and both compile routes compile it
on every edit: ``POST /api/flows/compile`` for a draft and
``POST /api/flows/{id}/compile`` for a saved flow, where ``structural`` lists
what validation refuses beside the plan. So any function on that path that
assumes a valid graph, or a finite number, turns a bad draft into a 500 with
no explanation where the operator should read the problem. #328 found two:

* a FILTER CYCLE count that is not finite. ``int()`` of an infinity raises
  ``OverflowError`` and of a NaN ``ValueError``, and validation found nothing
  wrong with such a graph, so a save stored it and every later compile of that
  flow failed, ``/run`` included;
* a flow wire into a node with no flow input. ``flow_order`` guarded the
  in-degree increment and not the decrement, so a wire into an event-only
  node (a FLAT PANEL) raised ``KeyError``.

What this file holds:

* the CORPUS: ``CORPUS_SIZE`` graphs from a Python-seeded generator (odd
  params, infinities and NaNs as numbers and as text, random wires to real
  and made-up ports), each compiled and asked of the doctor with a rig whose
  reject guards are off, so M9 compiles it a second time. Nothing may raise.
  The corpus checks its own premises first: it must contain each #328 shape,
  or a generator that stopped producing them would leave this green for no
  reason;
* each #328 graph by hand, and what it compiles to now;
* every other count the compile emits (a CAPTURE's frames, a POOL's quota,
  the DUSK FLATS count, the CALIBRATION QUEUE quota), read finite-only too;
* ``FlowGraph.validation_errors``: a count that is not a finite number above
  0 is refused in a sentence, so a save answers 422 and nothing is stored;
* the compile route: each #328 graph answers 200 with its problems listed;
* PAST ``compile_plan`` (#362, slice S5): over the same corpus,
  ``to_sequence_plan`` lets nothing out but ``GraphNotRunnable``, which names
  the block and the field the plan refused; every number the compile emits
  is finite or null and every text param a str, so the compile route's JSON
  (``allow_nan=False``) renders every answer; and an integer past a float's
  range in a DOME timeout or a TARGET rotation raises nothing from the
  compile or the doctor.

MUTANTS were run from byte backups in a private copy of ``server/`` under the
session scratchpad (``s4-compile-mut``, and the verifier's
``s4-compile-verify-mut`` for the cases it added; ``s5-compile-mut`` for the
#362 cases, and ``S5-COMPILE-verify-mut`` for the two refusal cases its
verifier added), never in the shared tree; each observed failure is quoted in
the test it turned red.
"""
from __future__ import annotations

import json
import math
import random
import re
from collections import Counter

import pytest

from astrodeck.devices.base import DEFAULT_SHUTTER_TIMEOUT_S
from astrodeck.flows import doctor
from astrodeck.flows.compile import compile_plan, flow_order
from astrodeck.flows.models import FlowGraph
from astrodeck.flows.nodes import NODE_DEFS, port_kind, target_angle
from astrodeck.flows.rig import RigFacts
from astrodeck.flows.to_plan import GraphNotRunnable, to_sequence_plan
from test_flows_continue import rig  # noqa: F401 (fixture)

#: How many graphs the corpus holds, and the seed that makes it the same
#: corpus on every run: a failure is then a graph anyone can rebuild.
CORPUS_SIZE = 3000
SEED = 328

#: The values a param may be given. The numbers a hand-edited file or a raw
#: POST can hold, the same numbers as text, text that is not one, and shapes a
#: param should never have (a list, a dict), next to the ordinary values.
ODD = [None, "", "abc", "inf", "-inf", "nan", "NaN", float("inf"),
       float("-inf"), float("nan"), 0, -1, 1, 2, 3, 0.5, 2.7, 1e308, -1e308,
       10 ** 40, True, False, "1e999", " 3 ", "0", "-5", [], {}, [1, 2],
       {"a": 1}, "1-1, 9-9", "L 60, R nan", "L inf", "Any angle",
       "Rotate to PA", "Camera fixed at PA", "Accepted subs"]

#: Port names a wire may name besides the node's own: real ports of other
#: types, and made-up ones.
PORTS = ["x", "pass", "next", "run", "target", "complete", "arm", "window",
         "frame", "done", "in", "clear", "do", "panel"]

#: Params a node may carry besides its type's own, the grid and the counts
#: included, so a POOL with `rows` and a CAPTURE with `cycles` turn up too.
EXTRA_KEYS = ["rows", "cols", "skip", "cycles", "perCycle", "quota"]

#: A rig with both reject guards off, so the doctor's M9 compiles the graph
#: too (it reads the stop boundary from ``compile_plan``'s schedule).
GUARDS_OFF = RigFacts(reject_guards_off=True)


def _graph(rng: random.Random) -> FlowGraph:
    """One random graph: 1 to 8 nodes of any type, 0 to 12 wires. TARGET,
    FILTER CYCLE and CAPTURE LOOP are drawn four times as often as the
    rest, so lanes, loops and mosaics are common rather than rare."""
    types = sorted(NODE_DEFS) + ["target", "cycle", "capture"] * 3
    nodes = []
    for i in range(rng.randint(1, 8)):
        kind = rng.choice(types)
        params = {k: rng.choice(ODD)
                  for k in [*NODE_DEFS[kind].params, *EXTRA_KEYS]
                  if rng.random() < 0.3}
        if kind == "target" and rng.random() < 0.5:
            # Half the blocks get a real grid, so the lane functions run.
            params.setdefault("rows", rng.choice([1, 2, 3]))
            params.setdefault("cols", rng.choice([1, 2, 3]))
        nodes.append({"id": f"n{i}", "type": kind, "x": rng.uniform(0, 900),
                      "y": rng.uniform(0, 600), "params": params})
    edges = []
    for _ in range(rng.randint(0, 12)):
        a, b = rng.choice(nodes), rng.choice(nodes)
        outs = [p.id for p in NODE_DEFS[a["type"]].outs]
        ins = [p.id for p in NODE_DEFS[b["type"]].ins]
        edges.append({"from": a["id"], "fromPort": rng.choice(outs + PORTS),
                      "to": b["id"] if rng.random() > 0.03 else "ghost",
                      "toPort": rng.choice(ins + PORTS)})
    return FlowGraph.model_validate({"nodes": nodes, "edges": edges})


def _corpus() -> list[FlowGraph]:
    rng = random.Random(SEED)
    return [_graph(rng) for _ in range(CORPUS_SIZE)]


def _not_finite(value) -> bool:
    try:
        return not math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def _into_no_flow_input(g: FlowGraph) -> bool:
    """A flow wire (by its source) into a port that is not a flow input,
    the #328 KeyError shape and its wider form."""
    for e in g.edges:
        src, dst = g.node(e.from_), g.node(e.to)
        if (src is not None and dst is not None
                and port_kind(src.type, e.fromPort, "out") == "flow"
                and port_kind(dst.type, e.toPort, "in") != "flow"):
            return True
    return False


def _bad_count(g: FlowGraph) -> bool:
    """A FILTER CYCLE whose `cycles` or `perCycle` is not finite."""
    return any(n.type == "cycle" and (_not_finite(n.params.get("cycles"))
                                      or _not_finite(n.params.get("perCycle")))
               for n in g.nodes)


#: How many graphs of the corpus must hold each #328 shape. A FLOOR, not a
#: pin: the generator draws its ports from ``NODE_DEFS``, so the corpus is
#: reshuffled whenever the vocabulary changes, and a pinned count went red
#: on a legitimate one (S4, #331: AUTOFOCUS and GUIDE gained ``pass``, and
#: 858, 338 and 241 became 841, 358 and 238). What the premise guards is
#: that each shape is still COMMON, which a generator that stopped making
#: one fails at once (each mutant below drops its shape to 0 or near it).
SHAPE_FLOOR = 100


class TestTheCorpus:
    def test_the_corpus_holds_every_shape_328_found(self):
        """THE PREMISE, checked before the verdict: a generator that stopped
        making these shapes would leave the next test green for no reason
        (a harness that cannot reach the branch). Each shape must be in at
        least ``SHAPE_FLOOR`` graphs; the counts on the vocabulary of
        2026-09-27 are 841, 358 and 238.

        Mutants of the GENERATOR (this file), run by the S4-COMPILE
        re-verifier in a private copy (``s4-compile-verify2-mut``), each
        observed red here:

        "no wires" (``rng.randint(0, 12)`` wires made ``rng.randint(0, 0)``):

            AssertionError: {'bad cycle count': 358, 'into no flow input':
            0, 'mosaic': 234}

        "no counts that are not finite" (``ODD`` without "inf", "-inf",
        "nan", "NaN", the two float infinities, the float NaN and "1e999"):

            AssertionError: {'bad cycle count': 0, 'into no flow input':
            842, 'mosaic': 234}

        "no grids" (the half of the TARGETs given a real grid given none,
        ``rng.random() < 0.5`` made ``< 0.0``):

            AssertionError: {'bad cycle count': 349, 'into no flow input':
            836, 'mosaic': 4}
        """
        corpus = _corpus()
        assert len(corpus) == CORPUS_SIZE
        shapes = Counter()
        for g in corpus:
            shapes["into no flow input"] += _into_no_flow_input(g)
            shapes["bad cycle count"] += _bad_count(g)
            shapes["mosaic"] += any(
                n.type == "target"
                and isinstance(n.params.get("rows"), int)
                and isinstance(n.params.get("cols"), int)
                and n.params["rows"] * n.params["cols"] > 1
                for n in g.nodes)
        assert all(shapes[k] >= SHAPE_FLOOR for k in (
            "into no flow input", "bad cycle count", "mosaic")), dict(shapes)

    def test_compile_plan_and_the_doctor_never_raise(self):
        """Every graph compiles, and the doctor, which compiles it again for
        M9, answers too. Each failure is counted by its exception and the
        first of each kind is quoted with its graph's index, so a red run
        says what broke and on which graph.

        RED under mutant "unguarded indeg decrement" (``flow_order``'s wires
        filtered by their source port alone, as before #328, so the
        decrement meets a destination that has no flow input), observed:

            AssertionError: 221 of 3000 graphs raised: {'KeyError': 221};
            first of each: {'KeyError': "graph 9: KeyError('n0')"}

        RED under mutant "int(_num()) for cycles" (the FILTER CYCLE's
        ``cycles`` read ``max(1, int(_num(..., 1) or 1))`` again, in place
        of the finite-only helper), observed:

            AssertionError: 192 of 3000 graphs raised: {'ValueError': 70,
            'OverflowError': 122}; first of each: {'ValueError': "graph 0:
            ValueError('cannot convert float NaN to integer')",
            'OverflowError': "graph 28: OverflowError('cannot convert float
            infinity to integer')"}

        Both re-run by the S4-COMPILE re-verifier on 2026-09-27, after #331
        had reshuffled the corpus (see ``SHAPE_FLOOR``), and red again,
        observed:

            AssertionError: 227 of 3000 graphs raised: {'KeyError': 227};
            first of each: {'KeyError': "graph 9: KeyError('n0')"}

            AssertionError: 204 of 3000 graphs raised: {'ValueError': 58,
            'OverflowError': 146}; first of each: {'ValueError': "graph 0:
            ValueError('cannot convert float NaN to integer')",
            'OverflowError': "graph 28: OverflowError('cannot convert float
            infinity to integer')"}
        """
        raised: Counter[str] = Counter()
        first: dict[str, str] = {}
        for i, g in enumerate(_corpus()):
            try:
                compile_plan(g, "fuzz")
                doctor.check(g, rig=GUARDS_OFF)
            except Exception as e:      # noqa: BLE001 - the verdict itself
                kind = type(e).__name__
                raised[kind] += 1
                first.setdefault(kind, f"graph {i}: {e!r}")
        assert not raised, (f"{sum(raised.values())} of {CORPUS_SIZE} graphs "
                            f"raised: {dict(raised)}; first of each: {first}")


# ------------------------------------------------------ the #328 graphs

#: The three graphs #328 quotes, verbatim.
INF_CYCLES = {"nodes": [{"id": "t", "type": "target"},
                        {"id": "cy", "type": "cycle",
                         "params": {"cycles": "inf"}}],
              "edges": [{"from": "t", "fromPort": "target", "to": "cy",
                         "toPort": "run"}]}
NAN_PER_CYCLE = {"nodes": [{"id": "t", "type": "target"},
                           {"id": "cy", "type": "cycle",
                            "params": {"perCycle": "nan"}}],
                 "edges": [{"from": "t", "fromPort": "target", "to": "cy",
                            "toPort": "run"}]}
INTO_A_FLAT_PANEL = {"nodes": [{"id": "c", "type": "capture"},
                               {"id": "fp", "type": "flatpanel"}],
                     "edges": [{"from": "c", "fromPort": "complete",
                                "to": "fp", "toPort": "x"}]}


def _g(raw: dict) -> FlowGraph:
    return FlowGraph.model_validate(raw)


class TestThe328Graphs:
    @pytest.mark.parametrize("raw,key", [(INF_CYCLES, "cycles"),
                                         (NAN_PER_CYCLE, "per_cycle")],
                             ids=["cycles-inf", "perCycle-nan"])
    def test_a_count_that_is_not_finite_reads_as_the_default(self, raw, key):
        """The count reads as 1, the value a count that is not a number has
        always read as (``_num``'s default), and the other count keeps the
        node's own. The step is still the stage, whole.

        RED under mutant "int(_num()) for cycles", observed on cycles-inf
        (perCycle-nan stays green, its count is not the one the mutant
        moved):

            OverflowError: cannot convert float infinity to integer
        """
        (entry,) = compile_plan(_g(raw), "n")["targets"]
        (step,) = entry["steps"]
        assert step[key] == 1
        other = "per_cycle" if key == "cycles" else "cycles"
        assert step[other] == {"cycles": 45, "per_cycle": 1}[other]

    def test_a_flow_wire_into_no_flow_input_is_no_step_of_the_cursor(self):
        """The FLAT PANEL has no flow port at all, so it is not on the
        cursor's path, and the wire into it is walked past.

        RED under mutant "unguarded indeg decrement", observed:

            KeyError: 'fp'
        """
        g = _g(INTO_A_FLAT_PANEL)
        assert [n.id for n in flow_order(g)] == ["c"]
        assert compile_plan(g, "n")["targets"] == []

    def test_a_flow_wire_into_an_event_input_orders_nothing(self):
        """The wider form: FILTER CYCLE 'all done' dragged onto TARGET 'next
        panel', a flow output into an event input, which validation refuses
        by name. The TARGET has a flow input of its own (`arm`), so it was
        in the walk, and the wire made it wait on its own stage: neither was
        ever ready, and the draft compiled to no target. A wire is a step of
        the cursor only when it enters a FLOW input, as the lane functions
        read it (``compile._flow_wire``).

        RED under mutant "unguarded indeg decrement" (the same mutant: the
        wire list filtered by its source alone), observed:

            AssertionError: assert [] == ['t', 'cy']
              Right contains 2 more items, first extra item: 't'
        """
        g = _g({"nodes": [{"id": "t", "type": "target"},
                          {"id": "cy", "type": "cycle", "x": 100}],
                "edges": [{"from": "t", "fromPort": "target", "to": "cy",
                           "toPort": "run"},
                          {"from": "cy", "fromPort": "complete", "to": "t",
                           "toPort": "next"}]})
        assert any("can't feed an event input" in s
                   for s in g.validation_errors()), "premise: refused"
        assert [n.id for n in flow_order(g)] == ["t", "cy"]
        (entry,) = compile_plan(g, "n")["targets"]
        assert [s["strategy"] for s in entry["steps"]] == ["cycle"]

    def test_an_integer_past_a_floats_range_reads_as_no_number(self):
        """A raw POST can hold a 400-digit integer, and ``float()`` of it
        raises ``OverflowError``, not ``ValueError``, so the compile's and the
        doctor's number readers let it through. It reads as a value that is
        not a number: the counts as their defaults, a grid side as 1.

        Kept out of the corpus: two readers outside this change raised on it
        too (``DomePolicy.from_node_params``'s ``timeout`` and
        ``nodes._rotation_deg``), so the corpus would have graded them. #362
        fixed both, and ``TestPastCompilePlan`` grades them by hand; the
        corpus is left as it was, because a longer ``ODD`` would reshuffle
        every graph the quoted counts in this file were observed on.

        RED under mutant "OverflowError escapes _num" (``compile._num``
        catching ``(TypeError, ValueError)`` only), observed:

            OverflowError: int too large to convert to float

        RED under mutant "OverflowError escapes the doctor's _num" (the same
        in ``doctor._num``), observed on the doctor's call:

            OverflowError: int too large to convert to float
        """
        huge = 10 ** 400
        g = _g({"nodes": [
            {"id": "t", "type": "target",
             "params": {"rows": huge, "cols": 2, "fovX": huge, "fovY": 1.0,
                        "overlap": huge, "passes": huge, "centerTol": huge}},
            {"id": "cy", "type": "cycle", "x": 100,
             "params": {"cycles": huge, "perCycle": huge, "gain": huge}},
            {"id": "c", "type": "capture", "x": 200,
             "params": {"count": huge, "exposure": huge}},
            {"id": "p", "type": "pool", "x": 300, "params": {"quota": huge}}],
            "edges": [{"from": "t", "fromPort": "target", "to": "cy",
                       "toPort": "run"},
                      {"from": "cy", "fromPort": "complete", "to": "c",
                       "toPort": "run"}]})
        compiled = compile_plan(g, "n")
        entry = next(t for t in compiled["targets"] if t["node_id"] == "t")
        grid = entry["mosaic"]
        assert (grid["rows"], grid["cols"]) == (1, 2), "a side past a float is 1"
        assert (grid["fov_x"], grid["overlap"], grid["passes"],
                entry["centre"]["tol_arcmin"]) == (None, None, None, None)
        cycle, capture = entry["steps"]
        assert (cycle["cycles"], cycle["per_cycle"], capture["count"]) == \
            (1, 1, 0)
        assert all(t["quota_cycles"] == 0 for t in compiled["targets"]
                   if t["node_id"] == "p")
        doctor.check(g, rig=GUARDS_OFF)

    def test_control_a_valid_lane_walks_as_it_did(self):
        """Control: DUSK -> TARGET -> CAPTURE, every wire into a flow input,
        walks in wire order whatever the canvas says, as it always did."""
        g = _g({"nodes": [{"id": "c", "type": "capture", "x": 0},
                          {"id": "t", "type": "target", "x": 50},
                          {"id": "d", "type": "dusk", "x": 900}],
                "edges": [{"from": "d", "fromPort": "window", "to": "t",
                           "toPort": "arm"},
                          {"from": "t", "fromPort": "target", "to": "c",
                           "toPort": "run"}]})
        assert g.validation_errors() == []
        assert [n.id for n in flow_order(g)] == ["d", "t", "c"]


# ------------------------------------------- every count, read finite-only

def _counted(kind: str, value) -> FlowGraph:
    """TARGET M42 -> CAPTURE LOOP, with ``value`` as one count the compile
    emits: the CAPTURE's own ``count``, the ``quota`` of a POOL of two in the
    TARGET's place, or the ``count`` of a DUSK FLATS or the ``quota`` of a
    CALIBRATION QUEUE beside the lane."""
    owner = {"id": "t", "type": "target",
             "params": {"name": "M42", "ra": "05h 35m 17s",
                        "dec": "-05 23 28"}}
    capture = {"id": "c", "type": "capture", "x": 200,
               "params": {"filter": "L", "exposure": 60, "count": 3}}
    nodes = [owner, capture]
    if kind == "capture":
        capture["params"]["count"] = value
    elif kind == "pool":
        owner.update(type="pool", params={"members": "M42, M78",
                                          "quota": value})
    elif kind == "duskflats":
        nodes.append({"id": "k", "type": kind, "x": 400,
                      "params": {"count": value}})
    else:
        nodes.append({"id": "k", "type": kind, "x": 400,
                      "params": {"quota": value}})
    return _g({"nodes": nodes,
               "edges": [{"from": "t", "fromPort": "target", "to": "c",
                          "toPort": "run"}]})


#: Where each count lands in the compiled dict.
_READ = {
    "capture": lambda c: [c["targets"][0]["steps"][0]["count"]],
    "pool": lambda c: [t["quota_cycles"] for t in c["targets"]],
    "duskflats": lambda c: [c["automation"]["dusk_flats"]["count"]],
    "calib": lambda c: [c["automation"]["calibration_queue"]["quota"]],
}


class TestEveryCountIsFinite:
    @pytest.mark.parametrize("value", ["inf", "nan"])
    @pytest.mark.parametrize("kind", ["capture", "pool", "duskflats",
                                      "calib"])
    def test_a_count_that_is_not_finite_reads_as_0(self, kind, value):
        """Every count ``compile_plan`` emits beside the FILTER CYCLE's two
        is read through ``_finite`` too: the CAPTURE's frames, the POOL's
        quota, the DUSK FLATS count and the CALIBRATION QUEUE quota. Read
        through ``_num`` alone, "inf" and "nan" came out as an infinity and
        a NaN, which the compile routes cannot render as JSON, and two of
        them raised further on in ``to_plan``, whose ``int()`` meets them.
        Finite-only, each reads as 0, the value text that is no number
        always read as; the CAPTURE's 0 is then ``to_plan``'s own refusal
        (``GraphNotRunnable``, a 422 at the routes), and the other three
        compile to a plan.

        Added by the S4-COMPILE verifier: before it, no test moved when any
        of these four readings went back to ``_num``.

        RED under mutant "the CAPTURE count read by _num" (that one
        reading back to ``_num``), observed on capture-inf:

            AssertionError: assert ([inf] and {inf} == {0}

        and on capture-nan the same with ``nan``. RED under "the POOL quota
        read by _num", "the DUSK FLATS count read by _num" and "the
        CALIBRATION QUEUE quota read by _num" the same way on each of their
        two cases, e.g. pool-inf:

            AssertionError: assert ([inf, inf] and {inf} == {0}

        The first assertion is what trips. The two after it are the reason
        it matters, seen by the verifier's probe with all four readings on
        ``_num``: ``json.dumps`` gave "ValueError: Out of range float values
        are not JSON compliant: inf" for each, and ``to_sequence_plan`` gave
        ``OverflowError`` for capture-inf and calib-inf.
        """
        g = _counted(kind, value)
        compiled = compile_plan(g, "n")
        assert _READ[kind](compiled) and set(_READ[kind](compiled)) == {0}
        json.dumps(compiled, allow_nan=False)
        if kind == "capture":
            with pytest.raises(GraphNotRunnable):
                to_sequence_plan(compiled, g, flow_id="f-328")
        else:
            to_sequence_plan(compiled, g, flow_id="f-328")

    @pytest.mark.parametrize("kind", ["capture", "pool", "duskflats",
                                      "calib"])
    def test_control_a_finite_count_reads_as_itself(self, kind):
        """Control: a finite count is ``_num``'s reading exactly, so every
        flow that compiled before compiles to the same numbers."""
        compiled = compile_plan(_counted(kind, "7"), "n")
        assert set(_READ[kind](compiled)) == {7}


# ------------------------------------------------------------- validation

def _one(kind: str, **params) -> FlowGraph:
    return FlowGraph.model_validate(
        {"nodes": [{"id": "k", "type": kind, "params": params}]})


class TestValidationRefusesACountThatIsNoCount:
    @pytest.mark.parametrize("kind,key", [("cycle", "cycles"),
                                          ("cycle", "perCycle"),
                                          ("pool", "quota")])
    @pytest.mark.parametrize("value", ["inf", "-inf", "nan", float("inf"),
                                       float("nan"), 0, -1, "0", -0.5, "1e999"],
                             ids=["inf", "-inf", "nan", "float-inf",
                                  "float-nan", "0", "-1", "text-0", "-0.5",
                                  "1e999"])
    def test_refused_in_a_sentence(self, kind, key, value):
        """Not finite, or not above 0: refused, naming the card, the node and
        the param, and saying what a count must be.

        RED under mutant "validation accepts a non-finite count"
        (``_not_a_count``'s ``not math.isfinite(number) or`` removed, so only
        a value at or below 0 is refused), observed on the fifteen
        non-finite cases (the zero and negative ones stay green), e.g.
        cycle cycles inf:

            AssertionError: assert [] == ['FILTER CYCL...mber above 0']
              Right contains one more item: "FILTER CYCLE 'k': 'cycles' is
              'inf', and a count must be a finite number above 0"
        """
        label = NODE_DEFS[kind].label
        assert _one(kind, **{key: value}).validation_errors() == [
            f"{label} 'k': {key!r} is {value!r}, and a count must be a "
            f"finite number above 0"]

    @pytest.mark.parametrize("kind,key", [("cycle", "cycles"),
                                          ("cycle", "perCycle"),
                                          ("pool", "quota")])
    def test_an_integer_past_a_floats_range_is_refused(self, kind, key):
        """A raw POST can hold a 400-digit integer. ``float()`` of it raises
        ``OverflowError``, not ``ValueError``, and the compile reads it as no
        number (the default), so it is no finite count the compile could
        honour, and the save refuses it with the same sentence.

        Added by the S4-COMPILE verifier: before it, the ``OverflowError``
        branch of ``_not_a_count`` was reached by no test (the "1e999" case
        above is text, and ``float()`` reads it as an infinity).

        RED under mutant "an overflowing count accepted" (that branch
        answering False), observed on each case, e.g. cycle cycles:

            ValueError: not enough values to unpack (expected 1, got 0)
        """
        huge = 10 ** 400
        (error,) = _one(kind, **{key: huge}).validation_errors()
        assert error == (f"{NODE_DEFS[kind].label} 'k': {key!r} is {huge!r}, "
                         f"and a count must be a finite number above 0")

    @pytest.mark.parametrize("kind,params", [
        ("cycle", {}), ("cycle", {"cycles": 45, "perCycle": 1}),
        ("cycle", {"cycles": "20", "perCycle": 0.5}),
        ("cycle", {"cycles": "", "perCycle": None}),
        ("cycle", {"cycles": "abc"}),
        ("pool", {"quota": 45}), ("pool", {}),
        ("calib", {"quota": 0}), ("capture", {"count": 0}),
        ("target", {"cycles": "inf"})],
        ids=["defaults", "numbers", "text-numbers", "blank", "not-a-number",
             "pool-quota", "pool-defaults", "calib-quota-0",
             "capture-count-0", "not-this-type"])
    def test_control_what_is_not_refused(self, kind, params):
        """Controls. A missing or blank value is the default; text that is
        not a number is left to the compile, which reads it as the default,
        because params are checked where they are used (``models.py``'s
        docstring) and this rule is about the three counts ``int()`` is
        called on. A calibration quota of 0 means "take none", a CAPTURE
        count is the capture step's own refusal in ``to_plan``, and a count
        key on a type that has no such count means nothing to it."""
        assert _one(kind, **params).validation_errors() == []


class TestTheRoutes:
    async def test_a_save_with_a_count_that_is_not_finite_is_a_422(self, rig):
        """The save answers 422 with the sentence and stores nothing, so the
        flow can never reach the compile that raised.

        RED under mutant "validation accepts a non-finite count", observed
        (the save stored it; the long line cut):

            AssertionError: {"id":"88aeef48d14a440ca9926fc3c1a2f674","name":
            "bad count","folder":"My flows","tagline":"","graph":{"nodes":
            [{"id":"t","type":"target",...
            assert 200 == 422
        """
        r = await rig.client.post("/api/flows", json={
            "flow": {"name": "bad count", "graph": INF_CYCLES}})
        assert r.status_code == 422, r.text
        assert ("FILTER CYCLE 'cy': 'cycles' is 'inf', and a count must be "
                "a finite number above 0") in r.text
        listed = await rig.client.get("/api/flows")
        assert [c["name"] for c in listed.json()
                if c.get("name") == "bad count"] == []

    @pytest.mark.parametrize("raw", [INF_CYCLES, NAN_PER_CYCLE,
                                     INTO_A_FLAT_PANEL],
                             ids=["cycles-inf", "perCycle-nan",
                                  "into-a-flat-panel"])
    async def test_the_compile_route_answers_200_with_the_problems(
            self, rig, raw):
        """The editor's compile answers with the problems listed, never 500:
        the count in ``structural`` (validation), the FLAT PANEL's made-up
        port there too, and a draft with no TARGET as the plan's danger row.

        RED under mutant "int(_num()) for cycles" on cycles-inf, and under
        "unguarded indeg decrement" on into-a-flat-panel, observed (the
        route calls ``compile_plan`` outside any try, so the error leaves
        the app):

            OverflowError: cannot convert float infinity to integer
            KeyError: 'fp'

        RED under mutant "validation accepts a non-finite count" on
        cycles-inf and perCycle-nan, observed (a 200, but with the problem
        unlisted), e.g.:

            assert [] == ['FILTER CYCL...mber above 0']
              Right contains one more item: "FILTER CYCLE 'cy': 'cycles' is
              'inf', and a count must be a finite number above 0"
        """
        r = await rig.client.post("/api/flows/compile", json={"graph": raw})
        assert r.status_code == 200, r.text
        out = r.json()
        if raw is INTO_A_FLAT_PANEL:
            assert out["structural"] == ["flatpanel has no input port 'x'"]
            assert [u["key"] for u in out["unmapped"]] == ["plan"]
            assert out["unmapped"][0]["level"] == "danger"
        else:
            key = "cycles" if raw is INF_CYCLES else "perCycle"
            assert out["structural"] == [
                f"FILTER CYCLE 'cy': {key!r} is "
                f"{raw['nodes'][1]['params'][key]!r}, and a count must be a "
                f"finite number above 0"]
            (entry,) = out["plan"]["targets"]
            assert [s["strategy"] for s in entry["steps"]] == ["cycle"]


# ------------------------------------------- past compile_plan (#362, S5)

#: The shape of the refusal ``to_plan`` gives for a value the plan's models
#: refuse (#362 item 1): the block, the field, the value, and why. Read off
#: the sentence, so the corpus premise below can count the graphs that reach
#: that refusal.
PLAN_REFUSAL = re.compile(r": \S+ of .+ cannot be used - input ")

#: How many corpus graphs must reach that refusal, and how many must carry
#: each shape item 2 is about. FLOORS, as ``SHAPE_FLOOR`` is: the counts on
#: the vocabulary of 2026-09-28 are in each test's docstring.
REFUSAL_FLOOR = 40
ODD_ROTATION_FLOOR = 20
ODD_TEXT_FLOOR = 50

#: Every text param the compile emits, by its path in the compiled dict.
#: Each must be a str (#362 item 2): copied verbatim, a JSON NaN in a
#: TARGET's name reached the route's answer, which cannot render it.
TEXT_PATHS = frozenset({
    ".targets[].name", ".targets[].ra", ".targets[].dec",
    ".targets[].steps[].filter", ".targets[].steps[].slots[].filter",
    ".targets[].mosaic.fov_from", ".targets[].mosaic.order",
    ".targets[].mosaic.frame_anchor", ".targets[].frame_anchor",
    ".automation.dusk_flats.method", ".automation.dusk_flats.window"})


def _leaves(value, path=""):
    """``(path, leaf)`` for every leaf of a compiled dict, lists written
    ``[]`` so one path names the key in every entry."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _leaves(v, f"{path}.{k}")
    elif isinstance(value, list):
        for v in value:
            yield from _leaves(v, f"{path}[]")
    else:
        yield path, value


def _odd_number(value) -> bool:
    return isinstance(value, float) and not math.isfinite(value)


def _plan_refused(raw: dict) -> str:
    g = _g(raw)
    with pytest.raises(GraphNotRunnable) as caught:
        to_sequence_plan(compile_plan(g, "n"), g, flow_id="f-362")
    return str(caught.value)


def _m42(capture: dict | None = None, **target) -> dict:
    """TARGET M42 -> CAPTURE LOOP, the capture's params over a sound L 60."""
    params = {"name": "M42", "ra": "05h 35m 17s", "dec": "-05 23 28"}
    params.update(target)
    return {"nodes": [{"id": "t", "type": "target", "params": params},
                      {"id": "c", "type": "capture", "x": 200,
                       "params": {"filter": "L", "exposure": 60, "count": 3,
                                  **(capture or {})}}],
            "edges": [{"from": "t", "fromPort": "target", "to": "c",
                       "toPort": "run"}]}


def _with_rule(raw: dict) -> dict:
    """``raw`` with a CONDITION "HFR above" at -1 wired to a REFOCUS: a
    rule whose threshold the plan's ``Instruction`` refuses (at least 0)."""
    return {"nodes": raw["nodes"] + [
                {"id": "k", "type": "condition", "x": 400,
                 "params": {"when": "HFR above", "threshold": -1}},
                {"id": "r", "type": "refocus", "x": 600}],
            "edges": raw["edges"] + [
                {"from": "k", "fromPort": "fire", "to": "r", "toPort": "do"}]}


def _m31_3x2(**over) -> dict:
    """A framed 3x2 of M31 locked at PA 30, owning a FILTER CYCLE with the
    loop wire from it: a mosaic ``to_plan`` expands into six panels."""
    params = {"name": "M31", "ra": "00h 42m 44s", "dec": "+41 16 09",
              "rows": 2, "cols": 3, "overlap": 25, "fovX": 2.0, "fovY": 1.33,
              "angle": "Rotate to PA", "rotation": 30}
    cycle = {"plan": "L 60, R 60"}
    for k, v in over.items():
        (cycle if k in ("gain", "bin") else params)[k] = v
    return {"nodes": [{"id": "t", "type": "target", "params": params},
                      {"id": "cy", "type": "cycle", "x": 200,
                       "params": cycle}],
            "edges": [{"from": "t", "fromPort": "target", "to": "cy",
                       "toPort": "run"},
                      {"from": "cy", "fromPort": "pass", "to": "t",
                       "toPort": "next"}]}


#: A POOL of one member whose hour-angle limit the plan's ``Schedule``
#: refuses (at most 12 h).
_POOL_HA_20 = {"nodes": [{"id": "p", "type": "pool",
                          "params": {"members": "M42", "maxHA": 20}},
                         {"id": "c", "type": "capture", "x": 200,
                          "params": {"exposure": 60, "count": 3}}],
               "edges": [{"from": "p", "fromPort": "target", "to": "c",
                          "toPort": "run"}]}


def _m31_then_m78(**m78) -> dict:
    """The sound 3x2 of M31 above, then TARGET M78 -> CAPTURE LOOP, M78's
    params over its catalogue position. M31's six panels are the plan's
    first six targets, so M78 is its seventh: a refusal of M78's value is
    named by a block that is not the plan's first, which a label read at the
    wrong index cannot name."""
    raw = _m31_3x2()
    params = {"name": "M78", "ra": "05h 46m 46s", "dec": "+00 04 45"}
    params.update(m78)
    raw["nodes"] += [{"id": "t2", "type": "target", "y": 300,
                      "params": params},
                     {"id": "c2", "type": "capture", "x": 200, "y": 300,
                      "params": {"filter": "L", "exposure": 60, "count": 3}}]
    raw["edges"] += [{"from": "t2", "fromPort": "target", "to": "c2",
                      "toPort": "run"}]
    return raw


class TestPastCompilePlan:
    def test_only_graph_not_runnable_leaves_to_sequence_plan(self):
        """#362 item 1: every corpus graph compiled, then made a plan, and
        nothing leaves ``to_sequence_plan`` but ``GraphNotRunnable``, which
        the compile route shows as the plan's danger row and ``/run``
        answers with 422. A value the plan's models refuse (an exposure past
        3600 s, a fractional gain, a count past a step's bound, an hour
        angle past 12) was a pydantic ``ValidationError`` and a 500.

        THE PREMISE, counted in the same walk: at least ``REFUSAL_FLOOR``
        graphs reach the refusal that names the block and the field (50 on
        the vocabulary of 2026-09-28), so a corpus that stopped reaching
        ``SequencePlan``'s validation cannot leave this green. Before #362's
        changes 133 graphs raised here (#362 quotes 131, on the corpus
        before #331 reshuffled it); the compile's finite-only numbers and
        str text, and ``_coords`` dropping a position off the sphere, took
        83 of them away before the plan is validated.

        RED under mutant "to_plan lets ValidationError out" (both of
        ``to_plan``'s catches removed: the one round
        ``SequencePlan.model_validate`` and the one round the mosaic's
        layout), observed:

            AssertionError: 50 of 3000 plans raised: {'ValidationError': 50}; first of each: {'ValidationError': 'graph 20: 4 validation errors for SequencePlan\\ntargets.0.schedule.max_hour_angle_h\\n  Input should be greater than or equal to 0 [type=greater_than_equal, input_'}
        """
        raised: Counter[str] = Counter()
        first: dict[str, str] = {}
        refused = 0
        for i, g in enumerate(_corpus()):
            compiled = compile_plan(g, "fuzz")
            try:
                to_sequence_plan(compiled, g, flow_id=f"f-{i}")
            except GraphNotRunnable as e:
                refused += bool(PLAN_REFUSAL.search(str(e)))
            except Exception as e:      # noqa: BLE001 - the verdict itself
                kind = type(e).__name__
                raised[kind] += 1
                first.setdefault(kind, f"graph {i}: {e!r}"[:160])
        assert not raised, (f"{sum(raised.values())} of {CORPUS_SIZE} plans "
                            f"raised: {dict(raised)}; first of each: {first}")
        assert refused >= REFUSAL_FLOOR, (
            f"premise: only {refused} graphs reached the plan's refusal")

    @pytest.mark.parametrize("raw,block,field,value,bound", [
        (_m42({"exposure": 5000}), "TARGET M42", "steps[0].exposure_s",
         "5000", "3600"),
        (_m42({"gain": 1.5}), "TARGET M42", "steps[0].gain", "1.5",
         "integer"),
        (_m42(ra="30h 00m 00s"), "TARGET M42", "ra_hours", "30.0", "24"),
        (_m31_3x2(ra="30h 00m 00s"), "TARGET M31", "ra_hours", "30.0", "24"),
        (_POOL_HA_20, "TARGET POOL member M42", "schedule.max_hour_angle_h",
         "20", "12"),
        (_with_rule(_m42()), "the rule on_hfr_above -> refocus", "threshold",
         "-1", "0"),
        (_m31_then_m78(ra="30h 00m 00s"), "TARGET M78", "ra_hours", "30.0",
         "24"),
        (_m42({"exposure": 10 ** 300}), "TARGET M42", "steps[0].exposure_s",
         "1" + "0" * 36 + "...", "3600")],
        ids=["exposure-past-3600", "fractional-gain", "ra-past-24h",
             "mosaic-ra-past-24h", "pool-hour-angle-past-12",
             "negative-rule-threshold", "a-later-block-ra-past-24h",
             "a-301-digit-exposure"])
    def test_a_value_the_plan_refuses_names_the_block_and_the_field(
            self, raw, block, field, value, bound):
        """The refusal says which block, which field, what it holds and
        what the plan allows, so the editor's danger row points at the card
        to fix. A mosaic's layout refuses its centre before any panel is
        made (``framing.compute_mosaic``), and names the block the same way.
        The block is found by the refused target's index, so one case puts
        the fault on the plan's seventh target (M78, after M31's six
        panels), and a value whose repr is past 40 characters is cut to its
        first digits.

        RED under mutant "a label read at the wrong index" (the verifier's,
        ``where`` answering ``target_blocks[max(index - 1, 0)]``), on
        a-later-block-ra-past-24h alone, observed:

            E       AssertionError: TARGET M31: ra_hours of 30.0 cannot be used - input should be less than 24.

        RED under mutant "value repr uncut" (the verifier's,
        ``_refused_values``'s 40-character cut removed), on
        a-301-digit-exposure alone, observed (the digits cut here):

            E       AssertionError: TARGET M42: steps[0].exposure_s of 10000000000000000000000000000000000000000000000000000000000000000...0000 cannot be used - input should be less than or equal to 3600.

        RED under mutant "to_plan lets ValidationError out", observed on all
        eight, e.g. a-later-block-ra-past-24h, the plan's seventh target:

            E       pydantic_core._pydantic_core.ValidationError: 1 validation error for SequencePlan
            E       targets.6.ra_hours
            E         Input should be less than 24 [type=less_than, input_value=30.0, input_type=float]

        and exposure-past-3600:

            E       pydantic_core._pydantic_core.ValidationError: 1 validation error for SequencePlan
            E       targets.0.steps.0.exposure_s
            E         Input should be less than or equal to 3600 [type=less_than_equal, input_value=5000, input_type=int]

        and mosaic-ra-past-24h, out of the layout before any panel is made:

            E           pydantic_core._pydantic_core.ValidationError: 1 validation error for MosaicSpecIn
            E           ra_hours
            E             Input should be less than 24 [type=less_than, input_value=30.0, input_type=float]
        """
        text = _plan_refused(raw)
        assert text.startswith(f"{block}: {field} of {value} cannot be used "
                               f"- input should "), text
        assert bound in text

    def test_a_mosaic_says_it_once_for_all_its_panels(self):
        """Six panels share the cycle's steps, and the cycle's two slots
        are a step each, so a fractional gain on the cycle is twelve errors
        and one fault in one place: said once, naming the block and the
        first step, not twelve times.

        RED under mutant "one sentence per error" (the refusal's
        de-duplication removed), observed (the sentence, twelve clauses
        long, cut):

            E       AssertionError: TARGET M31: steps[0].gain of 1.5 cannot be used - input should be a valid integer, got a number with a fractional part. TARGET M31: steps[1].gain of 1.5 cannot be used - ...
            E       assert 12 == 1
        """
        text = _plan_refused(_m31_3x2(gain=1.5))
        assert text.count("gain of 1.5 cannot be used") == 1, text
        assert text.startswith("TARGET M31: steps[0].gain of 1.5 cannot be "
                               "used - input should be a valid integer")

    @pytest.mark.parametrize("ra,dec", [
        ("05h 35m 17s", "+95 00 00"), ("05h 35m 17s", "-90 00 01"),
        ("inf", "-05 23 28"), ("nan", "-05 23 28"), ("05h 35m 17s", "1e999")],
        ids=["dec-past-the-north-pole", "dec-past-the-south-pole", "ra-inf",
             "ra-nan", "dec-1e999"])
    def test_a_typed_position_off_the_sphere_is_dropped(self, ra, dec):
        """A typed RA or Dec that parses to no place on the sphere (not a
        finite number, which "inf" and "nan" parse to through ``float``, or
        a Dec past a pole) is no usable coordinate, the drop text that does
        not parse gets: the block is left out with a danger row and the
        rest of the flow still makes a plan. It is the rule
        ``save_rules.current_anchor`` keeps for "no layout", whose docstring
        says ``to_plan`` drops such a block, and it keeps an infinity out of
        the block's key and its layout.

        RED under mutant "off the sphere kept" (``_coords``'s sphere test
        removed), observed on all five, e.g. dec-past-the-north-pole (the
        plan refuses the whole flow where the block alone should drop):

            E           astrodeck.flows.to_plan.GraphNotRunnable: TARGET M42: dec_deg of 95.0 cannot be used - input should be less than or equal to 90.

        and ra-inf:

            E           astrodeck.flows.to_plan.GraphNotRunnable: TARGET M42: ra_hours of inf cannot be used - input should be less than 24.
        """
        g = _g({"nodes": [
            {"id": "t1", "type": "target",
             "params": {"name": "M42", "ra": ra, "dec": dec}},
            {"id": "c1", "type": "capture", "x": 200,
             "params": {"exposure": 60, "count": 3}},
            {"id": "t2", "type": "target", "y": 300,
             "params": {"name": "M78", "ra": "05h 46m 46s",
                        "dec": "+00 04 45"}},
            {"id": "c2", "type": "capture", "x": 200, "y": 300,
             "params": {"exposure": 60, "count": 3}}],
            "edges": [{"from": "t1", "fromPort": "target", "to": "c1",
                       "toPort": "run"},
                      {"from": "t2", "fromPort": "target", "to": "c2",
                       "toPort": "run"}]})
        plan, unmapped = to_sequence_plan(compile_plan(g, "n"), g,
                                          flow_id="f-362")
        assert [t.name for t in plan.targets] == ["M78"]
        (row,) = [u for u in unmapped if u["key"] == "targets[M42]"]
        assert row["level"] == "danger"
        assert row["detail"].startswith("dropped: no usable coordinates")

    @pytest.mark.parametrize("dec", ["+90 00 00", "-90 00 00"],
                             ids=["north-pole", "south-pole"])
    def test_control_a_pole_is_on_the_sphere(self, dec):
        """CONTROL: a Dec of exactly +90 or -90 is a place, and is kept.
        Green on the code and under "off the sphere kept"."""
        g = _g(_m42(dec=dec))
        plan, _ = to_sequence_plan(compile_plan(g, "n"), g, flow_id="f-362")
        assert [t.dec_deg for t in plan.targets] == [float(dec[:3])]

    def test_control_a_sound_plan_is_not_refused(self):
        """CONTROL: the M42 target, the 3x2 and the pool above, each with
        nothing odd (the pool's hour angle at its card's 4), build their
        plans. Green on the code and under every mutant in this class."""
        pool = json.loads(json.dumps(_POOL_HA_20))
        pool["nodes"][0]["params"]["maxHA"] = 4
        for raw in (_m42(), _m31_3x2(), pool):
            g = _g(raw)
            plan, _ = to_sequence_plan(compile_plan(g, "n"), g, flow_id="f")
            assert plan.targets

    def test_every_number_is_finite_and_every_text_a_str(self):
        """#362 item 2: the compile route returns ``compile_plan``'s dict
        as JSON with ``allow_nan=False``, so one infinity or NaN anywhere in
        it was a 500. Every number the compile emits is read finite-only now
        (a value that is not a finite number reads as text that is no number
        always read), and every text param is a str, so every corpus graph's
        compile renders. The paths are named on failure, with the first
        graph each was seen on.

        THE PREMISE: at least ``ODD_ROTATION_FLOOR`` graphs carry a TARGET
        rotation that is a float infinity or NaN (the mutant's shape, which
        JSON cannot carry: 40 on the vocabulary of 2026-09-28), and
        ``ODD_TEXT_FLOOR`` a text param of a TARGET or a CAPTURE that is not
        a str (887). Before #362, 764 of the 3000 compiles could not be JSON,
        at 27 paths.

        RED under mutant "rotation copied verbatim" (``_target_entry``'s
        ``rotation_deg`` back to ``params.get("rotation")``), observed:

            E       AssertionError: 40 compiles cannot be JSON: numbers that are not finite at {'.targets[].rotation_deg': 'graph 26 (x40)'}; text that is not a str at {}

        Two more mutants of one reading each, red the same way, naming
        their paths: "threshold read by _num" (a rule's ``threshold`` back
        to ``_num``):

            E       AssertionError: 6 compiles cannot be JSON: numbers that are not finite at {'.instructions[].threshold': 'graph 1125 (x6)'}; text that is not a str at {}

        and "filter copied verbatim" (a CAPTURE's ``filter`` back to
        ``n.params.get("filter")``):

            E       AssertionError: 6 compiles cannot be JSON: numbers that are not finite at {'.targets[].steps[].filter': 'graph 103 (x6)'}; text that is not a str at {'.targets[].steps[].filter': 'graph 43 (x59)'}
        """
        rotation, text_param, not_json = 0, 0, 0
        numbers: dict[str, list] = {}
        texts: dict[str, list] = {}
        for i, g in enumerate(_corpus()):
            rotation += any(n.type == "target"
                            and _odd_number(n.params.get("rotation"))
                            for n in g.nodes)
            text_param += any(
                not isinstance(n.params.get(k, ""), str)
                for n in g.nodes for k in ("name", "ra", "dec", "filter")
                if n.type in ("target", "capture"))
            compiled = compile_plan(g, "fuzz")
            for path, leaf in _leaves(compiled):
                if _odd_number(leaf):
                    numbers.setdefault(path, []).append(i)
                if path in TEXT_PATHS and not isinstance(leaf, str):
                    texts.setdefault(path, []).append(i)
            try:
                json.dumps(compiled, allow_nan=False)
            except ValueError:
                not_json += 1
        assert (rotation >= ODD_ROTATION_FLOOR
                and text_param >= ODD_TEXT_FLOOR), \
            f"premise: {rotation} odd rotations, {text_param} odd texts"

        def say(found: dict[str, list]) -> dict[str, str]:
            return {p: f"graph {g[0]} (x{len(g)})"
                    for p, g in sorted(found.items())}
        assert not (numbers or texts or not_json), (
            f"{not_json} compiles cannot be JSON: numbers that are not finite "
            f"at {say(numbers)}; text that is not a str at {say(texts)}")

    @pytest.mark.parametrize("value,reads", [
        (float("nan"), ""), (float("inf"), ""), (12.5, "12.5"),
        (None, ""), (0, "0"), ([], ""), ("  M 31 ", "  M 31 "),
        (True, ""), ([1, 2], ""), (10 ** 400, "")],
        ids=["nan", "inf", "a-number", "null", "zero", "empty-list",
             "text-as-typed", "true", "a-list", "400-digits"])
    def test_a_text_param_is_its_text(self, value, reads):
        """How a text param that is not text reads (S7 orchestrator ruling
        6, ``compile._text``): a finite number is its text, 0 included;
        anything else that is not text (null, a bool, NaN, an infinity, a
        list, an integer no float holds) is blank; and text is copied as
        typed, untrimmed, so the PLAN tab shows what the card holds.

        DELIBERATE PIN CHANGE (ruling 6, #387's residual). S5 pinned "a value
        is its ``str``, a falsy one (null, 0, an empty list) is blank", so a
        NaN compiled to "nan" and 0 to "". That reading made an RA of the
        number 0 blank and an RA of NaN typed text that did not parse, while
        ``identity.typed_coordinates`` on the node and the modal's mirror
        read them otherwise (``test_flows_typed_coordinates.py``). The S5 pin
        run against the ruling-6 code, observed:

            E       AssertionError: assert '' == 'nan'
            E       AssertionError: assert '' == 'inf'
            E       AssertionError: assert '0' == ''

        RED under mutant "_text blanks 0" (``compile._text`` blanking a 0
        as S5 did and nothing else, ``str(v) if finite_number(v) and v
        else ""``), observed:

            E       AssertionError: assert '' == '0'
            FAILED tests/test_flows_compile_never_raises.py::TestPastCompilePlan::test_a_text_param_is_its_text[zero]
            1 failed, 9 passed, 114 deselected

        RED under mutant "_text as S5" (the whole S5 reading, ``str(v) if
        v else ""``; re-run by the S7-COMPILE verifier) on six of the ten:
        nan, inf, zero, true, a-list and 400-digits, e.g.:

            E       AssertionError: assert '[1, 2]' == ''

        RED under mutant "name copied verbatim" (``_target_entry``'s
        ``name`` back to ``params.get("name")``), observed (S5) on six of
        the seven cases it then had (text-as-typed stays green, as a
        control), e.g.:

            E       AssertionError: assert nan == 'nan'
            E       AssertionError: assert 12.5 == '12.5'
            E       AssertionError: assert None == ''
        """
        g = _g({"nodes": [{"id": "t", "type": "target",
                           "params": {"name": value}}]})
        (entry,) = compile_plan(g, "n")["targets"]
        assert entry["name"] == reads

    @pytest.mark.parametrize("where", ["dome-timeout", "target-rotation"])
    def test_a_400_digit_integer_raises_nothing(self, where):
        """#362 item 3: ``float()`` of an integer past a float's range
        raises ``OverflowError``, not ``ValueError``, and two readers on the
        compile and doctor path caught ``ValueError`` only. A DOME's timeout
        that is no number reads as the 120 s default, and a TARGET's
        rotation that is no number as -1, any angle, as text always has.
        Only a raw POST or a hand-edited file carries one.

        RED under mutant "DomePolicy catches ValueError only"
        (``devices.base._shutter_timeout``, the one reader behind
        ``from_node_params`` and ``from_plan``, catching ``(TypeError,
        ValueError)``) on dome-timeout, observed, raised out of
        ``compile_plan`` through ``DomePolicy.from_node_params``:

            E           OverflowError: int too large to convert to float

        RED under mutant "_rotation_deg catches ValueError only" on
        target-rotation, observed, raised out of ``compile_plan`` through
        ``_target_entry``, ``angle_code`` and ``nodes.target_angle``:

            E           OverflowError: int too large to convert to float
        """
        huge = 10 ** 400
        raw = _m42()
        if where == "dome-timeout":
            raw["nodes"] += [{"id": "dm", "type": "dome", "x": 400,
                              "params": {"timeout": huge}},
                             {"id": "sf", "type": "safety", "x": 600}]
        else:
            raw["nodes"][0]["params"].update(rotation=huge, rows=2, cols=3,
                                             fovX=2.0, fovY=1.33)
        g = _g(raw)
        compiled = compile_plan(g, "n")
        issues = doctor.check(g, rig=GUARDS_OFF)
        if where == "dome-timeout":
            assert compiled["automation"]["dome"]["shutter_timeout_s"] == \
                DEFAULT_SHUTTER_TIMEOUT_S
        else:
            (entry,) = compiled["targets"]
            assert (entry["rotation_deg"], entry["angle"]) == (-1, "any")
            assert target_angle(g.nodes[0].params) == "Any angle"
            # A mosaic with no angle is M2, a danger, where it raised.
            assert any("laid out at one camera angle" in i.text
                       for i in issues)

    @pytest.mark.parametrize("value", ["inf", "-inf", "nan", float("inf"),
                                       float("nan"), 0, -5, "abc", 10 ** 400],
                             ids=["inf", "-inf", "nan", "float-inf",
                                  "float-nan", "0", "-5", "text", "huge"])
    @pytest.mark.parametrize("reader", ["node", "plan"])
    def test_a_timeout_that_is_no_finite_number_is_the_default(self, reader,
                                                               value):
        """The DOME timeout is one of the numbers item 2 found non-finite in
        the compiled dict (``automation.dome.shutter_timeout_s``): "inf"
        passed the ``<= 0`` test and a NaN fails every comparison, so both
        were kept. Read by ``DomePolicy`` from the node and from a compiled
        plan alike, anything that is not a finite number above 0 is the 120 s
        default, which ``open_and_confirm`` can wait out.

        RED under mutant "a non-finite timeout kept" (``_shutter_timeout``'s
        ``not math.isfinite(timeout) or`` removed), observed on the eight
        infinity and NaN cases, node and plan alike, e.g. node-inf and
        node-nan:

            E       assert inf == 120.0
            E        +  where inf = DomePolicy(bind_to_mount=True, shutter_timeout_s=inf).shutter_timeout_s
            E       assert nan == 120.0
            E        +  where nan = DomePolicy(bind_to_mount=True, shutter_timeout_s=nan).shutter_timeout_s

        RED under mutant "DomePolicy catches ValueError only", observed on
        node-huge and plan-huge (and on nothing else here):

            E           OverflowError: int too large to convert to float
        """
        from astrodeck.devices.base import DomePolicy
        policy = (DomePolicy.from_node_params({"timeout": value})
                  if reader == "node"
                  else DomePolicy.from_plan({"shutter_timeout_s": value}))
        assert policy.shutter_timeout_s == DEFAULT_SHUTTER_TIMEOUT_S

    @pytest.mark.parametrize("value,seconds", [(90, 90.0), ("45", 45.0),
                                               (1e308, 1e308)])
    def test_control_a_finite_timeout_is_kept(self, value, seconds):
        """CONTROL: a finite timeout above 0 is the operator's, from the node
        and from a plan. Green on the code and under every timeout mutant."""
        from astrodeck.devices.base import DomePolicy
        assert DomePolicy.from_node_params(
            {"timeout": value}).shutter_timeout_s == seconds
        assert DomePolicy.from_plan(
            {"shutter_timeout_s": value}).shutter_timeout_s == seconds


# ------------------------------------------------ the #441 digit runs (S7)

#: A run of decimal digits past CPython's 4300-digit integer string limit,
#: on which ``int()`` raises ``ValueError``.
RUN_PAST_THE_LIMIT = "9" * 5000

#: #441's skip entry: row 1, and a column of 5000 digits.
SKIP_PAST_THE_LIMIT = "1-" + "1" * 5000


def _cycle_of(plan: str) -> dict:
    """TARGET M42 -> FILTER CYCLE with ``plan`` as its slot table: #441's
    first graph when a slot's exposure is a run past the limit."""
    return {"nodes": [{"id": "t", "type": "target",
                       "params": {"name": "M42", "ra": "05h 35m 17s",
                                  "dec": "-05 23 28"}},
                      {"id": "cy", "type": "cycle", "x": 200,
                       "params": {"plan": plan}}],
            "edges": [{"from": "t", "fromPort": "target", "to": "cy",
                       "toPort": "run"}]}


class TestThe441DigitRuns:
    """#441's two graphs, joined to this file as the 400-digit integer was:
    by hand, not in ``ODD``, because a longer ``ODD`` would reshuffle every
    graph the quoted counts above were observed on. Validation takes both,
    so a save stores them; before S7 every compile of either raised
    ``ValueError`` out of ``compile_plan`` and the doctor. Now a slot whose
    exposure ``int()`` refuses is dropped (``nodes.parse_cycle_plan``) and a
    skip entry it refuses is unread (``compile.parse_skip``), and what the
    operator reads is a note, never a 500."""

    @pytest.mark.parametrize("reader", ["compile_plan", "doctor"])
    @pytest.mark.parametrize("raw", [
        _cycle_of("L " + RUN_PAST_THE_LIMIT),
        _cycle_of(f"L 60, R {RUN_PAST_THE_LIMIT}"),
        _m31_3x2(skip=SKIP_PAST_THE_LIMIT)],
        ids=["cycle-only-slot", "cycle-one-slot-of-two", "skip"])
    def test_compile_plan_and_the_doctor_answer(self, raw, reader):
        """Each graph is valid (the premise: a save stores it), compiles,
        and the doctor answers, M9's second compile included; each reader
        asked on its own, so a doctor that raised could not hide behind a
        compile that raised first.

        RED under nodes.py mutant "guard removed" (``parse_cycle_plan``'s
        ``int()`` unguarded, as #441 found it), observed on both cycle
        graphs through both readers (the skip graph stays green), e.g.:

            >           doctor.check(g, rig=GUARDS_OFF)
            >               out.append((m.group(1), int(m.group(2))))
            E               ValueError: Exceeds the limit (4300 digits) for integer string conversion: value has 5000 digits; use sys.set_int_max_str_digits() to increase the limit
            5 failed, 3 passed, 119 deselected (the four cycle cases
            here and ``test_the_slot_nobody_can_read_is_dropped_and_said``)

        RED under compile.py mutant "guard removed" (``parse_skip``'s
        ``int()`` pair unguarded), observed on the skip graph through
        ``compile_plan`` alone. The doctor reads no skip for this graph
        (#441 found only ``compile_plan`` raising on it), so skip-doctor
        stays green under it:

            >           r, c = int(m.group(1)), int(m.group(2))
            E           ValueError: Exceeds the limit (4300 digits) for integer string conversion: value has 5000 digits; use sys.set_int_max_str_digits() to increase the limit
            FAILED ...::test_compile_plan_and_the_doctor_answer[skip-compile_plan]
            FAILED ...::test_the_skip_entry_nobody_can_read_is_a_note
            2 failed, 6 passed, 119 deselected
        """
        g = _g(raw)
        assert g.validation_errors() == [], "premise: a save stores it"
        if reader == "compile_plan":
            compile_plan(g, "n")
        else:
            doctor.check(g, rig=GUARDS_OFF)

    def test_the_slot_nobody_can_read_is_dropped_and_said(self):
        """A cycle of one slot past the limit compiles to no slots, and the
        plan refuses it in words (``to_plan``'s "no filters selected"), as
        it refuses a table of nothing readable; beside a readable slot, only
        the readable one is shot."""
        (entry,) = compile_plan(_g(_cycle_of("L " + RUN_PAST_THE_LIMIT)),
                                "n")["targets"]
        assert entry["steps"][0]["slots"] == []
        refused = _plan_refused(_cycle_of("L " + RUN_PAST_THE_LIMIT))
        assert "the FILTER CYCLE has no filters selected" in refused, refused
        (entry,) = compile_plan(_g(_cycle_of(
            f"L 60, R {RUN_PAST_THE_LIMIT}")), "n")["targets"]
        assert entry["steps"][0]["slots"] == [{"filter": "L",
                                               "exposure_s": 60}]

    def test_the_skip_entry_nobody_can_read_is_a_note(self):
        """The entry skips nothing and the compile says so in the note every
        unread entry gets, naming the grid; the other five panels' plan is
        untouched."""
        out = compile_plan(_g(_m31_3x2(skip=SKIP_PAST_THE_LIMIT)), "n")
        (entry,) = out["targets"]
        assert entry["mosaic"]["skip"] == []
        notes = [n for n in out["notes"] if n.get("node_id") == "t"
                 and "names no panel of this grid" in n.get("text", "")]
        assert len(notes) == 1 and notes[0]["level"] == "warn", out["notes"]
        assert SKIP_PAST_THE_LIMIT in notes[0]["text"]
