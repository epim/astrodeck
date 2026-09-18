"""Coordinate conventions for the photosphere simulator.

This module is derived from CONTRACT.md and the W3C DeviceOrientation
specification, never from the TypeScript under test. It is the simulator's
only definition of what an angle means, so every convention it depends on is
stated here rather than inferred from a call site.

Frames, all as declared in CONTRACT.md:

- World: ENU, ``x`` east, ``y`` north, ``z`` up. Azimuth runs clockwise from
  north, altitude up from the horizontal plane.
- Camera basis: ``right``, ``up``, ``forward``, unit vectors in world
  coordinates, with ``right x up = -forward``.
- Camera coordinates, as used by :meth:`Camera.project`, :meth:`Camera.ray`,
  :func:`to_camera` and :func:`to_world`: ``x`` along ``right``, ``y`` along
  ``up``, ``z`` along ``forward``, so a direction in front of the lens has
  ``z > 0``. This is the pinhole frame.
- :meth:`Basis.matrix_cam_to_world` is a different frame on purpose: its third
  column is ``-forward`` because the renderer's camera looks down its own -z,
  as in OpenGL. It exists for the renderer; it is not the matrix that feeds
  :meth:`Camera.project`. Mixing the two is a sign error that survives a
  determinant check, so the two are never converted into one another here.
- Device orientation: ``R = Rz(alpha) Rx(beta) Ry(gamma)`` maps device
  coordinates to world coordinates; device ``x`` points right across the
  screen, ``y`` to the top of the screen, ``z`` out of the screen.

Angles are degrees in every public signature, radians only inside a body.
Vectors are float64 ``numpy`` arrays of shape ``(3,)`` or ``(N, 3)``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

__all__ = [
    "Basis",
    "Camera",
    "angle_between",
    "basis_from_device_orientation",
    "device_orientation",
    "look_basis",
    "sky_angles",
    "sky_vector",
    "to_camera",
    "to_world",
    "wrap_deg",
]


def _vec3(v) -> np.ndarray:
    """Coerce to a float64 3-vector, refusing anything of another shape."""
    a = np.asarray(v, dtype=np.float64)
    if a.shape != (3,):
        raise ValueError(f"expected a 3-vector, got shape {a.shape}")
    return a


def wrap_deg(x):
    """Wrap an angle in degrees to [-180, 180), elementwise for arrays."""
    return (x + 180.0) % 360.0 - 180.0


def sky_vector(az_deg, alt_deg) -> np.ndarray:
    """The world direction at azimuth ``az_deg`` clockwise from north and
    altitude ``alt_deg`` above the horizontal plane."""
    az = math.radians(az_deg)
    alt = math.radians(alt_deg)
    ca = math.cos(alt)
    return np.array([math.sin(az) * ca, math.cos(az) * ca, math.sin(alt)], dtype=np.float64)


def sky_angles(v) -> tuple[float, float]:
    """The (azimuth, altitude) in degrees of a world direction.

    Azimuth is returned in [0, 360). The vector need not be normalised.
    """
    x, y, z = _vec3(v)
    norm = math.sqrt(x * x + y * y + z * z)
    if norm == 0.0:
        raise ValueError("the zero vector has no direction")
    az = math.degrees(math.atan2(x, y)) % 360.0
    alt = math.degrees(math.asin(min(1.0, max(-1.0, z / norm))))
    return (az, alt)


def angle_between(a, b) -> float:
    """The angle in degrees between two directions.

    Uses ``atan2(|a x b|, a . b)`` rather than a clamped ``acos(a . b)``: the
    two agree on paper, but near 0 and 180 degrees ``acos`` loses half its
    significant digits, and this function is what the round-trip tests measure
    their residuals with. One unit of rounding in the dot product is already
    1.2e-6 degrees of apparent error under ``acos``, which is above the 1e-7
    gate those tests assert; the ratio form reports 4.4e-14 for the same
    vectors. It is also insensitive to the vectors' lengths.
    """
    u = _vec3(a)
    v = _vec3(b)
    return math.degrees(math.atan2(float(np.linalg.norm(np.cross(u, v))), float(np.dot(u, v))))


@dataclass(frozen=True)
class Basis:
    """A camera attitude in world coordinates: where its image axes point.

    ``right`` is the image x axis, ``up`` is the image y axis before the rows
    are counted downward, and ``forward`` is the direction the lens looks.
    ``right x up = -forward``.
    """

    right: np.ndarray
    up: np.ndarray
    forward: np.ndarray

    def __post_init__(self) -> None:
        for name in ("right", "up", "forward"):
            object.__setattr__(self, name, _vec3(getattr(self, name)))

    def matrix_cam_to_world(self) -> np.ndarray:
        """The renderer's camera-to-world rotation, columns ``[right, up, -forward]``.

        A proper rotation, determinant +1, in the OpenGL convention where the
        camera looks down its own -z. See the module docstring: this is not the
        frame :meth:`Camera.project` consumes.
        """
        return np.column_stack([self.right, self.up, -self.forward])


def look_basis(az_deg, alt_deg, roll_deg=0.0) -> Basis:
    """The attitude of a camera aimed at ``(az_deg, alt_deg)``.

    At zero roll the image x axis is horizontal, 90 degrees clockwise of the
    aim, so the image's up is the upper side of the sky. Positive roll turns
    ``right`` toward ``up``, which fixed world content shows as a clockwise
    rotation of the image.
    """
    forward = sky_vector(az_deg, alt_deg)
    level_right = sky_vector(az_deg + 90.0, 0.0)
    r = math.radians(roll_deg)
    right = level_right * math.cos(r) + np.cross(level_right, forward) * math.sin(r)
    up = np.cross(right, forward)
    return Basis(right=right, up=up, forward=forward)


def to_camera(basis: Basis, dir_world) -> np.ndarray:
    """A world direction expressed in pinhole camera coordinates."""
    d = np.asarray(dir_world, dtype=np.float64)
    return d @ np.column_stack([basis.right, basis.up, basis.forward])


def to_world(basis: Basis, dir_cam) -> np.ndarray:
    """A pinhole camera direction expressed in world coordinates."""
    d = np.asarray(dir_cam, dtype=np.float64)
    return d @ np.vstack([basis.right, basis.up, basis.forward])


class Camera:
    """An undistorted pinhole camera described by its short-axis field of view.

    ``fx = fy = (short / 2) / tan(fov_short / 2)`` over the shorter image axis,
    with the principal point at the image centre. Pixel ``(i, j)`` has its
    centre at ``(i + 0.5, j + 0.5)``: ``u`` runs right, ``v`` runs down.
    """

    def __init__(self, width: int, height: int, fov_short_deg: float):
        if width <= 0 or height <= 0:
            raise ValueError("image dimensions must be positive")
        if not 0.0 < fov_short_deg < 180.0:
            raise ValueError("fov_short_deg must lie strictly between 0 and 180")
        self.width = int(width)
        self.height = int(height)
        self.fov_short_deg = float(fov_short_deg)
        short = min(self.width, self.height)
        f = (short / 2.0) / math.tan(math.radians(self.fov_short_deg) / 2.0)
        self.fx = f
        self.fy = f
        self.cx = self.width / 2.0
        self.cy = self.height / 2.0

    def __repr__(self) -> str:
        return (
            f"Camera(width={self.width}, height={self.height}, "
            f"fov_short_deg={self.fov_short_deg})"
        )

    def project(self, dir_cam) -> np.ndarray:
        """Pixel coordinates of camera-frame directions, shape (N, 3) -> (N, 2).

        Directions at or behind the image plane (``z <= 0``) return NaN rather
        than a plausible pixel: a point behind the lens has no image.
        """
        d = np.asarray(dir_cam, dtype=np.float64)
        if d.ndim != 2 or d.shape[1] != 3:
            raise ValueError(f"expected directions of shape (N, 3), got {d.shape}")
        z = d[:, 2]
        front = z > 0.0
        out = np.full((d.shape[0], 2), np.nan, dtype=np.float64)
        zf = z[front]
        out[front, 0] = self.cx + self.fx * d[front, 0] / zf
        out[front, 1] = self.cy - self.fy * d[front, 1] / zf
        return out

    def ray(self, u, v) -> np.ndarray:
        """The unit camera-frame direction through pixel coordinates (u, v)."""
        d = np.array(
            [(float(u) - self.cx) / self.fx, -(float(v) - self.cy) / self.fy, 1.0],
            dtype=np.float64,
        )
        return d / np.linalg.norm(d)


def _rz(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]], dtype=np.float64)


def _rx(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]], dtype=np.float64)


def _ry(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]], dtype=np.float64)


def device_orientation(basis: Basis, screen_angle_deg=0.0) -> tuple[float, float, float]:
    """The W3C DeviceOrientation angles a phone in this attitude would report.

    Returns ``(alpha, beta, gamma)`` in degrees, alpha in [0, 360), beta in
    [-180, 180), gamma in [-90, 90), the ranges the specification declares.
    ``screen_angle_deg`` is the display's rotation, so a landscape phone
    reports the same world attitude with different angles.
    """
    # Undo the screen rotation first: the rear camera's right/up at screen
    # angle s are the device x/y rotated by s about the device z axis.
    s = math.radians(screen_angle_deg)
    x = basis.right * math.cos(s) + basis.up * math.sin(s)   # device x in world
    y = -basis.right * math.sin(s) + basis.up * math.cos(s)  # device y in world
    z = -basis.forward                                      # device z in world
    R = np.column_stack([x, y, z])                          # world <- device
    sb = R[2, 1]
    cb_mag = math.hypot(R[0, 1], R[1, 1])
    if cb_mag < 1e-9:
        # beta is +-90: alpha and gamma are not separable; put it all in alpha.
        beta = math.degrees(math.atan2(sb, 0.0))
        gamma = 0.0
        alpha = math.degrees(math.atan2(R[1, 0], R[0, 0]))
    else:
        best = None
        for sign in (1.0, -1.0):
            cb = sign * cb_mag
            beta = math.degrees(math.atan2(sb, cb))
            gamma = math.degrees(math.atan2(-R[2, 0] * sign, R[2, 2] * sign))
            alpha = math.degrees(math.atan2(-R[0, 1] * sign, R[1, 1] * sign))
            if -90.0 <= gamma < 90.0:
                best = (alpha, beta, gamma)
                break
        alpha, beta, gamma = best
    return (alpha % 360.0, wrap_deg(beta), gamma)


def basis_from_device_orientation(alpha, beta, gamma, screen_angle_deg=0.0) -> Basis:
    """The camera attitude a phone reporting these angles is holding.

    The inverse of :func:`device_orientation`. The rear camera looks out of the
    back of the screen, so ``forward`` is the negated device z axis.
    """
    R = _rz(math.radians(alpha)) @ _rx(math.radians(beta)) @ _ry(math.radians(gamma))
    x = R[:, 0]  # device x in world
    y = R[:, 1]  # device y in world
    z = R[:, 2]  # device z in world
    s = math.radians(screen_angle_deg)
    right = x * math.cos(s) - y * math.sin(s)
    up = x * math.sin(s) + y * math.cos(s)
    return Basis(right=right, up=up, forward=-z)
