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

from .models import FlowGraph, FlowNode
from .nodes import port_kind

#: Trigger vocabulary. The first four already exist in the engine's closed
#: enum; the two cloud ones are the README's named ADDITIONS ("New TriggerKinds
#: are additive to the existing closed enum: on_clouds_in, on_clouds_clear").
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
    looped forever. A flow cycle is not expressible in the editor (one wire per
    input, and a cursor cannot revisit), so reaching one means the graph came
    from somewhere else; dropping is the fail-closed reading.
    """
    flow_edges = [e for e in graph.edges
                  if port_kind(_type_of(graph, e.from_), e.fromPort, "out") == "flow"]
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
    try:
        return int(v) if float(v).is_integer() else float(v)
    except (TypeError, ValueError):
        return default


def _trigger_for(node: FlowNode, from_port: str) -> str:
    """The `when` string for an event edge leaving ``node``.

    Named per source type rather than per port, because the engine's trigger
    enum is a vocabulary of SITUATIONS ("the sky closed in") and not of wires.
    An unmapped source falls back to ``type.port`` — visible and obviously
    wrong, which beats silently compiling to a trigger that already means
    something else."""
    if node.type == "cloudwatch":
        # 'in' -> on_clouds_in, 'clear' -> on_clouds_clear. The two additive
        # kinds, and the only place they are minted.
        return f"on_clouds_{from_port}"
    if node.type == "condition":
        when = str(node.params.get("when") or "").strip().lower().replace(" ", "_")
        return f"on_{when}" if when else "on_condition"
    if node.type == "safety":
        return "on_unsafe"
    if node.type == "capture":
        return "on_frame_graded"
    if node.type == "flatpanel":
        return "on_panel_ready"
    return f"{node.type}.{from_port}"


def compile_plan(graph: FlowGraph, name: str = "") -> dict:
    """The compiled plan: schedule + targets + automation + instructions."""
    graph = graph.with_defaults()
    order = flow_order(graph)

    targets: list[dict] = []
    for n in order:
        if n.type == "target":
            targets.append({
                "name": n.params.get("name"),
                "ra": n.params.get("ra"),
                "dec": n.params.get("dec"),
                "rotation_deg": _num(n.params.get("rotation")),
                "steps": [],
            })
        elif n.type == "pool":
            # A pool expands to one target per candidate, carrying the shared
            # constraint set and its rank. The scheduler picks between them at
            # run time; the compile just states who is eligible.
            members = [m.strip() for m in str(n.params.get("members") or "").split(",")]
            for i, m in enumerate([x for x in members if x]):
                targets.append({
                    "name": m, "pool_rank": i + 1,
                    "min_altitude_deg": _num(n.params.get("minAlt")),
                    "min_moon_sep_deg": _num(n.params.get("moonSep")),
                    "max_hour_angle_h": _num(n.params.get("maxHA")),
                    "steps": [],
                })
        elif n.type == "capture":
            # EVERY target so far gets this step. That is the prototype's
            # semantics and it is what makes a pool night work: four candidates
            # and one capture loop means all four are shot the same way, which
            # is the only reading under which "best available" can substitute
            # one for another mid-night.
            step = {
                "filter": n.params.get("filter"),
                "exposure_s": _num(n.params.get("exposure")),
                "gain": _num(n.params.get("gain")),
                "binning": _num(n.params.get("bin"), 1),
                "count": _num(n.params.get("count")),
                "frame_type": "Light",
            }
            goal = _num(n.params.get("goal"))
            if goal:
                step["integration_goal_h"] = goal
            for t in targets:
                # a copy per target: they are independently editable downstream,
                # and a shared dict would make one target's edit rewrite them all
                t["steps"].append(dict(step))

    dusk = next((n for n in graph.nodes if n.type == "dusk"), None)
    calib = next((n for n in graph.nodes if n.type == "calib"), None)
    flats = next((n for n in graph.nodes if n.type == "duskflats"), None)

    if dusk is not None:
        schedule = {
            "start_mode": "dusk",
            "start_offset_min": _num(dusk.params.get("offset")),
            "stop_mode": "dawn" if dusk.params.get("stop") == "Dawn" else "none",
            "min_altitude_deg": _num(dusk.params.get("minAlt")),
        }
    else:
        # No dusk node = run now. NOT "never": a flow with no window is one the
        # operator starts by hand, which is exactly the EAA example.
        schedule = {"start_mode": "now"}

    automation: dict = {}
    if any(n.type == "dome" for n in graph.nodes):
        automation["dome"] = {"slave": True, "on_unsafe": "close"}
    if flats is not None:
        automation["dusk_flats"] = {
            "method": flats.params.get("method"),
            "window": flats.params.get("window"),
            "adu_target": _num(flats.params.get("adu")),
            "count": _num(flats.params.get("count")),
        }
    if calib is not None:
        automation["calibration_queue"] = {
            "order": ["dark", "bias", "flat"],
            "policy": "if_stale",
            "quota": _num(calib.params.get("quota")),
            "flats_require_panel": True,
        }

    instructions: list[dict] = []
    for e in graph.edges:
        src = graph.node(e.from_)
        if src is None or port_kind(src.type, e.fromPort, "out") != "event":
            continue
        dst = graph.node(e.to)
        rule: dict = {"when": _trigger_for(src, e.fromPort),
                      "action": dst.type if dst is not None else "?"}
        thr = src.params.get("threshold")
        if thr is not None:
            rule["threshold"] = _num(thr)
        instructions.append(rule)

    return {
        "name": name,
        "schedule": schedule,
        "targets": targets,
        "automation": automation,
        "instructions": instructions,
    }
