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
from ..sequence.schedule import hour_angle_h
from .session import wait_if_paused

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

#: Consecutive failed live updates after which the displayed number is treated
#: as stale rather than current. At the 1 s cadence this is under a minute — long
#: enough to ride out a passing cloud, short enough that "alignment session
#: ended" never means "the panel froze while you were turning a bolt".
_STALE_UPDATE_LIMIT = 30

#: Seconds one leg of the measurement arc takes: slew, settle, expose, solve.
#:
#: Used ONLY to project where the sky will be when each point is actually
#: reached. The arc is not instantaneous, and for a western arc the sidereal
#: clock pushes hour angle the SAME way the step does, so projecting all three
#: points at "now" makes the altitude check optimistic exactly where it matters.
#: Measured on the rig 2026-08-06: consecutive point log lines 19 s apart on a
#: clean run, 43 s on one that included a re-slew. Rounded up.
_ARC_LEG_SECONDS = 45.0

#: Log the measured position-angle spread once it exceeds this. The three frames
#: are supposed to differ by a pure RA rotation; a spread this large means the
#: camera/pier angle moved between them (a meridian flip, a rotator step), and
#: the fit is then measuring that motion as well as the axis error. The engine
#: raises ``position_angle_spread_large`` for it — a WARNING, never a refusal
#: (docs/native-parity/algorithms/tppa-polar-alignment.md) — but a headless or
#: REST operator never sees the flag, so name the number in the log too.
_PA_SPREAD_WARN_DEG = 5.0

#: Largest polar error this routine will report as a MEASUREMENT rather than a
#: failed fit.
#:
#: The axis fit is a plane through three points on a small circle, and its
#: conditioning is savage: with the 12 degree step above, the two chords are
#: nearly parallel, so the plane normal is a small cross product. Measured
#: numerically against this exact geometry (38 degree pole distance, three 12
#: degree steps), an out-of-plane error of 0.05 degrees at the MIDDLE point
#: swings the fitted axis by 2.2 degrees -- a 44x amplification. The fit through
#: three points is exact by construction, so there are no residuals to check and
#: nothing inside the engine notices.
#:
#: That leaves physical plausibility as the honest test. An altitude/azimuth
#: bolt has perhaps 15 degrees of travel, and a tripod cannot point its RA axis
#: into the ground at all. So a fit that lands outside this bound is not a large
#: error, it is a failed measurement, and reporting it as a number with a
#: "adjust the mount" instruction sends someone out to turn a bolt 121 degrees.
MAX_PLAUSIBLE_ERROR_DEG = 30.0

#: The arc actually achieved between two measurement points, as a fraction of
#: the rotation that was commanded, outside which the run is a failed
#: measurement rather than a result.
#:
#: A mount that ACCEPTS a goto and does not honour it is the quietest way this
#: routine can lie. The AM5's settle loop compares each position sample to the
#: PREVIOUS one, never to the commanded target, so a mount that never moves
#: satisfies "stopped moving" in about 1.5 s and the slew returns success. Two
#: of three points then land on the same sky position, the engine fits them
#: happily (it refuses only the all-three-identical case), and the operator is
#: shown 0.00' and "polar aligned" on a mount that was never measured. That is
#: the 2026-08-06 failure with the sign flipped: a confident PERFECT number
#: instead of a confident huge one, and far more likely to be believed.
#:
#: The driver is the only layer that CAN catch this, because it is the only one
#: that knows a rotation was commanded between two frames. The bounds are wide
#: on purpose: the fit itself stays sound down to roughly 2-5 degrees of arc, so
#: this is a "did the mount do roughly what it was told" test, not a precision
#: one. The upper bound catches the opposite failure — an overshoot or a slew
#: that took a completely different path.
MIN_ARC_FRACTION = 0.5
MAX_ARC_FRACTION = 1.5

#: Refuse to MEASURE below this altitude.
#:
#: The meridian is the highest point of any track, so stepping AWAY from it —
#: which _ra_step_hours now always does — descends in both directions. That
#: makes a low start deterministic rather than unlucky: from 8.9 degrees, the
#: two 12-degree steps land at 1.1 degrees and then 6.1 degrees BELOW the
#: horizon, and nothing else in the polar path looks at altitude
#: (hub._check_horizon's only caller is the user-initiated goto).
#:
#: Set at the altitude where a frame stops being worth measuring rather than at
#: the horizon itself: differential refraction and extinction near the horizon
#: corrupt the very field centres the fit is built from. Kept low enough not to
#: refuse runs that would have worked.
MIN_MEASUREMENT_ALT_DEG = 10.0

#: Log a warning, but do not refuse, between here and MIN_MEASUREMENT_ALT_DEG.
LOW_MEASUREMENT_ALT_DEG = 20.0


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

    # Sidereal tracking must be RUNNING before the first frame. tppa_update
    # attributes every bit of field motion to the operator's knobs, so with
    # tracking off the sky's own 0.25 deg/minute drift is reported as polar
    # error that grows without limit — measured at 2x to 15x truth within an
    # hour, with no warning, because the update's quality flags are frozen from
    # the initial fit and structurally cannot fire. Every other motion path in
    # the server already asserts this before slewing; this one never did, and
    # park / find_home both LEAVE tracking off, which is exactly the state a
    # mount is in when someone reaches for Align.
    await _ensure_tracking(tel)

    session._publish(state="running", source="native", phase="measuring",
                     progress=0.0, message="native TPPA: measuring point 1/3")
    bus.log("info", "native TPPA started (measuring)", "polar")

    # ---- PHASE measuring: 3 × capture → solve → (rotate in RA) -------------
    solves: list[dict] = []
    step_hours: float | None = None
    for i in range(3):
        _check_alive(hub, epoch)
        await wait_if_paused(session)
        frame, result, geom = await _capture_and_solve(hub, solver)
        if i == 0:
            # The authoritative check: where the sky says we are, not where the
            # mount claims. Costs one exposure that was being taken anyway.
            await _refuse_near_pole(result.dec_deg, "the plate solve puts you")
            # Now that the SOLVED position is known, project the whole arc and
            # refuse before committing the mount to it.
            step_hours = _ra_step_hours(hub, result.ra_hours)
            _refuse_low_arc(hub, result, step_hours)
        else:
            # The mount was told to rotate before this frame. Verify it did,
            # against the sky rather than against the mount's own report.
            _refuse_if_it_did_not_arrive(solves[-1], result, step_hours, i)
        solves.append({
            "ra_hours": result.ra_hours,
            "dec_deg": result.dec_deg,
            "timestamp_unix_s": frame.timestamp,
            "position_angle_deg": result.rotation_deg or 0.0,
        })
        _log_measurement(hub, i, result, frame)
        session._publish(state="running", source="native", phase="measuring",
                         progress=0.1 + 0.15 * (i + 1), point_index=i,
                         message=f"native TPPA: measured point {i + 1}/3")
        if i < 2:
            # Pause BLOCKS the 12° slew. Committing one more rotation after the
            # user hit Pause leaves the tube somewhere they did not put it and
            # did not ask for — the one irreversible thing this loop does. The
            # motion-epoch fence inside _rotate_in_ra still raises independently.
            await wait_if_paused(session)
            await _rotate_in_ra(hub, tel, epoch, result, step_hours)

    # ---- fit the axis + initial error -------------------------------------
    opts = _options(hub, geom)
    out = _native.tppa_from_three(solves, site, opts)
    model = out["model"]
    err = out["error"]
    # Log the raw fit BEFORE the plausibility gate: when the gate refuses, the
    # numbers it refused are the evidence for why, and a refusal that throws
    # away its own inputs is the thing that made the 2026-08-06 run take a
    # second night to diagnose.
    bus.log("info",
            f"native TPPA solved: total {err['total_arcmin']:.1f}' "
            f"(az {err['az_arcmin']:.1f}', alt {err['alt_arcmin']:.1f}')", "polar")
    _log_pa_spread(err)
    _reject_implausible_fit(err, hub)  # raises rather than publish a wrong number
    # "adjust the mount" on a fit that is ALREADY inside the aligned threshold
    # sends someone to the bolts to chase a number the same routine would call
    # done one second later, when the adjust loop applies the same comparison.
    already = err["total_arcmin"] <= _DONE_THRESHOLD_ARCMIN
    _publish_error(session, err, phase="adjusting", point_index=2, progress=0.6,
                   message="polar aligned" if already else "adjust the mount")

    # ---- PHASE adjusting: live re-scale while the user turns the knobs -----
    updates_published = 0
    stale_updates = 0
    for _ in range(_MAX_ADJUST_UPDATES):
        _check_alive(hub, epoch)
        await wait_if_paused(session)
        await asyncio.sleep(_ADJUST_INTERVAL_S)
        # Pause most often lands DURING the 1 s cadence sleep. Without this
        # second check the flag is only read once per interval — one full extra
        # exposure fires after the user hit Pause, and on a real rig that is a
        # shutter they asked to stop.
        await wait_if_paused(session)
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
            stale_updates += 1
            continue
        updates_published += 1
        stale_updates = 0
        done = err["total_arcmin"] <= _DONE_THRESHOLD_ARCMIN
        _publish_error(session, err, phase="adjusting", point_index=2,
                       progress=1.0 if done else 0.85,
                       message="polar aligned" if done else "adjust the mount",
                       state="done" if done else "running")
        if done:
            bus.log("info", "native TPPA complete (within threshold)", "polar")
            return

    # Safety cap reached (session left running): settle on a terminal state
    # rather than spin. The last published error stands — UNLESS the number on
    # screen is not live. Reporting "done" for a phase whose panel is frozen
    # tells the operator their adjustment was tracked when it was not.
    #
    # Keyed on RECENCY, not on "did one ever succeed". Counting successes alone
    # meant a single update landing in the first second and the next 239 all
    # failing was indistinguishable, on screen, from a session that followed the
    # knobs the whole way — the same defect one notch weaker.
    if updates_published and stale_updates < _STALE_UPDATE_LIMIT:
        session._publish(state="done", source="native", phase="adjusting",
                         progress=1.0, message="alignment session ended")
    elif updates_published:
        bus.log("warning", f"native TPPA: the last {stale_updates} live updates "
                "failed — the number on screen is stale", "polar")
        session._publish(
            state="error", source="native", phase="adjusting", progress=1.0,
            message=f"the live error stopped updating: the last {stale_updates} "
                    "measurements failed, so the number on screen is older than "
                    "your most recent adjustment and does not reflect it. Check "
                    "that the sky is clear and the camera is still solving, then "
                    "run the alignment again.")
    else:
        bus.log("warning", "native TPPA: every live update failed — the panel "
                "never moved off the initial fit", "polar")
        session._publish(
            state="error", source="native", phase="adjusting", progress=1.0,
            message="the live error could not be updated: every measurement "
                    "during adjustment failed, so the number on screen is still "
                    "the original fit and does not reflect anything you changed. "
                    "Check that the sky is clear and the camera is still "
                    "solving, then run the alignment again.")


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


def _ra_step_hours(hub: Any, cur_ra_hours: float) -> float:
    """One RA step, SIGNED so the measurement arc moves AWAY from the meridian.

    The direction is not cosmetic, it decides whether the run is measurable at
    all. ``HA = LST - RA``, so stepping RA *up* walks the tube EAST (hour angle
    falls) and stepping RA *down* walks it WEST. Always stepping up — what this
    did before — marches a tube that starts west of the meridian straight
    through it, and a German-equatorial mount answers a meridian crossing with a
    pier flip. The flip rotates the camera 180 degrees and swings the tube
    around the mount, so the three frames are no longer related by the pure RA
    rotation the whole fit is built on.

    Measured on the rig 2026-08-06, LST 19.43h: the run started at HA +0.69h and
    stepped to -0.12h and -0.92h. The mount flipped between point 1 and point 2,
    the engine reported a position-angle spread of 179.7 degrees, and the fitted
    RA axis came out 80.8 degrees BELOW the horizon -- published to the operator
    as 7271 arcminutes of polar error with a "adjust the mount" instruction.

    So: west of the meridian, keep going west; east of it, keep going east. Two
    steps span 24 degrees of hour angle, which never reaches the meridian from
    either side as long as the first step moves away from it.
    """
    ha = hour_angle_h(cur_ra_hours, hub.site["longitude"])
    # HA > 0 => west of the meridian, so step further west, which is RA DOWN.
    # HA <= 0 => east (or exactly on it), so step further east, which is RA UP.
    return -_RA_STEP_HOURS if ha > 0.0 else _RA_STEP_HOURS


async def _rotate_in_ra(hub: Any, tel: Any, epoch: int, result: Any,
                        step: float | None = None) -> None:
    """Rotate the mount in RA by one step, safety-gated. Never slews through the
    sun cone; never walks across the meridian (:func:`_ra_step_hours`); abandons
    if the motion fence advanced (an abort/STOP landed).

    ``step`` is decided ONCE from the first solved position and passed in, so
    every leg of the arc goes the same way. Re-deciding per leg would let a run
    that started beside the meridian reverse direction halfway — walking back
    over the point it came from and collapsing the arc — because the hour angle
    is re-read each time. Defaults to deciding from the mount's current position
    for callers that have no arc in progress."""
    _check_alive(hub, epoch)
    cur_ra, cur_dec = await tel.get_position()
    if step is None:
        step = _ra_step_hours(hub, cur_ra)
    target_ra = (cur_ra + step) % 24.0
    # Sun-exclusion cone: refuse to rotate into a daytime pointing (defense in
    # depth — the same guard the hub's motion paths use). Raises DeviceError,
    # which run_native turns into a terminal error state.
    hub._check_solar(target_ra, cur_dec)
    bus.log("info",
            f"native TPPA: rotating RA to {target_ra:.2f}h "
            f"({'west' if step < 0 else 'east'}, away from the meridian)",
            "polar")
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


async def _ensure_tracking(tel: Any) -> None:
    """Start sidereal tracking if it is not already running.

    Best-effort: a mount that cannot report or set tracking is not a reason to
    refuse an alignment, and several supported drivers are quiet about it. The
    failure this prevents is silent, not loud — see the call site."""
    try:
        if await tel.get_tracking():
            return
    except Exception:  # noqa: BLE001 — a mount that will not say is not a refusal
        return
    try:
        await tel.set_tracking(True)
        bus.log("info",
                "native TPPA: sidereal tracking was off — started it, because "
                "the live error would otherwise measure the sky turning rather "
                "than the mount's axis", "polar")
    except Exception as e:  # noqa: BLE001
        bus.log("warning",
                f"native TPPA: could not start tracking ({e}); the live error "
                "during adjustment will drift if the mount is not tracking",
                "polar")


def _ra_wrap_hours(delta: float) -> float:
    """An RA difference wrapped into (-12, 12] hours, so an arc across 0h is not
    read as a 23-hour jump."""
    return ((delta + 12.0) % 24.0) - 12.0


def _refuse_if_it_did_not_arrive(previous: dict, result: Any,
                                 step_hours: float | None, index: int) -> None:
    """Verify the mount actually made the rotation it was told to make.

    Compares consecutive SOLVED positions, not the mount's own report: a mount
    that accepts a goto and ignores it also reports arriving. See
    :data:`MIN_ARC_FRACTION` for why the driver is the only layer that can see
    this and what it costs when nobody does.
    """
    if not step_hours:
        return
    achieved = _ra_wrap_hours(result.ra_hours - previous["ra_hours"])
    fraction = achieved / step_hours          # signed: negative = went the wrong way
    if MIN_ARC_FRACTION <= fraction <= MAX_ARC_FRACTION:
        return
    moved_deg = abs(achieved) * 15.0
    asked_deg = abs(step_hours) * 15.0
    if fraction < 0:
        what = (f"moved {moved_deg:.2f}° the WRONG WAY in right ascension")
    elif abs(achieved) < 1e-4:
        what = "did not move at all"
    elif fraction < MIN_ARC_FRACTION:
        what = f"moved only {moved_deg:.2f}° of the {asked_deg:.1f}° it was asked for"
    else:
        what = f"moved {moved_deg:.2f}°, far past the {asked_deg:.1f}° it was asked for"
    raise DeviceError(
        f"the mount accepted the rotation before point {index + 1} and then "
        f"{what}. Three-point alignment measures the axis from how the sky moves "
        "as the mount turns, so a rotation that did not happen is not a small "
        "error — the points collapse together and the fit reports a confident "
        "number for a mount it never measured (a stuck mount reads as a perfect "
        "0.00'). Nothing has been reported. Check that the mount is unparked, "
        "tracking, clear of its limits, and that no other slew is competing, "
        "then run the alignment again.")


def _refuse_low_arc(hub: Any, result: Any, step_hours: float) -> None:
    """Refuse a measurement arc that would drive the tube into the ground.

    The meridian is the highest point of any track, so the arc — which always
    steps AWAY from the meridian — always descends. Projected from the SOLVED
    first point, not the mount's claim. See :data:`MIN_MEASUREMENT_ALT_DEG`."""
    import time as _time

    from ..catalog.coords import altaz
    lat = float(hub.site["latitude"])
    lon = float(hub.site["longitude"])
    now = _time.time()
    # Each point is projected at the time it will actually be REACHED, not at
    # "now" — see _ARC_LEG_SECONDS.
    alts = [altaz(result.ra_hours + step_hours * n, result.dec_deg, lat, lon,
                  now + _ARC_LEG_SECONDS * n)[0]
            for n in range(3)]
    lowest = min(alts)
    if lowest >= LOW_MEASUREMENT_ALT_DEG:
        return
    track = " → ".join(f"{a:.1f}°" for a in alts)
    if lowest >= MIN_MEASUREMENT_ALT_DEG:
        bus.log("warning",
                f"native TPPA: the arc runs low ({track} altitude). Refraction "
                "and extinction near the horizon move the field centres this "
                "fit is built from, so treat the result as coarse.", "polar")
        return
    raise DeviceError(
        f"this alignment would drive the telescope too low: the three "
        f"measurement points sit at {track} altitude, and below "
        f"{MIN_MEASUREMENT_ALT_DEG:.0f}° a plate solve is measuring refraction "
        "as much as it is measuring the sky. The arc always moves AWAY from the "
        "meridian (crossing it would flip the mount mid-measurement), and away "
        "from the meridian is always downhill toward the horizon — so the fix "
        "is to start closer to it. Point at something within an hour or so of "
        "the meridian, at least 20° from the pole, and start again.")


def _log_measurement(hub: Any, index: int, result: Any, frame: Any) -> None:
    """Record what a measurement point actually was.

    The fit consumes three solved positions and publishes one number. Until this
    existed, a run that produced a nonsense number left NO record of the three
    positions it was computed from, so the only way to tell a bad solve from a
    mount that never arrived from a genuine misalignment was to run it again and
    watch. Hour angle is included because the sign of it is what says whether
    the arc is walking toward the meridian.

    Best-effort by construction. This writes a log line and nothing else, so a
    missing field or an unreadable timestamp must not abort a run that has
    already committed the mount to a 24 degree arc — losing the diagnostic is a
    far smaller loss than losing the alignment it was describing."""
    try:
        ha = hour_angle_h(result.ra_hours, hub.site["longitude"], frame.timestamp)
        bus.log("info",
                f"native TPPA point {index + 1}/3: RA {result.ra_hours:.4f}h "
                f"Dec {result.dec_deg:+.3f}° HA {ha:+.3f}h "
                f"PA {(result.rotation_deg or 0.0):.1f}° "
                f"({90.0 - abs(result.dec_deg):.1f}° from the pole)", "polar")
    except Exception:  # noqa: BLE001 — see above; a log line is never worth a run
        pass


def _reject_implausible_fit(err: dict, hub: Any) -> None:
    """Refuse to report an axis fit that cannot describe a mount on a tripod.

    Raises a user-presentable :class:`DeviceError`, which ``run_native`` turns
    into a terminal ``polar{state:"error"}``. Two independent tests, both purely
    physical — neither one needs a tuned threshold to be obviously true:

    * the fitted RA axis is BELOW the horizon (the mount is standing on the
      ground, so its polar axis points up at roughly the site latitude), and
    * the total error exceeds :data:`MAX_PLAUSIBLE_ERROR_DEG`, which is far
      beyond the travel of any altitude/azimuth adjuster.

    See :data:`MAX_PLAUSIBLE_ERROR_DEG` for why "report the big number and let
    the operator judge" is not an option here: the number is not large-but-real,
    it is the output of a fit that silently lost conditioning.
    """
    lat = float(hub.site["latitude"])
    alt_err_deg = err["alt_arcmin"] / 60.0
    total_deg = err["total_arcmin"] / 60.0
    # error_det.calculate_mount_axis_error: northern alt_err = axis_alt - pole;
    # southern alt_err = pole - axis_alt. Invert to recover the fitted axis.
    axis_alt = abs(lat) + alt_err_deg if lat > 0.0 else abs(lat) - alt_err_deg
    if axis_alt > 0.0 and total_deg <= MAX_PLAUSIBLE_ERROR_DEG:
        return
    detail = (f"the fitted axis sits {abs(axis_alt):.1f}° BELOW the horizon"
              if axis_alt <= 0.0
              else f"the fit puts the axis {total_deg:.1f}° from the pole")
    raise DeviceError(
        f"the three measurements do not describe a rotating mount: {detail}, "
        "which no tripod can do — so this is a failed fit, not a large polar "
        "error, and the number it produced is not something to turn a bolt by. "
        "The usual cause is that the three frames were not a pure rotation in "
        "RA: the mount flipped sides, never finished a slew, or one frame "
        "plate-solved to the wrong place. Check the three 'native TPPA point' "
        "lines in the log — their declinations should be within a degree or so "
        "of each other and their hour angles should all share one sign — then "
        "point at least 20° from the pole, well clear of the meridian, and "
        "start again.")


def _log_pa_spread(err: dict) -> None:
    """Name the position-angle spread in the log when it is large enough to make
    the fit untrustworthy. The UI renders the same caveat from ``flags`` +
    ``position_angle_spread_deg``; this is the channel a headless/REST operator
    has. Warning only — the run continues either way."""
    spread = err.get("position_angle_spread_deg")
    if spread is None or spread <= _PA_SPREAD_WARN_DEG:
        return
    bus.log("warning",
            f"native TPPA: the camera/pier angle moved {spread:.1f}° across the "
            "three measurement frames, so they were not a pure RA rotation and "
            "this fit is not trustworthy — re-run without a meridian crossing "
            "before turning a bolt", "polar")


def _publish_error(session: Any, err: dict, *, phase: str, point_index: int,
                   progress: float, message: str, state: str = "running") -> None:
    """Publish a native TPPA error on the canonical ``polar`` schema (errors are
    already arcminutes) plus the additive native fields.

    Not all of these reach the wizard: ``phase``, ``point_index`` and the two
    ``*_direction`` hints drive it, while ``flags`` +
    ``position_angle_spread_deg`` feed only the "this fit is not trustworthy"
    caveat banner. The spread is shipped as the NUMBER as well as the boolean
    flag, because "the geometry was off" is not actionable and "the angle moved
    9.4°" is."""
    session._publish(
        state=state, source="native", phase=phase, point_index=point_index,
        progress=progress, message=message,
        az_error=round(err["az_arcmin"], 2),
        alt_error=round(err["alt_arcmin"], 2),
        az_direction=err.get("az_direction"),
        alt_direction=err.get("alt_direction"),
        flags=err.get("flags", []),
        position_angle_spread_deg=err.get("position_angle_spread_deg"),
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
