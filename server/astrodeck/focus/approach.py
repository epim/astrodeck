"""Which way the drawtube is moving when it ARRIVES.

One rule, in one place, for every focuser move the product makes outside a
sweep's own inner loop: an OUTWARD target is passed by
``config.focus.approach_overshoot_steps`` and then returned to, so the last leg
of every move is inward and the backlash is taken up the same way every time.
Inward targets are reached directly — there is no slack to take up in that
direction.

WHY IT IS A MODULE RATHER THAN A CLOSURE. It was born inside
``run_native_autofocus`` (2026-09-08), where it fixed a 39-step final error on a
replay of the rig's EAF. But a sweep is not the only thing that moves this
focuser, and the moves OUTSIDE it are the ones nothing measures afterwards:
the sequence engine's per-filter offset move is 18 to 20 steps on a focuser with
about 40 steps of slack, so an outward offset very likely turns the motor and
never moves the tube at all — and then L and G shoot at R and B's focus, with a
log line saying the offset was applied and no frame anywhere disagreeing. A
correction that only the code that invented it obeys is not a correction.

See ``config.FocusConfig`` for the measurement the shipped 200 comes from, and
``focus.native`` for the sweep that reads it per run.
"""
from __future__ import annotations

from ..devices.base import Focuser


def configured_overshoot() -> int:
    """``config.focus.approach_overshoot_steps``, clamped to >= 0.

    Returns 0 — the rule disabled, moves exactly as they were before
    2026-09-08 — when the config cannot be read. An unreadable config is not a
    reason to refuse to move a focuser: the overshoot is an improvement on the
    move, not a precondition for it.
    """
    try:
        from ..config import config_store
        return max(0, int(config_store.cfg().focus.approach_overshoot_steps))
    except Exception:      # noqa: BLE001 - see above
        return 0


async def approach(focuser: Focuser, position: int, *, overshoot: int,
                   current: int | None = None) -> None:
    """Move ``focuser`` to ``position``, ARRIVING FROM ABOVE when it is outward.

    ``current`` is where the focuser is now, when the caller already knows —
    a sweep tracks its own position and a caller that has just read one passes
    it, both to save a device round trip and because on the EAF a read answers
    with the commanded count anyway, so it cannot see the slack this exists
    for. ``None`` reads it back (only when the answer can change what happens:
    an ``overshoot`` of 0 has nothing to decide).

    ``overshoot`` 0 disables the rule. The extra leg is also dropped entirely
    when there is no travel left above the target to use — clamped to
    ``focuser.max_position``, and skipped when that clamp leaves nothing.
    Refusing the move instead would turn a mechanical nicety into a failure.
    """
    position = int(position)
    overshoot = int(overshoot)
    if overshoot > 0:
        if current is None:
            current = await focuser.get_position()
        if position > int(current):
            ceiling = getattr(focuser, "max_position", None)
            over = position + overshoot
            if ceiling is not None:
                over = min(over, int(ceiling))
            if over > position:
                await focuser.move_to(over)
    await focuser.move_to(position)
