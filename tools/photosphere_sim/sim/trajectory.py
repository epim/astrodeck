"""Camera trajectories built from a route definition (CONTRACT.md's Route schema).

A route describes a person holding a phone: a sequence of ``aims`` the view
visits in order, each reached by a smoothstep move and then held, followed by
zero or more vertical ``sweeps``. This module turns that declarative route
into a :class:`Trajectory`: a discrete list of per-frame truth records at a
given fps, the list of holds, the reference position, and a continuous
``pose_at`` usable at any sampling rate (the sensor generator in
:mod:`sim.sensors` calls it at 100 Hz).

A move interpolates the FULL camera attitude as a rotation, not azimuth and
altitude as two independent numbers -- see "Why a rotation, not two angles"
below for why that distinction is load-bearing. Two conventions the route
schema leaves to this module:

- A move from attitude A to attitude B (both ``look_basis(az, alt, 0)``) is a
  **swing-twist decomposition**, the standard way to interpolate one 3D
  orientation into another while pinning down what the "forward" axis does
  along the way:

  - The **swing** is the minimal rotation carrying ``A.forward`` to
    ``B.forward``: axis ``n = normalize(A.forward x B.forward)`` (perpendicular
    to both), angle ``phi = angle_between(A.forward, B.forward)``. Applying
    ``s * phi`` of this swing to ``A`` for ``s`` in ``[0, 1]`` is mathematically
    identical to spherically interpolating ``A.forward`` and ``B.forward``
    directly, because ``n`` is perpendicular to every point on that arc: this
    is what keeps forward on the great circle EXACTLY, not approximately, at
    every ``s``, including every altitude-changing move, not just the
    same-altitude ones.
  - The **twist** is the residual roll about the now-current forward axis
    needed to turn the swung right/up into ``B``'s own right/up exactly.
    Applying ``s * psi`` of this twist (about the swing's OWN result at
    fraction ``s``, not a fixed axis) spreads that roll smoothly across the
    whole move rather than snapping it on at the end.
  - Both use the same smoothstep parameter ``s(u) = 3u^2 - 2u^3``,
    ``u = t / duration``, and the same duration, so they complete together.

- A move's duration is ``move_s``, unless ``theta = sqrt(phi^2 + psi^2)``
  exceeds ``30 * move_s`` degrees, in which case it takes ``theta / 30``
  seconds instead -- CONTRACT.md's Route schema rule, restated here: no move
  ever averages more than 30 degrees per second, where "no move" now means
  the FULL attitude, swing and twist combined, not forward alone. ``phi`` and
  ``psi`` act along mutually perpendicular instantaneous axes throughout the
  move (``n`` is perpendicular to forward at every ``s``, and the twist axis
  IS forward), so the combined instantaneous rate is exactly
  ``ds/dt * sqrt(phi^2 + psi^2)`` -- a single closed form, not an
  approximation, which is what makes ``theta/30`` an exact (not merely safe)
  bound on the average, with the usual smoothstep peak-over-average ratio of
  1.5 bounding the peak at ``45 deg/s`` whenever duration is set by ``theta``.

## Why a rotation, not two angles (and not a single whole-attitude slerp either)

An earlier version of this module (see git history, "no move averages more
than 30 degrees per second") interpolated azimuth and altitude independently
under one shared smoothstep parameter, and measured a move's duration from
``angle_between`` of the two ``forward`` vectors alone. Review
docs/ui-rebuild/17-photosphere-implementation-review.md P2 found that this
times one path (the forward-only great-circle angle) while moving the camera
along a DIFFERENT one (independent az/alt interpolation, which need not
follow that great circle), so the declared 30/45 deg/s budget was not
actually the bound honoured by the frames produced; the zenith-to-horizon
transition in the shipped ``arc075`` route measured 50 deg/s average and 85
deg/s peak against the declared 30/45. It also asked that ROLL be counted:
``look_basis``'s ``right = sky_vector(az + 90, 0)`` is defined from azimuth
alone, ignoring altitude entirely, so it can swing independently of how much
``forward`` actually moves -- most visibly near the pole, where a large
azimuth change can correspond to almost no physical motion of forward, or
(as below) the reverse.

The obvious fix -- represent each endpoint attitude as a single rotation and
SLERP the whole thing along the one shortest path connecting them in SO(3) --
turns out not to keep forward on the great circle either, except when the
relative rotation's axis happens to be perpendicular to forward (true for a
same-altitude, pure-yaw move, false in general). This is not a rounding
error: measured directly for this route's shipped last-aim-to-sweep
transition, ``(az=0, alt=89.5) -> (az=180, alt=0)``, a single whole-attitude
slerp carries forward up to 45.25 degrees off the direct great-circle arc
between the two endpoints (and 0.3-0.77 degrees off it for the three ordinary
cross-band moves, which also change altitude) -- because the relative
rotation that exactly maps one FULL basis onto the other is not, in general,
the same rotation that minimally carries forward alone from one direction to
the other. The swing-twist decomposition above is the standard resolution:
it deliberately separates "get forward to the right place, on the great
circle, using the perpendicular-axis rotation that is guaranteed to trace
it" from "then apply whatever roll is still needed", rather than asking one
single rotation to do both jobs along a path that need not respect either
requirement on its own.

One consequence worth stating plainly, because it looks surprising: the
``(0, 89.5) -> (180, 0)`` transition's swing is only ``phi = 90.5`` degrees
(matching the intuitive "pitch down about the east axis"), but its twist is
``psi = -180`` degrees, because ``look_basis``'s azimuth-only ``right``
convention hands this move a full reversal (east becomes west) on top of the
pitch. ``theta = sqrt(90.5^2 + 180^2)`` = about 201.5 degrees, not the ~90
the swing alone might suggest, and the transition's shipped duration follows
from that larger number. That the fix makes this move slower, not the same
length, is the point: it is roll near the zenith that this fix is for.

Angular rate for a hold or a sweep tilt is still the closed-form derivative
of the unit view direction (a hold's rate is exactly 0; a tilt's is
``|d alt/dt|``, since it moves altitude alone at a fixed azimuth and roll,
which is already a pure single-axis rotation with no swing/twist split
needed).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

import numpy as np

from .geometry import Basis, angle_between, look_basis, sky_angles

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


def _rotate_vector(v, axis, angle_deg):
    """Rodrigues' formula: rotate ``v`` by ``angle_deg`` about unit ``axis``.

    Well-conditioned for any angle, including exactly +-180 degrees: unlike
    extracting an axis FROM a rotation matrix (singular at 180, since the
    antisymmetric part vanishes there), applying a GIVEN axis has no such
    singularity.
    """
    theta = math.radians(angle_deg)
    c, s = math.cos(theta), math.sin(theta)
    return v * c + np.cross(axis, v) * s + axis * float(np.dot(axis, v)) * (1.0 - c)


def _rotate_basis(basis: Basis, axis, angle_deg) -> Basis:
    return Basis(
        right=_rotate_vector(basis.right, axis, angle_deg),
        up=_rotate_vector(basis.up, axis, angle_deg),
        forward=_rotate_vector(basis.forward, axis, angle_deg),
    )


def _unit_perp(v, n):
    """``v`` projected perpendicular to unit ``n``, renormalised to unit length."""
    p = v - float(np.dot(v, n)) * n
    return p / np.linalg.norm(p)


def _swing_twist(basis_a: Basis, basis_b: Basis):
    """Decompose the rotation from ``basis_a`` to ``basis_b`` into a swing
    (the minimal rotation carrying ``forward_a`` to ``forward_b`` along their
    great circle) and a twist (the roll, about the resulting forward, that
    reconciles the swung right/up with ``basis_b``'s own). Returns
    ``(axis, phi_deg, psi_deg)``; ``psi`` is signed via the right-hand rule
    about ``basis_b.forward``.
    """
    fwd_a, fwd_b = basis_a.forward, basis_b.forward
    phi = angle_between(fwd_a, fwd_b)
    if phi < 1e-9:
        axis = basis_a.right  # forward does not move; the axis is moot
    else:
        axis = np.cross(fwd_a, fwd_b)
        axis = axis / np.linalg.norm(axis)
    swung_right = _rotate_vector(basis_a.right, axis, phi)
    r_swung = _unit_perp(swung_right, fwd_b)
    r_b = _unit_perp(basis_b.right, fwd_b)
    cos_psi = float(np.clip(np.dot(r_swung, r_b), -1.0, 1.0))
    sin_psi = float(np.dot(np.cross(r_swung, r_b), fwd_b))
    psi = math.degrees(math.atan2(sin_psi, cos_psi))
    return axis, phi, psi


def _append_move(segments, t, az0, alt0, az1, alt1, move_s):
    """Append one move segment from ``(az0, alt0)`` to ``(az1, alt1)``
    starting at ``t``, and return the time it ends.

    Duration: ``move_s``, or ``theta / 30`` seconds if that is longer, where
    ``theta = sqrt(phi^2 + psi^2)`` combines the swing and twist -- see the
    module docstring for why the full attitude, not forward alone, is what
    must stay inside the 30 deg/s average / 45 deg/s peak budget.
    """
    basis_a = look_basis(az0, alt0, 0.0)
    basis_b = look_basis(az1, alt1, 0.0)
    axis, phi, psi = _swing_twist(basis_a, basis_b)
    theta = math.hypot(phi, psi)
    dur_ms = round(max(float(move_s), theta / 30.0) * 1000.0)
    segments.append({"kind": "move", "t0": t, "t1": t + dur_ms,
                     "basis_a": basis_a, "axis": axis, "phi": phi, "psi": psi,
                     "theta": theta})
    return t + dur_ms


def _segments_and_holds(route: dict):
    """The route's timeline as a list of hold/move/tilt segments plus the
    holds list, and the total duration in ms. All boundary times are integer
    milliseconds so later comparisons never drift."""
    move_s = float(route["move_s"])
    hold_ms = round(float(route["hold_s"]) * 1000.0)
    aims = route["aims"]
    if not aims:
        raise ValueError("a route needs at least one aim")

    segments: list[dict] = []
    holds: list[Hold] = []
    t = 0

    current_az, current_alt = (float(v) for v in aims[0])
    segments.append({"kind": "hold", "t0": t, "t1": t + hold_ms,
                     "az": current_az, "alt": current_alt})
    holds.append(Hold(index=len(holds), az=current_az, alt=current_alt, from_ms=t, to_ms=t + hold_ms))
    t += hold_ms

    for i in range(1, len(aims)):
        az_cur, alt_cur = (float(v) for v in aims[i])
        t = _append_move(segments, t, current_az, current_alt, az_cur, alt_cur, move_s)
        segments.append({"kind": "hold", "t0": t, "t1": t + hold_ms, "az": az_cur, "alt": alt_cur})
        holds.append(Hold(index=len(holds), az=az_cur, alt=alt_cur, from_ms=t, to_ms=t + hold_ms))
        t += hold_ms
        current_az, current_alt = az_cur, alt_cur

    for sweep in route.get("sweeps", []):
        az = float(sweep["az"])
        alt_from = float(sweep["alt_from"])
        alt_to = float(sweep["alt_to"])
        duration_ms = round(float(sweep["duration_s"]) * 1000.0)
        sweep_hold_ms = round(float(sweep["hold_s"]) * 1000.0)

        # The same move rule carries the view from wherever the aims phase
        # (or a previous sweep) left off into this sweep's start: no move is
        # ever a hard cut, however far it has to reach.
        t = _append_move(segments, t, current_az, current_alt, az, alt_from, move_s)
        segments.append({"kind": "hold", "t0": t, "t1": t + sweep_hold_ms, "az": az, "alt": alt_from})
        holds.append(Hold(index=len(holds), az=az, alt=alt_from, from_ms=t, to_ms=t + sweep_hold_ms))
        t += sweep_hold_ms

        segments.append({"kind": "tilt", "t0": t, "t1": t + duration_ms, "az": az,
                         "alt0": alt_from, "alt1": alt_to})
        t += duration_ms

        segments.append({"kind": "hold", "t0": t, "t1": t + sweep_hold_ms, "az": az, "alt": alt_to})
        holds.append(Hold(index=len(holds), az=az, alt=alt_to, from_ms=t, to_ms=t + sweep_hold_ms))
        t += sweep_hold_ms
        current_az, current_alt = az, alt_to

    return segments, holds, t


def _eval(segments, t):
    """``(az, alt, angular_rate_deg_s, basis)`` at time ``t`` (ms).

    Segments are contiguous and half-open, ``[t0, t1)``, except the very last,
    which is closed at both ends so the trajectory's final instant resolves.
    A boundary time therefore belongs to the segment that STARTS there, which
    is what carries a frame at the end of one hold into the next move (or
    tilt) with no third case to consider. ``basis`` is returned directly
    (rather than reconstructed by the caller via ``look_basis(az, alt, 0)``)
    because a move's basis carries roll ``look_basis`` alone would discard.
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
        return seg["az"], seg["alt"], 0.0, look_basis(seg["az"], seg["alt"], 0.0)
    if kind == "move":
        dur_ms = seg["t1"] - seg["t0"]
        u = 0.0 if dur_ms <= 0 else min(max((t - seg["t0"]) / dur_ms, 0.0), 1.0)
        s = 3.0 * u * u - 2.0 * u * u * u
        duds = 6.0 * u * (1.0 - u)
        ds_dt = 0.0 if dur_ms <= 0 else duds / (dur_ms / 1000.0)
        swung = _rotate_basis(seg["basis_a"], seg["axis"], s * seg["phi"])
        basis = _rotate_basis(swung, swung.forward, s * seg["psi"])
        az, alt = sky_angles(basis.forward)
        rate = ds_dt * seg["theta"]
        return az, alt, rate, basis
    if kind == "tilt":
        dur_ms = seg["t1"] - seg["t0"]
        u = 0.0 if dur_ms <= 0 else min(max((t - seg["t0"]) / dur_ms, 0.0), 1.0)
        alt = seg["alt0"] + (seg["alt1"] - seg["alt0"]) * u
        dalt_dt = 0.0 if dur_ms <= 0 else (seg["alt1"] - seg["alt0"]) / (dur_ms / 1000.0)
        return seg["az"], alt, abs(dalt_dt), look_basis(seg["az"], alt, 0.0)
    raise ValueError(f"unknown segment kind {kind!r}")  # pragma: no cover


def build(route: dict, fps: int) -> Trajectory:
    """Build a :class:`Trajectory` from a route definition at ``fps``.

    ``frames`` is sampled at ``round(k * 1000 / fps)`` for ``k = 0, 1, ...``,
    stopping at the last capture time that does not exceed the route's total
    duration (a move's duration is no longer always a whole multiple of the
    frame period, now that it can be stretched past ``move_s``). ``pose_at``
    is the same underlying continuous function and can be called at any time
    in ``[0, duration_ms]``, ms clamped at the ends.
    """
    kind = route["kind"]
    pivot = np.asarray(route["pivot"], dtype=np.float64)
    radius_m = float(route["radius_m"])
    height_m = float(route["height_m"])
    lift_m = float(route.get("lift_m", 0.0))

    segments, holds, duration_ms = _segments_and_holds(route)

    def pose_at(t_ms):
        az, alt, _, basis = _eval(segments, float(t_ms))
        position = _position(kind, pivot, radius_m, height_m, lift_m, az, alt)
        return position, basis

    period_ms = 1000.0 / fps
    n_frames = int(math.floor(duration_ms / period_ms + 1e-9)) + 1
    frames = []
    for k in range(n_frames):
        t_capture = int(round(k * 1000.0 / fps))
        az, alt, rate, basis = _eval(segments, t_capture)
        position = _position(kind, pivot, radius_m, height_m, lift_m, az, alt)
        frames.append(FrameTruth(frame_id=f"f{k:06d}", t_capture_ms=t_capture,
                                 position=position, basis=basis, az=az, alt=alt,
                                 angular_rate_deg_s=rate))

    c_ref = frames[0].position
    return Trajectory(frames=frames, holds=holds, c_ref=c_ref, pose_at=pose_at,
                      duration_ms=float(duration_ms))
