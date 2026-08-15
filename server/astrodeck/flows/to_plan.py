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

from typing import Any, Literal

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
#: from a different edge is a different promise. ``on_shutdown_complete ->
#: calib.do`` is the campaign's day-darks lane and it genuinely does not run;
#: collapsing both onto "calib" would trade one wrong sentence for another.
#:
#: Guarded by ``hold_darks`` at the call site: with a quota of 0 no darks are
#: taken, and calling the wire redundant then would be the same overclaim in
#: reverse.
HOLD_HONOURED: dict[tuple[str, str, str], str] = {
    ("on_clouds_in", "calib", "do"): (
        "the cloud hold takes darks itself, matched to the step it interrupts "
        "and capped at what the library still needs, so the darks leg of this "
        "wire is already honoured. Its bias and flat legs are not - see the "
        "calibration-queue note above"),
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
                      "min_moon_sep_deg": "min_moon_sep_deg",
                      "max_hour_angle_h": "max_hour_angle_h"}

Level = Literal["warn", "danger"]


def _note(key: str, detail: str, level: Level = "warn") -> dict:
    """One reported loss.

    ``level`` reuses ``doctor.Issue``'s vocabulary so the editor has ONE
    severity scale — an operator should not have to learn that a doctor warning
    and an adapter warning mean different things.
    """
    return {"key": key, "detail": detail, "level": level}


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
    hold_darks = int(plan_extras(compiled).get("cloud_hold_darks") or 0)
    for rule in compiled.get("instructions") or []:
        trigger = str(rule.get("when") or "")
        raw = str(rule.get("action") or "")
        port = str(rule.get("to_port") or "")
        if (raw, port) in REDUNDANT_PORTS:
            out.append(_note(f"instructions[{trigger} -> {raw}.{port}]",
                             REDUNDANT_PORTS[(raw, port)]))
            continue
        honoured = HOLD_HONOURED.get((trigger, raw, port))
        if honoured and hold_darks > 0:
            out.append(_note(f"instructions[{trigger} -> {raw}.{port}]",
                             honoured))
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
        out.append(_note(
            "instructions[*].message",
            "rule text, severity and 'only once' are not carried through the "
            "compile, so alerts from this flow use the engine's default wording"))
    return rules


def _automation(compiled: dict, out: list[dict]) -> None:
    """Report the automation blocks, none of which ``SequencePlan`` can hold.

    These are NOT equivalent losses and are not reported as if they were.
    Losing ``dusk_flats`` means no flats. Losing ``calibration_queue`` means the
    library does not top up. Losing ``dome`` means a shutter the graph promised
    would close on unsafe does not exist at run time - and ``DomePolicy``'s own
    docstring is emphatic that the roof must never be talked out of shutting.
    That one is ``danger``, and :func:`blocking_reasons` picks it up.
    """
    # THE CAMPAIGN BLOCK REACHES NOTHING, and saying so is the point. The
    # compile emits `campaign: {repeat, until, resume}` for a DUSK WINDOW set to
    # repeat, `SequencePlan` has nowhere to put it, and the run therefore ends at
    # dawn like any other - it does not come back, the cursor is not persisted,
    # and no target is ever marked done in the ledger.
    #
    # An operator who drew a month-long campaign and got one night with no
    # warning is the exact defect this whole list exists to prevent, and this
    # entry was missing when the block was added: the plan dropped it in silence.
    if compiled.get("campaign"):
        camp = compiled["campaign"]
        out.append(_note(
            "campaign",
            f"this flow is a campaign (repeat {camp.get('repeat')}, until "
            f"{camp.get('until')}), and the engine cannot run one yet: this run "
            f"images ONE night and stops at dawn. The capture cursor is not "
            f"persisted, no target is marked done, and the flow will not re-arm "
            f"at the next dusk", "danger"))

    auto = compiled.get("automation") or {}
    if "dome" in auto:
        out.append(_note(
            "automation.dome",
            "the dome policy compiled correctly but the engine cannot act on "
            "it yet, so nothing will bind the dome or close it on an unsafe "
            "reading during this run", "danger"))
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


def inert_nodes(graph: FlowGraph | None) -> list[dict]:
    """Nodes whose parameters reach nothing, reported FROM THE GRAPH.

    They have to come from the graph because they leave no trace in the
    compiled dict: ``compile_plan`` branches on target/pool/capture and walks
    past the rest, so by the time a plan exists there is nothing left to notice
    a GUIDE node's settle time was discarded.

    This is the honest surface for a whole class of dead control. A SLEW node's
    tolerance, an AUTOFOCUS node's step size, an ABORT+PARK node's "park: Yes"
    - all of them are editable, all of them look live, and none of them reaches
    the engine. The last is the one that bites: ``park_when_done`` stays False,
    so a flow that says park and warm leaves the mount tracking and the TEC cold.
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
        out.append(_note(
            f"nodes.{node.type}",
            f"the {node.type.upper()} node's settings do not reach the run - "
            f"the compiler does not carry them into the plan"))
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
    return out


def to_sequence_plan(compiled: dict, graph: FlowGraph | None = None, *,
                     when: float | None = None,
                     cool_to: float | None = None
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

        # 0 is what an untouched rotation field compiles to, and Target treats
        # None as "no constraint". Passing the 0 through commands the rotator to
        # PA 0 and adds 300 s of goto timeout on EVERY target, every night. The
        # asymmetry decides it: a wrong None costs an operator who really wanted
        # PA 0 an unconstrained angle - which on a rig with no rotator is the
        # same behaviour anyway.
        rotation = entry.get("rotation_deg")
        if not rotation:
            if rotation == 0 and "rotation_deg" in entry:
                unmapped.append(_note(
                    f"targets[{name}].rotation_deg",
                    "a rotation of 0 is read as 'no angle constraint'. If you "
                    "meant position angle 0 exactly, the rotator will not be "
                    "commanded to it"))
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

    _automation(compiled, unmapped)
    unmapped.extend(inert_nodes(graph))

    fields: dict = {
        "name": compiled.get("name") or "Flow",
        "targets": targets,
        "instructions": _instructions(compiled, unmapped),
        **plan_extras(compiled),
    }
    if cool_to is not None:
        fields["cool_to"] = float(cool_to)
    plan = SequencePlan.model_validate(fields)
    return plan, unmapped


def blocking_reasons(unmapped: list[dict], *, dome_connected: bool) -> list[dict]:
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
    return [u for u in unmapped if u["key"] == "automation.dome"]
