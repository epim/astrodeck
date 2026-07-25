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

_LEVEL_TRIGGERS = ("on_hfr_above", "on_guide_rms_above")

# flat TriggerKind -> compound PredicateKind. ONE leaf implementation serves
# both paths (`_eval_predicate`), so the two can never drift.
_PREDICATE_OF = {
    "on_hfr_above": "hfr_above",
    "on_guide_rms_above": "guide_rms_above",
    "on_frame_rejected": "frame_rejected",
    "on_target_complete": "target_complete",
    "at_time": "at_time",
}


@dataclass
class TriggerContext:
    now_ts: float
    frame_hfr: float | None = None
    guide_rms: float | None = None
    frame_rejected: bool = False
    target_complete: bool = False
    active_target: str | None = None


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
    """"HH:MM" 24h local -> today's epoch ts in LOCAL time, or None if
    malformed/absent. Pure (uses ``now_ts`` for today's date only)."""
    if not at_time:
        return None
    try:
        parsed = time.strptime(at_time, "%H:%M")
    except (ValueError, TypeError):
        return None
    lt = time.localtime(now_ts)
    try:
        # today's local date at the parsed H:M; isdst=-1 lets mktime infer DST.
        stamp = time.struct_time((
            lt.tm_year, lt.tm_mon, lt.tm_mday,
            parsed.tm_hour, parsed.tm_min, 0,
            lt.tm_wday, lt.tm_yday, -1))
        return time.mktime(stamp)
    except (ValueError, OverflowError):
        return None


def _eval_predicate(kind: str, threshold: float, at_time: str | None,
                    ctx: TriggerContext) -> bool | None:
    """Evaluate ONE leaf predicate as a LEVEL. ``None`` = indeterminate (the
    metric is unreadable this frame) — callers must not treat it as False.
    Pure; carries no arm/once/cooldown state (those are the caller's gates)."""
    if kind in ("hfr_above", "guide_rms_above"):
        v = ctx.frame_hfr if kind == "hfr_above" else ctx.guide_rms
        return None if v is None else v > threshold
    if kind == "frame_rejected":
        return ctx.frame_rejected
    if kind == "target_complete":
        return ctx.target_complete
    if kind == "at_time":
        t = parse_hhmm(at_time, ctx.now_ts)
        return None if t is None else ctx.now_ts >= t
    return None                              # unknown kind — never eligible


def _eval_condition(cond: Condition, ctx: TriggerContext) -> bool | None:
    """1-level ``all``/``any`` over leaf predicates -> tri-state level.

    Indeterminate rule: a ``None`` term only matters when it is still NEEDED to
    decide the expression — ``all`` short-circuits on any decisive False,
    ``any`` on any decisive True. Mirrors the flat unreadable-metric path."""
    vals = [_eval_predicate(t.kind, t.threshold, t.at_time, ctx)
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
                                i.at_time, ctx)
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
