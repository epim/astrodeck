"""Native three-point polar alignment (TPPA) — AstroDeck's own Rust engine.

The third driver behind :class:`PolarAlignSession`, selected for a native
(Alpaca/sim) rig when the capability resolver picks ``astrodeck``. It reproduces
NINA's TPPA workflow without NINA:

  PHASE "measuring"  — capture a short exposure, plate solve it (NO sync — a sync
    would corrupt the very axis error we are measuring), record (RA, Dec, t),
    then rotate the mount in RA by a configurable step; repeat three times. Every
    slew is safety-gated: the sun-exclusion cone (:meth:`Hub._check_solar`) is
    checked before each rotation so we never drive the optics through the Sun,
    and the motion-epoch fence + pause/stop are honored so a STOP or an abort
    aborts cleanly. The three solves feed
    :func:`astrodeck_native.tppa_from_three`, which fits the mount's RA axis and
    the initial polar error.

  PHASE "adjusting" — with the axis model frozen, keep capturing/solving and call
    :func:`astrodeck_native.tppa_update` so the live error refreshes while the
    user turns the altitude/azimuth knobs, until the error crosses the "aligned"
    threshold or the user stops.

Errors are published on the SAME ``polar`` event the UI reticle already consumes
(already arcminutes) with ``source:"native"`` plus additive fields the native
wizard uses: ``phase`` (measuring|adjusting), ``point_index``, and the
per-axis knob ``*_direction`` hints.

The Rust wheel is imported GUARDED: without it the provider degrades to a clear
"native engine not installed" error and the rest of the suite stays green.
"""
from __future__ import annotations

import asyncio
from typing import Any

from ..devices.base import DeviceError
from ..events import bus

# --- guarded native import -------------------------------------------------
try:  # pragma: no cover - trivially guarded; exercised both ways in tests
    import astrodeck_native as _native
    NATIVE_AVAILABLE = True
except ImportError:  # pragma: no cover
    _native = None
    NATIVE_AVAILABLE = False

# Rotate the mount this far in RA between the three measurement points. TPPA
# wants a clean ~10-15° arc — far enough to trace a well-conditioned circle,
# short enough to stay in a solvable sky region.
_RA_STEP_HOURS = 12.0 / 15.0  # 12° expressed in hours of RA

# Short exposure for the solve frames (the sim renders instantly; a real rig
# wants just enough signal for ASTAP). Kept small so the loop stays responsive.
_SOLVE_EXPOSURE_S = 0.3

# "Aligned — stop here" threshold and the adjustment-phase cadence + safety cap
# (the phase otherwise runs until the user stops; the cap keeps a forgotten
# session from spinning forever).
#: Refuse to measure when the telescope is closer to a celestial pole than this.
#:
#: Three-point polar alignment derives the mount's RA axis by fitting a circle
#: through three plate-solved positions as the mount rotates in RA. The circle's
#: radius IS the angular distance from that axis, so pointing near the pole
#: makes the fit ill-conditioned: each solve's small residual is amplified when
#: the circle is extrapolated to an axis direction. At 20 degrees the lever arm
#: is workable; at 5 it is not.
#:
#: Measured on the rig 2026-08-03: a run at Dec +85 (5 degrees from the pole)
#: reported 580 arcmin of error -- 9.7 degrees -- and told the operator to
#: "adjust the mount", on a rig that had just produced 16 unguided 10 s subs at
#: a steady HFR. Acting on that number would have wrecked a working alignment.
#: The engine had already raised position_angle_spread_large and
#: initial_error_large and reported the figure anyway.
#:
#: The trap is easy to fall into and has nothing to do with carelessness: park
#: leaves this mount pointing at the pole, so starting TPPA straight after a
#: park lands here every time.
MIN_POLE_DISTANCE_DEG = 20.0

_DONE_THRESHOLD_ARCMIN = 1.0
_ADJUST_INTERVAL_S = 1.0
_MAX_ADJUST_UPDATES = 240


async def run_native(session: Any, hub: Any) -> None:
    """Drive the native TPPA procedure for ``session`` on ``hub``.

    Entry point registered by :meth:`PolarAlignSession.start`. Owns the whole
    measuring→adjusting→terminal lifecycle and maps any user-presentable failure
    to a terminal ``polar{state:"error"}``. A motion-epoch advance (STOP, park,
    deadman, safety abort) is surfaced by ``_check_alive`` as a ``DeviceError``
    (not a ``CancelledError``) specifically so this handler always publishes a
    terminal event — the UI reticle must never observe a stale
    ``state:"running"``. True task cancellation (``session.stop()`` cancelling
    this coroutine directly) still re-raises ``CancelledError`` so it unwinds
    normally and ``stop()`` can publish the idle terminal state."""
    if not NATIVE_AVAILABLE:
        session._publish(state="error", source="native",
                         message="native engine not installed")
        return
    try:
        await _drive(session, hub)
    except asyncio.CancelledError:
        raise
    except DeviceError as e:
        session._publish(state="error", source="native", message=str(e))
        bus.log("error", f"native TPPA: {e}", "polar")
    except Exception as e:  # any engine/geometry failure -> clean terminal error
        session._publish(state="error", source="native",
                         message=f"native TPPA failed: {e}")
        bus.log("error", f"native TPPA failed: {e}", "polar")


# ------------------------------------------------------------------- internals

async def _drive(session: Any, hub: Any) -> None:
    tel = hub.require("telescope")
    hub.require("camera")  # fail fast with a clear error if no camera
    from .. import providers as _providers
    solver = _providers.pick_solver(hub)
    site = _site_dict(hub)

    # Refuse BEFORE any slew when the scope is parked at / near a pole. Checked
    # against the mount's own claim, which is cheap and catches the common case;
    # the authoritative check is on the first SOLVED position below, because a
    # mount can be wrong about where it points (2026-08-02: this one was 50 deg
    # out after a restart).
    await _refuse_near_pole(await _mount_dec(tel), "the mount reports")

    # TAKE THE CAMERA BEFORE THE FIRST SLEW. TPPA is a camera-owning MOUNT-MOTION
    # path — it runs solve, rotate 12 deg in RA, solve, rotate 12 deg, solve — so
    # losing the camera part-way does not merely fail, it abandons the tube 12 or
    # 24 degrees from wherever the user pointed it, with the session dead and only
    # a log line about a camera to explain it. A live loop with 30 s subs holds
    # the capture lock most of the time, which makes that the LIKELY outcome
    # rather than the unlucky one.
    #
    # Deliberately AFTER pick_solver and AFTER the mount-side pole refusal: both
    # can end this run before a single exposure, and amputating someone's Live
    # View for a run that was about to be refused anyway is its own small
    # betrayal. It is a no-op when nothing is running.
    #
    # This covers the adjust-phase solves too — they reuse the same camera and
    # the loop cannot restart itself.
    await hub.yield_camera_for("polar alignment")

    # Motion fence (W3.7): snapshot the epoch; a STOP/abort/safety halt bumps it,
    # and we abandon rather than keep slewing a mount someone just halted.
    epoch = getattr(hub, "_motion_epoch", 0)

    session._publish(state="running", source="native", phase="measuring",
                     progress=0.0, message="native TPPA: measuring point 1/3")
    bus.log("info", "native TPPA started (measuring)", "polar")

    # ---- PHASE measuring: 3 × capture → solve → (rotate in RA) -------------
    solves: list[dict] = []
    for i in range(3):
        _check_alive(hub, epoch)
        await _wait_if_paused(session)
        frame, result, geom = await _capture_and_solve(hub, solver)
        if i == 0:
            # The authoritative check: where the sky says we are, not where the
            # mount claims. Costs one exposure that was being taken anyway.
            await _refuse_near_pole(result.dec_deg, "the plate solve puts you")
        solves.append({
            "ra_hours": result.ra_hours,
            "dec_deg": result.dec_deg,
            "timestamp_unix_s": frame.timestamp,
            "position_angle_deg": result.rotation_deg or 0.0,
        })
        session._publish(state="running", source="native", phase="measuring",
                         progress=0.1 + 0.15 * (i + 1), point_index=i,
                         message=f"native TPPA: measured point {i + 1}/3")
        if i < 2:
            await _rotate_in_ra(hub, tel, epoch, result)

    # ---- fit the axis + initial error -------------------------------------
    opts = _options(hub, geom)
    out = _native.tppa_from_three(solves, site, opts)
    model = out["model"]
    err = out["error"]
    _publish_error(session, err, phase="adjusting", point_index=2, progress=0.6,
                   message="adjust the mount")
    bus.log("info",
            f"native TPPA solved: total {err['total_arcmin']:.1f}' "
            f"(az {err['az_arcmin']:.1f}', alt {err['alt_arcmin']:.1f}')", "polar")

    # ---- PHASE adjusting: live re-scale while the user turns the knobs -----
    for _ in range(_MAX_ADJUST_UPDATES):
        _check_alive(hub, epoch)
        await _wait_if_paused(session)
        await asyncio.sleep(_ADJUST_INTERVAL_S)
        frame, result, _ = await _capture_and_solve(hub, solver)
        solve = {
            "ra_hours": result.ra_hours,
            "dec_deg": result.dec_deg,
            "timestamp_unix_s": frame.timestamp,
            "position_angle_deg": result.rotation_deg or 0.0,
        }
        try:
            err = _native.tppa_update(model, solve)
        except Exception as e:
            # A degenerate live update (parallel correction lines / collapsed
            # leg) must not kill the session — surface it and keep going.
            bus.log("warning", f"native TPPA update skipped: {e}", "polar")
            continue
        done = err["total_arcmin"] <= _DONE_THRESHOLD_ARCMIN
        _publish_error(session, err, phase="adjusting", point_index=2,
                       progress=1.0 if done else 0.85,
                       message="polar aligned" if done else "adjust the mount",
                       state="done" if done else "running")
        if done:
            bus.log("info", "native TPPA complete (within threshold)", "polar")
            return

    # Safety cap reached (session left running): settle on a terminal state
    # rather than spin. The last published error stands.
    session._publish(state="done", source="native", phase="adjusting",
                     progress=1.0, message="alignment session ended")


async def _capture_and_solve(hub: Any, solver: Any):
    """One short exposure → plate solve (no sync). Returns (frame, SolveResult,
    geom) where ``geom`` is (arcsec_per_pixel, width_px, height_px) for the
    continuous-update image model."""
    from ..hub import CAPTURE_DIR  # lazy: avoid a hub<->polar import cycle
    cam = hub.require("camera")
    tel = hub.require("telescope")
    # Pointing hint (drives ASTAP's near search; lets a refusing SimSolver fail
    # loudly on a real rig instead of inventing a solve). Bring the mount frame
    # back to J2000 like the hub's own solve path (no-op for sim/NINA).
    try:
        ra_hint, dec_hint = await tel.get_position()
        if ra_hint is not None:
            ra_hint, dec_hint = await hub.from_mount_frame(tel, ra_hint, dec_hint)
    except Exception:
        ra_hint = dec_hint = None

    async with hub.exposure_guard("polar solve"):
        frame = await cam.expose(_SOLVE_EXPOSURE_S, 200, 30, binning=1)
    try:
        await hub._publish_preview(frame)
    except Exception:
        pass

    tmp = CAPTURE_DIR / "_solve" / "polar.fits"
    from ..imaging import save_fits
    await asyncio.to_thread(save_fits, frame, tmp,
                            ra_hours=ra_hint, dec_deg=dec_hint, instrument=cam.name)
    opt = hub.effective_optics()
    fov_hint = opt.get("fov_h_deg") or None
    result = await solver.solve(tmp, ra_hint=ra_hint, dec_hint=dec_hint,
                                fov_deg_hint=fov_hint)
    if not result.success:
        raise DeviceError(f"polar plate solve failed: {result.message}")

    h, w = frame.data.shape
    scale = (result.pixel_scale_arcsec or opt.get("image_scale_arcsec_px") or 1.55)
    return frame, result, (float(scale), float(w), float(h))


async def _rotate_in_ra(hub: Any, tel: Any, epoch: int, result: Any) -> None:
    """Rotate the mount in RA by one step, safety-gated. Never slews through the
    sun cone; abandons if the motion fence advanced (an abort/STOP landed)."""
    _check_alive(hub, epoch)
    cur_ra, cur_dec = await tel.get_position()
    target_ra = (cur_ra + _RA_STEP_HOURS) % 24.0
    # Sun-exclusion cone: refuse to rotate into a daytime pointing (defense in
    # depth — the same guard the hub's motion paths use). Raises DeviceError,
    # which run_native turns into a terminal error state.
    hub._check_solar(target_ra, cur_dec)
    bus.log("info", f"native TPPA: rotating RA to {target_ra:.2f}h", "polar")
    await tel.slew(target_ra, cur_dec)


def _check_alive(hub: Any, epoch: int) -> None:
    """Abort if the motion fence advanced under us — a STOP, safety halt, park,
    or deadman bumped the epoch, so this session must not keep committing
    motion. Raises DeviceError (not CancelledError) so run_native's handler
    always publishes a terminal polar{state:"error"} event instead of letting
    the abort unwind silently and leave the UI reticle stuck on stale
    state:"running"."""
    if getattr(hub, "_motion_epoch", epoch) != epoch:
        raise DeviceError("polar alignment fenced by a motion abort")


async def _wait_if_paused(session: Any) -> None:
    """Block while the session is paused (the user hit Pause mid-run). ``stop()``
    cancels this task, so a cancel still unwinds a paused session."""
    while getattr(session, "_native_paused", False):
        await asyncio.sleep(0.1)


def _site_dict(hub: Any) -> dict:
    s = hub.site
    return {"latitude_deg": s["latitude"], "longitude_deg": s["longitude"],
            "elevation_m": s.get("elevation_m", 0.0)}


def _options(hub: Any, geom: tuple) -> dict:
    scale, w, h = geom
    opts: dict[str, Any] = {"arcsec_per_pixel": scale,
                            "image_width_px": w, "image_height_px": h}
    # The simulator has no atmosphere, so its solves carry no refraction — turn
    # the engine's refraction model off (pressure 0) so the geometric sim
    # positions invert exactly to the injected error. Real rigs keep the
    # standard-atmosphere default.
    if getattr(hub, "mode", None) == "sim":
        opts["pressure_hpa"] = 0.0
    return opts


def _publish_error(session: Any, err: dict, *, phase: str, point_index: int,
                   progress: float, message: str, state: str = "running") -> None:
    """Publish a native TPPA error on the canonical ``polar`` schema (errors are
    already arcminutes) plus the additive native fields the wizard consumes."""
    session._publish(
        state=state, source="native", phase=phase, point_index=point_index,
        progress=progress, message=message,
        az_error=round(err["az_arcmin"], 2),
        alt_error=round(err["alt_arcmin"], 2),
        az_direction=err.get("az_direction"),
        alt_direction=err.get("alt_direction"),
        flags=err.get("flags", []),
    )


async def _mount_dec(tel: Any) -> float | None:
    """The mount's claimed declination, or None when it cannot say."""
    try:
        _ra, dec = await tel.get_position()
        return None if dec is None else float(dec)
    except Exception:  # noqa: BLE001 — a mount that cannot report is not a refusal
        return None


async def _refuse_near_pole(dec_deg: float | None, whose: str) -> None:
    """Raise a user-presentable DeviceError when too close to a celestial pole.

    Naming a REACHABLE fix matters more than naming the fault: the operator did
    nothing wrong -- park leaves the mount at the pole, so the obvious "align
    now" straight after a park lands exactly here. So the message says where to
    point instead, not merely that this spot is invalid.

    ``dec_deg`` None (a mount that will not report) is NOT a refusal: the solved
    check downstream still runs, and refusing on a missing reading would break
    rigs whose mounts are simply quiet.
    """
    if dec_deg is None:
        return
    pole_distance = 90.0 - abs(dec_deg)
    if pole_distance >= MIN_POLE_DISTANCE_DEG:
        return
    raise DeviceError(
        f"too close to the celestial pole to measure: {whose} "
        f"{abs(dec_deg):.1f}° declination, {pole_distance:.1f}° from the "
        f"pole (needs {MIN_POLE_DISTANCE_DEG:.0f}°). Three-point alignment "
        "fits a circle through three solved positions as the mount turns in RA, "
        "and that circle's radius is your distance from the pole — this close, "
        "the fit cannot resolve an axis and would report a large error that is "
        "not real. Slew to a target nearer the celestial equator, ideally within "
        "about 30° of the meridian, and start again. (A parked mount points at "
        "the pole, so this is the usual state right after parking.)")
