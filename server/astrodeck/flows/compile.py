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

from ..devices.base import DomePolicy
from .models import FlowGraph, FlowNode
from .nodes import parse_cycle_plan, port_kind

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
                # DEFAULT -1, NOT 0. -1 is "no angle constraint" (see
                # to_plan's rotation block); 0 is north-up, a real position
                # angle somebody may well want. An unparseable or empty field
                # must fall to "no constraint" — falling to 0 would silently
                # command the rotator to PA 0 on every target of every flow
                # whose angle box was left blank.
                "rotation_deg": _num(n.params.get("rotation"), -1),
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
                    # HOW MUCH EACH MEMBER OWES BEFORE IT COUNTS AS DONE. Without
                    # it "advance" has nothing to compare against and a campaign
                    # can never finish a target, only stop working on one.
                    "quota_cycles": _num(n.params.get("quota")),
                    "min_altitude_deg": _num(n.params.get("minAlt")),
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
        elif n.type == "cycle":
            # ONE STEP, NOT ONE PER SLOT. The handoff's compile spec is explicit:
            # a FILTER CYCLE stage becomes a single object carrying its whole
            # slot table, because the interleaving is the STAGE's behaviour and
            # not a property of any one filter. Seven separate steps would say
            # "shoot 45 L, then 45 R", which is the arrangement this node exists
            # to avoid.
            slots = [{"filter": f, "exposure_s": exp}
                     for f, exp in parse_cycle_plan(n.params.get("plan"))]
            cycles = max(1, int(_num(n.params.get("cycles"), 1) or 1))
            per_cycle = max(1, int(_num(n.params.get("perCycle"), 1) or 1))
            step = {
                "strategy": "cycle",
                "cycles": cycles,
                "per_cycle": per_cycle,
                "gain": _num(n.params.get("gain")),
                "binning": _num(n.params.get("bin"), 1),
                "reject_hfr": _num(n.params.get("reject")),
                "slots": slots,
                "frame_type": "Light",
            }
            for t in targets:
                t["steps"].append({**step, "slots": [dict(s) for s in slots]})

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
    else:
        plan_campaign = None

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
            rule["threshold"] = _num(thr)
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
    return out
