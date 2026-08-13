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

from dataclasses import dataclass, field
from typing import Literal

PortKind = Literal["flow", "event"]

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
    #: Inputs that may be left unwired without the doctor complaining. Only
    #: ``panel`` today — the calibration queue works fine without a flat panel,
    #: it simply skips flats, and the doctor says THAT separately (rule 7).
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
        outs=(_f("window", "window opens"),),
        params={"start": "Astro dusk", "offset": -30, "stop": "Dawn", "minAlt": 30}),
    "target": NodeDef(
        type="target", label="TARGET", cat="SOURCE",
        ins=(_f("arm", "arm"),), outs=(_f("target", "target"),),
        params={"name": "M31 — Andromeda", "ra": "00h 42m 44s",
                "dec": "+41° 16′ 09″", "rotation": 23.4}),
    "safety": NodeDef(
        type="safety", label="SAFETY MONITOR", cat="SOURCE",
        outs=(_e("unsafe", "unsafe"),),
        params={"source": "Cloud + rain sensor", "stale": "Unsafe (fail closed)"}),
    "cloudwatch": NodeDef(
        type="cloudwatch", label="CLOUD WATCH", cat="SOURCE",
        outs=(_e("in", "clouds in"), _e("clear", "clouds clear")),
        params={"source": "Sky quality sensor", "threshold": 40, "clearFor": 4}),
    # -------------------------------------------------------------- EQUIPMENT
    "dome": NodeDef(
        type="dome", label="DOME CONTROL", cat="RIG",
        ins=(_f("run", "open"),), outs=(_f("open", "shutter open"),),
        params={"slave": "Slave to mount", "onUnsafe": "Close (fail closed)",
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
    "duskflats": NodeDef(
        type="duskflats", label="DUSK FLATS", cat="RIG",
        ins=(_f("run", "run"),), outs=(_f("done", "flats done"),),
        params={"method": "Translucent lens cap", "window": "Sun −2° … −8°",
                "filters": "Tonight's plan only", "adu": 28500, "count": 15}),
    "calib": NodeDef(
        type="calib", label="CALIBRATION QUEUE", cat="RIG",
        # `panel` is OPTIONAL: a queue with no panel is a working queue that
        # skips flats, and rule 7 says so in its own words rather than as an
        # "unwired input" complaint that would read like a mistake.
        ins=(_e("do", "do"), _e("stop", "stop"), _e("panel", "panel")),
        optional_ins=frozenset({"panel"}),
        params={"darks": "If library stale", "bias": "If library stale",
                "flats": "If stale + panel wired", "blackSlot": "Rotate to black slot",
                "quota": 20, "dest": "captures/Calibration/"}),
    # ------------------------------------------------------------------ LOGIC
    "pool": NodeDef(
        type="pool", label="TARGET POOL", cat="LOGIC",
        ins=(_f("arm", "arm"),), outs=(_f("target", "best target"),),
        params={"members": "M16, M17, M8, NGC 6946",
                "strategy": "Best available (alt × moon)", "minAlt": 30,
                "moonSep": 40, "maxHA": 4}),
    "cycle": NodeDef(
        type="cycle", label="FILTER CYCLE", cat="LOGIC",
        # ONE PASS PER VISIT, many passes per night. The captures on this
        # target become one round: L R G B S Ha O3, and `cycles` says how many
        # times to go round. Each capture's own `count` is what it takes ON
        # each pass, so "1" and 45 cycles is forty-five subs of every filter.
        #
        # It applies to the target's whole capture chain rather than to a
        # sub-graph, because the graph model is flat — there is no container to
        # put a body inside. That is a real limit and `to_plan` reports it
        # rather than letting a second CYCLE node look like it does something.
        ins=(_f("run", "run"),),
        outs=(_f("body", "each pass"), _f("complete", "all passes")),
        params={"cycles": 45, "order": "As drawn"}),
    "condition": NodeDef(
        type="condition", label="CONDITION", cat="LOGIC",
        ins=(_e("events", "events"),), outs=(_e("fire", "fire"),),
        params={"when": "HFR above", "threshold": 3.2, "window": "3 frames",
                "once": "Every time"}),
    # -------------------------------------------------------- ACTIONS + SINKS
    "holdresume": NodeDef(
        type="holdresume", label="HOLD / RESUME", cat="ACTION",
        ins=(_e("pause", "pause"), _e("resume", "resume")),
        params={"whilePaused": "Keep tracking, park guider", "maxHold": 45,
                "onTimeout": "Abort + park", "recenter": "Re-center (plate solve)",
                "refocus": "If HFR drifted"}),
    "notify": NodeDef(
        type="notify", label="NOTIFY", cat="ACTION",
        ins=(_e("do", "do"),),
        params={"sink": "ntfy", "channel": "rig-alerts", "level": "warning"}),
    "refocus": NodeDef(
        type="refocus", label="REFOCUS", cat="ACTION",
        ins=(_e("do", "do"),),
        params={"boundary": "Next frame boundary"}),
    "abort": NodeDef(
        type="abort", label="ABORT + PARK", cat="ACTION",
        ins=(_e("do", "do"),),
        params={"park": "Yes", "warm": "Yes", "message": "Safety monitor unsafe"}),
    "report": NodeDef(
        type="report", label="SESSION REPORT", cat="SINK",
        ins=(_f("session", "session"),),
        params={"format": "JSON + FITS index", "dest": "captures/sessions/"}),
}

#: Palette grouping, in the order the rail renders them (README §3).
PALETTE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("SOURCES", ("dusk", "target", "safety", "cloudwatch")),
    ("EQUIPMENT", ("dome", "flatpanel")),
    ("RIG OPS", ("slew", "autofocus", "guide", "capture", "duskflats", "calib")),
    ("LOGIC", ("pool", "cycle", "condition")),
    ("ACTIONS + SINKS", ("holdresume", "notify", "refocus", "abort", "report")),
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


def default_params(node_type: str) -> dict:
    """A fresh copy of a node type's defaults. A copy, because a node dropped on
    the canvas is then edited, and the table must not be edited with it."""
    d = NODE_DEFS.get(node_type)
    return {} if d is None else dict(d.params)
