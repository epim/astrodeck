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
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from .models import Instruction

_LEVEL_TRIGGERS = ("on_hfr_above", "on_guide_rms_above")


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

        is_level = i.trigger in _LEVEL_TRIGGERS
        eligible = False
        if is_level:
            v = ctx.frame_hfr if i.trigger == "on_hfr_above" else ctx.guide_rms
            if v is None:
                eligible = False                 # unreadable metric — leave armed as-is
            elif v <= i.threshold:
                rec.armed = True                 # dropped to/below -> re-arm
                eligible = False
            else:                                # v > threshold
                eligible = rec.armed             # fire only on the rising edge
        elif i.trigger == "on_frame_rejected":
            eligible = ctx.frame_rejected
        elif i.trigger == "on_target_complete":
            eligible = ctx.target_complete
        elif i.trigger == "at_time":
            t = parse_hhmm(i.at_time, ctx.now_ts)
            # implicit-once: fires at the first boundary at/after the time.
            eligible = t is not None and ctx.now_ts >= t and rec.fired_count == 0

        if not eligible:
            continue
        if i.once and rec.fired_count > 0:
            continue
        if i.cooldown_s > 0 and (ctx.now_ts - rec.last_fire_ts) < i.cooldown_s:
            continue

        fired.append(FiredAction(i.id, i.action, i.message, i.level))
        rec.fired_count += 1
        rec.last_fire_ts = ctx.now_ts
        if is_level:
            rec.armed = False
    return fired, fire_state
