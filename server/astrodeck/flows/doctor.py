"""The doctor: the thirteen graph checks, transcribed from the handoff.

TRANSCRIBED from ``issues()`` in the prototype (README §8), including the WORDING
TONE, which the handoff calls out specifically: every check explains *why*, not
just what. "▸ CAPTURE with no SLEW + CENTER upstream" would be a lint rule;
"- the loop shoots wherever the mount happens to point" is the sentence that
makes somebody fix it at 21:00 instead of finding out at 03:00.

NO EM-DASHES IN THESE STRINGS. The 2026-08-14 do-not list bans them from every
shipped string, and an issue text is shipped twice over: it goes on screen in the
header chip and into the compile response. The separator is a hyphen.

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
from .nodes import NODE_DEFS, parse_cycle_plan, port_kind


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


#: The two capture stages. Every "what does a loop need upstream of it" rule
#: applies to both — a FILTER CYCLE that shoots 180s subs needs a guider for
#: exactly the reason a CAPTURE LOOP does, and exempting it would make the safer
#: stage the one the doctor stops checking.
_CAPTURE_TYPES = ("capture", "cycle")


def _longest_sub_s(node) -> float:
    """The longest exposure a capture stage will actually take.

    For a CAPTURE LOOP that is its one exposure. For a FILTER CYCLE it is the
    LONGEST slot, not the mean and not the first: the guiding and focus rules ask
    "will any frame tonight trail", and one 180s Ha sub in a table of 60s
    broadband is enough to answer yes.
    """
    if node.type == "cycle":
        slots = parse_cycle_plan(node.params.get("plan"))
        return float(max((s[1] for s in slots), default=0))
    return _num(node.params.get("exposure"))


def _is_campaign(graph: FlowGraph):
    """The DUSK WINDOW whose `repeat` makes this a campaign, or None.

    A campaign is not a longer night, it is a night that comes back: the cursor
    survives dawn and the flow re-arms. Rules 11 and 12 exist because the two
    things a single night never needs — something to advance the pool, and a
    shutdown lane — are exactly the two a campaign cannot run without.
    """
    for n in graph.nodes:
        if n.type == "dusk":
            repeat = str(n.params.get("repeat") or "Single night")
            if repeat != "Single night":
                return n
    return None


def check(graph: FlowGraph) -> list[Issue]:
    """All thirteen rules, in the prototype's order."""
    graph = graph.with_defaults()
    out: list[Issue] = []

    # 1. unwired required inputs (`panel` and `advance` are optional by design)
    for n in graph.nodes:
        d = NODE_DEFS[n.type]
        for p in d.ins:
            if p.id in d.optional_ins:
                continue
            if not any(e.to == n.id and e.toPort == p.id for e in graph.edges):
                out.append(Issue(f"▸ {d.label} - '{p.label}' input unwired", "warn"))

    # 2-4. what a capture stage needs upstream of it
    for n in [x for x in graph.nodes if x.type in _CAPTURE_TYPES]:
        up = _flow_upstream_types(graph, n.id)
        exp = _longest_sub_s(n)
        if exp >= 120 and "guide" not in up:
            out.append(Issue(
                f"▸ {exp:g}s subs with no GUIDE upstream - stars will trail at "
                f"any real focal length. Add Guide, or shorten the subs.", "warn"))
        if exp >= 60 and "autofocus" not in up:
            out.append(Issue(
                "▸ no AUTOFOCUS before the loop - focus drift goes uncorrected "
                "all night", "warn"))
        if "slew" not in up:
            out.append(Issue(
                "▸ CAPTURE with no SLEW + CENTER upstream - the loop shoots "
                "wherever the mount happens to point", "warn"))

    # 5-6. conditions
    for n in [x for x in graph.nodes if x.type == "condition"]:
        if not any(e.from_ == n.id for e in graph.edges):
            out.append(Issue("▸ CONDITION fires into nothing - wire an action", "warn"))
        cap = next((x for x in graph.nodes
                    if x.type in _CAPTURE_TYPES
                    and any(e.from_ == x.id and e.to == n.id for e in graph.edges)),
                   None)
        if (cap is not None and n.params.get("when") == "HFR above"
                and _num(n.params.get("threshold")) > _num(cap.params.get("reject"))):
            out.append(Issue(
                f"▸ watchdog threshold {n.params.get('threshold')}″ sits above "
                f"the grader's reject {cap.params.get('reject')}″ - frames get "
                f"rejected before the rule can ever fire", "warn"))

    # 7-8. the calibration queue
    for n in [x for x in graph.nodes if x.type == "calib"]:
        if (n.params.get("flats") != "Skip"
                and not any(e.to == n.id and e.toPort == "panel" for e in graph.edges)):
            out.append(Issue(
                "▸ QUEUE wants flats but nothing is wired to 'panel' - flats "
                "will be skipped", "warn"))
        triggered = any(e.to == n.id and e.toPort == "do" for e in graph.edges)
        held = any(x.type == "holdresume"
                   and any(e.to == x.id for e in graph.edges) for x in graph.nodes)
        if triggered and not held:
            out.append(Issue(
                "▸ queue runs but nothing HOLDS the light loop - wire "
                "HOLD / RESUME from the same trigger", "warn"))

    # 11-12. a campaign's two missing halves. Both are silent on a single night,
    # which is why they need saying: the graph looks finished either way, and the
    # failure only shows up on night two.
    dusk = _is_campaign(graph)
    if dusk is not None:
        pool = next((x for x in graph.nodes if x.type == "pool"), None)
        if pool is not None and not any(e.to == pool.id and e.toPort == "advance"
                                        for e in graph.edges):
            out.append(Issue(
                "▸ campaign repeats nightly but nothing advances the POOL - "
                "wire SESSION REPORT 'target done' → 'advance'", "warn"))
        if not any(e.from_ == dusk.id and e.fromPort == "nightend"
                   for e in graph.edges):
            out.append(Issue(
                "▸ campaign has no shutdown lane - wire 'night ends' → "
                "PARK + CLOSE", "warn"))

    # 13. the two weather tiers racing. Safety is non-recoverable and always
    # wins; a hold that would have ridden the cloud out never gets the chance.
    if (any(x.type == "safety"
            and str(x.params.get("watch") or "").startswith("Clouds")
            for x in graph.nodes)
            and any(x.type == "cloudwatch" for x in graph.nodes)):
        out.append(Issue(
            "▸ SAFETY watches clouds while CLOUD WATCH is wired - they race, "
            "and safety aborts before the hold can ride it out. Set SAFETY → "
            "Watch for → rain + wind + power.", "warn"))

    # 9. a dome with nothing to close it
    if (any(x.type == "dome" for x in graph.nodes)
            and not any(x.type == "safety" for x in graph.nodes)):
        out.append(Issue(
            "▸ DOME with no SAFETY MONITOR - nothing closes the shutter on "
            "rain. Add one; it fails closed.", "danger"))

    # 10. no ledger
    if not any(x.type == "report" for x in graph.nodes):
        out.append(Issue(
            "▸ no session report sink - the night leaves no ledger", "note"))

    return out
