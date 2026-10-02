# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""What a save does to a flow before it is written: ruling 2's counts switch
and ruling 3's server-owned anchor (#189 Revision 2, spec 3.1, 3.3).

PURE: no devices, no config, no clock, no disk. ``store.FlowStore.save`` reads
the stored flow and hands it in as ``prior``; a caller that must place a TARGET
known only by its name injects the catalogue as ``resolve``.

TWO RULES, ONE PASS, BECAUSE BOTH ARE THE SERVER'S AND NEVER THE CLIENT'S.

* COUNTS. The owner's ruling: "Only count accepted frames." Loading a flow
  never changes what it means, so a TARGET or POOL with no ``counts`` key, or
  with "Every sub taken", still compiles to attempts when it is read, and the
  read says so (``store._migrate``'s ``counts`` note). The save is the point
  at which the operator has seen that line and kept the flow, so every save
  rewrites ``counts`` to "Accepted subs" on every TARGET and POOL, whoever
  wrote the graph: both editors, the API, the wizard, the quick flow, or a
  stale tab POSTing an old-shaped graph.

* THE ANCHOR. ``frameAnchor`` is the geometry a block's counts started at,
  stored as exactly the text its identity key hashes (``identity.anchor_for``),
  so the ids hang off it rather than off where the block is drawn now. A
  client never supplies it: a client that could would choose which campaign a
  moved field's frames are credited to, which is the flaw D5 removes. It is
  read from the PRIOR stored node with the same id, and then kept when
  ``framing.reframe_carry`` says every panel corner moved less than half the
  overlap, and replaced otherwise. The anchor is compared with the new
  geometry and never with the previous save, so nudges under the threshold
  cannot add up across saves. A single panel anchored with no camera field
  and saved with one, nothing else changed, is COMPLETED rather than judged
  (#351, S4 orchestrator ruling 4): it keeps its key and records the field.
"""
from __future__ import annotations

import math
from typing import Callable

from ..catalog.coords import parse_dec, parse_ra
from ..catalog.framing import reframe_carry
from . import identity
from .compile import _grid_dim, is_multi_panel
from .models import FlowNode, FlowRecord
from .nodes import _rotation_deg, target_angle

#: What every save writes into ``counts`` (ruling 2), and what a new TARGET or
#: POOL is created with (``NodeDef.created_as``). A literal, pinned against
#: both created-as columns by test_flows_save_rules, so a reworded option in
#: one place is caught rather than silently making every save write a value
#: the compile reads as attempts.
ACCEPTED_SUBS = "Accepted subs"

#: The node types that carry ``counts`` (spec 3.1: POOL gets TARGET's
#: treatment, so a new pool-only flow is not the one new flow that counts
#: rejects).
COUNTED_TYPES: frozenset[str] = frozenset({"target", "pool"})

#: ``resolve(name)`` answers ``(ra_hours, dec_deg, canonical)`` for a TARGET
#: known only by its name, or None when the catalogue has no row for it. The
#: store injects ``tonight.resolve_target``'s answer; ``canonical`` is the
#: catalogue identity the block is keyed on (#229).
Resolver = Callable[[str], "tuple[float, float, str] | None"]


def counts_attempts(node_type, params) -> bool:
    """True when a node COUNTS EVERY SUB TAKEN: a TARGET or POOL whose
    ``counts`` is anything but "Accepted subs". A missing key is the old
    meaning ("Every sub taken", the missing-key default), and so is a value
    this build does not offer, because the plan counts accepted subs only
    when a block asks for them in those words (spec 3.3).

    The one test the read's note and the save's switch share, so the note
    can never promise a switch the save does not make."""
    if node_type not in COUNTED_TYPES:
        return False
    counts = params.get("counts") if isinstance(params, dict) else None
    return counts != ACCEPTED_SUBS


# ------------------------------------------------------ a block's geometry

def _fraction(percent) -> float:
    """The node's ``overlap`` (a PERCENT) as the fraction the key and
    ``compute_mosaic`` take, clamped to [0, 0.5] as ``compute_mosaic`` clamps
    it. A value that is not a finite number reads as the missing-key 25%.

    Clamped, not refused: ``identity.anchor_geometry`` refuses an overlap of
    1 or more, so an unclamped 150 typed into a raw field would be written as
    an anchor the compile then cannot read."""
    try:
        value = float(percent)
    except (TypeError, ValueError):
        value = float("nan")
    if not math.isfinite(value):
        value = identity.SINGLE_OVERLAP * 100.0
    return min(0.5, max(0.0, value / 100.0))


def _field(value) -> float:
    """A camera field in bin-1 degrees. Anything but a finite, positive number
    is 0, "not framed", the missing-key default: an anchor may not hold a
    negative field (``identity.anchor_geometry``)."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    return v if math.isfinite(v) and v > 0.0 else 0.0


def block_shape(params: dict) -> dict:
    """What a TARGET's field LOOKS like, from its params with the missing-key
    defaults applied (``FlowNode.with_defaults``): ``rows``, ``cols``,
    ``overlap`` (a fraction), ``rotation_deg`` (None for any angle),
    ``fov_x`` and ``fov_y``, as ``identity.anchor_for`` takes them.

    ``rotation_deg`` is the angle the grid is LAID OUT at: None for "Any
    angle", otherwise the ``rotation`` when it is 0 or more ("Camera fixed at
    PA" never commands it, but it placed every panel). A block with no
    ``angle`` key derives it from ``rotation`` (``nodes.target_angle``), so a
    TARGET saved before S3 is laid out, and keyed, as S1 keyed it. The grid
    is read as ``compile.is_multi_panel`` reads it (``_grid_dim``), so a
    block the compile calls one panel is keyed as one panel."""
    rotation = _rotation_deg(params.get("rotation"))
    angle = (None if target_angle(params) == "Any angle" or rotation < 0
             else rotation)
    return {"rows": _grid_dim(params.get("rows")),
            "cols": _grid_dim(params.get("cols")),
            "overlap": _fraction(params.get("overlap")),
            "rotation_deg": angle,
            "fov_x": _field(params.get("fovX")),
            "fov_y": _field(params.get("fovY"))}


def current_anchor(params: dict, *, resolve: Resolver | None = None
                   ) -> tuple[str, tuple[float, float]] | None:
    """The anchor a TARGET would have if its counts started now, and where it
    is: ``(anchor_text, (ra_hours, dec_deg))``. ``params`` carry the
    missing-key defaults.

    None when the block has NO LAYOUT, which is when ``to_plan`` drops it and
    keys nothing: typed coordinates that do not parse or lie off the sphere,
    or a name with no typed coordinates that ``resolve`` cannot place (or no
    ``resolve`` at all). "Typed" is ``identity.typed_coordinates``, the test
    ``to_plan`` and ``target_key`` both use, so a block is placed here by the
    same route it is keyed by there."""
    shape = block_shape(params)
    entry = {"name": params.get("name"), "ra": params.get("ra"),
             "dec": params.get("dec")}
    canonical = None
    if identity.typed_coordinates(entry):
        try:
            ra_hours = parse_ra(str(entry["ra"]))
            dec_deg = parse_dec(str(entry["dec"]))
        except (TypeError, ValueError):
            return None
    else:
        name = str(entry["name"] or "").strip()
        hit = resolve(name) if (name and resolve is not None) else None
        if hit is None:
            return None
        ra_hours, dec_deg, canonical = hit
        if canonical is None or not str(canonical).strip():
            return None
    if not (math.isfinite(ra_hours) and math.isfinite(dec_deg)
            and -90.0 <= dec_deg <= 90.0):
        return None
    text = identity.anchor_for(entry, ra_hours, dec_deg,
                               shape.pop("rotation_deg"),
                               canonical=canonical, **shape)
    return text, (ra_hours, dec_deg)


# -------------------------------------------------------------- the anchor

def _base_anchor(prior: FlowNode | None, resolve: Resolver | None) -> str:
    """The anchor a block's counts started at, as the stored flow holds it.

    THE PRIOR NODE'S STORED ``frameAnchor``, when it holds one. Otherwise the
    anchor it had IMPLICITLY: with no anchor the compile keys a block on its
    current geometry (``identity.target_key``), so a block saved before S3,
    or saved while it had no layout, banked its frames under the geometry
    the stored node is drawn at. Taking the new geometry instead would
    restart the counts of a flow nudged in its first S3 save, however small
    the nudge, and announce nothing (S3-G's finding).

    A stored anchor that is not an anchor (a hand edit; the server alone
    writes them) keyed nothing, because ``target_key`` refuses it, and is
    treated as absent. "" when there is no prior node, or it had no layout
    either: then nothing was keyed, and the new geometry starts the counts.

    THE SHAPE THE COMPILE KEYED, NOT THE SHAPE THE NODE HOLDS. With no
    anchor ``to_plan`` keys a multi-panel block on its grid, but a single
    panel on NO grid (``to_plan._block_key``): S1's single-target shape, 25%
    overlap and no camera field, whatever ``overlap``, ``fovX`` and ``fovY``
    the node carries. Read from the node, those would make the implicit
    anchor a geometry nothing was keyed on; the save would find it unchanged,
    carry it, and move the ids with nothing listed.
    """
    if prior is None:
        return ""
    stored = prior.params.get("frameAnchor")
    try:
        if identity.anchor_geometry(stored) is not None:
            return str(stored)
    except ValueError:
        pass
    params = prior.with_defaults().params
    if not is_multi_panel(prior):
        params = {**params, "overlap": identity.SINGLE_OVERLAP * 100.0,
                  "fovX": identity.SINGLE_FOV_X, "fovY": identity.SINGLE_FOV_Y}
    implicit = current_anchor(params, resolve=resolve)
    return "" if implicit is None else implicit[0]


def _anchor_on_save(node: FlowNode, prior: FlowNode | None,
                    resolve: Resolver | None) -> tuple[str, dict | None]:
    """``(frameAnchor, reanchored_row_or_None)`` for one TARGET.

    * no layout now: the base anchor, kept as it is. Nothing is keyed while
      the block cannot be placed, and a later save that places it again is
      measured against where its counts really started.
    * no base anchor: the current geometry. Nothing restarts, since nothing
      was keyed, so nothing is announced.
    * a single panel anchored with no camera field, saved with one and
      nothing else changed: the base COMPLETED (``identity.complete_anchor``,
      #351, S4 orchestrator ruling 4). Its key is kept, so its ids and counts
      are, and it records the field, against which later moves are judged.
      Judged as it was, its threshold of 0 would restart every single-target
      campaign the first time MATCH CAMERA recorded its field, although the
      pointing had not moved.
    * otherwise ``reframe_carry(base, now)``: kept on a carry; replaced by
      the current geometry, and listed, when it does not. The row carries
      the verdict's ``reason`` (#352, ruling 6), so a grid, angle or object
      change is said in its own words.
    * ...except a verdict against an anchor whose KEY the current geometry
      keeps, which restarts nothing: it is not listed, and the anchor is
      KEPT, as on a carry. Only a completed anchor can reach it: its keyed
      field is none, so a save that clears the field again moves every
      corner back to the centre, past the recorded field's threshold, and
      keys exactly as before. Listed, it would announce a restart that kept
      every id. Replaced by the cleared geometry, it would forget the field
      it records, and the next save could record ANOTHER field as though it
      were the first and keep every id: a change of field that re-anchors
      when made in one save would then carry when made in two, the anchor
      compared with the previous save after all (#390, found by S4-SAVE's
      verifier)."""
    base = _base_anchor(prior, resolve)
    now = current_anchor(node.with_defaults().params, resolve=resolve)
    if now is None:
        return base, None
    text, at = now
    if not base:
        return text, None
    base = identity.complete_anchor(base, text) or base
    verdict = reframe_carry(base, text, at=at)
    if verdict["carry"]:
        return base, None
    if identity.anchor_key(text) == identity.anchor_key(base):
        return base, None
    return text, {"node_id": node.id,
                  "max_move_deg": verdict["max_move_deg"],
                  "threshold_deg": verdict["threshold_deg"],
                  "reason": verdict["reason"]}


def prepare_save(record: FlowRecord, prior: FlowRecord | None, *,
                 resolve: Resolver | None = None
                 ) -> tuple[FlowRecord, list[str], list[dict]]:
    """``(record, migrated, reanchored)``: ``record`` as it is to be stored.

    ``prior`` is the flow as stored before this save (None for a new flow).
    Only its TARGET nodes are read, matched to the new graph by node id: the
    canvas node id is the one thing about a block an edit never changes.

    ``migrated`` is ``["counts"]`` when this save switched at least one
    ``counts`` to "Accepted subs", and ``[]`` when every TARGET and POOL
    already said it: it reports what the SAVE changed in the graph it was
    given, which is what the answer's "now counts accepted subs only" is.

    ``reanchored`` lists every TARGET whose counts this save restarts, as
    ``{node_id, max_move_deg, threshold_deg, reason}`` from
    ``reframe_carry`` (``max_move_deg`` None when no move was measured: the
    grid, the angle kind or the object changed, which ``reason`` names as
    ``grid``, ``angle`` or ``identity``; ``move`` otherwise), so an edit that
    restarts counts is said, and said in the words for what it changed
    (#352, S4 orchestrator ruling 6).

    Only ``counts`` and ``frameAnchor`` are written, and only into the nodes
    that carry them. Every other param stays as the client sent it: a
    missing key keeps meaning its missing-key default, and filling defaults
    in here would freeze today's defaults into every saved flow. ``record``
    is not modified; a copy is returned."""
    prior_targets = {n.id: n for n in (prior.graph.nodes if prior else [])
                     if n.type == "target"}
    switched = False
    reanchored: list[dict] = []
    nodes: list[FlowNode] = []
    for node in record.graph.nodes:
        if node.type not in COUNTED_TYPES:
            nodes.append(node)
            continue
        params = dict(node.params or {})
        if counts_attempts(node.type, params):
            params["counts"] = ACCEPTED_SUBS
            switched = True
        if node.type == "target":
            anchor, row = _anchor_on_save(node, prior_targets.get(node.id),
                                          resolve)
            params["frameAnchor"] = anchor
            if row is not None:
                reanchored.append(row)
        nodes.append(node.model_copy(update={"params": params}))
    graph = record.graph.model_copy(update={"nodes": nodes})
    return (record.model_copy(update={"graph": graph}),
            ["counts"] if switched else [], reanchored)
