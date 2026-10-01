"""Every imaging-camera plate solve records the sky angle it measured, and
calibrates the rotator from it.

A plate solve measures the camera's sky position angle as a by-product. Until
this module existed that number was thrown away on every path but two:
``sync_rotator_to_sky`` and the ``rotate_to_pa`` loop fed it to the rotator, and
the goto centring, the resume re-centre, polar alignment, the guide-scope offset
and the per-frame WCS stamp all solved the same camera and dropped it. So the
rotator's reported sky angle was only as fresh as the last time somebody pressed
a rotator button, and after a reconnect (which resets the offset) it reported
its bare mechanical angle as if it were a sky PA for the rest of the night.

THE ONE FUNCTION is :func:`note_solved_rotation`. Every solve of an imaging-
camera frame goes through it; ``tests/test_every_solve_records_the_sky_angle.py``
enumerates the solver call sites statically and fails on a new one that does
not. It does two things:

* RECORDS the measurement: the solved PA, when the frame was exposed, which
  path solved it and the pier side. Stored on ``hub.last_sky_angle`` (surfaced
  on the status frame as ``sky_angle``), published on the bus as a
  ``sky_angle`` event, and written to the durable night log as one info line.
* CALIBRATES the rotator: re-syncs its sky offset (``Rotator.sync``) so its
  reported PA equals the solved one. Only when doing so is provably right.

WHEN IT REFUSES TO CALIBRATE. A calibration asserts "the mechanical angle the
rotator reports NOW corresponds to the PA this frame measured". That is only
true when nothing turned the field between the shutter and the sync, so it
records but does not calibrate, and says why in the log line, when:

* no rotator is connected, or it was not read when the exposure was taken, or
  it is a different device object than the one read then (a reconnect);
* the rotator was moving when the exposure started, is moving now, or its
  mechanical angle changed by more than :data:`MOVED_TOL_DEG` since;
* the mount's pier side changed, or could not be confirmed unchanged. A German
  equatorial flip turns the field 180 degrees under a rotator that never moved,
  so a solve of a pre-flip frame applied after the flip would leave the rotator
  reporting a PA exactly 180 degrees wrong. The comparison is strict: a side
  known at one end and unknown at the other refuses, and only "unknown at both
  ends" (a fork mount, which never flips) is accepted.

NOTHING HERE FOLDS THE PA BY PIER SIDE. After a flip the camera really is at the
old PA plus 180, and the rotator's reported sky angle is meant to be the camera's
real sky angle, so the post-flip solve calibrates to what it measured. Framing
equivalence mod 180 is the rotate loop's business (``rotation.shortest_rotation``
already treats the 180-degree twin as the same framing), not the calibration's.

NEVER for a guide-camera solve (its angle is the guide train's, not the imaging
train's), never for a failed solve, and never for a solve with no finite
rotation: those return ``None`` and record nothing.

THE PA CONVENTION is the one both earlier callers used: ``mod360(rotation_deg)``,
where ``rotation_deg`` is the solver's CROTA2 (ASTAP) or the simulator's
mechanical-plus-clocking truth. It is not re-derived here; changing it would
repoint every rotator that was ever synced.

Tolerant of the bare hub stand-ins the polar tests use: every hub attribute is
read through ``getattr``, so a stand-in without a device map simply has no
imaging camera to match and nothing is recorded.
"""
from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from typing import Any

from .events import bus
from .rotation import mod360

#: How far the rotator's mechanical angle may differ between the exposure and
#: the calibration and still count as "did not move". Far below any framing
#: tolerance the rotate loop works to (``RotatorConfig.tolerance_deg``), and far
#: above the read-back jitter of a rotator at rest.
MOVED_TOL_DEG = 0.1

#: A pier-side read made for this module gives up after this long and falls back
#: to the hub's cached reading. The hub's own read allows 30 s, which is right
#: for a flip decision and wrong for a line of bookkeeping in front of a solve.
PIER_READ_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class ExposureAngle:
    """What the rig looked like when a solve's frame was exposed.

    Read BEFORE the exposure by every live solve path (``exposure_context``).
    The per-frame WCS stamp can only read it from the capture snapshot, which is
    taken as the shutter closes, so for that path "at exposure" means "at the
    end of the exposure".
    """

    #: ``time.time()`` when this was read.
    at: float
    #: The camera device that took the frame. Compared by identity against the
    #: hub's imaging camera: anything else is not recorded.
    camera: Any
    #: The rotator device object read, or ``None`` when none was connected.
    rotator: Any
    #: Its mechanical angle then, deg [0, 360), or ``None`` when unreadable.
    mech_deg: float | None
    #: Whether it reported motion then (``None``: could not say).
    moving: bool | None
    #: ``"east"`` / ``"west"``, or ``None`` when nobody could say.
    pier_side: str | None


def _side(value: Any) -> str | None:
    side = str(getattr(value, "value", value) or "").lower()
    return side if side in ("east", "west") else None


def _cached_pier_side(hub: Any) -> str | None:
    fn = getattr(hub, "pier_side_cached", None)
    if not callable(fn):
        return None
    try:
        return _side(fn())
    except Exception:                    # noqa: BLE001 - bookkeeping, never fatal
        return None


async def _pier_side(hub: Any, *, live: bool) -> str | None:
    """The pier side, live (bounded) when asked, else the hub's cached reading.

    A live read that fails or times out falls back to the cache, which only
    ever repeats a real answer this mount gave within the last few minutes.
    """
    if live:
        fn = getattr(hub, "pier_side_now", None)
        if callable(fn):
            try:
                side = _side(await asyncio.wait_for(fn(), PIER_READ_TIMEOUT_S))
            except asyncio.CancelledError:
                raise
            except Exception:            # noqa: BLE001 - includes the timeout
                side = None
            if side is not None:
                return side
    return _cached_pier_side(hub)


async def exposure_context(hub: Any, camera: Any, *,
                           live_pier: bool = True) -> ExposureAngle:
    """Read what :func:`note_solved_rotation` will compare against. Call it
    immediately before the exposure. Never raises (except on cancellation).

    ``live_pier=False`` is for the capture path, which must not buy a mount
    round trip per frame: it takes the hub's cached pier side instead. A rig
    with no rotator never pays for a live pier read either, because without a
    rotator there is nothing to calibrate and the side is only informational.
    """
    devices = getattr(hub, "devices", None) or {}
    rot = devices.get("rotator")
    mech: float | None = None
    moving: bool | None = None
    if rot is not None and getattr(rot, "connected", False):
        try:
            mech = mod360(float(await rot.get_mechanical_position()))
        except asyncio.CancelledError:
            raise
        except Exception:                # noqa: BLE001 - read failure = unknown
            mech = None
        try:
            moving = bool(await rot.is_moving())
        except asyncio.CancelledError:
            raise
        except Exception:                # noqa: BLE001
            moving = None
    else:
        rot = None
    side = await _pier_side(hub, live=live_pier and rot is not None)
    return ExposureAngle(at=time.time(), camera=camera, rotator=rot,
                         mech_deg=mech, moving=moving, pier_side=side)


def _angle_between(a: float, b: float) -> float:
    return abs(((a - b + 180.0) % 360.0) - 180.0)


async def _calibration_refusal(hub: Any, context: ExposureAngle,
                               rot: Any) -> str | None:
    """Why the rotator must NOT be calibrated from this solve, or ``None``."""
    if rot is None or not getattr(rot, "connected", False):
        return "no rotator is connected"
    if context.rotator is None or context.mech_deg is None:
        return ("the rotator's angle was not read when the frame was exposed, "
                "so nothing ties this solve to a mechanical position")
    if context.rotator is not rot:
        return "the rotator was reconnected after the frame was exposed"
    if context.moving:
        return "the rotator was moving when the exposure started"
    try:
        moving_now = bool(await rot.is_moving())
        mech_now = mod360(float(await rot.get_mechanical_position()))
    except asyncio.CancelledError:
        raise
    except Exception as e:               # noqa: BLE001
        return f"the rotator could not be read ({e})"
    if moving_now:
        return "the rotator is moving"
    moved = _angle_between(mech_now, context.mech_deg)
    if moved > MOVED_TOL_DEG:
        return (f"the rotator moved {moved:.2f}° between the exposure and the "
                f"solve")
    side_now = await _pier_side(hub, live=True)
    if side_now != context.pier_side:
        return (f"the mount's pier side went {context.pier_side or 'unknown'} -> "
                f"{side_now or 'unknown'} between the exposure and the solve, "
                f"and a meridian flip turns the field 180° under a rotator that "
                f"never moved")
    return None


def _log_line(rec: dict) -> str:
    head = (f"sky angle PA {rec['pa_deg']:.1f}° from the {rec['source']} solve "
            f"(pier {rec['pier_side'] or 'unknown'})")
    if rec["calibrated"]:
        before = rec.get("rotator_before_deg")
        was = f"it read {before:.1f}°, " if before is not None else ""
        return (f"{head}; rotator calibrated: {was}now {rec['pa_deg']:.1f}° "
                f"(offset {rec['offset_deg']:.1f}°)")
    if rec["reason"] == "no rotator is connected":
        return f"{head}; no rotator to calibrate"
    return f"{head}; rotator NOT calibrated: {rec['reason']}"


async def note_solved_rotation(hub: Any, result: Any, *, source: str,
                               context: ExposureAngle | None) -> dict | None:
    """Record the sky angle an imaging-camera solve measured, and calibrate the
    rotator from it when that is provably right. See the module docstring.

    Returns the record, or ``None`` when the solve is not one this function
    takes: failed, no finite rotation, no context, or a frame from anything
    but the hub's imaging camera. The record is also stored on
    ``hub.last_sky_angle``, but only when its ``exposed_at`` is at or after
    the one already held there (#292): two solves can finish out of order, and
    the held slot tracks the newest EXPOSURE, not whichever solve happened to
    finish last.

    Never raises into the solve path that called it (cancellation aside): a
    solve that cannot calibrate a rotator is still a perfectly good solve for
    whatever it was run for. Callers whose whole purpose IS the calibration
    (``sync_rotator_to_sky``, ``rotate_to_pa``) check ``calibrated`` and raise
    ``reason`` themselves.
    """
    if result is None or not getattr(result, "success", False):
        return None
    try:
        raw = float(getattr(result, "rotation_deg", None))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(raw):
        return None
    # #146: a solver that reported no rotation leaves rotation_deg at 0.0, and
    # calibrating the rotator to that 0 is the one thing this must never do.
    if not getattr(result, "rotation_known", True):
        return None
    if context is None:
        return None
    devices = getattr(hub, "devices", None) or {}
    imaging = devices.get("camera")
    if context.camera is None or context.camera is not imaging:
        return None
    pa = mod360(raw)
    rot = devices.get("rotator")
    rec: dict[str, Any] = {
        "pa_deg": pa,
        "exposed_at": context.at,
        "solved_at": time.time(),
        "source": source,
        "pier_side": context.pier_side,
        "camera": getattr(context.camera, "name", "") or "",
        "calibrated": False,
        "reason": None,
        "mechanical_deg": context.mech_deg,
        "rotator_before_deg": None,
        "offset_deg": None,
    }
    try:
        reason = await _calibration_refusal(hub, context, rot)
    except asyncio.CancelledError:
        raise
    except Exception as e:               # noqa: BLE001 - total by contract
        reason = f"the calibration check failed ({e})"
    if reason is None:
        try:
            before = mod360(float(await rot.get_position()))
            await rot.sync(pa)
            rec.update(calibrated=True, rotator_before_deg=before,
                       offset_deg=float(rot.sync_offset_deg))
        except asyncio.CancelledError:
            raise
        except Exception as e:           # noqa: BLE001
            reason = f"the rotator refused the calibration ({e})"
    rec["reason"] = reason
    try:
        # #292: this solve's record wins the held slot only when it is of an
        # EXPOSURE at or after the one already held, not merely because it is
        # the solve that happened to FINISH last. The per-frame WCS stamp
        # solves a saved light in the background, seconds behind the shutter,
        # so its record can land after a later centring solve already wrote
        # the newer one -- and without this guard it would overwrite it with
        # a stale angle. The solve is still published and logged either way;
        # only the bookkeeping `hub` exposes as "the current angle" is held
        # back. ">=", not ">": a second solve of the SAME exposure (the same
        # context handed to more than one path) must still be able to update it.
        held = getattr(hub, "last_sky_angle", None)
        held_at = held.get("exposed_at") if isinstance(held, dict) else None
        if held_at is None or rec["exposed_at"] >= held_at:
            hub.last_sky_angle = rec
    except Exception:                    # noqa: BLE001 - a frozen stand-in hub
        pass
    bus.publish("sky_angle", **rec)
    bus.log("info", _log_line(rec), "rotator")
    return rec
