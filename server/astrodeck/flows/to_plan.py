"""A compiled flow, as something ``SequenceEngine`` can actually run.

``compile_plan`` produces the README's documented five-key dict — the shape the
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
Whether an unmapped item should BLOCK a run. This function is pure — it has no
devices, no config and no clock beyond the one passed in — and "is there
actually a roof over this telescope" is not a question it can answer. It
classifies and reports; the route decides. See ``blocking_reasons``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Sequence

from ..catalog.coords import parse_dec, parse_ra
from ..sequence.models import ActionKind, SequencePlan, TriggerKind
from .models import FlowGraph
from .tonight import catalog_coords

#: The engine's real vocabularies, read off the ``Literal`` types rather than
#: retyped. A hand-copied list is a claim that silently stops being true the
#: day the enum is widened, which is the failure this module exists to surface.
LEGAL_TRIGGERS: frozenset[str] = frozenset(TriggerKind.__args__)
LEGAL_ACTIONS: frozenset[str] = frozenset(ActionKind.__args__)

#: Triggers that answer with a VERDICT, not a measurement — so a threshold means
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
#: parameters are inert — see :func:`inert_nodes`.
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

#: The four ``schedule`` keys ``compile_plan`` emits are field-for-field
#: identical to ``Schedule``'s. They are simply at the wrong NESTING LEVEL:
#: ``SequencePlan`` has no schedule, ``Target`` does.
SCHEDULE_KEYS = ("start_mode", "start_offset_min", "stop_mode", "min_altitude_deg")

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
    severity scale — an operator should not have to learn that a doctor warning
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
    # The tolerance and the attempt count are `hub.goto_and_center`'s own
    # defaults (0.02 deg, 3), not config - so "where the real value lives" is
    # the centring loop itself, and saying Settings would send someone looking
    # for a box that does not exist.
    "slew": SettingsNote(
        carried=(("", "the slew and the plate-solve centring themselves: every "
                      "target is centred before its first frame"),),
        ignored=(("tol", "the {value} arcmin tolerance"),
                 ("retries", "the {value} centring attempts"),
                 ("solver", "the solver name {value}")),
        source="the run's own centring loop (1.2 arcmin, at most 3 attempts, "
               "the rig's configured solver)",
        detail="SLEW + CENTER happens on every target: the run slews and "
               "plate-solve centres before the first frame, with or without "
               "this node on the canvas. {Ignored} come from {source} "
               "instead."),
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
    """The graph cannot become a plan at all — an operator error, not a bug.

    Distinct from an unmapped item: unmapped means "this ran without that",
    while this means "there is nothing here to run". The route maps it to a 422
    with ``code="invalid_graph"`` so the editor can say which node is at fault
    rather than showing a server error.
    """

    def __init__(self, message: str, code: str = "invalid_graph"):
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------- pieces

def _target_schedule(base: dict, entry: dict, *, is_pool: bool) -> dict:
    """The ``Schedule`` block for one target.

    Two sources merge here. The DUSK WINDOW node's block applies to the whole
    night; a POOL member's constraints apply to that member. WHERE THEY
    DISAGREE THE POOL WINS, because it is the more specific statement — an
    operator who set a 30 degree floor on the night and 40 on one candidate
    meant 40 for that candidate.
    """
    sched = dict(base)
    for src, dst in POOL_SCHEDULE_KEYS.items():
        if entry.get(src) is not None:
            sched[dst] = entry[src]
    if is_pool:
        # The closest honest approximation of "best of several" the existing
        # model can express. Under the default "wait", member 1 blocks the whole
        # night waiting for a window it may never get, and members 2-4 - the
        # entire point of a pool - never run at all. Under "skip" each member is
        # attempted and passed over when its window is missed.
        sched["on_missed"] = "skip"
    return sched


def _coords(entry: dict, when: float | None) -> tuple[float, float] | None:
    """``(ra_hours, dec_deg)`` for one compiled target entry, or ``None``.

    A plain TARGET carries sexagesimal text; a POOL member carries only a name
    and is resolved against the shipped catalogue.

    NEVER INVENTS (0, 0). ``Target`` accepts it happily and ``calibration``
    defaults to False, so the engine would slew there - and 0h/0deg is below
    the horizon at most sites, which means the operator gets a horizon refusal
    naming a target they never entered.
    """
    ra_text, dec_text = entry.get("ra"), entry.get("dec")
    if ra_text and dec_text:
        try:
            return parse_ra(str(ra_text)), parse_dec(str(dec_text))
        except (TypeError, ValueError):
            return None
    name = str(entry.get("name") or "").strip()
    return catalog_coords(name, when) if name else None


def _cycle_steps(step: dict, target_name: str, index: int) -> list[dict]:
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
    cycles = max(1, int(step.get("cycles") or 1))
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
        })
    return out


def _steps(entry: dict, target_name: str, out: list[dict]) -> list[dict]:
    steps: list[dict] = []
    for i, step in enumerate(entry.get("steps") or []):
        if step.get("strategy") == "cycle":
            steps.extend(_cycle_steps(step, target_name, i))
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
                # model layer is checked here first — a rule that failed it
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
                # `in` port usually feeds several destinations — the hold AND
                # the notify, in the shipped example — and each compiles to its
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
        "What does NOT happen is the cooler setting - the camera is warmed at "
        "the end of every run, so 'Hold cold (day darks)' is not honoured and "
        "no darks are taken after a shutdown"),
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
#: params arrive. Mostly true, and for `reject` it is false — which made this
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
    # `park_when_done=False` guaranteed would not happen — a claim nothing
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


def to_sequence_plan(compiled: dict, graph: FlowGraph | None = None, *,
                     when: float | None = None,
                     cool_to: float | None = None,
                     camera_can_cool: bool = False,
                     closes_on_unsafe: bool = False
                     ) -> tuple[SequencePlan, list[dict]]:
    """``(plan, unmapped)`` for a compiled flow.

    ``graph`` is optional but strongly wanted: without it the inert-node class
    cannot be reported at all, because it is invisible in ``compiled``.
    ``when`` is the timestamp pool names are resolved against.

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

    Raises :class:`GraphNotRunnable` when there is nothing runnable here - no
    targets at all, or a capture step with no exposure or no frames.
    """
    unmapped: list[dict] = []
    base_schedule = {k: v for k, v in (compiled.get("schedule") or {}).items()
                     if k in SCHEDULE_KEYS}

    targets: list[dict] = []
    pooled = 0
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
        ra_hours, dec_deg = coords

        # NEGATIVE MEANS "NO ANGLE CONSTRAINT". 0 IS A POSITION ANGLE.
        #
        # This used to read 0 as the sentinel, on the reasoning that 0 is what an
        # untouched field compiles to and that "a wrong None costs an operator
        # who really wanted PA 0 an unconstrained angle". Both halves were
        # wrong-headed: PA 0 is a perfectly ordinary answer — it is north up,
        # the angle most people frame at and the one a mosaic is planned around
        # — and a field whose most common value cannot be expressed is a field
        # that lies. It also cost every flow a permanent advisory line, because
        # the warning fired on the DEFAULT.
        #
        # So the sentinel is anything below zero. -1 is what the UI writes for
        # "any angle"; a rotator cannot be commanded to a negative PA, so no
        # real value is displaced, and an operator who types 0 now gets 0.
        rotation = entry.get("rotation_deg")
        if rotation is None or (isinstance(rotation, (int, float)) and rotation < 0):
            rotation = None

        target = {"name": name, "ra_hours": ra_hours, "dec_deg": dec_deg,
                  "rotation_deg": rotation,
                  "schedule": _target_schedule(base_schedule, entry,
                                               is_pool=is_pool),
                  "steps": _steps(entry, name or "?", unmapped)}
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
        targets.append(target)
        pooled += 1 if is_pool else 0

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
        # which is what caught this on 2026-08-22 — 80 minutes and 19 frames at
        # +23 °C against a -10 °C library. A line in a log at 22:00 is worth
        # less than a line on the canvas at 19:00, and this list is already the
        # thing the PLAN tab draws before anyone presses Run.
        #
        # NOTE, NOT A LOSS, and deliberately: `losses` is what makes /run refuse
        # with "parts of this flow do not survive the compile", and refusing
        # here would block an intentionally uncooled night on a rig whose
        # vocabulary cannot express cooling in the first place. A note is the
        # level for "worth reading, not worth blocking on" — the same reason
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
    plan = SequencePlan.model_validate(fields)
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
