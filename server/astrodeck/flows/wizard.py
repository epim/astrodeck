"""The guided wizard's generator — three answers in, a night's graph out.

TRANSCRIBED from ``genWizard()`` in the prototype (``design_handoff_astrodeck_
flows/AstroDeck Flows.dc.html``), README §9. The 560px sheet is the UI's job;
this is the half that decides what actually gets built, and it lives on the
server for the same reason the doctor does — two implementations of one rule set
is the drift this project keeps re-finding.

THE ACCEPTANCE BAR IS THE DOCTOR. §9 ends on one sentence — "Every generated
graph must pass the doctor" — and it is the entire point of the feature. The
wizard exists so that somebody's FIRST flow is a night that works; a generated
graph that arrives already lit up with warnings teaches them, on that first
flow, that the doctor is decoration. So every kind × every subset of the six
automation chips is enumerated in ``tests/test_flows_wizard.py`` and each graph
is required to come back with nothing above `note`.

THREE PLACES THIS DEVIATES FROM THE PROTOTYPE, each marked DEVIATION below, each
because transcribing ``genWizard()`` literally produces a graph its own
``issues()`` faults:

  1. an unguided lane still asked for 120 s subs           (doctor rule 2)
  2. a queue with no flat panel still wanted flats         (doctor rule 7)
  3. a dome turned up with no safety monitor               (doctor rule 9, DANGER)

Where §9's two requirements disagree — "match genWizard()" and "must pass the
doctor" — the doctor wins: it is the stated hard requirement, and the prototype
was plainly never run through its own checks across the option matrix (the
sheet's own defaults, Guiding + HFR watchdog, are one of the combinations that
happens to come out clean). Each deviation below is the smallest one that clears
the rule, and each sets a value the editor itself offers, so nothing generated
here is unreachable by hand.
"""
from __future__ import annotations

from collections.abc import Iterable

from .models import MY_FLOWS_FOLDER, FlowEdge, FlowGraph, FlowNode, FlowRecord
from .nodes import NODE_DEFS, default_params

# ---------------------------------------------------------------- the answers
# The three questions, verbatim from the sheet (screenshots/09-wizard-new-flow).
# Exported as constants because a caller matching on the string "EAA quick look"
# and a generator matching on "EAA Quick Look" would silently build a guided
# deep-sky night instead, which is the failure this whole module is about.
KIND_DEEP_SKY = "Deep-sky target"
KIND_POOL = "Best of several"
KIND_EAA = "EAA quick look"
KINDS: tuple[str, ...] = (KIND_DEEP_SKY, KIND_POOL, KIND_EAA)

OPT_GUIDING = "Guiding"
OPT_DUSK_FLATS = "Dusk flats"
OPT_DOME = "Dome"
OPT_CLOUD_DODGE = "Cloud-dodge calibration"
OPT_WATCHDOG = "HFR watchdog"
OPT_NOTIFY = "Notify my phone"
#: Chip order as the sheet renders them.
AUTOMATION_OPTIONS: tuple[str, ...] = (
    OPT_GUIDING, OPT_DUSK_FLATS, OPT_DOME, OPT_CLOUD_DODGE, OPT_WATCHDOG,
    OPT_NOTIFY)

#: What the sheet opens with (prototype state: kind + these two chips).
DEFAULT_OPTIONS: frozenset[str] = frozenset({OPT_GUIDING, OPT_WATCHDOG})

# ----------------------------------------------------------------- the layout
# The generated graph is dropped straight onto the canvas and the operator's
# first act is to read it, so these are real numbers, not filler: a node card is
# 188px wide (README §3), and both pitches leave a visible gap between cards.
_LANE_X0, _LANE_DX, _LANE_Y = 30.0, 228.0, 60.0        # the flow lane, one row
_RULES_X0, _RULES_DX, _RULES_Y = 30.0, 250.0, 380.0    # the rules row, beneath
_RULES_Y2 = _RULES_Y + 200.0                           # its second rank

#: EAA's capture overrides, from §9: "EAA: 4s g300 bin2 x60, goal 0". Short subs
#: on purpose — a live view is watched, not integrated, and `goal 0` says there
#: is no integration target to bank across nights.
_EAA_CAPTURE = {"exposure": 4, "gain": 300, "bin": "2", "count": 60, "goal": 0}

#: DEVIATION 1's number. The doctor draws its line at 120s ("stars will trail at
#: any real focal length"); 60 is one notch under it and still a deep-sky sub.
_UNGUIDED_EXPOSURE = 60

#: Used when the operator generates without typing a target — they get a flow
#: they can find in the library rather than a second "Untitled flow".
_FALLBACK_NAME = {KIND_DEEP_SKY: "New deep-sky run", KIND_POOL: "Pool night",
                  KIND_EAA: "quick look"}


class _Canvas:
    """Node ids, positions and wires for one generation.

    Ids are minted n1, n2, … in creation order, so the same answers always give
    the identical graph. Determinism is not cosmetic: the wizard is how most
    flows will be born, and a caller that regenerates to compare two option sets
    would otherwise see every node as changed.
    """

    def __init__(self) -> None:
        self.nodes: list[FlowNode] = []
        self.edges: list[FlowEdge] = []
        self._minted = 0
        self._wired = 0

    def add(self, node_type: str, x: float, y: float) -> FlowNode:
        self._minted += 1
        node = FlowNode(id=f"n{self._minted}", type=node_type,
                        x=float(x), y=float(y), params=default_params(node_type))
        self.nodes.append(node)
        return node

    def wire(self, a: FlowNode, a_port: str, b: FlowNode, b_port: str) -> None:
        # Edge ids counted too (the prototype's "we0", "we1", …). FlowEdge
        # otherwise defaults to a random uuid, which would make two generations
        # of identical answers differ on every wire.
        self.edges.append(FlowEdge(**{"id": f"we{self._wired}", "from": a.id,
                                      "fromPort": a_port, "to": b.id,
                                      "toPort": b_port}))
        self._wired += 1

    def chain(self, a: FlowNode, b: FlowNode) -> None:
        """Wire a's first FLOW output to b's first FLOW input.

        First-flow-port rather than a per-type table, transcribed from the
        prototype. It is not laziness: CAPTURE's second output is `frame`, an
        EVENT, and picking the first flow port is what stops the lane from
        wiring the frame stream into the session report — an edge the models
        would refuse anyway, but as a 500 rather than as a graph.
        """
        out = next((p for p in NODE_DEFS[a.type].outs if p.kind == "flow"), None)
        into = next((p for p in NODE_DEFS[b.type].ins if p.kind == "flow"), None)
        if out is not None and into is not None:
            self.wire(a, out.id, b, into.id)

    def graph(self) -> FlowGraph:
        return FlowGraph(nodes=self.nodes, edges=self.edges)


def _safety_pair(canvas: _Canvas) -> FlowNode:
    """A safety monitor with something wired to it. Returns the monitor.

    Never a bare monitor. SAFETY MONITOR has no inputs, so doctor rule 1 cannot
    see one whose `unsafe` event goes nowhere — it would satisfy rule 9's "add a
    monitor" while closing nothing on rain, which is the precise shape of a
    promise nothing keeps.
    """
    safety = canvas.add("safety", _RULES_X0, _RULES_Y2)
    abort = canvas.add("abort", _RULES_X0 + _RULES_DX, _RULES_Y2)
    canvas.wire(safety, "unsafe", abort, "do")
    return safety


def _checked(kind: str, options: Iterable[str] | None) -> tuple[str, frozenset[str]]:
    """The answers, or a refusal naming what was not understood.

    Refused rather than quietly ignored. Dropping an unrecognised option hands
    back a night that is missing its dome — or its watchdog — while looking
    exactly like one that has it, and the operator finds out at 03:00. A
    misspelled chip is a client bug and reads as one here.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown wizard kind {kind!r}; expected one of {list(KINDS)}")
    opts = frozenset(options or ())
    unknown = sorted(opts - set(AUTOMATION_OPTIONS))
    if unknown:
        raise ValueError(f"unknown automation option(s) {unknown}; "
                         f"expected any of {list(AUTOMATION_OPTIONS)}")
    return kind, opts


def generate(kind: str = KIND_DEEP_SKY,
             options: Iterable[str] | None = None,
             target: str = "") -> FlowGraph:
    """The graph for one set of wizard answers.

    ``kind`` is one of :data:`KINDS`, ``options`` any subset of
    :data:`AUTOMATION_OPTIONS`, ``target`` the free-text target — a comma list
    means pool candidates.

    Shape (§9): the flow lane is dusk → [dome] → [dusk flats] → target|pool →
    slew → autofocus → [guide] → capture → report, laid out left to right in the
    order the night runs; underneath it sits a rules row of whatever the chips
    asked for. Guide is skipped for EAA whether or not the chip is lit, because
    a 4-second sub does not need one and paying the settle time per frame would
    make a live view stutter.
    """
    kind, opts = _checked(kind, options)
    tname = (target or "").strip()
    canvas = _Canvas()

    # ------------------------------------------------------------- flow lane
    # TODO(flows-handoff): the lane always opens with a DUSK WINDOW, EAA
    # included — transcribed, but it means a "quick look" compiles to
    # start_mode=dusk and sits waiting, while the EAA *example* fixture has no
    # dusk node and starts now. Which is right for the wizard's EAA answer?
    lane_types = ["dusk"]
    if OPT_DOME in opts:
        lane_types.append("dome")
    if OPT_DUSK_FLATS in opts:
        lane_types.append("duskflats")
    lane_types.append("pool" if kind == KIND_POOL else "target")
    lane_types += ["slew", "autofocus"]
    guided = kind != KIND_EAA and OPT_GUIDING in opts
    if guided:
        lane_types.append("guide")
    lane_types += ["capture", "report"]

    lane = [canvas.add(t, _LANE_X0 + i * _LANE_DX, _LANE_Y)
            for i, t in enumerate(lane_types)]
    for a, b in zip(lane, lane[1:]):
        canvas.chain(a, b)

    for node in lane:
        if node.type == "target" and tname:
            node.params["name"] = tname
        elif node.type == "pool" and tname.find(",") > 0:
            # A LIST replaces the pool's candidates; a single name does not.
            # Transcribed — one name is not a pool, and overwriting four
            # defaults with it would leave "best available" nothing to choose
            # between. TODO(flows-handoff): the sheet accepts one name under
            # "Best of several" and then silently ignores it. Should the wizard
            # fall back to a plain TARGET when there is no comma?
            node.params["members"] = tname
        elif node.type == "capture":
            if kind == KIND_EAA:
                node.params.update(_EAA_CAPTURE)
            elif not guided:
                # DEVIATION 1 (doctor rule 2). The prototype leaves the 120s
                # default in place when Guiding is not picked, so the wizard's
                # own output opens with "120s subs with no GUIDE upstream —
                # stars will trail". Taking the doctor's second remedy, the one
                # that respects the answer given: shorten the subs.
                node.params["exposure"] = _UNGUIDED_EXPOSURE

    # ------------------------------------------------------------ rules row
    rules_x = _RULES_X0

    def rule(node_type: str) -> FlowNode:
        nonlocal rules_x
        node = canvas.add(node_type, rules_x, _RULES_Y)
        rules_x += _RULES_DX
        return node

    capture = next(n for n in lane if n.type == "capture")
    cloudwatch: FlowNode | None = None
    condition: FlowNode | None = None
    safety: FlowNode | None = None

    if OPT_CLOUD_DODGE in opts:
        # The full cloud-dodge choreography: clouds in holds the light loop AND
        # starts the queue, clouds clear resumes AND stops it cleanly. Both legs
        # of both events, because a hold with no resume is a night that ends at
        # the first cloud.
        cloudwatch = rule("cloudwatch")
        hold = rule("holdresume")
        queue = rule("calib")
        canvas.wire(cloudwatch, "in", hold, "pause")
        canvas.wire(cloudwatch, "in", queue, "do")
        canvas.wire(cloudwatch, "clear", hold, "resume")
        canvas.wire(cloudwatch, "clear", queue, "stop")
        if OPT_DUSK_FLATS in opts:
            # Panel directly under its queue, so the one wire that is not in
            # the row reads as belonging to the node above it.
            panel = canvas.add("flatpanel", queue.x, _RULES_Y2)
            canvas.wire(panel, "ready", queue, "panel")
        else:
            # DEVIATION 2 (doctor rule 7). Without the Dusk flats chip there is
            # no panel to wire, and the queue's default "If stale + panel wired"
            # then reads as a request the graph cannot serve — "QUEUE wants
            # flats but nothing is wired to 'panel'". Saying Skip is the same
            # night, minus the false promise, and it is one of the two values
            # the queue's own Flats field offers.
            queue.params["flats"] = "Skip"

    if OPT_WATCHDOG in opts:
        condition = rule("condition")
        refocus = rule("refocus")
        canvas.wire(capture, "frame", condition, "events")
        canvas.wire(condition, "fire", refocus, "do")

    if OPT_DOME in opts:
        # DEVIATION 3 (doctor rule 9, the only DANGER-level check). A dome with
        # no safety monitor is "nothing closes the shutter on rain", and the
        # prototype only ever mints a monitor down in the notify branch — so
        # picking Dome without Notify generated a graph that opens a shutter
        # nothing will close. Built here, before notify, so the two branches
        # share one monitor instead of racing to create two.
        safety = _safety_pair(canvas)

    if OPT_NOTIFY in opts:
        notify = rule("notify")
        if condition is not None:
            # Wired to the watchdog by preference: "your focus drifted" is the
            # message worth a phone buzzing, and it is the prototype's order.
            canvas.wire(condition, "fire", notify, "do")
        elif cloudwatch is not None:
            canvas.wire(cloudwatch, "in", notify, "do")
        else:
            # Nothing else in this graph fires an event, so the phone would
            # never buzz. A safety monitor is the honest thing to hang it on.
            if safety is None:
                safety = _safety_pair(canvas)
            canvas.wire(safety, "unsafe", notify, "do")

    return canvas.graph()


def flow_name(kind: str = KIND_DEEP_SKY, target: str = "") -> str:
    """What the generated flow is called in the library."""
    kind, _ = _checked(kind, ())
    tname = (target or "").strip()
    name = ("EAA — " if kind == KIND_EAA else "") + (tname or _FALLBACK_NAME[kind])
    # Trimmed to what FlowRecord accepts. A pool of eight candidates typed as a
    # comma list passes 120 characters easily, and a ValidationError here would
    # throw away a graph that is otherwise perfectly good.
    return name[:120]


def generate_record(kind: str = KIND_DEEP_SKY,
                    options: Iterable[str] | None = None,
                    target: str = "") -> FlowRecord:
    """The generated flow as the library stores it — graph, name and tagline.

    Lands in My flows, never in Examples: the five fixtures are read-only and a
    generated flow is the operator's, to edit from the moment it appears.
    """
    return FlowRecord(
        name=flow_name(kind, target),
        folder=MY_FLOWS_FOLDER,
        tagline=f"Generated by the wizard — {kind.lower()}",
        graph=generate(kind, options, target))
