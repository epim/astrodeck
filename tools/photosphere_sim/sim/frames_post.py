# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""What a phone camera does to a frame between the world and the page.

The renderer draws the world as a pinhole camera would see it from one pose.
A real frame is not that: the shutter is open for a while, the sensor is read
out column by column, the auto-exposure drifts, the stabiliser moves the crop,
the sensor adds noise, and the ISP may downscale without a prefilter. A case
whose definition carries a ``realism.frames`` block (the panorama scanner's
spec 13.5) is run through this module; a case without one never reaches it and
writes the bytes it always wrote.

The pipeline, per frame, in this order:

1. **EIS** (:func:`eis_offsets`). The stabiliser shows a virtual camera, not
   the device: a critically damped low-pass of the view direction, clamped to
   ``margin_deg`` of the truth (its crop margin), plus a small seeded jitter.
   It is rendered as a view offset: every sub-frame of the frame is drawn from
   the truth pose turned by the offset. The truth trajectory is not touched;
   it stays the device pose, which is the pose the sensors report.
2. **Exposure blur.** ``subframes`` renders at the midpoints of equal slices of
   the exposure window, which is centred on the frame's capture time, averaged.
   Four midpoint taps over a window of length L give a blur whose box-equivalent
   length (the square root of twelve times the kernel's variance) is 0.968 L,
   and whose mean pose is the truth pose.
3. **Rolling shutter and the aliasing option** (:func:`resample`). One bilinear
   resample. The sensor reads out across the image width in
   ``rolling_shutter_ms``, left to right, so a pan at ``omega`` degrees per
   second (positive when the view turns right) scales the image about its
   centre horizontally: output column ``x`` shows what the central-time frame
   shows at ``centre + s (x - centre)``, with ``s = 1 + omega t_r / hFoV``. The
   ``aliasing`` option is a render at ``factor`` times the resolution, sampled
   back to the camera's size at pixel centres with bilinear weights and no
   prefilter, the way a GPU minifies without mipmaps.
4. **Auto-exposure** (:class:`AutoExposure`). A gain from the frame mean luma,
   first-order lagged with ``tau_s``, clamped to ``min_gain`` .. ``max_gain``.
5. **Noise.** One Gaussian draw per pixel, added to all three channels, after
   the gain: the luma noise then has standard deviation ``noise_sigma``
   exactly, which is the figure the scanner estimates and the figure
   ``truth/visibility.json`` divides by. Then rounding to uint8.

Seeds come from ``SeedSequence(case seed).spawn(10)``, the order
:mod:`sim.sensors` fixes: child 4 is the frame noise (one stream per frame
number, so a frame's noise does not depend on which frames came before it),
child 5 the EIS jitter, child 6 the auto-exposure's starting gain.

Conventions the spec leaves open, chosen here and tested:

- The EIS filter's ``tau_s`` is its DELAY: the first moment of its impulse
  response, which is also the steady lag behind a constant-rate input. A
  critically damped pair of lags with natural frequency ``2 / tau_s`` has
  exactly that delay, so after a step of the view the mean lag is ``tau_s``
  (the 63 % point of a single lag would make the delay twice that).
- The EIS clamp is a disc: the offset's length, not each axis, stays within
  ``margin_deg``, so both readings of "within 1.8 degrees" hold.
- Averaging is in the renderer's display values, not in linear light.
- The rolling shutter's pitch-rate skew is not modelled; the spec asks for the
  horizontal scale only.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from .geometry import Basis, Camera

__all__ = ["AutoExposure", "FRAMES_DEFAULTS", "FramePost", "RenderRequest", "eis_offsets",
           "horizontal_rate_deg_s", "luma_of", "offset_basis", "resample", "resolve_frames",
           "rolling_shutter_scale", "subframe_times"]

#: Spec 13.5, verbatim. A key a case's block omits takes this value.
FRAMES_DEFAULTS = {
    "exposure_ms": 4,
    "subframes": 4,
    "ae": {"min_gain": 0.6, "max_gain": 1.6, "tau_s": 0.5, "target_luma": 118},
    "noise_sigma": 2.0,
    "rolling_shutter_ms": 25,
    "eis": {"tau_s": 0.2, "margin_deg": 1.8, "jitter_deg": 0.05},
    "aliasing": None,
}

# The children of ``SeedSequence(case seed).spawn(10)`` this module draws from;
# the numbering is the contract in :mod:`sim.sensors` (0 relative, 1 absolute,
# 2 motion, 3 tremor, then these three, 7-9 reserved).
_SEED_CHILDREN = 10
_SEED_NOISE = 4
_SEED_EIS = 5
_SEED_AE = 6

#: The seeded starting gain of the auto-exposure is 1 plus or minus this.
_AE_START_SPREAD = 0.1

#: Rec. 601 luma, the weights the scanner's own luma uses.
_LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float32)

#: The EIS filter is integrated in steps no longer than this, with the view
#: direction taken from the truth pose at each step.
_EIS_STEP_MS = 5.0

#: Half the interval a rate is differenced over, in ms.
_RATE_HALF_MS = 4.0


def _copy(value):
    return dict(value) if isinstance(value, dict) else value


def resolve_frames(block) -> dict:
    """``block`` with every omitted key filled in from :data:`FRAMES_DEFAULTS`.

    Returns a new dict. ``ae`` and ``eis`` may be given as ``null`` to switch
    that effect off, as ``aliasing`` is ``null`` when off. Raises
    ``ValueError`` on a key it does not know and on a value that cannot mean
    anything, so a typo fails in milliseconds and not after a case has spent
    minutes rendering.
    """
    if not isinstance(block, dict):
        raise ValueError(f"realism.frames must be an object, got {type(block).__name__}")
    unknown = sorted(set(block) - set(FRAMES_DEFAULTS))
    if unknown:
        raise ValueError(f"realism.frames has unknown keys {unknown}; "
                         f"known keys are {sorted(FRAMES_DEFAULTS)}")
    spec = {}
    for key, default in FRAMES_DEFAULTS.items():
        if key not in block:
            spec[key] = _copy(default)
        elif isinstance(default, dict) and isinstance(block[key], dict):
            extra = sorted(set(block[key]) - set(default))
            if extra:
                raise ValueError(f"realism.frames.{key} has unknown keys {extra}")
            spec[key] = {**default, **block[key]}
        else:
            spec[key] = _copy(block[key])

    if not float(spec["exposure_ms"]) >= 0.0:
        raise ValueError("realism.frames.exposure_ms must not be negative")
    subframes = spec["subframes"]
    if isinstance(subframes, bool) or int(subframes) != subframes or subframes < 1:
        raise ValueError("realism.frames.subframes must be a whole number of at least 1")
    if not float(spec["noise_sigma"]) >= 0.0:
        raise ValueError("realism.frames.noise_sigma must not be negative")
    if not float(spec["rolling_shutter_ms"]) >= 0.0:
        raise ValueError("realism.frames.rolling_shutter_ms must not be negative")
    ae = spec["ae"]
    if ae is not None:
        if not 0.0 < float(ae["min_gain"]) <= float(ae["max_gain"]):
            raise ValueError("realism.frames.ae needs 0 < min_gain <= max_gain")
        if not float(ae["tau_s"]) > 0.0 or not float(ae["target_luma"]) > 0.0:
            raise ValueError("realism.frames.ae needs a positive tau_s and target_luma")
    eis = spec["eis"]
    if eis is not None:
        if not float(eis["tau_s"]) > 0.0:
            raise ValueError("realism.frames.eis.tau_s must be positive")
        if not float(eis["margin_deg"]) >= 0.0 or not float(eis["jitter_deg"]) >= 0.0:
            raise ValueError("realism.frames.eis margin_deg and jitter_deg must not be negative")
    aliasing = spec["aliasing"]
    if aliasing is not None:
        if not isinstance(aliasing, dict) or set(aliasing) != {"factor"}:
            raise ValueError('realism.frames.aliasing must be null or {"factor": n}')
        factor = aliasing["factor"]
        if isinstance(factor, bool) or int(factor) != factor or factor < 2:
            raise ValueError("realism.frames.aliasing.factor must be a whole number of at least 2")
    return spec


def luma_of(image) -> np.ndarray:
    """Rec. 601 luma of an ``(H, W, 3)`` image, float32, on the 0..255 scale."""
    return np.asarray(image, dtype=np.float32) @ _LUMA


@dataclass(frozen=True)
class RenderRequest:
    """One render the case renderer is asked for: the shape ``renderer`` reads.

    ``sim.cases.build_case`` hands its renderer trajectory frames; a sub-frame
    is not one, so this carries only what a renderer reads of a frame.
    """

    frame_id: str
    position: np.ndarray
    basis: Basis


def subframe_times(t_ms: float, exposure_ms: float, subframes: int) -> np.ndarray:
    """The instants of a frame's sub-frame renders, ms.

    The exposure window is ``exposure_ms`` long and centred on ``t_ms``; it is
    cut into ``subframes`` equal slices and each is sampled at its midpoint.
    One sub-frame is the capture time itself, whatever the exposure.
    """
    slices = (np.arange(subframes) + 0.5) / subframes - 0.5
    return float(t_ms) + float(exposure_ms) * slices


def offset_basis(basis: Basis, yaw_deg: float, pitch_deg: float) -> Basis:
    """``basis`` with its view turned in its own image frame.

    A positive ``yaw_deg`` turns the forward axis toward ``right`` and a
    positive ``pitch_deg`` toward ``up``; ``right`` and ``up`` are carried
    along. This is a shift of the crop window, which is what an offset of the
    stabiliser's virtual camera is. Roll is untouched.
    """
    y, p = math.radians(yaw_deg), math.radians(pitch_deg)
    sy, cy, sp, cp = math.sin(y), math.cos(y), math.sin(p), math.cos(p)
    yaw = np.array([[cy, 0.0, sy], [0.0, 1.0, 0.0], [-sy, 0.0, cy]])
    pitch = np.array([[1.0, 0.0, 0.0], [0.0, cp, sp], [0.0, -sp, cp]])
    axes = np.column_stack([basis.right, basis.up, basis.forward]) @ (yaw @ pitch)
    return Basis(right=axes[:, 0], up=axes[:, 1], forward=axes[:, 2])


def _clamp_disc(vector: np.ndarray, radius: float) -> np.ndarray:
    """``vector`` with its length limited to ``radius``; the same object if it fits."""
    norm = math.hypot(float(vector[0]), float(vector[1]))
    if norm <= radius:
        return vector
    return vector * (radius / norm)


def eis_offsets(pose_at, times_ms, *, tau_s: float, margin_deg: float, jitter_deg: float,
                rng) -> np.ndarray:
    """The stabiliser's view offset at each of ``times_ms``, degrees.

    Returns ``(len(times_ms), 2)``: the yaw and pitch of the virtual camera's
    view direction from the truth camera's, in the truth camera's image frame
    (positive yaw is toward ``right``, positive pitch toward ``up``).

    The truth view direction is accumulated, in the image frame, from the poses
    ``pose_at(t)`` returns every 5 ms or less from t = 0, where the virtual
    camera starts locked on it. The virtual direction is that signal through a
    critically damped low-pass, ``x'' = w^2 (d - x) - 2 w x'`` with
    ``w = 2 / tau_s``, so that its delay is ``tau_s`` (module docstring); each
    step uses the closed form of the filter for an input held at the step's
    mean, so the integration error does not depend on the step. The offset is
    ``x`` minus the truth, clamped to the disc of radius ``margin_deg``: the
    crop window has run out of margin. When the clamp binds the virtual
    camera's velocity along the outward normal is dropped, so it does not wind
    up behind the limit and overshoot on the way back. ``rng`` then adds white
    jitter of ``jitter_deg`` per axis, and the sum is clamped again, so the
    returned offsets never exceed the margin. ``times_ms`` must be ascending.
    """
    times = [float(t) for t in times_ms]
    # Drawn for every frame whatever jitter_deg is, so that the stream does not
    # shift when the jitter is changed.
    jitter = rng.standard_normal((len(times), 2)) * float(jitter_deg)
    omega = 2.0 / float(tau_s)
    out = np.zeros((len(times), 2))

    t = 0.0
    basis = pose_at(0.0)[1]
    psi = np.zeros(2)       # the truth view direction, accumulated, image frame
    x = np.zeros(2)         # the virtual camera's
    v = np.zeros(2)         # and its velocity, deg/s
    for i, target in enumerate(times):
        if target > t:
            steps = max(1, math.ceil((target - t) / _EIS_STEP_MS - 1e-9))
            h_ms = (target - t) / steps
            h = h_ms / 1000.0
            for step in range(steps):
                t_next = target if step == steps - 1 else t + h_ms
                basis_next = pose_at(t_next)[1]
                ahead = float(basis_next.forward @ basis.forward)
                move = np.array([
                    math.degrees(math.atan2(float(basis_next.forward @ basis.right), ahead)),
                    math.degrees(math.atan2(float(basis_next.forward @ basis.up), ahead))])
                psi_next = psi + move
                rate = move / h

                held = 0.5 * (psi + psi_next)
                lag = x - held
                carry = v + omega * lag
                decay = math.exp(-omega * h)
                x = held + (lag + carry * h) * decay
                v = (v - omega * carry * h) * decay

                offset = x - psi_next
                limited = _clamp_disc(offset, margin_deg)
                if limited is not offset:
                    outward = offset / math.hypot(float(offset[0]), float(offset[1]))
                    relative = v - rate
                    along = float(relative @ outward)
                    if along > 0.0:
                        v = rate + relative - along * outward
                    x = psi_next + limited
                psi, basis, t = psi_next, basis_next, t_next
        out[i] = _clamp_disc((x - psi) + jitter[i], margin_deg)
    return out


def horizontal_rate_deg_s(pose_at, t_ms: float, duration_ms: float | None = None) -> float:
    """The rate the view turns toward ``right`` at ``t_ms``, degrees per second.

    Positive when the camera pans clockwise: the component, along the image's
    ``right`` axis, of the forward axis's motion over 8 ms centred on ``t_ms``
    (shortened at the ends of the trajectory). The tremor is in it, as it is in
    the gyro.
    """
    lo = max(t_ms - _RATE_HALF_MS, 0.0)
    hi = t_ms + _RATE_HALF_MS
    if duration_ms is not None:
        hi = min(hi, float(duration_ms))
    if hi <= lo:
        return 0.0
    centre = pose_at(t_ms)[1]
    moved = pose_at(hi)[1].forward - pose_at(lo)[1].forward
    return math.degrees(float(moved @ centre.right)) / ((hi - lo) / 1000.0)


def rolling_shutter_scale(rate_deg_s: float, readout_ms: float, h_fov_deg: float) -> float:
    """``s = 1 + omega t_r / hFoV``: see the module docstring for its sense."""
    return 1.0 + rate_deg_s * (readout_ms / 1000.0) / h_fov_deg


class AutoExposure:
    """A gain that chases ``target_luma / mean luma`` with a time constant.

    Each frame the wanted gain is the target over the frame's mean luma,
    clamped to ``min_gain`` .. ``max_gain``, and the actual gain moves toward
    it by the fraction ``1 - exp(-dt / tau_s)``. A convex step between two
    clamped values, so the gain cannot leave the range.
    """

    def __init__(self, min_gain: float, max_gain: float, tau_s: float, target_luma: float,
                 initial_gain: float = 1.0):
        self.min_gain = float(min_gain)
        self.max_gain = float(max_gain)
        self.tau_s = float(tau_s)
        self.target_luma = float(target_luma)
        self.gain = self._limit(float(initial_gain))

    def _limit(self, gain: float) -> float:
        return min(max(gain, self.min_gain), self.max_gain)

    def step(self, mean_luma: float, dt_s: float) -> float:
        """The gain for a frame whose mean luma (before the gain) is ``mean_luma``."""
        wanted = self._limit(self.target_luma / max(float(mean_luma), 1e-3))
        self.gain += (wanted - self.gain) * (1.0 - math.exp(-dt_s / self.tau_s))
        return self.gain


def _interpolate(image: np.ndarray, coords: np.ndarray, axis: int) -> np.ndarray:
    """Linear interpolation of ``image`` along ``axis`` at index-space ``coords``.

    Pixel ``i`` sits at coordinate ``i``; coordinates are clamped to the image,
    which repeats the edge pixel.
    """
    size = image.shape[axis]
    c = np.clip(coords, 0.0, size - 1.0)
    low = np.minimum(np.floor(c).astype(np.intp), size - 1)
    high = np.minimum(low + 1, size - 1)
    frac = (c - low).astype(np.float32)
    shape = [1] * image.ndim
    shape[axis] = -1
    frac = frac.reshape(shape)
    a = np.take(image, low, axis=axis)
    b = np.take(image, high, axis=axis)
    return a + (b - a) * frac


def resample(image: np.ndarray, out_width: int, out_height: int, x_scale: float = 1.0,
             factor: int = 1) -> np.ndarray:
    """One bilinear resample: the rolling-shutter scale and the aliasing downsample.

    ``image`` is ``(out_height * factor, out_width * factor, 3)`` float32. Output
    pixel ``(x, y)``, whose centre is at ``(x + 0.5, y + 0.5)``, takes the
    bilinear value at ``centre + x_scale (x + 0.5 - centre)`` horizontally and
    ``y + 0.5`` vertically, converted to the source's pixels by ``factor``. With
    ``factor = 4`` and ``x_scale = 1`` that is the mean of the four central
    pixels of each 4 x 4 block: a bilinear sample with no prefilter. Returns the
    input itself when there is nothing to do.
    """
    if factor == 1 and x_scale == 1.0:
        return image
    cx = out_width / 2.0
    xs = (cx + x_scale * (np.arange(out_width) + 0.5 - cx)) * factor - 0.5
    out = _interpolate(image, xs, axis=1)
    if factor > 1:
        out = _interpolate(out, (np.arange(out_height) + 0.5) * factor - 0.5, axis=0)
    return out


class _Exposures(Sequence):
    """The sub-frame render requests of every frame, built on demand."""

    def __init__(self, post: "FramePost"):
        self._post = post

    def __len__(self) -> int:
        return len(self._post.times_ms)

    def __getitem__(self, k):
        if isinstance(k, slice):
            return [self[i] for i in range(*k.indices(len(self)))]
        if k < 0:
            k += len(self)
        if not 0 <= k < len(self):
            raise IndexError(k)
        return self._post.requests(k)


class FramePost:
    """The frame realism of one case: render requests out, frames back.

    ``block`` is the case's ``realism.frames``, ``camera`` the case's camera,
    ``traj`` its :class:`sim.trajectory.Trajectory` and ``seed`` the case seed.
    Everything that depends on the trajectory alone (the EIS offsets, the
    rolling-shutter scales) is computed here, once. Per frame, :meth:`requests`
    says what to render and :meth:`process` turns the renders into the
    delivered frame; the auto-exposure is a running state, so frames must be
    processed in order.

    ``render_camera`` is the camera to render with: the case camera, or the
    case camera at ``factor`` times the resolution for the aliasing option.
    """

    def __init__(self, block, camera: Camera, traj, seed: int):
        self.spec = resolve_frames(block)
        spec = self.spec
        self.camera = camera
        aliasing = spec["aliasing"]
        self.factor = 1 if aliasing is None else int(aliasing["factor"])
        self.render_camera = camera if self.factor == 1 else Camera(
            camera.width * self.factor, camera.height * self.factor, camera.fov_short_deg)
        self.subframes = int(spec["subframes"])
        self.exposure_ms = float(spec["exposure_ms"])
        self.noise_sigma = float(spec["noise_sigma"])

        self._frames = traj.frames
        self._pose_at = traj.pose_at
        self.times_ms = [float(f.t_capture_ms) for f in traj.frames]
        count = len(self.times_ms)

        seeds = np.random.SeedSequence(seed).spawn(_SEED_CHILDREN)
        self._noise_seed = seeds[_SEED_NOISE]

        eis = spec["eis"]
        if eis is None:
            self.eis = np.zeros((count, 2))
        else:
            self.eis = eis_offsets(
                self._pose_at, self.times_ms, tau_s=float(eis["tau_s"]),
                margin_deg=float(eis["margin_deg"]), jitter_deg=float(eis["jitter_deg"]),
                rng=np.random.default_rng(seeds[_SEED_EIS]))

        duration = getattr(traj, "duration_ms", None)
        self.h_fov_deg = 2.0 * math.degrees(math.atan2(camera.width / 2.0, camera.fx))
        readout = float(spec["rolling_shutter_ms"])
        self.rolling_scale = np.array([
            rolling_shutter_scale(horizontal_rate_deg_s(self._pose_at, t, duration), readout,
                                  self.h_fov_deg) for t in self.times_ms])

        ae = spec["ae"]
        self._ae = None
        if ae is not None:
            start = 1.0 + np.random.default_rng(seeds[_SEED_AE]).uniform(
                -_AE_START_SPREAD, _AE_START_SPREAD)
            self._ae = AutoExposure(ae["min_gain"], ae["max_gain"], ae["tau_s"],
                                    ae["target_luma"], initial_gain=start)
        if count > 1:
            first = (self.times_ms[1] - self.times_ms[0]) / 1000.0
        else:
            first = 1.0 / 30.0
        self._dt_s = [first] + [(b - a) / 1000.0 for a, b in zip(self.times_ms, self.times_ms[1:])]

        #: The gain and the pre-gain mean luma of each frame processed so far.
        self.gains: list = []
        self.mean_lumas: list = []
        self._next = 0

    def exposures(self) -> Sequence:
        """Every frame's list of sub-frame render requests, in frame order."""
        return _Exposures(self)

    def requests(self, k: int) -> list:
        """The ``subframes`` renders frame ``k`` needs: truth poses plus the EIS offset."""
        yaw, pitch = (float(v) for v in self.eis[k])
        frame_id = self._frames[k].frame_id
        out = []
        for j, t in enumerate(subframe_times(self.times_ms[k], self.exposure_ms, self.subframes)):
            position, basis = self._pose_at(float(t))
            out.append(RenderRequest(
                frame_id=f"{frame_id}-s{j}",
                position=np.asarray(position, dtype=np.float64),
                basis=offset_basis(basis, yaw, pitch)))
        return out

    def seek(self, k: int) -> None:
        """Continue from frame ``k``, for a caller that develops only some frames.

        The auto-exposure is not wound forward: it carries on from where it is.
        """
        self._next = int(k)

    def develop(self, k: int, images) -> np.ndarray:
        """Frame ``k`` as float32, before the rounding to uint8.

        ``images`` are the renders of :meth:`requests`, in order, at
        ``render_camera``'s size.
        """
        if k != self._next:
            raise ValueError(f"frames must be developed in order: expected {self._next}, got {k}")
        if len(images) != self.subframes:
            raise ValueError(f"frame {k} needs {self.subframes} renders, got {len(images)}")
        rc = self.render_camera
        total = np.zeros((rc.height, rc.width, 3), dtype=np.float32)
        for image in images:
            if image.shape != total.shape:
                raise ValueError(f"a render is {image.shape}, expected {total.shape}")
            total += image
        total /= len(images)

        cam = self.camera
        frame = resample(total, cam.width, cam.height, float(self.rolling_scale[k]), self.factor)
        mean_luma = float(luma_of(frame).mean())
        gain = 1.0 if self._ae is None else self._ae.step(mean_luma, self._dt_s[k])
        self.gains.append(gain)
        self.mean_lumas.append(mean_luma)
        frame = frame * np.float32(gain)
        if self.noise_sigma > 0.0:
            noise_rng = np.random.default_rng(np.random.SeedSequence(
                self._noise_seed.entropy, spawn_key=self._noise_seed.spawn_key + (k,)))
            frame += (noise_rng.standard_normal((cam.height, cam.width), dtype=np.float32)
                      * np.float32(self.noise_sigma))[:, :, None]
        self._next += 1
        return frame

    def process(self, k: int, images) -> np.ndarray:
        """Frame ``k`` as the delivered ``(H, W, 3)`` uint8 image."""
        return np.clip(np.rint(self.develop(k, images)), 0, 255).astype(np.uint8)
