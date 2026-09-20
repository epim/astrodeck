"""Change-driven sensor events and frame delivery records, from a Trajectory.

Neither function samples the rendered images: :func:`orientation_events`
samples the trajectory's own continuous ``pose_at`` at a fixed rate and
converts each sample with :func:`sim.geometry.device_orientation`, exactly as
a real device's orientation sensor would report its own attitude regardless
of what the camera happens to be looking at; :func:`frame_records` only
reads the fields the trajectory already computed for each frame.
"""

from __future__ import annotations

from .geometry import device_orientation, wrap_deg

__all__ = ["frame_records", "orientation_events"]


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
