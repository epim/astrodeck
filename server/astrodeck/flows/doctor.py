"""The doctor — the ten graph checks, transcribed from the handoff.

TRANSCRIBED from ``issues()`` in the prototype (README §8), including the WORDING
TONE, which the handoff calls out specifically: every check explains *why*, not
just what. "▸ CAPTURE with no SLEW + CENTER upstream" would be a lint rule;
"— the loop shoots wherever the mount happens to point" is the sentence that
makes somebody fix it at 21:00 instead of finding out at 03:00.

The UI runs its own copy for latency; THIS is authoritative (README backend work
list item 2). Two implementations of one rule set is the drift risk this project
keeps re-finding, so the wording lives here and the UI check is explicitly a
cache of it — the compile endpoint returns these strings and the editor shows
what it is given.

SEVERITY IS NOT COLOUR. Each issue carries a level (`warn` / `danger` / `note`)
and the UI maps it to a token; the README's do-not list forbids colour-alone
status, so the level is also what picks the glyph.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import FlowGraph
from .nodes import NODE_DEFS, port_kind


@dataclass(frozen=True)
class Issue:
    text: str
    level: str          # warn | danger | note

    def to_json(self) -> dict:
        return {"text": self.text, "level": self.level}


def _flow_upstream_types(graph: FlowGraph, node_id: str) -> set[str]:
    """Every node type reachable BACKWARDS from ``node_id`` along FLOW edges.

    Flow edges only, because the question these checks ask is "did the run
    cursor pass through a guider before it got here?" — an event wire from a
    cloud watcher is not a thing the cursor travelled, and counting it would let
    a graph claim focus it never ran.
    """
    seen: set[str] = set()
    stack = [node_id]
    while stack:
        cur = stack.pop()
        for e in graph.edges:
            if e.to != cur:
                continue
            src = graph.node(e.from_)
            if src is None:
                continue
            if port_kind(src.type, e.fromPort, "out") != "flow":
                continue
            if e.from_ not in seen:
                seen.add(e.from_)
                stack.append(e.from_)
    return {n.type for nid in seen if (n := graph.node(nid)) is not None}


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def check(graph: FlowGraph) -> list[Issue]:
    """All ten rules, in the prototype's order."""
    graph = graph.with_defaults()
    out: list[Issue] = []

    # 1. unwired required inputs (`panel` is optional by design)
    for n in graph.nodes:
        d = NODE_DEFS[n.type]
        for p in d.ins:
            if p.id in d.optional_ins:
                continue
            if not any(e.to == n.id and e.toPort == p.id for e in graph.edges):
                out.append(Issue(f"▸ {d.label} — '{p.label}' input unwired", "warn"))

    # 2-4. what a capture loop needs upstream of it
    for n in [x for x in graph.nodes if x.type == "capture"]:
        up = _flow_upstream_types(graph, n.id)
        exp = _num(n.params.get("exposure"))
        if exp >= 120 and "guide" not in up:
            out.append(Issue(
                f"▸ {exp:g}s subs with no GUIDE upstream — stars will trail at "
                f"any real focal length. Add Guide, or shorten the subs.", "warn"))
        if exp >= 60 and "autofocus" not in up:
            out.append(Issue(
                "▸ no AUTOFOCUS before the loop — focus drift goes uncorrected "
                "all night", "warn"))
        if "slew" not in up:
            out.append(Issue(
                "▸ CAPTURE with no SLEW + CENTER upstream — the loop shoots "
                "wherever the mount happens to point", "warn"))

    # 5-6. conditions
    for n in [x for x in graph.nodes if x.type == "condition"]:
        if not any(e.from_ == n.id for e in graph.edges):
            out.append(Issue("▸ CONDITION fires into nothing — wire an action", "warn"))
        cap = next((x for x in graph.nodes
                    if x.type == "capture"
                    and any(e.from_ == x.id and e.to == n.id for e in graph.edges)),
                   None)
        if (cap is not None and n.params.get("when") == "HFR above"
                and _num(n.params.get("threshold")) > _num(cap.params.get("reject"))):
            out.append(Issue(
                f"▸ watchdog threshold {n.params.get('threshold')}″ sits above "
                f"the grader's reject {cap.params.get('reject')}″ — frames get "
                f"rejected before the rule can ever fire", "warn"))

    # 7-8. the calibration queue
    for n in [x for x in graph.nodes if x.type == "calib"]:
        if (n.params.get("flats") != "Skip"
                and not any(e.to == n.id and e.toPort == "panel" for e in graph.edges)):
            out.append(Issue(
                "▸ QUEUE wants flats but nothing is wired to 'panel' — flats "
                "will be skipped", "warn"))
        triggered = any(e.to == n.id and e.toPort == "do" for e in graph.edges)
        held = any(x.type == "holdresume"
                   and any(e.to == x.id for e in graph.edges) for x in graph.nodes)
        if triggered and not held:
            out.append(Issue(
                "▸ queue runs but nothing HOLDS the light loop — wire "
                "HOLD / RESUME from the same trigger", "warn"))

    # 9. a dome with nothing to close it
    if (any(x.type == "dome" for x in graph.nodes)
            and not any(x.type == "safety" for x in graph.nodes)):
        out.append(Issue(
            "▸ DOME with no SAFETY MONITOR — nothing closes the shutter on "
            "rain. Add one; it fails closed.", "danger"))

    # 10. no ledger
    if not any(x.type == "report" for x in graph.nodes):
        out.append(Issue(
            "▸ no session report sink — the night leaves no ledger", "note"))

    return out
