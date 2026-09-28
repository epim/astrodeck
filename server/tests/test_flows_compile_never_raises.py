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
* the compile route: each #328 graph answers 200 with its problems listed.

MUTANTS were run from byte backups in a private copy of ``server/`` under the
session scratchpad (``s4-compile-mut``, and the verifier's
``s4-compile-verify-mut`` for the cases it added), never in the shared tree;
each observed failure is quoted in the test it turned red.
"""
from __future__ import annotations

import json
import math
import random
from collections import Counter

import pytest

from astrodeck.flows import doctor
from astrodeck.flows.compile import compile_plan, flow_order
from astrodeck.flows.models import FlowGraph
from astrodeck.flows.nodes import NODE_DEFS, port_kind
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

        Kept out of the corpus: two readers outside this change raise on it
        too (``DomePolicy.from_node_params``'s ``timeout`` and
        ``nodes._rotation_deg``), so the corpus would grade them.

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
