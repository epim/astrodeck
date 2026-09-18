"""Camera trajectories built from a route definition (CONTRACT.md's Route schema).

A route describes a person holding a phone: a sequence of ``aims`` the view
visits in order, each reached by a smoothstep move and then held, followed by
zero or more vertical ``sweeps``. This module turns that declarative route
into a :class:`Trajectory`: a discrete list of per-frame truth records at a
given fps, the list of holds, the reference position, and a continuous
``pose_at`` usable at any sampling rate (the sensor generator in
:mod:`sim.sensors` calls it at 100 Hz).

Two conventions the route schema leaves to this module:

- Between aims, both azimuth and altitude are carried by the same smoothstep
  parameter ``s(u) = 3u^2 - 2u^3``, ``u = t / move_s``: azimuth moves along
  ``wrap_deg(az1 - az0)`` (the shorter arc, so it can go through north) and
  altitude moves along the plain (unwrapped) difference. This is the only
  reading that keeps every one of the 45 aim-to-aim moves inside the
  smoothstep-continuity bound the plan asks for: the largest of them, 24
  degrees, is far short of the 1 degree per 10 ms sample it demands.
- Reaching a sweep's start point is a hard cut, not a move: the schema defines
  a move only "between consecutive aims", and the final aim before the first
  sweep is ``[0, 89.5]`` while stage A's sweep starts at ``(180, 0)``, roughly
  a 90 degree turn on the sky. Spreading that over the same ``move_s`` used
  for a 24 degree aim-to-aim step would swing at several degrees per 10 ms
  sample, breaking the very continuity bound the aims phase is built to
  satisfy. A cut between two logical phases of a prescribed test route -- the
  aims scan, then a separate vertical sweep -- has no such obligation, so the
  aims-phase's last hold is followed immediately by the sweep's own first
  hold at the new (az, alt), with no move segment between them.

Angular rate is computed analytically per segment, not by finite-differencing
``pose_at``: a hold's rate is exactly 0 (nothing here reads as "very small"
instead), and a move's or a sweep tilt's rate is the closed-form derivative of
the unit view direction, using the sphere's own metric
``sqrt(cos(alt)^2 * (d az/dt)^2 + (d alt/dt)^2)``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

import numpy as np

from .geometry import Basis, look_basis, wrap_deg

__all__ = ["FrameTruth", "Hold", "Trajectory", "build"]


@dataclass(frozen=True)
class FrameTruth:
    """One frame's ground truth: where the camera was and how fast it turned."""

    frame_id: str
    t_capture_ms: int
    position: np.ndarray
    basis: Basis
    az: float
    alt: float
    angular_rate_deg_s: float


@dataclass(frozen=True)
class Hold:
    """One static interval the route holds at, in chronological order."""

    index: int
    az: float
    alt: float
    from_ms: int
    to_ms: int


@dataclass(frozen=True)
class Trajectory:
    """A built route: discrete frames, holds, the reference position, and a
    continuous pose function usable at any sampling rate."""

    frames: list  # list[FrameTruth]
    holds: list  # list[Hold]
    c_ref: np.ndarray
    pose_at: Callable[[float], tuple]
    duration_ms: float


def _position(kind, pivot, radius_m, height_m, lift_m, az, alt):
    """The camera centre for ``kind`` at aim ``(az, alt)``, per CONTRACT.md."""
    if kind == "still":
        return pivot + np.array([0.0, radius_m, height_m])
    if kind == "arc":
        az_r = math.radians(az)
        lift = lift_m * max(0.0, alt) / 90.0
        return pivot + np.array([radius_m * math.sin(az_r), radius_m * math.cos(az_r),
                                 height_m + lift])
    raise ValueError(f"unknown route kind {kind!r}")


def _segments_and_holds(route: dict):
    """The route's timeline as a list of hold/move/tilt segments plus the
    holds list, and the total duration in ms. All boundary times are integer
    milliseconds so later comparisons never drift."""
    move_ms = round(float(route["move_s"]) * 1000.0)
    hold_ms = round(float(route["hold_s"]) * 1000.0)
    aims = route["aims"]
    if not aims:
        raise ValueError("a route needs at least one aim")

    segments: list[dict] = []
    holds: list[Hold] = []
    t = 0

    az0, alt0 = (float(v) for v in aims[0])
    segments.append({"kind": "hold", "t0": t, "t1": t + hold_ms, "az": az0, "alt": alt0})
    holds.append(Hold(index=len(holds), az=az0, alt=alt0, from_ms=t, to_ms=t + hold_ms))
    t += hold_ms

    for i in range(1, len(aims)):
        az_prev, alt_prev = (float(v) for v in aims[i - 1])
        az_cur, alt_cur = (float(v) for v in aims[i])
        d_az = float(wrap_deg(az_cur - az_prev))
        d_alt = alt_cur - alt_prev
        segments.append({"kind": "move", "t0": t, "t1": t + move_ms,
                         "az0": az_prev, "alt0": alt_prev, "d_az": d_az, "d_alt": d_alt})
        t += move_ms
        segments.append({"kind": "hold", "t0": t, "t1": t + hold_ms, "az": az_cur, "alt": alt_cur})
        holds.append(Hold(index=len(holds), az=az_cur, alt=alt_cur, from_ms=t, to_ms=t + hold_ms))
        t += hold_ms

    for sweep in route.get("sweeps", []):
        az = float(sweep["az"])
        alt_from = float(sweep["alt_from"])
        alt_to = float(sweep["alt_to"])
        duration_ms = round(float(sweep["duration_s"]) * 1000.0)
        sweep_hold_ms = round(float(sweep["hold_s"]) * 1000.0)

        # A hard cut into the sweep's own start -- see the module docstring.
        segments.append({"kind": "hold", "t0": t, "t1": t + sweep_hold_ms, "az": az, "alt": alt_from})
        holds.append(Hold(index=len(holds), az=az, alt=alt_from, from_ms=t, to_ms=t + sweep_hold_ms))
        t += sweep_hold_ms

        segments.append({"kind": "tilt", "t0": t, "t1": t + duration_ms, "az": az,
                         "alt0": alt_from, "alt1": alt_to})
        t += duration_ms

        segments.append({"kind": "hold", "t0": t, "t1": t + sweep_hold_ms, "az": az, "alt": alt_to})
        holds.append(Hold(index=len(holds), az=az, alt=alt_to, from_ms=t, to_ms=t + sweep_hold_ms))
        t += sweep_hold_ms

    return segments, holds, t


def _eval(segments, t):
    """``(az, alt, angular_rate_deg_s)`` at time ``t`` (ms).

    Segments are contiguous and half-open, ``[t0, t1)``, except the very last,
    which is closed at both ends so the trajectory's final instant resolves.
    A boundary time therefore belongs to the segment that STARTS there, which
    is what carries a frame at an aim's hold into the next move (or, at the
    aims-to-sweep cut, into the sweep) with no third case to consider.
    """
    last = len(segments) - 1
    t = min(max(t, segments[0]["t0"]), segments[last]["t1"])
    for i, seg in enumerate(segments):
        if t < seg["t1"] or i == last:
            return _eval_segment(seg, t)
    raise AssertionError("unreachable")  # pragma: no cover


def _eval_segment(seg, t):
    kind = seg["kind"]
    if kind == "hold":
        return seg["az"], seg["alt"], 0.0
    if kind == "move":
        dur_ms = seg["t1"] - seg["t0"]
        u = 0.0 if dur_ms <= 0 else min(max((t - seg["t0"]) / dur_ms, 0.0), 1.0)
        s = 3.0 * u * u - 2.0 * u * u * u
        az = seg["az0"] + seg["d_az"] * s
        alt = seg["alt0"] + seg["d_alt"] * s
        duds = 6.0 * u * (1.0 - u)
        ds_dt = 0.0 if dur_ms <= 0 else duds / (dur_ms / 1000.0)
        daz_dt = seg["d_az"] * ds_dt
        dalt_dt = seg["d_alt"] * ds_dt
        rate = math.hypot(daz_dt * math.cos(math.radians(alt)), dalt_dt)
        return az, alt, rate
    if kind == "tilt":
        dur_ms = seg["t1"] - seg["t0"]
        u = 0.0 if dur_ms <= 0 else min(max((t - seg["t0"]) / dur_ms, 0.0), 1.0)
        alt = seg["alt0"] + (seg["alt1"] - seg["alt0"]) * u
        dalt_dt = 0.0 if dur_ms <= 0 else (seg["alt1"] - seg["alt0"]) / (dur_ms / 1000.0)
        return seg["az"], alt, abs(dalt_dt)
    raise ValueError(f"unknown segment kind {kind!r}")  # pragma: no cover


def build(route: dict, fps: int) -> Trajectory:
    """Build a :class:`Trajectory` from a route definition at ``fps``.

    ``frames`` is sampled at ``round(k * 1000 / fps)`` for ``k = 0, 1, ...``
    up to and including the route's total duration. ``pose_at`` is the same
    underlying continuous function and can be called at any time in
    ``[0, duration_ms]``, ms clamped at the ends.
    """
    kind = route["kind"]
    pivot = np.asarray(route["pivot"], dtype=np.float64)
    radius_m = float(route["radius_m"])
    height_m = float(route["height_m"])
    lift_m = float(route.get("lift_m", 0.0))

    segments, holds, duration_ms = _segments_and_holds(route)

    def pose_at(t_ms):
        az, alt, _ = _eval(segments, float(t_ms))
        position = _position(kind, pivot, radius_m, height_m, lift_m, az, alt)
        return position, look_basis(az, alt, 0.0)

    period_ms = 1000.0 / fps
    n_frames = int(round(duration_ms / period_ms)) + 1
    frames = []
    for k in range(n_frames):
        t_capture = int(round(k * 1000.0 / fps))
        az, alt, rate = _eval(segments, t_capture)
        position = _position(kind, pivot, radius_m, height_m, lift_m, az, alt)
        basis = look_basis(az, alt, 0.0)
        frames.append(FrameTruth(frame_id=f"f{k:06d}", t_capture_ms=t_capture,
                                 position=position, basis=basis, az=az, alt=alt,
                                 angular_rate_deg_s=rate))

    c_ref = frames[0].position
    return Trajectory(frames=frames, holds=holds, c_ref=c_ref, pose_at=pose_at,
                      duration_ms=float(duration_ms))
