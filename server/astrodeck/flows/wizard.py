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

from ..catalog.coords import parse_dec, parse_ra
from .models import MY_FLOWS_FOLDER, FlowEdge, FlowGraph, FlowNode, FlowRecord
from .nodes import NODE_DEFS, default_params, parse_cycle_plan

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

#: DEVIATION 1's number: the sub length the wizard writes when the operator did
#: NOT ask for guiding.
#:
#: A STARTING POINT, not a derivation. What an unguided mount can hold depends on
#: focal length, polar alignment, periodic error, declination and how much
#: trailing the operator will accept, and this generator knows none of those —
#: so the honest thing is a conservative opener the operator changes, which is
#: why ``generate()`` takes it as an argument and the wizard sheet offers it as a
#: field. 30 s is chosen to be short enough to be defensible at most focal
#: lengths rather than to be right at any particular one; the doctor's 120 s line
#: ("stars will trail at any real focal length") is the ceiling it stays well
#: under.
#:
#: Deliberately NOT presented to the operator as a recommendation. Sub length is
#: a question with more confident answers in circulation than good ones, and a
#: number the wizard states as if it computed something would carry authority it
#: has not earned.
UNGUIDED_EXPOSURE_DEFAULT = 30

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


#: How SAFETY MONITOR is scoped when a CLOUD WATCH shares the graph with it.
#: Verbatim from the node's `watch` option list — the two tiers must not both
#: claim clouds (doctor rule 13).
_WATCH_PAIRED = "Rain + wind + power (pair with Cloud Watch)"


def _safety_pair(canvas: _Canvas, *, paired_with_cloudwatch: bool = False) -> FlowNode:
    """A safety monitor with something wired to it. Returns the monitor.

    Never a bare monitor. SAFETY MONITOR has no inputs, so doctor rule 1 cannot
    see one whose `unsafe` event goes nowhere — it would satisfy rule 9's "add a
    monitor" while closing nothing on rain, which is the precise shape of a
    promise nothing keeps.

    SCOPED AWAY FROM CLOUDS when the same flow has a CLOUD WATCH. The default is
    the standalone reading, which is right for a rig with no transient tier; put
    both on the same graph unscoped and safety aborts the night that the hold was
    there to ride out. The wizard knows which graph it just drew, so it is the
    one thing that can set this without asking.
    """
    safety = canvas.add("safety", _RULES_X0, _RULES_Y2)
    if paired_with_cloudwatch:
        safety.params["watch"] = _WATCH_PAIRED
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
             target: str = "",
             unguided_exposure_s: float | None = None,
             *,
             cycle_plan: str = "",
             cycles: int = 1,
             coords: tuple[str, str] | None = None,
             safety_abort: bool = False) -> FlowGraph:
    """The graph for one set of wizard answers.

    ``kind`` is one of :data:`KINDS`, ``options`` any subset of
    :data:`AUTOMATION_OPTIONS`, ``target`` the free-text target — a comma list
    means pool candidates. ``unguided_exposure_s`` is the sub length used when
    guiding was NOT asked for; ``None`` takes
    :data:`UNGUIDED_EXPOSURE_DEFAULT`. It is an argument rather than a constant
    because the right value is a property of the rig and the operator's
    tolerance, neither of which this function can see.

    Shape (§9): the flow lane is dusk → [dome] → [dusk flats] → target|pool →
    slew → autofocus → [guide] → capture → report, laid out left to right in the
    order the night runs; underneath it sits a rules row of whatever the chips
    asked for. Guide is skipped for EAA whether or not the chip is lit, because
    a 4-second sub does not need one and paying the settle time per frame would
    make a live view stutter.

    FOUR KEYWORD-ONLY EXTRAS, all inert at their defaults, exist so ``quick()``
    below can reuse THIS builder instead of drawing a second deep-sky graph:

    * ``cycle_plan`` -- a FILTER CYCLE slot table (``"L 60, R 60, …"``). When it
      is given the capture stage is a FILTER CYCLE carrying it rather than a
      CAPTURE LOOP, and every rule that hangs off the capture stage (the HFR
      watchdog's ``frame`` wire, the doctor's guide/focus/slew checks) follows
      it there, because both node types are capture stages.
    * ``cycles`` -- that cycle's pass count (``perCycle`` stays 1).
    * ``coords`` -- ``(ra, dec)`` written onto the TARGET node. The wizard's own
      sheet only collects a name, so it passes None and the node keeps the
      catalogue-shaped default it has always had.
    * ``safety_abort`` -- mint the SAFETY MONITOR → ABORT + PARK pair even with
      no dome and no notify chip. It is the same ``_safety_pair`` the dome
      branch uses, so a graph never ends up with two monitors.

    A SECOND BUILDER IS THE THING THIS MODULE EXISTS TO PREVENT. The header
    above names drift between two implementations of one rule set as what this
    project keeps re-finding; a "quick" generator that drew its own dusk →
    target → slew → autofocus → guide → report lane would be exactly that, and
    it would drift on the first deviation the doctor forces on one of them.
    """
    kind, opts = _checked(kind, options)
    tname = (target or "").strip()
    # A garbled or non-positive override falls back to the default rather than
    # being honoured: an exposure of 0 compiles to a step that captures nothing,
    # and the wizard's whole promise is that its output runs.
    try:
        unguided_s = float(unguided_exposure_s)
    except (TypeError, ValueError):
        unguided_s = float(UNGUIDED_EXPOSURE_DEFAULT)
    if unguided_s <= 0:
        unguided_s = float(UNGUIDED_EXPOSURE_DEFAULT)
    if unguided_s.is_integer():
        unguided_s = int(unguided_s)      # 30, not 30.0, in the node's params
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
    lane_types += ["cycle" if cycle_plan else "capture", "report"]

    lane = [canvas.add(t, _LANE_X0 + i * _LANE_DX, _LANE_Y)
            for i, t in enumerate(lane_types)]
    for a, b in zip(lane, lane[1:]):
        canvas.chain(a, b)

    for node in lane:
        if node.type == "target":
            if tname:
                node.params["name"] = tname
            if coords is not None:
                # THE COORDINATES ARE THE RUNNABLE PART. A TARGET whose name was
                # replaced and whose ra/dec were not still points at M31 -- the
                # node's default -- so the night would slew to Andromeda and file
                # the frames under the name the operator typed. `to_plan` reads
                # ra/dec and never the name, so this is not cosmetic.
                node.params["ra"], node.params["dec"] = coords
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
                node.params["exposure"] = unguided_s
        elif node.type == "cycle":
            node.params["plan"] = cycle_plan
            node.params["cycles"] = max(1, int(cycles))
            # ONE SUB PER FILTER PER PASS. `cycles` is then literally "how many
            # subs of each filter", which is the number the operator typed.
            node.params["perCycle"] = 1

    # ------------------------------------------------------------ rules row
    rules_x = _RULES_X0

    def rule(node_type: str) -> FlowNode:
        nonlocal rules_x
        node = canvas.add(node_type, rules_x, _RULES_Y)
        rules_x += _RULES_DX
        return node

    # EITHER capture stage. `cycle` and `capture` are siblings, not a loop and
    # its body (nodes.py says so at the type), and both expose `frame` -- so the
    # watchdog wires to whichever one this lane carries.
    capture = next(n for n in lane if n.type in ("capture", "cycle"))
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

    if OPT_DOME in opts or safety_abort:
        # DEVIATION 3 (doctor rule 9, the only DANGER-level check). A dome with
        # no safety monitor is "nothing closes the shutter on rain", and the
        # prototype only ever mints a monitor down in the notify branch — so
        # picking Dome without Notify generated a graph that opens a shutter
        # nothing will close. Built here, before notify, so the two branches
        # share one monitor instead of racing to create two.
        safety = _safety_pair(canvas, paired_with_cloudwatch=cloudwatch is not None)

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
                safety = _safety_pair(
                    canvas, paired_with_cloudwatch=cloudwatch is not None)
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
                    target: str = "",
                    unguided_exposure_s: float | None = None) -> FlowRecord:
    """The generated flow as the library stores it — graph, name and tagline.

    Lands in My flows, never in Examples: the five fixtures are read-only and a
    generated flow is the operator's, to edit from the moment it appears.
    """
    return FlowRecord(
        name=flow_name(kind, target),
        folder=MY_FLOWS_FOLDER,
        tagline=f"Generated by the wizard — {kind.lower()}",
        graph=generate(kind, options, target, unguided_exposure_s))


# ============================================================== the quick flow
#
# ONE SCREEN, FOUR ANSWERS: which target, how many subs of each filter, which
# filters, and whether the guider runs. Everything else is the shape the rig has
# actually shot - the same lane `generate()` draws for a guided deep-sky night,
# with a FILTER CYCLE where the CAPTURE LOOP would be, the relative HFR watchdog
# wired to it, and a safety monitor that aborts and parks.
#
# It is a WRAPPER over `generate()`, not a second generator. See that function's
# docstring for why: the four keyword-only extras it takes exist for this caller
# and for no other, precisely so the deep-sky lane has one definition.

#: A broadband slot's default sub length, and a narrowband one's.
#:
#: Not a recommendation, the same way ``UNGUIDED_EXPOSURE_DEFAULT`` is not: they
#: are the numbers the FILTER CYCLE node itself ships with (nodes.py's default
#: plan reads "L 60, ... Ha 180, ...") so a quick flow opens on the values an
#: operator would have found in the editor, and every one of them is a field on
#: the sheet that generated it.
QUICK_BROADBAND_EXPOSURE_S = 60
QUICK_NARROWBAND_EXPOSURE_S = 180

#: Narrowband spellings, matched case-insensitively BY PREFIX.
#:
#: MIRRORS ``defaultExposureFor`` in ui/src/components/flows/cyclePlanRows.ts,
#: which is where the browser pre-fills the same boxes. Two copies, and they are
#: allowed to be two copies for one reason: this one is a FALLBACK. The sheet
#: sends the exposures it displayed, so the server's table is consulted only by a
#: caller that omitted them entirely - an API client, or a test. If they ever
#: disagree the operator still gets what they saw on screen.
_NARROWBAND_PREFIXES = ("ha", "h-a", "halpha", "h-alpha", "oiii", "o3",
                        "sii", "s2", "nb")
#: Single letters that mean narrowband, matched EXACTLY. This rig's own wheel
#: names its SII slot `S`; no broadband filter is called S, H or O, but plenty
#: start with those letters (`Sloan g`), so a prefix match would be wrong.
_NARROWBAND_LETTERS = ("s", "h", "o")


def default_exposure_s(filter_name: str) -> int:
    """Seconds for a slot nobody gave an exposure for. 180 for narrowband, 60
    otherwise - 60 s of Ha is an empty frame."""
    f = (filter_name or "").strip().lower()
    if f in _NARROWBAND_LETTERS:
        return QUICK_NARROWBAND_EXPOSURE_S
    return (QUICK_NARROWBAND_EXPOSURE_S
            if any(f.startswith(p) for p in _NARROWBAND_PREFIXES)
            else QUICK_BROADBAND_EXPOSURE_S)


def fallback_wheel() -> list[str]:
    """The wheel to offer when no filter wheel is connected.

    READ OFF THE FILTER CYCLE NODE'S OWN DEFAULT PLAN rather than retyped, so
    there is exactly one list of assumed filters on the server and the editor's
    fresh cycle node and a quick flow cannot disagree about what a rig with no
    wheel is presumed to have. (The browser's ``FALLBACK_WHEEL`` is the same
    seven names for the same reason.)

    A fallback, never a merge: a connected wheel replaces this whole.
    """
    return [f for f, _ in parse_cycle_plan(NODE_DEFS["cycle"].params["plan"])]


def cycle_plan_for(filters: Iterable[str],
                   wheel: Iterable[str] | None = None,
                   exposures_s: dict[str, float] | None = None) -> str:
    """The FILTER CYCLE slot table for a set of ticked filters.

    ORDER IS WHEEL ORDER, not the order the client happened to send. The cycle
    shoots its table top to bottom each pass, and a table in click order would
    make two operators who ticked the same five filters get two different
    nights - and neither would match the carousel, so the wheel would take the
    long way round on most passes.

    A NAME THIS WHEEL DOES NOT HAVE IS A REFUSAL. cyclePlanRows.ts opens on why:
    a filter name is not a label, it lands in the FITS ``FILTER`` header, in the
    filename and in the calibration matcher's key. Typing ``OIII`` at a wheel
    whose slot reads ``Oiii`` asks for a filter that does not exist, and the run
    finds out at 21:00 in the dark.
    """
    order = list(wheel) if wheel is not None else fallback_wheel()
    picked = [str(f).strip() for f in filters]
    picked = [f for f in picked if f]
    if not picked:
        raise ValueError("no filters selected; tick at least one")
    unknown = sorted({f for f in picked if f not in order})
    if unknown:
        have = ", ".join(order) if order else "no filters"
        raise ValueError(f"unknown filter(s) {unknown}; this wheel has {have}")
    exp = {str(k): v for k, v in (exposures_s or {}).items()}
    wanted = set(picked)
    slots: list[str] = []
    for f in order:
        if f not in wanted:
            continue
        try:
            secs = int(round(float(exp[f])))
        except (KeyError, TypeError, ValueError):
            secs = default_exposure_s(f)
        if secs <= 0:
            # A zero-second slot is a run-time refusal (`to_plan._cycle_steps`)
            # on a graph that looks fine on the canvas. Fall back rather than
            # persist one.
            secs = default_exposure_s(f)
        slots.append(f"{f} {secs}")
    return ", ".join(slots)


def _quick_coords(target: dict) -> tuple[str, str]:
    """``(ra, dec)`` from the picker's target, or a refusal naming what is off.

    PARSED HERE, at the door, rather than left to the run. ``to_plan`` raises
    ``GraphNotRunnable`` on an unparseable RA - which is correct, but it arrives
    when the operator presses RUN, and by then the sheet that could have fixed
    it is closed. The same strings ``parse_ra``/``parse_dec`` accept are the
    ones the TARGET node stores, so this refuses exactly what the night would.
    """
    ra = str(target.get("ra") or "").strip()
    dec = str(target.get("dec") or "").strip()
    if not ra or not dec:
        raise ValueError("the target needs an RA and a Dec; pick it from the "
                         "catalog rather than typing a name alone")
    try:
        parse_ra(ra)
    except Exception as e:
        raise ValueError(f"cannot read the RA {ra!r}: {e}") from e
    try:
        parse_dec(dec)
    except Exception as e:
        raise ValueError(f"cannot read the Dec {dec!r}: {e}") from e
    return ra, dec


def _one_channel_exposure_s(exposures_s: dict[str, float] | None) -> int:
    """The sub length for a rig with no wheel.

    ``exposures_s`` there carries ONE entry whose key is a label ("OSC"), not a
    slot, because there is no slot - so the value is taken and the key is not
    matched against anything. Anything else falls to the broadband default.
    """
    values = list((exposures_s or {}).values())
    if len(values) == 1:
        try:
            secs = int(round(float(values[0])))
        except (TypeError, ValueError):
            secs = 0
        if secs > 0:
            return secs
    return QUICK_BROADBAND_EXPOSURE_S


def quick(target: dict,
          subs_per_filter: int = 10,
          filters: Iterable[str] | None = None,
          exposures_s: dict[str, float] | None = None,
          guided: bool = True,
          name: str | None = None,
          *,
          wheel: Iterable[str] | None = None) -> FlowRecord:
    """A whole night from four answers, as the library stores it.

    ``target`` is ``{name, ra, dec}`` - the same three strings the TARGET node
    holds, so the picker's row goes onto the canvas unchanged. ``filters`` are
    ticked slot names in any order; ``wheel`` is the rig's carousel, which
    decides both the order they end up in and which names are legal (None means
    the rig has no wheel connected and the assumed list applies).

    AN EMPTY ``filters`` IS THE ONE-CHANNEL RIG, not a mistake. A colour camera
    with no wheel has one channel, so it gets a CAPTURE LOOP with no filter name
    rather than a one-slot FILTER CYCLE: a cycle of one interleaves nothing, and
    the slot's name would be invented - and an invented filter name is what lands
    in the FITS header and in the calibration key.

    Returns a :class:`FlowRecord`, unsaved. The route persists it through the
    same ``_persist_flow`` every other write uses, so the four server-owned
    fields are re-derived here exactly as they are everywhere else.
    """
    tname = str(target.get("name") or "").strip()
    if not tname:
        raise ValueError("the target needs a name")
    coords = _quick_coords(target)
    try:
        subs = int(subs_per_filter)
    except (TypeError, ValueError):
        raise ValueError(f"subs per filter must be a whole number, "
                         f"not {subs_per_filter!r}") from None
    if subs < 1:
        raise ValueError("subs per filter must be at least 1")

    picked = [str(f).strip() for f in (filters or []) if str(f).strip()]
    # The two automation chips this shape is: the guider (when asked for) and
    # the relative HFR watchdog, which is "HFR above (x focus)" at 1.3 - the
    # CONDITION node's own defaults, so nothing here re-types a threshold.
    opts = [OPT_WATCHDOG] + ([OPT_GUIDING] if guided else [])

    if picked:
        plan = cycle_plan_for(picked, wheel, exposures_s)
        graph = generate(KIND_DEEP_SKY, opts, tname,
                         cycle_plan=plan, cycles=subs, coords=coords,
                         safety_abort=True)
        channels = ", ".join(f for f, _ in parse_cycle_plan(plan))
    else:
        graph = generate(KIND_DEEP_SKY, opts, tname,
                         coords=coords, safety_abort=True)
        one = _one_channel_exposure_s(exposures_s)
        cap = next(n for n in graph.nodes if n.type == "capture")
        # No filter NAME on a rig with no wheel: the frames are one channel and
        # writing a label into FILTER would file them under a slot that does not
        # exist. `count` is the sub total, since there is nothing to cycle.
        # `goal` GOES TO 0 with it. The cycle path has no integration target at
        # all (the node has no such param), so leaving the CAPTURE LOOP's shipped
        # 12-hour goal here would make the two halves of one feature promise
        # different things about when the night is finished.
        cap.params.update({"filter": "", "exposure": one, "count": subs,
                           "goal": 0})
        channels = ""

    total = subs * (len(picked) or 1)
    each = f"{subs} subs each of {channels}" if channels else f"{subs} subs"
    return FlowRecord(
        name=(name or f"Quick: {tname}")[:120],
        folder=MY_FLOWS_FOLDER,
        # The tagline is the library card's only line of prose, so it carries
        # what the operator would otherwise have to open the flow to learn.
        tagline=(f"{each} on {tname}: {total} frames, "
                 f"{'guided' if guided else 'unguided'}")[:400],
        graph=graph)
