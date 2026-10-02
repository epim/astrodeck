# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The numbers the Target modal's RUN section prints (#189 spec 2.4, S4 item 1).

"Every number comes from the server compile, never computed in the client"
(spec 2.4, RUN). The modal is drawn by two UIs, and a number each of them
worked out for itself is two numbers that drift: the classic editor and the
rebuild would each own a copy of the visit bound, the pre-flip idle and the
arithmetic of a cycle. So ``_compile_payload`` answers them, computed here, as
two keys beside the plan and the doctor's issues:

* ``readouts``, one entry per TARGET block the plan shoots, keyed by the
  block's node id (``readouts``);
* ``rig``, what the live rig is known to be, as the modal words it
  (``rig_readout``).

For the shipped default cycle (L R G B at 60 s, Ha OIII SII at 180 s, 45
cycles) on a 3x2 the block reads: 6 panels x 7 filters x 45 = 1890 subs, 9.75
h per panel and 58.5 h in all, 270 visits at 1 pass per visit (spec 5.5, A.3).

WHAT THE NUMBERS ARE READ FROM. The SequencePlan the preview compiled, which
is the plan the run would start: its targets (the live panels, their steps,
their centres), its groups (the visit bound's passes and minimum, the mode,
the angle tolerance ``to_plan`` took from ``framing.angle_tolerance_deg``) and
its flip lead; the compile entry, for the block's node id; the ``RigFacts``
the route read (the measured hop); and two focus settings the caller passes,
``autofocus_every`` and ``refocus_on_temp_delta_c``. Nothing is re-derived
from the node's params, so the RUN section can never describe a plan the run
would not shoot.

NOTHING HERE IS SITE DATA (spec 6.9, the #19 class). The pre-flip idle is an
RA span of panel centres in sidereal time, the plan's lead and a hop: "the
idle scales with the hop and the lead, not with the site, so the modal can
show it to every role" (A.4). When the idle happens is site-derived, and this
module never asks: it takes no site, no clock and no config, and
``tests/test_flows_readouts.py`` moves the site under the route and checks
that no readout moves with it.

ASSUMED, NOT MEASURED, AND NAMED. Two inputs to the pre-flip idle are
assumptions the spec states (A.4): the per-frame overhead
(``FRAME_OVERHEAD_S``) and, until a hop has been timed on this rig, the hop
itself (the engine's seed, ``HOP_COST_S``, with ``hop_measured`` false). The
visit efficiency is a claim about a measured cost, so it is absent until
there is one: the seed is a number nobody timed.
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Iterable

from ..sequence.engine import FLIP_FRAME_MARGIN_S, HOP_COST_S
from ..sequence.group_rules import preflip_idle_cut_h, ra_span_solar_h
from ..sequence.schedule import MERIDIAN_FLIP_LEAD_MAX_MIN, MERIDIAN_FLIP_LEAD_MIN
from .rig import RigFacts
# ONE READING OF WHETHER TO_PLAN POINTED AN ENTRY. `to_plan` drops an entry
# whose coordinates do not parse and a name the catalogue does not know, and
# `_coords` is the test it drops them by; asking it here is what keeps the
# walk below in step with the plan it walks (see `readouts`).
from .to_plan import _coords

if TYPE_CHECKING:                       # pragma: no cover - typing only
    from ..sequence.models import SequencePlan, Target, TargetGroup


#: The per-frame overhead the pre-flip idle assumes, in seconds: A.4's stated
#: assumption ("10 s of per-frame overhead"), not a measurement. It is not
#: the engine's seed (`DEFAULT_OVERHEAD_S`, 12 s): the modal's numbers are the
#: spec's worked numbers for the spec's inputs (0 min for the 3x2 of 2.0 x
#: 1.33 deg at Dec 41, 10.6 min for the 2x2 of 0.9 x 0.6 deg), and the engine
#: replaces its seed from the first frame of a run, which no preview has.
FRAME_OVERHEAD_S = 10.0

#: A ceiling division's slack for floats. ``minVisit`` 26 on a 13-minute
#: pass is exactly 2 passes, and 1560 / 780 must not become 3 by a rounding
#: in the last place.
_CEIL_EPS = 1e-9

#: The focus line's three readings (spec 2.4 RUN, 5.6 step 6): the frame
#: loop refocuses when the focuser's temperature moves (``temperature``),
#: every N frames (``frames``), or neither is armed and a hop owes no sweep,
#: so a mosaic focuses once, at its first panel (``once``).
FOCUS_TEMPERATURE = "temperature"
FOCUS_FRAMES = "frames"
FOCUS_ONCE = "once"


def visit_passes(passes: int, visit_min_s: float, pass_s: float) -> int:
    """The passes one visit makes: ``max(passes, ceil(minVisit x 60 /
    pass_s))`` (spec 5.3).

    A visit ends at a round boundary once it has made ``passes`` rounds AND
    been going ``visit_min_s``, so a minimum longer than ``passes`` rounds
    adds rounds until the clock is met: ``minVisit`` 30 on a 13-minute pass
    is 3 passes, not 1. EXPORTED for Tonight, whose hop count is the same
    visits: ``tonight._visit_passes`` calls this for the budget's hops and
    the brief's visit sentence; two copies of this bound would price two
    different nights.

    ``pass_s`` is one pass's SHUTTER seconds, the spec's pass (A.3: 4 x 60 +
    3 x 180 = 780 s). The engine's visit clock also runs through each frame's
    overhead, so a real visit meets its minimum a little sooner; the bound
    here is the most passes the minimum can ask for. A pass of no shutter
    time (no cycle) has no rounds to add, and the answer is ``passes``."""
    passes = max(1, int(passes))
    if not (isinstance(pass_s, (int, float)) and math.isfinite(pass_s)
            and pass_s > 0):
        return passes
    minimum = float(visit_min_s or 0.0)
    if not math.isfinite(minimum) or minimum <= 0:
        return passes
    return max(passes, math.ceil(minimum / pass_s - _CEIL_EPS))


def plan_lead_s(plan: "SequencePlan") -> float:
    """The plan's flip lead in seconds, clamped exactly as the engine's
    ``_plan_flip_lead_s`` clamps it: the margin the group's meridian rule
    keeps (spec 5.7, "the margin is the plan's lead, never the learned
    one"). A plan that names no lead has the schedule's default."""
    lead = getattr(plan, "meridian_flip_lead_min", None)
    try:
        lead = float(MERIDIAN_FLIP_LEAD_MIN if lead is None else lead)
    except (TypeError, ValueError):
        lead = MERIDIAN_FLIP_LEAD_MIN
    if lead != lead:                            # NaN
        lead = MERIDIAN_FLIP_LEAD_MIN
    return min(max(0.0, lead), MERIDIAN_FLIP_LEAD_MAX_MIN) * 60.0


def rig_readout(rig: RigFacts | None) -> dict:
    """The rig facts the modal prints, JSON-ready::

        {fov_deg: [x, y] | null, fov_from, has_rotator: bool | null,
         hop_s: float | null, hop_samples, hop_measured}

    ``fov_deg`` is the live camera field at bin 1 the GRID section compares
    the block's snapshot with (spec 2.4, the 2% banner), ``has_rotator`` what
    locks ROTATE TO with its reason, and the hop the RUN line "hop 2 m 40 s,
    measured over 6 hops". ``hop_s`` is the MEASURED mean or null, never the
    seed: "hop: not measured on this rig yet" is the sentence for null. A
    route with no rig hands ``None``, which knows nothing."""
    rig = rig if rig is not None else RigFacts()
    measured = rig.hop_cost_s is not None
    return {"fov_deg": list(rig.fov_deg) if rig.fov_deg is not None else None,
            "fov_from": rig.fov_from,
            "has_rotator": rig.has_rotator,
            "hop_s": round(rig.hop_cost_s, 1) if measured else None,
            "hop_samples": rig.hop_samples if measured else 0,
            "hop_measured": measured}


def readouts(compiled: dict, plan: "SequencePlan | None",
             rig: RigFacts | None, *, autofocus_every: int = 0,
             refocus_on_temp_delta_c: float = 0.0,
             when: float | None = None) -> dict[str, dict]:
    """``{node_id: block}`` for every TARGET block ``plan`` shoots.

    ``compiled`` is ``compile_plan``'s output and ``plan`` the
    ``to_sequence_plan`` it expanded to, or None when the compile refused
    (a graph half built): then there is nothing to read and the answer is
    empty. ``when`` is the instant ``to_sequence_plan`` resolved names at.

    A block is::

        {node_id, mode: "rotate" | "sequential" | "single",
         panels, steps, rounds, subs_per_panel, subs_total,
         pass_s, panel_s, total_s,
         passes, visit_min_s, visit_passes, visits_per_panel, visits_total,
         hop_s, hop_measured, [efficiency],
         preflip_idle_s, angle_tolerance_deg,
         focus, autofocus_every, refocus_delta_c}

    See ``_block`` for each. A POOL is not a TARGET block and has none; a
    block ``to_plan`` dropped (no coordinates, every panel skipped) shoots
    nothing and has none; an entry with no node id cannot be looked up by
    the modal and has none.

    WHICH PLAN TARGETS ARE A BLOCK'S. ``to_plan`` walks the compiled entries
    in order and appends each one's targets (a mosaic's live panels, a
    single target, a pool member) and each mosaic's group in that order,
    leaving out an entry it cannot point at. This walks the same entries
    the same way, asking the same question (``_coords``) and the same
    all-skipped rule, so the n-th chunk of targets is the n-th pointed
    entry's. It must not match on ids: an unsaved draft (the editor's
    compile) has uuid4 ids, and matching on names alone would hand a
    dropped block the next block of the same name. Each chunk is checked
    against its entry (the name, the group), and the walk stops at the
    first that does not match rather than read a neighbour's panels."""
    if plan is None:
        return {}
    hop_measured = rig is not None and rig.hop_cost_s is not None
    hop_s = float(rig.hop_cost_s) if hop_measured else float(HOP_COST_S)
    lead_s = plan_lead_s(plan)
    out: dict[str, dict] = {}
    targets = list(plan.targets)
    groups = list(plan.groups)
    i = g = 0
    for entry in compiled.get("targets") or []:
        name = str(entry.get("name") or "").strip()
        is_pool = entry.get("pool_rank") is not None
        if _coords(entry, when) is None:
            continue                    # to_plan dropped it: no targets
        if entry.get("mosaic") and not is_pool:
            if _every_panel_skipped(entry):
                continue                # dropped whole, no group (to_plan)
            if g >= len(groups) or groups[g].name != name:
                break
            group = groups[g]
            # The group's members, which `to_plan` appended in one run at
            # this point of the list; one anywhere else means the walk and
            # the plan disagree about where this block is.
            j = i
            while j < len(targets) and targets[j].mosaic_group == group.id:
                j += 1
            members = targets[i:j]
            if not members or sum(t.mosaic_group == group.id
                                  for t in targets) != len(members):
                break
            g += 1
            i = j
            block = _block(members, group, hop_s=hop_s,
                           hop_measured=hop_measured, lead_s=lead_s,
                           meridian_flip=bool(plan.meridian_flip),
                           autofocus_every=autofocus_every,
                           refocus_delta_c=refocus_on_temp_delta_c)
        else:
            if i >= len(targets) or targets[i].mosaic_group is not None \
                    or targets[i].name != name:
                break
            target = targets[i]
            i += 1
            if is_pool:
                continue
            block = _block([target], None, hop_s=hop_s,
                           hop_measured=hop_measured, lead_s=lead_s,
                           meridian_flip=bool(plan.meridian_flip),
                           autofocus_every=autofocus_every,
                           refocus_delta_c=refocus_on_temp_delta_c)
        node_id = str(entry.get("node_id") or "")
        if node_id:
            out[node_id] = {"node_id": node_id, **block}
    return out


def _every_panel_skipped(entry: dict) -> bool:
    """``to_plan._expand_mosaic``'s drop: a block whose ``skip`` names every
    panel of its grid emits no panel and no group. ``skip`` is the compile's
    parsed list, 1-based pairs inside the grid (``compile.parse_skip``)."""
    m = entry.get("mosaic") or {}
    try:
        cells = int(m.get("rows") or 0) * int(m.get("cols") or 0)
    except (TypeError, ValueError):
        return False
    skipped = {(int(r), int(c)) for r, c in (m.get("skip") or [])}
    return cells > 0 and len(skipped) >= cells


def _block(members: list["Target"], group: "TargetGroup | None", *,
           hop_s: float, hop_measured: bool, lead_s: float,
           meridian_flip: bool, autofocus_every: int,
           refocus_delta_c: float) -> dict:
    """One block's readouts, from its plan targets and its group (None for a
    single target).

    * ``panels``: the live panels (skipped ones are not in the plan);
      ``steps``: one panel's steps, the "7 filters"; ``rounds``: the passes
      a panel owes, its most-served step's ``count / per_visit`` rounded up,
      the "45" (null when the block shoots in blocks, which has no pass).
    * ``subs_per_panel``, ``subs_total``: the steps' counts, one panel's and
      every panel's. ``pass_s``: one pass's shutter seconds (null without a
      pass); ``panel_s``, ``total_s``: one panel's and every panel's shutter
      seconds, the "9.75 h per panel, 58.5 h in all".
    * ``passes``, ``visit_min_s``: the block's own visit settings, from its
      group. ``visit_passes``: the bound (``visit_passes``), for a block
      whose panels rotate; ``visits_per_panel``: ``rounds`` over that bound
      rounded up when they rotate, 1 when a panel runs to completion
      (sequential), the engine's own rule (``_visits_owed``, nothing
      banked); ``visits_total``: every panel's, the "270 visits". The four
      are null for a single target, which makes no hops.
    * ``hop_s``, ``hop_measured``: the hop the idle is priced with, measured
      or the engine's seed; both null for a single target, which makes no
      hop. ``efficiency``: shutter time over shutter time
      plus hops, ``total_s / (total_s + visits_total x hop_s)`` (A.3's
      ``4K / (4K + H)``), PRESENT ONLY WITH A MEASURED HOP: absent, not
      null, so no reading of the answer prints a seed as a measurement.
    * ``preflip_idle_s``: spec 5.7's cost 1 with cut visits,
      ``group_rules.preflip_idle_cut_h`` over ``ra_span_solar_h`` of the
      live panel centres, the plan's lead, the hop and the shortest frame
      (its exposure, ``FRAME_OVERHEAD_S`` and the flip gate's
      ``FLIP_FRAME_MARGIN_S``). 0 is "no idle before the flip". Null for a
      single target, which no group rule holds, and for a plan that does
      not flip.
    * ``angle_tolerance_deg``: how far the camera may sit off the layout
      angle, the group's (``framing.angle_tolerance_deg``, A.2 with
      convergence's share taken first); null for a single target.
    * ``focus``: ``FOCUS_TEMPERATURE`` when a temperature delta is armed,
      else ``FOCUS_FRAMES`` when ``autofocus_every`` is set, else
      ``FOCUS_ONCE`` (spec 5.6 step 6: a hop owes no sweep then), with the
      two settings beside it."""
    first = members[0]
    steps = list(first.steps)
    cycle = first.acquisition == "cycle"
    panels = len(members)
    subs_per_panel = sum(int(s.count) for s in steps)
    panel_s = sum(float(s.exposure_s) * int(s.count) for s in steps)
    pass_s = (sum(float(s.exposure_s) * max(1, int(s.per_visit))
                  for s in steps) if cycle else None)
    rounds = (max((-(-int(s.count) // max(1, int(s.per_visit)))
                   for s in steps), default=0) if cycle else None)
    total_s = panel_s * panels
    block: dict[str, Any] = {
        "mode": group.mode if group is not None else "single",
        "panels": panels, "steps": len(steps), "rounds": rounds,
        "subs_per_panel": subs_per_panel,
        "subs_total": subs_per_panel * panels,
        "pass_s": _r(pass_s, 3), "panel_s": _r(panel_s, 3),
        "total_s": _r(total_s, 3),
        "passes": None, "visit_min_s": None, "visit_passes": None,
        "visits_per_panel": None, "visits_total": None,
        # A single target makes no hop, so neither the hop nor whether it
        # was measured is its to report: the rig block says what the rig
        # knows, and a single block reads the same whatever it knows.
        "hop_s": None, "hop_measured": None,
        "preflip_idle_s": None, "angle_tolerance_deg": None,
    }
    if group is not None:
        rotating = group.mode == "rotate" and cycle and rounds
        bound = (visit_passes(group.visit_passes, group.visit_min_s, pass_s)
                 if rotating else None)
        per_panel = -(-rounds // bound) if rotating else 1
        visits = per_panel * panels
        block.update({
            "passes": group.visit_passes,
            "visit_min_s": _r(group.visit_min_s, 3),
            "visit_passes": bound, "visits_per_panel": per_panel,
            "visits_total": visits, "hop_s": _r(hop_s, 1),
            "hop_measured": hop_measured,
            "preflip_idle_s": (_r(_preflip_idle_s(
                (t.ra_hours for t in members), steps, lead_s=lead_s,
                hop_s=hop_s), 1) if meridian_flip else None),
            "angle_tolerance_deg": _r(group.angle_tolerance_deg, 3),
        })
        if hop_measured:
            spent = total_s + visits * hop_s
            block["efficiency"] = _r(total_s / spent if spent > 0 else 0.0, 4)
    delta = float(refocus_delta_c or 0.0)
    every = int(autofocus_every or 0)
    block.update({
        "focus": (FOCUS_TEMPERATURE if delta > 0 else
                  FOCUS_FRAMES if every > 0 else FOCUS_ONCE),
        "autofocus_every": every, "refocus_delta_c": delta})
    return block


def _preflip_idle_s(ras: Iterable[float], steps: list, *, lead_s: float,
                    hop_s: float) -> float:
    """Spec 5.7 cost 1 with cut visits, in seconds: ``max(0, lead + hop + f
    - span)``. ``f`` is the shortest frame a panel owes (on a fresh block,
    its shortest step) plus the assumed overhead and the flip gate's
    margin, which is the budget the gate draws for a frame (5.3). The span
    is the live panel centres' RA span in clock time, because the meridian
    sweeps from the first to the last in sidereal time (A.4)."""
    shortest = min((float(s.exposure_s) for s in steps), default=0.0)
    f_s = shortest + FRAME_OVERHEAD_S + FLIP_FRAME_MARGIN_S
    idle_h = preflip_idle_cut_h(lead_h=lead_s / 3600.0, hop_h=hop_s / 3600.0,
                                f_h=f_s / 3600.0,
                                span_h=ra_span_solar_h(ras))
    return idle_h * 3600.0


def _r(value: float | None, places: int) -> float | None:
    """Rounded for the wire, or None. Rounded so the answer is the same bytes
    on every machine a float's last place might differ on, which is what lets
    ``tests/fixtures/flow_readouts_m31.json`` pin it; every place kept is
    finer than the modal prints."""
    return None if value is None else round(float(value), places)
