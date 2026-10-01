"""The Flows node vocabulary — the contract, transcribed from the handoff.

TRANSCRIBED, NOT DESIGNED. Every port id, port kind, label and default parameter
here comes from ``DEFS`` in ``design_handoff_astrodeck_flows/AstroDeck Flows.dc.html``
(README §"Node vocabulary (contract)"). The handoff is explicit that this is a
scoped implementation task: where this file and the prototype disagree, the
prototype is right and this file is a bug.

TWO PORT KINDS, and the distinction is the whole grammar:

* ``flow`` (cyan) — exactly ONE run cursor travels it. This is the sequence: the
  window opens, a target is chosen, the mount slews, focus runs, the loop
  captures. It compiles to a ``SequencePlan``'s ordered targets and steps.
* ``event`` (amber) — may fire at any time, any number of times, including while
  a flow stage is mid-frame. It compiles to an ``Instruction`` when/then rule.

Wiring is only ever kind-to-kind. That is not a UI nicety: a flow edge means
"then", an event edge means "whenever", and the engine runs those through
completely different machinery. Letting an event feed a flow input would ask the
run cursor to be in two places.

WHY A CLOSED VOCABULARY. The README is blunt about it — "resist any 'just add a
script node' temptation". A flow compiles to what the engine ALREADY runs. Every
node here maps to an existing capability or to one named in the backend work
list; none of them can express something the server cannot do, which is what
keeps a graph from promising a night it cannot deliver.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Callable, Literal

PortKind = Literal["flow", "event"]

#: One FILTER CYCLE slot: a filter name, whitespace, whole seconds. Anchored at
#: the start and deliberately tolerant of trailing text, matching the
#: prototype's ``/^(\S+)\s+(\d+)/`` exactly — the parser must not become stricter
#: than the thing that writes the string.
_CYCLE_SLOT_RE = re.compile(r"^(\S+)\s+(\d+)")

#: Category -> the token the UI colours it with (README §"Design tokens"). Kept
#: here beside the node table so a new node cannot be added without one.
CATEGORY_TOKEN = {
    "SOURCE": "--accent",
    "RIG": "--sky",
    "LOGIC": "--accent-dim",
    "ACTION": "--warn",
    "SINK": "--good",
}


@dataclass(frozen=True)
class Port:
    id: str
    label: str
    kind: PortKind


@dataclass(frozen=True)
class Derived:
    """A missing-key default another module owns, read each time the table is
    read rather than once when it is built (#461).

    One so far: TARGET's ``overlap``, which is ``catalog.framing``'s
    ``DEFAULT_OVERLAP`` in percent (``target_overlap_pct``), the one overlap
    every framing starts from (spec 2.4, S6). Until S7 the table wrote 25 of
    its own, a fourth copy of a number S6 made one constant. It cannot be read
    when this table is built: ``catalog.framing`` imports ``flows.identity``,
    and so this whole package, so a process that imports framing first builds
    this table while framing's constant is not yet bound; and importing
    framing at the top of this module would load the catalogue and the web
    stack with every flow, which ``doctor.py`` avoids for the same reason.
    ``NodeDef.params`` reads each ``Derived`` value as it is asked for, so no
    reader ever meets one: the table as read holds numbers, in the order it
    was written.

    NEVER READ WHILE THE PACKAGE LOADS. ``read`` imports framing, so a module
    that read a TARGET's defaults at import time would meet the half-loaded
    framing this exists to avoid. Nothing does; ``test_overlap_constant_one``
    loads both orders in a fresh interpreter."""
    read: Callable[[], object]


def target_overlap_pct() -> int | float:
    """TARGET's ``overlap`` default: ``catalog.framing.DEFAULT_OVERLAP``, a
    fraction, as the percent a TARGET holds (#461; spec 2.4, S6's one overlap
    constant). ``nodeDefs.ts`` writes the same ``DEFAULT_OVERLAP * 100`` from
    its mirror of the constant, one IEEE product, so the two sides agree to
    the bit. A whole percent is an int, as the 25 it replaces was: the
    inspector coerces an edit by the type of the default, and the vocabulary's
    missing-key column is pinned by type.

    A MISSING-KEY DEFAULT THAT CAN MOVE. A multi-panel TARGET whose stored
    params hold no ``overlap`` reads as this, so re-deciding DEFAULT_OVERLAP
    would re-mean such a block, which is the "semantics flip needs a
    migration" class; a single panel is keyed on ``identity.SINGLE_OVERLAP``,
    a stored contract, and never reads this."""
    # Imported here, not at the top: see ``Derived``.
    from ..catalog import framing
    pct = framing.DEFAULT_OVERLAP * 100
    return int(pct) if float(pct).is_integer() else pct


@dataclass(frozen=True)
class NodeDef:
    """One node type's contract: what it is called, what it connects to, and what
    it starts life configured as."""
    type: str
    label: str
    cat: str
    ins: tuple[Port, ...] = ()
    outs: tuple[Port, ...] = ()
    params: dict = field(default_factory=dict)
    #: Inputs that may be left unwired without the doctor complaining, because
    #: the node WORKS without the wire. Either a separate rule explains the
    #: absence in its own words (``calib.panel`` -> rule 7, flats get skipped),
    #: or the engine already does the thing by another route and
    #: ``to_plan.REDUNDANT_PORTS`` / ``HOLD_HONOURED`` says so:
    #:
    #: * ``calib.do`` / ``calib.stop`` - ``plan_extras`` funds cloud-hold darks
    #:   from the queue's QUOTA alone, and the hold ends when the sky clears.
    #: * ``holdresume.resume`` - the hold releases itself.
    #: * ``parkclose.do`` - the night ends parked with the cover shut regardless.
    #: * ``pool.advance`` - the scheduler advances the pool from the ledger.
    #:
    #: `test_flows_doctor_agrees_with_the_engine` asserts structurally that
    #: nothing can be in one of those tables AND demanded by doctor rule 1.
    optional_ins: frozenset[str] = frozenset()
    #: THE "CREATED AS" COLUMN (spec 3.1): overrides written out when a node
    #: is CREATED (a palette drop, the wizard, a fixture), never when one is
    #: loaded. ``params`` are the missing-key defaults, and they must keep the
    #: meaning a key had before it existed, because a saved flow that lacks
    #: the key is read through them; a new choice goes here instead. That is
    #: the "semantics flip needs a migration" class closed structurally: a
    #: TARGET saved on S2 still counts every sub taken, while every new block
    #: is written with "Accepted subs" and cannot be re-meant by a stale tab
    #: POSTing an old-shaped graph either. A key may name a derived param
    #: that has no missing-key default at all (TARGET's `angle`).
    created_as: dict = field(default_factory=dict)

    def __getattribute__(self, name: str):
        """``params`` with each ``Derived`` default read now (see
        ``Derived``), as a fresh dict in the table's order, so no reader of
        the table, direct or through :func:`default_params`, ever meets one;
        every other attribute, and a table with nothing derived, as stored."""
        value = object.__getattribute__(self, name)
        if name == "params" and any(isinstance(v, Derived)
                                    for v in value.values()):
            return {k: v.read() if isinstance(v, Derived) else v
                    for k, v in value.items()}
        return value

    @property
    def create_params(self) -> dict:
        """The params a freshly created node is written with: the defaults
        overlaid with the Created-as column. A fresh dict on every call, for
        the same reason as :func:`default_params`."""
        merged = dict(self.params)
        merged.update(self.created_as)
        return merged

    def port(self, port_id: str, direction: str) -> Port | None:
        for p in (self.ins if direction == "in" else self.outs):
            if p.id == port_id:
                return p
        return None


def _f(pid: str, label: str) -> Port:
    return Port(pid, label, "flow")


def _e(pid: str, label: str) -> Port:
    return Port(pid, label, "event")


NODE_DEFS: dict[str, NodeDef] = {
    # ---------------------------------------------------------------- SOURCES
    "dusk": NodeDef(
        type="dusk", label="DUSK WINDOW", cat="SOURCE",
        # `nightend` is what makes a CAMPAIGN differ from a night: it fires
        # BEFORE dawn so a shutdown lane can run while there is still time to
        # run it. With `repeat` set, dawn stops being the end of the run and
        # becomes a scheduled hold — the capture cursor survives it and the flow
        # re-arms at the next dusk, mid-cycle.
        outs=(_f("window", "window opens"), _e("nightend", "night ends")),
        # `startClock`/`stopClock` are "HH:MM" text, read only when `start` or
        # `stop` is "Clock time" (#191): before these existed the choice had
        # nowhere to write its time, so picking "Clock time" compiled to the
        # same sun-based start every other choice did, silently. Blank by
        # default, as an operator who has not picked the clock option yet
        # has typed no time.
        params={"start": "Astro dusk", "offset": -30, "startClock": "",
                "stop": "Dawn", "stopClock": "", "minAlt": 30,
                "repeat": "Single night"}),
    "target": NodeDef(
        type="target", label="TARGET", cat="SOURCE",
        # ONE BLOCK, ONE OR MANY PANELS (spec 1.2). `next` is the panel loop's
        # socket: the dashed "pass done" wire from the tail of this block's
        # panel lane lands here and the compile consumes it as structure. It
        # is an EVENT input, so the flow lane stays acyclic and every type
        # still has at most one flow input, and it is OPTIONAL, because a
        # single target (and a mosaic shot panel-first) never wires it.
        ins=(_f("arm", "arm"), _e("next", "next panel")),
        optional_ins=frozenset({"next"}),
        # The id stays `target` so every saved wire survives; the label says
        # what the wire now carries: the stages after it run once per panel.
        outs=(_f("target", "each panel"),),
        # `rotation` -1 IS "ANY ANGLE" (#150). It was 23.4 -- the M31 example's
        # own angle, copied in as the palette default -- and anything 0 or
        # above is a real position angle to `to_plan`, so every palette-dropped,
        # wizard and quick-flow target commanded a connected rotator to PA 23.4,
        # an angle nobody chose. The wizard overwrites only the name and the
        # coordinates, so the default IS the angle for most flows. Stored 23.4s
        # are rewritten by `store._migrate` (FLOW_SCHEMA 3). Still a number:
        # the inspector coerces an edit by the type of the default.
        #
        # THE MOSAIC KEYS (spec 3.1) default to what a TARGET saved before
        # them meant: one panel, no camera field recorded, today's hub
        # centring (0.02 deg is 1.2 arcmin, 3 attempts), and counting every sub
        # taken. `counts` is not offered in the editor (Revision 2, ruling 2);
        # a save switches it. `frameAnchor` is written by the server at save
        # (ruling 3). `angle` HAS NO DEFAULT HERE on purpose: it is derived
        # from `rotation` (see `target_angle`), so a stored block with a real
        # PA keeps commanding it. The name, ra and dec keep today's defaults
        # so a stored TARGET with no coordinates still means M31; only a NEW
        # block is written blank (`created_as`), which is what stops a typed
        # name landing on M31's coordinates (#190). `overlap` is the one
        # overlap every framing starts from, `framing.DEFAULT_OVERLAP` in
        # percent (25), read when the table is read (`Derived`, #461).
        params={"name": "M31 - Andromeda", "ra": "00h 42m 44s",
                "dec": "+41° 16′ 09″", "rotation": -1,
                "rows": 1, "cols": 1, "overlap": Derived(target_overlap_pct),
                "fovX": 0, "fovY": 0,
                "fovFrom": "", "skip": "", "passes": 1, "minVisit": 0,
                "order": "Least complete first", "centerTol": 1.2,
                "centerTries": 3, "ifNotCentred": "Auto",
                "counts": "Every sub taken", "frameAnchor": ""},
        # Rulings 2 and 9: a new block counts accepted subs only and carries no
        # angle nobody chose.
        created_as={"name": "", "ra": "", "dec": "", "rotation": -1,
                    "angle": "Any angle", "counts": "Accepted subs"}),
    "safety": NodeDef(
        type="safety", label="SAFETY MONITOR", cat="SOURCE",
        outs=(_e("unsafe", "unsafe"),),
        # `watch` SCOPES the fail-closed tier away from clouds. Safety aborts and
        # never holds; CLOUD WATCH holds and never aborts. A safety monitor also
        # watching clouds beats the hold to the punch every time, so the two
        # tiers race and the recoverable one always loses (doctor rule 13).
        params={"source": "Cloud + rain sensor",
                "watch": "Clouds + rain + wind (standalone)",
                "stale": "Unsafe (fail closed)"}),
    "cloudwatch": NodeDef(
        type="cloudwatch", label="CLOUD WATCH", cat="SOURCE",
        outs=(_e("in", "clouds in"), _e("clear", "clouds clear")),
        params={"source": "Sky quality sensor", "threshold": 40, "clearFor": 4}),
    # -------------------------------------------------------------- EQUIPMENT
    "dome": NodeDef(
        type="dome", label="DOME CONTROL", cat="RIG",
        ins=(_f("run", "open"),), outs=(_f("open", "shutter open"),),
        # BIND, never "slave" — the handoff's do-not list names the word in UI
        # labels, code identifiers, API fields and comments alike, and a param
        # key is all four at once.
        params={"bind": "Bind to mount", "onUnsafe": "Close (fail closed)",
                "timeout": 120}),
    "flatpanel": NodeDef(
        type="flatpanel", label="FLAT PANEL", cat="RIG",
        outs=(_e("ready", "panel ready"),),
        params={"position": "Dust-cover panel", "adu": 28500,
                "solve": "Solve per filter"}),
    # ---------------------------------------------------------------- RIG OPS
    # LEGACY (spec 1.7, see LEGACY_TYPES): centring is part of the TARGET
    # block now. Kept so every saved graph loads -- `FlowNode._known_type`
    # refuses an unknown type before any migration could fold it away -- and
    # hidden from the palette. Its `tol`, `retries` and `solver` never reached
    # the run (the hub's 0.02 deg and 3 attempts did), so they are NOT carried
    # onto the TARGET: carrying `tol 0.5` would silently tighten every saved
    # flow's centring.
    "slew": NodeDef(
        type="slew", label="SLEW + CENTER", cat="RIG",
        ins=(_f("run", "run"),), outs=(_f("centered", "centered"),),
        params={"tol": 0.5, "retries": 3, "solver": "ASTAP"}),
    # AUTOFOCUS AND GUIDE CARRY `pass` TOO (S4, #331). Either can be the last
    # stage of a panel lane (a lane may end on any lane node, spec 1.5), and
    # the loop wire must leave the last stage. Without the port, appending
    # one after a looped FILTER CYCLE left the loop mid-lane (M12) with no
    # wire the editor could carry it to, and the mosaic stopped running until
    # the operator rewired it by hand. Structural, exactly as on CAPTURE LOOP
    # below, and appended last so `focused` and `guiding` keep their rows.
    "autofocus": NodeDef(
        type="autofocus", label="AUTOFOCUS", cat="RIG",
        ins=(_f("run", "run"),),
        outs=(_f("focused", "focused"), _e("pass", "pass done")),
        params={"method": "V-curve sweep", "step": 12, "samples": 9}),
    "guide": NodeDef(
        type="guide", label="GUIDE", cat="RIG",
        ins=(_f("run", "run"),),
        outs=(_f("guiding", "guiding"), _e("pass", "pass done")),
        params={"provider": "PHD2", "settle": 1.5, "dither": 3}),
    "capture": NodeDef(
        type="capture", label="CAPTURE LOOP", cat="RIG",
        ins=(_f("run", "run"),),
        # `pass` IS STRUCTURAL (spec 1.3): it means something only leaving the
        # tail of a panel lane for the owning TARGET's `next`, where it makes
        # the panels rotate every pass. Appended last so the existing ports
        # keep their places on the card. `complete` reads "all done" because on
        # a mosaic it fires once, when every panel owes nothing.
        outs=(_f("complete", "all done"), _e("frame", "frame graded"),
              _e("pass", "pass done")),
        params={"filter": "L", "exposure": 120, "gain": 100, "bin": "1",
                "count": 24, "reject": 3.5, "goal": 12}),
    "cycle": NodeDef(
        type="cycle", label="FILTER CYCLE", cat="RIG",
        # A SIBLING OF `capture`, NOT A LOOP CONTAINER. The 2026-08-14 handoff
        # is explicit: "no loop construct exists at graph level — the graph stays
        # acyclic, loops live inside stages, and campaign loops are event wires".
        # So this is one capture stage that happens to interleave: it shoots its
        # slot table one sub per filter per pass and repeats until every slot has
        # its cycle count.
        #
        # WHY INTERLEAVE AT ALL: channels grow evenly, so a night cut short by
        # cloud still stacks. Forty-five L followed by nothing else is a mono
        # image; one of each, forty-five times, is an image at every prefix.
        ins=(_f("run", "run"),),
        # The same `pass` / "all done" pair as CAPTURE LOOP, for the same
        # reason: this is the stage that most often ends a panel lane.
        outs=(_f("complete", "all done"), _e("frame", "frame graded"),
              _e("pass", "pass done")),
        # `plan` is the slot table, stored as the prototype's `parsePlan` text:
        # "<filter> <seconds>" comma-separated, in wheel order. The INSPECTOR
        # never lets it be typed — the handoff requires one row per filter in the
        # rig's actual wheel, so a filter name that is not in the wheel cannot be
        # entered. The string is the storage format, not the input method.
        params={"plan": "L 60, R 60, G 60, B 60, Ha 180, OIII 180, SII 180",
                "cycles": 45, "perCycle": 1, "gain": 100, "bin": "1",
                "reject": 3.5}),
    "duskflats": NodeDef(
        type="duskflats", label="DUSK FLATS", cat="RIG",
        ins=(_f("run", "run"),), outs=(_f("done", "flats done"),),
        params={"method": "Translucent lens cap", "window": "Sun −2° … −8°",
                "filters": "Tonight's plan only", "adu": 28500, "count": 15}),
    "calib": NodeDef(
        type="calib", label="CALIBRATION QUEUE", cat="RIG",
        # ALL THREE INPUTS ARE OPTIONAL, and the queue still works with none of
        # them wired.
        #
        # `panel` — a queue with no panel is a working queue that skips flats,
        # and rule 7 says so in its own words rather than as an "unwired input"
        # complaint that would read like a mistake.
        #
        # `do` / `stop` — `to_plan.plan_extras` funds `cloud_hold_darks` from
        # this node's QUOTA alone, so a queue nobody wired still tops the dark
        # library up during a weather hold, and the hold ends when the sky
        # clears without anything telling it to. The doctor spent releases
        # telling operators to wire two ports the engine does not consult, and
        # `to_plan.REDUNDANT_PORTS` said the opposite one panel away.
        # (`day_darks` IS keyed on the operator's own wire — see plan_extras —
        # so a shutdown lane still buys something; it is just not required.)
        ins=(_e("do", "do"), _e("stop", "stop"), _e("panel", "panel")),
        optional_ins=frozenset({"panel", "do", "stop"}),
        params={"darks": "If library stale", "bias": "If library stale",
                "flats": "If stale + panel wired", "blackSlot": "Rotate to black slot",
                "quota": 20, "dest": "captures/Calibration/"}),
    # ------------------------------------------------------------------ LOGIC
    "pool": NodeDef(
        type="pool", label="TARGET POOL", cat="LOGIC",
        # `advance` is the campaign's loop-back. It is an EVENT input, and it is
        # optional, because a single-night pool never needs one: the flow lane
        # stays acyclic and the loop is drawn as SESSION REPORT 'target done' ->
        # here, which is an event wire and so may legally point backwards.
        ins=(_f("arm", "arm"), _e("advance", "advance")),
        optional_ins=frozenset({"advance"}),
        # `floor` is the other half of an unattended campaign: when the active
        # target sinks to the altitude floor the scheduler sets THAT target
        # aside for tonight — set aside, not done: its frames stay owed, and
        # since S2 its record in `Session.set_aside` means a restart tonight
        # does not retry it and the next night does (#208) — and hands out the
        # next best member. The `onFloor` value below is never reworded: saved
        # flows store it and compile.py matches only its verb. Since S2 it is
        # also true.
        outs=(_f("target", "best target"), _e("floor", "floor hit")),
        #
        # `counts` gets TARGET's treatment (Revision 2, ruling 2): missing-key
        # "Every sub taken", created "Accepted subs", so a new pool-only flow
        # is not the one new flow that counts rejects.
        params={"members": "M16, M17, M8, NGC 6946",
                "strategy": "Best available (alt × moon)", "quota": 45,
                "minAlt": 30, "onFloor": "Advance now; retry it next night",
                "moonSep": 40, "maxHA": 4, "counts": "Every sub taken"},
        created_as={"counts": "Accepted subs"}),
    "condition": NodeDef(
        type="condition", label="CONDITION", cat="LOGIC",
        ins=(_e("events", "events"),), outs=(_e("fire", "fire"),),
        params={"when": "HFR above (x focus)", "threshold": 1.3, "window": "3 frames",
                "once": "Every time"}),
    # -------------------------------------------------------- ACTIONS + SINKS
    "holdresume": NodeDef(
        type="holdresume", label="HOLD / RESUME", cat="ACTION",
        # `resume` is OPTIONAL and `pause` is not, and the asymmetry is the
        # whole behaviour: a hold with nothing wired to `pause` never triggers,
        # which is a broken graph; a hold with nothing wired to `resume`
        # releases itself when the sky clears, which is how it already worked.
        # `to_plan.REDUNDANT_PORTS` has said so about this exact wire since it
        # shipped, while the doctor went on demanding it.
        ins=(_e("pause", "pause"), _e("resume", "resume")),
        optional_ins=frozenset({"resume"}),
        # THE COOLER GATE COMES FIRST, and the ordering is the whole point. A
        # hold can outlive the thing that was keeping the sensor cold: a daybreak
        # park warms it by design, a power cycle drops the TEC, a cooler fault
        # leaves it drifting. Resuming into that shoots warm subs against a cold
        # dark library, which is exactly what happened on 2026-08-12 — 63 frames
        # at ambient because a restart raced the camera's connect.
        #
        # So the checklist is: re-cool and stabilize -> restore the filter ->
        # re-center -> refocus -> re-settle the guider -> same slot. Pointing and
        # focus are worth nothing on a frame the temperature already ruined.
        params={"whilePaused": "Keep tracking, park guider", "maxHold": 45,
                "onTimeout": "Abort + park",
                "cooler": "Re-cool + stabilize before capture",
                "recenter": "Re-center (plate solve)",
                "refocus": "If HFR drifted"}),
    "notify": NodeDef(
        type="notify", label="NOTIFY", cat="ACTION",
        ins=(_e("do", "do"),),
        params={"sink": "ntfy", "channel": "rig-alerts", "level": "warning"}),
    "refocus": NodeDef(
        type="refocus", label="REFOCUS", cat="ACTION",
        ins=(_e("do", "do"),),
        params={"boundary": "Next frame boundary"}),
    "parkclose": NodeDef(
        type="parkclose", label="PARK + CLOSE", cat="ACTION",
        # A SCHEDULED SHUTDOWN, NOT AN ABORT, and the difference is the campaign
        # cursor. ABORT + PARK ends a run; this ends a NIGHT and leaves the run
        # able to pick up where it stopped. `closed` then chains into a
        # CALIBRATION QUEUE so the day is spent on darks that match the night —
        # which is only true if the cooler was held cold, hence the param.
        # `do` is OPTIONAL, and found by the structural check rather than by
        # reading: the night already ends parked with the dust cover shut
        # whether or not this wire is here — every flow's plan carries
        # park-when-done and the wind-down closes the cover — which is what
        # `to_plan.REDUNDANT_PORTS` says about this exact port. Demanding it
        # sent the operator to draw a wire the adapter calls redundant. Whether
        # the ROOF closes depends on config, not on this node, and rule 9 is
        # what speaks about that.
        ins=(_e("do", "do"),), outs=(_e("closed", "closed"),),
        optional_ins=frozenset({"do"}),
        params={"closure": "Dust flap + dome", "cooler": "Hold cold (day darks)",
                "tracking": "Park"}),
    "abort": NodeDef(
        type="abort", label="ABORT + PARK", cat="ACTION",
        ins=(_e("do", "do"),),
        params={"park": "Yes", "warm": "Yes", "message": "Safety monitor unsafe"}),
    "report": NodeDef(
        type="report", label="SESSION REPORT", cat="SINK",
        ins=(_f("session", "session"),),
        # `done` is what closes a campaign's loop: it fires when the ACTIVE
        # target's quota is met, and wiring it back to a pool's `advance` is the
        # whole mechanism. It is an event, so the backward wire is legal and the
        # flow lane stays a DAG.
        outs=(_e("done", "target done"),),
        params={"format": "JSON + FITS index", "dest": "captures/sessions/"}),
}

#: Types that still LOAD but are no longer OFFERED (spec 1.7). A saved graph
#: may carry one and keeps working; the palette omits it, and the UI mirror
#: (`nodeDefs.ts` `legacy`) and its parity test read this set. Still 21 types
#: in NODE_DEFS, one of them legacy.
LEGACY_TYPES: frozenset[str] = frozenset({"slew"})

#: Palette grouping, in the order the rail renders them (README §3).
#:
#: ITEM ORDER inside LOGIC and ACTIONS + SINKS was the handoff's §G-3 dispute:
#: three sources, no two agreeing. The 2026-08-14 prototype settles it by being
#: the only source that names every current type — it is authority level 3, and
#: the README (level 1) still specifies group names and group order only. So
#: these five rows are the prototype's `groups` array, verbatim, except that
#: SLEW + CENTER has left RIG OPS (spec 1.7): it is in LEGACY_TYPES, so every
#: type is offered exactly once EXCEPT the legacy ones.
PALETTE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("SOURCES", ("dusk", "target", "safety", "cloudwatch")),
    ("EQUIPMENT", ("dome", "flatpanel")),
    ("RIG OPS", ("autofocus", "guide", "capture", "cycle",
                 "duskflats", "calib")),
    ("LOGIC", ("condition", "pool")),
    ("ACTIONS + SINKS", ("notify", "refocus", "holdresume", "parkclose",
                         "abort", "report")),
)


def port_kind(node_type: str, port_id: str, direction: str) -> PortKind | None:
    """The kind of one port, or None when the node type or port is unknown.

    Returns None rather than raising: a graph arriving over the API may name a
    node type this build does not have (an older or newer client), and the
    honest response is "I cannot type that edge", which the validator turns into
    a refusal — not a 500.
    """
    d = NODE_DEFS.get(node_type)
    if d is None:
        return None
    p = d.port(port_id, direction)
    return None if p is None else p.kind


def parse_cycle_plan(plan) -> list[tuple[str, int]]:
    """Decode a FILTER CYCLE slot table into ``[(filter, exposure_s), …]``.

    The storage format is the prototype's ``parsePlan``: comma-separated
    ``"<filter> <seconds>"`` in wheel order, e.g.
    ``"L 60, R 60, G 60, B 60, Ha 180, OIII 180, SII 180"``.

    UNPARSEABLE ENTRIES ARE DROPPED, not defaulted, and that is deliberate. A
    slot nobody can read is a slot nobody can shoot; inventing an exposure for it
    would put frames on disk under a filter the operator never asked for. An
    empty result makes the stage contribute nothing, which the doctor and the
    compile both notice — silence here would not be noticed by either.

    A DIGIT RUN ``int()`` REFUSES IS UNPARSEABLE TOO (#441). Past CPython's
    integer string limit (4300 digits, leading zeros counted) ``int()``
    raises ``ValueError``, and unguarded that reached ``compile_plan`` and
    ``doctor.check`` from a flow validation lets a save store, so the flow
    never compiled again. An exposure that long is no exposure, so its slot
    is dropped like any other slot nobody can read.
    """
    out: list[tuple[str, int]] = []
    for chunk in str(plan or "").split(","):
        m = _CYCLE_SLOT_RE.match(chunk.strip())
        if m:
            try:
                seconds = int(m.group(2))
            except ValueError:
                continue
            out.append((m.group(1), seconds))
    return out


def default_params(node_type: str) -> dict:
    """A fresh copy of a node type's defaults. A copy, because a node dropped on
    the canvas is then edited, and the table must not be edited with it.

    These are the MISSING-KEY defaults, what ``FlowNode.with_defaults`` merges
    under a loaded node. A node being CREATED takes :func:`create_params`."""
    d = NODE_DEFS.get(node_type)
    return {} if d is None else dict(d.params)


def create_params(node_type: str) -> dict:
    """The params a node of this type is CREATED with: the defaults overlaid
    with its Created-as column (``NodeDef.created_as``, spec 3.1). The palette
    drop, the wizard and the Example fixtures take this; loading never does.
    ``{}`` for an unknown type, like :func:`default_params`."""
    d = NODE_DEFS.get(node_type)
    return {} if d is None else d.create_params


#: TARGET's `angle` choices (spec 2.4 ANGLE, 3.1), in the order the editor
#: offers them. Stored verbatim in saved flows, so never reworded.
TARGET_ANGLES: tuple[str, ...] = ("Any angle", "Rotate to PA",
                                  "Camera fixed at PA")

#: TARGET's and POOL's `counts` values (Revision 2, ruling 2): the old meaning
#: first, then what every new block is created with. `plan.count_mode` is
#: "attempts" for the first and "accepted" for the second.
COUNT_MODES: tuple[str, ...] = ("Every sub taken", "Accepted subs")


def _rotation_deg(value) -> float:
    """`rotation` as a number, with the run's reading of a bad value: an
    unparseable, empty or non-finite field is -1, "no constraint", never PA 0.

    The run reads it in two steps, and this is both at once: compile's
    ``_num(rotation, -1)`` makes an unparseable or empty field -1, but it
    passes "inf" or "nan" through as a float, and ``to_plan._number`` then
    reads a non-finite rotation as no angle. (Python's ``float`` also takes
    forms such as "1_0" that ``nodeDefs.ts``'s stricter ``DECIMAL`` refuses,
    so a raw string only a hand-written graph can hold may read "Rotate to
    PA" here and "Any angle" in the inspector; an edit in either editor
    stores a number.)

    OverflowError too (#362 item 3): ``float()`` of an integer past a
    float's range raises that, not ValueError, and a raw POST can hold a
    400-digit one. It is no number, so it is no constraint; uncaught, it
    raised out of ``compile_plan`` (through ``compile.angle_code``) and out
    of the doctor (``_pa``), which the compile routes run on every draft."""
    try:
        v = float(value)
    except (TypeError, ValueError, OverflowError):
        return -1.0
    return v if math.isfinite(v) else -1.0


def target_angle(params: dict) -> str:
    """The angle a TARGET node means: its stored `angle` when it has one,
    otherwise the one derived from `rotation` (spec 3.1).

    DERIVED, NEVER DEFAULTED, and the difference is the whole point. A TARGET
    saved before `angle` existed carries only `rotation`; a missing-key "Any
    angle" would turn one that commands the rotator to PA 23.4 into one that
    commands nothing. So a negative rotation is "Any angle" and anything else,
    0 included (north up is a real PA, #150), is "Rotate to PA". The inspector
    shows the same derivation (`nodeDefs.ts` `targetAngle`) for a node with no
    angle key, and writes nothing until the operator picks one.
    """
    stored = (params or {}).get("angle")
    if stored not in (None, ""):
        return str(stored)
    return ("Any angle" if _rotation_deg((params or {}).get("rotation")) < 0
            else "Rotate to PA")
