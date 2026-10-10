# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
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

## Position's carry azimuth (a regression the swing-twist fix introduced, since fixed)

The ``arc`` route's position formula, ``C(t) = pivot + [radius sin(az(t)),
radius cos(az(t)), height + lift * max(0, alt(t)) / 90]``, needs its own
azimuth number to feed to ``sin``/``cos``. The first version of the
swing-twist fix reused ``az`` from ``sky_angles(basis.forward)`` -- correct
for reporting where the camera is actually looking, but wrong here: that
azimuth is computed with ``atan2``, which is only well-conditioned away from
the poles. The zenith-to-sweep-start move's great circle passes close to the
pole partway through, where ``atan2`` can flip its answer by 180 degrees
between two adjacent 100 ms frames even though the physical direction barely
moved -- and unlike a truth record (which only reports the number), feeding
that flip into ``sin``/``cos`` swings the ARC position formula's horizontal
component through a diameter: measured on the shipped route, frames
``f000924`` and ``f000925`` jumped 1.5 m, against a next-largest inter-frame
step of 0.14 m.

The fix keeps the azimuth flip for truth (it is what the camera is really
looking at, and `sky_angles(basis.forward)` is still what `FrameTruth.az`
and `truth/trajectory.jsonl` carry during a move) but gives POSITION its own,
always-continuous "carry azimuth": the same shorter-arc, smoothstep-scaled
interpolation between the two endpoint azimuths the route used before the
swing-twist fix (``az0 + wrap_deg(az1 - az0) * s``). This never calls
``atan2`` and so cannot flip; it agrees with the reported ``az`` exactly at
both endpoints of every move (mod 360), and in between it draws the same
smooth arc through the horizontal plane the pre-swing-twist implementation
always did, independent of whatever the orientation's great circle is doing
near a pole. The altitude fed into the lift term is NOT given the same
treatment: ``alt`` from ``sky_angles(basis.forward)`` has no equivalent
coordinate singularity (``asin`` is continuous throughout the range), so
reusing it is safe and keeps the lift tied to where the camera is actually
tilted.

## Pan routes (kind ``pan``, the panorama scanner's spec 13.3)

A pan route is a person turning on the spot, or at arm's length, at one fixed
altitude: ``pans`` is a list of segments run back to back, each turning
``turn_deg`` at ``speed_deg_s`` with a smoothstep speed ramp of ``ramp_s`` at
each end. It has no moves, no aims and no per-segment holds. The only holds are
the ``start_hold_s`` before the first segment and the ``end_hold_s`` after the
last, and :attr:`Trajectory.holds` lists exactly those two. A reversal between
two segments passes through zero speed for an instant, never for an interval.

- The optical axis is ``look_basis(az(t) + yaw, alt + pitch, roll)``: the
  segment's azimuth and altitude, with roll 0, plus the tremor. Azimuth is the
  distance the speed profile has covered, so the turn is exact: the profile's
  peak is ``turn_deg / (duration - ramp_s)``, which is ``speed_deg_s`` unless
  the duration had to be rounded up to a whole millisecond. A segment shorter
  than its own two ramps cannot reach its speed and raises.
- The camera centre is ``pivot + [r sin az, r cos az, height_m]`` with
  ``r = radius_m`` and ``az`` the nominal azimuth, without the tremor (the arm
  follows the body, not the hand's shake). A radius of 0 is a still pivot.
- ``reference: "axis"`` makes ``c_ref = pivot + [0, 0, height_m]``: the point
  the arc goes round, which no frame sits on for a radius above 0. Without it
  ``c_ref`` is the first frame's position, as for the other kinds.
- Tremor is, per axis, five sinusoids log-spaced over ``band_hz`` whose phases
  and weights come from child 3 of ``SeedSequence(seed).spawn(10)`` (the order
  :mod:`sim.sensors` documents), scaled so the series has the given rms. It
  is faded in and out over half a second at the two ends of the panning
  interval, so the two holds stay exactly still (a still phone reads exact
  zero rates, which the sensor realism cases rely on) and the attitude has no
  jump where the pan begins. All three axes are always drawn, in the order
  yaw, pitch, roll, so changing one axis's rms never moves another's series.
- ``rate_cap_deg_s`` bounds the TOTAL angular rate of the attitude, tremor
  included. The build measures it on a 2 ms grid over the panning interval
  and raises if it is exceeded, rather than handing a scanner a route no hand
  could make.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

import numpy as np

from .geometry import Basis, angle_between, look_basis, sky_angles, wrap_deg

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


def _position(kind, pivot, radius_m, height_m, lift_m, carry_az, alt):
    """The camera centre for ``kind``, per CONTRACT.md.

    ``carry_az`` is the arc's own continuous azimuth parameter (see
    ``_eval_segment``'s "move" branch): during a hold or a tilt it is the
    same value as the reported ``az``, but during a move it is NOT the
    forward-derived azimuth (``sky_angles(basis.forward)``) -- that one can
    legitimately flip by 180 degrees in a single sample when the swing's
    great circle passes near a pole, which is fine for truth (it is what
    the camera is really looking at) but would swing the arc position
    through more than a metre between two adjacent frames if fed into
    ``sin``/``cos`` directly. ``alt`` is still the forward-derived altitude:
    unlike azimuth, altitude has no such coordinate singularity (``asin`` is
    continuous throughout), so reusing it for the lift is safe.
    """
    if kind == "still":
        return pivot + np.array([0.0, radius_m, height_m])
    if kind == "arc":
        az_r = math.radians(carry_az)
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


def _swing_twist(basis_a: Basis, basis_b: Basis, az0, alt0, az1, alt1):
    """Decompose the rotation from ``basis_a`` to ``basis_b`` into a swing
    (the minimal rotation carrying ``forward_a`` to ``forward_b`` along their
    great circle) and a twist (the roll, about the resulting forward, that
    reconciles the swung right/up with ``basis_b``'s own). Returns
    ``(axis, phi_deg, psi_deg)``; ``psi`` is signed via the right-hand rule
    about ``basis_b.forward``. ``az0``/``alt0``/``az1``/``alt1`` are the
    aims the two bases came from, used only to name them if the swing axis
    is undefined.
    """
    fwd_a, fwd_b = basis_a.forward, basis_b.forward
    phi = angle_between(fwd_a, fwd_b)
    if phi < 1e-9:
        axis = basis_a.right  # forward does not move; the axis is moot
    elif phi > 180.0 - 1e-6:
        # forward_a x forward_b vanishes here too (antiparallel vectors have
        # no well-defined perpendicular), which would otherwise divide by
        # ~0 and hand back NaN silently.
        raise ValueError(
            f"aim ({az0}, {alt0}) and aim ({az1}, {alt1}) look in exactly "
            "opposite directions: the swing axis (forward_a x forward_b) "
            "is undefined for an antipodal pair"
        )
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
    must stay inside the 30 deg/s average / 45 deg/s peak budget. Rounded up
    (``math.ceil``, not ``round``), so the declared budget is never exceeded
    by a duration that rounded down to the nearest millisecond short of what
    ``theta`` actually needs.

    Also stores ``az0`` and ``d_az`` (the shorter-arc azimuth delta): the
    ORIENTATION follows the swing-twist basis, but the ARC route's POSITION
    needs its own continuous "carry azimuth" -- see ``_eval_segment`` and the
    module docstring's "Position's carry azimuth" section for why reusing
    the orientation's own (forward-derived) azimuth for position is wrong
    near the pole.
    """
    basis_a = look_basis(az0, alt0, 0.0)
    basis_b = look_basis(az1, alt1, 0.0)
    axis, phi, psi = _swing_twist(basis_a, basis_b, az0, alt0, az1, alt1)
    theta = math.hypot(phi, psi)
    dur_ms = math.ceil(max(float(move_s), theta / 30.0) * 1000.0)
    d_az = float(wrap_deg(az1 - az0))
    segments.append({"kind": "move", "t0": t, "t1": t + dur_ms,
                     "basis_a": basis_a, "axis": axis, "phi": phi, "psi": psi,
                     "theta": theta, "az0": az0, "d_az": d_az})
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
    """``(az, alt, angular_rate_deg_s, basis, carry_az)`` at time ``t`` (ms).

    Segments are contiguous and half-open, ``[t0, t1)``, except the very last,
    which is closed at both ends so the trajectory's final instant resolves.
    A boundary time therefore belongs to the segment that STARTS there, which
    is what carries a frame at the end of one hold into the next move (or
    tilt) with no third case to consider. ``basis`` is returned directly
    (rather than reconstructed by the caller via ``look_basis(az, alt, 0)``)
    because a move's basis carries roll ``look_basis`` alone would discard.
    ``carry_az`` is ``az`` outside a move, and a separate, always-continuous
    azimuth during one -- see ``_eval_segment``'s "move" branch and
    ``_position``.
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
        basis = look_basis(seg["az"], seg["alt"], 0.0)
        return seg["az"], seg["alt"], 0.0, basis, seg["az"]
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
        # The position's own continuous parameter: NOT az above (see
        # _position's docstring for why sky_angles(basis.forward) is wrong
        # to feed into sin/cos here), but the same shorter-arc smoothstep
        # interpolation the whole route used before the swing-twist fix --
        # unaffected by it, because it never touches an atan2 near a pole.
        carry_az = seg["az0"] + seg["d_az"] * s
        return az, alt, rate, basis, carry_az
    if kind == "tilt":
        dur_ms = seg["t1"] - seg["t0"]
        u = 0.0 if dur_ms <= 0 else min(max((t - seg["t0"]) / dur_ms, 0.0), 1.0)
        alt = seg["alt0"] + (seg["alt1"] - seg["alt0"]) * u
        dalt_dt = 0.0 if dur_ms <= 0 else (seg["alt1"] - seg["alt0"]) / (dur_ms / 1000.0)
        basis = look_basis(seg["az"], alt, 0.0)
        return seg["az"], alt, abs(dalt_dt), basis, seg["az"]
    raise ValueError(f"unknown segment kind {kind!r}")  # pragma: no cover


# --- Pan routes (spec 13.3) ---------------------------------------------------

# The seed order is a contract with :mod:`sim.sensors` (see ``_SEED_CHILDREN``
# there): 0 relative, 1 absolute, 2 motion, 3 tremor, 4 frame noise, 5 EIS,
# 6 auto-exposure. This module uses 3 only.
_SEED_CHILDREN = 10
_SEED_TREMOR = 3
_TREMOR_AXES = ("yaw_deg", "pitch_deg", "roll_deg")
_TREMOR_SINUSOIDS = 5
#: How long the tremor takes to come up at the start of the panning interval
#: and to die away at its end. Fixed, not taken from ``ramp_s``, because a
#: route may have ``ramp_s`` 0 and the tremor must still not jump.
_TREMOR_FADE_MS = 500.0
#: The grid the rate cap is measured on. A 3 Hz tremor moves 0.04 of a radian
#: in one step, so a secant on this grid reads the peak rate to within 0.01 %.
_RATE_SCAN_MS = 2.0
#: Half the baseline of the per-frame rate (a central secant 2 ms wide).
_RATE_HALF_MS = 1.0


def _smoothstep(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3.0 - 2.0 * x)


def _attitude_angle_deg(basis_a: Basis, basis_b: Basis) -> float:
    """The angle of the rotation between two FULL attitudes, not forward alone.

    ``atan2`` of the antisymmetric part against the trace, which stays exact
    for the 0.04 degree steps the rate scan takes (an ``acos`` of a trace a
    few ulps under 3 would not).
    """
    r = basis_b.matrix_cam_to_world() @ basis_a.matrix_cam_to_world().T
    sin_a = 0.5 * math.sqrt((r[2, 1] - r[1, 2]) ** 2 + (r[0, 2] - r[2, 0]) ** 2
                            + (r[1, 0] - r[0, 1]) ** 2)
    cos_a = (float(np.trace(r)) - 1.0) / 2.0
    return math.degrees(math.atan2(sin_a, cos_a))


def _pan_segments(route: dict):
    """The route's ``pans`` as back-to-back segments, in integer milliseconds.

    Returns ``(segments, start_ms, end_ms)``: the panning interval is
    ``[start_ms, end_ms]`` and the holds are on either side of it. Each
    segment carries its start azimuth (unwrapped, so a 385 degree turn ends
    at 385), its sign, the turn, its profile's peak speed and its ramp.

    The duration is ``turn / speed + ramp`` seconds, the exact time a profile
    that ramps up and down over ``ramp`` covers ``turn`` at ``speed``. It is
    rounded UP to a whole millisecond, as the move durations are, and the
    peak speed is then re-derived from it so the turn stays exact. Every
    shipped route is already a whole number of milliseconds.
    """
    pans = route.get("pans")
    if not pans:
        raise ValueError("a pan route needs at least one entry in 'pans'")
    start_ms = round(float(route["start_hold_s"]) * 1000.0)
    t = start_ms
    segments: list[dict] = []
    az = alt = None
    for index, pan in enumerate(pans):
        pan_alt = float(pan["alt"])
        if index == 0:
            if "from_az" not in pan:
                raise ValueError("the first pan needs 'from_az'")
            az, alt = float(pan["from_az"]), pan_alt
        else:
            # Segments run back to back: a later one that names where it
            # starts must name where the last one ended, and the altitude is
            # one number per route, not a jump.
            if "from_az" in pan and abs(wrap_deg(float(pan["from_az"]) - az)) > 1e-6:
                raise ValueError(
                    f"pan {index} starts at {pan['from_az']} but the previous pan ends at "
                    f"{az % 360.0}: segments run back to back")
            if abs(pan_alt - alt) > 1e-9:
                raise ValueError(
                    f"pan {index} is at altitude {pan_alt} but the previous pan is at {alt}")
        sign = {"cw": 1.0, "ccw": -1.0}.get(pan.get("direction"))
        if sign is None:
            raise ValueError(f"pan {index}: direction must be 'cw' or 'ccw'")
        turn, speed, ramp = (float(pan["turn_deg"]), float(pan["speed_deg_s"]),
                             float(pan["ramp_s"]))
        if not (turn > 0.0 and speed > 0.0 and ramp >= 0.0):
            raise ValueError(f"pan {index}: turn_deg and speed_deg_s must be positive "
                             "and ramp_s not negative")
        if turn < speed * ramp - 1e-9:
            raise ValueError(
                f"pan {index} turns {turn} degrees, less than the {speed * ramp} its two "
                f"{ramp} s ramps need to reach {speed} deg/s")
        dur_ms = math.ceil((turn / speed + ramp) * 1000.0 - 1e-6)
        segments.append({"t0": t, "t1": t + dur_ms, "az0": az, "alt": alt, "sign": sign,
                         "turn": turn, "ramp": ramp,
                         "peak": turn / (dur_ms / 1000.0 - ramp)})
        t += dur_ms
        az += sign * turn
    return segments, start_ms, t


def _pan_az_alt(segments, t_ms: float):
    """The nominal ``(azimuth, altitude)`` at ``t_ms``: unwrapped degrees.

    The distance a segment has covered is the integral of its speed profile,
    which ramps up as ``S(x)`` over ``ramp`` (``S`` the smoothstep, so the
    integral is ``peak * ramp * (x^3 - x^4 / 2)``), holds ``peak``, and ramps
    down the same way. Before the first segment the view is at its start;
    after the last it stays where that ended.
    """
    first = segments[0]
    if t_ms <= first["t0"]:
        return first["az0"], first["alt"]
    for seg in segments:
        if t_ms < seg["t1"]:
            u = (t_ms - seg["t0"]) / 1000.0
            duration = (seg["t1"] - seg["t0"]) / 1000.0
            ramp, peak = seg["ramp"], seg["peak"]
            if ramp > 0.0 and u < ramp:
                x = u / ramp
                covered = peak * ramp * (x ** 3 - x ** 4 / 2.0)
            elif ramp > 0.0 and u > duration - ramp:
                x = (duration - u) / ramp
                covered = seg["turn"] - peak * ramp * (x ** 3 - x ** 4 / 2.0)
            else:
                covered = peak * (u - ramp / 2.0)
            return seg["az0"] + seg["sign"] * covered, seg["alt"]
    last = segments[-1]
    return last["az0"] + last["sign"] * last["turn"], last["alt"]


def _make_tremor(spec, seed, start_ms: float, end_ms: float):
    """``tremor(t_ms) -> (yaw, pitch, roll)`` in degrees, for a pan route.

    ``spec`` is the route's ``tremor`` block; without one, or with every rms
    0, the tremor is identically zero. Each axis is five sinusoids log-spaced
    over ``band_hz`` with weights uniform in [0.5, 1.5) and phases uniform in
    [0, 2 pi), drawn from child 3 of ``SeedSequence(seed).spawn(10)`` -- the
    phases of an axis first, then its weights, the axes in yaw, pitch, roll
    order, and ALL THREE axes drawn whatever their rms. The sum is scaled so
    its rms (``sqrt(sum w^2 / 2)`` for sinusoids of different frequencies) is
    the axis's ``*_deg``.

    A ``seed`` of None is seed 0, so a bare ``build(route, fps)`` is still
    reproducible; ``cases.build_case`` always passes the case's own seed.
    """
    spec = spec or {}
    rms = [float(spec.get(axis, 0.0)) for axis in _TREMOR_AXES]
    if any(v < 0.0 for v in rms):
        raise ValueError("tremor rms values must not be negative")
    if not any(rms):
        return lambda t_ms: (0.0, 0.0, 0.0)
    band = spec.get("band_hz")
    if band is None or len(band) != 2:
        raise ValueError("tremor needs band_hz: [low, high]")
    lo, hi = (float(v) for v in band)
    if not 0.0 < lo <= hi:
        raise ValueError("tremor band_hz must be two positive frequencies, low to high")

    rng = np.random.default_rng(
        np.random.SeedSequence(0 if seed is None else seed).spawn(_SEED_CHILDREN)[_SEED_TREMOR])
    omega = [2.0 * math.pi * float(f) for f in np.geomspace(lo, hi, _TREMOR_SINUSOIDS)]
    axes = []
    for axis_rms in rms:
        phases = rng.uniform(0.0, 2.0 * math.pi, _TREMOR_SINUSOIDS)
        weights = rng.uniform(0.5, 1.5, _TREMOR_SINUSOIDS)
        scale = axis_rms / math.sqrt(float(np.sum(weights ** 2)) / 2.0)
        axes.append([(scale * float(w), om, float(p))
                     for w, om, p in zip(weights, omega, phases)])
    fade_ms = min(_TREMOR_FADE_MS, (end_ms - start_ms) / 2.0)

    def tremor(t_ms):
        gain = _smoothstep((t_ms - start_ms) / fade_ms) * _smoothstep((end_ms - t_ms) / fade_ms)
        if gain <= 0.0:
            return (0.0, 0.0, 0.0)
        t_s = t_ms / 1000.0
        return tuple(gain * sum(a * math.sin(om * t_s + p) for a, om, p in axis)
                     for axis in axes)

    return tremor


def _build_pan(route: dict, fps: int, seed) -> Trajectory:
    """The :class:`Trajectory` of a ``pan`` route; see the module docstring."""
    pivot = np.asarray(route["pivot"], dtype=np.float64)
    radius_m = float(route["radius_m"])
    height_m = float(route["height_m"])
    if radius_m < 0.0:
        raise ValueError("radius_m must not be negative")
    reference = route.get("reference")
    if reference not in (None, "axis"):
        raise ValueError(f"reference must be 'axis' or absent, got {reference!r}")
    rate_cap = float(route["rate_cap_deg_s"])

    segments, start_ms, end_ms = _pan_segments(route)
    duration_ms = end_ms + round(float(route["end_hold_s"]) * 1000.0)
    tremor = _make_tremor(route.get("tremor"), seed, start_ms, end_ms)

    def pose_at(t_ms):
        t = min(max(float(t_ms), 0.0), float(duration_ms))
        az, alt = _pan_az_alt(segments, t)
        yaw, pitch, roll = tremor(t)
        az_r = math.radians(az)
        position = pivot + np.array([radius_m * math.sin(az_r), radius_m * math.cos(az_r),
                                     height_m])
        return position, look_basis(az + yaw, alt + pitch, roll)

    # The cap is checked on the whole attitude, tremor included, before any
    # frame is built: a route over its cap is a mistake in the route.
    peak, peak_at = 0.0, start_ms
    previous = pose_at(start_ms)[1]
    t = float(start_ms)
    while t < end_ms:
        step = min(_RATE_SCAN_MS, end_ms - t)
        t += step
        current = pose_at(t)[1]
        rate = _attitude_angle_deg(previous, current) / (step / 1000.0)
        if rate > peak:
            peak, peak_at = rate, t
        previous = current
    if peak > rate_cap:
        raise ValueError(
            f"route {route.get('name')!r}: the total angular rate, tremor included, reaches "
            f"{peak:.2f} deg/s at {peak_at / 1000.0:.2f} s, above its rate_cap_deg_s {rate_cap}")

    def rate_at(t_ms):
        # Zero EXACTLY in the holds (a closed interval, so the instant a pan
        # begins or ends counts as still: its speed is 0 there anyway), the
        # central secant of the full attitude inside the panning interval.
        if t_ms <= start_ms or t_ms >= end_ms:
            return 0.0
        lo = max(float(start_ms), t_ms - _RATE_HALF_MS)
        hi = min(float(end_ms), t_ms + _RATE_HALF_MS)
        return _attitude_angle_deg(pose_at(lo)[1], pose_at(hi)[1]) / ((hi - lo) / 1000.0)

    period_ms = 1000.0 / fps
    n_frames = int(math.floor(duration_ms / period_ms + 1e-9)) + 1
    frames = []
    for k in range(n_frames):
        t_capture = int(round(k * 1000.0 / fps))
        position, basis = pose_at(t_capture)
        az, alt = sky_angles(basis.forward)
        frames.append(FrameTruth(frame_id=f"f{k:06d}", t_capture_ms=t_capture,
                                 position=position, basis=basis, az=az, alt=alt,
                                 angular_rate_deg_s=rate_at(t_capture)))

    holds = []
    first, last = segments[0], segments[-1]
    if start_ms > 0:
        holds.append(Hold(index=len(holds), az=first["az0"] % 360.0, alt=first["alt"],
                          from_ms=0, to_ms=start_ms))
    if duration_ms > end_ms:
        holds.append(Hold(index=len(holds),
                          az=(last["az0"] + last["sign"] * last["turn"]) % 360.0,
                          alt=last["alt"], from_ms=end_ms, to_ms=duration_ms))

    if reference == "axis":
        c_ref = pivot + np.array([0.0, 0.0, height_m])
    else:
        c_ref = frames[0].position
    return Trajectory(frames=frames, holds=holds, c_ref=c_ref, pose_at=pose_at,
                      duration_ms=float(duration_ms))


def build(route: dict, fps: int, seed: int | None = None) -> Trajectory:
    """Build a :class:`Trajectory` from a route definition at ``fps``.

    ``frames`` is sampled at ``round(k * 1000 / fps)`` for ``k = 0, 1, ...``,
    stopping at the last capture time that does not exceed the route's total
    duration (a move's duration is no longer always a whole multiple of the
    frame period, now that it can be stretched past ``move_s``). ``pose_at``
    is the same underlying continuous function and can be called at any time
    in ``[0, duration_ms]``, ms clamped at the ends.

    ``seed`` is the case's own seed. Only a ``pan`` route reads it (for its
    tremor, see :func:`_make_tremor`); the other kinds are deterministic
    without one and build exactly as they always have.
    """
    if route["kind"] == "pan":
        return _build_pan(route, fps, seed)
    kind = route["kind"]
    pivot = np.asarray(route["pivot"], dtype=np.float64)
    radius_m = float(route["radius_m"])
    height_m = float(route["height_m"])
    lift_m = float(route.get("lift_m", 0.0))

    segments, holds, duration_ms = _segments_and_holds(route)

    def pose_at(t_ms):
        az, alt, _, basis, carry_az = _eval(segments, float(t_ms))
        position = _position(kind, pivot, radius_m, height_m, lift_m, carry_az, alt)
        return position, basis

    period_ms = 1000.0 / fps
    n_frames = int(math.floor(duration_ms / period_ms + 1e-9)) + 1
    frames = []
    for k in range(n_frames):
        t_capture = int(round(k * 1000.0 / fps))
        az, alt, rate, basis, carry_az = _eval(segments, t_capture)
        position = _position(kind, pivot, radius_m, height_m, lift_m, carry_az, alt)
        frames.append(FrameTruth(frame_id=f"f{k:06d}", t_capture_ms=t_capture,
                                 position=position, basis=basis, az=az, alt=alt,
                                 angular_rate_deg_s=rate))

    c_ref = frames[0].position
    return Trajectory(frames=frames, holds=holds, c_ref=c_ref, pose_at=pose_at,
                      duration_ms=float(duration_ms))
