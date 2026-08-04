"""Cooler warm-down policy — the ramp that replaces cutting the TEC dead.

WHY THIS MODULE EXISTS (the bug it was written for, 2026-08-04)
---------------------------------------------------------------
Every "warm the camera" path in the product was a single ``set_cooler(False)``:
the Warm button (api/app.py), the unattended ``abort_park_warm`` wind-down
(sequence/engine.py) and the NINA bridge (devices/nina.py, which sent NINA's
``warm?minutes=0`` — "0" is NINA's warm-IMMEDIATELY). The owner heard the TEC
stop and measured the sensor on the live camera straight afterwards:

    8.3 → 9.4 → 11.0 → 11.8 °C in about 40 s  (≈ 5 °C/min)

That is not a controlled warm-up; that is a cold sensor equalising with the
room. Cooled astro cameras are warmed gradually on every vendor's advice: the
risks are thermal shock across the sensor/cold-finger stack and condensation
inside the chamber when a cold sensor meets un-cooled air — the latter getting
worse as the desiccant ages. It is a hardware-longevity defect, and the path
that ran it most often was the one nobody is awake to watch.

The policy lives here (not in hub.py) for two reasons: the schedule arithmetic
is pure and therefore directly testable, and ``devices/nina.py`` needs the same
numbers to tell NINA how long ITS internal ramp should take, without importing
the hub (which imports nina — a cycle).

THE DEFAULT RATE, AND WHAT IT IS NOT
------------------------------------
There is no published per-sensor figure to copy. Vendors say "warm gradually"
and stop. What we do have is two measurements and one cross-check:

  * Measured on this rig (above): ~5 °C/min of UNCONTROLLED equalisation with
    the TEC switched off. That is the number a ramp has to beat — a "ramp" no
    slower than free-running equalisation is controlling nothing at all.
  * NINA's Warm Camera default is a 10-MINUTE duration. From a typical −10 °C
    setpoint to a +15 °C ambient that is 25 °C in 10 min ≈ 2.5 °C/min. A sanity
    check, not gospel: it is a DURATION, so the rate it implies moves with the
    delta (the same 10 minutes is 1 °C/min from a −5 °C setpoint on a cold
    night, and 4 °C/min from −25 °C in summer).

2 °C/min sits under both: ~12.5 min for that same 25 °C delta, comfortably
slower than the passive rate, so the TEC is genuinely doing the work the whole
way down rather than being outrun by physics. It is a POLICY DEFAULT chosen to
be defensibly slow — NOT a measured safe limit for any particular sensor, which
is precisely why ``AppConfig.cooling.warm_rate_c_per_min`` exists for anyone who
has a vendor figure for their camera.
"""
from __future__ import annotations

import math

#: Default ramp rate (°C per minute). See the module docstring for where this
#: number comes from and, just as importantly, where it does not.
WARM_RATE_C_PER_MIN = 2.0

#: How often the ramp advances the setpoint. 15 s at 2 °C/min is a 0.5 °C step:
#: small enough that no single step is itself a shock, long enough that a slow
#: USB driver is not being hammered, and short enough that a cancel (user hits
#: Cool, or the rig is torn down) is acted on inside a quarter of a minute.
WARM_STEP_S = 15.0

#: How far the commanded setpoint may run AHEAD of the measured sensor before
#: we conclude the sensor is no longer following it. Two different things are
#: being caught: a TEC that cannot keep up (do not open a gap that turns the
#: final switch-off into the very jump we are avoiding), and — the common case —
#: a setpoint that has climbed past the real ambient, where the cooler has
#: nothing left to do and the sensor simply stops rising with it. The second is
#: the ramp's natural END, which is why reaching this lead FINISHES the ramp
#: rather than stalling it: at that point the TEC is already idle and switching
#: it off changes nothing thermally.
WARM_MAX_LEAD_C = 3.0

#: Consecutive lead breaches before we act on them. One is noise — a driver can
#: report a stale temperature for a poll or two right after a setpoint change.
WARM_LEAD_CHECKS = 2

#: Ambient assumed when nothing on the rig can measure it. Deliberately warm
#: (a heated indoor observatory) rather than a guess at tonight's air: guessing
#: LOW would end the ramp early and cut the TEC while the sensor was still cold,
#: which is the bug. Guessing HIGH costs nothing — the lead check above ends the
#: ramp as soon as the sensor stops following, i.e. at the REAL ambient.
WARM_FALLBACK_AMBIENT_C = 20.0

#: A delta smaller than this is not worth ramping: the sensor is already at
#: (or above) ambient, so there is nothing to protect it from. Switch off and
#: say why, rather than run a 20-second theatre ramp.
WARM_MIN_DELTA_C = 1.0

#: How long a FINISHED warm stays visible on status after it ends, so the
#: Capture screen can say "warm complete" instead of the panel silently
#: reverting to a plain "Off" that looks identical to the bug.
WARM_STATE_RETAIN_S = 180.0

#: Bound on any single cooler/temperature device call inside the ramp. Mirrors
#: sequence.engine.COOLER_CMD_TIMEOUT_S — a wedged driver must not park a
#: background task forever.
WARM_CMD_TIMEOUT_S = 30.0


def warm_rate_c_per_min(cfg: object | None = None) -> float:
    """The configured ramp rate, clamped to something physically sane.

    ``cfg`` is an ``AppConfig`` (or anything with a ``.cooling.warm_rate_c_per_min``);
    None / a config too old to have the block falls back to the module default,
    so an existing config.json on disk keeps working untouched."""
    rate = WARM_RATE_C_PER_MIN
    cooling = getattr(cfg, "cooling", None)
    value = getattr(cooling, "warm_rate_c_per_min", None)
    if value is not None:
        try:
            rate = float(value)
        except (TypeError, ValueError):
            rate = WARM_RATE_C_PER_MIN
    # Upper clamp: 20 °C/min is already faster than the ~5 °C/min free-running
    # equalisation we measured, so anything above it is a ramp in name only —
    # refuse to pretend. Lower clamp keeps a fat-fingered 0 from producing an
    # infinite ramp (or a divide-by-zero in the ETA).
    return max(0.1, min(20.0, rate))


def warm_ramp_enabled(cfg: object | None = None) -> bool:
    """Whether the ramp runs at all. Off ⇒ the pre-fix behaviour (cut the TEC),
    which is a legitimate choice for someone with a camera whose driver mishandles
    setpoint changes — but the hub LOGS that it took the shortcut, because a
    silent fallback to the bug is worse than the bug."""
    cooling = getattr(cfg, "cooling", None)
    value = getattr(cooling, "warm_ramp", None)
    return True if value is None else bool(value)


def warm_ambient_c(cfg: object | None, start_c: float,
                   measured_ambient_c: float | None = None) -> tuple[float, str]:
    """The temperature the ramp is climbing TO, and where that number came from.

    Priority: an explicit ``cooling.warm_ambient_c`` (someone who knows their
    observatory) → a rig-measured ambient (a backend that reports heat-sink /
    ambient temperature) → the fallback constant. The provenance string rides
    along to the log and the UI: "warming to 20 °C (assumed)" and "warming to
    11 °C (measured)" are different claims and must not look alike.

    The returned value is never below ``start_c`` — a ramp that runs DOWNWARD
    is not a warm-up."""
    explicit = getattr(getattr(cfg, "cooling", None), "warm_ambient_c", None)
    if explicit is not None:
        return max(float(explicit), start_c), "configured"
    if measured_ambient_c is not None:
        return max(float(measured_ambient_c), start_c), "measured"
    return max(WARM_FALLBACK_AMBIENT_C, start_c), "assumed"


def warm_next_setpoint_c(setpoint_c: float, ambient_c: float, rate_c_per_min: float,
                         step_s: float = WARM_STEP_S) -> float:
    """One step up the ramp, never past ``ambient_c``.

    Advancing from the PREVIOUS setpoint (rather than recomputing from a start
    time) is deliberate: when a step is skipped — because the sensor is lagging,
    or because a device call took 20 s — the schedule pauses instead of catching
    up in one big jump. Erring slow is free; erring fast is the bug."""
    return min(float(ambient_c), float(setpoint_c) + rate_c_per_min * (step_s / 60.0))


def warm_duration_s(start_c: float, ambient_c: float,
                    rate_c_per_min: float = WARM_RATE_C_PER_MIN) -> float:
    """How long the whole ramp should take, for the ETA and for NINA's duration.
    Zero when there is nothing to climb."""
    delta = max(0.0, float(ambient_c) - float(start_c))
    return delta / max(0.1, rate_c_per_min) * 60.0


def warm_minutes(start_c: float, ambient_c: float,
                 rate_c_per_min: float = WARM_RATE_C_PER_MIN) -> int:
    """The same duration in whole minutes, for backends that own their ramp and
    take a duration instead of a rate (NINA's ``/equipment/camera/warm?minutes=``).

    Rounds UP and never returns 0: 0 is NINA's warm-IMMEDIATELY sentinel — the
    exact value that produced this bug — so a short ramp must never round into
    it."""
    return max(1, math.ceil(warm_duration_s(start_c, ambient_c, rate_c_per_min) / 60.0))


def warm_is_pointless(start_c: float, ambient_c: float) -> bool:
    """True when the sensor is already at/above ambient: there is no thermal
    gradient to protect, so ramping would be theatre."""
    return (float(ambient_c) - float(start_c)) < WARM_MIN_DELTA_C
