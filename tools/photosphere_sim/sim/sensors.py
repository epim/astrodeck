# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Change-driven sensor events and frame delivery records, from a Trajectory.

Neither function samples the rendered images: :func:`orientation_events`
samples the trajectory's own continuous ``pose_at`` at a fixed rate and
converts each sample with :func:`sim.geometry.device_orientation`, exactly as
a real device's orientation sensor would report its own attitude regardless
of what the camera happens to be looking at; :func:`frame_records` only
reads the fields the trajectory already computed for each frame.

A case definition with a ``realism`` block (CONTRACT.md's case definition
additions; the panorama scanner's spec 13.2) takes the second path in this
module instead, through :func:`realism_readings`: a relative and an absolute
orientation stream with the faults a phone's fused sensors really have, a
gyro with scale error, bias and rounding, and frame delivery times with a
camera-pipeline lag. A case without one never reaches that code, and its
records are byte for byte what the functions above have always written.
"""

from __future__ import annotations

import math

import numpy as np

from .geometry import device_orientation, sky_angles, wrap_deg

__all__ = ["REALISM_DEFAULTS", "frame_records", "motion_events", "orientation_events",
           "realism_readings", "resolve_realism"]


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


def frame_records(traj, present_latency_ms: int = 60, capture_time=None) -> list:
    """``frame`` observation records for every frame in ``traj.frames``.

    ``width`` and ``height`` are not filled in here: this function only knows
    the trajectory, and the trajectory was built with no reference to a
    camera. ``cases.build_case`` adds them from the case's camera block before
    writing ``observations.jsonl``.

    ``capture_time`` is a realism block's ``{"mode", "lag_ms"}`` (see
    :data:`REALISM_DEFAULTS`); without one, ``t_capture_ms`` is the exposure
    time and ``t_present_ms`` is ``present_latency_ms`` after it, as always.
    With one, the exposure time is still the trajectory's ``t_capture_ms`` and
    what the page is TOLD about the frame is:

    - ``delivery``: the exposure plus ``lag_ms``. Chromium's default I420 path
      stamps ``captureTime`` when the frame is delivered to the page, after
      the ISP and any stabilisation, not at the exposure;
    - ``sensor``: the exposure itself (the zero-copy path);
    - ``none``: ``null``, a frame whose metadata carries no ``captureTime``.

    The presentation time is never earlier than ``present_latency_ms`` after
    the exposure, nor than 10 ms after a ``captureTime`` that is present.
    """
    records = []
    for frame in traj.frames:
        exposure_ms = frame.t_capture_ms
        t_present_ms = exposure_ms + present_latency_ms
        t_capture_ms = exposure_ms
        if capture_time is not None:
            mode = capture_time["mode"]
            if mode == "delivery":
                t_capture_ms = exposure_ms + int(round(capture_time.get("lag_ms", 50)))
            elif mode == "sensor":
                t_capture_ms = exposure_ms
            elif mode == "none":
                t_capture_ms = None
            else:
                raise ValueError(f"capture_time mode must be delivery, sensor or none, got {mode!r}")
            if t_capture_ms is not None:
                t_present_ms = max(t_present_ms, t_capture_ms + 10)
        records.append({
            "kind": "frame",
            "frame_id": frame.frame_id,
            "t_capture_ms": t_capture_ms,
            "t_present_ms": t_present_ms,
            "file": f"frames/{frame.frame_id}.png",
        })
    return records


def motion_events(traj, sample_hz: float = 60, latency_ms: int = 10,
                  noise_deg_s: float = 0.0, seed=0, *, scale_err: float = 0.0,
                  bias_deg_s: float = 0.0, round_deg_s: float = 0.0) -> list:
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
    device x, y and z, in that order, which is the order ``rate`` is written in
    below (a portrait phone turning on the spot at pitch 0 reads it all in
    beta; a pure pitch reads it in alpha). An earlier version of this text
    said z, x and y, which the code never did; issue #897 proposed changing
    the code to match it, and its premise is inverted. Its MAGNITUDE is the
    trajectory's own ``angular_rate_deg_s`` by construction, which is the
    quantity the scanner reads (`MotionStability` compares `hypot(a, b, c)`
    against `QUIET_RATE_DEG_S`).

    ``noise_deg_s`` is the profile knob the issue asks for: Gaussian per axis,
    so a case can carry a gyro too noisy to vouch for anything as well as a
    clean one. At 0 the stream is exact and a hold reads exactly zero, which no
    real device does - a case that wants to grade `QUIET_RATE_DEG_S` itself has
    to set it.

    ``seed`` is anything ``numpy.random.default_rng`` accepts, so a realism
    block can hand in one child of its ``SeedSequence``. The three keyword
    arguments are that block's ``motion`` faults and are all off by default:
    ``scale_err`` multiplies the true rate by ``1 + scale_err`` (the same gyro
    scale error the relative orientation stream carries), ``bias_deg_s`` is
    added to each axis, and ``round_deg_s`` rounds each axis to a multiple of
    it AFTER the noise, which is what Chromium does (0.1 deg/s): a still phone
    with a quiet gyro then reads exact zeros on most samples, not on none.
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
        if scale_err:
            rate = rate * (1.0 + scale_err)
        if bias_deg_s:
            rate = rate + bias_deg_s
        if noise_deg_s > 0:
            rate = rate + rng.normal(0.0, noise_deg_s, 3)
        if round_deg_s:
            rate = np.array([_round_to(float(v), round_deg_s) for v in rate])
        t_event_ms = int(round(t))
        events.append({
            "kind": "motion",
            "t_event_ms": t_event_ms,
            "t_receive_ms": t_event_ms + int(round(latency_ms)),
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


# ---------------------------------------------------------------------------
# Realism: the faults a phone's fused sensors really have (panorama spec 13.2)
# ---------------------------------------------------------------------------

# What a realism block means when it leaves a key out. Every number here is the
# spec's stated default and a later task re-derives them from measured device
# data, so they are data in one place rather than literals in the code below.
# ``spikes`` is off by default; a block that turns it on without naming both
# keys gets the spec's 0.2 Hz of 15 degrees for the missing one.
_SPIKE_DEFAULTS = {"rate_hz": 0.2, "size_deg": 15.0}

REALISM_DEFAULTS = {
    "gyro_scale_err": 0.02,
    "relative": {"yaw_zero_deg": "random", "drift_deg_min": 2.0, "noise_deg": 0.05,
                 "quant_deg": 0.1, "threshold_deg": 0.1, "pump_hz": 60,
                 "latency_ms": 5, "spikes": None},
    "absolute": {"bias_deg": 0.0, "declination_deg": 0.0,
                 "sinusoid": {"amp_deg": 2.0, "phase_deg": 40.0},
                 "noise": {"sigma_deg": 3.0, "tau_s": 5.0}, "steps": [],
                 "tilt_noise_deg": 0.05, "quant_deg": 0.1, "threshold_deg": 0.1,
                 "pump_hz": 60, "latency_ms": 5, "spikes": None},
    "motion": {"bias_deg_s": 0.05, "noise_deg_s": 0.03, "round_deg_s": 0.1,
               "pump_hz": 60, "latency_ms": 5},
    "streams": "both",
    "gyro": True,
    "capture_time": {"mode": "delivery", "lag_ms": 50},
}

# Every realism source draws from its own child of ``SeedSequence(case seed)``,
# so changing one stream's parameters never moves another stream's noise, and
# two cases that differ only in seed differ in every source (issue #901: the
# old cases all drew from ``default_rng(0)`` whatever their seed). The order is
# fixed and is a contract with the route and frame generators that spawn from
# the same seed: 0 relative, 1 absolute, 2 motion, 3 tremor, 4 frame noise,
# 5 EIS, 6 auto-exposure, 7-9 reserved. This module uses 0-2 only.
_SEED_CHILDREN = 10
_SEED_RELATIVE = 0
_SEED_ABSOLUTE = 1
_SEED_MOTION = 2

_STREAMS = ("both", "absolute-only")
_CAPTURE_MODES = ("delivery", "sensor", "none")


def _merge(defaults: dict, given: dict) -> dict:
    """``given`` laid over ``defaults``, recursing where both sides are dicts."""
    merged = {}
    for key, value in defaults.items():
        merged[key] = _copy(value)
    for key, value in given.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = _copy(value)
    return merged


def _copy(value):
    if isinstance(value, dict):
        return {k: _copy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_copy(v) for v in value]
    return value


def resolve_realism(realism: dict) -> dict:
    """``realism`` with every omitted key filled in from :data:`REALISM_DEFAULTS`.

    Returns a new dict and leaves the argument alone, because
    ``cases.build_case`` copies the block into the manifest exactly as the case
    definition wrote it. Keys the defaults do not name pass through (``frames``
    is the frame realism block, which a later task reads). Raises
    ``ValueError`` on a value that cannot mean anything, so a typo fails before
    a case spends minutes rendering frames rather than after.
    """
    spec = _merge(REALISM_DEFAULTS, realism)
    if spec["streams"] not in _STREAMS:
        raise ValueError(f"realism streams must be one of {_STREAMS}, got {spec['streams']!r}")
    if spec["capture_time"]["mode"] not in _CAPTURE_MODES:
        raise ValueError(f"capture_time mode must be one of {_CAPTURE_MODES}, "
                         f"got {spec['capture_time']['mode']!r}")
    for name in ("relative", "absolute", "motion"):
        if not float(spec[name]["pump_hz"]) > 0:
            raise ValueError(f"realism {name} pump_hz must be positive")
    for name in ("relative", "absolute"):
        if spec[name]["spikes"] is not None:
            spec[name]["spikes"] = {**_SPIKE_DEFAULTS, **spec[name]["spikes"]}
    return spec


def realism_readings(traj, spec: dict, seed: int) -> list:
    """Every ``orientation`` and ``motion`` record a resolved realism block asks for.

    ``spec`` is :func:`resolve_realism`'s output and ``seed`` is the case's own.
    The records are in generation order (relative, absolute, then motion), not
    delivery order; ``cases.build_case`` sorts them in with the frames.

    ``streams: "both"`` writes the two orientation streams a phone with a
    relative sensor has: ``deviceorientation`` with ``absolute: false`` and
    ``deviceorientationabsolute`` with ``absolute: true``. ``streams:
    "absolute-only"`` is Chromium's fallback shape when there is no relative
    sensor: no relative stream, and every absolute record written a second time
    as a ``deviceorientation`` event with ``absolute: true``, the same values at
    the same times, straight after the original. ``gyro: false`` writes no
    ``motion`` records at all, which is how a phone without a gyroscope looks.
    """
    seeds = np.random.SeedSequence(seed).spawn(_SEED_CHILDREN)
    records = []
    if spec["streams"] == "both":
        records += _orientation_stream(traj, spec["relative"], seeds[_SEED_RELATIVE],
                                       absolute=False, gyro_scale_err=spec["gyro_scale_err"])
    for record in _orientation_stream(traj, spec["absolute"], seeds[_SEED_ABSOLUTE],
                                      absolute=True):
        records.append(record)
        if spec["streams"] == "absolute-only":
            records.append({**record, "event": "deviceorientation"})
    if spec["gyro"]:
        motion = spec["motion"]
        records += motion_events(
            traj, sample_hz=motion["pump_hz"], latency_ms=motion["latency_ms"],
            noise_deg_s=motion["noise_deg_s"], seed=seeds[_SEED_MOTION],
            scale_err=spec["gyro_scale_err"], bias_deg_s=motion["bias_deg_s"],
            round_deg_s=motion["round_deg_s"])
    return records


def _heading_deg(basis) -> float:
    """The truth heading in the sense of W3C ``alpha``: counter-clockwise.

    That is minus the compass azimuth of the optical axis, and it is the sense
    every heading offset in this module is written in. It matters for exactly
    one thing, the sign of a declination. A compass reads MAGNETIC north, so it
    reports the true azimuth minus the declination D (east is positive), which
    is alpha plus D. A scanner that turns magnetic into true with
    ``az + D`` using the case's own ``declination_deg`` then recovers the true
    azimuth, which is the whole point of a case carrying the number twice.

    The azimuth of the optical axis rather than ``device_orientation``'s own
    alpha, because that alpha trades places with gamma as the phone nears
    upright (beta = 90, the usual way to hold it): at half a degree of pitch,
    a third of a degree of roll moves it by 31 degrees. A scale error applied
    to that would be noise, not a gyro; the offsets are instead added to the
    alpha the truth already has, and alpha plus an offset is always the same
    attitude turned about the vertical.
    """
    azimuth, _ = sky_angles(basis.forward)
    return -azimuth


def _round_to(x: float, step: float) -> float:
    """``x`` rounded to the nearest multiple of ``step``, as a short float and never ``-0.0``."""
    return round(round(x / step) * step, 6) + 0.0


def _orientation_stream(traj, spec: dict, seed, *, absolute: bool,
                        gyro_scale_err: float = 0.0) -> list:
    """One ``orientation`` stream, the way a browser's event pump delivers it.

    The pump fires on a fixed ``pump_hz`` grid. At each tick the sensor is read
    ``age`` milliseconds late, ``age`` uniform on ``[0, 1000 / pump_hz)``
    (Chromium's pump dispatches whatever the sensor last wrote, which is up to
    one period old), so the reading is the trajectory at ``tick - age``. It is
    delivered if any of alpha, beta or gamma has moved by ``threshold_deg``
    from the last DELIVERED reading, and not otherwise: a phone holding still
    goes silent, and one turning faster than ``threshold_deg * pump_hz`` degrees
    a second fires on every tick. ``t_event_ms`` is the tick and
    ``t_receive_ms`` is ``latency_ms`` later.

    Two choices about WHAT the change test reads, both made so that the
    spec's own numbers come out of the pump arithmetic (a 2 deg/s turn at
    15 Hz, a 20 deg/s turn at 60, a still phone silent):

    - it reads the sensor's value before the record rounds it to
      ``quant_deg``. Comparing the ROUNDED values would fire a 2 deg/s turn at
      20 Hz, because each quantum then takes exactly three ticks to cross;
    - it reads the sensor's attitude before the white TILT noise is added to
      the record. At the spec's default of 0.05 degrees that noise is half the
      0.1-degree threshold, so letting it into the test fires a phone lying
      still at about 16 Hz and a 2 deg/s turn at 25. The noise is the error on
      the numbers the record carries, not a change in the attitude the sensor
      holds. (The compass's slow Ornstein-Uhlenbeck error below IS part of the
      attitude, and does reach the test.)

    Relative (``absolute=False``): the truth with
    ``yaw0 + gyro_scale_err * (heading - heading0) + drift * t`` added to its
    alpha, where ``heading`` is unwrapped, ``yaw0`` is the seeded yaw zero (or
    the number the block gives), and tilt is the truth plus white noise of
    ``noise_deg``. The heading is smooth: a gyro integrates cleanly and drifts
    slowly, which is what makes a scale error the dominant relative fault.

    Absolute: the truth with ``bias_deg + declination_deg + amp * sin(heading +
    phase) + OU + steps`` added to its alpha, where OU is an Ornstein-Uhlenbeck
    process (stationary sd ``sigma_deg``, correlation time ``tau_s``) sampled
    on the pump grid and ``steps`` is ``[{"t_s", "deg"}]``, a heading step at
    ``t_s``. Tilt is the truth plus white noise of ``tilt_noise_deg``, a key
    the spec's block does not name and which defaults to the relative stream's
    0.05. A compass is slow and biased, not white, which is why its noise is
    correlated and its heading carries a sinusoid.

    Spikes (``{"rate_hz", "size_deg"}``): with probability ``rate_hz /
    pump_hz`` per tick, that ONE reading's alpha is displaced by plus or minus
    ``size_deg`` and the next is back where it was, so a spike is two records.
    """
    pump_hz = float(spec["pump_hz"])
    period_ms = 1000.0 / pump_hz
    n_ticks = int(round(traj.duration_ms / period_ms)) + 1

    # Everything random is drawn up front, in a fixed order, for every case
    # whether or not it uses it (a standard-normal draw is scaled afterwards),
    # so turning one fault on or off, or changing its size, leaves every other
    # random number in the stream where it was.
    rng = np.random.default_rng(seed)
    yaw_zero_draw = float(rng.uniform(0.0, 360.0))
    ages_ms = rng.uniform(0.0, period_ms, n_ticks)
    tilt_noise = rng.normal(0.0, 1.0, (n_ticks, 2))
    heading_noise = rng.normal(0.0, 1.0, n_ticks)
    spike_draw = rng.random(n_ticks)
    spike_up = rng.random(n_ticks) < 0.5

    spikes = spec.get("spikes")
    spike_p = float(spikes["rate_hz"]) / pump_hz if spikes else 0.0
    quant_deg = float(spec["quant_deg"])
    threshold_deg = float(spec["threshold_deg"]) - 1e-9
    latency_ms = int(round(spec["latency_ms"]))
    event = "deviceorientationabsolute" if absolute else "deviceorientation"

    if absolute:
        tilt_sigma = float(spec["tilt_noise_deg"])
        bias_deg = float(spec["bias_deg"])
        declination_deg = float(spec["declination_deg"])
        amp_deg = float(spec["sinusoid"]["amp_deg"])
        phase_deg = float(spec["sinusoid"]["phase_deg"])
        sigma_deg = float(spec["noise"]["sigma_deg"])
        tau_s = float(spec["noise"]["tau_s"])
        steps = spec["steps"]
        # The exact discretisation of an OU process on this grid: stationary
        # variance sigma^2 whatever the step.
        keep = math.exp(-(period_ms / 1000.0) / tau_s) if tau_s > 0 else 0.0
        innovation = sigma_deg * math.sqrt(1.0 - keep * keep)
    else:
        tilt_sigma = float(spec["noise_deg"])
        yaw_zero = yaw_zero_draw if spec["yaw_zero_deg"] == "random" else float(spec["yaw_zero_deg"])
        drift_deg_s = float(spec["drift_deg_min"]) / 60.0

    events = []
    last = None
    ou = 0.0
    previous_heading = None
    turned = 0.0
    for k in range(n_ticks):
        tick_ms = k * period_ms
        read_ms = min(max(tick_ms - float(ages_ms[k]), 0.0), float(traj.duration_ms))
        _, basis = traj.pose_at(read_ms)
        alpha, beta, gamma = device_orientation(basis)

        heading = _heading_deg(basis)
        if previous_heading is not None:
            turned += float(wrap_deg(heading - previous_heading))
        previous_heading = heading
        t_s = read_ms / 1000.0

        if absolute:
            ou = sigma_deg * float(heading_noise[k]) if k == 0 else (
                keep * ou + innovation * float(heading_noise[k]))
            offset = (bias_deg + declination_deg
                      + amp_deg * math.sin(math.radians(heading + phase_deg))
                      + ou
                      + sum(float(step["deg"]) for step in steps if t_s >= float(step["t_s"])))
        else:
            offset = yaw_zero + gyro_scale_err * turned + drift_deg_s * t_s
        if spike_draw[k] < spike_p:
            offset += float(spikes["size_deg"]) * (1.0 if spike_up[k] else -1.0)

        # The attitude the sensor holds, which the change test reads; the tilt
        # noise below is the error on the numbers the record carries.
        held = ((alpha + offset) % 360.0, float(beta), float(gamma))
        if last is not None and not _changed(*held, last, threshold_deg):
            continue
        last = held
        reported = (held[0],
                    float(wrap_deg(held[1] + tilt_sigma * float(tilt_noise[k, 0]))),
                    min(90.0, max(-90.0, held[2] + tilt_sigma * float(tilt_noise[k, 1]))))

        t_event_ms = int(round(tick_ms))
        events.append({
            "kind": "orientation",
            "event": event,
            "t_event_ms": t_event_ms,
            "t_receive_ms": t_event_ms + latency_ms,
            "alpha": _round_to(reported[0], quant_deg) % 360.0 if quant_deg else reported[0],
            "beta": _round_to(reported[1], quant_deg) if quant_deg else reported[1],
            "gamma": _round_to(reported[2], quant_deg) if quant_deg else reported[2],
            "absolute": absolute,
        })
    return events
