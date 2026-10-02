# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A compiled flow, as something ``SequenceEngine`` can actually run.

``compile_plan`` produces the README's documented five-key dict - the shape the
PLAN tab renders verbatim and ``resolve_tonight`` reads. ``SequencePlan`` is a
different shape entirely. This module is the seam between them, and it exists as
its own module rather than inside either one because both ``/compile`` and
``/run`` need it and because reshaping ``compile_plan``'s output would take the
whole Tonight surface with it.

THE SECOND RETURN VALUE IS THE POINT
------------------------------------
``SequencePlan``, ``Target``, ``ExposureStep`` and ``Instruction`` set no
``model_config``, so pydantic's default ``extra="ignore"`` applies: **unknown
keys are dropped in silence, never rejected.** That turns most of the gap
between the two shapes invisible. Measured against the five shipped examples,
a naive hand-off produces a plan that validates *clean* and starts a run
immediately, in daylight, with no altitude gate, no dawn stop, no dome policy
and none of the cloud rules the operator drew. Green start, wrong night, no
error anywhere.

So every dropped thing is reported. The operator finds out that their cloud rule
is not running now, from a list on screen, instead of at 3 a.m. from a mount
that kept shooting through an overcast.

WHAT IS NOT DECIDED HERE
------------------------
Whether an unmapped item should BLOCK a run. This function is pure - it has no
devices, no config and no clock beyond the one passed in - and "is there
actually a roof over this telescope" is not a question it can answer. It
classifies and reports; the route decides. See ``blocking_reasons``.
"""
from __future__ import annotations

import copy
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Literal, Sequence
from uuid import uuid4

from pydantic import ValidationError

from ..catalog.coords import parse_dec, parse_ra
from ..sequence.models import ActionKind, SequencePlan, TriggerKind
from ..sequence.schedule import sun_window_needs_a_site
from . import identity, tonight
from .compile import lane_refusals
from .models import FlowGraph
from .nodes import NODE_DEFS
from .rig import RigFacts

#: The engine's real vocabularies, read off the ``Literal`` types rather than
#: retyped. A hand-copied list is a claim that silently stops being true the
#: day the enum is widened, which is the failure this module exists to surface.
LEGAL_TRIGGERS: frozenset[str] = frozenset(TriggerKind.__args__)
LEGAL_ACTIONS: frozenset[str] = frozenset(ActionKind.__args__)

#: Triggers that answer with a VERDICT, not a measurement - so a threshold means
#: nothing to them.
#:
#: `_eval_predicate` reads ``threshold`` for ``hfr_above`` and
#: ``guide_rms_above`` and for nothing else; these four return a boolean the
#: detector or the safety monitor already decided. Listed here so the loss is
#: reported at the one place a threshold is copied into the plan, rather than
#: being a fact about the evaluator that this module has to remember.
BOOLEAN_TRIGGERS: frozenset[str] = frozenset(
    {"on_clouds_in", "on_clouds_clear", "on_unsafe", "on_panel_ready"})

#: Flow node types that ``compile_plan`` reads. Everything else in a graph is
#: walked by ``flow_order`` and contributes nothing to the compiled dict, so its
#: parameters are inert - see :func:`inert_nodes`.
COMPILED_NODE_TYPES: frozenset[str] = frozenset(
    {"target", "pool", "capture", "dusk", "dome", "duskflats", "calib",
     "cycle"})

#: Flow-vocabulary action -> engine ActionKind, keyed by (node type, INPUT PORT).
#:
#: The port is in the key because a HOLD / RESUME node is two different actions
#: depending on which of its inputs a rule lands on, and the node type alone
#: cannot tell them apart. Mapping on type alone turned "stop on cloud, start
#: again when it clears" into "stop on cloud, stop again when it clears".
#:
#: `holdresume.pause` becomes `hold_for_clear` rather than `pause`, and that is
#: the substantive decision here rather than a rename. The engine's `pause()`
#: blocks the frame loop above the dawn boundary, the safety gate and the
#: dead-man ping, and a paused loop has no frame boundaries for a resume rule to
#: be evaluated at - so a hold built from pause/resume would sit through sunrise
#: with the watchdog silent, waiting for a rule that can never fire.
#: `hold_for_clear` is the self-releasing hold that keeps all three armed.
PORTED_ACTIONS: dict[tuple[str, str], str] = {
    ("holdresume", "pause"): "hold_for_clear",
    ("condition", "events"): "condition",     # the pass-through, dropped below
}

#: Rules whose destination port makes them REDUNDANT rather than unsupported -
#: the capability exists, it is simply not driven from here. Reported at `note`
#: weight rather than as a loss, because telling an operator their resume wire
#: "will not run" would be a lie: the run does resume, the hold does it itself.
REDUNDANT_PORTS: dict[tuple[str, str], str] = {
    ("holdresume", "resume"): (
        "the hold releases itself when the sky clears, so this wire is not "
        "needed - it is kept in the graph and does no harm"),
    # THE CAMPAIGN LOOP-BACK, and the engine has done this all along. SESSION
    # REPORT `done` -> POOL `advance` asks for one thing: when the active
    # target has the frames it wanted, hand out the next member. The scheduler
    # already does exactly that, from the frame ledger rather than from a rule
    # - `_target_complete` is true, it logs "already complete - skipping", and
    # the next candidate gets the night. Across nights too, because `_done`
    # seeds from `done_map()` on resume.
    #
    # So "the engine has no 'pool' action" was true about the enum and false
    # about the run, and it is the campaign's own loop-back wire - the one an
    # operator would look at first to decide whether a month-long flow works.
    # `test_a_campaign_advances_across_nights` is the evidence.
    ("pool", "advance"): (
        "the scheduler advances the pool itself: a target whose frames are all "
        "in the ledger is skipped and the next member gets the night, on this "
        "night and on every night after - so this wire is not needed, and it "
        "is kept in the graph and does no harm"),
    # THE NIGHT ENDS PARKED AND SHUT WITHOUT BEING ASKED. `plan_extras` sets
    # `park_when_done` for every flow-derived plan, and `_panel_off_safe` closes
    # the dust cover on every wind-down. So "the engine has no 'on_night_end'
    # trigger; the engine has no 'parkclose' action" was true about the enums
    # and wrong about the night - the same shape as the pool loop-back above.
    #
    # The ROOF is the one part that depends on something outside the graph, and
    # it has its own two reports: `automation.dome` says whether it will close,
    # and `blocking_reasons` refuses the run when it will not. Repeating that
    # here would give an operator two different sentences about one shutter.
    ("parkclose", "do"): (
        "the night already ends parked with the dust cover shut, whether or "
        "not this wire is here - every flow's plan carries park-when-done and "
        "the wind-down closes the cover - so this wire is not needed. Whether "
        "the ROOF closes is reported separately, under the dome"),
}

#: Rules the hold ALREADY HONOURS, keyed by (trigger, node type, input port).
#:
#: The engine has no ``calib`` action, so a CLOUD WATCH wired to a CALIBRATION
#: QUEUE was reported as "this rule will not run" at DANGER weight. Measured
#: against the shipped M16 example that sentence is false: the queue's quota
#: reaches the plan as ``cloud_hold_darks`` (see :func:`plan_extras`), and
#: ``_hold_for_clear`` spends the hold shooting darks matched to the step it
#: interrupted. The operator's wire is answered - by the hold rather than by a
#: rule.
#:
#: Keyed on the TRIGGER as well as the port, because the same CALIB node fed
#: from a different edge is a different promise, and the two are answered by
#: different lanes: ``on_clouds_in`` by the hold, ``on_shutdown_complete`` by
#: the wind-down's day-darks phase between the park and the warm. Collapsing
#: both onto "calib" would trade one wrong sentence for another - a rig with no
#: day-darks quota would be told its shutdown wire was covered by a cloud hold
#: that never ran.
#:
#: (This note used to end "``on_shutdown_complete -> calib.do`` genuinely does
#: not run". It does now. A comment that outlives the behaviour it describes is
#: the same defect this module exists to catch.)
#:
#: Guarded per entry by :data:`HONOURED_BY` at the call site: with a quota of 0
#: the lane takes nothing, and calling the wire redundant then would be the same
#: overclaim in reverse.
#: Which ``plan_extras`` key funds each honoured wire, so "already honoured" is
#: never claimed for a lane with nothing to spend. Keyed the same way as
#: :data:`HOLD_HONOURED`; a missing entry there means the guard reads 0 and the
#: wire falls through to the ordinary loss report, which is the safe direction.
HONOURED_BY: dict[tuple[str, str, str], str] = {
    ("on_clouds_in", "calib", "do"): "cloud_hold_darks",
    ("on_clouds_clear", "calib", "stop"): "cloud_hold_darks",
    ("on_shutdown_complete", "calib", "do"): "day_darks",
}

HOLD_HONOURED: dict[tuple[str, str, str], str] = {
    ("on_clouds_in", "calib", "do"): (
        "the cloud hold takes darks itself, matched to the step it interrupts "
        "and capped at what the library still needs, so the darks leg of this "
        "wire is already honoured. Its bias and flat legs are not - see the "
        "calibration-queue note above"),
    ("on_shutdown_complete", "calib", "do"): (
        "the wind-down takes these darks itself, in the window between the park "
        "and the warm ramp - the one moment the mount is stowed, the cover is "
        "shut and the sensor is still at setpoint. They are matched to the "
        "lights this plan actually shot and capped at what the library still "
        "needs, and only a night that ended normally takes them: an abort, an "
        "unsafe trip or a cooling skip goes straight to the warm"),
    ("on_clouds_clear", "calib", "stop"): (
        "the hold ends when the sky clears and its darks stop with it, so this "
        "wire is not needed - it is kept in the graph and does no harm"),
}

#: The ``schedule`` keys ``compile_plan`` emits are field-for-field identical
#: to ``Schedule``'s. They are simply at the wrong NESTING LEVEL:
#: ``SequencePlan`` has no schedule, ``Target`` does. ``start_time`` and
#: ``stop_time`` (backlog WP-09, #191) are the Clock-time choices' own field,
#: present in the compiled dict only when a DUSK WINDOW's Start or Stop is
#: "Clock time" (`compile._dusk_schedule`); left out of this tuple, they were
#: filtered out here before ever reaching the running Target, so a Clock
#: time chosen on the card compiled a `start_mode`/`stop_mode` of "time"
#: with no time to act on. ``twilight_deg`` (backlog WP-09, #191, the
#: per-target Sun-altitude fix) is present only for a sun-based Start;
#: left out of this tuple the same way, a DUSK WINDOW's Astro/Nautical/Civil
#: choice would compile its own angle and then lose it here, resolving
#: against the rig's one angle again regardless of what the card said.
SCHEDULE_KEYS = ("start_mode", "start_offset_min", "start_time",
                 "twilight_deg", "stop_mode", "min_altitude_deg", "stop_time")

#: How a refusal names the card those keys come from (#483): its own label,
#: read off the vocabulary so a renamed card is renamed here too.
DUSK_BLOCK = NODE_DEFS["dusk"].label

#: A pool member's constraints are also real ``Schedule`` fields.
POOL_SCHEDULE_KEYS = {"min_altitude_deg": "min_altitude_deg",
                      # The floor POLICY travels with the floor NUMBER. Carrying
                      # one without the other is how `onFloor` spent its whole
                      # life as a sentence in the Tonight story with no engine
                      # behind it.
                      "on_floor": "on_floor",
                      "min_moon_sep_deg": "min_moon_sep_deg",
                      "max_hour_angle_h": "max_hour_angle_h"}

#: ``doctor.Issue``'s vocabulary, all three members of it.
#:
#: ``note`` was missing here while two tables below documented themselves as
#: emitting it, which made their central claim unkeepable: the whole argument
#: for :data:`REDUNDANT_PORTS` and :data:`HOLD_HONOURED` is that calling those
#: wires losses would be a lie, and they were then reported at the same weight
#: as the losses, under a heading that reads NOT HONOURED BY A RUN. The doctor
#: has emitted ``note`` since it shipped and the editor already inks it dim, so
#: the level existed everywhere except the one module that needed it.
Level = Literal["warn", "danger", "note"]


def _note(key: str, detail: str, level: Level = "warn", *,
          carried: list[str] | None = None,
          ignored: list[str] | None = None,
          source: str | None = None) -> dict:
    """One reported loss, or - at ``note`` - one thing an operator drew that is
    answered by some other part of the engine.

    ``level`` reuses ``doctor.Issue``'s vocabulary so the editor has ONE
    severity scale - an operator should not have to learn that a doctor warning
    and an adapter warning mean different things.

    ``carried`` / ``ignored`` / ``source`` are the three OPTIONAL fields that
    turn a sentence into a reading. A blanket "the X node's settings do not
    reach the run" made ten amber rows out of a clean flow on 2026-09-11 and
    two of them were false; splitting the card into the half the plan honours,
    the half it does not, and where the real value lives is what makes the row
    checkable. They are omitted entirely when not supplied, so every entry that
    has nothing extra to say stays byte-identical to what it was.
    """
    out: dict = {"key": key, "detail": detail, "level": level}
    if carried is not None:
        out["carried"] = list(carried)
    if ignored is not None:
        out["ignored"] = list(ignored)
    if source is not None:
        out["source"] = source
    return out


# ------------------------------------------------- the card, split three ways

class _Vals(dict):
    """``format_map`` source that renders an absent param as nothing rather than
    raising. A graph arriving over the API may carry a node with a param this
    build does not know, and a KeyError inside a sentence about dropped settings
    would be the module reporting its own loss with a 500."""

    def __missing__(self, key: str) -> str:
        return ""


def _say(value: Any) -> str:
    """One param value the way the operator typed it.

    ``2.0`` back off a JSON round-trip is the 2 they entered, and a sentence
    that says "2.0 arcsec" about a field showing "2" is the small kind of wrong
    that makes a reader distrust the large kind.
    """
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _and_list(items: Sequence[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def _upper_first(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


@dataclass(frozen=True)
class SettingsNote:
    """What one node's card promises, split into the half the plan honours, the
    half it does not, and WHERE the half it does not actually comes from.

    ``carried`` and ``ignored`` are ``(param key, template)`` pairs rendered
    against the node's own params - so the row names the operator's numbers,
    not the shipped defaults - and an empty param key is a statement that does
    not hang off any one field. A pair whose param is unset is dropped, because
    a sentence about a field nobody filled in is noise.

    A key written ``param=value`` fires only when that param holds that value,
    which is how a two-state field can land on OPPOSITE sides of the split.
    ABORT + PARK's "Park mount: Yes" is carried - the wind-down parks - and its
    "Park mount: No" is not, because the wind-down parks anyway; one template
    could not say both without lying in one of the two directions.

    ``detail`` is a format string over ``{carried}``/``{ignored}`` (and their
    ``{Carried}``/``{Ignored}`` sentence-case forms), ``{source}``, and any
    param of the node by name.
    """
    carried: tuple[tuple[str, str], ...]
    ignored: tuple[tuple[str, str], ...]
    source: str
    detail: str

    def render(self, params: dict) -> tuple[str, list[str], list[str], str]:
        shown = {k: _say(v) for k, v in (params or {}).items()}
        carried = self._fill(self.carried, params, shown)
        ignored = self._fill(self.ignored, params, shown)
        fields = _Vals(shown)
        fields.update({"carried": _and_list(carried),
                       "Carried": _upper_first(_and_list(carried)),
                       "ignored": _and_list(ignored),
                       "Ignored": _upper_first(_and_list(ignored)),
                       "source": self.source})
        return self.detail.format_map(fields), carried, ignored, self.source

    @staticmethod
    def _fill(entries: tuple[tuple[str, str], ...], params: dict,
              shown: dict) -> list[str]:
        out: list[str] = []
        for key, template in entries:
            if not key:
                out.append(template)
                continue
            wanted = None
            if "=" in key:
                key, wanted = key.split("=", 1)
            if (params or {}).get(key) in (None, ""):
                continue
            if wanted is not None and shown.get(key, "").lower() != wanted.lower():
                continue
            out.append(template.format_map(_Vals({**shown,
                                                  "value": shown.get(key, "")})))
        return out


#: The class of node whose card is answered SOMEWHERE ELSE, keyed by node type.
#:
#: Every one of these used to get the blanket "the X node's settings do not
#: reach the run - the compiler does not carry them into the plan", at ``warn``.
#: On the rig's "NGC 7129 - LRGB+SHO cycle" that printed eight amber rows plus
#: two more from the tables below, so a graph with a clean structural check and
#: no issues read as ten warnings - and the operator read a working flow as
#: broken. Two of the ten sentences were also FALSE: the CONDITION node's
#: threshold IS carried into the plan (the run's rule holds it), and the REFOCUS
#: node's presence IS the refocus instruction.
#:
#: So they are ``note``, the level this module already uses for "you drew this
#: and it happens, just not from here" (REDUNDANT_PORTS, HOLD_HONOURED), and
#: each says which half is honoured, which half is not, and where the number the
#: run actually obeys is set. A genuine loss - a factor out of range, a trigger
#: the engine cannot detect, a port the compiler dropped - stays ``warn``, and
#: ``losses`` still holds the run for those until the operator accepts them.
NODE_SETTINGS: dict[str, SettingsNote] = {
    "safety": SettingsNote(
        carried=(("", "the unsafe watch itself: every run polls the rig's "
                      "safety monitor and acts on an unsafe reading"),),
        ignored=(("source", "the sensor choice {value}"),
                 ("watch", "what it watches ({value})"),
                 ("stale", "how a stale reading is read ({value})")),
        source="Settings > Safety",
        detail="The SAFETY MONITOR's watch is honoured: every run polls the "
               "rig's safety monitor and acts on an unsafe reading, with or "
               "without this node on the canvas. {Ignored} come from {source} "
               "instead."),
    # LEGACY SINCE S3 (spec 1.7): centring is part of the TARGET block, whose
    # `centerTol` and `centerTries` reach the run as each target's
    # `center_tolerance_arcmin` and `center_attempts` (#170). So "where the
    # real value lives" is the TARGET's CENTRING section now. This node's own
    # numbers never reached a run (the hub's 0.02 deg and 3 did), and they
    # are deliberately NOT carried onto the TARGET: `tol 0.5` would silently
    # tighten the centring of every flow saved with this card on it.
    "slew": SettingsNote(
        carried=(("", "the slew and the plate-solve centring themselves: every "
                      "target is centred before its first frame"),),
        ignored=(("tol", "the {value} arcmin tolerance"),
                 ("retries", "the {value} centring attempts"),
                 ("solver", "the solver name {value}")),
        source="the TARGET block's CENTRING settings (1.2 arcmin and 3 tries "
               "unless set there; the rig's configured solver)",
        detail="SLEW + CENTER is part of the TARGET block now: the run slews "
               "and plate-solve centres every target before its first frame, "
               "with or without this node on the canvas. {Ignored} never "
               "reached the run and are not carried; the tolerance and tries "
               "come from {source}. Delete this stage and set centring on the "
               "TARGET."),
    "autofocus": SettingsNote(
        carried=(("", "the autofocus itself: the run focuses at each target's "
                      "start, and again whenever a rule or the temperature "
                      "asks"),),
        ignored=(("method", "the {value} method"),
                 ("step", "the {value}-step size"),
                 ("samples", "the {value} samples")),
        source="the focuser's own measured sweep geometry",
        detail="AUTOFOCUS runs at every target's start, with or without this "
               "node on the canvas. {Ignored} come from {source} instead - the "
               "sweep sizes its step from the defocus slope this focuser has "
               "measured, not from a number on the card."),
    # #239 stage C: this node's PRESENCE decides whether the run guides at all,
    # so the blanket "does not reach the run" line was a lie in the other
    # direction, and the settle/dither/provider half is the part that is true.
    "guide": SettingsNote(
        carried=(("", "presence: the night guides"),),
        ignored=(("settle", "settle below {value} arcsec"),
                 ("dither", "dither every {value} frames"),
                 ("provider", "the provider name {value}")),
        source="Rig > Guider",
        detail="The GUIDE stage decides that the night guides, which the run "
               "honours. {Ignored} come from {source} instead."),
    "report": SettingsNote(
        carried=(("", "the session report itself: every run writes one"),),
        ignored=(("format", "the {value} format"),
                 ("dest", "the destination {value}")),
        source="captures/reports, where the engine files every run's report "
               "in its own format",
        detail="SESSION REPORT is written for every run, with or without this "
               "node on the canvas. {Ignored} do not reach the plan - the "
               "report lands in {source}."),
    # THE FIRST OF THE TWO FALSE SENTENCES. The threshold reaches the plan -
    # `_instructions` copies it onto the rule, and the compiled plan for the
    # rig's own flow shows `on_hfr_above` carrying it. Only the window and the
    # once-per-run setting are dropped.
    "condition": SettingsNote(
        carried=(("when", "the {value} test, which becomes the run's rule"),
                 ("threshold", "its threshold of {value}")),
        ignored=(("window", "its {value} window"),
                 ("once", "its 'fire {value}' setting")),
        source="the engine's own rule evaluation, at every frame boundary",
        detail="The CONDITION's {when} test and its threshold of {threshold} "
               "both reach the run - the plan carries them as the rule the "
               "engine evaluates. {Ignored} do not, and the engine checks the "
               "rule at every frame boundary and fires it every time it holds."),
    # THE SECOND FALSE SENTENCE. A rule wired to this node compiles to the
    # plan's `refocus` action, so the node's presence IS the refocus.
    "refocus": SettingsNote(
        carried=(("", "presence: a rule wired here reaches the plan as the "
                      "run's refocus action"),),
        ignored=(("boundary", "its '{value}' setting"),),
        source="the engine, which runs every rule at a frame boundary anyway",
        detail="The REFOCUS node IS the refocus: a rule wired to it reaches "
               "the plan as the run's refocus action. {Ignored} does not reach "
               "the plan, and it does not need to: the engine runs every rule "
               "at a frame boundary anyway."),
    # `warm` is honest about its condition: the abort wind-down parks
    # unconditionally and warms only when `safety.on_unsafe` is
    # "abort_park_warm" (engine.py's SafetyAbort arm).
    "abort": SettingsNote(
        carried=(("park=Yes", "'Park mount: Yes' - the abort wind-down parks "
                           "the mount"),
                 ("warm=Yes", "'Warm camera: Yes' - it warms the camera when "
                              "Settings > Safety's unsafe action is park and "
                              "warm")),
        ignored=(("park=No", "'Park mount: No' - the wind-down parks anyway"),
                 ("warm=No", "'Warm camera: No' - the wind-down still warms "
                             "when Settings > Safety asks it to"),
                 ("message", "its reason text '{value}'")),
        source="the engine's default wording",
        detail="ABORT + PARK is what the engine already does on an abort: the "
               "wind-down parks the mount, and warms the camera when Settings "
               "> Safety's unsafe action asks for it. {Ignored} is not carried, "
               "so an alert this flow raises uses {source}."),
}


class GraphNotRunnable(ValueError):
    """The graph cannot become a plan at all - an operator error, not a bug.

    Distinct from an unmapped item: unmapped means "this ran without that",
    while this means "there is nothing here to run". The route maps it to a 422
    with ``code="invalid_graph"`` so the editor can say which node is at fault
    rather than showing a server error.
    """

    def __init__(self, message: str, code: str = "invalid_graph"):
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------- pieces

def _pool_overrides(entry: dict) -> dict:
    """The ``Schedule`` fields one entry writes itself: a POOL member's
    constraints (``POOL_SCHEDULE_KEYS``), each one the entry carries.

    ONE RULE, TWO READERS: ``_target_schedule`` merges these over the DUSK
    WINDOW's block, and ``to_sequence_plan``'s refusal names the DUSK WINDOW
    for exactly the fields left to it (#483), so the card a refusal sends
    the operator to is the card whose value the plan holds."""
    return {dst: entry[src] for src, dst in POOL_SCHEDULE_KEYS.items()
            if entry.get(src) is not None}


def _dusk_fields(base: dict, entry: dict) -> frozenset[str]:
    """The fields of one target's ``schedule`` the DUSK WINDOW wrote: its
    block's (``base``), less any the entry wrote over (``_pool_overrides``).
    A POOL member's floor is always its POOL's, whatever the night's is."""
    return frozenset(base) - frozenset(_pool_overrides(entry))


def _target_schedule(base: dict, entry: dict, *, is_pool: bool) -> dict:
    """The ``Schedule`` block for one target.

    Two sources merge here. The DUSK WINDOW node's block applies to the whole
    night; a POOL member's constraints apply to that member. WHERE THEY
    DISAGREE THE POOL WINS, because it is the more specific statement - an
    operator who set a 30 degree floor on the night and 40 on one candidate
    meant 40 for that candidate.
    """
    sched = {**base, **_pool_overrides(entry)}
    if is_pool:
        # The closest honest approximation of "best of several" the existing
        # model can express. Under the default "wait", member 1 blocks the whole
        # night waiting for a window it may never get, and members 2-4 - the
        # entire point of a pool - never run at all. Under "skip" each member is
        # attempted and passed over when its window is missed.
        sched["on_missed"] = "skip"
    return sched


def _coords(entry: dict, when: float | None
            ) -> tuple[float, float, str | None] | None:
    """``(ra_hours, dec_deg, canonical)`` for one compiled target entry, or
    ``None``.

    A plain TARGET carries sexagesimal text, and ``canonical`` is None: its
    field is what was typed. A POOL member, or a TARGET with only a name,
    is resolved against the shipped catalogue through
    ``tonight.resolve_target``, and ``canonical`` is the catalogue's
    canonical identity for the name, which ``_identify`` keys a TARGET on
    (#229). One call answers both, so the coordinates the run points at and
    the identity its ids carry are the same row. The row is chosen at
    ``tonight.IDENTITY_WHEN``, the instant ``progress._single`` and ADOPT
    choose it at too, and placed at ``when``, the compile's (#249): asked
    at ``when`` alone, a name whose best rank a body shares with a fixed row
    could key two nights' compiles on two objects.

    NEVER INVENTS (0, 0). ``Target`` accepts it happily and ``calibration``
    defaults to False, so the engine would slew there - and 0h/0deg is below
    the horizon at most sites, which means the operator gets a horizon refusal
    naming a target they never entered.

    "Typed" is ``identity.typed_coordinates``, the same test ``_identify``
    keys by: an entry resolved by name here is keyed on its name's identity
    there (#189 A5), and the two must never read one entry differently. A
    field of only whitespace is not typed (#387), so it is placed by name.

    A TYPED POSITION OFF THE SPHERE IS NO POSITION (#362): an RA or Dec that
    is not a finite number ("inf" and "nan" parse, through ``float``), or a
    Dec past a pole, drops the entry as text that does not parse does. That
    is ``save_rules.current_anchor``'s rule for "no layout", whose docstring
    says this function drops such a block, and it keeps an infinity out of
    the identity key and the mosaic's layout. A finite RA outside 0 to 24 h
    is not dropped here: it is refused by name where the plan checks it
    (``_refused_values``).
    """
    if identity.typed_coordinates(entry):
        try:
            ra, dec = parse_ra(str(entry["ra"])), parse_dec(str(entry["dec"]))
        except (TypeError, ValueError):
            return None
        if not (math.isfinite(ra) and math.isfinite(dec)
                and -90.0 <= dec <= 90.0):
            return None
        return ra, dec, None
    name = str(entry.get("name") or "").strip()
    # Looked up on the module at call time, never bound here by name, so a
    # test that replaces `tonight.resolve_target` replaces it for this and
    # for `progress._single` at once: two bindings of one resolver could let
    # a test pass with the two sides reading different catalogues.
    hit = tonight.resolve_target(name, when) if name else None
    return None if hit is None else (hit.ra_hours, hit.dec_deg, hit.identity)


def _quota_cycles(entry: dict) -> int | None:
    """A pool member's ``quota_cycles`` as a whole, positive cycle count, or
    None when there is none to honour (#155).

    ``compile_plan`` writes ``quota_cycles`` onto every pool member from the
    POOL's own ``quota`` dial - "how many cycles this member owes before it
    counts as done" - read finite-only (``compile._finite``), so a missing or
    unreadable quota already arrives here as 0. An ordinary TARGET entry
    carries no such key at all, and ``dict.get`` hands back None for it,
    which is also this function's answer: nothing to override, and every
    flow with no pool stays exactly as it compiled.

    ZERO AND NEGATIVE ARE REFUSED, NOT CLAMPED (the #362 class): a quota of 0
    is not "zero cycles are enough", it is "no quota was ever read", and
    treating it as a count would make a 0 or an unreadable dial end a
    campaign's member before its first visit - the opposite of #155's
    complaint that the dial does nothing. The FILTER CYCLE's own count keeps
    governing a member whose quota cannot be read, exactly as it does for a
    flow with no pool at all."""
    value = entry.get("quota_cycles")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return int(value)


def _cycle_steps(step: dict, target_name: str, index: int,
                 quota_cycles: int | None = None) -> list[dict]:
    """Expand one compiled FILTER CYCLE object into engine ``ExposureStep``s.

    The compile keeps the stage whole (``{strategy: "cycle", slots: […]}``)
    because that is what the operator drew. ``SequencePlan`` has no such shape:
    it has a flat list of steps and a per-TARGET ``acquisition`` mode. So the
    slot table becomes one step per filter, each carrying

        count     = cycles x per_cycle     (what the night owes for that filter)
        per_visit = per_cycle              (what one pass takes before moving on)

    and the target is marked ``acquisition="cycle"``, which is the field the
    engine's round-robin driver branches on. Both halves are needed: per_visit
    alone would sit inert on a target the engine still walks block-by-block.

    ``quota_cycles``, WHEN GIVEN, REPLACES THE STAGE'S OWN ``cycles`` (#155).
    A POOL member's finish line is the POOL's own dial - "how many cycles
    this member owes" is what `advance` compares against - not the FILTER
    CYCLE node's count, which is one template every member of the pool
    shares. Before this the two were two separate numbers nothing reconciled:
    the engine ran to the stage's own count while the Campaign tab (
    ``tonight._campaign``) judged members against the pool's quota by name,
    so the dial that looked like "how much this candidate gets" changed what
    the tab claimed and nothing about when a night actually moved on. Mapping
    the pool's number onto the step the engine runs makes the two the same
    question asked once: ``tonight._campaign`` reads the POOL's ``quota``
    straight off the node, unchanged, and now agrees with the engine by
    construction, because this is the one place that number ever reaches a
    run.

    AN EMPTY SLOT TABLE IS A REFUSAL, not an empty list. A cycle stage that
    compiles to nothing would let a graph carrying a visibly-configured capture
    node produce a plan with no frames, and the run would report "complete"
    having shot none.
    """
    slots = step.get("slots") or []
    if not slots:
        raise GraphNotRunnable(
            f"{target_name}: the FILTER CYCLE has no filters selected - "
            f"tick at least one row of the cycle plan")
    cycles = (quota_cycles if quota_cycles is not None
             else max(1, int(step.get("cycles") or 1)))
    per_cycle = max(1, int(step.get("per_cycle") or 1))
    out: list[dict] = []
    for slot in slots:
        exposure = slot.get("exposure_s")
        if not exposure or float(exposure) <= 0:
            raise GraphNotRunnable(
                f"{target_name}: cycle slot {slot.get('filter')!r} has no "
                f"exposure time - set one on the cycle plan row")
        out.append({
            "filter": slot.get("filter"),
            "exposure_s": exposure,
            "gain": step.get("gain"),
            "binning": step.get("binning", 1),
            "count": cycles * per_cycle,
            "per_visit": per_cycle,
            "frame_type": step.get("frame_type", "Light"),
            # The STAGE's node id, on every slot it expands to; `_identify`
            # keys the step ids on it and then removes it.
            "node_id": step.get("node_id"),
        })
    return out


def _steps(entry: dict, target_name: str, out: list[dict]) -> list[dict]:
    # One answer per ENTRY (#155), read once: a pool member's quota governs
    # every FILTER CYCLE stage it owns, not just the first.
    quota_cycles = _quota_cycles(entry)
    steps: list[dict] = []
    for i, step in enumerate(entry.get("steps") or []):
        if step.get("strategy") == "cycle":
            steps.extend(_cycle_steps(step, target_name, i, quota_cycles))
            continue
        exposure = step.get("exposure_s")
        count = step.get("count")
        # gt=0 on both. _num() returns 0 for a missing or garbled node param, so
        # this is the shape a half-filled CAPTURE node arrives in - a graph
        # error the editor can point at, not a 500 and not a silent zero-frame
        # step that reports "complete" having shot nothing.
        if not exposure or float(exposure) <= 0:
            raise GraphNotRunnable(
                f"{target_name}: a CAPTURE step has no exposure time - "
                f"set one on the capture node")
        if not count or int(count) <= 0:
            raise GraphNotRunnable(
                f"{target_name}: a CAPTURE step has a frame count of "
                f"{count!r} - set how many frames to take")
        clean = {k: v for k, v in step.items() if k != "integration_goal_h"}
        goal = step.get("integration_goal_h")
        if goal:
            # The Tonight panel's "banked vs goal" bar draws THIS, and the run
            # does not enforce it - nothing in SequencePlan expresses an
            # integration goal, and mapping it onto `count` would silently
            # change the frame count the operator typed.
            out.append(_note(
                f"targets[{target_name}].steps[{i}].integration_goal_h",
                f"the {goal} h integration goal is a Tonight budget, not a run "
                f"quota - this run stops at {count} frames regardless"))
        steps.append(clean)
    return steps


def _identify(target: dict, entry: dict, *, flow_id: str, is_pool: bool,
              members_seen: dict[str, set[str]],
              canonical: str | None = None, key: str | None = None,
              tid: str | None = None) -> None:
    """Give ``target`` and its steps deterministic ids, in place (spec 3.3).

    ``canonical`` is the catalogue's canonical identity for the entry's name,
    as ``_coords`` resolved it, or None for typed coordinates. A single
    TARGET with only a name is keyed on it (#229); a POOL member keeps its
    typed-name key, which the ruling left alone.

    ``key`` is the block's key when the caller has made it (``_block_key``:
    the anchor first, then the current geometry), and ``tid`` a mosaic
    panel's id, ``identity.target_id(group, row, col)``, which the caller
    minted with its group's; a panel's steps are keyed on it here like any
    target's.

    Every step arrives carrying its stage's ``node_id`` (the compile put it
    there); it is taken off here whatever happens, because it is a compile
    fact and not an ``ExposureStep`` field.

    NOTHING TO KEY ON, NOTHING KEYED. With no ``flow_id`` (an unsaved preview,
    a graph-less caller) or an entry with no ``node_id`` (a compiled dict built
    by hand, or by a caller older than S1), the ids are left unset and
    ``SequencePlan`` mints uuid4s exactly as it always has. Keying such an
    entry on "" instead would give two id-less entries on one field one id,
    and ``plan_identity_errors`` would refuse a run that used to start.

    ``members_seen`` holds, per POOL node, the member keys already issued. A
    repeated name takes the next free occurrence suffix, checked against the
    keys issued rather than counted, so even a member literally named "M31#1"
    beside two M31s cannot collide with the second copy's suffix."""
    stages = [str(s.pop("node_id", None) or "") for s in target["steps"]]
    node_id = str(entry.get("node_id") or "")
    if tid is None and (not flow_id or not node_id):
        return
    if tid is None and is_pool:
        used = members_seen.setdefault(node_id, set())
        occurrence = 0
        while identity.member_key(target["name"], occurrence) in used:
            occurrence += 1
        used.add(identity.member_key(target["name"], occurrence))
        tid = identity.member_id(flow_id, node_id, target["name"], occurrence)
    elif tid is None:
        # A single TARGET is its block's 1x1 grid. Since S3 it is keyed on its
        # ANCHOR when it has one (`_block_key`), so a nudge the save carried
        # keeps the counts; with none, on the geometry it is at NOW. A TARGET
        # with only a NAME is keyed on the catalogue's canonical identity for
        # it instead: its geometry is the catalogue's answer at `when`, which
        # moves (#189 A5), and the name as typed is one spelling of many
        # (#229). `target_key` decides which, and `progress._single` asks it
        # the same question with the same resolver's answer.
        if key is None:
            key = identity.target_key(entry, target["ra_hours"],
                                      target["dec_deg"],
                                      target["rotation_deg"],
                                      canonical=canonical)
        tid = identity.target_id(identity.group_id(flow_id, node_id, key), 0, 0)
    target["id"] = tid
    seen: Counter[tuple[str, str]] = Counter()
    for stage, step in zip(stages, target["steps"]):
        recipe = dict(frame_type=step.get("frame_type", "Light"),
                      filter=step.get("filter"),
                      exposure_s=step.get("exposure_s"),
                      gain=step.get("gain"), binning=step.get("binning", 1))
        signature = identity.step_signature(**recipe)
        n = seen[(stage, signature)]
        seen[(stage, signature)] += 1
        step["id"] = identity.step_id(tid, stage, **recipe, n=n)


def _instructions(compiled: dict, out: list[dict]) -> list[dict]:
    """Compiled rules, as the subset the engine can actually evaluate.

    Every compiled rule fails validation twice as-emitted: the compiler's
    ``when`` is a STRING and is semantically the model's ``trigger``, while the
    model's own ``when`` is a bounded compound predicate. The names collide with
    opposite meanings, so this is a rename, not a coincidence.
    """
    rules: list[dict] = []
    # What the hold will actually spend on darks, from the SAME function the
    # plan is built with. Recomputing the rule here would be a second copy of
    # the quota policy, free to drift from the one the engine obeys.
    # From the SAME function the plan is built with. Recomputing either quota
    # here would be a second copy of the policy, free to drift from the one the
    # engine obeys - and each note is gated on ITS OWN key, because a wire is
    # only "already honoured" if the lane that honours it has frames to spend.
    # The two came from one quota and would usually agree, which is exactly the
    # kind of coincidence that stops being true later.
    extras = plan_extras(compiled)
    for rule in compiled.get("instructions") or []:
        trigger = str(rule.get("when") or "")
        raw = str(rule.get("action") or "")
        port = str(rule.get("to_port") or "")
        if (raw, port) in REDUNDANT_PORTS:
            out.append(_note(f"instructions[{trigger} -> {raw}.{port}]",
                             REDUNDANT_PORTS[(raw, port)], "note"))
            continue
        honoured = HOLD_HONOURED.get((trigger, raw, port))
        funded = int(extras.get(HONOURED_BY.get((trigger, raw, port), "")) or 0)
        if honoured and funded > 0:
            out.append(_note(f"instructions[{trigger} -> {raw}.{port}]",
                             honoured, "note"))
            continue
        action = PORTED_ACTIONS.get((raw, port), raw)
        if action == "condition":
            # NOT a lost capability. The CONDITION node is a pass-through: its
            # inbound edge compiles to this row and its outbound edges compile
            # to the real rules, which are already present. Reporting it would
            # train operators to ignore the list.
            continue
        bad = []
        if trigger not in LEGAL_TRIGGERS:
            bad.append(f"the engine has no {trigger!r} trigger")
        if action not in LEGAL_ACTIONS:
            bad.append(f"the engine has no {action!r} action")
        if bad:
            # Weather and safety rules are the ones whose absence has physical
            # consequences, so they are called out louder than a lost notify.
            danger = trigger in ("on_clouds_in", "on_clouds_clear", "on_unsafe")
            out.append(_note(
                f"instructions[{trigger} -> {action}]",
                f"this rule will not run: {'; '.join(bad)}. "
                f"It stays in the graph and in the compiled plan, and starts "
                f"working the day the engine learns the trigger",
                "danger" if danger else "warn"))
            continue
        legal = {"trigger": trigger, "action": action}
        if rule.get("threshold") is not None:
            legal["threshold"] = rule["threshold"]
            if trigger == "on_hfr_above" and rule.get("relative"):
                # GN-08: the CONDITION node's "HFR above (x focus)" form. The
                # SAME 1.0 < factor <= 5.0 bound `Instruction` enforces at the
                # model layer is checked here first - a rule that failed it
                # would otherwise reach `SequencePlan.model_validate` and turn
                # a bad canvas value into an unhandled ValidationError at
                # /run, instead of a note on the PLAN tab like every other
                # unmappable thing in this function.
                factor = float(rule["threshold"])
                if not (1.0 < factor <= 5.0):
                    out.append(_note(
                        f"instructions[{trigger}].threshold",
                        f"this rule will not run: a relative HFR-above-focus "
                        f"factor must be above 1.0 (at or below fires on the "
                        f"baseline itself) and at most 5.0 - got {factor:g}x",
                        "warn"))
                    continue
                legal["relative"] = True
            if trigger in BOOLEAN_TRIGGERS:
                # A DIAL WIRED TO NOTHING. `_eval_predicate` reads `threshold`
                # for the MEASURED triggers (hfr_above, guide_rms_above) and
                # ignores it for these, which answer with a verdict the detector
                # already reached: `clouds_in` returns ctx.cloudy and nothing
                # else. The CLOUD WATCH node still offers a threshold, still
                # stores it, and still shows it back on the canvas.
                #
                # Reported rather than wired, deliberately. The node's dial is a
                # 0-100 number and the detector's answer is a boolean it reached
                # from bright-star density and contrast, calibrated against a
                # real cloudy frame. Inventing a mapping between them would
                # replace a validated decision with a guess, and the handoff's
                # rule is to report ambiguity rather than resolve it.
                key = f"instructions[{trigger}].threshold"
                # ONCE PER TRIGGER, not once per rule. A CLOUD WATCH node's
                # `in` port usually feeds several destinations - the hold AND
                # the notify, in the shipped example - and each compiles to its
                # own rule carrying the same dead dial. Printed per rule, the
                # operator sees the identical sentence twice and learns to skim
                # a list whose whole value is that every line is news.
                if not any(u["key"] == key for u in out):
                    out.append(_note(
                        key,
                        f"the {trigger.replace('on_', '').replace('_', ' ')} "
                        f"rule's threshold ({rule['threshold']:g}) does not "
                        f"reach the engine: this trigger fires on the "
                        f"detector's own verdict, so moving the dial changes "
                        f"nothing"))
        rules.append(legal)
    if rules:
        # A rule carries its destination node's TYPE and nothing else, so a
        # NOTIFY node's text, an ABORT node's reason and a CONDITION node's
        # `once` are gone before this function ever sees them.
        #
        # A NOTE, not a warning: what the rules DO - the trigger and the action,
        # which is the whole of what the operator wired - survives the compile
        # intact. Only the wording does not, and an alert that arrives in the
        # engine's own words is still an alert that arrives.
        out.append(_note(
            "instructions[*].message",
            "Every rule's trigger and action reach the plan, so the wires "
            "drawn on the canvas are the wires that run. The words are not "
            "carried: a NOTIFY node's message text and severity, an ABORT "
            "node's reason and a CONDITION node's 'fire once per run' are "
            "dropped, so an alert this flow raises uses the engine's default "
            "wording",
            "note",
            carried=[f"the trigger and the action of each of the "
                     f"{len(rules)} rule{'' if len(rules) == 1 else 's'} this "
                     f"flow compiles to"],
            ignored=["a NOTIFY node's message text and severity",
                     "an ABORT node's reason",
                     "a CONDITION node's 'fire once per run'"],
            source="the engine's default wording"))
    return rules


def _automation(compiled: dict, out: list[dict], *,
                closes_on_unsafe: bool = False) -> None:
    """Report the automation blocks, none of which ``SequencePlan`` can hold.

    These are NOT equivalent losses and are not reported as if they were.
    Losing ``dusk_flats`` means no flats. Losing ``calibration_queue`` means the
    library does not top up. Losing ``dome`` means a shutter the graph promised
    would close on unsafe does not exist at run time - and ``DomePolicy``'s own
    docstring is emphatic that the roof must never be talked out of shutting.
    That one is ``danger``, and :func:`blocking_reasons` picks it up.
    """
    # THE CAMPAIGN BLOCK REACHES MORE THAN THIS NOTE USED TO ADMIT, and
    # over-reporting a loss is the same defect as hiding one. It said the run
    # "images ONE night and stops at dawn", that "the capture cursor is not
    # persisted", that "no target is marked done" and that the flow "will not
    # re-arm at the next dusk". Three of those four are false, and had been
    # since the multi-night session machinery landed:
    #
    #   * a run that ends at its stop boundary leaves the session DORMANT with
    #     `auto_resume` set (engine.start arms it unconditionally), and
    #     `ResumeArm.tick` restarts it the next time the window opens;
    #   * the cursor IS the frame ledger - `_done` seeds from `done_map()` on
    #     resume, which is what makes night two shoot the remainder rather than
    #     the whole count again;
    #   * a target whose frames are all in the ledger is reported "already
    #     complete - skipping" by the scheduler, so the pool advances across
    #     nights without anything having to mark it.
    #
    # `test_window_dormant_then_resume_exact_remaining` and
    # `test_a_campaign_advances_across_nights` hold those three up.
    #
    # What is genuinely not honoured is the STOP CONDITION, and only for one of
    # its two forms - so that is what this says now.
    if compiled.get("campaign"):
        camp = compiled["campaign"]
        until = str(camp.get("until") or "")
        runs = ("It images until its window closes, goes dormant with "
                "auto-resume armed, and comes back the next night picking up "
                "from the frame ledger - targets already finished are skipped "
                "rather than reshot")
        if until == "nights_30":
            out.append(_note(
                "campaign",
                f"{runs}. Nothing counts NIGHTS, though: the campaign ends when "
                f"every target has the frames it asked for, however many nights "
                f"that takes, so 'until {until}' is not a limit the run "
                f"enforces"))
        else:
            out.append(_note(
                "campaign",
                f"{runs}. It ends when every target has the FRAME COUNT it asked "
                f"for - the integration-goal hours are a Tonight budget, not the "
                f"thing that closes the campaign"))

    auto = compiled.get("automation") or {}
    if "dome" in auto:
        # SPLIT ON WHAT THE RIG WILL ACTUALLY DO. This said "nothing will bind
        # the dome or close it on an unsafe reading" at DANGER, unconditionally.
        # The close half is false on any rig with `close_dome_on_unsafe` set
        # (the Remote preset sets it): the engine routes a connected dome
        # through `roof.close_observatory` on the unsafe teardown, so the node's
        # one non-negotiable promise is kept - by config rather than by the
        # node. Saying otherwise sends someone out to a roof that shut hours ago.
        #
        # What is genuinely lost either way is the node's own azimuth binding
        # and shutter timeout: `DomePolicy.apply_binding` and
        # `DomePolicy.from_plan` have no callers anywhere in the server.
        if closes_on_unsafe:
            out.append(_note(
                "automation.dome",
                "the roof WILL close on an unsafe reading - this rig's safety "
                "settings do that for every run, over a parked mount. What "
                "this node adds does not reach the engine: its azimuth binding "
                "and shutter timeout are dropped, so a dome that tracks the "
                "mount will not be told to"))
        else:
            out.append(_note(
                "automation.dome",
                "the dome policy compiled correctly but the engine cannot act "
                "on it yet, so nothing will bind the dome or close it on an "
                "unsafe reading during this run", "danger"))
    if "dusk_flats" in auto:
        out.append(_note("automation.dusk_flats",
                         "the dusk-flats stage is not wired into the engine "
                         "yet - this run will not take flats"))
    if "calibration_queue" in auto:
        # PARTIALLY honoured now. The darks half reaches the engine as
        # `cloud_hold_darks` (see plan_extras) - a hold spends its dead time
        # shooting darks matched to the step it interrupted. What does NOT
        # reach it is the ORDER, the if_stale policy, the bias half and the
        # flats-if-panel half, so this still reports rather than going quiet.
        out.append(_note(
            "automation.calibration_queue",
            "darks will be taken during a cloud hold, matched to the step it "
            "interrupts, and only up to what the library still needs at those "
            "settings. The queue's order, and its bias and flat legs, are not "
            "wired into the engine yet"))


#: Node types whose generic "settings do not reach the run" sentence is WRONG,
#: keyed by type, and which are still a genuine LOSS - so they keep ``warn``.
#: One entry, and it is here rather than inline because the next node to
#: half-work will want the same treatment.
#:
#: PARK + CLOSE claims four things and three of them happen - just not because
#: this node is on the canvas. Telling an operator that none of them do is the
#: same defect as telling them all of them do, and it is the more dangerous
#: direction: someone who believes the night does not park will go out and park
#: it themselves, or leave a run they would otherwise have trusted. The fourth -
#: "Hold cold (day darks)" - is not honoured at all, which is why this one stays
#: a warn while the :data:`NODE_SETTINGS` class became notes.
#:
#: (GUIDE used to live here. Its card is now split three ways by
#: ``NODE_SETTINGS["guide"]``, which says the same thing in the same words and
#: also names the settle, dither and provider values it is talking about.)
NODE_LOSS: dict[str, str] = {
    "parkclose": (
        "most of what this node promises already happens, but not because it "
        "is here: every flow's night ends with the mount parked and the dust "
        "cover shut whether or not this node is on the canvas, and the dome "
        "closes only if Settings > Safety has 'close dome when done' ticked. "
        "What does NOT happen because of THIS node is the cooler setting - the "
        "camera is warmed at the end of every run regardless of 'Hold cold "
        "(day darks)'. Darks after a shutdown come from a calibration step "
        "wired to on_shutdown_complete, not from this node's cooler setting"),
}


def inert_nodes(graph: FlowGraph | None) -> list[dict]:
    """Nodes whose parameters reach nothing, reported FROM THE GRAPH.

    They have to come from the graph because they leave no trace in the
    compiled dict: ``compile_plan`` branches on target/pool/capture and walks
    past the rest, so by the time a plan exists there is nothing left to notice
    a GUIDE node's settle time was discarded.

    This is the honest surface for a whole class of dead control. A SLEW node's
    tolerance, an AUTOFOCUS node's step size, a NOTIFY node's severity - all of
    them are editable, all of them look live, and none of them reaches the
    engine.

    THE PARK EXAMPLE THAT USED TO BE HERE IS NO LONGER TRUE, and leaving it
    would have made this docstring the thing it warns about. It said
    ``park_when_done`` stays False "so a flow that says park and warm leaves the
    mount tracking and the TEC cold". :func:`plan_extras` now sets it True for
    every flow-derived plan, unconditionally - so the night ends parked whether
    or not a PARK + CLOSE node is on the canvas, and the sentence describing the
    opposite outlived the behaviour it described.

    PARK + CLOSE therefore gets its own wording below rather than the generic
    one: three of the four things that node claims DO happen, and telling an
    operator none of them do is the same defect as telling them all of them do.
    """
    if graph is None:
        return []
    seen: set[str] = set()
    out: list[dict] = []
    for node in graph.nodes:
        if node.type in COMPILED_NODE_TYPES or node.type in seen:
            continue
        seen.add(node.type)
        if not node.params:
            continue
        settings = NODE_SETTINGS.get(node.type)
        if settings is not None:
            # A NOTE, not a warning: the run does the thing the card draws, and
            # the numbers it does it with are set somewhere the row now names.
            detail, carried, ignored, source = settings.render(node.params)
            out.append(_note(f"nodes.{node.type}", detail, "note",
                             carried=carried, ignored=ignored, source=source))
            continue
        out.append(_note(f"nodes.{node.type}",
                         NODE_LOSS.get(node.type) or (
                             f"the {node.type.upper()} node's settings do not "
                             f"reach the run - the compiler does not carry them "
                             f"into the plan")))
    out.extend(_inert_params(graph))
    return out


#: Params on an OTHERWISE-COMPILED node that still reach nothing. ``(type,
#: param) -> sentence``.
#:
#: The loop above cannot see these: it skips every type in
#: ``COMPILED_NODE_TYPES`` wholesale, on the reasoning that a compiled node's
#: params arrive. Mostly true, and for `reject` it is false - which made this
#: the one dropped setting with NOTHING anywhere saying so, while
#: `tonight.py`'s brief went on promising it by name and by number ("a sub is
#: graded and only counts below HFR 3.5in"). A silent loss under a list whose
#: whole job is that losses are not silent.
#:
#: WHY IT IS NOT SIMPLY WIRED UP INSTEAD. The nearest plan field,
#: ``hfr_reject_factor``, is a MULTIPLIER of the running median of accepted
#: frames (`engine.py`: `hfr > med * factor`, needing a 4-frame window). The
#: node's `reject` is presented everywhere as an absolute HFR in arcsec.
#: Feeding 3.5 into a field that means "3.5x the median" would be a different
#: rule wearing the same number, which is worse than not carrying it.
#:
#: A ``note`` for the same reason the :data:`NODE_SETTINGS` class is: the stage
#: itself reaches the run whole - its filters, exposures, gain and binning are
#: the night - and the one dial that does not is set on the rig instead. Ten
#: amber rows on a working flow is how an operator learns to stop reading them.
INERT_PARAMS: dict[tuple[str, str], SettingsNote] = {
    (t, "reject"): SettingsNote(
        carried=(("", f"the {t.upper()} stage itself: its filters, exposures, "
                      f"gain and binning all reach the plan"),),
        ignored=(("reject", "its reject-HFR-above {value} arcsec"),),
        source="Settings > Standards, plus the HFR factor under Settings > "
               "Safety",
        detail=f"The {t.upper()} stage reaches the run whole - filters, "
               f"exposures, gain and binning are all carried. {{Ignored}} is "
               f"not, because the plan's nearest field is a MULTIPLE of the "
               f"running median rather than an absolute HFR; frame grading "
               f"uses the rig's own standards instead ({{source}}).")
    for t in ("capture", "cycle")
}


def _inert_params(graph: FlowGraph) -> list[dict]:
    """Dropped params on nodes the compiler otherwise reads. See INERT_PARAMS.

    Only reported when the param is actually SET to something: a node left at a
    default the operator never looked at does not need a warning, and a list
    that fires on every flow is a list nobody reads.
    """
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for node in graph.nodes:
        for (ntype, param), settings in INERT_PARAMS.items():
            if node.type != ntype or (ntype, param) in seen:
                continue
            value = (node.params or {}).get(param)
            if value in (None, "", 0, 0.0):
                continue
            seen.add((ntype, param))
            detail, carried, ignored, source = settings.render(node.params)
            out.append(_note(f"nodes.{ntype}.{param}", detail, "note",
                             carried=carried, ignored=ignored, source=source))
    return out


# ---------------------------------------------------------------- the adapter

def plan_extras(compiled: dict) -> dict:
    """Plan-level fields the compiled automation blocks DO reach.

    Small and explicit rather than a general merge: every key here is one the
    engine acts on, and a merge would let a future automation block set a plan
    field nobody reviewed.
    """
    auto = compiled.get("automation") or {}
    cq = auto.get("calibration_queue") or {}
    # THE NIGHT ENDS PARKED AND WARM, ALWAYS.
    #
    # Not a policy invented here: the Tonight timeline has always closed with
    # "Dawn: loop ends, mount parks, camera warms", unconditionally, for every
    # flow. The plan just never carried it, so the preview promised a park that
    # `park_when_done=False` guaranteed would not happen - a claim nothing
    # keeps, told to the one operator who is asleep when it comes due.
    #
    # Unconditional because the flow vocabulary has no node for "deliberately
    # leave the mount tracking", and the safe reading of silence is the one that
    # does not point a tube at the ground through sunrise. The engine already
    # treats the opposite choice as something a run must SAY out loud.
    #
    # `test_the_preview_cannot_promise_what_the_plan_drops` binds the two
    # together, so the next edit to either has to move both.
    out: dict = {"park_when_done": True, "warm_cooler_when_done": True}
    # #195: DUSK WINDOW's "Single night" vs the other `repeat` choices,
    # carried through to `SequencePlan.resume_across_nights` - but ONLY when
    # `compile_plan` wrote it, which it does only for "Single night"
    # (compile.py keeps the key absent when True, the same convention as
    # `campaign`). Writing `True` here unconditionally would add a key to
    # EVERY plan this function has ever built, breaking every byte-identical
    # and exact-key-set fixture that pins `plan_extras` or a compiled plan's
    # shape; the model's own default (True) already covers every caller that
    # says nothing, which is every caller before this field existed.
    if compiled.get("resume_across_nights") is False:
        out["resume_across_nights"] = False
    quota = cq.get("quota")
    if isinstance(quota, (int, float)) and quota > 0:
        # The queue's quota is "how many of each kind the library wants". A hold
        # is bounded and cannot deliver a whole quota, so it is capped: the hold
        # tops the library up, it does not fill it.
        out["cloud_hold_darks"] = min(int(quota), 40)
        # THE DAY-DARKS LANE, and it is keyed on the OPERATOR'S OWN WIRE rather
        # than on the queue existing. A CALIBRATION QUEUE fed only from a CLOUD
        # WATCH is asking for hold darks; one fed from SHUTDOWN COMPLETE is
        # asking for day darks; a lot of flows want the first and not the
        # second, and holding a camera cold for an extra hour on a night nobody
        # asked for it is a real cost in power and TEC life.
        #
        # The compiled instruction is where that wire survives - `automation`
        # records the queue's settings but not who feeds it - so this reads the
        # rule the operator drew. Same cap as the hold: the lane tops the
        # library up, it does not fill it.
        if any(str(r.get("when") or "") == "on_shutdown_complete"
               and str(r.get("action") or "") == "calib"
               for r in (compiled.get("instructions") or [])):
            out["day_darks"] = min(int(quota), 40)
    return out


# ------------------------------------------------------ blocks and mosaics
#
# Spec 3.3, #189 (U-09), #170, #151. A TARGET entry with a grid becomes one
# Target per panel and one TargetGroup; a TARGET of one panel stays one plain
# Target, with its centring. The panels are laid out by
# `framing.compute_mosaic`, the one projection (spec 3.3: "there is no third
# copy"), so the run slews to exactly what the Atlas previewed.

#: A TARGET's `order` (spec 3.1) -> ``TargetGroup.order``. The operator's
#: words, stored verbatim and never reworded; a value this build does not
#: offer reads as the missing-key default, least complete first.
GROUP_ORDERS: dict[str, str] = {"Least complete first": "least_complete",
                                "Setting first": "setting_first",
                                "Grid order": "grid"}

#: The flow setting (spec 1.6) under which a mosaic's followers wait for it
#: (``Target.after_group``). The default lets them fill the gaps instead,
#: which the engine does from plan order with no field at all.
WAIT_FOR_THE_MOSAIC = "Wait for the mosaic"

#: ``Target``'s own bounds on the centring it carries (#170): above 0 and at
#: most 30 arcmin, 1 to 10 attempts. Checked here so a bad canvas number is a
#: refusal the editor can show, not a ValidationError out of /run.
CENTRE_TOL_MAX_ARCMIN = 30.0
CENTRE_ATTEMPTS_MAX = 10

#: ``TargetGroup``'s and ``MosaicSpecIn``'s bounds on what a block's grid
#: holds (spec 3.1): 1 to 10 panels a side, overlap 0 to 50 percent, 1 to 20
#: passes per visit, 0 to 180 minutes a visit.
GRID_MAX = 10
OVERLAP_MAX_PCT = 50.0
PASSES_MAX = 20
VISIT_MAX_MIN = 180.0


def _number(value) -> float | None:
    """``value`` as a finite float, or None for anything that is not one (a
    bool is not a number here: True is not an angle)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _whole(value) -> int | None:
    number = _number(value)
    return int(number) if number is not None and number.is_integer() else None


def _block_label(entry: dict) -> str:
    """How a sentence names the block an entry came from."""
    name = str(entry.get("name") or "").strip()
    if entry.get("pool_rank") is not None:
        return f"TARGET POOL member {name or '?'}"
    return f"TARGET {name}" if name else "an unnamed TARGET"


def _angles(entry: dict) -> tuple[str, float | None, float | None]:
    """``(angle, layout, commanded)`` for a compiled entry.

    ``layout`` is the angle the block is LAID OUT at, the one its panels and
    its identity are made from (``identity.anchor_for``: for "camera fixed
    at" the planned angle, never commanded, which still placed every
    panel). ``commanded`` is what the rotator is told, only for "rotate"
    (spec 3.3: "the PA when angle == 'rotate', otherwise None").

    NEGATIVE MEANS "NO ANGLE CONSTRAINT". 0 IS A POSITION ANGLE. This used
    to read 0 as the sentinel, on the reasoning that 0 is what an untouched
    field compiles to. PA 0 is north up, the angle most people frame at and
    the one a mosaic is planned around, and a field whose most common value
    cannot be expressed is a field that lies (#150). -1 is what the UI writes
    for "any angle"; a rotator cannot be commanded to a negative PA, so no
    real value is displaced.

    An entry with no ``angle`` (a compiled dict older than S3, or built by
    hand) reads its rotation alone, as every run before S3 did: a PA of 0 or
    more is commanded."""
    rotation = _number(entry.get("rotation_deg"))
    if rotation is not None and rotation < 0:
        rotation = None
    angle = entry.get("angle")
    if angle not in ("any", "rotate", "fixed"):
        angle = "any" if rotation is None else "rotate"
    layout = None if angle == "any" else rotation
    return angle, layout, (layout if angle == "rotate" else None)


def _centring(entry: dict) -> dict:
    """The ``Target`` fields a block's ``centre`` sets (#170), or ``{}``.

    A value the compile read as None is left unset, so the run centres to
    the hub's own 0.02 deg and 3 attempts, which are the missing-key 1.2
    arcmin and 3. A number outside ``Target``'s bounds is refused, naming
    the block, rather than raising a ValidationError out of the run."""
    centre = entry.get("centre")
    if not isinstance(centre, dict):
        return {}
    label = _block_label(entry)
    out: dict = {}
    tol = centre.get("tol_arcmin")
    if tol is not None:
        number = _number(tol)
        if number is None or not 0 < number <= CENTRE_TOL_MAX_ARCMIN:
            raise GraphNotRunnable(
                f"{label}: a centring tolerance of {tol!r} arcmin cannot be "
                f"used - it must be above 0 (a residual of exactly 0 is never "
                f"reached) and at most {CENTRE_TOL_MAX_ARCMIN:g} arcmin")
        out["center_tolerance_arcmin"] = number
    tries = centre.get("attempts")
    if tries is not None:
        whole = _whole(tries)
        if whole is None or not 1 <= whole <= CENTRE_ATTEMPTS_MAX:
            raise GraphNotRunnable(
                f"{label}: {tries!r} centring tries cannot be used - it must "
                f"be a whole number from 1 to {CENTRE_ATTEMPTS_MAX}")
        out["center_attempts"] = whole
    return out


def _mosaic_refusals(compiled: dict, graph: FlowGraph | None) -> list[str]:
    """Why a graph with a mosaic cannot become a plan at all (spec 1.8), one
    sentence each, every one at once:

    * M1, a multi-panel block with no camera field: panels are tiled from
      the field, and 0 x 0 tiles them onto one spot;
    * M2, a multi-panel block at "any angle": a grid is laid out at one
      angle, and panels shot at whatever angle the camera sits at do not
      tile;
    * M12 and M13, the wire shapes ``compile.lane_refusals`` names: a
      branched lane, a loop wire from a stage that is not the tail, a stage
      that belongs to no block. Those need the GRAPH, which both production
      callers pass; a graph-less caller gets M1 and M2 only."""
    out: list[str] = []
    if graph is not None:
        out.extend(r["text"] for r in lane_refusals(graph))
    for entry in compiled.get("targets") or []:
        mosaic = entry.get("mosaic")
        if not mosaic or entry.get("pool_rank") is not None:
            continue
        label = _block_label(entry)
        fov = [_number(mosaic.get("fov_x")), _number(mosaic.get("fov_y"))]
        if any(f is None or f <= 0 for f in fov):
            out.append(f"{label}: frame this block: panels are tiled from "
                       f"the camera's field, and this block has not recorded "
                       f"one.")
        if _angles(entry)[1] is None:
            out.append(f"{label}: a mosaic is laid out at one camera angle, "
                       f"and with no angle the panels will not tile. Lock an "
                       f"angle, or use the angle the camera measured.")
    return out


def _block_key(entry: dict, ra_hours: float, dec_deg: float,
               layout: float | None, canonical: str | None, *,
               anchor, grid: dict | None = None) -> str:
    """The key a block's ids hang off (spec 3.3): ``identity.target_key``
    with the block's ANCHOR, the geometry its counts started at, and its
    grid. With no anchor (an unsaved preview, a flow saved before S3) the
    current geometry is the anchor; a 1x1 block passes no grid, so a single
    target saved before S3 keys exactly as S1 keyed it.

    A STORED ANCHOR THAT CANNOT BE READ REFUSES THE PLAN. The server alone
    writes it, so it is a damaged file, and a key guessed from it would file
    tonight's frames under ids no ledger holds (``identity.anchor_geometry``
    says the same)."""
    try:
        return identity.target_key(entry, ra_hours, dec_deg, layout,
                                   canonical=canonical, anchor=anchor or None,
                                   **(grid or {}))
    except ValueError as e:
        raise GraphNotRunnable(
            f"{_block_label(entry)}: its stored frame anchor cannot be read "
            f"({e}), so its panels cannot be matched to the frames already "
            f"banked. The server writes it at save, so the file is damaged") \
            from None


def _mosaic_numbers(entry: dict) -> dict:
    """A mosaic entry's grid, checked against the bounds the group and the
    projection hold (``GRID_MAX`` and the rest). A value outside them, or
    one the compile could not read as a number (None), is refused naming
    the block and the field: guessing an overlap or a pass count would
    shoot a layout nobody framed."""
    m = entry["mosaic"]
    label = _block_label(entry)

    def bounded(key: str, low: float, high: float, what: str, *,
                whole: bool = False):
        value = _whole(m.get(key)) if whole else _number(m.get(key))
        if value is None or not low <= value <= high:
            raise GraphNotRunnable(
                f"{label}: {what} of {m.get(key)!r} cannot be used - it must "
                f"be {'a whole number ' if whole else ''}from {low:g} to "
                f"{high:g}")
        return value

    return {"rows": bounded("rows", 1, GRID_MAX, "a grid of rows", whole=True),
            "cols": bounded("cols", 1, GRID_MAX, "a grid of columns",
                            whole=True),
            "overlap": bounded("overlap", 0, OVERLAP_MAX_PCT,
                               "an overlap (percent)"),
            "passes": bounded("passes", 1, PASSES_MAX,
                              "a number of passes per visit", whole=True),
            "visit_min": bounded("visit_min", 0, VISIT_MAX_MIN,
                                 "a visit (minutes)"),
            "fov_x": _number(m.get("fov_x")), "fov_y": _number(m.get("fov_y"))}


def _expand_mosaic(entry: dict, *, name: str, ra_hours: float,
                   dec_deg: float, canonical: str | None, base_schedule: dict,
                   flow_id: str, unmapped: list[dict],
                   rig: RigFacts | None,
                   left_out: list[str]) -> tuple[list[dict], dict | None]:
    """``(panels, group)`` for one multi-panel TARGET entry (spec 3.3).

    One Target per panel ``framing.compute_mosaic`` lays out, in its order
    (rows from the top, snaking), SKIPPED PANELS DROPPED. Each is named
    "<name> <row>-<col>" 1-based and carries its 0-based ``panel_row`` and
    ``panel_col``, the group's id as ``mosaic_group``, the rotator's PA only
    for "rotate", ``acquisition = "cycle"`` when a stage cycles or the loop
    wire rotates the panels, the block's centring, ``autofocus_skip_if_fresh``
    (a hop does not move the focuser, spec 5.6) and the dusk schedule.

    The group's id is ``identity.group_id`` over the block's key
    (``_block_key``: its anchor), so a panel's id, ``target_id(group, row,
    col)``, is a function of the flow, the block, the anchor and the panel,
    and ``skip`` is in none of them: skipping a panel moves its id into the
    group's ``skipped_ids`` and leaves every other id where it was. Without a
    flow id (an unsaved preview) the group id is a fresh uuid4 and the
    panels' ids follow it: fresh on each compile, as uuid4s are, but agreeing
    with each other.

    A block whose every panel is skipped shoots nothing: it is dropped with a
    warn, not emitted as a group of no members, which ``plan_identity_errors``
    would refuse at every start path. ITS SKIP IS KEPT all the same (#335):
    the panels' ids are appended to ``left_out``, which becomes the plan's
    own ``skipped_ids``, so CONTINUE still tells its panels' frames from a
    dropped step's (``continuation.plan_skipped_ids``). Dropped with no
    trace, as S3 built it, those frames were a 409 "subs belong to steps
    this flow no longer has", about frames that re-enabling a panel brings
    straight back."""
    # Imported here, not at the top: `catalog.framing` is the Atlas's router,
    # and importing it loads the auth layer, which a plan with no mosaic has
    # no use for (framing imports `sequence` lazily for the same reason).
    from ..catalog import framing

    label = _block_label(entry)
    m = entry["mosaic"]
    nums = _mosaic_numbers(entry)
    angle, layout, commanded = _angles(entry)
    overlap = nums["overlap"] / 100.0
    spec = {"ra_hours": ra_hours, "dec_deg": dec_deg, "rows": nums["rows"],
            "cols": nums["cols"], "overlap": overlap, "rotation_deg": layout,
            "fov_x_deg": nums["fov_x"], "fov_y_deg": nums["fov_y"]}
    try:
        layout_panels = framing.compute_mosaic(spec)["panels"]
        tolerance = framing.angle_tolerance_deg(spec)
    except ValidationError as e:
        # THE LAYOUT REFUSES THE CENTRE BEFORE ANY PANEL IS MADE (#362):
        # ``MosaicSpecIn`` holds the sphere's bounds, so an RA typed as 30h
        # was a pydantic error out of the route. Named as the plan's own
        # refusals are, the block and the field.
        raise GraphNotRunnable(_refused_values(
            e, lambda loc: (label, _path(loc)))) from None
    key = _block_key(entry, ra_hours, dec_deg, layout, canonical,
                     anchor=m.get("frame_anchor"),
                     grid=dict(rows=nums["rows"], cols=nums["cols"],
                               overlap=overlap, fov_x=nums["fov_x"],
                               fov_y=nums["fov_y"]))
    node_id = str(entry.get("node_id") or "")
    group_id = (identity.group_id(flow_id, node_id, key)
                if flow_id and node_id else uuid4().hex)
    skip = {(int(r) - 1, int(c) - 1) for r, c in (m.get("skip") or [])}
    steps = _steps(entry, name or "?", unmapped)
    cycles = entry.get("loop") is True or any(
        (s or {}).get("strategy") == "cycle"
        for s in (entry.get("steps") or []))
    centring = _centring(entry)
    panels: list[dict] = []
    skipped_ids: list[str] = []
    for p in layout_panels:
        row, col = p["row"], p["col"]
        tid = identity.target_id(group_id, row, col)
        if (row, col) in skip:
            skipped_ids.append(tid)
            continue
        panel = {"name": (f"{name} {row + 1}-{col + 1}" if name
                          else f"{row + 1}-{col + 1}"),
                 "ra_hours": p["ra_hours"], "dec_deg": p["dec_deg"],
                 "rotation_deg": commanded,
                 "schedule": _target_schedule(base_schedule, entry,
                                              is_pool=False),
                 "steps": copy.deepcopy(steps),
                 "mosaic_group": group_id, "panel_row": row,
                 "panel_col": col, "autofocus_skip_if_fresh": True,
                 **centring}
        if cycles:
            panel["acquisition"] = "cycle"
        _identify(panel, entry, flow_id=flow_id, is_pool=False,
                  members_seen={}, tid=tid)
        panels.append(panel)
    if not panels:
        unmapped.append(_note(
            f"targets[{name or '?'}].mosaic.skip",
            f"every panel of {label} is skipped, so it shoots nothing and "
            f"is left out of the plan"))
        left_out.extend(skipped_ids)
        return [], None
    fov_x, fov_y = nums["fov_x"], nums["fov_y"]
    if rig is not None and rig.fov_deg is not None:
        # M5's LOSS (spec 1.8): the camera on the rig now images less than
        # one step of the grid, so neighbouring panels would not meet. The
        # compile never re-tiles from the live optics - the snapshot is
        # what the engine slews to - so the operator re-frames.
        live_x, live_y = rig.fov_deg
        if live_x < fov_x * (1 - overlap) or live_y < fov_y * (1 - overlap):
            unmapped.append(_note(
                f"targets[{name or '?'}].mosaic.fov",
                f"framed for {fov_x:.2f} x {fov_y:.2f} deg; this camera now "
                f"images {live_x:.2f} x {live_y:.2f} deg, so the panels would "
                f"leave gaps. Re-frame."))
    group = {
        "id": group_id, "name": name, "kind": "mosaic",
        "mode": "rotate" if entry.get("loop") is True else "sequential",
        "visit_passes": nums["passes"],
        "visit_min_s": nums["visit_min"] * 60.0,
        "order": GROUP_ORDERS.get(str(m.get("order") or "").strip(),
                                  "least_complete"),
        # Only "Shoot anyway" lets a panel shoot off its tile.
        "require_centred": m.get("require_centred") is not False,
        "pa_deg": layout,
        "rotate": angle == "rotate",
        # A.2 with convergence's share taken first (Revision 1): how far the
        # camera may sit off `pa_deg` before the corner overlap runs out.
        "angle_tolerance_deg": tolerance,
        "skipped_ids": skipped_ids,
        # Provenance only; `_group_cols` reads `cols` as a fallback.
        "geometry": {"rows": nums["rows"], "cols": nums["cols"],
                     "overlap": overlap, "fov_x": fov_x, "fov_y": fov_y,
                     "fov_from": str(m.get("fov_from") or ""), "key": key},
    }
    return panels, group


def _refused_values(error: ValidationError, where) -> str:
    """The sentence a ``ValidationError`` from the plan's models becomes
    (#362 item 1): one clause per value refused, each naming the block, the
    field, the value and the bound, all of them at once, as
    ``_mosaic_refusals`` gives every refusal at once.

    ``where(loc)`` answers ``(block, field)`` for one error's location: the
    block's label, as ``_block_label`` writes it, and the rest of the path
    inside what it built (``steps[0].exposure_s``, ``ra_hours``).

    SAID ONCE PER BLOCK AND FIELD. A mosaic's six panels carry one copy of
    the block's steps each, and a FILTER CYCLE's slots are one step each, so
    one fractional gain on a two-filter cycle over six panels is twelve
    errors and one fault in one place on the canvas. Clauses that differ
    only in a list index (``steps[0]``, ``steps[1]``) are that one fault,
    and the first is said. A value's repr is cut at 40 characters: a
    309-digit integer names itself by its first digits."""
    clauses: list[str] = []
    seen: set[tuple[str, str, str, str]] = set()
    for err in error.errors():
        block, field = where(tuple(err.get("loc") or ()))
        msg = str(err.get("msg") or "it is not accepted")
        why = f"{msg[:1].lower()}{msg[1:]}"
        value = repr(err.get("input")) if field else ""
        if len(value) > 40:
            value = value[:37] + "..."
        fault = (block, re.sub(r"\[\d+\]", "[]", field), value, why)
        if fault in seen:
            continue
        seen.add(fault)
        if field:
            clauses.append(f"{block}: {field} of {value} cannot be used - "
                           f"{why}.")
        else:
            # A model's own check (an Instruction's relative factor): the
            # whole thing is the value, and its message names the field.
            clauses.append(f"{block} cannot be used - {why}.")
    return " ".join(clauses)


def _path(loc: tuple) -> str:
    """A location inside one model as the PLAN tab would name it:
    ``("steps", 0, "exposure_s")`` is ``steps[0].exposure_s``, and ``()``,
    the model itself, is blank."""
    out = ""
    for part in loc:
        out += f"[{part}]" if isinstance(part, int) else (
            f".{part}" if out else str(part))
    return out


def _count_mode(built: list[tuple[dict, list[dict]]], out: list[dict]) -> str:
    """``plan.count_mode`` for the blocks that made it into the plan (spec
    3.3): "accepted" when ANY asks for accepted subs. The count mode is one
    setting for the whole plan (``SequencePlan.count_mode``), so blocks that
    disagree cannot both be honoured, and the loser is M7, a loss: a block
    that counts every sub would reach its quota on frames the grader threw
    away, or one that counts accepted subs would stop early. An entry with
    no ``count_mode`` (compiled before S3) counts every sub, as it did."""
    # One ask per BLOCK: a pool's members all carry the pool's words.
    asks: dict[str, dict] = {}
    for entry, targets in built:
        if not targets:
            continue
        block = str(entry.get("node_id") or "") or f"#{id(entry)}"
        ask = asks.setdefault(block, {
            "pool": entry.get("pool_rank") is not None, "names": [],
            "label": _block_label(entry),
            "mode": ("accepted" if entry.get("count_mode") == "accepted"
                     else "attempts")})
        ask["names"].append(str(entry.get("name") or "").strip() or "?")
    labels = {b: (f"TARGET POOL ({', '.join(a['names'])})" if a["pool"]
                  else a["label"]) for b, a in asks.items()}
    accepted = [labels[b] for b, a in asks.items() if a["mode"] == "accepted"]
    counting = [labels[b] for b, a in asks.items() if a["mode"] == "attempts"]
    if accepted and counting:
        out.append(_note(
            "count_mode",
            f"this plan counts accepted subs because {_and_list(accepted)} "
            f"{'asks' if len(accepted) == 1 else 'ask'} for it; "
            f"{_and_list(counting)}'s 'every sub taken' cannot be honoured in "
            f"the same run"))
    return "accepted" if accepted else "attempts"


def to_sequence_plan(compiled: dict, graph: FlowGraph | None = None, *,
                     flow_id: str = "",
                     when: float | None = None,
                     cool_to: float | None = None,
                     camera_can_cool: bool = False,
                     closes_on_unsafe: bool = False,
                     rig: RigFacts | None = None
                     ) -> tuple[SequencePlan, list[dict]]:
    """``(plan, unmapped)`` for a compiled flow.

    ``graph`` is optional but strongly wanted: without it the inert-node class
    cannot be reported at all, because it is invisible in ``compiled``.
    ``when`` is the timestamp pool names are resolved against.

    ``flow_id`` makes the target and step ids DETERMINISTIC (#189 S1, spec
    3.3): a uuid5 of the flow, the node, the geometry and the recipe (see
    ``flows/identity.py``), so compiling one flow twice names the same targets
    and steps, and the session ledger - which counts frames by step id alone -
    can continue a campaign across nights. Empty (the default, an unsaved
    preview) leaves every id a fresh uuid4, as before. Instruction ids stay
    uuid4 either way: the ledger counts frames, not rules.

    ``cool_to`` is THE RIG'S OWN STANDING SETPOINT, injected by the caller -
    never read from config here, so this stays a pure function of the compile.
    The flow vocabulary has no cooling node, so without it every flow-driven
    night shot at whatever temperature the sensor happened to be, against a dark
    library indexed by a temperature it never matched. Passing it also buys the
    stabilize-before-lights wait, which is the half a standing setpoint alone
    cannot give: the cooler may still be ramping when the first sub opens.

    ``None`` leaves ``cool_to`` unset and the run behaves exactly as before -
    the rig with no configured setpoint has expressed no intent to cool, and
    inventing one here would be picking a number on the operator's behalf.

    ``camera_can_cool`` is the rig's answer to "is there a TEC on this camera",
    injected for the same reason ``cool_to`` is: this function has no devices.
    It only decides whether ``None`` is worth REPORTING - a plan with no
    temperature on a camera that could have held one earns a note, and the same
    plan on an uncooled camera earns nothing. The default is False so a caller
    that cannot answer stays silent rather than nagging.

    ``rig`` is what the route knows about the live rig (``flows.rig``),
    injected for the same reason. Only M5 reads it here: a mosaic framed for
    a field the camera no longer images leaves gaps, which is a loss. None,
    the default, knows nothing and reports nothing.

    A TARGET ENTRY WITH A GRID becomes one Target per panel and one
    ``TargetGroup`` (``_expand_mosaic``, spec 3.3); one of a single panel
    stays one plain Target, with its centring; one whose every panel is
    skipped is left out, and its panels' ids become the plan's own
    ``skipped_ids`` (#335). A target a mosaic's tail feeds
    waits for the mosaic's group (``after_group``) only when the flow says
    "Wait for the mosaic"; otherwise the engine lets it fill the mosaic's
    gaps, from plan order. ``plan.count_mode`` is "accepted" when any block
    asks for it, and a block that disagrees is M7, a loss.

    Raises :class:`GraphNotRunnable` when there is nothing runnable here - no
    targets at all, or a capture step with no exposure or no frames - and
    for a mosaic that cannot run (M1, M2, M12, M13; ``_mosaic_refusals``),
    every such sentence at once. And for a value the plan's models refuse,
    naming the block and the field (``_refused_values``, #362): nothing
    else leaves this function, so the compile route never answers 500 on a
    draft and ``/run`` answers 422.
    """
    refusals = _mosaic_refusals(compiled, graph)
    if refusals:
        raise GraphNotRunnable(" ".join(refusals))
    unmapped: list[dict] = []
    base_schedule = {k: v for k, v in (compiled.get("schedule") or {}).items()
                     if k in SCHEDULE_KEYS}

    targets: list[dict] = []
    groups: list[dict] = []
    # The block each target and each group came from, index for index, so a
    # value the plan's models refuse is named by its block (#362 item 1).
    target_blocks: list[str] = []
    group_blocks: list[str] = []
    # And, index for index with the targets, the `schedule` fields the DUSK
    # WINDOW wrote there, the ones no POOL wrote over (`_pool_overrides`): a
    # refused one names the DUSK WINDOW, not the target carrying a copy (#483).
    dusk_fields: list[frozenset[str]] = []
    # Each entry with the targets it became, for the count mode and the
    # followers, which are settled once every block is built.
    built: list[tuple[dict, list[dict]]] = []
    # A mosaic's node id -> its group, for the targets that follow it.
    group_of: dict[str, dict] = {}
    # The panels of every block left out whole because each is skipped
    # (#335): the plan's own `skipped_ids`, absent while empty.
    left_out: list[str] = []
    pooled = 0
    members_seen: dict[str, set[str]] = {}
    for entry in compiled.get("targets") or []:
        name = str(entry.get("name") or "").strip()
        is_pool = entry.get("pool_rank") is not None
        coords = _coords(entry, when)
        if coords is None:
            unmapped.append(_note(
                f"targets[{name or '?'}]",
                "dropped: no usable coordinates. A pool member has to be a "
                "name this catalogue knows; a target needs an RA and Dec that "
                "parse", "danger"))
            continue
        ra_hours, dec_deg, canonical = coords

        if entry.get("mosaic") and not is_pool:
            panels, group = _expand_mosaic(
                entry, name=name, ra_hours=ra_hours, dec_deg=dec_deg,
                canonical=canonical, base_schedule=base_schedule,
                flow_id=flow_id, unmapped=unmapped, rig=rig,
                left_out=left_out)
            if group is not None:
                groups.append(group)
                group_blocks.append(_block_label(entry))
                group_of[str(entry.get("node_id") or "")] = {
                    **group, "when_waiting": entry["mosaic"].get(
                        "when_waiting")}
            targets.extend(panels)
            target_blocks.extend(_block_label(entry) for _ in panels)
            dusk_fields.extend(_dusk_fields(base_schedule, entry)
                               for _ in panels)
            built.append((entry, panels))
            continue

        # The angle the rotator is told (`_angles`: negative is no angle, 0 is
        # north up, and only "rotate" commands one) and the one the block is
        # laid out at, which keys it.
        _angle, layout, rotation = _angles(entry)

        target = {"name": name, "ra_hours": ra_hours, "dec_deg": dec_deg,
                  "rotation_deg": rotation,
                  "schedule": _target_schedule(base_schedule, entry,
                                               is_pool=is_pool),
                  "steps": _steps(entry, name or "?", unmapped)}
        if not is_pool:
            # #170: the TARGET's own centring reaches the run. A legacy SLEW's
            # tolerance does not (spec 1.7; `NODE_SETTINGS["slew"]`).
            target.update(_centring(entry))
        if any((s or {}).get("strategy") == "cycle"
               for s in (entry.get("steps") or [])):
            # The FILTER CYCLE reaches the engine. `_cycle_steps` put `per_visit`
            # on every expanded step; THIS is the field the engine branches on,
            # and without it those per_visit values would be inert decoration on
            # a plan that still shot in blocks.
            #
            # Read off the STEPS rather than a separate `acquisition` key on the
            # target: one fact, one place. A plan whose steps say interleave and
            # whose target says blocks is a plan nobody can read, and keeping two
            # keys in step is how that happens.
            target["acquisition"] = "cycle"
        key = None
        if not is_pool and flow_id and entry.get("node_id"):
            # The anchor first (spec 3.3, ruling 3), and no grid: a single
            # target with no anchor keys exactly as S1 keyed it.
            key = _block_key(entry, ra_hours, dec_deg, layout, canonical,
                             anchor=entry.get("frame_anchor"))
        _identify(target, entry, flow_id=flow_id, is_pool=is_pool,
                  members_seen=members_seen, canonical=canonical, key=key)
        targets.append(target)
        target_blocks.append(_block_label(entry))
        dusk_fields.append(_dusk_fields(base_schedule, entry))
        built.append((entry, [target]))
        pooled += 1 if is_pool else 0

    # FOLLOWERS WAIT ONLY WHEN THE FLOW SAYS SO (spec 1.6). Under the default
    # a follower needs no field: the engine treats every target after a
    # group in plan order as its follower and lets it fill the group's gaps,
    # bounded. A mosaic dropped from the plan has no group to wait for, and a
    # gate on a group that is not there would never open
    # (`plan_identity_errors` refuses it), so its followers run as targets.
    for entry, made in built:
        group = group_of.get(str(entry.get("follows") or ""))
        if group is not None and group["when_waiting"] == WAIT_FOR_THE_MOSAIC:
            for t in made:
                t["after_group"] = group["id"]

    if not targets:
        raise GraphNotRunnable(
            "this flow has no target the run could point at - add a TARGET "
            "node with coordinates, or a POOL whose members are catalogue names")

    if pooled:
        # Counted as targets are BUILT, not zipped against the compiled list
        # afterwards: a dropped member shifts the two lists out of step, and the
        # count would then describe candidates that are not in this plan.
        unmapped.append(_note(
            "targets[*].pool_rank",
            f"'best of several' is not something the engine can do yet: all "
            f"{pooled} candidates run in rank order, each skipped if its "
            f"window is missed, rather than one being chosen", "warn"))

    _automation(compiled, unmapped, closes_on_unsafe=closes_on_unsafe)
    unmapped.extend(inert_nodes(graph))

    fields: dict = {
        "name": compiled.get("name") or "Flow",
        "targets": targets,
        "instructions": _instructions(compiled, unmapped),
        **plan_extras(compiled),
    }
    if _count_mode(built, unmapped) == "accepted":
        fields["count_mode"] = "accepted"
    if groups:
        fields["groups"] = groups
    if left_out:
        fields["skipped_ids"] = left_out
    # THE GUIDE NODE MEANS WHAT IT DRAWS (#239 stage C).
    #
    # `guide` was never set here, so every flow-built night guided - a graph
    # with no GUIDE node on it guided anyway, and one with a GUIDE node was no
    # different. `inert_nodes` was honest that the node's SETTINGS went nowhere,
    # but the sentence an operator actually reads off a canvas is "there is no
    # guider in this picture", and the run disagreed with it.
    #
    # Seven nights on the rig were shot with guide=False, one of them a real
    # imaging night (NGC 7023, unguided). A flow could not express any of them.
    #
    # Only when the GRAPH is in hand: `compiled` does not carry node types, and
    # guessing "unguided" from its absence would turn every graph-less caller
    # (tests, the odd internal path) into an unguided night by accident. Both
    # production callers pass the graph.
    if graph is not None:
        fields["guide"] = any(n.type == "guide" for n in graph.nodes)
    if cool_to is not None:
        fields["cool_to"] = float(cool_to)
    elif camera_can_cool:
        # THE NIGHT HAS NO TEMPERATURE, AND THE EDITOR IS WHERE TO SAY IT.
        #
        # The engine warns at run start (`_warn_if_the_run_has_no_temperature`),
        # which is what caught this on 2026-08-22 - 80 minutes and 19 frames at
        # +23 °C against a -10 °C library. A line in a log at 22:00 is worth
        # less than a line on the canvas at 19:00, and this list is already the
        # thing the PLAN tab draws before anyone presses Run.
        #
        # NOTE, NOT A LOSS, and deliberately: `losses` is what makes /run refuse
        # with "parts of this flow do not survive the compile", and refusing
        # here would block an intentionally uncooled night on a rig whose
        # vocabulary cannot express cooling in the first place. A note is the
        # level for "worth reading, not worth blocking on" - the same reason
        # HOLD_HONOURED and the redundant ports use it.
        unmapped.append(_note(
            "cooling.setpoint_c",
            "this run has no target temperature: the flow vocabulary has no "
            "cooling node, so a run cools to the rig's standing setpoint and "
            "nothing has set one. This camera can cool, so every frame will be "
            "exposed at whatever the sensor happens to read and will not match "
            "a dark library. Set Target °C on the Capture tab and press Cool, "
            "or run uncooled on purpose",
            "note"))

    def where(loc: tuple) -> tuple[str, str]:
        """``(block, field)`` for one refused value's location in ``fields``:
        a target's or a group's block, the DUSK WINDOW for a ``schedule``
        field it wrote, a rule by its trigger and action, or the flow for a
        plan-level field.

        THE DUSK WINDOW, NOT THE TARGET (#483). Every target carries a copy
        of the DUSK WINDOW's block in its ``schedule``, so a fractional
        offset was named by each target that carried it, "TARGET M31 -
        Andromeda: schedule.start_offset_min of -30.7", and a POOL's four
        members were four clauses, each sending the operator to a card that
        does not hold the value. Named by the DUSK WINDOW, the copies are
        one fault in one place, and ``_refused_values`` says it once. A
        field a POOL wrote over the night's is that member's, as before."""
        head, index = (loc + (None, None))[:2]
        rules = fields["instructions"]
        if isinstance(index, int):
            if head == "targets" and index < len(target_blocks):
                rest = loc[2:]
                if (len(rest) > 1 and rest[0] == "schedule"
                        and rest[1] in dusk_fields[index]):
                    return DUSK_BLOCK, _path(rest)
                return target_blocks[index], _path(rest)
            if head == "groups" and index < len(group_blocks):
                return group_blocks[index], _path(loc[2:])
            if head == "instructions" and index < len(rules):
                rule = rules[index]
                return (f"the rule {rule.get('trigger')} -> "
                        f"{rule.get('action')}", _path(loc[2:]))
        return "this flow", _path(loc)

    try:
        plan = SequencePlan.model_validate(fields)
    except ValidationError as e:
        # A VALUE THE PLAN'S MODELS REFUSE IS THE OPERATOR'S TO FIX (#362
        # item 1). The compile reads a card's numbers as it finds them, and
        # `ExposureStep`, `Schedule` and `Target` hold the bounds (an
        # exposure of at most 3600 s, a whole gain, an hour angle of at most
        # 12 h, an RA under 24 h). Uncaught, their ValidationError was a 500
        # from the editor's compile on every edit and from /run, where the
        # route answers GraphNotRunnable with the block to fix: the plan's
        # danger row, and a 422. Checking each bound here as well, as
        # `_centring` and `_mosaic_numbers` do for theirs, would be a second
        # copy of the models' bounds, free to drift from the ones the run
        # obeys; the models' own verdict, named by block, cannot.
        raise GraphNotRunnable(_refused_values(e, where)) from None
    # D-08 (backlog ruling, owner-approved 2026-09-30; #559, #582). This
    # function is pure - no site, no clock beyond ``when`` - so it cannot say
    # whether a site is actually saved; it says what ``sequence.engine.start``
    # will insist on if one is not, the SAME sentence
    # (`schedule.sun_window_needs_a_site`), read off the plan this compile
    # just validated so the two can never drift into saying it two ways.
    #
    # LEVEL "note", NOT "warn" (caught in review): this fires on EVERY
    # dusk/dawn-scheduled plan, including one on a rig with a site already
    # saved, because this function has no site to check. A "warn" entry is
    # what `losses()` reports as something the compile actually drops - and
    # `/api/flows/{id}/run` refuses on a loss until `accept_unmapped` - so at
    # "warn" this blocked every correctly-configured DUSK WINDOW flow with a
    # false "parts of this flow do not survive the compile", caught by the
    # existing tests/test_a_clean_flow_is_not_ten_warnings.py and
    # tests/test_flows_routes.py. A note, not a loss: a flow with a site
    # already saved survives the compile whole, and one with no site yet is
    # still a flow worth saving - this is what stands between it and a run
    # that refuses to start, not something this compile failed to honour.
    site_warning = sun_window_needs_a_site(plan.targets)
    if site_warning is not None:
        unmapped.append(_note("schedule", site_warning, "note"))
    return plan, unmapped


def blocking_reasons(unmapped: list[dict], *, dome_connected: bool,
                     closes_on_unsafe: bool = False) -> list[dict]:
    """The subset of ``unmapped`` that should stop a run from starting.

    ONE ENTRY QUALIFIES TODAY: a dome policy that the engine cannot act on,
    when a dome is actually connected. A roof is the only thing in this list
    whose absence can damage equipment - everything else costs frames.

    ``dome_connected`` is why this is not decided inside
    :func:`to_sequence_plan`. If no dome is attached there is no roof to leave
    open, and refusing would block the shipped M16 example from ever running on
    the simulator - which the handoff's definition of done explicitly requires.
    The refusal is about a physical shutter, so it is gated on there being one.
    """
    if not dome_connected:
        return []
    if closes_on_unsafe:
        # THE ROOF WILL CLOSE, so refusing the run protects nothing.
        #
        # The plan still cannot carry the compiled DomePolicy - that part of the
        # refusal was true - but the sentence that followed it was not: the
        # engine passes `cfg.safety.close_dome_on_unsafe` into the unsafe
        # wind-down, which routes a connected dome through
        # `roof.close_observatory` (never over an unparked mount). So on a rig
        # with that setting on, the DOME CONTROL node's one non-negotiable
        # promise IS kept - by config rather than by the node.
        #
        # Blocking anyway made the advice actively wrong: "remove the dome node"
        # changes nothing about whether the roof closes and throws away the
        # operator's stated intent. What remains unhonoured is the node's
        # azimuth binding and shutter timeout, and that is a note, not a wall.
        return []
    return [u for u in unmapped if u["key"] == "automation.dome"]


def losses(unmapped: list[dict]) -> list[dict]:
    """The subset of ``unmapped`` that is actually a LOSS.

    ``/api/flows/{id}/start`` refuses with "parts of this flow do not survive
    the compile" until the operator passes ``accept_unmapped``, and a ``note``
    entry is the one kind that contradicts that sentence: it says the drawn
    thing IS honoured, by some other part of the engine. Asking an operator to
    accept a statement the same list disproves teaches them the whole list is
    noise - which is expensive, because the rest of it is not.

    The notes still travel in the response. They are worth reading; they are
    just not worth blocking on.
    """
    return [u for u in unmapped if u.get("level") != "note"]
