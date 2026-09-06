"""Pure evaluator for the conditional sequencer (PRO-3).

``evaluate_instructions`` is a pure function — no engine, hub, or clock access:
``ctx`` carries the per-frame snapshot and ``fire_state`` carries the
per-instruction bookkeeping the engine holds between calls. Determinism + purity
make the subtle edge/re-arm/once/cooldown/only_target interaction exhaustively
unit-testable (see ``server/tests/test_instructions_eval.py``).

No-double-fire semantics:

* **Level triggers** (``on_hfr_above``/``on_guide_rms_above``) are EDGE-triggered:
  they fire only on the rising crossing (was at/below threshold, now above) and
  re-arm when the value drops back to/below threshold. A stuck-high value must
  never re-fire every frame. ``armed`` lives in :class:`FireRecord`.
* ``on_frame_rejected`` / ``on_target_complete`` are per-event.
* ``at_time`` is implicitly once: fires at the first boundary with
  ``now_ts >= parse_hhmm(at_time)``.

Cross-cutting gates, applied to EVERY trigger in order: ``enabled`` →
``only_target`` match → trigger eligibility (+ edge) → ``once`` → ``cooldown_s``.
Only when all pass is a :class:`FiredAction` emitted, in author list order.

Compound conditions (control-flow expansion, ADDITIVE): when ``i.when`` is set
it OVERRIDES the flat ``trigger``. The whole 1-level ``all``/``any`` expression
is treated as ONE level and edge-triggered exactly like a level trigger — it
fires on the false→true crossing and re-arms when it evaluates decisively
false. An indeterminate expression (a term whose metric is unreadable and which
is needed to decide the result) is NOT eligible and leaves ``armed`` untouched,
mirroring the flat ``v is None`` path. ``i.when is None`` ⇒ nothing below the
compound branch is reached, so existing plans run byte-identical.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from .models import Condition, Instruction

# LEVEL triggers are edge-triggered: they fire on the rising crossing and re-arm
# when the condition goes decisively false.
#
# The four SKY/RIG conditions are levels too, and putting them here is what gives
# them the right behaviour for free. "It is cloudy" is a state, not an event: a
# rule that fired on every frame while the cloud sat overhead would hold, hold,
# hold, and a resume rule that fired every clear frame would fight it. Edge means
# each transition acts once.
#
# It also gives them the STALENESS rule at no cost. `_eval_predicate` returns
# None when a metric is unreadable, and the level branch leaves `armed` untouched
# on None - so a cloud reading that has gone stale fires nothing and re-arms
# nothing. A stale "clear" resuming a run into an overcast is the failure this
# design has to prevent, and it prevents it by treating "I do not know" as
# neither.
_LEVEL_TRIGGERS = (
    "on_hfr_above", "on_guide_rms_above",
    "on_clouds_in", "on_clouds_clear", "on_unsafe", "on_panel_ready",
)

# flat TriggerKind -> compound PredicateKind. ONE leaf implementation serves
# both paths (`_eval_predicate`), so the two can never drift.
_PREDICATE_OF = {
    "on_hfr_above": "hfr_above",
    "on_guide_rms_above": "guide_rms_above",
    "on_frame_rejected": "frame_rejected",
    "on_target_complete": "target_complete",
    "at_time": "at_time",
    # --- sky / rig conditions (ADDITIVE) ---------------------------------
    "on_clouds_in": "clouds_in",
    "on_clouds_clear": "clouds_clear",
    "on_unsafe": "unsafe",
    "on_panel_ready": "panel_ready",
    "on_altitude_floor": "altitude_floor",
}


@dataclass
class TriggerContext:
    now_ts: float
    frame_hfr: float | None = None
    # GN-08: the HFR measured on the first ACCEPTED frame after the most
    # recent autofocus (or, if the flow never autofocuses, the run's own first
    # accepted frame — see engine._run_steps). None until the engine has seen
    # one such frame, or after a fresh autofocus invalidates the old baseline.
    # A `hfr_above` rule with `relative=True` reads this instead of a fixed
    # pixel value; None makes it indeterminate, the same as an unreadable
    # `frame_hfr`.
    focus_baseline_hfr: float | None = None
    guide_rms: float | None = None
    frame_rejected: bool = False
    target_complete: bool = False
    active_target: str | None = None
    # --- sky / rig conditions (ADDITIVE) ---------------------------------
    #
    # ALL THREE ARE TRI-STATE, and None is the whole point: it means "nobody
    # can currently say", not "no". The engine sets None when a reading is
    # absent or older than its freshness budget, and the level branch below
    # treats None as indeterminate - fires nothing, re-arms nothing.
    #
    # The alternative, defaulting an unknown sky to False, would mean a rig that
    # had stopped judging its frames looked exactly like a clear night, and a
    # cloud hold would resume into an overcast on the strength of a reading
    # nobody took.
    #
    # `cloudy` carries BOTH cloud triggers. One metric, two edges - so they can
    # never disagree with each other, only with the sky.
    cloudy: bool | None = None
    unsafe: bool | None = None
    panel_ready: bool | None = None
    # NOT tri-state, and not a level. The engine sets this True at the one frame
    # boundary where it has just measured the active target below its own floor
    # and is about to set that target aside; there is no "unreadable" case,
    # because an unreadable altitude is not a floor hit and the engine returns
    # without building this context at all.
    altitude_floor: bool = False


@dataclass
class FireRecord:
    fired_count: int = 0
    last_fire_ts: float = 0.0
    armed: bool = True                    # level triggers: True when currently at/below threshold


@dataclass
class FiredAction:
    instruction_id: str
    action: str
    message: str
    level: str
    # jump destination for run_target / skip_target; None for every other action
    # (so the existing five actions are constructed exactly as before).
    target_arg: str | None = None


def parse_hhmm(at_time: str | None, now_ts: float) -> float | None:
    """"HH:MM" 24h local -> the epoch ts of the occurrence NEAREST ``now_ts``
    (within ±12 h), or None if malformed/absent. Pure.

    THE NEAREST OCCURRENCE, NOT TODAY'S. This resolved HH:MM against the
    current CALENDAR day, which splits every observing night down the middle:
    an evening run that starts at 21:00 resolved a "03:00" rule to 03:00 THAT
    MORNING — eighteen hours in the past — so the rule was true on the very
    first sub. A user who wrote "At 03:00 → stop the session", and whose UI
    rendered exactly that, had the night end immediately. The mirror case is as
    bad and quieter: a "23:00" rule in a run that started at 00:30 resolves to
    23:00 tomorrow and never fires at all.

    The ±12 h snap is the same rule ``schedule._clock_time_near_now`` already
    applies to window boundaries, delegated rather than reimplemented so the
    sequencer's two notions of "tonight at HH:MM" cannot drift apart. Parsing
    stays here because ``strptime`` rejects malformed input the split-on-colon
    form accepts.
    """
    if not at_time:
        return None
    try:
        parsed = time.strptime(at_time, "%H:%M")
    except (ValueError, TypeError):
        return None
    from .schedule import _clock_time_near_now
    try:
        return _clock_time_near_now(
            f"{parsed.tm_hour:02d}:{parsed.tm_min:02d}", now_ts)
    except (ValueError, OverflowError):
        return None


def _eval_predicate(kind: str, threshold: float, at_time: str | None,
                    ctx: TriggerContext, relative: bool = False) -> bool | None:
    """Evaluate ONE leaf predicate as a LEVEL. ``None`` = indeterminate (the
    metric is unreadable this frame) — callers must not treat it as False.
    Pure; carries no arm/once/cooldown state (those are the caller's gates).

    ``relative`` (GN-08) only changes ``hfr_above``: ``threshold`` is then a
    FACTOR of ``ctx.focus_baseline_hfr`` rather than an absolute pixel value,
    and a missing baseline (no autofocus/accepted frame yet this run) is
    indeterminate — the same "I do not know" treatment an unreadable
    ``frame_hfr`` already gets, so a relative watchdog cannot fire on nothing.
    """
    if kind in ("hfr_above", "guide_rms_above"):
        v = ctx.frame_hfr if kind == "hfr_above" else ctx.guide_rms
        if v is None:
            return None
        if kind == "hfr_above" and relative:
            if ctx.focus_baseline_hfr is None:
                return None
            return v > threshold * ctx.focus_baseline_hfr
        return v > threshold
    if kind == "frame_rejected":
        return ctx.frame_rejected
    if kind == "target_complete":
        return ctx.target_complete
    if kind == "altitude_floor":
        # A per-event flag like the two above, not a level: the engine builds a
        # context carrying it only at the boundary where it has just measured
        # the target below its floor, and the target leaves the rotation in the
        # same breath. There is no unreadable case to pass through as None -
        # an altitude nobody could read is not a floor hit.
        return ctx.altitude_floor
    if kind == "at_time":
        t = parse_hhmm(at_time, ctx.now_ts)
        return None if t is None else ctx.now_ts >= t
    # Sky and rig conditions. Each passes an unknown straight through as None so
    # the caller's level branch leaves the rule armed - the reading, not the
    # rule, is what is missing.
    if kind == "clouds_in":
        return None if ctx.cloudy is None else ctx.cloudy
    if kind == "clouds_clear":
        # The SAME metric as clouds_in, negated. Deriving it rather than giving
        # "clear" its own input is what stops the two triggers reading different
        # skies: with one source, a hold and its resume cannot both be armed.
        return None if ctx.cloudy is None else (not ctx.cloudy)
    if kind == "unsafe":
        return None if ctx.unsafe is None else ctx.unsafe
    if kind == "panel_ready":
        return None if ctx.panel_ready is None else ctx.panel_ready
    return None                              # unknown kind — never eligible


def _eval_condition(cond: Condition, ctx: TriggerContext) -> bool | None:
    """1-level ``all``/``any`` over leaf predicates -> tri-state level.

    Indeterminate rule: a ``None`` term only matters when it is still NEEDED to
    decide the expression — ``all`` short-circuits on any decisive False,
    ``any`` on any decisive True. Mirrors the flat unreadable-metric path."""
    vals = [_eval_predicate(t.kind, t.threshold, t.at_time, ctx, t.relative)
            for t in cond.terms]
    if cond.op == "all":
        if any(v is False for v in vals):
            return False
        return None if any(v is None for v in vals) else True
    if any(v is True for v in vals):         # op == "any"
        return True
    return None if any(v is None for v in vals) else False


def evaluate_instructions(
    instructions: list[Instruction],
    ctx: TriggerContext,
    fire_state: dict[str, FireRecord],
) -> tuple[list[FiredAction], dict[str, FireRecord]]:
    """Pure. Returns (actions to fire in list order, updated fire_state). No I/O."""
    fired: list[FiredAction] = []
    for i in instructions:
        if not i.enabled:
            continue
        rec = fire_state.setdefault(i.id, FireRecord())
        # only_target gate: skip while a different target is active.
        if i.only_target is not None and i.only_target != ctx.active_target:
            continue

        # `when` OVERRIDES the flat trigger when present (one active path per
        # rule). None => the flat PRO-3 path below, untouched.
        compound = i.when is not None
        is_level = (not compound) and i.trigger in _LEVEL_TRIGGERS
        eligible = False
        if compound:
            # the WHOLE expression is edge-triggered as one level, so the
            # no-double-fire guarantee generalizes unchanged.
            c = _eval_condition(i.when, ctx)
            if c is None:
                eligible = False                 # indeterminate — leave armed as-is
            elif not c:
                rec.armed = True                 # decisively false -> re-arm
                eligible = False
            else:
                eligible = rec.armed             # fire only on the rising edge
        elif is_level:
            v = _eval_predicate(_PREDICATE_OF[i.trigger], i.threshold,
                                i.at_time, ctx, i.relative)
            if v is None:
                eligible = False                 # unreadable metric — leave armed as-is
            elif not v:
                rec.armed = True                 # dropped to/below -> re-arm
                eligible = False
            else:                                # v > threshold
                eligible = rec.armed             # fire only on the rising edge
        elif i.trigger == "at_time":
            v = _eval_predicate("at_time", i.threshold, i.at_time, ctx)
            # implicit-once: fires at the first boundary at/after the time.
            eligible = bool(v) and rec.fired_count == 0
        else:
            eligible = bool(_eval_predicate(_PREDICATE_OF.get(i.trigger, ""),
                                            i.threshold, i.at_time, ctx))

        if not eligible:
            continue
        if i.once and rec.fired_count > 0:
            continue
        if i.cooldown_s > 0 and (ctx.now_ts - rec.last_fire_ts) < i.cooldown_s:
            continue

        fired.append(FiredAction(i.id, i.action, i.message, i.level,
                                 i.target_arg))
        rec.fired_count += 1
        rec.last_fire_ts = ctx.now_ts
        if is_level or compound:
            rec.armed = False
    return fired, fire_state
