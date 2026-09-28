"""The flow graph, and the library record that wraps it.

**THE GRAPH IS THE SOURCE OF TRUTH; THE PLAN IS DERIVED.** That sentence is from
the handoff's backend work list and it decides the shape of everything here: we
persist nodes and edges, never the compiled ``SequencePlan``. A stored plan would
be a second copy of the truth that goes stale the moment somebody drags a wire,
and the first symptom would be a night that runs something other than what the
canvas shows.

Validation is deliberately strict about EDGES and permissive about PARAMS. An
edge that names a missing node, a missing port, or crosses the flow/event
boundary is a graph that cannot be compiled or drawn, so it is refused at the
door. Params are a dict per node type and are checked when they reach the thing
that uses them — a `count` of "twenty" is a bad exposure step, not a bad graph,
and refusing the whole save would lose the other nineteen edits the operator made
in the same session.
"""
from __future__ import annotations

import math
import re
import time
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from .nodes import NODE_DEFS, default_params, port_kind

#: Params that changed NAME, per node type: ``{type: ((old, new), …)}``.
#:
#: A flow is stored on disk as the graph the operator drew, so a param rename is
#: a data migration whether or not anyone calls it one. Every entry here is a
#: promise that a graph saved before the rename still MEANS what it meant.
#:
#: `dome.slave` -> `dome.bind` (2026-08-14): the export's do-not list bans the
#: word from code identifiers and API fields, not just from labels. Without this
#: table a July flow with the azimuth set to "Manual" would come back bound to
#: the mount, because `with_defaults` would merge the new key's default over the
#: top of the old key's value and the default wins.
RENAMED_PARAMS: dict[str, tuple[tuple[str, str], ...]] = {
    "dome": (("slave", "bind"),),
}

#: Folder path: one or more segments of word characters, spaces and dashes.
#: No dots, no slashes at the ends, no traversal — this becomes a display path
#: and (for the Examples fixtures) a lookup key, never a filesystem path, but a
#: name that cannot be mistaken for a path is cheaper than remembering why.
_FOLDER_RE = re.compile(r"^[\w][\w \-]{0,48}(/[\w][\w \-]{0,48}){0,3}$", re.UNICODE)

EXAMPLES_FOLDER = "Examples"
MY_FLOWS_FOLDER = "My flows"

#: The refusal for a flow back-edge, filled with the two nodes' NODE_DEFS labels
#: (#149). The editor's drop resolvers print the same sentence, so the literal
#: is mirrored in the UI and a test pins the two together: an operator must read
#: one rule, not two paraphrases of it.
FLOW_LOOP_REFUSAL = ("this flow loops back on itself at {src} -> {dst}; "
                     "a flow lane runs once")

#: The params ``compile_plan`` takes ``int()`` of, by node type (#328): a
#: FILTER CYCLE's two counts and a POOL's quota. THE ONE EXCEPTION TO
#: "permissive about params" (the module docstring), and a narrow one: a
#: value that is a number but not a finite one above 0 is refused at the
#: door. An infinity or a NaN is a number ``int()`` raises on and JSON cannot
#: carry, so a flow saved with one could never be compiled, previewed or run
#: again, and nothing on screen said why; 0 or a negative is no number of
#: anything. Text that is not a number at all is not judged here: the compile
#: reads it as the default, as it reads every other param it cannot parse.
COUNT_PARAMS: dict[str, tuple[str, ...]] = {
    "cycle": ("cycles", "perCycle"),
    "pool": ("quota",),
}

#: The refusal for one of those counts, filled with the card's label, the
#: node id (two FILTER CYCLEs on one canvas share a label), the param and the
#: value as stored.
COUNT_REFUSAL = ("{label} {node!r}: {key!r} is {value!r}, and a count must be "
                 "a finite number above 0")


def _not_a_count(value) -> bool:
    """True when ``value`` is a number and not a finite one above 0 (#328).

    None, blank text and text that is not a number are False: the compile
    reads each as its default, and the editor stores a blank field that way.
    An integer past a float's range is True: ``float()`` of it overflows, so
    it is no finite count either."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    except OverflowError:
        return True
    return not math.isfinite(number) or number <= 0

#: Flow-level settings: one answer per FLOW, not per block (spec 1.6, Revision
#: 2 ruling 1). ``{key: {"default": missing-key value, "options": [...]}}``.
#:
#: ONE TABLE, READ THROUGH ONE RESOLVER (:func:`resolve_setting`), for the
#: same reason node params have one: a graph saved before a setting existed
#: must read as what it meant then, and a default restated at each call site
#: drifts. Stored as FLAT SCALARS in ``FlowGraph.settings`` (flowsTypes.ts
#: types them so), and never written by a read: a flow saved without settings
#: round-trips without them.
#:
#: `whenWaiting` decides what the run does while every live panel of a mosaic
#: cannot be shot. The owner's default: "No sense in wasting time due to an
#: obstruction", so later targets fill the gap and hand the cursor back when a
#: panel is due. There is no older meaning to preserve: no graph had a
#: multi-panel block before S3, and the setting does nothing without one.
#:
#: A JSON-shaped literal on purpose (double quotes, lists): the UI mirror in
#: `flowsTypes.ts` is pinned against THIS text by flowSettingsParity.test.ts,
#: which parses it rather than trusting a third copy.
FLOW_SETTINGS: dict[str, dict] = {
    "whenWaiting": {
        "default": "Shoot later targets, then come back",
        "options": ["Shoot later targets, then come back",
                    "Wait for the mosaic"],
    },
}


def resolve_setting(settings: dict | None, key: str):
    """The value a flow's setting means: the stored one when it is one of the
    declared options, otherwise the declared default.

    A key nobody declared raises ``KeyError``: asking for it is a programming
    error, not an operator's. A value this build does not know (a newer build's
    third choice, a hand-written typo) reads as the default, because the engine
    has only the declared behaviours to run; the stored value is left in place
    so the build that wrote it reads it back.
    """
    spec = FLOW_SETTINGS[key]
    value = (settings or {}).get(key)
    return value if value in spec["options"] else spec["default"]


class FlowNode(BaseModel):
    id: str
    type: str
    x: float = 0.0
    y: float = 0.0
    params: dict = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def _known_type(cls, v: str) -> str:
        if v not in NODE_DEFS:
            raise ValueError(f"unknown node type {v!r}")
        return v

    def with_defaults(self) -> "FlowNode":
        """This node's params, with any the operator never touched filled in.

        Merged rather than replaced so that a graph saved by an older build —
        before a param existed — loads with the new default instead of a
        KeyError somewhere in the compiler.

        RENAMED PARAMS ARE MIGRATED FIRST, and the order is the whole reason
        this is not a one-liner. A saved graph carrying the OLD key would
        otherwise have the NEW key's default merged in on top of it, and the
        default would win — so a dome an operator set to "Manual" in July would
        silently come back bound to the mount, because the word for it changed.
        Migrating before the merge means the operator's value survives the
        rename; migrating after would be indistinguishable from not migrating.
        """
        raw = dict(self.params or {})
        for old, new in RENAMED_PARAMS.get(self.type, ()):
            if old in raw and new not in raw:
                raw[new] = raw.pop(old)
        merged = default_params(self.type)
        merged.update(raw)
        return self.model_copy(update={"params": merged})


class FlowEdge(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    from_: str = Field(alias="from")
    fromPort: str
    to: str
    toPort: str

    model_config = {"populate_by_name": True}


class FlowGraph(BaseModel):
    nodes: list[FlowNode] = Field(default_factory=list, max_length=400)
    edges: list[FlowEdge] = Field(default_factory=list, max_length=800)
    #: Flow-level settings, flat scalars keyed as in :data:`FLOW_SETTINGS`.
    #: Empty by default and on every graph saved before S3; read them through
    #: :meth:`setting`, never by indexing, so a missing key means the default.
    #: Unknown keys are kept, not dropped, so a round trip through this build
    #: does not erase what a newer one wrote.
    settings: dict = Field(default_factory=dict, max_length=32)

    @field_validator("settings")
    @classmethod
    def _flat_scalars(cls, v: dict) -> dict:
        # FLAT, because the editor draws one control per setting and the TS
        # mirror types a setting as a scalar; a nested value is a setting no
        # control can show. Strict here, like edges and unlike params: it is a
        # shape the graph cannot be drawn with, not a bad value for one knob.
        # None is a scalar too (JSON null), and reads as the default.
        for key, value in v.items():
            if value is not None and not isinstance(value, (str, int, float)):
                raise ValueError(
                    f"flow setting {key!r} must be a flat scalar (text, a "
                    f"number, true/false or null), not {type(value).__name__}")
        return v

    def setting(self, key: str):
        """This flow's value for one setting, missing-key default applied."""
        return resolve_setting(self.settings, key)

    def node(self, node_id: str) -> FlowNode | None:
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None

    def validation_errors(self) -> list[str]:
        """Everything structurally wrong with this graph, all at once.

        ALL at once, not the first: a client that fixes one error, re-posts and
        is told about the next has to make N round trips to learn what it did
        wrong. The doctor (``doctor.py``) answers a different question — this is
        "can this be drawn and compiled at all", that is "is this a good night".
        """
        out: list[str] = []
        ids = [n.id for n in self.nodes]
        dupes = {i for i in ids if ids.count(i) > 1}
        for d in sorted(dupes):
            out.append(f"duplicate node id {d!r}")
        known = set(ids)

        seen_inputs: set[tuple[str, str]] = set()
        for e in self.edges:
            src, dst = self.node(e.from_), self.node(e.to)
            if src is None:
                out.append(f"edge from unknown node {e.from_!r}")
                continue
            if dst is None:
                out.append(f"edge to unknown node {e.to!r}")
                continue
            k_out = port_kind(src.type, e.fromPort, "out")
            k_in = port_kind(dst.type, e.toPort, "in")
            if k_out is None:
                out.append(f"{src.type} has no output port {e.fromPort!r}")
                continue
            if k_in is None:
                out.append(f"{dst.type} has no input port {e.toPort!r}")
                continue
            if k_out != k_in:
                # The exact refusal the UI shows on a bad drop (README §3).
                out.append(
                    f"{k_out.capitalize()} output can't feed an {k_in} input "
                    f"({src.type}.{e.fromPort} → {dst.type}.{e.toPort})")
                continue
            # THE FAN-IN RULE, and it differs by lane.
            #
            # A FLOW input takes exactly one wire, because a flow edge means
            # "then" and there is exactly one run cursor: two predecessors would
            # ask it to arrive twice. The editor enforces this by REPLACING on
            # drop, so two flow edges into one input means the graph was
            # assembled somewhere else and is not a graph the editor can draw.
            #
            # An EVENT input takes as many as you like, because an event edge
            # means "whenever" and several unrelated things can legitimately
            # cause one action. The campaign example needs it twice over: the
            # calibration queue starts when clouds roll in AND when the night's
            # shutdown finishes, and a rule that allowed only the first would
            # make the day-darks lane undrawable.
            if k_in != "event":
                key = (e.to, e.toPort)
                if key in seen_inputs:
                    out.append(f"input {dst.type}.{e.toPort} is wired twice")
                seen_inputs.add(key)

        # A FLOW LANE RUNS ONCE (#149). One wire per input does not stop a
        # back-edge: the editor's replace-on-drop turns FILTER CYCLE `complete`
        # -> TARGET `arm` into one by swapping out the TARGET's dusk wire. The
        # compiler's topological walk then drops every node in the loop, so the
        # save succeeded, the plan held no frames for the looped stages and the
        # doctor -- which reasons along wires, all still present -- was clean.
        for src, dst in self._flow_back_edges():
            out.append(FLOW_LOOP_REFUSAL.format(
                src=NODE_DEFS[src.type].label, dst=NODE_DEFS[dst.type].label))

        # A COUNT THAT IS NO COUNT (#328): see COUNT_PARAMS. Refused here, so
        # save and /run answer 422 with the sentence, and both compile routes
        # list it under `structural` beside the plan they still compile.
        for n in self.nodes:
            for key in COUNT_PARAMS.get(n.type, ()):
                value = (n.params or {}).get(key)
                if _not_a_count(value):
                    out.append(COUNT_REFUSAL.format(
                        label=NODE_DEFS[n.type].label, node=n.id, key=key,
                        value=value))

        if len(known) != len(self.nodes):
            pass        # already reported as duplicates
        return out

    def _flow_back_edges(self) -> list[tuple[FlowNode, FlowNode]]:
        """Every back-edge a depth-first walk of the FLOW wires finds.

        FLOW WIRES ONLY. An event wire means "whenever", so pointing backwards
        closes no circle: SESSION REPORT `done` -> TARGET POOL `advance` spans
        the whole lane and is how a campaign loops (README §"Node vocabulary").

        DETERMINISTIC, by the same canvas tie-break ``compile.flow_order``
        uses: roots (no incoming flow wire) in x, y order, then the leftmost
        node still unvisited, which is how a loop with no way in gets entered;
        each node's successors in x, y order too. Which wire gets named depends
        on where the walk enters the loop, so an order taken from the node list
        would name a different wire for the same drawing. Entering at the
        leftmost stage names the wire that closes the circle back to it.

        Only an edge into a node still on the walk's stack (grey) is a loop. An
        edge into a finished node (black) is a second way to reach it, which
        the fan-in rule above already refuses in its own words.
        Iterative, so a 400-node graph cannot reach the recursion limit.
        """
        by_id: dict[str, FlowNode] = {}
        for n in self.nodes:
            by_id.setdefault(n.id, n)       # duplicates are reported above
        succ: dict[str, list[str]] = {i: [] for i in by_id}
        indeg: dict[str, int] = {i: 0 for i in by_id}
        for e in self.edges:
            src, dst = by_id.get(e.from_), by_id.get(e.to)
            if src is None or dst is None:
                continue
            if (port_kind(src.type, e.fromPort, "out") != "flow"
                    or port_kind(dst.type, e.toPort, "in") != "flow"):
                continue
            succ[src.id].append(dst.id)
            indeg[dst.id] += 1

        def pos(node_id: str) -> tuple[float, float]:
            return (by_id[node_id].x, by_id[node_id].y)

        for targets in succ.values():
            targets.sort(key=pos)           # stable: wire order breaks ties
        order = sorted(by_id, key=pos)

        white, grey, black = 0, 1, 2
        colour = {i: white for i in by_id}
        found: list[tuple[FlowNode, FlowNode]] = []
        reported: set[tuple[str, str]] = set()

        def walk(start: str) -> None:
            colour[start] = grey
            stack = [(start, iter(succ[start]))]
            while stack:
                node_id, rest = stack[-1]
                nxt = next(rest, None)
                if nxt is None:
                    colour[node_id] = black
                    stack.pop()
                elif colour[nxt] == grey:
                    if (node_id, nxt) not in reported:
                        reported.add((node_id, nxt))
                        found.append((by_id[node_id], by_id[nxt]))
                elif colour[nxt] == white:
                    colour[nxt] = grey
                    stack.append((nxt, iter(succ[nxt])))

        for node_id in [i for i in order if indeg[i] == 0] + order:
            if colour[node_id] == white:
                walk(node_id)
        return found

    def with_defaults(self) -> "FlowGraph":
        return self.model_copy(
            update={"nodes": [n.with_defaults() for n in self.nodes]})


class MigrationNote(BaseModel):
    """One thing a read changed in a stored flow, said to the operator on
    every read of the file until they save it (only ``save()`` stamps
    FLOW_SCHEMA, so an unsaved file reads its old version every time).

    ``key`` names what moved (``rotation`` for FLOW_SCHEMA 3's 23.4 rewrite;
    the mosaic slice adds ``counts``), ``note`` is the sentence to show."""
    key: str
    note: str


class FlowRecord(BaseModel):
    """A flow as the library lists it: the graph plus what the cards show."""
    id: str = Field(default_factory=lambda: uuid4().hex)
    name: str = Field("Untitled flow", min_length=1, max_length=120)
    folder: str = MY_FLOWS_FOLDER
    tagline: str = Field("", max_length=400)
    graph: FlowGraph = Field(default_factory=FlowGraph)
    created_ts: float = Field(default_factory=time.time)
    updated_ts: float = Field(default_factory=time.time)
    #: Observations of the last run, for the card's meta + status rows. Never
    #: consulted to decide anything — same separation as sync's PushState.
    last_run: float | None = None
    last_result: str = ""            # "" | ok | warn | bad
    #: Read-only fixtures (the five Examples) refuse PUT/DELETE. Carried on the
    #: record rather than inferred from the folder name so that renaming a
    #: folder can never accidentally make a fixture writable.
    readonly: bool = False
    #: What ``store._migrate`` rewrote on THIS read, for the editor to say.
    #: NOT PERSISTED, and deliberately not ``Field(exclude=True)``: FastAPI
    #: serialises a response with the same dump, so an excluded field would
    #: never reach ``GET /api/flows/{id}`` and the note would be computed for
    #: nobody. The store's writers strip it instead, and ``save`` drops one a
    #: client sends, so the file never holds a note and a client cannot plant
    #: one. Only a ``save`` makes the file current and retires the note. A
    #: run's ``touch_run`` and the folder verbs edit the raw file and keep its
    #: ``schema_version`` (carry-over 1), so until the operator saves, every
    #: read says the note again, and ``run_flow`` logs it on every run.
    migrated: list[MigrationNote] = Field(default_factory=list)

    @field_validator("folder")
    @classmethod
    def _folder_shape(cls, v: str) -> str:
        v = (v or "").strip().strip("/")
        if not v:
            return MY_FLOWS_FOLDER
        if not _FOLDER_RE.match(v):
            raise ValueError(
                "a folder is 1-4 segments of letters, numbers, spaces or dashes")
        return v

    def card(self) -> dict:
        """The library-card projection: everything a card draws, nothing else.

        A separate shape on purpose — the library lists every flow, and shipping
        each one's full graph to draw a name and a stage count would send
        megabytes to render a grid."""
        return {
            "id": self.id, "name": self.name, "folder": self.folder,
            "tagline": self.tagline, "readonly": self.readonly,
            "stages": len(self.graph.nodes), "wires": len(self.graph.edges),
            "last_run": self.last_run, "last_result": self.last_result,
            "updated_ts": self.updated_ts,
        }
