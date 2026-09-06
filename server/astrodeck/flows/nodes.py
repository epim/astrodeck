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

import re
from dataclasses import dataclass, field
from typing import Literal

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
        params={"start": "Astro dusk", "offset": -30, "stop": "Dawn",
                "minAlt": 30, "repeat": "Single night"}),
    "target": NodeDef(
        type="target", label="TARGET", cat="SOURCE",
        ins=(_f("arm", "arm"),), outs=(_f("target", "target"),),
        params={"name": "M31 - Andromeda", "ra": "00h 42m 44s",
                "dec": "+41° 16′ 09″", "rotation": 23.4}),
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
    "slew": NodeDef(
        type="slew", label="SLEW + CENTER", cat="RIG",
        ins=(_f("run", "run"),), outs=(_f("centered", "centered"),),
        params={"tol": 0.5, "retries": 3, "solver": "ASTAP"}),
    "autofocus": NodeDef(
        type="autofocus", label="AUTOFOCUS", cat="RIG",
        ins=(_f("run", "run"),), outs=(_f("focused", "focused"),),
        params={"method": "V-curve sweep", "step": 12, "samples": 9}),
    "guide": NodeDef(
        type="guide", label="GUIDE", cat="RIG",
        ins=(_f("run", "run"),), outs=(_f("guiding", "guiding"),),
        params={"provider": "PHD2", "settle": 1.5, "dither": 3}),
    "capture": NodeDef(
        type="capture", label="CAPTURE LOOP", cat="RIG",
        ins=(_f("run", "run"),),
        outs=(_f("complete", "complete"), _e("frame", "frame graded")),
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
        outs=(_f("complete", "complete"), _e("frame", "frame graded")),
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
        # target sinks to the altitude floor the scheduler suspends THAT target's
        # cursor — suspended, not done, so it is retried next night — and hands
        # out the next best member.
        outs=(_f("target", "best target"), _e("floor", "floor hit")),
        params={"members": "M16, M17, M8, NGC 6946",
                "strategy": "Best available (alt × moon)", "quota": 45,
                "minAlt": 30, "onFloor": "Advance now; retry it next night",
                "moonSep": 40, "maxHA": 4}),
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

#: Palette grouping, in the order the rail renders them (README §3).
#:
#: ITEM ORDER inside LOGIC and ACTIONS + SINKS was the handoff's §G-3 dispute:
#: three sources, no two agreeing. The 2026-08-14 prototype settles it by being
#: the only source that names every current type — it is authority level 3, and
#: the README (level 1) still specifies group names and group order only. So
#: these five rows are the prototype's `groups` array, verbatim.
PALETTE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("SOURCES", ("dusk", "target", "safety", "cloudwatch")),
    ("EQUIPMENT", ("dome", "flatpanel")),
    ("RIG OPS", ("slew", "autofocus", "guide", "capture", "cycle",
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
    """
    out: list[tuple[str, int]] = []
    for chunk in str(plan or "").split(","):
        m = _CYCLE_SLOT_RE.match(chunk.strip())
        if m:
            out.append((m.group(1), int(m.group(2))))
    return out


def default_params(node_type: str) -> dict:
    """A fresh copy of a node type's defaults. A copy, because a node dropped on
    the canvas is then edited, and the table must not be edited with it."""
    d = NODE_DEFS.get(node_type)
    return {} if d is None else dict(d.params)
