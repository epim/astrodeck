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
  (``continuation.plan_replace_report``), with one difference S3 settles: a
  skipped panel's steps are not dropped there (spec 5.9), and here they
  are counted with the rest until S3 compiles a skip and the modal draws the
  panel as skipped. No compile names a skipped panel before S3.
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
  emits are pinned to an allow-list by ``tests/test_flows_progress.py``.
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

if TYPE_CHECKING:                       # pragma: no cover - typing only
    from ..sequence.models import SequencePlan, Target
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
        panel = _panel(target, name, counts, grid=not is_pool)
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
    return {"flow_id": flow_id,
            "session": _summary(session),
            "blocks": blocks,
            "orphaned": _orphaned(plan, session)}


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
    1x1 grid at r0c0. A key on anything else (the anchor S3 brings, a panel's
    row and col) has to be matched here the same way ``to_plan`` mints it, or
    every block reads "nothing banked"."""
    canonical = None
    if not identity.typed_coordinates(entry):
        name = str(entry.get("name") or "").strip()
        hit = tonight.resolve_target(name) if name else None
        if hit is None:
            return None
        canonical = hit.identity
    for target in plan.targets:
        key = identity.target_key(entry, target.ra_hours, target.dec_deg,
                                  target.rotation_deg, canonical=canonical)
        group = identity.group_id(flow_id, node_id, key)
        if identity.target_id(group, 0, 0) == target.id:
            return target
    return None


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
           grid: bool) -> dict:
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
            "row": 0 if grid else None, "col": 0 if grid else None,
            "banked": sum(s["banked"] for s in steps),
            "owed": sum(s["owed"] for s in steps),
            "total": sum(s["count"] for s in steps),
            "steps": steps}


def _orphaned(plan: "SequencePlan", session: "Session | None") -> dict:
    if session is None:
        return {"frames": 0, "steps": 0}
    live = {s.id for t in plan.targets for s in t.steps}
    lost = {sid: n for sid, n in session.recorded_by_step().items()
            if sid not in live}
    return {"frames": sum(lost.values()), "steps": len(lost)}
