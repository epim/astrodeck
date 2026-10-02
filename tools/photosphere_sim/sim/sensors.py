# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Change-driven sensor events and frame delivery records, from a Trajectory.

Neither function samples the rendered images: :func:`orientation_events`
samples the trajectory's own continuous ``pose_at`` at a fixed rate and
converts each sample with :func:`sim.geometry.device_orientation`, exactly as
a real device's orientation sensor would report its own attitude regardless
of what the camera happens to be looking at; :func:`frame_records` only
reads the fields the trajectory already computed for each frame.
"""

from __future__ import annotations

import math

import numpy as np

from .geometry import device_orientation, wrap_deg

__all__ = ["frame_records", "motion_events", "orientation_events"]


def orientation_events(traj, threshold_deg: float = 0.1, sample_hz: float = 100,
                       latency_ms: int = 20) -> list:
    """Change-driven ``orientation`` observation records.

    Sampled on the fixed ``1000 / sample_hz`` ms grid from 0 to the
    trajectory's duration, PLUS every segment boundary instant: every hold's
    ``from_ms`` and ``to_ms``, which is the same set of instants as every
    move's start and end, since a move is always exactly the gap between two
    holds. The boundary samples matter because a move's duration need not be
    a whole multiple of the sampling period (CONTRACT.md's Route schema
    move-duration rule uses a real angular quantity): without them, an
    orientation change still accumulating at the tail of a move could first
    cross the emission threshold on the grid tick immediately AFTER a hold
    has already begun, reporting an event a few ms inside a hold that should
    have been silent (see https://github.com/epim/astrodeck/issues/55).
    Sampling exactly at ``from_ms`` uses that instant's own pose -- the
    hold's, per the usual half-open segment convention (a boundary belongs
    to the segment that starts there; see ``trajectory._eval``) -- so the
    same accumulated change is instead seen, and if warranted emitted, AT
    the hold's first instant, never strictly inside it.

    The first sample overall is always emitted; every later sample is
    emitted only if ``alpha``, ``beta`` or ``gamma`` has moved by at least
    ``threshold_deg`` from the LAST EMITTED sample (alpha's distance
    computed through the wrap at 360). During a hold every sample is
    bit-identical to the last, so nothing is emitted there: a hold at least
    ``hold_s`` seconds long still produces a silent gap of at least that
    length.
    """
    period_ms = 1000.0 / sample_hz
    n_samples = int(round(traj.duration_ms / period_ms))
    times = {k * period_ms for k in range(n_samples + 1)}
    for hold in traj.holds:
        times.add(float(hold.from_ms))
        times.add(float(hold.to_ms))

    events = []
    last = None
    for t in sorted(times):
        _, basis = traj.pose_at(t)
        alpha, beta, gamma = device_orientation(basis)
        if last is None or _changed(alpha, beta, gamma, last, threshold_deg):
            t_event_ms = int(round(t))
            events.append({
                "kind": "orientation",
                "t_event_ms": t_event_ms,
                "t_receive_ms": t_event_ms + latency_ms,
                "alpha": alpha,
                "beta": beta,
                "gamma": gamma,
                "absolute": True,
            })
            last = (alpha, beta, gamma)
    return events


def _changed(alpha, beta, gamma, last, threshold_deg):
    last_alpha, last_beta, last_gamma = last
    d_alpha = abs(wrap_deg(alpha - last_alpha))
    d_beta = abs(beta - last_beta)
    d_gamma = abs(gamma - last_gamma)
    return d_alpha >= threshold_deg or d_beta >= threshold_deg or d_gamma >= threshold_deg


def frame_records(traj, present_latency_ms: int = 60) -> list:
    """``frame`` observation records for every frame in ``traj.frames``.

    ``width`` and ``height`` are not filled in here: this function only knows
    the trajectory, and the trajectory was built with no reference to a
    camera. ``cases.build_case`` adds them from the case's camera block before
    writing ``observations.jsonl``.
    """
    return [
        {
            "kind": "frame",
            "frame_id": frame.frame_id,
            "t_capture_ms": frame.t_capture_ms,
            "t_present_ms": frame.t_capture_ms + present_latency_ms,
            "file": f"frames/{frame.frame_id}.png",
        }
        for frame in traj.frames
    ]


def motion_events(traj, sample_hz: float = 60, latency_ms: int = 10,
                  noise_deg_s: float = 0.0, seed: int = 0) -> list:
    """``motion`` observation records: the phone's rotation rate, continuously.

    The second witness (issues #63, #76, #105). The orientation stream is
    CHANGE-DRIVEN, so a phone holding still goes silent and its samples alone
    cannot tell a steady view from a lost sensor; ``devicemotion`` fires at a
    fixed rate whether or not anything moved, which is what lets a reading be
    vouched for during a hold. That difference is the whole reason this exists,
    so these are emitted on every tick of the grid and never thresholded.

    WHAT THE THREE NUMBERS ARE. `DeviceMotionEvent.rotationRate` is the rate of
    turn about the DEVICE's own axes, not the rate of change of the Euler
    angles, and the two are not the same thing: d(alpha)/dt diverges as beta
    approaches the poles while the phone is turning perfectly steadily. That
    matters here more than anywhere, because the route this stream exists to
    measure ends at the zenith - differentiating the angles would have reported
    a violent spin through the one hold #76 is about.

    So the rotation is taken between the attitudes at ``t - dt`` and
    ``t + dt``, as a rotation of the device frame: ``R1^T R2`` with the device
    axes as columns, read back as an axis and an angle. The result is already
    in device coordinates, and W3C's alpha, beta and gamma are the rates about
    z, x and y. Its MAGNITUDE is the trajectory's own ``angular_rate_deg_s`` by
    construction, which is the quantity the scanner reads (`MotionStability`
    compares `hypot(a, b, c)` against `QUIET_RATE_DEG_S`).

    ``noise_deg_s`` is the profile knob the issue asks for: Gaussian per axis,
    so a case can carry a gyro too noisy to vouch for anything as well as a
    clean one. At 0 the stream is exact and a hold reads exactly zero, which no
    real device does - a case that wants to grade `QUIET_RATE_DEG_S` itself has
    to set it.
    """
    period_ms = 1000.0 / sample_hz
    n_samples = int(round(traj.duration_ms / period_ms))
    rng = np.random.default_rng(seed)
    # Half a sampling period each side, clamped into the trajectory: a central
    # difference measures the turn the phone is making AT t rather than the one
    # it has just finished, and the clamp keeps the first and last samples one
    # sided instead of reading a pose that does not exist.
    dt_ms = period_ms / 2.0
    events = []
    for k in range(n_samples + 1):
        t = k * period_ms
        t0 = max(0.0, t - dt_ms)
        t1 = min(traj.duration_ms, t + dt_ms)
        if t1 <= t0:
            continue
        rate = _device_rate_deg_s(traj, t0, t1)
        if noise_deg_s > 0:
            rate = rate + rng.normal(0.0, noise_deg_s, 3)
        t_event_ms = int(round(t))
        events.append({
            "kind": "motion",
            "t_event_ms": t_event_ms,
            "t_receive_ms": t_event_ms + latency_ms,
            "rate": {"alpha": float(rate[0]), "beta": float(rate[1]),
                     "gamma": float(rate[2])},
        })
    return events


def _device_axes(basis) -> np.ndarray:
    """The device's x, y, z as COLUMNS in world coordinates.

    W3C's device frame is x to the right of the screen, y up the screen and z
    out of the screen toward the user. The camera looks along ``forward``, out
    of the BACK, so the device z is ``-forward``.
    """
    return np.column_stack([basis.right, basis.up, -basis.forward])


def _device_rate_deg_s(traj, t0_ms: float, t1_ms: float) -> np.ndarray:
    """The mean rotation rate about the device's own axes over ``[t0, t1]``."""
    _, b0 = traj.pose_at(t0_ms)
    _, b1 = traj.pose_at(t1_ms)
    relative = _device_axes(b0).T @ _device_axes(b1)
    # Axis and angle of a rotation matrix. `trace = 1 + 2 cos(angle)`, and the
    # antisymmetric part carries the axis times `sin(angle)`. Clipped because a
    # trace a hair outside [-1, 3] from rounding would make `arccos` NaN, which
    # would travel silently into the recording.
    cos_angle = (float(np.trace(relative)) - 1.0) / 2.0
    angle = math.acos(max(-1.0, min(1.0, cos_angle)))
    seconds = (t1_ms - t0_ms) / 1000.0
    if angle < 1e-12 or seconds <= 0:
        return np.zeros(3)
    axis = np.array([relative[2, 1] - relative[1, 2],
                     relative[0, 2] - relative[2, 0],
                     relative[1, 0] - relative[0, 1]])
    norm = float(np.linalg.norm(axis))
    if norm < 1e-12:
        # A half turn within one sampling period: the axis is unrecoverable
        # from the antisymmetric part and no route here produces one. Reported
        # as the magnitude about z rather than as zero, so it cannot read as a
        # still phone.
        return np.array([math.degrees(angle) / seconds, 0.0, 0.0])
    return (axis / norm) * (math.degrees(angle) / seconds)
