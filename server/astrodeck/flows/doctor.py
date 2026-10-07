# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The doctor: the graph checks, transcribed from the handoff, and the mosaic
rules of spec 2026-09-23 (flows mosaic) 1.8.

TRANSCRIBED from ``issues()`` in the prototype (README §8), including the WORDING
TONE, which the handoff calls out specifically: every check explains *why*, not
just what. "▸ CAPTURE with no TARGET upstream" would be a lint rule;
"- the loop shoots wherever the mount happens to point" is the sentence that
makes somebody fix it at 21:00 instead of finding out at 03:00.

NO EM-DASHES IN THESE STRINGS. The 2026-08-14 do-not list bans them from every
shipped string, and an issue text is shipped twice over: it goes on screen in the
header chip and into the compile response. The separator is a hyphen. The same
list bans the word "slave" everywhere; a dome is BOUND to the mount.

THIS IS THE ONLY COPY OF THE RULES (#169). No rule text exists anywhere in
``ui/src``: the compile route returns these issues and both editors render what
they are given. This docstring used to say the UI ran its own copy for latency,
and the mosaic design review nearly scheduled work to update a copy that does
not exist. A rule is added, reworded or removed here and nowhere else.

SEVERITY IS NOT COLOUR. Each issue carries a level (`warn` / `danger` / `note`)
and the UI maps it to a token; the README's do-not list forbids colour-alone
status, so the level is also what picks the glyph.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# The lane is ONE reading shared with the compile (spec 1.5): the doctor's
# M3, M4, M12 and M13 must name exactly the stages the compile scopes, so they
# ask the compile's own functions. The three private helpers are imported for
# the same reason: a grid side, a stage's name and a flow parent are read one
# way in this package, and a second reading here would let the doctor and the
# compile's notes disagree about the same wire.
from .compile import (
    LANE_TYPES, NEXT_PORT, OWNER_TYPES, PASS_PORT, PASS_TYPES, _grid_dim,
    _lane_index, _stage_label, angle_code, campaign_block, compile_plan, is_multi_panel,
    lane_branched, lane_tail, loop_wires, one_panel_pass_wires, owner_of,
    panel_lane)
from .identity import typed_coordinates
from .models import FlowGraph
from .nodes import NODE_DEFS, parse_cycle_plan, port_kind, target_angle
from .rig import RigFacts


@dataclass(frozen=True)
class Issue:
    text: str
    level: str          # warn | danger | note

    def to_json(self) -> dict:
        return {"text": self.text, "level": self.level}


#: Rule 2's line: a capture stage with no GUIDE upstream warns when its
#: longest sub is this many seconds or more ("stars will trail at any real
#: focal length"). THE ONE COPY OF THE NUMBER (#432). The wizard's unguided
#: answer is bounded strictly below it at the route's door
#: (``FlowWizardBody.unguided_exposure_s``) and in the generator
#: (``wizard._unguided_seconds``, which the door's filter rows are held to
#: through ``_within_the_unguided_cap``), because every generated graph must
#: pass this doctor at note level or better (spec 1.8, and Revision 2, ruling
#: 4). A second literal in either place is how the door came to accept 3600 s
#: while this rule warned from 120. The wizard reads it as
#: ``doctor.UNGUIDED_SUB_LINE_S`` at call time, as this rule does, so moving
#: the line moves both; the door's field bound is read once, when app.py is
#: imported, the only moment a pydantic bound can be read.
UNGUIDED_SUB_LINE_S = 120


def _flow_upstream_types(graph: FlowGraph, node_id: str) -> set[str]:
    """Every node type reachable BACKWARDS from ``node_id`` along FLOW edges.

    Flow edges only, because the question these checks ask is "did the run
    cursor pass through a guider before it got here?" — an event wire from a
    cloud watcher is not a thing the cursor travelled, and counting it would let
    a graph claim focus it never ran.

    A PRESENCE QUESTION ONLY. It collects every type upstream through any node,
    so it cannot say which block a stage belongs to: TARGET -> CYCLE -> DOME ->
    CAPTURE has a TARGET upstream of the CAPTURE, and the CAPTURE is still
    nobody's. The mosaic rules ask ``compile.owner_of`` instead (spec 1.5).
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
    # OverflowError too: ``float()`` of an integer past a float's range (a
    # raw POST can hold a 400-digit one) raises that, and the compile route
    # runs these rules on every draft (#328).
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return default


#: The two capture stages. Every "what does a loop need upstream of it" rule
#: applies to both — a FILTER CYCLE that shoots 180s subs needs a guider for
#: exactly the reason a CAPTURE LOOP does, and exempting it would make the safer
#: stage the one the doctor stops checking.
#:
#: NOT the stages a pass wire leaves (#375). Which types have 'pass done' is
#: ``compile.PASS_TYPES``, read off the vocabulary; the mosaic rules read
#: that, so a type that gains the port is read by the doctor as by the
#: compile. This list once stood in for both, and when AUTOFOCUS and GUIDE
#: gained the port (S4, #331) M3, M4 and M12 went on reading only these two.
_CAPTURE_TYPES = ("capture", "cycle")

#: What satisfies R4 (spec 1.7): the cursor passed something that points the
#: mount. A TARGET or a POOL does now, because centring is part of the block;
#: a legacy SLEW still does, because every saved flow draws one.
_POINTS_THE_MOUNT = frozenset({"target", "pool", "slew"})


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
    """The DUSK WINDOW of a campaign, or None.

    A campaign is not a longer night, it is a night that comes back: the cursor
    survives dawn and the flow re-arms. Rules 11 and 12 exist because the two
    things a single night never needs - something to advance the pool, and a
    shutdown lane - are exactly the two a campaign cannot run without.

    KEYED ON THE COMPILED CAMPAIGN BLOCK, NOT ON `repeat` (#195, WP-118). It
    asks ``compile.campaign_block``, the one function ``compile_plan`` writes
    the plan's ``campaign`` key from, so this answers "campaign" exactly when
    the plan says so: a POOL in a flow whose DUSK WINDOW has Automatic resume
    On (``SequencePlan.resume_across_nights`` is the other half of the same
    option). It read `repeat` until then, which no editor offered any more, so
    the one flow that was a campaign by that test was a stored file or the
    campaign Example, and the default pool flow was not.
    """
    if campaign_block(graph.with_defaults()) is None:
        return None
    return next((n for n in graph.nodes if n.type == "dusk"), None)


# ------------------------------------------------------------ the mosaic rules
#
# Spec 2026-09-23 1.8 (#189 U-09). Every threshold is named here so a test can
# name the mutant that moves it, and every number a sentence prints is
# computed from the block's own params or from the rig fact the route handed
# over. The rules that read the rig (the M5 warning, M8, M9, M10) run only
# when ``rig`` carries their fact: an unknown is a state, never a reading
# (``rig.py``), so a preview with no rig hears nothing from them rather than
# "no rotator" about a rig nobody asked.

#: M5: the live field may differ from the block's snapshot by this share on
#: either axis before the doctor says re-frame (spec 1.8).
FIELD_DRIFT_SHARE = 0.02
#: M6: meridian convergence may use this share of the overlap between
#: neighbouring panels before the doctor warns (spec 1.8, Appendix A.1). The
#: 4x1 (4 columns by 1 row) at 10% at Dec 75 uses 38.9% and warns; the 3x3
#: at 25% at Dec 41 uses 3.1% and does not. A grid's size is written columns
#: by rows, in words where an operator reads it (S4 orchestrator ruling 1,
#: #339; ``compile.grid_size``): the sentence's remedy, fewer columns, is
#: about the four side by side.
CONVERGENCE_WARN_SHARE = 0.25
#: M10: a measured hop may cost this share of a visit before the doctor notes
#: that more passes per visit would buy the night back (spec 1.8).
HOP_NOTE_SHARE = 0.25

#: The port labels a sentence quotes, read off the cards so a relabel in
#: `nodes.py` cannot leave the doctor quoting a word no card shows.
_PASS = NODE_DEFS["cycle"].port(PASS_PORT, "out").label        # pass done
_NEXT = NODE_DEFS["target"].port(NEXT_PORT, "in").label        # next panel
_DONE = NODE_DEFS["report"].port("done", "out").label          # target done


def _counts_accepted(node) -> bool:
    """Whether a TARGET's or POOL's `counts` asks for accepted subs (Revision
    2, ruling 2). Only "Accepted subs" does, read trimmed and in any case, as
    the compile reads it into `plan.count_mode`; the old meaning, and anything
    a hand-edited file holds, counts every sub taken."""
    return (str(node.params.get("counts") or "").strip().lower()
            == "accepted subs")


def _block_name(node) -> str:
    """How a sentence names a block: TARGET and its name, or the POOL's card
    label (a pool has no name of its own)."""
    if node.type == "pool":
        return NODE_DEFS["pool"].label
    name = str(node.params.get("name") or "").strip()
    return f"TARGET {name}" if name else "TARGET (no name)"


def _lane_phrase(node) -> str:
    """``the M31 panel lane``: a block's lane, as M13 names the one a stage
    fell out of."""
    if node.type == "pool":
        return f"the {NODE_DEFS['pool'].label}'s panel lane"
    name = str(node.params.get("name") or "").strip()
    return f"the {name} panel lane" if name else "that TARGET's panel lane"


def _positive(value) -> float | None:
    """A finite number above zero, else None. A camera field of 0 is "not
    framed" (spec 3.1); a negative, a NaN or text is no field either."""
    v = _num(value, math.nan)
    return v if math.isfinite(v) and v > 0 else None


def _fov(node) -> tuple[float, float] | None:
    """The block's snapshotted bin-1 field, or None when it has none (M1)."""
    fx = _positive(node.params.get("fovX"))
    fy = _positive(node.params.get("fovY"))
    return None if fx is None or fy is None else (fx, fy)


def _pa(node) -> float | None:
    """The block's position angle when it has one to lay panels out at, else
    None: "Any angle", or a rotation that is negative (spec 3.1: negative means
    none), unparseable or not finite. A "Rotate to PA" with rotation -1 rotates
    to nothing, so it has no angle either."""
    if target_angle(node.params) == "Any angle":
        return None
    rot = _num(node.params.get("rotation"), -1.0)
    return rot if math.isfinite(rot) and rot >= 0 else None


def _whole(value, default: int = 1) -> int:
    """A count param (`passes`, `perCycle`) as a whole number of at least 1;
    anything else is ``default``. A NaN or an infinity included: ``int()`` of
    either raises, and the compile route runs the doctor on every edit of a
    half-built graph, where a hand-typed "inf" must read as a bad value and
    not as a 500."""
    v = _num(value, math.nan)
    return int(v) if math.isfinite(v) and v >= 1 else default


def _overlap_pct(node) -> float | None:
    v = _num(node.params.get("overlap"), math.nan)
    return v if math.isfinite(v) else None


def _grid(node) -> tuple[int, int]:
    return (_grid_dim(node.params.get("rows")),
            _grid_dim(node.params.get("cols")))


def _mosaic_spec(node):
    """The block as ``framing.MosaicSpecIn``, or None when it cannot be laid
    out: no field (M1), no angle (M2), no typed coordinates, or a value the
    spec refuses. None means "not measured", never "measured clean", which is
    why M6 and M15 stay silent rather than guess.

    TYPED COORDINATES ONLY, read by ``identity.typed_coordinates`` as
    ``to_plan`` reads them. A block known only by its name is resolved against
    the catalogue at compile time, which is not a pure question; the modal
    writes the coordinates it framed, so a framed block always has them."""
    fov, pa, ov = _fov(node), _pa(node), _overlap_pct(node)
    if fov is None or pa is None or ov is None:
        return None
    if not typed_coordinates(node.params):
        return None
    # Imported here, not at the top: `framing` imports `flows.identity`, so a
    # top-level import would make `astrodeck.flows` and `catalog.framing` each
    # half-load the other, and it brings the framing route's web stack with it.
    # `coords` for the second reason: importing it runs `catalog/__init__`,
    # which loads the whole catalogue and the auth stack, and this module is
    # imported by everything that touches a flow.
    from ..catalog import framing
    from ..catalog.coords import parse_dec, parse_ra
    rows, cols = _grid(node)
    try:
        return framing.MosaicSpecIn(
            ra_hours=parse_ra(str(node.params["ra"])),
            dec_deg=parse_dec(str(node.params["dec"])),
            rows=rows, cols=cols, overlap=ov / 100.0, rotation_deg=pa,
            fov_x_deg=fov[0], fov_y_deg=fov[1])
    except (TypeError, ValueError):
        # pydantic's ValidationError is a ValueError.
        return None


def _pass_seconds(graph: FlowGraph, block) -> float:
    """One pass of this block's panel lane, in shutter seconds (spec 1.3 items
    3-5): a FILTER CYCLE shoots ``perCycle`` of every slot, a CAPTURE LOOP one
    frame, in lane order. Overheads are left out: they are not measured here,
    and M10 compares a MEASURED hop against the visit, so an assumed overhead
    would put a guess inside a verdict."""
    total = 0.0
    for stage in panel_lane(graph, block):
        if stage.type == "cycle":
            per = _whole(stage.params.get("perCycle"))
            total += per * sum(s for _, s in
                               parse_cycle_plan(stage.params.get("plan")))
        elif stage.type == "capture":
            total += max(0.0, _num(stage.params.get("exposure")))
    return total


def _mmss(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 60} m {s % 60:02d} s" if s >= 60 else f"{s} s"


def _minutes(seconds: float) -> str:
    return f"{seconds / 60:.0f} min" if seconds >= 60 else f"{seconds:.0f} s"


def _a(phrase: str) -> str:
    """The article a sentence puts before ``phrase``, a number and its unit
    as ``_minutes`` writes it: "an" where the number is said with a vowel
    (8, 11, 18, 80 to 89, 800), "a" otherwise. M10's visit length is any
    whole number of minutes, and "a 18 min visit" is what a fixed "a" says
    for one of them."""
    lead = phrase.split()[0] if phrase.split() else ""
    return "an" if lead.startswith("8") or lead in ("11", "18") else "a"


def _plain(value) -> str:
    """A param as a sentence prints it: 0.5, not 0.50000; the raw text when it
    is not a number, so a hand-edited value is quoted rather than invented."""
    v = _num(value, math.nan)
    return f"{v:g}" if math.isfinite(v) else str(value)


def _and(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _m13_text(stage, by_id, parents) -> str:
    """Why a stage has no owner, in the terms of what the operator drew: the
    node that ended the lane and the block whose lane it ended."""
    name = _stage_label(stage)
    tail = "It would shoot nothing. Give it a TARGET."
    cur, seen = stage, {stage.id}
    while True:
        ups = parents.get(cur.id, [])
        if len(ups) != 1:
            break
        up = ups[0]
        if up.type in LANE_TYPES and up.id not in seen:
            seen.add(up.id)
            cur = up
            continue
        if up.type in LANE_TYPES or up.type in OWNER_TYPES:
            break           # a flow loop; an owner cannot be here (M13 says none)
        # `up` ends the lane (DOME, DUSK FLATS, DUSK, ...). Whose lane was it?
        ender = NODE_DEFS[up.type].label
        above, walked = up, {up.id}
        while True:
            ps = parents.get(above.id, [])
            if len(ps) != 1 or ps[0].id in walked:
                return (f"▸ {name} belongs to no TARGET: {ender} comes before "
                        f"it and ends its lane, and no TARGET comes before "
                        f"that. {tail}")
            above = ps[0]
            walked.add(above.id)
            if above.type in OWNER_TYPES:
                return (f"▸ {name} belongs to no TARGET: {ender} ends "
                        f"{_lane_phrase(above)}. {tail}")
    return (f"▸ {name} belongs to no TARGET: its wires do not lead back to a "
            f"TARGET or a {NODE_DEFS['pool'].label} through lane stages alone. "
            f"{tail}")


def _mosaic_rules(graph: FlowGraph, rig: RigFacts | None) -> list[Issue]:
    """M1 to M15 (spec 1.8), in the table's order. ``graph`` has its defaults.

    Most of them read a multi-panel block, so they cannot fire on a graph saved
    before mosaics. The ones that could (M11, M13, M14) are held to graphs that
    have a multi-panel block, so every flow saved before S3 draws exactly the
    issues it drew before. M4, M7 and M9 need a `pass` wire or a `counts`
    value, which no flow saved before S3 carries, so they speak wherever one
    appears."""
    out: list[Issue] = []
    blocks = [n for n in graph.nodes if is_multi_panel(n)]
    mosaic = bool(blocks)
    by_id, parents = _lane_index(graph)
    specs = {b.id: _mosaic_spec(b) for b in blocks}
    # Every pass wire into a TARGET's 'next panel', from any type the
    # vocabulary gives the port (``PASS_TYPES``): the wires the compile
    # reads, so M3, M4 and M12 speak about the graph it compiles (#375).
    # Read off ``_CAPTURE_TYPES`` instead, a pass wire from an AUTOFOCUS or
    # GUIDE was invisible here: no M12 for one mid-lane, which the compile
    # refuses, and no M4 for one from another block's lane.
    pass_wires = [e for e in graph.edges
                  if e.fromPort == PASS_PORT and e.toPort == NEXT_PORT
                  and (dst := by_id.get(e.to)) is not None
                  and dst.type == "target"
                  and (src := by_id.get(e.from_)) is not None
                  and src.type in PASS_TYPES]

    # M12's mid-lane wires, found once: M3 stays silent for a block that has
    # one, because M12 already says where the loop wire should start, and two
    # sentences about one wire teach the operator to skim past both.
    mid_lane: dict[str, list] = {}
    for b in blocks:
        if lane_branched(graph, b):
            continue
        tail = lane_tail(graph, b)
        for e in pass_wires:
            src = by_id[e.from_]
            if (e.to == b.id and tail is not None and src.id != tail.id
                    and (o := owner_of(graph, src.id)) is not None
                    and o.id == b.id
                    and all(s.id != src.id for s in mid_lane.get(b.id, []))):
                mid_lane.setdefault(b.id, []).append(src)

    # M1. Panels are tiled from the camera's field; with none recorded the
    # grid has no step, and to_plan refuses it (GraphNotRunnable).
    for b in blocks:
        if _fov(b) is None:
            out.append(Issue(
                f"▸ {_block_name(b)} - frame this block: panels are tiled from "
                f"the camera's field, and this block has not recorded one.",
                "danger"))

    # M2. A mosaic is one grid at one angle. "Any angle" lets each panel land
    # however the camera happens to sit, and the panels stop tiling.
    for b in blocks:
        if _pa(b) is None:
            out.append(Issue(
                f"▸ {_block_name(b)} - a mosaic is laid out at one camera "
                f"angle, and with no angle the panels will not tile. Lock an "
                f"angle, or use the angle the camera measured.", "danger"))

    # M3. No loop wire: the block compiles to panel-first ("sequential"), so
    # a night that ends early has shot the first panels deep and the last not
    # at all. Named by the lane's tail, which is where the wire must start.
    for b in blocks:
        lane = panel_lane(graph, b)
        if (not any(s.type in _CAPTURE_TYPES for s in lane)
                or lane_branched(graph, b) or loop_wires(graph, b)
                or b.id in mid_lane):
            continue
        tail = lane_tail(graph, b)
        if tail is not None and tail.type in PASS_TYPES:
            # Any stage with 'pass done' (``PASS_TYPES``), AUTOFOCUS and
            # GUIDE included since #331: the wire can be drawn from it, so
            # M3 names it (#375).
            how = (f"Wire {_stage_label(tail)} '{_PASS}' to TARGET '{_NEXT}' "
                   f"to rotate panels every pass.")
        else:
            # A lane that ends on a legacy SLEW, the one lane type with no
            # 'pass done' output (spec 1.5 item 6): there is no wire to draw
            # until a stage that has one ends the lane. Said of AUTOFOCUS and
            # GUIDE too until #375, after they had gained the port.
            how = (f"The panel lane ends at {_stage_label(tail)}, which has no "
                   f"'{_PASS}'; end it on a FILTER CYCLE or CAPTURE LOOP and "
                   f"wire that to TARGET '{_NEXT}' to rotate panels every "
                   f"pass.")
        out.append(Issue(
            f"▸ {_block_name(b)} - panels are shot one after another: a night "
            f"cut short leaves the last panels empty. {how}", "warn"))

    # M4. A pass wire the compile will not read as a loop. From a stage in
    # another block's lane (by owner_of, so a stage past a DOME is nobody's
    # and counts as another lane) it does nothing at all: warn, and the
    # compile emits it as a rule that will not run, a loss. Into a 1x1 block
    # from its own lane it has nothing to rotate between: a note, and the
    # compile consumes it with no rule and no loss (S4 orchestrator ruling
    # 3). WHICH wires get the note is the compile's own list
    # (``one_panel_pass_wires``), so the note and the consumption cannot
    # name two sets. And spec 1.4 item 2: more than one loop wire into one
    # block is harmless, and one is enough.
    #
    # WALKED OVER BOTH LISTS, in wire order: ``pass_wires`` and every wire
    # the compile consumes. Both read the vocabulary (``PASS_TYPES``) now,
    # so the compile's list is inside ``pass_wires``; the walk still takes
    # the union because the note must follow the compile's consumption
    # whatever ``pass_wires`` reads. It was the other way round until #375:
    # the compile's list took in AUTOFOCUS and GUIDE when they gained
    # ``pass`` (S4, #331) while ``pass_wires`` read the two capture stages,
    # and walked over ``pass_wires`` alone a one-panel wire from an
    # AUTOFOCUS was consumed with no rule, no loss and no word from the
    # doctor either (#389). A consumed wire is from its block's own lane by
    # construction, so it never reaches the warning.
    consumed = {(e.from_, e.fromPort, e.to, e.toPort)
                for e in one_panel_pass_wires(graph)}
    listed = {id(e) for e in pass_wires}
    one_panel: set[str] = set()
    for e in [e for e in graph.edges
              if id(e) in listed
              or (e.from_, e.fromPort, e.to, e.toPort) in consumed]:
        src, dst = by_id[e.from_], by_id[e.to]
        owner = owner_of(graph, src.id)
        if owner is None or owner.id != dst.id:
            out.append(Issue(
                f"▸ {_block_name(dst)} '{_NEXT}' - this wire does nothing: "
                f"{_stage_label(src)} is not in this TARGET's panel lane.",
                "warn"))
        elif ((e.from_, e.fromPort, e.to, e.toPort) in consumed
              and dst.id not in one_panel):
            one_panel.add(dst.id)
            out.append(Issue(
                f"▸ {_block_name(dst)} - one panel, nothing to rotate between: "
                f"the '{_PASS}' wire into '{_NEXT}' changes nothing.",
                "note"))
    for b in blocks:
        loops = loop_wires(graph, b)
        if len(loops) > 1:
            out.append(Issue(
                f"▸ {_block_name(b)} - {len(loops)} loop wires reach "
                f"'{_NEXT}'; one is enough, and the others change nothing.",
                "note"))

    # M5. The block was framed with one camera and the rig now carries
    # another (or a new focal length). Only the rig knows its live field.
    live = None if rig is None else rig.fov_deg
    if live is not None:
        for b in blocks:
            fov, ov = _fov(b), _overlap_pct(b)
            if fov is None:
                continue        # M1 already says it was never framed
            if not any(abs(lv - fv) > FIELD_DRIFT_SHARE * fv
                       for lv, fv in zip(live, fov)):
                continue
            keep = 1.0 - min(max((ov or 0.0) / 100.0, 0.0), 0.5)
            if any(lv < fv * keep for lv, fv in zip(live, fov)):
                so = "the panels would leave gaps"
            elif all(lv >= fv for lv, fv in zip(live, fov)):
                so = "the panels would overlap more than framed"
            else:
                so = "the panels would overlap less than framed"
            out.append(Issue(
                f"▸ {_block_name(b)} - framed for {fov[0]:.2f} x {fov[1]:.2f} "
                f"deg; this camera now images {live[0]:.2f} x {live[1]:.2f} "
                f"deg, so {so}. Re-frame.", "warn"))

    # M6 and M15 read one number, the share of the overlap meridian
    # convergence uses between neighbours (Appendix A.1), computed by framing
    # and never here. M15 is the same fact past the budget (k = 0.5 - c <= 0,
    # A.2) and REPLACES M6 there: the two would say the same thing with the
    # same remedy, once as a warning and once as a danger.
    #
    # THEY PRICE A CAMERA THAT SHOOTS EVERY PANEL AT ONE ANGLE (#175). A
    # block the compile commands each panel's OWN angle (`to_plan.
    # corrects_convergence`: a rotating block on a rig that is not known to
    # lack a rotator, with a worst turn the rotator can settle to) is not
    # charged for convergence, so both stay silent for it and only a block
    # whose camera cannot correct (fixed, a rig with no rotator, a turn under
    # the rotator's tolerance, or the switch off) is priced here. They ask
    # the compile's own function, so the doctor never calls a block corrected
    # that the compile leaves at one angle.
    from_conv: dict[str, tuple[float, float]] = {}
    if any(specs.values()):
        from ..catalog import framing
        from . import to_plan
        for b in blocks:
            spec = specs[b.id]
            if spec is None:
                continue
            if to_plan.corrects_convergence(
                    framing.compute_mosaic(spec)["panels"],
                    rotate=angle_code(b.params) == "rotate", rig=rig):
                continue
            from_conv[b.id] = (framing.convergence_share(spec),
                               framing.ROTATION_BUDGET)
    for b in blocks:
        if b.id not in from_conv:
            continue
        c, budget = from_conv[b.id]
        if budget - c <= 0 or c <= CONVERGENCE_WARN_SHARE:
            continue
        out.append(Issue(
            f"▸ {_block_name(b)} - at Dec {specs[b.id].dec_deg:.0f} meridian "
            f"convergence turns neighbouring panels against each other, which "
            f"uses {c * 100:.1f}% of their {_plain(_overlap_pct(b))}% overlap. "
            f"Widen the overlap or use fewer columns.", "warn"))

    # M7. `plan.count_mode` is one setting for the whole run
    # (`sequence/models.py`), so blocks that disagree cannot all be honoured.
    # Accepted wins, because counting a rejected sub toward a quota is the
    # defect ruling 2 removed.
    counted = [n for n in graph.nodes if n.type in OWNER_TYPES]
    askers = [n for n in counted if _counts_accepted(n)]
    others = [n for n in counted if not _counts_accepted(n)]
    if askers and others:
        ask = _and([_block_name(n) for n in askers])
        out.append(Issue(
            f"▸ this plan counts accepted subs because {ask} "
            f"{'asks' if len(askers) == 1 else 'ask'} for it; "
            f"{_and([_block_name(n) + chr(39) + 's' for n in others])} 'every "
            f"sub taken' cannot be honoured in the same run.", "warn"))

    # M8. A fixed angle with nothing to turn the camera: the operator turns it
    # by hand, and the run measures it at every panel against this grid's
    # combined budget (A.2, `framing.angle_tolerance_deg`).
    has_rotator = None if rig is None else rig.has_rotator
    if has_rotator is not None:
        for b in blocks:
            pa, angle = _pa(b), target_angle(b.params)
            if pa is None:
                continue        # M2 speaks
            if angle == "Rotate to PA" and has_rotator:
                continue
            spec = specs[b.id]
            if spec is not None:
                from ..catalog import framing
                limit = (f"if it is off by more than "
                         f"{framing.angle_tolerance_deg(spec):.1f} deg")
            else:
                limit = "if it is off by more than this grid can absorb"
            lead = ("no rotator: turn" if angle == "Rotate to PA"
                    else "camera fixed: turn")
            out.append(Issue(
                f"▸ {_block_name(b)} - {lead} the camera by hand to PA "
                f"{pa:.1f} before the first panel. The run measures the angle "
                f"at every panel and holds the mosaic {limit}.",
                "warn" if angle == "Rotate to PA" else "note"))

    # M9. The `quota_unbounded` refusal, before Run rather than at it: in
    # accepted mode with both reject guards off, only a stop boundary ends a
    # step that never gets an accepted sub. The boundary is read from the
    # compile's own schedule, the one to_plan hands the engine, so a Stop of
    # "None" counts as none, as it does at Run. A Stop of "Clock time" with
    # the card left blank (backlog WP-09, #191) is unbounded the same way:
    # a `stop_mode` of "time" with no `stop_time` is a boundary that never
    # arrives, not a real one, the same gap a blank `startClock` used to
    # leave silent on the Start side before #191 closed it there. A graph
    # compile_plan cannot compile raises here as it does in the route, which
    # compiles first (#328).
    if rig is not None and rig.reject_guards_off is True and askers:
        sched = compile_plan(graph).get("schedule", {})
        stop_mode = sched.get("stop_mode", "none")
        unbounded_stop = (stop_mode == "none"
                          or (stop_mode == "time"
                              and not sched.get("stop_time")))
        if unbounded_stop and not sched.get("max_run_min"):
            out.append(Issue(
                "▸ this plan counts accepted subs with no stop time and both "
                "reject guards off, so Run will refuse it: a sub the grader "
                "never accepts would be retried without end. Stop the night "
                "at Dawn (DUSK WINDOW), or set Settings > Standards > 'Give up "
                "on a step after'.", "warn"))

    # M10. A measured hop against the visit it buys (spec 5.3: a visit runs
    # `passes` rounds, and at least `minVisit` minutes of shutter).
    hop = None if rig is None else rig.hop_cost_s
    if hop is not None:
        for b in blocks:
            if lane_branched(graph, b):
                continue        # M12: no one pass to measure
            pass_s = _pass_seconds(graph, b)
            if pass_s <= 0:
                continue
            passes = _whole(b.params.get("passes"))
            minutes = _num(b.params.get("minVisit"), 0.0)
            min_s = (minutes * 60.0
                     if math.isfinite(minutes) and minutes > 0 else 0.0)
            visit = max(passes, math.ceil(min_s / pass_s)) * pass_s
            if hop <= HOP_NOTE_SHARE * visit:
                continue
            more = (f"{2 * passes} passes per visit would cut hops by half"
                    if 2 * passes <= 20
                    else "a longer minimum visit would cut hops")
            out.append(Issue(
                f"▸ {_block_name(b)} - each hop costs {_mmss(hop)} (measured "
                f"over {rig.hop_samples} "
                f"{'hop' if rig.hop_samples == 1 else 'hops'}) against "
                f"{_a(_minutes(visit))} {_minutes(visit)} visit; {more}.",
                "note"))

    # M11. Panel names are the block's name plus a grid cell, and rules find
    # targets by name, so two blocks with one name are one target to both.
    if mosaic:
        names: dict[str, int] = {}
        for n in graph.nodes:
            if n.type == "target":
                key = str(n.params.get("name") or "").strip()
                if key:
                    names[key] = names.get(key, 0) + 1
        for key, count in names.items():
            if count > 1:
                who = ("two blocks are both" if count == 2
                       else f"{count} blocks are all")
                out.append(Issue(
                    f"▸ {who} called {key}; panel names and rules find "
                    f"targets by name.", "danger"))

    # M12. A multi-panel lane must be one chain, and the loop wire must leave
    # its last stage: a stage after the wire's source would be shot once per
    # panel with nothing to say when. Not runnable (to_plan refuses it).
    for b in blocks:
        if lane_branched(graph, b):
            lane = panel_lane(graph, b)
            kids: dict[str, list] = {}
            for s in lane:
                ps = parents.get(s.id, [])
                if len(ps) == 1:
                    kids.setdefault(ps[0].id, []).append(s)
            # The lane is a tree rooted at the block (every member has one
            # parent, the block or a member), so a branched one always has a
            # fork; the default only keeps a StopIteration out of the route.
            fork = next((pid for pid in [b.id] + [s.id for s in lane]
                         if len(kids.get(pid, [])) > 1), b.id)
            after = (_block_name(b) if fork == b.id
                     else _stage_label(by_id[fork]))
            branch = [_stage_label(s) for s in kids.get(fork) or lane]
            out.append(Issue(
                f"▸ {_block_name(b)} - the panel lane branches: "
                f"{_and(branch)} {'both' if len(branch) == 2 else 'all'} "
                f"follow {after}, so no stage is the last one and the panels "
                f"have no loop to run. Make the lane one chain, or give a "
                f"branch its own TARGET.", "danger"))
            continue
        lane = panel_lane(graph, b)
        tail = lane_tail(graph, b)
        for src in mid_lane.get(b.id, []):
            later = lane[[s.id for s in lane].index(src.id) + 1:]
            names_after = _and([_stage_label(s) for s in later])
            one = len(later) == 1
            out.append(Issue(
                f"▸ {_block_name(b)} - the loop wire starts at "
                f"{_stage_label(src)}, but {names_after} "
                f"{'comes' if one else 'come'} after it in the panel lane. "
                f"Start the loop wire at {_stage_label(tail)} to shoot "
                f"{'it' if one else 'them'} on every panel, or give "
                f"{names_after} {'its' if one else 'their'} own TARGET.",
                "danger"))

    # M13 replaces R4 in a graph with a multi-panel block (spec 1.5 item 7):
    # every stage is scoped by its wires there, and a stage whose chain does
    # not reach a block compiles to nothing. Under the old rule it would have
    # been shot on every panel; under this one it must be loud.
    if mosaic:
        for n in graph.nodes:
            if n.type in _CAPTURE_TYPES and owner_of(graph, n.id) is None:
                out.append(Issue(_m13_text(n, by_id, parents), "danger"))

    # M14. REPORT 'target done' is `on_target_complete`, and on a mosaic every
    # panel is a target: whatever it triggers runs once per panel, no matter
    # which lane the REPORT sits in (#184, owner comment 2026-09-25, found
    # verifying S3-D).
    #
    # THIS USED TO SCOPE ITSELF TO THE REPORT THAT ENDS THE MOSAIC'S OWN LANE
    # (its one flow parent was the block, or a stage the block owns), which
    # restated spec 1.8's trigger instead of reading the engine. The compiled
    # rule carries no `only_target` gate at all (`compile._trigger_for`;
    # neither `compile.py` nor `to_plan.py` sets one), so the engine's
    # `_fire_target_complete` runs it at EVERY target's completion, and every
    # panel is a target. A REPORT after a single-panel TARGET drawn beside a
    # mosaic looked safe under the old rule and was not: the wire still fired
    # once per panel of the mosaic sitting in another lane entirely.
    #
    # An `only_target` gate would not close this gap either - it is a name
    # gate, and panels are named "M31 1-1" and so on, so no single name
    # means "the mosaic". So: any REPORT with a 'target done' wire, anywhere
    # in a graph that has a multi-panel block, names every such block and its
    # panel count.
    if mosaic:
        # The grid's size, not a count of panels left to finish: a skipped
        # panel never completes, but no reading of `skip` is shared with
        # to_plan yet, and a second one here could disagree.
        panel_counts = [f"{_block_name(b)} has {rows * cols} panels"
                        for b in blocks for rows, cols in (_grid(b),)]
        for n in graph.nodes:
            if n.type != "report" or not any(
                    e.from_ == n.id and e.fromPort == "done"
                    for e in graph.edges):
                continue
            out.append(Issue(
                f"▸ {NODE_DEFS['report'].label} '{_DONE}' fires once per "
                f"panel, not once for the mosaic: {_and(panel_counts)}, so "
                f"what the wire triggers runs once for each panel finished.",
                "note"))

    # M15. The angle budget is spent (A.2: k = 0.5 - c <= 0): convergence
    # alone uses half the overlap, which leaves nothing for a camera angle
    # error and makes the run's angle tolerance 0. A grid with no overlap
    # answers an infinite share; that is the same verdict in other words.
    for b in blocks:
        if b.id not in from_conv:
            continue
        c, budget = from_conv[b.id]
        if budget - c > 0:
            continue
        dec = f"{specs[b.id].dec_deg:.0f}"
        if math.isfinite(c):
            why = (f"at Dec {dec} meridian convergence alone uses "
                   f"{c * 100:.1f}% of the overlap")
        else:
            why = (f"with no overlap, meridian convergence at Dec {dec} opens "
                   f"gaps between neighbouring panels")
        out.append(Issue(
            f"▸ {_block_name(b)} - {why}, which leaves no room for camera "
            f"angle error. Widen the overlap or use fewer columns.", "danger"))

    return out


def check(graph: FlowGraph, *, standards=None, mount=None,
          rig: RigFacts | None = None) -> list[Issue]:
    """Every rule: the prototype's, in its order, then the mosaic rules (spec
    1.8), then L1.

    ``standards`` is the rig's :class:`~astrodeck.config.StandardsConfig` when
    the caller has one (the compile route passes it). ``mount`` is the
    connected ``Telescope`` when the caller has one (same route, GN-09):
    when its ``needs_guiding`` capability flag is set, the unguided-capture
    rule below fires for every sub length, not just the ones long enough to
    trail on a well-behaved mount. ``rig`` is the route's
    :class:`~astrodeck.flows.rig.RigFacts` (the live field, a measured hop,
    whether the profile has a rotator, whether both reject guards are off);
    each rig rule runs only when its fact is known. ALL KEYWORD AND OPTIONAL
    on purpose: every existing caller and test calls ``check(graph)``
    positionally and must keep seeing exactly the list it saw before, so a
    rule that depends on rig state is simply not run when no rig state was
    offered. This module stays importable without config, which is what lets
    the UI-facing tests and the structural guard in
    `test_flows_doctor_agrees_with_the_engine` reason about the graph alone.
    """
    graph = graph.with_defaults()
    out: list[Issue] = []
    # One multi-panel block switches the whole graph to scoping by wires
    # (spec 1.5), and with it R4 hands over to M13.
    mosaic = any(is_multi_panel(n) for n in graph.nodes)

    # 1. unwired required inputs (`panel` and `advance` are optional by design)
    for n in graph.nodes:
        d = NODE_DEFS[n.type]
        for p in d.ins:
            if p.id in d.optional_ins:
                continue
            if not any(e.to == n.id and e.toPort == p.id for e in graph.edges):
                out.append(Issue(f"▸ {d.label} - '{p.label}' input unwired", "warn"))

    # 2-4. what a capture stage needs upstream of it
    #
    # GN-09: on a mount whose `needs_guiding` flag is set (a harmonic drive
    # whose unguided tracking cannot hold a sub of ordinary length -- the AM5
    # trailed unguided 60 s subs by 15 px on 2026-09-06), the generic "120 s
    # or more" trailing rule below (``UNGUIDED_SUB_LINE_S``) is the WRONG
    # rule: it read clean on a 60 s unguided cycle the night this defect was
    # found. When `mount` names such a mount, every capture stage with no
    # GUIDE upstream gets the mount-specific line INSTEAD, at any sub length
    # -- not doubled with the generic line, because a doctor that says two
    # things about one wire teaches the operator to skim past both.
    needs_guide_mount = mount is not None and getattr(mount, "needs_guiding", False)
    mount_name = getattr(mount, "name", "this mount") if needs_guide_mount else ""
    for n in [x for x in graph.nodes if x.type in _CAPTURE_TYPES]:
        up = _flow_upstream_types(graph, n.id)
        exp = _longest_sub_s(n)
        if needs_guide_mount and "guide" not in up:
            out.append(Issue(
                f"▸ {exp:g}s subs with no GUIDE upstream on a mount that needs "
                f"guiding ({mount_name}: the harmonic drive trailed unguided "
                f"60 s subs by 15 px on 2026-09-06). Add Guide.", "warn"))
        elif exp >= UNGUIDED_SUB_LINE_S and "guide" not in up:
            out.append(Issue(
                f"▸ {exp:g}s subs with no GUIDE upstream - stars will trail at "
                f"any real focal length. Add Guide, or shorten the subs.", "warn"))
        if exp >= 60 and "autofocus" not in up:
            out.append(Issue(
                "▸ no AUTOFOCUS before the loop - focus drift goes uncorrected "
                "all night", "warn"))
        # R4 (spec 1.7), which replaced rule 4's "no SLEW + CENTER upstream":
        # centring is part of the TARGET block now, so a TARGET or a POOL
        # upstream points the mount, and so does a legacy SLEW every saved
        # flow still draws. A presence question, so the set walk answers it.
        # With a multi-panel block M13 asks the sharper question instead.
        if not mosaic and not (up & _POINTS_THE_MOUNT):
            out.append(Issue(
                "▸ CAPTURE with no TARGET upstream - the loop shoots "
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

    # 11-12. REMOVED 2026-08-16, and deliberately not replaced.
    #
    # They told a campaign to draw the two wires that `to_plan.REDUNDANT_PORTS`
    # documents as doing nothing:
    #
    #   "nothing advances the POOL - wire SESSION REPORT 'target done' ->
    #    'advance'"   vs   "the scheduler advances the pool itself: a target
    #    whose frames are all in the ledger is skipped and the next member gets
    #    the night, on this night and on every night after"
    #
    #   "campaign has no shutdown lane - wire 'night ends' -> PARK + CLOSE"
    #   vs   "the night already ends parked with the dust cover shut, whether or
    #    not this wire is here - every flow's plan carries park-when-done and
    #    the wind-down closes the cover"
    #
    # An operator who followed this advice drew a wire the very next panel told
    # them was redundant. The ROOF, which is the one part that does depend on
    # something outside the graph, is rule 9's job and still fires.
    #
    # `_is_campaign` is kept: it is where the doctor asks what a campaign IS
    # (``compile.campaign_block``, a pool in a flow whose Automatic resume is
    # On, WP-118), and the next rule that needs the distinction should not
    # have to rediscover it.
    #
    # `test_flows_doctor_agrees_with_the_engine` asserts both stay gone, and
    # asserts structurally that no REDUNDANT_PORTS entry can be demanded here.

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
            "▸ no session report stage - the night will not have a saved report", "note"))

    # 14. the rig's frame grading, not the graph's. The only rule here that
    # reads state outside the canvas, and it earns that: on 2026-09-06 a flow
    # shot 170 subs through a runaway guider and the grader accepted every one,
    # because `max_eccentricity` was 0 and nothing anywhere said so. The flow
    # cannot express the setting, so the flow's own doctor is where an operator
    # who never opens Settings will see it.
    if (standards is not None
            and not getattr(standards, "max_eccentricity", 0)
            and any(x.type in _CAPTURE_TYPES for x in graph.nodes)):
        out.append(Issue(
            "▸ eccentricity rejection is off: trailed subs will be accepted "
            "and stacked in. Set Settings > Standards > 'Reject rounder than' "
            "to 0.65.", "warn"))

    # M1-M15, after every older rule, so a flow saved before mosaics reads
    # its issues in the order it always did.
    out.extend(_mosaic_rules(graph, rig))

    # L1 (spec 1.7). SLEW + CENTER still loads, so every saved flow keeps
    # working, but its settings were never the run's: `to_plan` has always
    # carried none of them, and the run centred to the hub's own 0.02 deg,
    # which is the TARGET's missing-key `centerTol` by construction. Filled
    # from the node's own tolerance, so the sentence is about this card.
    centred = NODE_DEFS["target"].params["centerTol"]
    for n in graph.nodes:
        if n.type == "slew":
            out.append(Issue(
                f"▸ {NODE_DEFS['slew'].label} - this stage is part of the "
                f"TARGET block now. Its {_plain(n.params.get('tol'))} arcmin "
                f"tolerance never reached the run, which centred to "
                f"{_plain(centred)} arcmin. Delete it and set centring on the "
                f"TARGET.", "note"))

    return out
