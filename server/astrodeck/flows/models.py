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

        if len(known) != len(self.nodes):
            pass        # already reported as duplicates
        return out

    def with_defaults(self) -> "FlowGraph":
        return self.model_copy(
            update={"nodes": [n.with_defaults() for n in self.nodes]})


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
