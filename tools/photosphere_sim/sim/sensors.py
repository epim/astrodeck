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

    ``pose_at`` is sampled every ``1000 / sample_hz`` ms from 0 to the
    trajectory's duration. The first sample is always emitted; every later
    sample is emitted only if ``alpha``, ``beta`` or ``gamma`` has moved by at
    least ``threshold_deg`` from the LAST EMITTED sample (alpha's distance
    computed through the wrap at 360). During a hold every sample is
    bit-identical to the last, so nothing is emitted there: a hold at least
    ``hold_s`` seconds long produces a silent gap of at least that length.
    """
    period_ms = 1000.0 / sample_hz
    n_samples = int(round(traj.duration_ms / period_ms))
    events = []
    last = None
    for k in range(n_samples + 1):
        t = k * period_ms
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
