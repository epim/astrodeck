"""The guided wizard's generator — three answers in, a night's graph out.

TRANSCRIBED from ``genWizard()`` in the prototype (``design_handoff_astrodeck_
flows/AstroDeck Flows.dc.html``), README §9. The 560px sheet is the UI's job;
this is the half that decides what actually gets built, and it lives on the
server for the same reason the doctor does — two implementations of one rule set
is the drift this project keeps re-finding.

THE ACCEPTANCE BAR IS THE DOCTOR. §9 ends on one sentence — "Every generated
graph must pass the doctor" — and it is the entire point of the feature. The
wizard exists so that somebody's FIRST flow is a night that works; a generated
graph that arrives already lit up with warnings teaches them, on that first
flow, that the doctor is decoration. So every kind × every subset of the six
automation chips is enumerated in ``tests/test_flows_wizard.py`` and each graph
is required to come back with nothing above `note`.

THREE PLACES THIS DEVIATES FROM THE PROTOTYPE, each marked DEVIATION below, each
because transcribing ``genWizard()`` literally produces a graph its own
``issues()`` faults:

  1. an unguided lane still asked for 120 s subs           (doctor rule 2)
  2. a queue with no flat panel still wanted flats         (doctor rule 7)
  3. a dome turned up with no safety monitor               (doctor rule 9, DANGER)

Where §9's two requirements disagree — "match genWizard()" and "must pass the
doctor" — the doctor wins: it is the stated hard requirement, and the prototype
was plainly never run through its own checks across the option matrix (the
sheet's own defaults, Guiding + HFR watchdog, are one of the combinations that
happens to come out clean). Each deviation below is the smallest one that clears
the rule, and each sets a value the editor itself offers, so nothing generated
here is unreachable by hand.

WHAT SLICE S3 CHANGED (#189 U-09, #190, #196; spec 1.4, 1.7, 1.8):

  * every node is CREATED, through ``nodes.create_params``, never read through
    the missing-key defaults. A generated TARGET therefore counts accepted subs
    and carries no angle nobody chose, and it starts with no name and no
    coordinates, so a typed name can never land on M31's (Revision 2 rulings 2
    and 9; #190);
  * no lane draws SLEW + CENTER any more: centring is part of the TARGET block
    (spec 1.7), and its settings never reached the run anyway;
  * a typed name with no coordinates is resolved through the catalogue (#190);
  * a fourth kind, Mosaic, lays a TARGET out as a grid from the rig's own
    camera field, at an angle the operator gave or the camera measured, and
    wires the panel loop from the tail of its lane (spec 1.4, 1.8).

WHAT SLICE S6 ADDED, the server half of "Send to Flow Wizard" (#196; spec
Revision 2 ruling 4, S6): the answers a door pre-fills, which
``generate_answer`` takes beside the three the sheet has always sent:

  * ``ra`` and ``dec``, the coordinates as typed, written onto the TARGET as
    given, and the name is then not looked up: a framing's centre is not the
    catalogue's row for the name;
  * ``skip``, the panels a framing took out, read as the TARGET's own `skip`
    is read (``compile.parse_skip``) and written as given;
  * ``cycle_plan`` and ``cycles``, the filter rows, a FILTER CYCLE's slot
    table, checked against the connected wheel as ``quick`` checks its
    filters, and held to the unguided sub cap on a lane that does not guide;
  * ``guiding``, which lights the Guiding chip, or confirms it is dark.

Each is inert when it is not given, so a body with only the three original
answers generates exactly what it did (``test_flows_wizard_door_answers``
pins that byte for byte), and each one the generator cannot honour is a
refusal that names it. The camera field, the measured angle and the wheel
stay rig facts the route injects; a client sends none of them.
"""
from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass

from ..catalog.coords import (format_dec_fits, format_ra_fits, parse_dec,
                              parse_ra)
from .compile import NEXT_PORT, PASS_PORT, grid_size, lane_tail, parse_skip
from .models import MY_FLOWS_FOLDER, FlowEdge, FlowGraph, FlowNode, FlowRecord
from .nodes import NODE_DEFS, TARGET_ANGLES, create_params, parse_cycle_plan
from .rig import RigFacts

# ---------------------------------------------------------------- the answers
# The three questions, verbatim from the sheet (screenshots/09-wizard-new-flow).
# Exported as constants because a caller matching on the string "EAA quick look"
# and a generator matching on "EAA Quick Look" would silently build a guided
# deep-sky night instead, which is the failure this whole module is about.
KIND_DEEP_SKY = "Deep-sky target"
KIND_POOL = "Best of several"
KIND_EAA = "EAA quick look"
#: The generator behind "Send to Flow Wizard" (#196, spec S3 item 4): one
#: TARGET block laid out as a grid, with the panel loop wired. Its stepped
#: sheet and its doors are S6's (#196): S4 built the framing modal and not
#: the sheet (spec section 8, S4's "Not built"). S6's server half gave the
#: route the answers a door pre-fills (the coordinates, the skipped panels,
#: the filter rows and guiding), and the three original kinds generate
#: exactly what they did.
KIND_MOSAIC = "Mosaic"
KINDS: tuple[str, ...] = (KIND_DEEP_SKY, KIND_POOL, KIND_EAA, KIND_MOSAIC)

OPT_GUIDING = "Guiding"
OPT_DUSK_FLATS = "Dusk flats"
OPT_DOME = "Dome"
OPT_CLOUD_DODGE = "Cloud-dodge calibration"
OPT_WATCHDOG = "HFR watchdog"
OPT_NOTIFY = "Notify my phone"
#: Chip order as the sheet renders them.
AUTOMATION_OPTIONS: tuple[str, ...] = (
    OPT_GUIDING, OPT_DUSK_FLATS, OPT_DOME, OPT_CLOUD_DODGE, OPT_WATCHDOG,
    OPT_NOTIFY)

#: What the sheet opens with (prototype state: kind + these two chips).
DEFAULT_OPTIONS: frozenset[str] = frozenset({OPT_GUIDING, OPT_WATCHDOG})

# ----------------------------------------------------------------- the layout
# The generated graph is dropped straight onto the canvas and the operator's
# first act is to read it, so these are real numbers, not filler: a node card is
# 188px wide (README §3), and both pitches leave a visible gap between cards.
_LANE_X0, _LANE_DX, _LANE_Y = 30.0, 228.0, 60.0        # the flow lane, one row
_RULES_X0, _RULES_DX, _RULES_Y = 30.0, 250.0, 380.0    # the rules row, beneath
_RULES_Y2 = _RULES_Y + 200.0                           # its second rank

#: EAA's capture overrides, from §9: "EAA: 4s g300 bin2 x60, goal 0". Short subs
#: on purpose — a live view is watched, not integrated, and `goal 0` says there
#: is no integration target to bank across nights.
_EAA_CAPTURE = {"exposure": 4, "gain": 300, "bin": "2", "count": 60, "goal": 0}

#: DEVIATION 1's number: the sub length the wizard writes when the operator did
#: NOT ask for guiding.
#:
#: A STARTING POINT, not a derivation. What an unguided mount can hold depends on
#: focal length, polar alignment, periodic error, declination and how much
#: trailing the operator will accept, and this generator knows none of those —
#: so the honest thing is a conservative opener the operator changes, which is
#: why ``generate()`` takes it as an argument and the wizard sheet offers it as a
#: field. 30 s is chosen to be short enough to be defensible at most focal
#: lengths rather than to be right at any particular one; the doctor's 120 s line
#: ("stars will trail at any real focal length") is the ceiling it stays well
#: under.
#:
#: Deliberately NOT presented to the operator as a recommendation. Sub length is
#: a question with more confident answers in circulation than good ones, and a
#: number the wizard states as if it computed something would carry authority it
#: has not earned.
UNGUIDED_EXPOSURE_DEFAULT = 30

#: Used when the operator generates without typing a target — they get a flow
#: they can find in the library rather than a second "Untitled flow".
_FALLBACK_NAME = {KIND_DEEP_SKY: "New deep-sky run", KIND_POOL: "Pool night",
                  KIND_EAA: "quick look", KIND_MOSAIC: "New mosaic"}

# ---------------------------------------------------------- the mosaic kind
# EVERY NUMBER THE MOSAIC KIND USES IS NAMED HERE, AT MODULE LEVEL, and the
# functions that build it hold no numeric literal at all. That is what lets
# `test_flows_wizard_mosaic` prove, by reading their source, that no angle is
# ever defaulted: an angle nobody chose is the I-04 defect (23.4 commanding a
# rotator, #150), and a literal inside the builder is the one place such a
# default could hide. None of the names below is an angle.

#: TARGET's three angle choices, unpacked so that a fourth added to
#: ``nodes.TARGET_ANGLES`` fails here, at import, rather than being quietly
#: offered to a mosaic.
ANY_ANGLE, ROTATE_TO_PA, CAMERA_FIXED_AT_PA = TARGET_ANGLES
#: The two a mosaic may use (doctor M2: a grid is laid out at one angle, and
#: "Any angle" lets every panel land however the camera happens to sit).
MOSAIC_ANGLES: tuple[str, ...] = (ROTATE_TO_PA, CAMERA_FIXED_AT_PA)
#: The smallest side a grid may have: one panel. A grid of one panel by one
#: is a single target, not a mosaic.
GRID_MIN = 1
#: The lowest overlap, in percent; the highest is `to_plan.OVERLAP_MAX_PCT`.
OVERLAP_MIN_PCT = 0.0
#: A TARGET holds its overlap in percent, `framing.DEFAULT_OVERLAP` is a
#: fraction.
_PERCENT = 100.0
#: A position angle is an angle modulo one turn. Folded into [0, 360) because
#: a negative `rotation` is "Any angle" to every reader (#150): a measured
#: -4.8 written as it came would be read as no angle at all.
_FULL_TURN_DEG = 360.0

#: The wizard's answer when the rig has no camera field (spec 1.8), verbatim.
#: It never emits a grid it cannot tile (doctor M1): it answers with a single
#: target and says this.
NO_OPTICS_REASON = ("set the camera and focal length in Settings > Optics to "
                    "plan a mosaic")


class _Canvas:
    """Node ids, positions and wires for one generation.

    Ids are minted n1, n2, … in creation order, so the same answers always give
    the identical graph. Determinism is not cosmetic: the wizard is how most
    flows will be born, and a caller that regenerates to compare two option sets
    would otherwise see every node as changed.
    """

    def __init__(self) -> None:
        self.nodes: list[FlowNode] = []
        self.edges: list[FlowEdge] = []
        self._minted = 0
        self._wired = 0

    def add(self, node_type: str, x: float, y: float) -> FlowNode:
        # CREATED, not loaded (spec 3.1): `create_params` overlays the node's
        # Created-as column on its missing-key defaults. The defaults keep
        # the meaning a key had before it existed, for flows saved before it;
        # a node the wizard makes is new, so it takes the new choices, and a
        # TARGET starts with no name, no coordinates, no angle and accepted
        # subs only (Revision 2 rulings 2 and 9, #190).
        self._minted += 1
        node = FlowNode(id=f"n{self._minted}", type=node_type,
                        x=float(x), y=float(y), params=create_params(node_type))
        self.nodes.append(node)
        return node

    def wire(self, a: FlowNode, a_port: str, b: FlowNode, b_port: str) -> None:
        # Edge ids counted too (the prototype's "we0", "we1", …). FlowEdge
        # otherwise defaults to a random uuid, which would make two generations
        # of identical answers differ on every wire.
        self.edges.append(FlowEdge(**{"id": f"we{self._wired}", "from": a.id,
                                      "fromPort": a_port, "to": b.id,
                                      "toPort": b_port}))
        self._wired += 1

    def chain(self, a: FlowNode, b: FlowNode) -> None:
        """Wire a's first FLOW output to b's first FLOW input.

        First-flow-port rather than a per-type table, transcribed from the
        prototype. It is not laziness: CAPTURE's second output is `frame`, an
        EVENT, and picking the first flow port is what stops the lane from
        wiring the frame stream into the session report — an edge the models
        would refuse anyway, but as a 500 rather than as a graph.
        """
        out = next((p for p in NODE_DEFS[a.type].outs if p.kind == "flow"), None)
        into = next((p for p in NODE_DEFS[b.type].ins if p.kind == "flow"), None)
        if out is not None and into is not None:
            self.wire(a, out.id, b, into.id)

    def graph(self) -> FlowGraph:
        return FlowGraph(nodes=self.nodes, edges=self.edges)


#: How SAFETY MONITOR is scoped when a CLOUD WATCH shares the graph with it.
#: Verbatim from the node's `watch` option list — the two tiers must not both
#: claim clouds (doctor rule 13).
_WATCH_PAIRED = "Rain + wind + power (pair with Cloud Watch)"


def _safety_pair(canvas: _Canvas, *, paired_with_cloudwatch: bool = False) -> FlowNode:
    """A safety monitor with something wired to it. Returns the monitor.

    Never a bare monitor. SAFETY MONITOR has no inputs, so doctor rule 1 cannot
    see one whose `unsafe` event goes nowhere — it would satisfy rule 9's "add a
    monitor" while closing nothing on rain, which is the precise shape of a
    promise nothing keeps.

    SCOPED AWAY FROM CLOUDS when the same flow has a CLOUD WATCH. The default is
    the standalone reading, which is right for a rig with no transient tier; put
    both on the same graph unscoped and safety aborts the night that the hold was
    there to ride out. The wizard knows which graph it just drew, so it is the
    one thing that can set this without asking.
    """
    safety = canvas.add("safety", _RULES_X0, _RULES_Y2)
    if paired_with_cloudwatch:
        safety.params["watch"] = _WATCH_PAIRED
    abort = canvas.add("abort", _RULES_X0 + _RULES_DX, _RULES_Y2)
    canvas.wire(safety, "unsafe", abort, "do")
    return safety


def _checked(kind: str, options: Iterable[str] | None) -> tuple[str, frozenset[str]]:
    """The answers, or a refusal naming what was not understood.

    Refused rather than quietly ignored. Dropping an unrecognised option hands
    back a night that is missing its dome — or its watchdog — while looking
    exactly like one that has it, and the operator finds out at 03:00. A
    misspelled chip is a client bug and reads as one here.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown wizard kind {kind!r}; expected one of {list(KINDS)}")
    opts = frozenset(options or ())
    unknown = sorted(opts - set(AUTOMATION_OPTIONS))
    if unknown:
        raise ValueError(f"unknown automation option(s) {unknown}; "
                         f"expected any of {list(AUTOMATION_OPTIONS)}")
    return kind, opts


#: What the wizard says when a TARGET was given no name and no coordinates.
#: The node is left blank, as a palette drop leaves it, and the run refuses it
#: ("no target the run could point at") rather than guessing one: before S3
#: the blank was filled with M31's, a default nobody chose (#190).
NO_TARGET_NOTE = ("no target was named, so the TARGET has no name and no "
                  "coordinates: set them in the editor before RUN")


def _sexagesimal(ra_hours: float, dec_deg: float) -> tuple[str, str]:
    """``(ra, dec)`` as a TARGET card writes them, ``"00h 42m 44.3s"`` and
    ``"+41° 16' 08\\""``, from a catalogue row's decimal hours and degrees.

    THROUGH THE FITS FORMATTERS, which round before they split. The card
    formatters (``format_ra``, ``format_dec``) truncate the minutes and round
    the seconds, so 42m 59.97s prints as "42m 60.0s". Both strings parse back
    through ``parse_ra``/``parse_dec`` to within 0.05 s of time and half an
    arcsecond of the row, far inside any centring tolerance."""
    h, m, s = format_ra_fits(ra_hours).split()
    d, am, asec = format_dec_fits(dec_deg).split()
    return f"{h}h {m}m {s}s", f"{d}° {am}' {asec}\""


def _resolve_name(name: str) -> tuple[tuple[str, str] | None, str | None]:
    """``(coords, note)`` for a TARGET given a name and no coordinates (#190).

    THROUGH THE ONE RESOLVER, ``tonight.resolve_target``: the one ``to_plan``
    places a name-only TARGET with and keys its ids on, so the wizard and the
    run cannot pick two objects for one name. Looked up on the module at call
    time, as ``to_plan`` does, so a test that replaces it replaces it for all.

    * A FIXED ROW (a deep-sky object, a star) gets its coordinates written onto
      the node. The card then shows where the run will point, and the doctor's
      convergence and angle rules, which measure typed coordinates only, can
      measure a mosaic of it.
    * A MOVING BODY (a planet, the Moon, a comet, a satellite) gets none. Its
      coordinates are the catalogue's answer at one instant, and written down
      they would point tomorrow's run at where it was when the wizard ran. Left
      name-only, the compile places it where it is at each run (spec 3.3).
    * A NAME THE CATALOGUE DOES NOT KNOW gets none, and a note that says so.
      The node keeps the name the operator typed and the run refuses it for
      want of coordinates, which is what #190 asked for: before S3 it would
      have slewed to M31 and filed the frames under the typed name."""
    from . import tonight       # loads the sequence package, so only when asked
    hit = tonight.resolve_target(name)
    if hit is None:
        return None, (f"the catalogue has no {name!r}, so its TARGET has no "
                      f"coordinates: type its RA and Dec in the editor before "
                      f"RUN")
    if hit.moves:
        return None, None
    return _sexagesimal(hit.ra_hours, hit.dec_deg), None


def _mosaic_answers_belong(kind: str, **answers) -> None:
    """Refuse a grid, an angle or a skip given with a kind that is not
    Mosaic.

    Refused rather than dropped, for ``_checked``'s reason: a client that sent
    a 3x2 with "Deep-sky target" would otherwise get one panel back looking
    exactly like the mosaic it asked for. The rig facts (``rig``,
    ``measured_pa_deg``) are not answers, and the route may pass them with any
    kind."""
    if kind == KIND_MOSAIC:
        return
    given = sorted(k for k, v in answers.items()
                   if v is not None and v is not False)
    if given:
        raise ValueError(f"{', '.join(given)} belong to the {KIND_MOSAIC!r} "
                         f"kind; this is {kind!r}")


def _mosaic_grid(rows, cols, overlap_pct) -> tuple[int, int, float]:
    """``(rows, cols, overlap in percent)``, checked against the bounds the
    compile and the projection hold (``to_plan.GRID_MAX`` and
    ``OVERLAP_MAX_PCT``), so a grid the wizard writes is one the run takes.

    The overlap defaults to ``framing.DEFAULT_OVERLAP``, the one server
    constant for it (spec 2.4); rows and cols have no default, because a grid
    is the one thing a mosaic answer is for."""
    from ..catalog import framing
    from .to_plan import GRID_MAX, OVERLAP_MAX_PCT
    sides: list[int] = []
    for what, value in (("rows", rows), ("cols", cols)):
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not float(value).is_integer()
                or not GRID_MIN <= value <= GRID_MAX):
            raise ValueError(f"a mosaic's {what} is a whole number from "
                             f"{GRID_MIN} to {GRID_MAX}, not {value!r}")
        sides.append(int(value))
    n_rows, n_cols = sides
    if n_rows == n_cols == GRID_MIN:
        raise ValueError(f"a grid of {GRID_MIN} x {GRID_MIN} is a single "
                         f"target, not a mosaic; use the "
                         f"{KIND_DEEP_SKY!r} kind")
    if overlap_pct is None:
        overlap = framing.DEFAULT_OVERLAP * _PERCENT
    else:
        overlap = overlap_pct
        if (isinstance(overlap, bool) or not isinstance(overlap, (int, float))
                or not math.isfinite(overlap)
                or not OVERLAP_MIN_PCT <= overlap <= OVERLAP_MAX_PCT):
            raise ValueError(f"a mosaic's overlap is a percentage from "
                             f"{OVERLAP_MIN_PCT:g} to {OVERLAP_MAX_PCT:g}, "
                             f"not {overlap_pct!r}")
    # 25, not 25.0, in the node's params: the inspector renders it.
    return n_rows, n_cols, (int(overlap) if float(overlap).is_integer()
                            else float(overlap))


def _mosaic_angle(angle_mode, pa_deg, use_measured, measured_pa_deg,
                  rig: RigFacts | None) -> tuple[str, float]:
    """``(angle, pa_deg)`` for a mosaic: the operator's PA, or the angle the
    camera measured (USE MEASURED), and NEVER A DEFAULT (spec 1.8).

    A mosaic with no angle cannot tile (doctor M2), and an angle nobody chose
    is the I-04 defect: 23.4, the M31 Example's angle copied in as a palette
    default, commanded every connected rotator to it (#150). So when neither
    is given the answer is a refusal naming both ways to give one.

    ``measured_pa_deg`` is a rig fact the route injects from the last solve's
    sky angle (``status.sky_angle``). Asked for with none recorded, it is a
    refusal too: a fixed camera laid out at a guessed angle holds the mosaic at
    its first panel (spec 5.6).

    "Rotate to PA" is refused when the rig says it has no rotator: the modal
    locks that choice with a reason (spec 2.4), and a generated graph must not
    offer what the editor would not. An unknown rotator (``has_rotator`` None)
    is not a "no"."""
    if angle_mode not in MOSAIC_ANGLES:
        raise ValueError(
            f"a mosaic is laid out at one camera angle, so its angle is "
            f"{' or '.join(repr(a) for a in MOSAIC_ANGLES)}, not "
            f"{angle_mode!r}")
    if (angle_mode == ROTATE_TO_PA and rig is not None
            and rig.has_rotator is False):
        raise ValueError(
            f"the active profile has no rotator, so nothing can turn the "
            f"camera to a PA: choose {CAMERA_FIXED_AT_PA!r} and lay the grid "
            f"out at the angle the camera sits at")
    if use_measured:
        if pa_deg is not None:
            raise ValueError("give a PA or ask for the angle the camera "
                             "measured, not both")
        if measured_pa_deg is None:
            raise ValueError("the camera has no measured angle yet: no "
                             "centring solve has recorded one. Type the PA")
        value, what = measured_pa_deg, "the measured angle"
    else:
        if pa_deg is None:
            raise ValueError("a mosaic is laid out at one camera angle: type "
                             "the PA, or use the angle the camera measured")
        value, what = pa_deg, "the PA"
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value)):
        raise ValueError(f"{what} is a finite number of degrees, not "
                         f"{value!r}")
    return angle_mode, float(value) % _FULL_TURN_DEG


#: Where a generated block's camera field came from, when the route's rig
#: facts carry no provenance line of their own.
_FOV_FROM_LIVE = "the rig's live optics when the wizard planned it"


def checked_skip(skip, rows: int, cols: int) -> str | None:
    """The skipped panels as given, or None for none; a refusal naming what
    cannot be read (#196, spec S6).

    READ AS THE TARGET'S OWN `skip` IS READ, by ``compile.parse_skip`` ("r-c"
    entries, 1-based, commas or semicolons), so the panels the door says are
    left out are the ones the compile leaves out. The compile answers an
    entry it cannot place with a warning and skips nothing for it; the door
    refuses it instead, because a skip that skipped nothing would shoot a
    panel the operator took out, and the door is the one place that can
    still ask. A skip of every panel is refused too: ``to_plan`` drops such
    a block whole, and the answer would be a mosaic that shoots nothing.

    Handed back AS GIVEN, not rewritten into the compile's order: it is the
    operator's text, and both readers read it the same. Blank is no skip.
    PUBLIC because the route's door asks it too: with no camera field the
    generator never reads the grid, and a skip of it would otherwise come
    back as a flow unread."""
    if skip is None:
        return None
    if not isinstance(skip, str):
        raise ValueError(f"skip is the panels to leave out, written "
                         f"row-column like '2-3, 1-1', not {skip!r}")
    if not skip.strip():
        return None
    cells, unread = parse_skip(skip, rows, cols)
    if unread:
        raise ValueError(
            f"skip {', '.join(repr(u) for u in unread)} names no panel of "
            f"this grid of {grid_size(rows, cols)}: a panel is written "
            f"row-column, from 1-1 to {rows}-{cols}")
    if len(cells) >= rows * cols:
        raise ValueError(f"skip names every panel of this grid of "
                         f"{grid_size(rows, cols)}, so the mosaic would "
                         f"shoot nothing: leave a panel in")
    return skip


def _mosaic_params(rows, cols, overlap_pct, angle_mode, pa_deg, use_measured,
                   measured_pa_deg, rig: RigFacts | None, skip=None
                   ) -> tuple[dict | None, str | None]:
    """``(params, reason)``: the TARGET params that make it a grid, or None
    and the reason it stays one target.

    THE CAMERA FIELD COMES FROM THE RIG (spec 1.8): ``rig.fov_deg``, the live
    effective optics at bin 1, which is what the modal's MATCH CAMERA
    snapshots. With none there is nothing to tile from, so the answer is a
    single target and ``NO_OPTICS_REASON``, never a grid of 0 x 0 degree
    panels (doctor M1). Checked FIRST: without a field the grid and the angle
    cannot be used at all, and a refusal about an angle that would be thrown
    away would hide the one thing the operator has to fix.

    THE SKIP (S6) is read against the grid it names, so after the grid and
    only with one, as the grid is: with no field neither is read."""
    field = None if rig is None else rig.fov_deg
    if field is None:
        return None, NO_OPTICS_REASON
    n_rows, n_cols, overlap = _mosaic_grid(rows, cols, overlap_pct)
    skipped = checked_skip(skip, n_rows, n_cols)
    angle, pa = _mosaic_angle(angle_mode, pa_deg, use_measured,
                              measured_pa_deg, rig)
    fov_x, fov_y = field
    params = {"rows": n_rows, "cols": n_cols, "overlap": overlap,
              "fovX": fov_x, "fovY": fov_y,
              "fovFrom": rig.fov_from or _FOV_FROM_LIVE,
              "angle": angle, "rotation": pa}
    if skipped is not None:
        params["skip"] = skipped
    return params, None


def _wire_the_loop(canvas: "_Canvas", target: FlowNode) -> None:
    """The panel loop (spec 1.4): ``<tail>.pass -> target.next``, from the
    TAIL of the block's panel lane as ``compile.lane_tail`` finds it, the same
    function the compile and the doctor read the lane with, so the wire the
    wizard draws is the wire they call the loop (M3 silent, M12 silent).

    The wizard's lane always ends its panel lane on the capture stage (the
    REPORT after it ends the lane), and both capture stages have a `pass`
    output; a lane without one is a bug here, not an answer to give."""
    tail = lane_tail(canvas.graph(), target)
    if tail is None or NODE_DEFS[tail.type].port(PASS_PORT, "out") is None:
        raise RuntimeError(f"the wizard's panel lane ended on {tail!r}, "
                           f"which has no {PASS_PORT!r} output")
    canvas.wire(tail, PASS_PORT, target, NEXT_PORT)


# -------------------------------------------------------- the door's answers
# What Send to Flow Wizard pre-fills (#196, spec Revision 2 ruling 4, S6),
# checked here, where the generator is, so the route and any other caller
# refuse one set of things. Every one is optional and INERT WHEN NOT GIVEN:
# none of these helpers answers a missing answer with a value, because a
# body carrying only the three original answers must generate exactly what
# it did (ruling 4), and a default here would be a change to every flow the
# sheet has ever made.

#: The most passes a door's filter rows may ask for. The quick sheet's own
#: ceiling on subs per filter (``FlowQuickBody.subs``), since ``cycles`` is
#: that same number: how many subs of each filter, one per pass.
CYCLES_MAX = 10_000

#: One filter row, read WHOLE: ``"<filter> <seconds>"``. The FILTER CYCLE's
#: own reader (``nodes.parse_cycle_plan``) matches a row's start and drops a
#: row it cannot read, so "L 0.5" would be read as "L 0" and "L sixty" not
#: at all; the door reads each row to its end and refuses one it cannot, so
#: the table the cycle carries is exactly the one the operator was shown.
_ROW_RE = re.compile(r"(\S+)\s+(\d+)")


def _text_answer(what: str, value) -> str | None:
    """A text answer stripped, or None when it is not given or blank: a
    door that always sends its fields sends empty ones for what it does not
    know, and an empty answer is not an answer. Anything but text is a
    refusal naming it."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{what} is text, not {value!r}")
    return value.strip() or None


def checked_cycles(cycles) -> int:
    """``cycles`` as a whole number from 1 to ``CYCLES_MAX``, or a refusal.

    Refused as the grid's sides are (``_mosaic_grid``): a bool or a string is
    not a count even where Python would read one as a number, and a whole
    float (3.0) is taken as 3. PUBLIC because the route's door asks it before
    pydantic's int coercion, which would read ``true`` as 1 and "3" as 3."""
    if (isinstance(cycles, bool) or not isinstance(cycles, (int, float))
            or not math.isfinite(cycles) or not float(cycles).is_integer()
            or not 1 <= cycles <= CYCLES_MAX):
        raise ValueError(f"cycles is a whole number of passes from 1 to "
                         f"{CYCLES_MAX}, not {cycles!r}")
    return int(cycles)


def checked_guiding(guiding) -> bool | None:
    """``guiding`` as given (True, False or None), or a refusal: a 1 or a
    "true" is a client that means something the answer does not say. PUBLIC
    for the route's door, which asks before pydantic's bool coercion."""
    if guiding is None or isinstance(guiding, bool):
        return guiding
    raise ValueError(f"guiding is true or false, not {guiding!r}")


def _guided(kind: str, opts: frozenset[str]) -> bool:
    """Whether the lane guides: the Guiding chip, never for EAA (a 4-second
    sub does not need a guider). ONE READING for the lane and for the
    unguided cap on the door's rows, so the cap is asked of exactly the
    lanes that draw no GUIDE."""
    return kind != KIND_EAA and OPT_GUIDING in opts


def _unguided_seconds(unguided_exposure_s) -> int | float:
    """The sub length an unguided lane is held to: the answer's, or
    ``UNGUIDED_EXPOSURE_DEFAULT`` for one that is missing, garbled or not
    positive. An int when it is whole (30, not 30.0, in the node's params).

    A garbled or non-positive override falls back to the default rather than
    being honoured: an exposure of 0 compiles to a step that captures
    nothing, and the wizard's whole promise is that its output runs."""
    try:
        secs = float(unguided_exposure_s)
    except (TypeError, ValueError):
        secs = float(UNGUIDED_EXPOSURE_DEFAULT)
    if secs <= 0:
        secs = float(UNGUIDED_EXPOSURE_DEFAULT)
    if secs.is_integer():
        secs = int(secs)
    return secs


def _door_guiding(kind: str, opts: frozenset[str], guiding) -> frozenset[str]:
    """The chips with the ``guiding`` answer applied.

    ``True`` lights the Guiding chip, so the lane is exactly the one the chip
    draws. ``False`` beside a lit chip is two answers that disagree, and is
    refused rather than settled either way: turning the chip off would drop
    an answer, and keeping it would ignore one. ``True`` for EAA is refused
    for the reason the lane never guides EAA; today's sheet may light the
    chip under EAA and the lane ignores it, but an answer that says "guide"
    in so many words is not one to ignore. ``None`` leaves the chips as
    they are, which is what a body with no ``guiding`` has always meant."""
    guiding = checked_guiding(guiding)
    if guiding is None:
        return opts
    if guiding is False:
        if OPT_GUIDING in opts:
            raise ValueError(f"guiding is false, but the options light the "
                             f"{OPT_GUIDING!r} chip: send one answer")
        return opts
    if kind == KIND_EAA:
        raise ValueError(f"guiding is true, but an {KIND_EAA!r} never "
                         f"guides: its short subs need no guider, and the "
                         f"settle time would make the live view stutter")
    return opts | {OPT_GUIDING}


#: What each typed coordinate is read with and the range it must fall in:
#: ``(parse, lowest, highest, highest included, unit, the range in words)``.
_COORDS = {"ra": (parse_ra, 0.0, 24.0, False, "hours",
                  "an RA runs from 0 up to 24 hours"),
           "dec": (parse_dec, -90.0, 90.0, True, "degrees",
                   "a Dec runs from -90 to +90 degrees")}


def _door_coords(kind: str, target: str, ra, dec) -> tuple[str, str] | None:
    """``(ra, dec)`` as typed (stripped), or None when neither is given.

    PARSED HERE, as ``_quick_coords`` parses the quick flow's, by the readers
    the run reads the node with, so a coordinate the night would refuse at
    RUN is refused while the sheet that can fix it is open; and held to its
    range, since ``parse_ra`` reads "nan" as a number and nothing downstream
    asks whether an RA of 25 hours is one. Refused as well: one of the two
    without the other (half a position is no position), coordinates for a
    POOL (it has no TARGET to carry them; its candidates are placed by
    name), and coordinates with no name, since the frames are filed under
    the name and a nameless TARGET files them under nothing."""
    ra, dec = _text_answer("ra", ra), _text_answer("dec", dec)
    if ra is None and dec is None:
        return None
    if ra is None or dec is None:
        has = "an ra and no dec" if dec is None else "a dec and no ra"
        raise ValueError(f"typed coordinates are an ra and a dec together; "
                         f"this answer has {has}")
    if kind == KIND_POOL:
        raise ValueError(f"ra and dec place a TARGET, and {KIND_POOL!r} has "
                         f"none: its candidates are placed by name")
    if not (target or "").strip():
        raise ValueError("ra and dec need the target's name as well: the "
                         "frames are filed under it")
    for what, text in (("ra", ra), ("dec", dec)):
        parse, low, high, closed, unit, words = _COORDS[what]
        try:
            value = parse(text)
        except Exception as e:
            raise ValueError(f"cannot read the {what} {text!r}: {e}") from e
        if not (math.isfinite(value) and low <= value
                and (value <= high if closed else value < high)):
            raise ValueError(f"the {what} {text!r} is {value:g} {unit}; "
                             f"{words}")
    return ra, dec


def _door_rows(kind: str, cycle_plan, cycles, wheel
               ) -> tuple[str, int, list[tuple[str, int]]] | None:
    """``(cycle_plan, cycles, rows)`` for the door's filter rows, or None
    when there are none; a refusal naming what is off.

    CHECKED AGAINST THE WHEEL AS ``quick`` CHECKS ITS FILTERS
    (``_unknown_filters``, the one rule and the one sentence for both):
    ``wheel`` is the connected wheel's usable slot names, a rig fact the
    route injects, and None means no wheel is connected, so the assumed seven
    apply. A filter name lands in the FITS header, the filename and the
    calibration key, so a name the wheel does not have is a refusal, never a
    row the run discovers at 21:00.

    Each row is read whole (``_ROW_RE``) and must be at least a second: a
    zero-second row compiles to a step that captures nothing. The rows keep
    the order they were given in, which is the order the sheet listed them
    in (the wheel's, from the rows ``cyclePlanRows.ts`` draws).

    ``cycles`` goes with the rows and only with them: rows with no count, or
    a count with no rows, is refused rather than filled in, since either
    default (1 pass, or the FILTER CYCLE's shipped 45) would be a number
    nobody gave. EAA refuses rows: its answer is a CAPTURE LOOP of 4-second
    subs, and a FILTER CYCLE in its place would be a different night."""
    plan = _text_answer("cycle_plan", cycle_plan)
    if plan is None:
        if cycles is not None:
            raise ValueError("cycles is how many subs of each filter row, "
                             "and this answer has no cycle_plan")
        return None
    if kind == KIND_EAA:
        raise ValueError(f"cycle_plan belongs to a night of filter rows; an "
                         f"{KIND_EAA!r} shoots its short subs through one "
                         f"CAPTURE LOOP")
    if cycles is None:
        raise ValueError("cycle_plan needs cycles: how many subs of each "
                         "filter row")
    passes = checked_cycles(cycles)
    rows: list[tuple[str, int]] = []
    for chunk in plan.split(","):
        token = chunk.strip()
        if not token:
            continue            # "L 60, R 60," is two rows to the cycle too
        m = _ROW_RE.fullmatch(token)
        if m is None:
            raise ValueError(f"cycle_plan row {token!r} is not '<filter> "
                             f"<seconds>': a filter the wheel names and a "
                             f"whole number of seconds")
        secs = int(m.group(2))
        if secs <= 0:
            raise ValueError(f"cycle_plan row {token!r} is {secs} seconds, "
                             f"which shoots nothing")
        rows.append((m.group(1), secs))
    if not rows:
        raise ValueError(f"cycle_plan {plan!r} names no filter row")
    refusal = _unknown_filters([f for f, _ in rows], wheel)
    if refusal is not None:
        raise ValueError(f"cycle_plan: {refusal}")
    return plan, passes, rows


def _within_the_unguided_cap(rows: list[tuple[str, int]],
                             unguided_exposure_s) -> None:
    """Refuse a filter row an unguided lane could not hold.

    The same cap the lane's CAPTURE LOOP is written to (DEVIATION 1, and
    ``UNGUIDED_EXPOSURE_DEFAULT``'s reasons), and the one the sheet shows
    beside the guiding step. A CAPTURE LOOP's sub is the wizard's to set, so
    it is shortened; a filter row's length is the operator's answer, so a
    row past the cap is refused, naming the two ways out, rather than
    silently shortened or left to trail: a row of 180 s with no GUIDE is
    the doctor's rule 2 on the operator's first flow. The cap itself may be
    answered up to 3600 s, past that rule's 120 s line, for the CAPTURE LOOP
    and these rows alike (#432)."""
    cap = _unguided_seconds(unguided_exposure_s)
    over = [f"{f} {s}" for f, s in rows if s > cap]
    if over:
        raise ValueError(f"cycle_plan row(s) {over} run longer than the "
                         f"{cap:g} s an unguided lane is held to: turn "
                         f"guiding on, or shorten them")


def generate(kind: str = KIND_DEEP_SKY,
             options: Iterable[str] | None = None,
             target: str = "",
             unguided_exposure_s: float | None = None,
             *,
             cycle_plan: str = "",
             cycles: int = 1,
             coords: tuple[str, str] | None = None,
             safety_abort: bool = False,
             rows: int | None = None,
             cols: int | None = None,
             overlap_pct: float | None = None,
             angle_mode: str | None = None,
             pa_deg: float | None = None,
             use_measured: bool = False,
             skip: str | None = None,
             rig: RigFacts | None = None,
             measured_pa_deg: float | None = None) -> FlowGraph:
    """The graph for one set of wizard answers.

    ``kind`` is one of :data:`KINDS`, ``options`` any subset of
    :data:`AUTOMATION_OPTIONS`, ``target`` the free-text target — a comma list
    means pool candidates. ``unguided_exposure_s`` is the sub length used when
    guiding was NOT asked for; ``None`` takes
    :data:`UNGUIDED_EXPOSURE_DEFAULT`. It is an argument rather than a constant
    because the right value is a property of the rig and the operator's
    tolerance, neither of which this function can see.

    Shape (§9): the flow lane is dusk → [dome] → [dusk flats] → target|pool →
    autofocus → [guide] → capture → report, laid out left to right in the
    order the night runs; underneath it sits a rules row of whatever the chips
    asked for. Guide is skipped for EAA whether or not the chip is lit, because
    a 4-second sub does not need one and paying the settle time per frame would
    make a live view stutter. There is NO SLEW + CENTER (spec 1.7, S3): the
    TARGET block centres, with the tolerance and tries it carries, and the
    stage's own settings never reached the run.

    FOUR KEYWORD-ONLY EXTRAS, all inert at their defaults, exist so ``quick()``
    below can reuse THIS builder instead of drawing a second deep-sky graph:

    * ``cycle_plan`` -- a FILTER CYCLE slot table (``"L 60, R 60, …"``). When it
      is given the capture stage is a FILTER CYCLE carrying it rather than a
      CAPTURE LOOP, and every rule that hangs off the capture stage (the HFR
      watchdog's ``frame`` wire, the doctor's guide/focus checks) follows it
      there, because both node types are capture stages.
    * ``cycles`` -- that cycle's pass count (``perCycle`` stays 1).
    * ``coords`` -- ``(ra, dec)`` written onto the TARGET node as given. With
      None, a typed name is resolved through the catalogue (``_resolve_name``,
      #190); the node itself starts with no coordinates at all.
    * ``safety_abort`` -- mint the SAFETY MONITOR → ABORT + PARK pair even with
      no dome and no notify chip. It is the same ``_safety_pair`` the dome
      branch uses, so a graph never ends up with two monitors.

    THE MOSAIC KIND'S ANSWERS (spec 1.8, S3 item 4), refused with any other
    kind: ``rows`` and ``cols`` (1 to ``to_plan.GRID_MAX``, more than one
    panel), ``overlap_pct`` (percent; None takes ``framing.DEFAULT_OVERLAP``),
    ``angle_mode`` (:data:`MOSAIC_ANGLES`), either ``pa_deg`` or
    ``use_measured``, and ``skip`` (S6: the panels to leave out, read and
    refused by ``checked_skip``, written onto the block as given; None or
    blank is none). And two rig facts the route injects, never answers:
    ``rig`` (``RigFacts``; its ``fov_deg`` is the camera field the grid is
    tiled from, its ``has_rotator`` gates "Rotate to PA") and
    ``measured_pa_deg`` (the sky angle the last centring solve recorded, for
    USE MEASURED). See ``_mosaic_params`` for what each does when missing.

    A SECOND BUILDER IS THE THING THIS MODULE EXISTS TO PREVENT. The header
    above names drift between two implementations of one rule set as what this
    project keeps re-finding; a "quick" generator that drew its own dusk →
    target → autofocus → guide → report lane would be exactly that, and it
    would drift on the first deviation the doctor forces on one of them. The
    mosaic kind is this builder too, for the same reason.
    """
    return _generate(
        kind, options, target, unguided_exposure_s, cycle_plan=cycle_plan,
        cycles=cycles, coords=coords, safety_abort=safety_abort, rows=rows,
        cols=cols, overlap_pct=overlap_pct, angle_mode=angle_mode,
        pa_deg=pa_deg, use_measured=use_measured, skip=skip, rig=rig,
        measured_pa_deg=measured_pa_deg)[0]


def _generate(kind, options, target, unguided_exposure_s, *, cycle_plan,
              cycles, coords, safety_abort, rows, cols, overlap_pct,
              angle_mode, pa_deg, use_measured, skip, rig, measured_pa_deg
              ) -> tuple[FlowGraph, list[str]]:
    """``generate``'s graph and the notes that go with it: why a mosaic was
    answered as one target, why a TARGET has no coordinates. ``generate``
    documents every argument."""
    kind, opts = _checked(kind, options)
    if isinstance(skip, str) and not skip.strip():
        skip = None             # a blank skip is no skip, with any kind
    _mosaic_answers_belong(kind, rows=rows, cols=cols,
                           overlap_pct=overlap_pct, angle_mode=angle_mode,
                           pa_deg=pa_deg, use_measured=use_measured,
                           skip=skip)
    notes: list[str] = []
    layout: dict | None = None
    if kind == KIND_MOSAIC:
        layout, why = _mosaic_params(rows, cols, overlap_pct, angle_mode,
                                     pa_deg, use_measured, measured_pa_deg,
                                     rig, skip)
        if why is not None:
            notes.append(why)
    tname = (target or "").strip()
    unguided_s = _unguided_seconds(unguided_exposure_s)
    canvas = _Canvas()

    # ------------------------------------------------------------- flow lane
    # TODO(flows-handoff): the lane always opens with a DUSK WINDOW, EAA
    # included — transcribed, but it means a "quick look" compiles to
    # start_mode=dusk and sits waiting, while the EAA *example* fixture has no
    # dusk node and starts now. Which is right for the wizard's EAA answer?
    lane_types = ["dusk"]
    if OPT_DOME in opts:
        lane_types.append("dome")
    if OPT_DUSK_FLATS in opts:
        lane_types.append("duskflats")
    lane_types.append("pool" if kind == KIND_POOL else "target")
    # No "slew" (spec 1.7): the TARGET block centres. Every stage after it is
    # in its panel lane, which is what a mosaic's loop needs.
    lane_types.append("autofocus")
    guided = _guided(kind, opts)
    if guided:
        lane_types.append("guide")
    lane_types += ["cycle" if cycle_plan else "capture", "report"]

    lane = [canvas.add(t, _LANE_X0 + i * _LANE_DX, _LANE_Y)
            for i, t in enumerate(lane_types)]
    for a, b in zip(lane, lane[1:]):
        canvas.chain(a, b)

    for node in lane:
        if node.type == "target":
            if tname:
                node.params["name"] = tname
            # THE COORDINATES ARE THE RUNNABLE PART. A name alone used to
            # leave the node's shipped M31 coordinates in place, so NEW FLOW
            # for M16 slewed to Andromeda and filed the frames as M16 (#190).
            # The node is created blank now, and a typed name is resolved
            # through the catalogue; one it cannot place stays blank, which
            # the run refuses rather than guessing.
            where = coords
            if where is None:
                where, why = (_resolve_name(tname) if tname
                              else (None, NO_TARGET_NOTE))
                if why is not None:
                    notes.append(why)
            if where is not None:
                node.params["ra"], node.params["dec"] = where
            if layout is not None:
                node.params.update(layout)
        elif node.type == "pool" and tname.find(",") > 0:
            # A LIST replaces the pool's candidates; a single name does not.
            # Transcribed — one name is not a pool, and overwriting four
            # defaults with it would leave "best available" nothing to choose
            # between. TODO(flows-handoff): the sheet accepts one name under
            # "Best of several" and then silently ignores it. Should the wizard
            # fall back to a plain TARGET when there is no comma?
            node.params["members"] = tname
        elif node.type == "capture":
            if kind == KIND_EAA:
                node.params.update(_EAA_CAPTURE)
            elif not guided:
                # DEVIATION 1 (doctor rule 2). The prototype leaves the 120s
                # default in place when Guiding is not picked, so the wizard's
                # own output opens with "120s subs with no GUIDE upstream —
                # stars will trail". Taking the doctor's second remedy, the one
                # that respects the answer given: shorten the subs.
                node.params["exposure"] = unguided_s
        elif node.type == "cycle":
            node.params["plan"] = cycle_plan
            node.params["cycles"] = max(1, int(cycles))
            # ONE SUB PER FILTER PER PASS. `cycles` is then literally "how many
            # subs of each filter", which is the number the operator typed.
            node.params["perCycle"] = 1

    if layout is not None:
        # THE CIRCLE THE OWNER ASKED FOR (spec 1.4): the wizard's mosaic is
        # one of the named moments the loop wire is added, so its panels
        # rotate every pass, and a night cut short leaves every panel
        # started rather than the last ones empty (doctor M3). Wired before
        # the rules row so its edge id sits with the lane's.
        _wire_the_loop(canvas, next(n for n in lane if n.type == "target"))

    # ------------------------------------------------------------ rules row
    rules_x = _RULES_X0

    def rule(node_type: str) -> FlowNode:
        nonlocal rules_x
        node = canvas.add(node_type, rules_x, _RULES_Y)
        rules_x += _RULES_DX
        return node

    # EITHER capture stage. `cycle` and `capture` are siblings, not a loop and
    # its body (nodes.py says so at the type), and both expose `frame` -- so the
    # watchdog wires to whichever one this lane carries.
    capture = next(n for n in lane if n.type in ("capture", "cycle"))
    cloudwatch: FlowNode | None = None
    condition: FlowNode | None = None
    safety: FlowNode | None = None

    if OPT_CLOUD_DODGE in opts:
        # The full cloud-dodge choreography: clouds in holds the light loop AND
        # starts the queue, clouds clear resumes AND stops it cleanly. Both legs
        # of both events, because a hold with no resume is a night that ends at
        # the first cloud.
        cloudwatch = rule("cloudwatch")
        hold = rule("holdresume")
        queue = rule("calib")
        canvas.wire(cloudwatch, "in", hold, "pause")
        canvas.wire(cloudwatch, "in", queue, "do")
        canvas.wire(cloudwatch, "clear", hold, "resume")
        canvas.wire(cloudwatch, "clear", queue, "stop")
        if OPT_DUSK_FLATS in opts:
            # Panel directly under its queue, so the one wire that is not in
            # the row reads as belonging to the node above it.
            panel = canvas.add("flatpanel", queue.x, _RULES_Y2)
            canvas.wire(panel, "ready", queue, "panel")
        else:
            # DEVIATION 2 (doctor rule 7). Without the Dusk flats chip there is
            # no panel to wire, and the queue's default "If stale + panel wired"
            # then reads as a request the graph cannot serve — "QUEUE wants
            # flats but nothing is wired to 'panel'". Saying Skip is the same
            # night, minus the false promise, and it is one of the two values
            # the queue's own Flats field offers.
            queue.params["flats"] = "Skip"

    if OPT_WATCHDOG in opts:
        condition = rule("condition")
        refocus = rule("refocus")
        canvas.wire(capture, "frame", condition, "events")
        canvas.wire(condition, "fire", refocus, "do")

    if OPT_DOME in opts or safety_abort:
        # DEVIATION 3 (doctor rule 9, the only DANGER-level check). A dome with
        # no safety monitor is "nothing closes the shutter on rain", and the
        # prototype only ever mints a monitor down in the notify branch — so
        # picking Dome without Notify generated a graph that opens a shutter
        # nothing will close. Built here, before notify, so the two branches
        # share one monitor instead of racing to create two.
        safety = _safety_pair(canvas, paired_with_cloudwatch=cloudwatch is not None)

    if OPT_NOTIFY in opts:
        notify = rule("notify")
        if condition is not None:
            # Wired to the watchdog by preference: "your focus drifted" is the
            # message worth a phone buzzing, and it is the prototype's order.
            canvas.wire(condition, "fire", notify, "do")
        elif cloudwatch is not None:
            canvas.wire(cloudwatch, "in", notify, "do")
        else:
            # Nothing else in this graph fires an event, so the phone would
            # never buzz. A safety monitor is the honest thing to hang it on.
            if safety is None:
                safety = _safety_pair(
                    canvas, paired_with_cloudwatch=cloudwatch is not None)
            canvas.wire(safety, "unsafe", notify, "do")

    return canvas.graph(), notes


def flow_name(kind: str = KIND_DEEP_SKY, target: str = "") -> str:
    """What the generated flow is called in the library."""
    kind, _ = _checked(kind, ())
    tname = (target or "").strip()
    name = ("EAA — " if kind == KIND_EAA else "") + (tname or _FALLBACK_NAME[kind])
    # Trimmed to what FlowRecord accepts. A pool of eight candidates typed as a
    # comma list passes 120 characters easily, and a ValidationError here would
    # throw away a graph that is otherwise perfectly good.
    return name[:120]


@dataclass(frozen=True)
class WizardAnswer:
    """What the wizard answers: the flow, and what it has to say about it.

    ``notes`` are sentences for the operator, in the order they arose: why a
    mosaic was answered as one target (``NO_OPTICS_REASON``), why a TARGET
    has no coordinates (a name the catalogue does not know, or no name). A
    route that drops them hands back a flow whose card and canvas do not say
    why it is not what was asked for."""
    record: FlowRecord
    notes: tuple[str, ...] = ()


def generate_answer(kind: str = KIND_DEEP_SKY,
                    options: Iterable[str] | None = None,
                    target: str = "",
                    unguided_exposure_s: float | None = None,
                    *,
                    rows: int | None = None,
                    cols: int | None = None,
                    overlap_pct: float | None = None,
                    angle_mode: str | None = None,
                    pa_deg: float | None = None,
                    use_measured: bool = False,
                    skip: str | None = None,
                    ra: str | None = None,
                    dec: str | None = None,
                    cycle_plan: str | None = None,
                    cycles: int | None = None,
                    guiding: bool | None = None,
                    rig: RigFacts | None = None,
                    measured_pa_deg: float | None = None,
                    wheel: Iterable[str] | None = None) -> WizardAnswer:
    """The generated flow as the library stores it, with the wizard's notes.

    The arguments are ``generate``'s (the three answers, the unguided sub
    length, the mosaic kind's answers and the two injected rig facts); this is
    the call a route makes when it will show the notes. Raises ValueError for
    an answer it cannot honour (an unknown kind or chip, a grid or angle with
    another kind, a mosaic grid out of bounds, a mosaic with no angle, "Rotate
    to PA" on a rig with no rotator), which a route answers 422.

    THE DOOR'S ANSWERS (S6, #196), each None when not given, and inert then:

    * ``ra`` and ``dec``: the TARGET's coordinates as typed
      (``_door_coords``). The name is not looked up behind them.
    * ``skip``: the Mosaic kind's skipped panels (``checked_skip``).
    * ``cycle_plan`` and ``cycles``: the filter rows, a FILTER CYCLE where
      the CAPTURE LOOP would be (``_door_rows``), checked against ``wheel``,
      a rig fact the route injects (the connected wheel's usable slots, None
      for no wheel, which is the assumed seven, as for ``quick``); on a lane
      that does not guide, held to the unguided sub cap
      (``_within_the_unguided_cap``).
    * ``guiding``: lights the Guiding chip, or confirms it is dark
      (``_door_guiding``).

    Each refusal names the answer it refuses.

    A mosaic answered as one target says so on its card too: the tagline is
    the library card's only line of prose, and a card reading "mosaic" over a
    single target would be the claim nothing keeps."""
    kind, opts = _checked(kind, options)
    opts = _door_guiding(kind, opts, guiding)
    coords = _door_coords(kind, target, ra, dec)
    table = _door_rows(kind, cycle_plan, cycles, wheel)
    if table is not None and not _guided(kind, opts):
        _within_the_unguided_cap(table[2], unguided_exposure_s)
    # No door answer, no change: "" and 1 are what ``generate`` defaults
    # the rows to, the inert values the three original answers always met.
    plan, passes = (table[0], table[1]) if table is not None else ("", 1)
    graph, notes = _generate(
        kind, opts, target, unguided_exposure_s, cycle_plan=plan,
        cycles=passes, coords=coords, safety_abort=False, rows=rows,
        cols=cols, overlap_pct=overlap_pct, angle_mode=angle_mode,
        pa_deg=pa_deg, use_measured=use_measured, skip=skip, rig=rig,
        measured_pa_deg=measured_pa_deg)
    tagline = f"Generated by the wizard — {kind.lower()}"
    if NO_OPTICS_REASON in notes:
        tagline += f", planned as one target: {NO_OPTICS_REASON}"
    return WizardAnswer(
        record=FlowRecord(name=flow_name(kind, target), folder=MY_FLOWS_FOLDER,
                          tagline=tagline[:400], graph=graph),
        notes=tuple(notes))


def generate_record(kind: str = KIND_DEEP_SKY,
                    options: Iterable[str] | None = None,
                    target: str = "",
                    unguided_exposure_s: float | None = None,
                    **answers_and_rig) -> FlowRecord:
    """The generated flow as the library stores it — graph, name and tagline.

    Lands in My flows, never in Examples: the Examples are read-only fixtures
    and a generated flow is the operator's, to edit from the moment it appears.

    ``generate_answer``'s record, with the same arguments: the four positional
    answers the route has always passed, and, keyword-only, ``rows``,
    ``cols``, ``overlap_pct``, ``angle_mode``, ``pa_deg``, ``use_measured``,
    the door's answers (``skip``, ``ra``, ``dec``, ``cycle_plan``,
    ``cycles``, ``guiding``) and the rig facts (``rig``, ``measured_pa_deg``,
    ``wheel``). A caller with only the three original answers gets exactly
    the graph those answers have always made, less SLEW + CENTER (spec 1.7)
    and with created params. A route that must say why a mosaic came back as
    one target calls ``generate_answer`` instead, for the notes; the record's
    tagline says it either way.
    """
    return generate_answer(kind, options, target, unguided_exposure_s,
                           **answers_and_rig).record


# ============================================================== the quick flow
#
# ONE SCREEN, FOUR ANSWERS: which target, how many subs of each filter, which
# filters, and whether the guider runs. Everything else is the shape the rig has
# actually shot - the same lane `generate()` draws for a guided deep-sky night,
# with a FILTER CYCLE where the CAPTURE LOOP would be, the relative HFR watchdog
# wired to it, and a safety monitor that aborts and parks.
#
# It is a WRAPPER over `generate()`, not a second generator. See that function's
# docstring for why: the four keyword-only extras it takes exist for this caller
# and for no other, precisely so the deep-sky lane has one definition.

#: A broadband slot's default sub length, and a narrowband one's.
#:
#: Not a recommendation, the same way ``UNGUIDED_EXPOSURE_DEFAULT`` is not: they
#: are the numbers the FILTER CYCLE node itself ships with (nodes.py's default
#: plan reads "L 60, ... Ha 180, ...") so a quick flow opens on the values an
#: operator would have found in the editor, and every one of them is a field on
#: the sheet that generated it.
QUICK_BROADBAND_EXPOSURE_S = 60
QUICK_NARROWBAND_EXPOSURE_S = 180

#: Narrowband spellings, matched case-insensitively BY PREFIX.
#:
#: MIRRORS ``defaultExposureFor`` in ui/src/components/flows/cyclePlanRows.ts,
#: which is where the browser pre-fills the same boxes. Two copies, and they are
#: allowed to be two copies for one reason: this one is a FALLBACK. The sheet
#: sends the exposures it displayed, so the server's table is consulted only by a
#: caller that omitted them entirely - an API client, or a test. If they ever
#: disagree the operator still gets what they saw on screen.
_NARROWBAND_PREFIXES = ("ha", "h-a", "halpha", "h-alpha", "oiii", "o3",
                        "sii", "s2", "nb")
#: Single letters that mean narrowband, matched EXACTLY. This rig's own wheel
#: names its SII slot `S`; no broadband filter is called S, H or O, but plenty
#: start with those letters (`Sloan g`), so a prefix match would be wrong.
_NARROWBAND_LETTERS = ("s", "h", "o")


def default_exposure_s(filter_name: str) -> int:
    """Seconds for a slot nobody gave an exposure for. 180 for narrowband, 60
    otherwise - 60 s of Ha is an empty frame."""
    f = (filter_name or "").strip().lower()
    if f in _NARROWBAND_LETTERS:
        return QUICK_NARROWBAND_EXPOSURE_S
    return (QUICK_NARROWBAND_EXPOSURE_S
            if any(f.startswith(p) for p in _NARROWBAND_PREFIXES)
            else QUICK_BROADBAND_EXPOSURE_S)


def fallback_wheel() -> list[str]:
    """The wheel to offer when no filter wheel is connected.

    READ OFF THE FILTER CYCLE NODE'S OWN DEFAULT PLAN rather than retyped, so
    there is exactly one list of assumed filters on the server and the editor's
    fresh cycle node and a quick flow cannot disagree about what a rig with no
    wheel is presumed to have. (The browser's ``FALLBACK_WHEEL`` is the same
    seven names for the same reason.)

    A fallback, never a merge: a connected wheel replaces this whole.
    """
    return [f for f, _ in parse_cycle_plan(NODE_DEFS["cycle"].params["plan"])]


def _unknown_filters(names: Iterable[str],
                     wheel: Iterable[str] | None) -> str | None:
    """The refusal for filter names this wheel does not have, or None.

    ONE RULE AND ONE SENTENCE for the quick flow's ticked filters
    (``cycle_plan_for``) and the wizard door's rows (``_door_rows``): ``wheel``
    is the connected wheel's usable slot names, and None means no wheel is
    connected, so the assumed list (``fallback_wheel``) applies. Matched
    exactly, case and all, for the reason ``cycle_plan_for`` gives."""
    order = list(wheel) if wheel is not None else fallback_wheel()
    unknown = sorted({f for f in names if f not in order})
    if not unknown:
        return None
    have = ", ".join(order) if order else "no filters"
    return f"unknown filter(s) {unknown}; this wheel has {have}"


def cycle_plan_for(filters: Iterable[str],
                   wheel: Iterable[str] | None = None,
                   exposures_s: dict[str, float] | None = None) -> str:
    """The FILTER CYCLE slot table for a set of ticked filters.

    ORDER IS WHEEL ORDER, not the order the client happened to send. The cycle
    shoots its table top to bottom each pass, and a table in click order would
    make two operators who ticked the same five filters get two different
    nights - and neither would match the carousel, so the wheel would take the
    long way round on most passes.

    A NAME THIS WHEEL DOES NOT HAVE IS A REFUSAL. cyclePlanRows.ts opens on why:
    a filter name is not a label, it lands in the FITS ``FILTER`` header, in the
    filename and in the calibration matcher's key. Typing ``OIII`` at a wheel
    whose slot reads ``Oiii`` asks for a filter that does not exist, and the run
    finds out at 21:00 in the dark.
    """
    order = list(wheel) if wheel is not None else fallback_wheel()
    picked = [str(f).strip() for f in filters]
    picked = [f for f in picked if f]
    if not picked:
        raise ValueError("no filters selected; tick at least one")
    refusal = _unknown_filters(picked, order)
    if refusal is not None:
        raise ValueError(refusal)
    exp = {str(k): v for k, v in (exposures_s or {}).items()}
    wanted = set(picked)
    slots: list[str] = []
    for f in order:
        if f not in wanted:
            continue
        try:
            secs = int(round(float(exp[f])))
        except (KeyError, TypeError, ValueError):
            secs = default_exposure_s(f)
        if secs <= 0:
            # A zero-second slot is a run-time refusal (`to_plan._cycle_steps`)
            # on a graph that looks fine on the canvas. Fall back rather than
            # persist one.
            secs = default_exposure_s(f)
        slots.append(f"{f} {secs}")
    return ", ".join(slots)


def _quick_coords(target: dict) -> tuple[str, str]:
    """``(ra, dec)`` from the picker's target, or a refusal naming what is off.

    PARSED HERE, at the door, rather than left to the run. ``to_plan`` raises
    ``GraphNotRunnable`` on an unparseable RA - which is correct, but it arrives
    when the operator presses RUN, and by then the sheet that could have fixed
    it is closed. The same strings ``parse_ra``/``parse_dec`` accept are the
    ones the TARGET node stores, so this refuses exactly what the night would.
    """
    ra = str(target.get("ra") or "").strip()
    dec = str(target.get("dec") or "").strip()
    if not ra or not dec:
        raise ValueError("the target needs an RA and a Dec; pick it from the "
                         "catalog rather than typing a name alone")
    try:
        parse_ra(ra)
    except Exception as e:
        raise ValueError(f"cannot read the RA {ra!r}: {e}") from e
    try:
        parse_dec(dec)
    except Exception as e:
        raise ValueError(f"cannot read the Dec {dec!r}: {e}") from e
    return ra, dec


def _one_channel_exposure_s(exposures_s: dict[str, float] | None) -> int:
    """The sub length for a rig with no wheel.

    ``exposures_s`` there carries ONE entry whose key is a label ("OSC"), not a
    slot, because there is no slot - so the value is taken and the key is not
    matched against anything. Anything else falls to the broadband default.
    """
    values = list((exposures_s or {}).values())
    if len(values) == 1:
        try:
            secs = int(round(float(values[0])))
        except (TypeError, ValueError):
            secs = 0
        if secs > 0:
            return secs
    return QUICK_BROADBAND_EXPOSURE_S


def quick(target: dict,
          subs_per_filter: int = 10,
          filters: Iterable[str] | None = None,
          exposures_s: dict[str, float] | None = None,
          guided: bool = True,
          name: str | None = None,
          *,
          wheel: Iterable[str] | None = None) -> FlowRecord:
    """A whole night from four answers, as the library stores it.

    ``target`` is ``{name, ra, dec}`` - the same three strings the TARGET node
    holds, so the picker's row goes onto the canvas unchanged. ``filters`` are
    ticked slot names in any order; ``wheel`` is the rig's carousel, which
    decides both the order they end up in and which names are legal (None means
    the rig has no wheel connected and the assumed list applies).

    AN EMPTY ``filters`` IS THE ONE-CHANNEL RIG, not a mistake. A colour camera
    with no wheel has one channel, so it gets a CAPTURE LOOP with no filter name
    rather than a one-slot FILTER CYCLE: a cycle of one interleaves nothing, and
    the slot's name would be invented - and an invented filter name is what lands
    in the FITS header and in the calibration key.

    Returns a :class:`FlowRecord`, unsaved. The route persists it through the
    same ``_persist_flow`` every other write uses, so the four server-owned
    fields are re-derived here exactly as they are everywhere else.
    """
    tname = str(target.get("name") or "").strip()
    if not tname:
        raise ValueError("the target needs a name")
    coords = _quick_coords(target)
    try:
        subs = int(subs_per_filter)
    except (TypeError, ValueError):
        raise ValueError(f"subs per filter must be a whole number, "
                         f"not {subs_per_filter!r}") from None
    if subs < 1:
        raise ValueError("subs per filter must be at least 1")

    picked = [str(f).strip() for f in (filters or []) if str(f).strip()]
    # The two automation chips this shape is: the guider (when asked for) and
    # the relative HFR watchdog, which is "HFR above (x focus)" at 1.3 - the
    # CONDITION node's own defaults, so nothing here re-types a threshold.
    opts = [OPT_WATCHDOG] + ([OPT_GUIDING] if guided else [])

    if picked:
        plan = cycle_plan_for(picked, wheel, exposures_s)
        graph = generate(KIND_DEEP_SKY, opts, tname,
                         cycle_plan=plan, cycles=subs, coords=coords,
                         safety_abort=True)
        channels = ", ".join(f for f, _ in parse_cycle_plan(plan))
    else:
        graph = generate(KIND_DEEP_SKY, opts, tname,
                         coords=coords, safety_abort=True)
        one = _one_channel_exposure_s(exposures_s)
        cap = next(n for n in graph.nodes if n.type == "capture")
        # No filter NAME on a rig with no wheel: the frames are one channel and
        # writing a label into FILTER would file them under a slot that does not
        # exist. `count` is the sub total, since there is nothing to cycle.
        # `goal` GOES TO 0 with it. The cycle path has no integration target at
        # all (the node has no such param), so leaving the CAPTURE LOOP's shipped
        # 12-hour goal here would make the two halves of one feature promise
        # different things about when the night is finished.
        cap.params.update({"filter": "", "exposure": one, "count": subs,
                           "goal": 0})
        channels = ""

    total = subs * (len(picked) or 1)
    each = f"{subs} subs each of {channels}" if channels else f"{subs} subs"
    return FlowRecord(
        name=(name or f"Quick: {tname}")[:120],
        folder=MY_FLOWS_FOLDER,
        # The tagline is the library card's only line of prose, so it carries
        # what the operator would otherwise have to open the flow to learn.
        tagline=(f"{each} on {tname}: {total} frames, "
                 f"{'guided' if guided else 'unguided'}")[:400],
        graph=graph)
