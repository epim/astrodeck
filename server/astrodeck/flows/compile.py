# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Graph → plan. The one function that decides what a flow actually runs.

Transcribed from ``flowOrder()`` + ``compilePlan()`` in the prototype (README
§"Compile output"). Two passes over two different edge sets, because the graph
has two lanes and they mean different things:

* the FLOW lane is walked in topological order and becomes ``targets`` — the run
  cursor's itinerary, which is what a ``SequencePlan`` is;
* every EVENT edge becomes one ``instructions`` entry — a when/then rule, which
  is what an ``Instruction`` is.

DETERMINISTIC, and that is load-bearing: the Tonight panel's timeline is
described in the README as "a rendering of the compile, never a separate truth",
and the PLAN tab shows this output verbatim. If compiling twice could give two
answers, the timeline and the run would disagree and neither would be wrong.
Ties in the topological walk are broken by canvas position (x, then y), so two
stages with no ordering between them still come out in the order they are read
on screen.

WHAT THIS DELIBERATELY DOES NOT DO: invent. Every field below comes from a node
parameter or from a fixed literal the README specifies. A graph cannot express
a capability the server lacks, and the compiler is where that stops being an
aspiration — if a node had no mapping, the right outcome is a missing key that
the doctor already complained about, not a plausible default that makes an
unrunnable night look runnable.
"""
from __future__ import annotations

import math
import re

from ..devices.base import DomePolicy
from .identity import finite_number
from .models import FlowEdge, FlowGraph, FlowNode
from .nodes import NODE_DEFS, parse_cycle_plan, port_kind, target_angle

#: Trigger vocabulary, and what the engine can now do with it.
#:
#: HISTORY WORTH KEEPING, because this comment has been wrong in both
#: directions. It first claimed the README's additive widening was already in
#: place when it was not. That was corrected to say the widening had not
#: happened. As of 2026-08-12 it HAS: ``sequence/models.py`` carries
#: ``on_clouds_in``, ``on_clouds_clear``, ``on_unsafe`` and ``on_panel_ready``,
#: ``TriggerContext`` carries the tri-state readings they evaluate against, and
#: the engine holds for weather and releases itself.
#:
#: The lesson is not about clouds. A comment that states what ANOTHER module
#: contains is a claim nothing keeps, and this one was wrong twice before it was
#: right. The durable version is the test: ``to_plan.LEGAL_TRIGGERS`` reads
#: ``TriggerKind.__args__`` rather than restating it, so the vocabulary cannot
#: drift from the engine's without a test failing.
#:
#: WHAT IS STILL NOT RUNNABLE: ``on_frame_graded`` and the ``type.port``
#: fallback have no engine trigger, and ``calib``/``report`` have no engine
#: action. Those are still emitted - the compiled dict is the README's
#: documented contract and the PLAN tab renders it verbatim, so silently
#: dropping a rule the operator drew would be worse than emitting one the engine
#: has yet to learn. ``flows/to_plan.py`` is where that gap is made visible.
TRIGGER_CLOUDS_IN = "on_clouds_in"
TRIGGER_CLOUDS_CLEAR = "on_clouds_clear"


def flow_order(graph: FlowGraph) -> list[FlowNode]:
    """The flow lane, topologically sorted — the run cursor's itinerary.

    Only nodes that HAVE a flow port take part. A pure event node (a cloud
    watcher, a notify sink) is not on the cursor's path and including it would
    put a rule in the middle of the sequence.

    Kahn's algorithm, with the ready-set ordered by canvas x then y. That
    tie-break is the difference between a deterministic compile and one that
    depends on dict ordering: two independent stages must come out in the order
    the operator SEES, because that is the order they will expect the night to
    run in.

    A cycle simply stops the walk — the nodes in it are dropped rather than
    looped forever. THE EDITOR CAN DRAW ONE. This docstring used to say it
    could not ("one wire per input, and a cursor cannot revisit"), but one wire
    per input does not stop a back-edge: dragging FILTER CYCLE ``complete``
    onto TARGET ``arm`` replaces the TARGET's dusk wire with a loop, and this
    walk then dropped every looped stage while the doctor stayed clean (#149).
    ``FlowGraph.validation_errors`` now refuses a loop by name: save and
    ``/run`` answer 422, and both compile routes list it under ``structural``
    beside the plan. Those routes still compile the graph (an editor's graph is
    half-built by nature), as would any caller that skips validation, and for
    whatever reaches here with a loop, dropping it stays the fail-closed
    reading.

    A WIRE IS A STEP OF THE CURSOR ONLY WHEN IT ENTERS A FLOW INPUT (#328),
    as the lane functions read it (``_flow_wire``). Read by its source alone,
    a flow wire into a node with no flow port at all (a FLAT PANEL, an event
    sink) was counted into no in-degree and then decremented out of one, a
    ``KeyError`` and a 500 from the editor's compile; and one into an event
    input of a node that does have a flow input (FILTER CYCLE 'all done'
    onto TARGET 'next panel') made the TARGET wait on its own stage, so the
    draft compiled to no target at all. Validation refuses both shapes by
    name, and a graph it accepts has no other kind of flow wire, so every
    saved flow walks as it did.
    """
    flow_edges = [e for e in graph.edges
                  if port_kind(_type_of(graph, e.from_), e.fromPort, "out") == "flow"
                  and port_kind(_type_of(graph, e.to), e.toPort, "in") == "flow"]
    nodes = [n for n in graph.nodes if _has_flow_port(n)]
    indeg = {n.id: 0 for n in nodes}
    for e in flow_edges:
        if e.to in indeg:
            indeg[e.to] += 1
    ready = sorted([n for n in nodes if indeg[n.id] == 0], key=lambda n: (n.x, n.y))
    out: list[FlowNode] = []
    seen: set[str] = set()
    while ready:
        n = ready.pop(0)
        if n.id in seen:
            continue
        seen.add(n.id)
        out.append(n)
        for e in [x for x in flow_edges if x.from_ == n.id]:
            m = graph.node(e.to)
            if m is None or m.id in seen:
                continue
            indeg[m.id] -= 1
            if indeg[m.id] <= 0:
                ready.append(m)
        ready.sort(key=lambda x: (x.x, x.y))
    return out


def _type_of(graph: FlowGraph, node_id: str) -> str:
    n = graph.node(node_id)
    return "" if n is None else n.type


def _has_flow_port(node: FlowNode) -> bool:
    from .nodes import NODE_DEFS
    d = NODE_DEFS.get(node.type)
    if d is None:
        return False
    return any(p.kind == "flow" for p in (*d.ins, *d.outs))


def _num(v, default=0):
    # OverflowError too: a JSON integer past a float's range (a raw POST can
    # hold a 400-digit one) is no number this compile can read, and
    # ``float()`` of it raises that, not ValueError.
    try:
        f = float(v)
    except (TypeError, ValueError, OverflowError):
        return default
    if not f.is_integer():
        return f
    # TRY ``int(v)`` FIRST, NOT ``int(f)`` ALONE (#547, and the regression a
    # first fix here made: a 301-digit exposure that WAS read exactly,
    # digit for digit, coming back rounded through float64 instead).
    # ``int()`` is EXACT for any plain integer text, of any length -
    # Python's ints are arbitrary precision - which is what a 301-digit
    # value stored as text depends on: ``float(v)`` above already collapsed
    # it to ~17 significant digits, so ``int(f)`` would hand back a
    # DIFFERENT huge number, silently, the same "a claim nothing keeps"
    # shape #547 is itself about. ``int(v)`` only raises here for
    # WHOLE-NUMBER TEXT WITH A DECIMAL POINT OR AN EXPONENT ("-30.0",
    # "10.0", "1e1") — ``int()`` never parses either, only ``float()``
    # does — which is #547's actual bug: the old code took that path
    # unconditionally (``int(v) if float(v).is_integer() else float(v)``),
    # so EVERY float-shaped whole number, however small, hit the
    # exception and silently became ``default``. Only THAT case falls
    # through to ``int(f)``, and only such text is short enough for the
    # float round trip to be exact.
    try:
        return int(v)
    except (TypeError, ValueError):
        return int(f)


def _finite(v, default=0):
    """``_num``, with anything that is not a FINITE number read as
    ``default``: the one reading of every number ``compile_plan`` emits
    (#328 for the counts, #362 for the rest).

    ``_num`` hands back an infinity or a NaN for "inf" and "nan", and those
    are numbers ``int()`` raises on (``OverflowError``, ``ValueError``) and
    JSON cannot carry at all, so a count typed as one turned the editor's
    compile into a 500, and a saved flow holding one could no longer be
    compiled, previewed or run. Validation now refuses such a count at the
    save (``FlowGraph.validation_errors``); this is for the drafts the
    compile routes see before any save, and for a file written by hand.

    NOT ONLY THE COUNTS (#362 item 2). The route renders the compiled dict
    with ``allow_nan=False``, so an infinite rotation, centring tolerance,
    camera field or rule threshold was a 500 just as a count was: 764 of
    the #328 corpus's 3000 compiles held one, at 27 paths. Every number is
    read here now, each with the default its ``_num`` call always had, so
    "inf" reads exactly as "abc" does. A finite value is ``_num``'s
    exactly, so every flow that compiled before compiles to the same plan.
    ``default`` may be None, the centring's and the grid's "not a number",
    which ``to_plan`` leaves to the hub or refuses."""
    value = _num(v, default)
    if isinstance(value, (int, float)) and math.isfinite(value):
        return value
    return default


def _text(v) -> str:
    """A text param as the compile emits it: always a str (#362 item 2).

    COPIED VERBATIM, a TARGET's name, RA or Dec, a CAPTURE's filter and
    DUSK FLATS' method and window reached the compiled dict as whatever
    the node held, and a raw POST can hold a JSON NaN, which the route
    cannot render, or a number, which the plan's ``filter: str`` refuses.
    Text is kept exactly as typed, untrimmed, because the PLAN tab shows
    the compile verbatim and the readers do their own trimming
    (``identity.typed_coordinates``, ``to_plan``'s name).

    A FINITE NUMBER IS ITS TEXT, 0 INCLUDED; ANYTHING ELSE IS BLANK (S7
    orchestrator ruling 6, #387's residual). This is ``identity._typed``'s
    reading, so the compiled entry is typed exactly when the node it came
    from is: the doctor and the save's anchor read the node, the run reads
    the entry. Until S7 a falsy value was blank and any other its ``str``,
    so an RA of the number 0 compiled to "" and was placed by its name
    although RA 0h is a real coordinate, and ``True``, a NaN or a list
    compiled to typed text ("True", "nan", "[1, 2]") that did not parse,
    which dropped a block the node's own reading placed by its name. A
    filter, a method or a window of such a value is blank too, as one the
    operator left empty is."""
    if isinstance(v, str):
        return v
    return str(v) if finite_number(v) else ""


# ------------------------------------------------------------ the panel lane
#
# Spec 2026-09-23 (flows mosaic) 1.5 and D2, #151, #189. A mosaic is one TARGET
# block whose stages are shot on every panel, so the compile has to know which
# stages are the block's. The canvas-order rule (every stage goes to every
# target seen earlier) cannot say: a CAPTURE drawn after a second TARGET would
# be shot on all six M31 panels too. These functions answer it from the wires,
# and they are pure, because compile, to_plan and the doctor (M3, M4, M12, M13)
# all ask the same question and must get one answer.
#
# NOT ONLY MOSAICS (backlog WP-19(a), #151's own general case). The leak these
# functions were built to stop - a stage going to whichever target canvas
# order happened to place it near, rather than the one its wires lead to - is
# exactly as real with two ordinary TARGETs and no grid at all. The switch
# that turns this scoping on, ``needs_wire_scoping``, reads that; the lane
# functions themselves never cared whether a block was a mosaic to begin with.

#: LANE NODES (spec 1.5 item 1): the stages that happen TO a panel. AUTOFOCUS
#: and GUIDE are in because their effect is re-established on every hop (5.6).
#: Legacy SLEW is in because every saved flow and every Example still draws it
#: between the TARGET and its stages (1.7); if it ended the lane, all of those
#: stages would belong to no target. Every other node with a flow port (DOME,
#: DUSK FLATS, REPORT, DUSK, a TARGET or a POOL) ends a lane.
LANE_TYPES: frozenset[str] = frozenset(
    {"autofocus", "guide", "slew", "capture", "cycle"})

#: The blocks a lane hangs off.
OWNER_TYPES: frozenset[str] = frozenset({"target", "pool"})

#: The loop wire's two ends (D2): ``<tail>.pass -> <block>.next``. MATCHED AS
#: STRINGS, not through ``port_kind``, so the lane reads the same graph whether
#: or not the node vocabulary declares the two ports yet, and a wire the
#: operator drew is never ignored because of which build is reading it.
PASS_PORT = "pass"
NEXT_PORT = "next"

#: The node types that DECLARE a ``pass`` output, read off the vocabulary so
#: a type that gains the port is one here at once, as AUTOFOCUS and GUIDE did
#: (S4, #331): CAPTURE LOOP, FILTER CYCLE, AUTOFOCUS and GUIDE, every lane
#: type but legacy SLEW. The lane functions below match the port as a string
#: and never read this. It is for the two answers about what a pass wire
#: means once the vocabulary gives it a meaning: ``_trigger_for``'s name for
#: one that is not the loop, and the one-panel consumption
#: (``one_panel_pass_wires``).
PASS_TYPES: frozenset[str] = frozenset(
    t for t, d in NODE_DEFS.items() if d.port(PASS_PORT, "out") is not None)


#: The Sun altitude each sun-based Start choice means, degrees below the
#: horizon (backlog WP-09, #191): the standard astronomical/nautical/civil
#: twilight figures, matching ``catalog.visibility.ASTRO_DARK_DEG`` /
#: ``NAUTICAL_DARK_DEG`` and ``dawn_park.CIVIL_TWILIGHT_DEG``. Not imported
#: from those modules: this compile owns no dependency on the catalog or on
#: dawn_park, and the three figures are fixed astronomical constants, not a
#: number this compile invents.
_DUSK_START_TWILIGHT_DEG = {
    "Astro dusk": -18.0,
    "Nautical dusk": -12.0,
    "Civil dusk": -6.0,
}


def _dusk_schedule(dusk: FlowNode, notes: list[dict]) -> dict:
    """The ``schedule`` dict one DUSK WINDOW block compiles to (#191).

    START. "Clock time" reads as ``start_mode: "time"`` with ``start_time``
    the card's own "HH:MM" text (``schedule._resolve_event_ts`` already
    reads that mode). Every other choice - the three sun-based ones (Astro,
    Nautical, Civil dusk), and anything this build does not recognise, such
    as a hand-edited file - reads as ``"dusk"``, the one this compile has
    always emitted.

    THE THREE SUN-BASED CHOICES NOW DIFFER (backlog WP-09, #191, closing the
    residual S7 left open). Each compiles its own ``twilight_deg``
    (``_DUSK_START_TWILIGHT_DEG``: astro -18, nautical -12, civil -6) onto
    ``Schedule`` (sequence/models.py, which now carries this PER-TARGET
    field), so ``schedule.resolve_window`` resolves this target's dusk AND
    dawn boundaries at its own Sun altitude instead of the rig's single
    ``safety.twilight_deg`` - three actually different arming instants
    instead of three cards that all silently meant "the rig's one setting".
    "Clock time", and anything this build does not recognise (a hand-edited
    file), write no ``twilight_deg`` at all: ``Schedule``'s ``None`` default
    keeps reading as the rig's angle, unchanged, for a Start this table does
    not name. What IS ALSO fixed here: "Clock time" used to compile to that
    same "dusk" mode too, with no field to even hold a time, so picking it
    silently kept the sun-based arming; it now reads as its own mode below.

    STOP. "Dawn" and "Clock time" read as their own ``Schedule`` modes
    (``dawn``/``time``, the latter with ``stop_time``); "None" reads as
    ``"none"``, as it always has, but ``notes`` now gets a warning for it.
    BEFORE THIS, "Clock time" ALSO fell through to ``"none"`` - only the
    literal string "Dawn" was ever matched - so a flow the operator gave a
    real stop time ran with none at all, the same silent drop as the Start
    side, and a Stop of "None" gave no warning that the run would not park
    itself. A Stop of "Dawn" shares the SAME ``twilight_deg`` this Start
    wrote, if any: one target's night has one twilight definition, not a
    different angle at each end of it.

    ``notes`` is the compile's own list (mutated, not returned): the same
    one ``_target_entry``'s skip warning and ``_scope_note`` write into, so
    every compile warning reaches the caller through one list regardless of
    which block raised it.

    KEY ORDER IS PINNED for the four keys this DUSK WINDOW has always
    emitted (test_flows_compile.py's byte-identical guard on every Example
    predating this field): they keep their original order and are never
    conditional. ``twilight_deg``, ``start_time`` and ``stop_time`` are
    appended after them, each only for the choice that needs it, so they
    are simply ABSENT rather than null for every flow that does not - the
    same "not there at all" reading ``mosaic`` gives a 1x1 TARGET (S3's own
    precedent). A flow whose Start is a sun-based choice - which by now
    includes every Example and every flow saved before "Clock time" existed,
    since ``with_defaults()`` fills an unset Start with "Astro dusk" - gains
    ``twilight_deg`` and so no longer compiles the bare four keys; that
    shift is this fix, not a regression of it."""
    start = str(dusk.params.get("start") or "")
    stop = str(dusk.params.get("stop") or "")
    start_mode = "time" if start == "Clock time" else "dusk"
    if stop == "Clock time":
        stop_mode = "time"
    elif stop == "Dawn":
        stop_mode = "dawn"
    else:
        # "None", or anything this build does not recognise: no stop, as a
        # flow saved before "Clock time" existed already reads (back-compat),
        # but now said out loud rather than left for the operator to notice
        # at dawn, imaging on (#191's Impact: dawn park stands off, and
        # nothing else ends a light run at sunrise).
        stop_mode = "none"
        notes.append({"node_id": dusk.id, "level": "warn", "text": (
            "DUSK WINDOW's Stop is None: this run images into daylight and "
            "does not park at dawn.")})
    schedule: dict = {
        "start_mode": start_mode,
        "start_offset_min": _finite(dusk.params.get("offset")),
        "stop_mode": stop_mode,
        "min_altitude_deg": _finite(dusk.params.get("minAlt")),
    }
    twilight_deg = _DUSK_START_TWILIGHT_DEG.get(start)
    if twilight_deg is not None:
        schedule["twilight_deg"] = twilight_deg
    if start_mode == "time":
        schedule["start_time"] = _text(dusk.params.get("startClock"))
    if stop_mode == "time":
        schedule["stop_time"] = _text(dusk.params.get("stopClock"))
    return schedule


def _grid_dim(value) -> int:
    """One side of a TARGET's grid, read defensively (spec 3.1): missing means
    1, the meaning every flow saved before mosaics has. Anything that is not a
    whole number of at least 1 (text, 0, a negative, 1.9, NaN, infinity) also
    reads as 1, so a hand-edited file cannot flip a whole graph's scoping rule
    with a string."""
    v = _num(value, 1)
    return v if isinstance(v, int) and v >= 1 else 1


def is_multi_panel(node: FlowNode) -> bool:
    """True for a TARGET block with more than one panel (rows x cols > 1).

    A POOL has no grid, so rows and cols on one (only a hand-edited file could
    carry them) never make it a mosaic."""
    if node.type != "target":
        return False
    params = node.params or {}
    return _grid_dim(params.get("rows")) * _grid_dim(params.get("cols")) > 1


def needs_wire_scoping(graph: FlowGraph) -> bool:
    """True when ``compile_plan`` must scope stages by their wires rather than
    by canvas order (spec 1.5, #151's general case).

    ANY MULTI-PANEL TARGET (the original mosaic trigger, S3) - a stray stage
    canvas order would add to it becomes a quota multiplied across every
    panel, which is where the leak costs most.

    MORE THAN ONE TARGET OR POOL BLOCK (#151). Canvas order hands every
    capture or cycle stage to every target or pool seen earlier in the walk,
    so with two independent lanes - TARGET A -> CAPTURE a, TARGET B ->
    CAPTURE b - what a target shoots depends on where its card sits rather
    than which wires lead to it: move B's card left of A's capture and A
    gets both captures. The doctor (``flows.doctor``) already reasons about
    a multi-block graph by wire ancestry (``_flow_upstream_types``), so a
    compile that still read canvas order there was answering a different
    question than the one the doctor's clean bill was about.

    A GRAPH WITH AT MOST ONE BLOCK NEEDS NEITHER: there is nothing else a
    stage could leak onto, so turning the switch on would only print notes
    about a flow nothing is wrong with. That is also every flow saved before
    mosaics existed and almost every Example, which is why this stays off
    for them and their compile is untouched, byte for byte."""
    owners = [n for n in graph.nodes if n.type in OWNER_TYPES]
    return len(owners) > 1 or any(is_multi_panel(n) for n in owners)


def _flow_wire(graph_nodes: dict[str, FlowNode], e: FlowEdge
               ) -> tuple[FlowNode, FlowNode] | None:
    """The two ends of ``e`` when it is a FLOW wire at both ends, else None.

    Both ends, not just the source as ``flow_order`` reads it: a wire from a
    flow output into an event input is refused by validation, and treating it
    as a step of the cursor would make the TARGET it enters (through ``next``)
    the child of its own tail."""
    src, dst = graph_nodes.get(e.from_), graph_nodes.get(e.to)
    if src is None or dst is None:
        return None
    if (port_kind(src.type, e.fromPort, "out") != "flow"
            or port_kind(dst.type, e.toPort, "in") != "flow"):
        return None
    return src, dst


def _lane_index(graph: FlowGraph
                ) -> tuple[dict[str, FlowNode], dict[str, list[FlowNode]]]:
    """``(nodes by id, flow parents by child id)``, built once per question.

    Parents are deduplicated by node: the same wire drawn twice from one
    parent is still one chain. A duplicate node id keeps its first node, as
    ``FlowGraph.node`` does."""
    by_id: dict[str, FlowNode] = {}
    for n in graph.nodes:
        by_id.setdefault(n.id, n)
    parents: dict[str, list[FlowNode]] = {}
    for e in graph.edges:
        ends = _flow_wire(by_id, e)
        if ends is None:
            continue
        src, dst = ends
        ups = parents.setdefault(dst.id, [])
        if all(p.id != src.id for p in ups):
            ups.append(src)
    return by_id, parents


def _walk_to_owner(node_id: str, by_id: dict[str, FlowNode],
                   parents: dict[str, list[FlowNode]]
                   ) -> tuple[FlowNode | None, int]:
    """``owner_of``'s walk, plus how many wires it climbed to get there.

    THE SEEN-SET IS WHAT MAKES THIS RETURN. Two stages feeding each other with
    nothing feeding them each have exactly one flow parent, so a walk with no
    memory goes round them for ever. Validation refuses such a loop at save
    and /run, but the compile routes compile half-built graphs, so this walk
    meets whatever an editor holds."""
    node = by_id.get(node_id)
    if node is None or node.type not in LANE_TYPES:
        return None, 0
    seen = {node.id}
    cur, depth = node, 0
    while True:
        ups = parents.get(cur.id, [])
        if len(ups) != 1:
            # No parent: the chain ran out. Two: two answers, and choosing one
            # would hand the stage to a block by edge order. Only a graph built
            # outside the editor has two (validation refuses it).
            return None, 0
        up = ups[0]
        depth += 1
        if up.type in OWNER_TYPES:
            return up, depth
        if up.type not in LANE_TYPES or up.id in seen:
            return None, 0
        seen.add(up.id)
        cur = up


def owner_of(graph: FlowGraph, node_id: str) -> FlowNode | None:
    """The TARGET or POOL a lane node belongs to, or None (spec 1.5 item 3).

    A CHAIN WALK: the node's single flow parent, then that parent's, through
    lane nodes only, to the first TARGET or POOL. It is None when the walk
    reaches any other node type (TARGET -> CYCLE -> DOME -> CAPTURE: DOME ends
    the lane, so the CAPTURE is nobody's, which is M13), runs out of parents,
    or meets two. A node that is not itself a lane node (a DOME, a REPORT, a
    TARGET) is in no lane and is owned by nobody.

    This is NOT the doctor's ``_flow_upstream_types``. That collects every
    type upstream through any node, which answers "did the cursor pass a
    guider?" but cannot name a nearest owner: it would find the TARGET above
    the DOME and hand the CAPTURE to all six panels."""
    return _walk_to_owner(node_id, *_lane_index(graph))[0]


def _lane_members(graph: FlowGraph, owner: FlowNode | str | None
                  ) -> list[tuple[int, FlowNode]]:
    """``(depth, node)`` for every lane node ``owner_of`` gives to ``owner``,
    nearest first, then canvas x, y and id.

    DEFINED BY OWNERSHIP, not by a second forward walk. The spec defines the
    lane as the lane nodes reachable from the block through lane nodes; with
    at most one flow input per node that is the same set. Where a graph built
    outside the editor breaks that (two wires into one stage), a forward walk
    would put a stage in the lane that ``owner_of`` gives to nobody, and the
    lane the doctor checks would not be the lane the compile scopes."""
    oid = owner if isinstance(owner, str) else getattr(owner, "id", None)
    if oid is None:
        return []
    by_id, parents = _lane_index(graph)
    out: list[tuple[int, FlowNode]] = []
    for n in by_id.values():
        up, depth = _walk_to_owner(n.id, by_id, parents)
        if up is not None and up.id == oid:
            out.append((depth, n))
    out.sort(key=lambda dn: (dn[0], dn[1].x, dn[1].y, dn[1].id))
    return out


def panel_lane(graph: FlowGraph, owner: FlowNode | str | None
               ) -> list[FlowNode]:
    """The block's panel lane (spec 1.5 item 2): every lane node it owns,
    nearest first. For an unbranched lane that is chain order."""
    return [n for _, n in _lane_members(graph, owner)]


def lane_branched(graph: FlowGraph, owner: FlowNode | str | None) -> bool:
    """True when the lane is not one chain: the block, or a stage in it, feeds
    two lane nodes (spec 1.5 item 4, M12). Every member has one parent and
    that parent is the block or another member, so the lane is a tree rooted
    at the block, and a tree is a chain exactly when no two members sit at the
    same distance from its root. A sibling that is not a lane node (a DOME off
    the TARGET) is outside the lane and branches nothing."""
    depths = [d for d, _ in _lane_members(graph, owner)]
    return len(depths) != len(set(depths))


def lane_tail(graph: FlowGraph, owner: FlowNode | str | None
              ) -> FlowNode | None:
    """The lane's last stage, the one the loop wire must leave (spec 1.5 items
    4-5): the member whose output feeds no lane node. None for an empty lane,
    and None for a branched one, where "the last stage" has two answers and
    choosing one would put the loop on a stage the operator did not mean.

    Any lane node can be the tail, AUTOFOCUS and GUIDE included. Every lane
    type but legacy SLEW has a ``pass`` output (``PASS_TYPES``; AUTOFOCUS and
    GUIDE since S4, #331), so only a lane ending on a SLEW has no loop wire
    to give."""
    members = _lane_members(graph, owner)
    if not members:
        return None
    depths = [d for d, _ in members]
    if len(depths) != len(set(depths)):
        return None
    return members[-1][1]


def lane_next(graph: FlowGraph, owner: FlowNode | str | None
              ) -> list[FlowNode]:
    """What runs after the block (spec 1.5 item 6, 1.6): the nodes the tail's
    flow output feeds, in canvas order. Never a stage by construction, since a
    lane node wired after the tail is in the lane and is the tail instead.
    Empty when there is no tail."""
    tail = lane_tail(graph, owner)
    if tail is None:
        return []
    by_id, _ = _lane_index(graph)
    out: list[FlowNode] = []
    for e in graph.edges:
        if e.from_ != tail.id:
            continue
        ends = _flow_wire(by_id, e)
        if ends is not None and all(n.id != ends[1].id for n in out):
            out.append(ends[1])
    return sorted(out, key=lambda n: (n.x, n.y, n.id))


def loop_wires(graph: FlowGraph, owner: FlowNode | str | None
               ) -> list[FlowEdge]:
    """The loop wires of a block (D2, spec 1.4 item 1): ``pass`` event wires
    from its lane's TAIL into its own ``next``.

    A pass wire from an earlier stage of the lane is not one (M12: the stages
    after it would be shot once per panel with nothing to say when), and nor
    is one from a stage in another block's lane (M4). Structural only: whether
    the block has more than one panel to rotate between is the caller's
    question (a loop on a 1x1 block is M4's note)."""
    oid = owner if isinstance(owner, str) else getattr(owner, "id", None)
    tail = lane_tail(graph, owner)
    if oid is None or tail is None:
        return []
    return [e for e in graph.edges
            if e.from_ == tail.id and e.fromPort == PASS_PORT
            and e.to == oid and e.toPort == NEXT_PORT]


def _stage_label(node: FlowNode) -> str:
    """How a scope note names a stage: its card label, and a CAPTURE LOOP's
    filter, which is what tells two of them apart on a canvas."""
    if node.type == "capture":
        filt = str(node.params.get("filter") or "").strip()
        return f"CAPTURE LOOP {filt}" if filt else "CAPTURE LOOP"
    d = NODE_DEFS.get(node.type)
    return node.type if d is None else d.label


def _entry_names(entries: list[dict]) -> str:
    names: list[str] = []
    for t in entries:
        name = str(t.get("name") or "").strip() or "an unnamed target"
        if name not in names:
            names.append(name)
    return ", ".join(names)


def _scope_note(stage: FlowNode, owner: FlowNode | None, mine: list[dict],
                others: list[dict]) -> dict:
    """The compile's note for a stage the two rules assign differently (spec
    1.5): which block the wires give it to, and which targets canvas order
    would have added. A stage the wires give to nobody compiles to nothing,
    which canvas order never did, so that one is a warn."""
    stage_name = _stage_label(stage)
    was = _entry_names(others)
    if owner is None or not mine:
        return {"node_id": stage.id, "level": "warn", "text": (
            f"{stage_name} belongs to no TARGET, so it shoots nothing: its "
            f"wires do not lead back to a TARGET or POOL through lane stages "
            f"alone. By canvas order it would have been shot on {was}. Give "
            f"it a TARGET.")}
    block = (f"TARGET {_entry_names(mine)}" if owner.type == "target"
             else f"{NODE_DEFS['pool'].label} ({_entry_names(mine)})")
    return {"node_id": stage.id, "level": "note", "text": (
        f"{stage_name} is shot on {block} only: with more than one TARGET or "
        f"POOL in this flow, a stage belongs to the block its wires lead "
        f"back to. By canvas order it would also have been shot on "
        f"{was}.")}


# ------------------------------------------------- the block's compile entry
#
# Spec 3.2, #189 (U-09), #170, #151. A TARGET node still emits ONE entry, so
# the PLAN tab shows one block as one mosaic, and `to_plan` expands it into
# panels and a group (spec 3.3). Everything below reads a node's params and
# nothing else: no devices, no config, no clock.

#: TARGET's `angle` choices (``nodes.TARGET_ANGLES``) as the compile spells
#: them. Keyed on the stored words, which are never reworded.
ANGLE_CODES: dict[str, str] = {"Any angle": "any", "Rotate to PA": "rotate",
                               "Camera fixed at PA": "fixed"}

#: The `counts` value that asks for accepted subs (``nodes.COUNT_MODES[1]``).
ACCEPTED_SUBS = "Accepted subs"

#: The `ifNotCentred` choice that lets a mosaic panel shoot off its tile.
SHOOT_ANYWAY = "Shoot anyway"

#: One entry of a TARGET's `skip` text: "<row>-<col>", both 1-based.
_SKIP_RE = re.compile(r"^(\d+)\s*-\s*(\d+)$")


def angle_code(params: dict) -> str:
    """``"any"``, ``"rotate"`` or ``"fixed"`` for a TARGET's params (spec 3.2).

    The stored `angle` when it is one this build offers, otherwise the one
    ``nodes.target_angle`` derives from `rotation`: a negative rotation is any
    angle, anything else a PA to rotate to. DERIVED, never defaulted, so a
    block saved before `angle` existed keeps commanding the PA it always
    commanded (ruling 9: the M31 Example stays at Rotate to PA 23.4). A value
    this build does not know (a newer build's fourth choice, a hand edit) is
    read the same way, as the block's rotation alone says it: guessing any of
    the three from unknown words would be inventing."""
    code = ANGLE_CODES.get(str(target_angle(params)).strip())
    if code is None:
        code = ANGLE_CODES[target_angle({"rotation": (params or {})
                                         .get("rotation")})]
    return code


def count_mode_of(params: dict) -> str:
    """``plan.count_mode``'s word for a TARGET's or POOL's `counts` (Revision
    2 ruling 2): "accepted" for "Accepted subs", "attempts" for anything else.

    Anything else, and not only "Every sub taken", because that is the
    MISSING-KEY meaning (spec 3.1): a block saved before the key existed
    counted every sub it took, and so does one whose value this build cannot
    read. The save rewrites the key to "Accepted subs" (ruling 2); the
    compile never does, so a flow nobody has saved compiles as it ran."""
    value = str((params or {}).get("counts") or "").strip().lower()
    return "accepted" if value == ACCEPTED_SUBS.lower() else "attempts"


def parse_skip(text, rows: int, cols: int) -> tuple[list[list[int]], list[str]]:
    """``(skip, unread)`` for a TARGET's `skip` text, e.g. ``"3-1, 3-2"``.

    ``skip`` is ``[[row, col], ...]``, 1-based as the operator writes them,
    each panel once, in grid order. ``unread`` holds every entry that names
    no panel of this grid (not "r-c", a zero, a row past the last), so the
    compile can say so: a skip that silently skipped nothing would shoot a
    panel the operator took out, and one that guessed would take out a panel
    they meant to keep. Commas and semicolons both separate entries.

    AN ENTRY ``int()`` REFUSES IS UNREAD (#441). Past CPython's integer
    string limit (4300 digits, leading zeros counted) ``int()`` raises
    ``ValueError``; unguarded, that reached ``compile_plan`` and Tonight from
    a flow validation lets a save store. Such an entry names no panel, as a
    row past the last names none. The pattern stays as wide as it is, since
    "01-002" is panel 1-2 (``skip_cases.json``), and the modal's mirror
    counts the digits the way ``int()`` does."""
    skip: set[tuple[int, int]] = set()
    unread: list[str] = []
    for chunk in re.split(r"[,;]", str(text or "")):
        token = chunk.strip()
        if not token:
            continue
        m = _SKIP_RE.match(token)
        if m is None:
            unread.append(token)
            continue
        try:
            r, c = int(m.group(1)), int(m.group(2))
        except ValueError:
            unread.append(token)
            continue
        if not (1 <= r <= rows and 1 <= c <= cols):
            unread.append(token)
            continue
        skip.add((r, c))
    return [[r, c] for r, c in sorted(skip)], unread


def grid_size(rows: int, cols: int) -> str:
    """A grid's size as text an operator reads (S4 orchestrator ruling 1,
    #339): "3 columns by 2 rows" for 2 rows of 3.

    IN WORDS, COLUMNS FIRST. The code wrote the size both ways: the Example
    named "M31 3x2" and the framing card write columns by rows, width by
    height as a camera field is written, while this note wrote rows by
    columns, pulled by the panel labels, which are row-column ("2-1" is row
    2, column 1). So one block read "3x2" in the library and "2x3" here. In
    words it cannot be misread whichever convention the reader brings; the
    labels stay row-column, and the sentence that quotes them says so."""
    return (f"{cols} column{'' if cols == 1 else 's'} by "
            f"{rows} row{'' if rows == 1 else 's'}")


def _grid_of(node: FlowNode) -> tuple[int, int]:
    params = node.params or {}
    return _grid_dim(params.get("rows")), _grid_dim(params.get("cols"))


def _block_name(node: FlowNode) -> str:
    """How a sentence names a block: TARGET and its name, or its members for
    a POOL, which has no name of its own."""
    if node.type == "pool":
        return f"{NODE_DEFS['pool'].label} ({node.params.get('members') or ''})"
    name = str((node.params or {}).get("name") or "").strip()
    return f"TARGET {name}" if name else "an unnamed TARGET"


def _loop_edge_keys(graph: FlowGraph) -> set[tuple[str, str, str, str]]:
    """The LEGAL loop wires of the graph (spec 1.4 item 1): a pass wire from
    the tail of a MULTI-PANEL block's lane into that block's `next`. Keyed by
    their four ends, so two copies of one wire (the doctor's "one is enough")
    are both the loop.

    A pass wire into a 1x1 block is not one: there is nothing to rotate
    between, so it rotates nothing and sets no block's ``loop``. It is
    consumed all the same (``one_panel_pass_wires``, S4 orchestrator ruling
    3), but by the instructions pass alone."""
    keys: set[tuple[str, str, str, str]] = set()
    for n in graph.nodes:
        if is_multi_panel(n):
            for e in loop_wires(graph, n):
                keys.add((e.from_, e.fromPort, e.to, e.toPort))
    return keys


def one_panel_pass_wires(graph: FlowGraph) -> list[FlowEdge]:
    """The pass wires into a ONE-PANEL TARGET's ``next`` from a stage of that
    TARGET's own lane (S4 orchestrator ruling 3, #349; spec 1.4 item 4).

    CONSUMED AS STRUCTURE, like the loop wire: the compile emits no rule for
    one, so ``to_plan`` reports no loss and ``/run`` asks nothing about it.
    The doctor's M4 notes it, "one panel, nothing to rotate between", and
    reads THIS list to decide which wires get the note, so the note and the
    consumption are one set by construction. Before the ruling the compile
    emitted the wire as a ``<type>.pass`` rule, ``to_plan`` reported that as
    a rule that will not run, at warn level, and ``/run`` refused until the
    operator accepted a loss the doctor had called harmless. The likely way
    in is a 3x2 turned back into a single target, which keeps its loop wire.

    From ANY stage of the lane, the tail or not: with one panel there is no
    later panel for the stages after the wire's source to be skipped on, so
    M12's reason does not arise. NOT consumed, and still a rule that will
    not run: a pass wire from a stage of another block's lane (M4's
    warning, "this wire does nothing", a stage past a DOME included, since
    ``owner_of`` gives it to nobody), and a pass wire into any port but a
    TARGET's ``next``. Matched on the port names as the loop wire is
    (``PASS_PORT``, ``NEXT_PORT``), from a type that has the port
    (``PASS_TYPES``). That took in AUTOFOCUS and GUIDE when they gained it
    (S4, #331), so a 3x2 whose loop left an AUTOFOCUS and was turned back
    into a single target is treated as one whose loop left a FILTER CYCLE."""
    by_id, parents = _lane_index(graph)
    out: list[FlowEdge] = []
    for e in graph.edges:
        if e.fromPort != PASS_PORT or e.toPort != NEXT_PORT:
            continue
        src, dst = by_id.get(e.from_), by_id.get(e.to)
        if (src is None or dst is None or dst.type != "target"
                or src.type not in PASS_TYPES
                or is_multi_panel(dst)):
            continue
        owner = _walk_to_owner(src.id, by_id, parents)[0]
        if owner is not None and owner.id == dst.id:
            out.append(e)
    return out


def _followers(graph: FlowGraph) -> dict[str, str]:
    """``{node id: mosaic node id}`` for every TARGET or POOL a multi-panel
    block's tail feeds, directly or further down the flow lane (spec 1.6:
    "targets reachable from it are followers of the group").

    A target downstream of two mosaics follows the NEAREST, the one fewer
    flow wires away: A -> B -> C makes C follow B and B follow A, which is
    the order the gates open in. A block with no stage is its own tail, so
    what it feeds follows it; a branched lane has no tail and so no
    followers (M12 refuses the graph). Empty for a graph with no mosaic."""
    blocks = sorted((n for n in graph.nodes if is_multi_panel(n)),
                    key=lambda n: (n.x, n.y, n.id))
    if not blocks:
        return {}
    by_id, _parents = _lane_index(graph)
    children: dict[str, list[FlowNode]] = {}
    for e in graph.edges:
        ends = _flow_wire(by_id, e)
        if ends is not None:
            kids = children.setdefault(ends[0].id, [])
            if all(k.id != ends[1].id for k in kids):
                kids.append(ends[1])
    best: dict[str, tuple[int, int, str]] = {}
    for rank, block in enumerate(blocks):
        if panel_lane(graph, block):
            starts = lane_next(graph, block)
        elif lane_branched(graph, block):
            starts = []
        else:
            starts = list(children.get(block.id, []))
        # Breadth first, with a seen-set: the compile routes compile a graph
        # validation would refuse, loops included.
        seen: set[str] = {block.id}
        frontier = [(n, 0) for n in starts]
        while frontier:
            node, depth = frontier.pop(0)
            if node.id in seen:
                continue
            seen.add(node.id)
            if node.type in OWNER_TYPES:
                was = best.get(node.id)
                if was is None or (depth, rank) < was[:2]:
                    best[node.id] = (depth, rank, block.id)
            frontier.extend((k, depth + 1) for k in children.get(node.id, []))
    return {nid: held[2] for nid, held in best.items()}


def lane_refusals(graph: FlowGraph) -> list[dict]:
    """The wire shapes a graph with a mosaic cannot run, as ``{code, node_id,
    text}`` (spec 1.5, 1.8). ``to_plan`` raises ``GraphNotRunnable`` on any
    of them, and the doctor's M12 and M13 are the same findings:

    * M12, a multi-panel block's lane BRANCHES (the block or a stage feeds
      two stages): "the last stage" has two answers and the panels no single
      order;
    * M12, a pass wire into the block's `next` leaves a stage of its lane
      that is NOT THE TAIL: the stages after it would be shot once per panel
      with nothing to say when;
    * M13, a CAPTURE LOOP or FILTER CYCLE BELONGS TO NO BLOCK (``owner_of``
      is None) in a graph with a mosaic: the wire rule gives it to nobody,
      so it would shoot nothing, where canvas order would have put it on
      every panel of the mosaic above it.

    Empty for a graph with no multi-panel block: none of the three can
    happen under the canvas-order rule, which that graph keeps (spec 1.5).

    Read through the missing-key defaults, as ``compile_plan`` reads the
    graph, so a sentence names a stage as its card shows it (a CAPTURE LOOP
    with no stored filter is the card's "L")."""
    graph = graph.with_defaults()
    blocks = sorted((n for n in graph.nodes if is_multi_panel(n)),
                    key=lambda n: (n.x, n.y, n.id))
    if not blocks:
        return []
    by_id, parents = _lane_index(graph)
    out: list[dict] = []
    for block in blocks:
        name = _block_name(block)
        if lane_branched(graph, block):
            members = _lane_members(graph, block)
            depths = [d for d, _ in members]
            fork = min(d for d in depths if depths.count(d) > 1)
            both = [_stage_label(n) for d, n in members if d == fork]
            out.append({"code": "M12", "node_id": block.id, "text": (
                f"the {name} panel lane branches: {' and '.join(both)} both "
                f"follow the same stage, so no stage is the last one and the "
                f"panels have no single order. Make the lane one chain, or "
                f"give the second branch its own TARGET.")})
            continue
        lane = panel_lane(graph, block)
        tail = lane[-1] if lane else None
        seen: set[str] = set()
        for e in graph.edges:
            if (e.fromPort != PASS_PORT or e.to != block.id
                    or e.toPort != NEXT_PORT or tail is None
                    or e.from_ == tail.id or e.from_ in seen):
                continue
            at = next((i for i, n in enumerate(lane) if n.id == e.from_), None)
            if at is None:
                continue        # another block's stage: M4's warning, not this
            seen.add(e.from_)
            after = lane[at + 1:]
            later = " and ".join(_stage_label(n) for n in after)
            one = len(after) == 1
            out.append({"code": "M12", "node_id": e.from_, "text": (
                f"the loop wire starts at {_stage_label(lane[at])}, but "
                f"{later} {'comes' if one else 'come'} after it in the "
                f"{name} panel lane. Start the loop wire at "
                f"{_stage_label(tail)} to shoot every stage on every panel, "
                f"or give {later} a TARGET of {'its' if one else 'their'} "
                f"own.")})
    for n in sorted(graph.nodes, key=lambda n: (n.x, n.y, n.id)):
        if n.type not in ("capture", "cycle"):
            continue
        if _walk_to_owner(n.id, by_id, parents)[0] is not None:
            continue
        out.append({"code": "M13", "node_id": n.id,
                    "text": _no_owner_text(n, by_id, parents)})
    return out


def _no_owner_text(stage: FlowNode, by_id: dict[str, FlowNode],
                   parents: dict[str, list[FlowNode]]) -> str:
    """M13's sentence, naming what cut the stage off: the first node up its
    chain that ends a lane, or that nothing leads into it at all."""
    label = _stage_label(stage)
    cur, seen = stage, {stage.id}
    while True:
        ups = parents.get(cur.id, [])
        if not ups:
            why = "nothing leads into it"
            break
        if len(ups) > 1:
            why = "two wires lead into it, so it has no single lane"
            break
        up = ups[0]
        if up.type not in LANE_TYPES:
            d = NODE_DEFS.get(up.type)
            why = f"{up.type if d is None else d.label} ends the lane above it"
            break
        if up.id in seen:
            why = "its lane loops back on itself"
            break
        seen.add(up.id)
        cur = up
    return (f"{label} belongs to no TARGET: {why}. In a flow with a mosaic a "
            f"stage is shot only on the block its wires lead back to, so it "
            f"would shoot nothing. Give it a TARGET.")


def _target_entry(n: FlowNode, graph: FlowGraph, loop_keys: set,
                  follows: str | None, notes: list[dict]) -> dict:
    """One TARGET node's compile entry (spec 3.2), steps still empty.

    ``mosaic`` is None for a block of one panel and the grid otherwise, so a
    single target reads as one: its centring and count mode are the whole of
    what S3 adds to it. ``loop`` is True only for a multi-panel block with a
    legal loop wire, the wire the instructions pass then leaves out."""
    params = n.params or {}
    rows, cols = _grid_of(n)
    entry: dict = {
        # Text, as typed (``_text``): the one reading of whether RA and Dec
        # are typed, trimmed, is ``identity.typed_coordinates`` (#387).
        "name": _text(params.get("name")),
        "ra": _text(params.get("ra")),
        "dec": _text(params.get("dec")),
        # DEFAULT -1, NOT 0. -1 is "no angle constraint" (see to_plan's
        # rotation block); 0 is north-up, a real position angle somebody
        # may well want. An unparseable or empty field must fall to "no
        # constraint" — falling to 0 would silently command the rotator to
        # PA 0 on every target of every flow whose angle box was left blank.
        # Finite-only (#362), so "inf" is no constraint too, as
        # ``nodes._rotation_deg`` reads it for the block's angle.
        "rotation_deg": _finite(params.get("rotation"), -1),
        # THE BLOCK THIS CAME FROM (#189 S1, spec 3.2). `to_plan` keys the
        # target's deterministic id on it, so a flow compiled on night two
        # names the targets night one banked frames against. The canvas node
        # id is the one thing about a block that an edit never changes; the
        # name and the coordinates both can.
        "node_id": n.id,
        "angle": angle_code(params),
        "mosaic": None,
        "loop": False,
        # THE CENTRING THE RUN WILL USE (#170). A number that is not one is
        # None, which `to_plan` leaves unset, so the run centres to the
        # hub's own 0.02 deg and 3 attempts: exactly the missing-key values,
        # 1.2 arcmin and 3, and never a number the compile made up.
        "centre": {"tol_arcmin": _finite(params.get("centerTol"), None),
                   "attempts": _finite(params.get("centerTries"), None)},
        "count_mode": count_mode_of(params),
    }
    if rows * cols > 1:
        skip, unread = parse_skip(params.get("skip"), rows, cols)
        if unread:
            notes.append({"node_id": n.id, "level": "warn", "text": (
                f"{_block_name(n)}: skip {', '.join(repr(u) for u in unread)} "
                f"names no panel of this grid of {grid_size(rows, cols)}, so "
                f"nothing is skipped for it. A panel is written row-column, "
                f"from 1-1 to {rows}-{cols}.")})
        entry["mosaic"] = {
            "rows": rows, "cols": cols,
            # A percent, as the block holds it; `to_plan` divides by 100.
            # None for a value that is not a number: `to_plan` refuses it,
            # where a guessed overlap would tile panels nobody framed. An
            # infinity or a NaN is not one either (#362).
            "overlap": _finite(params.get("overlap"), None),
            "fov_x": _finite(params.get("fovX"), None),
            "fov_y": _finite(params.get("fovY"), None),
            "fov_from": str(params.get("fovFrom") or ""),
            "skip": skip,
            # The operator's words; `to_plan` maps them onto the engine's.
            "order": str(params.get("order") or ""),
            "passes": _finite(params.get("passes"), None),
            "visit_min": _finite(params.get("minVisit"), None),
            # "Auto" and "Skip it this pass" both keep a panel on its tile:
            # Auto means skip for a mosaic panel (spec 2.4 CENTRING).
            "require_centred": (str(params.get("ifNotCentred") or "").strip()
                                != SHOOT_ANYWAY),
            # ONE ANSWER PER FLOW (Revision 2 ruling 1), copied onto every
            # block so the engine reads it per group, as it did before.
            "when_waiting": graph.setting("whenWaiting"),
            "frame_anchor": str(params.get("frameAnchor") or ""),
        }
        entry["loop"] = any(k[2] == n.id for k in loop_keys)
    else:
        # A 1x1 block's anchor rides on the entry (spec 3.3), so a nudge the
        # save carried keeps its id for a single target as for a mosaic.
        entry["frame_anchor"] = str(params.get("frameAnchor") or "")
    if follows is not None:
        # Spec 3.2 lacks this key (S3-SPEC3 records it): the multi-panel
        # block this one follows, whose `when_waiting` decides whether it
        # waits for the group (spec 1.6).
        entry["follows"] = follows
    entry["steps"] = []
    return entry


def _trigger_for(node: FlowNode, from_port: str) -> str:
    """The `when` string for an event edge leaving ``node``.

    Named per source type rather than per port, because the engine's trigger
    enum is a vocabulary of SITUATIONS ("the sky closed in") and not of wires.
    An unmapped source falls back to ``type.port`` — visible and obviously
    wrong, which beats silently compiling to a trigger that already means
    something else."""
    if from_port == PASS_PORT and node.type in PASS_TYPES:
        # THE LOOP WIRE'S TRIGGER (spec 1.3 item 1), FIRST, so no type branch
        # below can claim a pass wire. "Pass done" is structure, never a
        # situation: the compile consumes the wire from a lane's tail into its
        # TARGET's `next` as the group's rotate mode, and one into a one-panel
        # TARGET's `next` from its own lane as nothing (S4 orchestrator ruling
        # 3), and a pass wire anywhere else is emitted with this trigger and
        # reported as a rule that will not run. Before this branch a capture
        # stage's pass wire compiled to `on_frame_graded`, a rule that would
        # fire on every graded frame.
        #
        # AUTOFOCUS AND GUIDE are named here since they gained the port (S4,
        # #331), not left to the fallback at the bottom, which happens to
        # spell the same string today: an edit to the fallback must not turn
        # their pass wire into a rule the engine runs.
        return f"{node.type}.pass"
    if node.type == "cloudwatch":
        # 'in' -> on_clouds_in, 'clear' -> on_clouds_clear. The two additive
        # kinds, and the only place they are minted.
        return f"on_clouds_{from_port}"
    if node.type == "condition":
        when = str(node.params.get("when") or "").strip().lower()
        if when == "hfr above (x focus)":
            # GN-08: the relative form of the HFR watchdog is still the ENGINE'S
            # `on_hfr_above` trigger — only the meaning of `threshold` changes
            # (a factor of the post-focus baseline, not a pixel value). That
            # extra bit travels on the rule as `relative`, minted in
            # `compile_plan` below, not folded into a trigger name of its own.
            return "on_hfr_above"
        when = when.replace(" ", "_")
        return f"on_{when}" if when else "on_condition"
    if node.type == "safety":
        return "on_unsafe"
    if node.type in ("capture", "cycle"):
        return "on_frame_graded"
    if node.type == "flatpanel":
        return "on_panel_ready"
    # The four campaign kinds. Each names a MOMENT the engine can recognise
    # without the graph having to describe it: the night is ending, this target
    # has what it owes, the shutdown finished, the active target sank.
    if node.type == "dusk" and from_port == "nightend":
        return "on_night_end"
    if node.type == "report" and from_port == "done":
        return "on_target_complete"
    if node.type == "parkclose" and from_port == "closed":
        return "on_shutdown_complete"
    if node.type == "pool" and from_port == "floor":
        return "on_altitude_floor"
    return f"{node.type}.{from_port}"


def compile_plan(graph: FlowGraph, name: str = "") -> dict:
    """The compiled plan: schedule + targets + automation + instructions.

    WHICH TARGETS A STAGE GOES TO depends on the graph (spec 1.5,
    ``needs_wire_scoping``):

    * With at most one TARGET or POOL block and none of them a mosaic, the
      canvas-order rule, byte for byte: every capture or cycle stage goes to
      every target seen earlier in the walk. Every Example bar the pool ones
      and every flow saved before mosaics compiles exactly as it did - a
      single block has nothing else a stage could leak onto.
    * Otherwise EVERY stage in the graph goes to its ``owner_of`` block only,
      and a stage with no owner goes nowhere (the doctor's M13 makes that
      loud). A mosaic needs this because it is where a leak costs most - one
      stray CAPTURE becomes a quota on every panel - but it is not the only
      graph a leak costs: two ordinary TARGETs with independent lanes leaked
      the same way, by canvas x and y alone, which was filed as the general
      case of the mosaic fix (I-05, #151) and is covered here now rather than
      deferred to a card's position on screen.

    Each stage the two rules assign differently gets an entry in ``notes``
    that names it, so the operator reads what the switch changed rather than
    finding it in the plan - including a flow saved before this fix, the
    first time it is opened or compiled after it ships. ``notes`` is ABSENT
    when empty, as ``campaign`` is, which is what keeps a plan with one
    TARGET or POOL byte-identical.

    EVERY TARGET ENTRY CARRIES THE BLOCK (spec 3.2, ``_target_entry``): its
    ``angle``, its grid as ``mosaic`` (None for one panel), ``loop``, its
    ``centre`` and its ``count_mode``; a POOL member carries its pool's
    ``count_mode``. A TARGET or POOL a mosaic's tail feeds carries
    ``follows``, the mosaic's node id. The legal loop wire is consumed as
    ``loop`` and never emitted as a rule, and so is a pass wire into a
    one-panel block from its own lane, as nothing at all
    (``one_panel_pass_wires``, S4 orchestrator ruling 3). In a graph with
    no mosaic the
    entries gain those keys and nothing else moves, and ``to_plan`` turns
    them into the same plan plus the centring (the controls in
    ``test_flows_compile.py`` and ``test_flows_to_plan.py``)."""
    graph = graph.with_defaults()
    order = flow_order(graph)

    targets: list[dict] = []
    # The entries each TARGET or POOL node emitted, by node id: what a stage
    # scoped by its wires is appended to.
    by_block: dict[str, list[dict]] = {}
    scoping = _lane_index(graph) if needs_wire_scoping(graph) else None
    notes: list[dict] = []
    # The loop wires the instructions pass leaves out, the blocks they make
    # rotate, and who follows which mosaic. All three are empty in a graph
    # with no mosaic, which is what keeps its compile what it was.
    loop_keys = _loop_edge_keys(graph) if scoping is not None else set()
    looped = {k[2] for k in loop_keys}
    followers = _followers(graph) if scoping is not None else {}
    # The wires the instructions pass consumes: the loop wires, and every
    # pass wire into a one-panel block from its own lane (S4 orchestrator
    # ruling 3). Looked for in EVERY graph, with a mosaic or without: a
    # single target has no mosaic to switch the scoping on. Only the loop
    # wires make a block rotate (``looped``); a one-panel wire makes nothing
    # a one-slot cycle, because with one panel there is no pass to rotate.
    consumed = loop_keys | {(e.from_, e.fromPort, e.to, e.toPort)
                            for e in one_panel_pass_wires(graph)}

    def receivers(stage: FlowNode) -> tuple[list[dict], FlowNode | None]:
        """The target entries ``stage``'s step is appended to, and the block
        its wires give it to (None under the canvas-order rule)."""
        if scoping is None:
            return targets, None
        owner = _walk_to_owner(stage.id, *scoping)[0]
        mine = [] if owner is None else by_block.get(owner.id, [])
        # The owner precedes its stages in a topological walk, so ``mine`` is
        # always a subset of the targets seen so far; the rest are what canvas
        # order would have added.
        others = [t for t in targets if not any(t is m for m in mine)]
        if others:
            notes.append(_scope_note(stage, owner, mine, others))
        return mine, owner

    for n in order:
        if n.type == "target":
            entry = _target_entry(n, graph, loop_keys, followers.get(n.id),
                                  notes)
            targets.append(entry)
            by_block.setdefault(n.id, []).append(entry)
        elif n.type == "pool":
            # A pool expands to one target per candidate, carrying the shared
            # constraint set and its rank. The scheduler picks between them at
            # run time; the compile just states who is eligible.
            members = [m.strip() for m in str(n.params.get("members") or "").split(",")]
            for i, m in enumerate([x for x in members if x]):
                entry = {
                    "name": m, "pool_rank": i + 1,
                    # The POOL's node id on every member: a member's id is keyed
                    # on the pool and the member's NAME, never its rank, so
                    # reordering the members box re-keys nothing (spec 3.3).
                    "node_id": n.id,
                    # HOW MUCH EACH MEMBER OWES BEFORE IT COUNTS AS DONE. Without
                    # it "advance" has nothing to compare against and a campaign
                    # can never finish a target, only stop working on one.
                    # A count, so read finite-only (#328, ``_finite``).
                    "quota_cycles": _finite(n.params.get("quota")),
                    # Every number below finite-only too (#362): the route
                    # renders this dict as JSON, which has no infinity.
                    "min_altitude_deg": _finite(n.params.get("minAlt")),
                    # WHAT THE FLOOR MEANS ONCE THE TARGET IS RUNNING, and it
                    # only reached a sentence in the Tonight story before this.
                    # The dial's two options are prose ("Advance now; retry it
                    # next night" / "Keep imaging (not recommended)"), so match
                    # on the verb rather than the whole string - the copy is
                    # allowed to change without silently flipping a night's
                    # behaviour back to "keep".
                    "on_floor": ("advance"
                                 if str(n.params.get("onFloor") or "")
                                 .strip().lower().startswith("advance")
                                 else "keep"),
                    "min_moon_sep_deg": _finite(n.params.get("moonSep")),
                    "max_hour_angle_h": _finite(n.params.get("maxHA")),
                    # The POOL's `counts`, which ruling 2 treats as TARGET's:
                    # one plan-wide count mode, so every block says what it
                    # asks for and `to_plan` settles it (M7).
                    "count_mode": count_mode_of(n.params),
                }
                if n.id in followers:
                    entry["follows"] = followers[n.id]
                entry["steps"] = []
                targets.append(entry)
                by_block.setdefault(n.id, []).append(entry)
        elif n.type == "capture":
            # EVERY target so far gets this step, in a graph with no mosaic
            # (`receivers`; with one, only the stage's own block). That is the
            # prototype's semantics and it is what makes a pool night work:
            # four candidates and one capture loop means all four are shot the
            # same way, which is the only reading under which "best available"
            # can substitute one for another mid-night. The wire rule keeps
            # that, since every member of a pool is the pool's.
            step = {
                # Text (#362): a filter that is a number or a JSON NaN was
                # copied verbatim, which the plan's `filter: str` refuses.
                "filter": _text(n.params.get("filter")),
                # Finite-only, as every number here is (#362): an "inf"
                # exposure reads as 0, which `to_plan` refuses by name.
                "exposure_s": _finite(n.params.get("exposure")),
                "gain": _finite(n.params.get("gain")),
                "binning": _finite(n.params.get("bin"), 1),
                # A count, so read finite-only (#328): "inf" is no number of
                # frames, and `to_plan` refuses the 0 it reads as.
                "count": _finite(n.params.get("count")),
                "frame_type": "Light",
                # The STAGE, not the target: two capture nodes drawing the same
                # recipe on one target are two quotas, and this is what keeps
                # their step ids apart (spec 3.3).
                "node_id": n.id,
            }
            goal = _finite(n.params.get("goal"))
            if goal:
                step["integration_goal_h"] = goal
            mine, owner = receivers(n)
            if owner is not None and owner.id in looped:
                # INSIDE THE CIRCLE A CAPTURE IS A ONE-SLOT CYCLE (spec 1.3
                # item 4): one frame per pass on every panel, so a mono lane
                # needs no FILTER CYCLE of one filter to rotate. `to_plan`
                # marks the panels `acquisition = "cycle"` for the loop.
                step["per_visit"] = 1
            for t in mine:
                # a copy per target: they are independently editable downstream,
                # and a shared dict would make one target's edit rewrite them all
                t["steps"].append(dict(step))
        elif n.type == "cycle":
            # ONE STEP, NOT ONE PER SLOT. The handoff's compile spec is explicit:
            # a FILTER CYCLE stage becomes a single object carrying its whole
            # slot table, because the interleaving is the STAGE's behaviour and
            # not a property of any one filter. Seven separate steps would say
            # "shoot 45 L, then 45 R", which is the arrangement this node exists
            # to avoid.
            slots = [{"filter": f, "exposure_s": exp}
                     for f, exp in parse_cycle_plan(n.params.get("plan"))]
            # FINITE-ONLY (#328). Read through ``_num`` alone, "inf" was an
            # infinity and "nan" a NaN, and ``int()`` raised on both: the
            # editor's compile answered 500, and validation let the save
            # store the flow, so it never compiled again. Anything that is
            # not a finite number reads as 1, as text always has.
            cycles = max(1, int(_finite(n.params.get("cycles"), 1) or 1))
            per_cycle = max(1, int(_finite(n.params.get("perCycle"), 1) or 1))
            step = {
                "strategy": "cycle",
                "cycles": cycles,
                "per_cycle": per_cycle,
                "gain": _finite(n.params.get("gain")),
                "binning": _finite(n.params.get("bin"), 1),
                "reject_hfr": _finite(n.params.get("reject")),
                "slots": slots,
                "frame_type": "Light",
                # Every slot step `to_plan` expands from this stage carries it.
                "node_id": n.id,
            }
            for t in receivers(n)[0]:
                t["steps"].append({**step, "slots": [dict(s) for s in slots]})

    dusk = next((n for n in graph.nodes if n.type == "dusk"), None)
    calib = next((n for n in graph.nodes if n.type == "calib"), None)
    flats = next((n for n in graph.nodes if n.type == "duskflats"), None)

    if dusk is not None:
        schedule = _dusk_schedule(dusk, notes)
    else:
        # No dusk node = run now. NOT "never": a flow with no window is one the
        # operator starts by hand, which is exactly the EAA example.
        schedule = {"start_mode": "now"}

    automation: dict = {}
    dome = next((n for n in graph.nodes if n.type == "dome"), None)
    if dome is not None:
        # DEVIATION FROM THE PROTOTYPE, authorised by the owner 2026-08-12.
        #
        # `compilePlan()` hardcodes `{bind: true, on_unsafe: "close"}`, so the
        # DOME CONTROL node's own azimuth field ("Bind to mount" | "Manual")
        # and its `timeout` never reached the server. Picking Manual in the
        # editor compiled to bound, and a shutter timeout the operator typed
        # was discarded — two controls that look live and do nothing, which is
        # the broken-promise class this codebase has a detector suite for.
        #
        # `on_unsafe` is NOT part of the change and stays a constant: DomePolicy
        # deliberately has no field for it, because a value that can arrive is a
        # value that can say "don't close", and the roof would still be open in
        # the rain having been talked out of shutting.
        #
        # STILL OUTSTANDING — FLAT PANEL has no `automation` block at all, so
        # its placement/ADU/solve params do not survive the compile either. Not
        # fixed here because the README's compile-output spec defines exactly
        # three automation keys (dome, dusk_flats, calibration_queue), and
        # minting a fourth would be inventing a contract rather than connecting
        # an existing one. Raised as an open question instead.
        automation["dome"] = DomePolicy.from_node_params(dome.params).to_plan()
    if flats is not None:
        automation["dusk_flats"] = {
            "method": _text(flats.params.get("method")),
            "window": _text(flats.params.get("window")),
            "adu_target": _finite(flats.params.get("adu")),
            "count": _finite(flats.params.get("count")),
        }
    if calib is not None:
        automation["calibration_queue"] = {
            "order": ["dark", "bias", "flat"],
            "policy": "if_stale",
            # A count, read finite-only (#328): `plan_extras` takes
            # ``int()`` of it for the hold's darks.
            "quota": _finite(calib.params.get("quota")),
            "flats_require_panel": True,
        }

    if dusk is not None:
        repeat = str(dusk.params.get("repeat") or "Single night")
        if repeat != "Single night":
            # A CAMPAIGN, and the three keys say the three things that make one:
            # it comes back (`repeat`), it knows when to stop (`until`), and it
            # picks up where it left off rather than starting the night again
            # (`resume`). `until` is derived from the operator's own phrasing so
            # a future option cannot silently compile to "run for ever".
            plan_campaign = {
                "repeat": "nightly",
                "until": ("pool_complete" if "pool" in repeat.lower()
                          else "nights_30"),
                "resume": "cursor",
            }
        else:
            plan_campaign = None
        # #195: "Single night" now means auto-resume does not arm across
        # nights (`SequencePlan.resume_across_nights`, `ResumeArm.tick`).
        # Every other `repeat` value keeps coming back by design, so it keeps
        # the field True - the same value a flow with no DUSK WINDOW gets,
        # below.
        resume_across_nights = repeat != "Single night"
    else:
        plan_campaign = None
        # A flow with no DUSK WINDOW carries no opinion on repeat at all, so
        # it keeps doing what it has always done: whatever ends the run
        # leaves it dormant and armed, exactly as before this field existed.
        resume_across_nights = True

    instructions: list[dict] = []
    for e in graph.edges:
        src = graph.node(e.from_)
        if src is None or port_kind(src.type, e.fromPort, "out") != "event":
            continue
        dst = graph.node(e.to)
        # EQUIPMENT TOPOLOGY IS NOT AN INSTRUCTION. A FLAT PANEL wired to a
        # CALIBRATION QUEUE's `panel` input is the operator saying "there is a
        # panel on this rig", not "when the panel is ready, do something". It is
        # already carried as `automation.calibration_queue.flats_require_panel`,
        # and emitting a rule for it as well would put a when/then in the plan
        # that fires on a fact rather than on an event.
        if (src.type == "flatpanel" and dst is not None
                and dst.type == "calib" and e.toPort == "panel"):
            continue
        # THE LOOP WIRE IS STRUCTURE, NOT A RULE (spec 1.4 item 3). A legal
        # one is the block's `loop`, which `to_plan` turns into the group's
        # rotate mode; emitted here as well it would become "this rule will
        # not run", a loss the operator would have to accept for the one
        # wire that works. The calib wires `plan_extras` honours are the
        # precedent. A pass wire into a one-panel block from its own lane is
        # structure too (S4 orchestrator ruling 3, #349): it rotates nothing,
        # which the doctor's M4 notes, and a loss for it contradicted that
        # note. Any other pass wire is still emitted, and reported.
        if (e.from_, e.fromPort, e.to, e.toPort) in consumed:
            continue
        # THE DESTINATION PORT IS PART OF THE RULE, not decoration.
        #
        # A HOLD / RESUME node has two inputs, `pause` and `resume`, and the M16
        # example wires both cloud edges into it: clouds-in to pause,
        # clouds-clear to resume. Emitting only `dst.type` made those two edges
        # produce the SAME rule - action "holdresume", twice - so the graph the
        # operator drew as "stop, then start again" compiled to "stop, then
        # stop". The port is the entire difference between them.
        #
        # Additive: `to_port` is a new key on the rule. Nothing that reads the
        # compiled plan today looks for it, and the PLAN tab renders the dict
        # verbatim, so the operator now simply sees which input a rule lands on.
        rule: dict = {"when": _trigger_for(src, e.fromPort),
                      "action": dst.type if dst is not None else "?",
                      "to_port": e.toPort}
        thr = src.params.get("threshold")
        if thr is not None:
            # Finite-only (#362): an infinite threshold was one more number
            # the route's JSON could not carry.
            rule["threshold"] = _finite(thr)
        if (src.type == "condition"
                and str(src.params.get("when") or "").strip().lower()
                == "hfr above (x focus)"):
            # GN-08: carries the factor-vs-pixel distinction through to
            # `to_plan._instructions`, which reads it to set `Instruction.relative`.
            rule["relative"] = True
        instructions.append(rule)

    out = {
        "name": name,
        "schedule": schedule,
        "targets": targets,
        "automation": automation,
        "instructions": instructions,
    }
    # ABSENT, not None, on a single night. A `campaign: null` key would make
    # every reader test for two falsy shapes, and the PLAN tab renders this dict
    # verbatim — a null there reads as "campaign: broken" to an operator.
    if plan_campaign is not None:
        out["campaign"] = plan_campaign
    # ABSENT WHEN TRUE (#195), the same convention as `campaign` above and for
    # the same reason: `SequencePlan.resume_across_nights` already defaults to
    # True, so every compile before this field existed - and every one of
    # today's that is not "Single night" - must produce the exact same dict it
    # always has. Only "Single night" writes anything here.
    if not resume_across_nights:
        out["resume_across_nights"] = False
    # Absent when empty for the same reason, and because an empty list on
    # every compile would change the plan of every flow that has no mosaic.
    if notes:
        out["notes"] = notes
    return out
