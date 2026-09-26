"""What a flow has banked and what it still owes (#189 S1 item 9, spec 8).

The pure half of ``GET /api/flows/{id}/progress``: per block, per panel and
per step, how many subs the flow's session has banked and how many are still
owed, plus how many of the session's frames sit on steps this flow no longer
has. The session is the one Run would continue or has just finished
(``SessionStore.current_for_flow``, #189 hardening A2); the route picks it,
and this module only counts it.
The card's state chip ("212/315 subs", spec 1.2), the modal's per-panel bars
and the CONTINUE button's copy are all read off this one answer, so every rule
here is a number an operator acts on:

* BLOCKS ARE THE COMPILE'S NODES. One entry per TARGET, one per POOL member,
  grouped by the compile's ``node_id``: the canvas card is what the operator
  looks at, and the card is a node.
* A PLAN TARGET IS FOUND BY ITS IDENTITY, never by its position in the list.
  ``to_plan`` drops an entry it cannot point at (a TARGET with no coordinates,
  a POOL member the catalogue does not know), so the plan's list is SHORTER
  than the compile's whenever that happens, and zipping the two hands every
  later block its neighbour's frames. Recomputing the id with the functions in
  ``identity`` that minted it cannot slide.
* A FRAME COUNTS THE WAY THE SESSION COUNTS. The ledger was counted by the
  session's FROZEN ``count_mode``; a new compile that asks for a different
  mode is a recount CONTINUE has to announce first (spec 5.9,
  ``accept_recount``), not one the card may show on its own. Banked is capped
  at the step's count, so what the blocks owe is ``Session.owed()`` whenever
  the session's plan is this plan.
* A FRAME ON A STEP NO PLAN STEP HAS IS ORPHANED. The ledger counts by step id
  alone (``Session.accepted_by_step``), and a step id changes with the recipe
  (#77), so a 120 s sub is never banked on the 180 s step that replaced it,
  however alike their filters. EVERY such frame is counted, accepted or not:
  the count mode decides what fills a quota, and an orphaned frame fills
  none. It is a file on disk the flow no longer has a step for, which is how
  CONTINUE's dropped-steps refusal counts it too
  (``continuation.plan_replace_report``). A SKIPPED PANEL'S FRAMES ARE NOT
  ORPHANED (S3, spec 5.9, 2.5): skipping is not a change of identity, so
  re-enabling the panel brings its step ids and its frames back. They are
  shown on the block's ``skipped`` entry for that panel instead, found by
  the frame's target id as ``plan_replace_report`` finds them.
* A MOSAIC'S PANELS ARE FOUND THROUGH ITS PLAN GROUP (S3, spec 3.3). The
  group's id is recomputed from the block's anchor with the functions
  ``to_plan`` minted it with (``identity.group_id`` over ``_block_key``), and
  the panels are the plan targets whose ``mosaic_group`` is that id, never
  the targets whose names look like the block's: two blocks may share a
  name, and a panel's name is only its label.
* A LOCKED ANGLE IS SHOWN WHERE IT CAME FROM (Revision 2, owner ruling 9).
  An unframed TARGET takes the position angle its first plate solve
  measured, and from then on every run commands it, so the flow editor must
  show it rather than leave it a hidden fact. A panel whose target the
  session holds a lock for carries ``locked_angle``: the angle, and the
  words ``LOCK_SOURCE`` says it came from. A TARGET block carries it too
  when every one of its panels is locked to that one angle. The key is
  absent when there is no lock, so every answer without one is the answer
  S1 gave.
* NOTHING DERIVED FROM THE SITE (spec 6.9). The route is ``CAP_VIEW_STATUS``
  and a viewer can read it. This function takes no site, no clock and no
  config, so it cannot compute an altitude or a transit time; the keys it
  emits are pinned to an allow-list by ``tests/test_flows_progress.py`` (a
  mosaic's by ``tests/test_flows_progress_mosaic.py``, which also moves the
  site and checks that no number in the answer moves with it, the #19 way).
  The one outside answer it reads is the catalogue's canonical IDENTITY of a
  TARGET known only by its name (``tonight.resolve_target``, #229), which is
  a catalogue constant ("M31", "Jupiter"). The lookup computes a position as
  well, and for a body that position comes from the clock and, for the Moon,
  the site; it is thrown away here, never used and never emitted. A lock's
  times are never emitted either: when the first solve ran is when the run
  first reached the target, a moment its altitude at the site decides.

Pure otherwise: no devices, no store, and no clock or config of its own.
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

from . import identity, tonight
# ONE READING OF A BLOCK'S KEY. `to_plan` mints every id from these: the
# coordinates (`_coords`), the layout angle (`_angles`), the grid
# (`_mosaic_numbers`) and the key over the anchor (`_block_key`). Asked the
# same question through a second copy, a rounding or a percent read as a
# fraction would key the card on ids no ledger holds, and every panel would
# read "nothing banked" against a live campaign.
from .to_plan import _angles, _block_key, _coords, _mosaic_numbers

if TYPE_CHECKING:                       # pragma: no cover - typing only
    from ..sequence.models import SequencePlan, Target, TargetGroup
    from ..sequence.session import Session


#: Where a locked angle came from, in the words the flow editor shows (owner
#: ruling 9: "whatever the first angle of the first shot is, is locked").
#: The route says it itself rather than forwarding the ``source`` text the
#: engine stored with the lock: that text is free, a viewer reads this
#: answer, and nothing here can vet free text for a time or a place.
LOCK_SOURCE = "measured by the first shot's plate solve"


def flow_progress(compiled: dict, plan: "SequencePlan",
                  session: "Session | None", *, flow_id: str) -> dict:
    """Banked and owed subs of one flow, JSON-ready.

    ``compiled`` is ``compile_plan``'s output and ``plan`` the
    ``to_sequence_plan(compiled, flow_id=flow_id)`` it expands to. ``session``
    is ``SessionStore.current_for_flow``'s answer: the flow's newest session,
    or None when the flow has never run or that newest session was
    abandoned::

        {flow_id,
         session: null | {id, status, nights, count_mode},
         blocks: [{node_id, name, kind: "target" | "pool", banked, owed, total,
                   [locked_angle: {pa_deg, source}],
                   panels: [{target_id, name, row, col, banked, owed, total,
                             [locked_angle: {pa_deg, source}],
                             steps: [{step_id, filter, frame_type, exposure_s,
                                      count, banked, owed}]}]}],
         orphaned: {frames, steps}}

    A TARGET block has one panel, at row 0 and col 0. A POOL block has one
    panel per member, in rank order, with row and col null: a pool is a list
    of candidates, not a grid. A panel whose entry ``to_plan`` dropped has
    ``target_id`` null and no steps, so it owes nothing, which is what the plan
    will shoot for it. ``nights`` is how many nights the session has run.

    A MOSAIC (a TARGET block with a grid, S3) has one panel per panel the
    plan shoots, in the plan's order, each at its own 0-based ``row`` and
    ``col``, and the block adds two keys no other block carries::

        grid: {rows, cols},
        skipped: [{target_id, name, row, col, banked}]

    ``skipped`` lists, in grid order, the panels the operator skipped
    (``TargetGroup.skipped_ids``). They owe nothing while skipped and are in
    none of the block's sums, and ``banked`` is what the ledger holds on the
    panel, counted by the session's count mode, so a skip never hides what
    re-enabling the panel brings back (``_skipped``). A block ``to_plan``
    dropped whole (no coordinates, or every panel skipped) has no panels.

    ``locked_angle`` is present only where there is a lock (``_locked``,
    ``_block_lock``): ``pa_deg`` in the CROTA2 convention the session stored,
    and ``source``, ``LOCK_SOURCE``. Never the lock's times.

    ``orphaned`` counts every one of the session's frames whose step id is in
    no step of ``plan``, whatever the count mode, and how many distinct step
    ids they sit on.

    Raises ``ValueError`` for an empty ``flow_id``: with no flow id nothing can
    be matched by identity, and every block would read "nothing banked" while
    the ledger holds the campaign. Raises it too for a plan that is not this
    compile's (see ``_refuse_a_foreign_plan``), for the same reason.
    """
    if not flow_id:
        raise ValueError(
            "flow progress needs the flow id: plan targets are found by the "
            "ids the flow compiled to, and with no flow id none can be found")
    counts = _counts(session)
    by_id = {t.id: t for t in plan.targets}
    members_used: dict[str, set[str]] = {}
    blocks: list[dict] = []
    block_of: dict[str, dict] = {}
    for index, entry in enumerate(compiled.get("targets") or []):
        node_id = str(entry.get("node_id") or "")
        name = str(entry.get("name") or "").strip()
        is_pool = entry.get("pool_rank") is not None
        if entry.get("mosaic") and not is_pool:
            # One compiled entry is the whole block, so it is one block here
            # whatever its node id; `to_plan` gives an id-less entry uuid4s,
            # which `_mosaic` cannot find and `_refuse_a_foreign_plan` skips.
            block = _mosaic(plan, entry, counts, session, flow_id=flow_id,
                            node_id=node_id, name=name)
            block_of[node_id or f"#{index}"] = block
            blocks.append(block)
            continue
        if is_pool:
            target = _member(plan_by_id=by_id, flow_id=flow_id,
                             node_id=node_id, name=name,
                             used=members_used.setdefault(node_id, set()))
        else:
            target = _single(plan, entry, flow_id=flow_id, node_id=node_id)
        # An entry with no node id (a compiled dict built by hand, older than
        # S1) is its own block: grouping every such entry under "" would give
        # one block two panels at row 0, col 0.
        key = node_id or f"#{index}"
        block = block_of.get(key)
        if block is None:
            block = block_of[key] = {
                "node_id": node_id, "name": name,
                "kind": "pool" if is_pool else "target",
                "banked": 0, "owed": 0, "total": 0, "panels": []}
            blocks.append(block)
        panel = _panel(target, name, counts,
                       at=(None, None) if is_pool else (0, 0))
        lock = _locked(session, target)
        if lock is not None:
            panel["locked_angle"] = lock
        block["panels"].append(panel)
        for field in ("banked", "owed", "total"):
            block[field] += panel[field]
    for block in blocks:
        if block["kind"] == "pool":
            # A POOL node has no name of its own: its members ARE what the
            # operator typed on it, so the block reads as its members box.
            block["name"] = ", ".join(p["name"] for p in block["panels"])
        else:
            lock = _block_lock(block["panels"])
            if lock is not None:
                block["locked_angle"] = lock
    _refuse_a_foreign_plan(compiled, plan, blocks, flow_id)
    skipped = {s["target_id"] for b in blocks for s in b.get("skipped", ())}
    return {"flow_id": flow_id,
            "session": _summary(session),
            "blocks": blocks,
            "orphaned": _orphaned(plan, session, skipped)}


def _refuse_a_foreign_plan(compiled: dict, plan: "SequencePlan",
                           blocks: list[dict], flow_id: str) -> None:
    """Refuse a plan whose targets this compile does not account for.

    Every target ``to_plan`` builds comes from one compiled entry, keyed on
    that entry's node id, so when the plan really is
    ``to_sequence_plan(compiled, flow_id=flow_id)`` every one of its targets
    is some panel's. A target no panel claims means the plan came from
    somewhere else: most likely a compile WITHOUT ``flow_id``, whose uuid4 ids
    nothing can match, so every panel would read "nothing banked" and every
    frame of the campaign "orphaned". That payload is well-formed and wrong,
    and an operator would believe it, so it is an error instead.

    Not checked when an entry has no node id: ``to_plan`` leaves such an
    entry's ids uuid4 on purpose, and its target is unclaimable by design."""
    if any(not entry.get("node_id")
           for entry in compiled.get("targets") or []):
        return
    claimed = {p["target_id"] for b in blocks for p in b["panels"]}
    stray = [t.name for t in plan.targets if t.id not in claimed]
    if stray:
        raise ValueError(
            f"{len(stray)} plan target(s) match no block of this compile "
            f"({', '.join(stray)}): the plan was not compiled from it with "
            f"flow_id={flow_id!r}, so every count read against it would be "
            f"wrong")


def _counts(session: "Session | None") -> dict[str, int]:
    """Frames per step id, counted by the session's frozen ``count_mode``.

    ``Session._counts`` rather than a second copy of its rule: it is what
    ``owed()``, ``remaining()`` and ``done_map()`` count by, and the chip and
    the run's ending must never answer "is this done" differently (#252)."""
    return {} if session is None else session._counts()


def _summary(session: "Session | None") -> dict | None:
    if session is None:
        return None
    return {"id": session.id, "status": session.status,
            "nights": len(session.nights),
            "count_mode": session.plan.count_mode}


def _locked(session: "Session | None",
            target: "Target | None") -> dict | None:
    """``{pa_deg, source}`` for the angle ``session`` has locked ``target``
    to (owner ruling 9), or None when it has none.

    Read through ``Session.locked_angle``, keyed by the target id this compile
    names: a re-frame re-keys the ids (spec 3.3, ruling 3), and the lock the
    old ids hold is then nobody's here, which is the one way a lock is
    cleared. 0 is a real position angle, so it is shown like any other.

    Only the angle is taken from the record. Its ``solved_at`` and
    ``exposed_at`` are the lock's times, which a viewer may not read (see
    the module docstring), and its ``source`` is free text (``LOCK_SOURCE``).
    A record whose angle is not a finite number is shown as no lock:
    ``lock_angle`` never writes one, a hand-edited file can, and NaN in the
    answer would fail the route's JSON rendering for every reader."""
    if session is None or target is None:
        return None
    lock = session.locked_angle(target.id)
    if not isinstance(lock, dict):
        return None
    pa = lock.get("pa_deg")
    if (isinstance(pa, bool) or not isinstance(pa, (int, float))
            or not math.isfinite(pa)):
        return None
    return {"pa_deg": float(pa), "source": LOCK_SOURCE}


def _block_lock(panels: list[dict]) -> dict | None:
    """A TARGET block's locked angle: the one every panel the plan holds is
    locked to, or None. A single target is one panel, so this is that
    panel's lock. A block some of whose panels are unlocked, or locked to
    different angles, has no one angle to show, and each panel shows its
    own. A POOL block is never asked: its members are separate objects, not
    one field, so their angles agreeing would mean nothing."""
    held = [p for p in panels if p["target_id"] is not None]
    locks = [p.get("locked_angle") for p in held]
    if not locks or any(lock is None for lock in locks):
        return None
    if len({lock["pa_deg"] for lock in locks}) != 1:
        return None
    return dict(locks[0])


def _single(plan: "SequencePlan", entry: dict, *, flow_id: str,
            node_id: str) -> "Target | None":
    """The plan target a TARGET block compiled to, or None when ``to_plan``
    dropped it.

    Each plan target's id is recomputed from this block's compiled ``entry``,
    ITS OWN geometry and this block's node id, through ``identity.target_key``
    - the one function ``to_plan._identify`` minted it with. The node id is in
    the id, so a target of any other block cannot match, whatever its place in
    the list; the geometry comes from the target, so this needs no coordinate
    parsing. The entry is what says WHICH key: a TARGET with typed
    coordinates is keyed on the geometry it is at now, and one with only a
    name on the catalogue's canonical identity for the name (#189 A5, #229),
    because the catalogue's answer for a name moves between the compile the
    run made and the one the card reads, and the name as typed is only one
    spelling of the object. That identity comes from ``tonight.resolve_target``,
    the resolver ``to_plan._coords`` asked, so the two sides cannot disagree
    on which object a name is; the name as typed would key "M 31" apart from
    the "M31" ``to_plan`` keyed. It is asked here at now and there at the
    compile's instant, and answers alike because the resolver chooses the
    row at one shared instant, ``tonight.IDENTITY_WHEN`` (#249); chosen at
    each caller's own, a body and a fixed row tied on a name's best rank
    could swap between the two. A name the catalogue does not know was
    dropped by ``to_plan``, and is None here. Either way it is the block's
    1x1 grid at r0c0.

    KEYED AS ``to_plan`` KEYS A 1x1 BLOCK SINCE S3 (#189, spec 3.3): on its
    stored ANCHOR when the anchor is this block's, so a nudge the save carried
    finds the ids the counts were banked on, and on the angle the block is
    LAID OUT at (``_angles``), which for "Camera fixed at PA" is the planned
    angle the rotator is never told. Keyed on the commanded angle, as S1 did,
    a fixed-camera block matched nothing, and neither did a nudged one, and
    ``_refuse_a_foreign_plan`` then failed the whole answer. With no anchor
    and a commanded angle the key is S1's, byte for byte."""
    canonical = None
    if not identity.typed_coordinates(entry):
        name = str(entry.get("name") or "").strip()
        hit = tonight.resolve_target(name) if name else None
        if hit is None:
            return None
        canonical = hit.identity
    layout = _angles(entry)[1]
    for target in plan.targets:
        key = _block_key(entry, target.ra_hours, target.dec_deg, layout,
                         canonical, anchor=entry.get("frame_anchor"))
        group = identity.group_id(flow_id, node_id, key)
        if identity.target_id(group, 0, 0) == target.id:
            return target
    return None


def _group(plan: "SequencePlan", entry: dict, *, flow_id: str,
           node_id: str) -> "tuple[str | None, TargetGroup | None]":
    """``(group id, group)`` for a mosaic entry: the id ``to_plan`` gave the
    block's ``TargetGroup``, and that group in ``plan``, or None for either.

    THE ID IS RECOMPUTED, NEVER LOOKED UP BY NAME OR PLACE. ``to_plan``
    minted it as ``identity.group_id(flow_id, node_id, key)`` over
    ``_block_key``: the block's anchor first, its current geometry and grid
    otherwise (spec 3.3, ruling 3). The same four helpers answer the same
    question here, with the same inputs: the entry's coordinates as
    ``_coords`` parses or resolves them, the layout angle, and the grid with
    the overlap as a fraction. The node id is in the id, so no other block's
    group can match, whatever it is called.

    No id without a node id or a flow id (``to_plan`` gave the group a
    uuid4 then), nor for an entry ``to_plan`` dropped for want of
    coordinates. The group is None as well when the id names none, which is
    a block whose every panel is skipped: ``to_plan`` emits no group of no
    members."""
    if not flow_id or not node_id:
        return None, None
    coords = _coords(entry, None)
    if coords is None:
        return None, None
    ra_hours, dec_deg, canonical = coords
    nums = _mosaic_numbers(entry)
    key = _block_key(entry, ra_hours, dec_deg, _angles(entry)[1], canonical,
                     anchor=entry["mosaic"].get("frame_anchor"),
                     grid=dict(rows=nums["rows"], cols=nums["cols"],
                               overlap=nums["overlap"] / 100.0,
                               fov_x=nums["fov_x"], fov_y=nums["fov_y"]))
    gid = identity.group_id(flow_id, node_id, key)
    return gid, next((g for g in plan.groups if g.id == gid), None)


def _mosaic(plan: "SequencePlan", entry: dict, counts: dict[str, int],
            session: "Session | None", *, flow_id: str, node_id: str,
            name: str) -> dict:
    """The block entry of a mosaic: its panels found through its plan group
    (``_group``), in the plan's order, and the panels it skipped.

    A panel is a plan target whose ``mosaic_group`` is the group's id and
    nothing else: its name is a label the operator can repeat on another
    block, and a second block called "Veil" would otherwise take this one's
    frames. A block with no group in the plan has no panels, and a target
    of it the plan does hold then goes unclaimed, which
    ``_refuse_a_foreign_plan`` refuses rather than read as nothing banked."""
    gid, group = _group(plan, entry, flow_id=flow_id, node_id=node_id)
    rows = int(entry["mosaic"].get("rows") or 1)
    cols = int(entry["mosaic"].get("cols") or 1)
    block: dict = {"node_id": node_id, "name": name, "kind": "target",
                   "banked": 0, "owed": 0, "total": 0, "panels": [],
                   "grid": {"rows": rows, "cols": cols}}
    for target in plan.targets:
        if group is None or target.mosaic_group != group.id:
            continue
        panel = _panel(target, name, counts,
                       at=(target.panel_row, target.panel_col))
        lock = _locked(session, target)
        if lock is not None:
            panel["locked_angle"] = lock
        block["panels"].append(panel)
        for field in ("banked", "owed", "total"):
            block[field] += panel[field]
    block["skipped"] = _skipped(entry, gid, group, name, rows, cols, counts,
                                session)
    return block


def _skipped(entry: dict, gid: str | None, group: "TargetGroup | None",
             name: str, rows: int, cols: int, counts: dict[str, int],
             session: "Session | None") -> list[dict]:
    """The panels a mosaic skips, in grid order, each with what the ledger
    holds on it (spec 2.5: "re-enabling a panel restores its progress").

    WHICH PANELS: the group's ``skipped_ids``, the ids ``to_plan`` named for
    CONTINUE (spec 5.9). A block whose every panel is skipped has no group,
    so its panels are the compile's ``skip`` list, each id minted the way
    ``to_plan`` mints a panel's, ``identity.target_id(group, row, col)``.
    Row and col come from matching the id against every cell of the grid.

    WHAT IS BANKED: the frames whose target id is the panel's, counted by the
    session's count mode (``Session._counts``, one rule with every other
    count here), found by target id as ``plan_replace_report`` finds a
    skipped panel's frames. NOT capped at a step's count, as a live panel's
    are: the plan holds no steps for a skipped panel, so there is no count to
    cap at, and the engine stops a step at its count, so the ledger rarely
    holds more. A frame of the panel on a recipe that has since changed is
    counted too; re-enabling the panel will not bring that one back, and
    CONTINUE asks about it then (``plan_replace_report``)."""
    if gid is None:
        return []
    if group is not None:
        ids = list(group.skipped_ids)
    else:
        ids = [identity.target_id(gid, int(r) - 1, int(c) - 1)
               for r, c in (entry["mosaic"].get("skip") or [])]
    cell = {identity.target_id(gid, r, c): (r, c)
            for r in range(rows) for c in range(cols)}
    on: dict[str, set[str]] = {}
    for f in (session.frames if session is not None else []):
        on.setdefault(f.target_id, set()).add(f.step_id)
    out: list[dict] = []
    for tid in ids:
        row, col = cell.get(tid, (None, None))
        label = f"{row + 1}-{col + 1}" if row is not None else "?"
        out.append({"target_id": tid,
                    "name": f"{name} {label}" if name else label,
                    "row": row, "col": col,
                    "banked": sum(counts.get(sid, 0)
                                  for sid in on.get(tid, ()))})
    out.sort(key=lambda p: (p["row"] is None, p["row"] or 0, p["col"] or 0))
    return out


def _member(*, plan_by_id: dict[str, "Target"], flow_id: str, node_id: str,
            name: str, used: set[str]) -> "Target | None":
    """The plan target a POOL member compiled to, or None when ``to_plan``
    dropped it.

    The occurrence suffix is chosen the way ``to_plan._identify`` chose it:
    the first member key not yet issued in this pool. ``to_plan`` drops a
    member with no coordinates BEFORE keying it, so a miss here issues no key
    either, or every later copy of a name would be looked up one suffix too
    far. The name has to match as well as the id, because a member literally
    named "M31#1" has the same key as the second M31 (``identity.member_key``),
    and when that member is the one dropped the id alone would hand it the
    second M31's frames."""
    occurrence = 0
    while identity.member_key(name, occurrence) in used:
        occurrence += 1
    target = plan_by_id.get(identity.member_id(flow_id, node_id, name,
                                               occurrence))
    if target is None or target.name != name:
        return None
    used.add(identity.member_key(name, occurrence))
    return target


def _panel(target: "Target | None", name: str, counts: dict[str, int], *,
           at: tuple[int | None, int | None]) -> dict:
    """One panel's entry. ``at`` is its ``(row, col)``: ``(0, 0)`` for a
    single target, a mosaic panel's own place, ``(None, None)`` for a pool
    member, which has no place in any grid."""
    steps: list[dict] = []
    for step in (target.steps if target is not None else []):
        # Capped at the count: frames past it are real subs on a live step,
        # but a step cannot owe a negative number, and ``Session.remaining``
        # floors at zero for the same reason.
        banked = min(step.count, counts.get(step.id, 0))
        steps.append({"step_id": step.id, "filter": step.filter,
                      "frame_type": step.frame_type,
                      "exposure_s": step.exposure_s, "count": step.count,
                      "banked": banked, "owed": step.count - banked})
    return {"target_id": target.id if target is not None else None,
            "name": target.name if target is not None else name,
            "row": at[0], "col": at[1],
            "banked": sum(s["banked"] for s in steps),
            "owed": sum(s["owed"] for s in steps),
            "total": sum(s["count"] for s in steps),
            "steps": steps}


def _orphaned(plan: "SequencePlan", session: "Session | None",
              skipped: set[str]) -> dict:
    """Frames on steps no plan step has, and how many steps they sit on.

    A frame on a SKIPPED panel is not one (S3, spec 5.9): the panel's
    ``skipped`` entry shows it, and re-enabling the panel brings it back.
    ``skipped`` is the target ids the blocks list as skipped, so a frame is
    shown in one place, never as held and lost at once. It is recognised by
    its target id, as ``continuation.plan_replace_report`` recognises it
    through the groups' ``skipped_ids``, so the card and CONTINUE's
    dropped-steps question agree about which frames are lost, with one
    exception: a block whose every panel is skipped has no group in the
    plan, so ``plan_replace_report`` cannot see its skip and counts its
    frames as dropped, where this, reading the compile's skip list, shows
    them on the block (#335). A plan that skips nothing reads as it always
    did."""
    if session is None:
        return {"frames": 0, "steps": 0}
    live = {s.id for t in plan.targets for s in t.steps}
    lost: dict[str, int] = {}
    for f in session.frames:
        if f.step_id not in live and f.target_id not in skipped:
            lost[f.step_id] = lost.get(f.step_id, 0) + 1
    return {"frames": sum(lost.values()), "steps": len(lost)}
