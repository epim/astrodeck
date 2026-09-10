"""Simulator backend.

A coherent virtual rig: the sim camera renders a synthetic star field based on
where the sim mount points, with star sharpness driven by the sim focuser's
distance from true focus. This makes every higher-level routine (autofocus,
plate solving, goto-centering, sequencing, guiding) genuinely exercisable
with zero hardware.
"""
from __future__ import annotations

import asyncio
import math
import os
import time

import numpy as np

from .base import (
    Camera,
    CameraFrame,
    CoverCalibrator,
    CoverState,
    DeviceError,
    Dome,
    DomeShutterState,
    FilterWheel,
    Focuser,
    PierSide,
    Rotator,
    SafetyMonitor,
    SafetyReading,
    Switch,
    SwitchPort,
    Telescope,
    TRACKING_RATES,
)


def _sim_delay(seconds: float) -> float:
    """Real-time pacing wait for the simulator, in seconds.

    Under the test fast-path (env ``ASTRODECK_FAST_TEST=1``, read LIVE on every
    call so a conftest fixture / ``monkeypatch`` takes effect without a reimport)
    this collapses to ``0.0`` so the sim's hard-coded connect-latency, exposure
    dwell, and guide-pulse dwell sleeps disappear under the test suite. It is a
    PACING knob ONLY: every simulated VALUE (RA/Dec offsets, rendered star/frame
    pixels, calibration geometry, guiding corrections) is derived from the
    logical/virtual clock + the REQUESTED ``exposure_s`` / commanded pulse ``ms``,
    never from elapsed wall-clock dwell, so zeroing the wait leaves results
    bit-identical. Production (flag unset) returns ``seconds`` unchanged, so the
    sim paces exactly as it does today."""
    return 0.0 if os.environ.get("ASTRODECK_FAST_TEST") == "1" else seconds


# --------------------------------------------------------------------------
# Polar-misalignment forward model (native-TPPA test support)
#
# A misaligned German-equatorial mount rotates its optics about an RA axis that
# is TILTED from the true celestial pole. As the mount turns in RA, a tracked
# field traces a small circle about that tilted axis on the celestial sphere;
# three plate solves at different RA hand ``astrodeck_native.tppa_from_three``
# exactly the geometry it inverts to recover the tilt. We forward-model that here
# so the native polar-alignment provider is exercisable end to end with no
# hardware. The transforms below are the refraction-free geometric equatorial<->
# horizontal pair the Rust crate uses internally (astro-tppa/src/sky.rs), so a
# solve generated here round-trips through the engine to the injected error to
# ~arcsecond accuracy. The sim has no atmosphere, so no refraction is modelled
# (the provider passes ``pressure_hpa=0`` for sim rigs to match).
# --------------------------------------------------------------------------


def _gmst_deg(jd: float) -> float:
    """Greenwich Mean Sidereal Time, degrees (IAU 1982), matching the Rust
    crate's ``gmst_deg`` so forward model and engine agree on sidereal time."""
    d = jd - 2451545.0
    t = d / 36525.0
    g = (280.46061837 + 360.98564736629 * d + 0.000387933 * t * t
         - (t * t * t) / 38710000.0)
    return g % 360.0


def _jd_from_unix(ts: float) -> float:
    """UTC Julian Date from a Unix timestamp (matches the PyO3 bridge)."""
    return ts / 86400.0 + 2440587.5


def _neu_from_altaz(alt_deg: float, az_deg: float) -> tuple[float, float, float]:
    """Topocentric [North, East, Up] unit vector for an (alt, az) direction
    (azimuth North=0, East=90)."""
    alt = math.radians(alt_deg)
    az = math.radians(az_deg)
    return (math.cos(alt) * math.cos(az),
            math.cos(alt) * math.sin(az),
            math.sin(alt))


def _altaz_from_neu(v: tuple[float, float, float]) -> tuple[float, float]:
    n, e, u = v
    alt = math.degrees(math.asin(max(-1.0, min(1.0, u))))
    az = math.degrees(math.atan2(e, n)) % 360.0
    return alt, az


def _horiz_to_equ(alt_deg: float, az_deg: float, lat_deg: float, lon_deg: float,
                  jd: float) -> tuple[float, float]:
    """Geometric (vacuum) horizontal -> equatorial (RA deg, Dec deg), the exact
    inverse of the Rust crate's ``horizontal_to_equatorial_geometric``."""
    alt = math.radians(alt_deg)
    az = math.radians(az_deg)
    phi = math.radians(lat_deg)
    north = math.cos(alt) * math.cos(az)
    east = math.cos(alt) * math.sin(az)
    up = math.sin(alt)
    x_e = -math.sin(phi) * north + math.cos(phi) * up
    y_e = -east
    z_e = math.cos(phi) * north + math.sin(phi) * up
    dec = math.degrees(math.asin(max(-1.0, min(1.0, z_e))))
    ha = math.degrees(math.atan2(y_e, x_e))
    lst = (_gmst_deg(jd) + lon_deg) % 360.0
    ra = (lst - ha) % 360.0
    return ra, dec


def _cross(a: tuple, b: tuple) -> tuple:
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _normalize(a: tuple) -> tuple:
    n = math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])
    if n == 0.0:
        return (0.0, 0.0, 0.0)
    return (a[0] / n, a[1] / n, a[2] / n)


def _rodrigues(v: tuple, k: tuple, theta: float) -> tuple:
    """Rotate ``v`` about unit axis ``k`` by ``theta`` radians (matches the Rust
    crate's ``rotate_rodrigues``)."""
    c = math.cos(theta)
    s = math.sin(theta)
    kv = _cross(k, v)
    kd = k[0] * v[0] + k[1] * v[1] + k[2] * v[2]
    return (v[0] * c + kv[0] * s + k[0] * kd * (1.0 - c),
            v[1] * c + kv[1] * s + k[1] * kd * (1.0 - c),
            v[2] * c + kv[2] * s + k[2] * kd * (1.0 - c))


class PolarMisalignment:
    """A tilted mount RA axis and the small circle its optics trace.

    ``az_arcmin``/``alt_arcmin`` are the injected polar error the engine should
    recover (the tilt of the RA axis from the true pole); ``rho_deg`` is the
    angular radius of the traced small circle (the mount's declination-from-axis,
    arbitrary but well-conditioned at 40°). ``expected_total_arcmin`` lets a
    test assert recovery against the exact injected magnitude.

    ``phase_step_deg`` is FAULT INJECTION, and ``None`` (the default) turns it
    off: the circle then advances by exactly the RA rotation the provider
    commanded, which is what a mount that honours its gotos does. Give it a
    number and every slew advances the circle by that many degrees whatever it
    was asked for -- a mount that over- or under-rotates on every leg. That used
    to be the only behaviour, at 15° against a driver commanding 12°, and
    nothing noticed because nothing graded the rotation until
    ``polar/native.py``'s MAX_ROTATION_DISAGREEMENT_DEG.

    ``goto_pointing_error_arcmin`` IS THE OTHER FAULT INJECTION, and it is the
    one that ruined three real runs on 2026-09-09. Zero (the default) is a
    mount whose gotos land exactly where they were sent, which no mount is. Give
    it a number and every GOTO lands that far from its target, in a smooth,
    deterministic, POSITION-DEPENDENT way — a pointing-model residual, which is
    what a real mount's 10 arcmin of goto error is. The declination half of it
    is the damaging half: it displaces the mount's declination AXIS, so the
    three measurement points stop lying on one cone and the exact-through-three-
    points fit reports the wreck as a confident number (504' on a mount 33'
    out). The right-ascension half merely slides each point ALONG the arc, which
    the driver's rotation-agreement guard measures and tolerates.

    Deliberately applied by ``SimTelescope.slew`` and NOT by
    ``SimTelescope.rotate_axis``: that difference IS the fix under test. Turning
    one mechanical axis is a rigid-body rotation, so it traces an exact cone
    however wrong the mount's model of itself is, and there is nothing for a
    pointing error to get wrong."""

    def __init__(self, *, az_arcmin: float, alt_arcmin: float, lat_deg: float,
                 lon_deg: float, rho_deg: float = 40.0,
                 phase_step_deg: float | None = None,
                 goto_pointing_error_arcmin: float = 0.0) -> None:
        self.az_arcmin = az_arcmin
        self.alt_arcmin = alt_arcmin
        self.lat_deg = lat_deg
        self.lon_deg = lon_deg
        self.rho_deg = rho_deg
        self.phase_step_deg = phase_step_deg
        self.goto_pointing_error_arcmin = goto_pointing_error_arcmin

    @property
    def expected_total_arcmin(self) -> float:
        return math.hypot(self.az_arcmin, self.alt_arcmin)

    def goto_landing_error(self, phase_deg: float) -> tuple[float, float]:
        """(phase error, declination-axis error) in DEGREES that a goto aiming
        the mount at RA-axis angle ``phase_deg`` lands with.

        A FUNCTION OF THE MOUNT'S OWN AXIS ANGLE, and repeatable in it, because
        that is what a pointing error is: a mount does not miss by a random
        amount, it misses by whatever its model of itself gets wrong THERE, and
        it misses by the same amount every time you send it back. Two different
        harmonics so the two axes' errors are not each other — a common-mode
        error would slide the whole arc along its cone and bend nothing, which
        is the one shape this must not model.

        THE SPATIAL FREQUENCY IS CHOSEN, NOT PHYSICAL, and that is worth saying
        out loud. A slowly-varying residual — the sin(HA) of a non-orthogonal
        declination axis, say — would land two points 12 degrees apart at almost
        the same error, which is harmless: a common offset is just a slightly
        different cone. What ruined the real runs was error that CHANGED across
        the arc, and the rig's own numbers say it does: declination stepped
        +9.7' then -6.8' across two equal 12 degree rotations on 2026-09-09,
        which no low-order function of hour angle produces. So these harmonics
        turn over in a few tens of degrees, which reproduces the measured
        behaviour without pretending to know its mechanism.

        Amplitudes are the full ``goto_pointing_error_arcmin`` on each axis;
        the rig's own two centring attempts that night measured 10.2' and 4.9',
        so ~10' is life-sized. Over the two 12 degree legs of a standard arc the
        declination term lands at 0.951 and 0.588 of amplitude, a bend of 1.31
        amplitudes — 13' at 10', against the 15.8-16.5' measured on the sky."""
        amp = self.goto_pointing_error_arcmin / 60.0
        if not amp:
            return (0.0, 0.0)
        return (amp * math.sin(math.radians(5.0 * phase_deg + 40.0)),
                amp * math.sin(math.radians(6.0 * phase_deg)))

    def true_radec(self, phase_deg: float, unix_t: float,
                   dec_axis_offset_deg: float = 0.0) -> tuple[float, float]:
        """True (RA deg, Dec deg) of the optics at tilted-circle ``phase_deg`` and
        time ``unix_t``. The mount axis sits at (alt = lat + alt_err, az = az_err)
        in the topocentric frame; a reference point ``rho`` away from it, rotated
        about the axis by ``phase_deg``, is the pointing — inverse-transformed to
        equatorial at ``unix_t`` so ``tppa_from_three`` reproduces it exactly.

        ``dec_axis_offset_deg`` displaces the DECLINATION AXIS: it widens or
        narrows the cone the optics trace, exactly as turning the declination
        axis does on a real mount. Zero (the default) keeps every pre-2026-09-09
        caller byte-identical. It is a separate parameter from ``rho_deg``
        because ``rho`` is the geometry the arc was set up with and this is
        motion that happened DURING the arc — the difference between a cone and
        a wreck."""
        alt_m = self.lat_deg + self.alt_arcmin / 60.0
        az_m = self.az_arcmin / 60.0
        m = _neu_from_altaz(alt_m, az_m)
        # A stable axis to tilt the mount axis away from itself by rho: the
        # horizontal direction perpendicular to both the axis and the zenith.
        tilt_axis = _normalize(_cross(m, (0.0, 0.0, 1.0)))
        ref = _rodrigues(m, tilt_axis,
                         math.radians(self.rho_deg + dec_axis_offset_deg))
        u = _rodrigues(ref, m, math.radians(phase_deg))
        alt, az = _altaz_from_neu(u)
        return _horiz_to_equ(alt, az, self.lat_deg, self.lon_deg,
                             _jd_from_unix(unix_t))


class SimRig:
    """Shared state tying the simulated devices together."""

    def __init__(self) -> None:
        self.ra_hours = 5.59          # roughly M42 for a pretty default
        self.dec_deg = -5.39
        self.tracking = True
        self.parked = False
        self.focuser_pos = 19_200
        self.best_focus = 20_000      # the autofocus routine must find this
        #: Focuser steps of defocus per pixel of added star sigma — how STEEPLY
        #: this rig's stars bloat as the drawtube leaves focus. 700 keeps every
        #: existing focus test byte-identical, and it models a gentle train:
        #: ±1400 steps only doubles the star. A real f/5 rig is far steeper (the
        #: 2026-08-17 measurement: 2.96 px at focus, 27 px at ±350). Exposed so
        #: a test can be a fast scope, which is what `focus/span.py` exists to
        #: size a sweep for.
        self.defocus_steps_per_px = 700.0
        self.filter_slot = 0
        #: Sky transmission per slot, indexed by ``filter_slot``. THE single
        #: source of truth: ``SimFilterWheel.filter_opaque`` is derived from it
        #: (0.0 == no glass), so "the wheel says this slot is a blackout" and
        #: "the camera renders nothing through it" cannot disagree.
        #:
        #: They used to. The render read a SEVEN-element list indexed
        #: ``filter_slot % 7`` while the wheel had EIGHT slots, so the blackout
        #: slot wrapped onto slot 0 and rendered at full L flux -- measured
        #: 2 stars and 7.2 sigma against L's 3 and 8.3. That made the
        #: 2026-08-13 incident (a cloud hold's probe frames taken through the
        #: blackout slot, scored "clear (12 bright stars)", releasing the hold)
        #: impossible to reproduce, and hub.py's blackout guard impossible to
        #: grade.
        self.filter_transmission = [1.0, 0.8, 0.8, 0.8, 0.12, 0.10, 0.10, 0.0]
        self.rotator_mech_deg = 0.0
        # hidden ground truth: how the camera is "clocked" vs mechanical zero.
        # 0.0 by default so every existing sim solve/TPPA test is byte-identical
        # (the polar_misalignment opt-in precedent); rotate-loop tests set it.
        self.rotator_pa_offset_deg = 0.0
        self.pointing_error_deg = 0.04  # goto lands slightly off until synced
        self.sensor_temp = -9.8
        # --- native-TPPA test hook: injected polar-axis misalignment ----------
        # OFF by default (``None``) so the mount is perfectly aligned and every
        # existing sim test is byte-for-byte unaffected — nothing below runs and
        # the default pointing behavior is untouched. A test calls
        # ``set_polar_misalignment(...)`` to tilt the mount's RA axis by a KNOWN
        # (az, alt) error; ``SimTelescope`` then reports plate-solvable RA/Dec
        # that trace the tilted small circle as the mount rotates in RA, so the
        # native TPPA engine can recover the injected error end to end.
        self.polar_misalignment: PolarMisalignment | None = None
        self._polar_phase_deg = 0.0
        #: Where the DECLINATION AXIS has been driven to, in degrees off the
        #: cone the arc started on. Stays 0.0 unless a goto's injected pointing
        #: error moves it (``PolarMisalignment.goto_landing_error``), which is
        #: the whole mechanism behind the 2026-09-09 failures; a single-axis
        #: ``rotate_axis`` never touches it.
        self._polar_dec_axis_deg = 0.0
        # --- guide-star model (P1-T9): the ground truth SimGuideCamera renders
        # and the closed loop SimTelescope.pulse_guide drives. ``_guide_base_px``
        # is filled in by SimGuideCamera.__init__ (the camera owns its sensor
        # geometry — the rig doesn't know pixel dimensions on its own);
        # everything else here is disturbance state a test tunes directly on
        # the rig, mirroring the ``rotator_pa_offset_deg``/``polar_misalignment``
        # opt-in precedent. ``guide_scale_arcsec_px`` is what lets
        # ``SimTelescope.pulse_guide`` convert its RA/Dec nudge (degrees) into
        # the SAME physical pixel delta applied to ``_guide_offset_px`` — see
        # ``GUIDE_RATE_DEG_S``'s docstring for the full unit reconciliation.
        self._guide_base_px: tuple[float, float] = (0.0, 0.0)
        self._guide_offset_px: tuple[float, float] = (0.0, 0.0)  # accumulated pulse_guide correction
        self._guide_epoch_s: float = time.time()  # drift zero-point
        self.guide_scale_arcsec_px: float = 1.0    # guide-cam plate scale, "/px
        self.guide_drift_px_s: float = 0.0         # constant Dec-axis creep (off by default)
        self.guide_pe_amplitude_px: float = 0.6    # RA worm periodic-error amplitude
        self.guide_pe_period_s: float = 383.0      # RA worm period (EQ6-R-class mount)
        self.guide_seeing_px: float = 0.3          # seeing jitter sigma, both axes
        # --- star-loss injection (P2-T2): a "cloud" hook for the native
        # guider's star-lost recovery gate. OFF by default (False) so every
        # existing SimGuideCamera render is byte-for-byte unaffected — the
        # same opt-in idiom as ``rotator_pa_offset_deg``/
        # ``polar_misalignment`` above. A test flips this True to blank the
        # guide star from every subsequent frame (background noise still
        # renders — a real cloud attenuates the star, not the sky) and False
        # again to let it "return"; ``SimGuideCamera._render`` is the sole
        # reader.
        self.guide_star_hidden: bool = False
        # --- PRO-5 flat panel: uniform ADU/sec the sim CoverCalibrator adds when
        # on. OFF (0.0) by default so every existing SimCamera render is
        # byte-for-byte unchanged (the guide_star_hidden / rotator_pa_offset_deg
        # opt-in idiom). ``SimCoverCalibrator.calibrator_on`` sets it; the camera
        # render (below) reads it.
        self.flat_illumination = 0.0

    def guide_star_px(self, now_s: float) -> tuple[float, float]:
        """Ground-truth guide-star pixel position at instant ``now_s`` —
        what ``SimGuideCamera.expose`` renders: the base sensor center, plus
        the accumulated ``pulse_guide`` correction (mount motion — the
        P1-T9 closed loop), plus a linear Dec-axis drift, plus an RA-axis
        periodic-error sinusoid at the configured worm period, plus seeing
        jitter on both axes.

        The jitter draw is deterministically seeded off ``now_s`` (the same
        state-hashed-seed convention ``SimCamera._render`` uses for its own
        determinism), so two calls at the SAME instant with the SAME rig
        state render the SAME frame — this model is intentionally
        time-varying (that's the point: a static guide star can't exercise a
        guiding algorithm), but it is still fully reproducible for a given
        ``now_s``.
        """
        base_x, base_y = self._guide_base_px
        off_x, off_y = self._guide_offset_px
        elapsed = now_s - self._guide_epoch_s
        drift_y = self.guide_drift_px_s * elapsed
        pe_x = 0.0
        if self.guide_pe_period_s > 0:
            pe_x = self.guide_pe_amplitude_px * math.sin(
                2 * math.pi * now_s / self.guide_pe_period_s)
        seed = abs(hash(round(now_s, 3))) % (2**32)
        rng = np.random.default_rng(seed)
        jitter_x, jitter_y = rng.normal(0.0, max(0.0, self.guide_seeing_px), size=2)
        x = base_x + off_x + pe_x + jitter_x
        y = base_y + off_y + drift_y + jitter_y
        return float(x), float(y)

    # ------------------------------------------------------ polar misalignment

    def set_polar_misalignment(self, az_arcmin: float, alt_arcmin: float, *,
                               lat_deg: float, lon_deg: float,
                               rho_deg: float = 40.0,
                               phase_step_deg: float | None = None,
                               goto_pointing_error_arcmin: float = 0.0) -> None:
        """Inject a deterministic polar-axis misalignment for native-TPPA tests.

        The mount's RA axis is tilted from the true celestial pole by
        ``(az_arcmin, alt_arcmin)`` (azimuth: North=0, East=90 — the TPPA
        convention). From this instant the sim reports the TRUE (plate-solvable)
        RA/Dec its optics point at; three captures spaced by a ~RA rotation trace
        the small circle about the tilted axis, which ``tppa_from_three`` inverts
        back to the injected error. Immediately writes the phase-0 pointing so the
        very first capture is already on the tilted circle."""
        self.polar_misalignment = PolarMisalignment(
            az_arcmin=az_arcmin, alt_arcmin=alt_arcmin, lat_deg=lat_deg,
            lon_deg=lon_deg, rho_deg=rho_deg, phase_step_deg=phase_step_deg,
            goto_pointing_error_arcmin=goto_pointing_error_arcmin)
        self._polar_phase_deg = 0.0
        self._polar_dec_axis_deg = 0.0
        self._apply_polar_pointing()

    def clear_polar_misalignment(self) -> None:
        """Remove the injected misalignment (restore a perfectly-aligned mount)."""
        self.polar_misalignment = None
        self._polar_phase_deg = 0.0
        self._polar_dec_axis_deg = 0.0

    def _apply_polar_pointing(self) -> None:
        """Recompute the true RA/Dec for the current tilted-circle phase at the
        current instant and publish it as the mount's reported pointing (what a
        plate solve reads)."""
        m = self.polar_misalignment
        if m is None:
            return
        ra_deg, dec_deg = m.true_radec(self._polar_phase_deg, time.time(),
                                       self._polar_dec_axis_deg)
        self.ra_hours = (ra_deg / 15.0) % 24.0
        self.dec_deg = dec_deg


class SimCamera(Camera):
    """Renders stars from a deterministic per-sky-tile catalog."""

    FOV_DEG = 1.4  # diagonal field of view at bin 1
    AMBIENT_C = 12.3  # uncooled sensor temperature; also the cooler-power baseline
    #: how fast the simulated sensor follows the TEC (°C per REAL second), and
    #: how fast it drifts back up when nothing is holding it. The upward figure
    #: is the one measured on the owner's camera the night the warm bug was
    #: reported: 8.3 → 11.8 °C in about 40 s ≈ 5 °C/min ≈ 0.083 °C/s.
    SIM_TEC_SLEW_C_PER_S = 0.5
    SIM_PASSIVE_SLEW_C_PER_S = 0.083
    SIM_EGAIN = 0.8  # e-/ADU: a plausible constant so EGAIN is exercised end-to-end

    #: the sim is the one backend that reports a real cooler-power number so the
    #: Monitor ThermometerBar is exercised out of the box (monitor spec §6.2).
    can_report_cooler_power: bool = True

    #: the sim renders into a full 16-bit container and clips at 65535, so that is
    #: its true saturation ADU. Carried on each frame so the clip/saturation
    #: overlay is exercised on the dev-default backend (live-preview finding #1/#3).
    full_well: int | None = 65535

    def __init__(self, rig: SimRig, name: str = "Sim Camera 533MM"):
        super().__init__(name)
        self.rig = rig
        self.sensor_width = 1216
        self.sensor_height = 912
        self.pixel_size_um = 3.76
        self.can_cool = True
        self.has_dew_heater = True
        self.bayer_pattern = None
        self._dew_power = 0
        self._cooler_on = False
        self._target_c = -10.0
        # --- thermal model (warm-ramp fix, 2026-08-04) -------------------------
        # ``None`` = nobody has touched the cooler yet, so reads report the same
        # constants as before. Once the cooler is commanded, the sensor MOVES,
        # which is the only way the warm ramp is observable on a sim rig — and a
        # ramp you cannot watch is a ramp nobody will notice regressing.
        self._temp_c: float | None = None
        self._temp_ts: float = time.monotonic()
        self._abort = asyncio.Event()
        self.can_report_cooler_power = True
        self.full_well = 65535

    async def connect(self) -> None:
        await asyncio.sleep(_sim_delay(0.1))
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def abort_exposure(self) -> None:
        self._abort.set()

    async def set_cooler(self, on: bool, target_c: float | None = None) -> None:
        # Settle the model to "now" BEFORE the setpoint changes, so the elapsed
        # time since the last read is credited to the OLD goal. Without it, a
        # warm ramp's 15 s steps would each be integrated against the newest
        # setpoint and the sensor would appear to lead the ramp it is following.
        self._advance_temp()
        self._cooler_on = on
        if target_c is not None:
            self._target_c = target_c

    async def get_temperature(self) -> float | None:
        return self._advance_temp()

    def _advance_temp(self) -> float:
        """Move the simulated sensor toward whatever the cooler is asking for.

        Two rates, because the two directions are different physics and the
        difference is the whole point of this feature:

          * DOWN (or held) is the TEC doing work — fast.
          * UP is heat leaking back in. The rig measurement that started all
            this (8.3 → 11.8 °C in ~40 s with the TEC off) is ~5 °C/min, so
            that is the ceiling on the upward rate here. It means the sim
            reproduces the BUG faithfully too: turn the ramp off in config and
            the sim sensor jumps for ambient at the same rate the real one did.

        Under the test fast-path the model is inert and the historical constants
        are returned unchanged — ``_sim_delay``'s rule (no simulated VALUE may
        depend on elapsed wall-clock) still holds for the suite, which is what
        keeps every existing cooler/telemetry test byte-identical. The live sim
        is the one place the clock is real, and the one place a human is
        watching the number move."""
        now = time.monotonic()
        dt = max(0.0, now - self._temp_ts)
        self._temp_ts = now
        if _sim_delay(1.0) == 0.0:      # fast-test: frozen, pre-model behaviour
            return self.rig.sensor_temp if self._cooler_on else self.AMBIENT_C
        if self._temp_c is None:
            self._temp_c = self.rig.sensor_temp if self._cooler_on else self.AMBIENT_C
        # The TEC can pull below ambient but has no heater: it can never drive
        # the sensor ABOVE the room. A setpoint above ambient therefore parks the
        # sensor AT ambient — which is exactly the condition the hub ramp watches
        # for to decide it has arrived and may switch off.
        goal = min(self._target_c, self.AMBIENT_C) if self._cooler_on else self.AMBIENT_C
        rate = self.SIM_TEC_SLEW_C_PER_S if goal < self._temp_c             else self.SIM_PASSIVE_SLEW_C_PER_S
        step = rate * dt
        if abs(goal - self._temp_c) <= step:
            self._temp_c = goal
        else:
            self._temp_c += step if goal > self._temp_c else -step
        # Keep the rig's shared ground truth in step: SimGuideCamera reports it
        # too, and two sim cameras in one box disagreeing about the temperature
        # would be a bug report of its own.
        self.rig.sensor_temp = self._temp_c
        return self._temp_c

    async def get_cooler(self) -> dict | None:
        """A trivial power model so the Monitor cooler readout is non-trivial:
        power tracks how hard the cooler is working to hold the delta from
        ambient (0% when off / at ambient, ~100% pulling the full delta)."""
        temp = await self.get_temperature()
        if not self._cooler_on or temp is None:
            power = 0.0
        else:
            span = self.AMBIENT_C - self._target_c
            power = 0.0 if span <= 0 else (self.AMBIENT_C - temp) / span * 100.0
        return {"on": self._cooler_on, "power": round(max(0.0, min(100.0, power)), 1),
                "target_c": self._target_c, "can_report_power": True}

    async def set_dew_heater(self, power: int) -> None:
        self._dew_power = max(0, min(100, int(power)))

    async def expose(self, seconds: float, gain: int, offset: int, binning: int = 1,
                     light: bool = True, save: bool = False,
                     target: str = "") -> CameraFrame:
        self._abort.clear()
        # Wait out the exposure in small slices so aborts are responsive. Only
        # the wall-clock DWELL is faked out under tests (_sim_delay); the frame
        # itself is rendered from the REQUESTED ``seconds`` below, so a fast-path
        # exposure is byte-identical to a real-dwell one.
        deadline = time.monotonic() + _sim_delay(seconds)
        while time.monotonic() < deadline:
            if self._abort.is_set():
                raise asyncio.CancelledError("exposure aborted")
            await asyncio.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        data = self._render(seconds, gain, offset, binning, light)
        return CameraFrame(
            data=data,
            exposure_s=seconds,
            gain=gain,
            offset=offset,
            binning=binning,
            bayer_pattern=None,
            temperature_c=await self.get_temperature(),
            timestamp=time.time(),
            # raw linear render that clips at 65535 → carry full_well so the
            # clip/saturation overlay is honest on the dev-default backend.
            full_well=self.full_well, data_is_linear=True,
            egain_e_per_adu=self.SIM_EGAIN,
            # The sim renders sharpness from this, so it is the one backend that
            # can attribute a frame to a focuser position. See CameraFrame.
            focuser_position=int(self.rig.focuser_pos),
        )

    # ---------------------------------------------------------------- render

    def _stars_for_tile(self, tx: int, ty: int) -> np.ndarray:
        """Deterministic stars for one 0.5°x0.5° sky tile: (ra_deg, dec_deg, mag)."""
        rng = np.random.default_rng(abs(hash((tx, ty))) % (2**32))
        n = rng.integers(12, 30)
        ras = (tx + rng.random(n)) * 0.5
        decs = (ty + rng.random(n)) * 0.5 - 90.0
        mags = 5.0 + 6.0 * rng.power(2.2, n)  # many dim, few bright
        return np.column_stack([ras, decs, mags])

    def _render(self, seconds: float, gain: int, offset: int, binning: int,
                light: bool) -> np.ndarray:
        h, w = self.sensor_height // binning, self.sensor_width // binning
        # DETERMINISTIC render: seed the per-exposure RNG from the rig + exposure
        # state. Two exposures with the same focuser position / pointing / params
        # produce byte-identical frames, so the autofocus V-curve is repeatable
        # and its parabola fit doesn't ride per-frame seeing noise (the source of
        # the historically flaky test_autofocus_converges). The frame still varies
        # realistically with every input (focus, pointing, gain, offset, filter).
        seed = abs(hash((
            int(self.rig.focuser_pos), round(self.rig.ra_hours, 6),
            round(self.rig.dec_deg, 6), self.rig.filter_slot, int(self.rig.parked),
            round(float(seconds), 4), int(gain), int(offset), int(binning),
            bool(light)))) % (2**32)
        rng = np.random.default_rng(seed)
        bias = 100.0 + offset * 2.0
        read_noise = 3.0 + gain / 80.0
        img = rng.normal(bias, read_noise, (h, w))

        if light and not self.rig.parked:
            img += self._render_stars(h, w, seconds, gain, binning, rng)
            # gentle sky background gradient
            yy = np.linspace(0, 1, h)[:, None]
            img += (8.0 + 14.0 * yy) * seconds * (1 + gain / 200.0)
            # PRO-5 flat panel: a uniform, exposure-linear term so the sim panel
            # is OBSERVABLE (turning it on brightens frames, letting the ADU
            # solver converge end to end). Gated on the opt-in knob so a rig that
            # never touches the panel renders byte-identically to before.
            if self.rig.flat_illumination > 0:
                img += self.rig.flat_illumination * seconds

        return np.clip(img, 0, 65535).astype(np.uint16)

    def _render_stars(self, h: int, w: int, seconds: float, gain: int,
                      binning: int, rng: np.random.Generator) -> np.ndarray:
        field = np.zeros((h, w), dtype=np.float64)
        ra_deg = self.rig.ra_hours * 15.0
        dec_deg = self.rig.dec_deg
        fov = self.FOV_DEG
        half = fov / 2.0
        cosd = max(0.05, math.cos(math.radians(dec_deg)))

        # focus quality: sigma grows with distance from best focus.
        # The 1.8 floor is seeing: even perfect focus never beats the sky.
        defocus = abs(self.rig.focuser_pos - self.rig.best_focus)
        sigma = (1.8 + defocus / self.rig.defocus_steps_per_px) / binning
        seeing_jitter = rng.normal(0, 0.05)
        sigma = max(1.2 / binning, sigma + seeing_jitter)

        # narrowband filters cut star flux
        # No modulo. A slot outside the table is a bug worth seeing as a
        # black frame rather than silently wrapping onto luminance.
        _tx = self.rig.filter_transmission
        _slot = self.rig.filter_slot
        flux_scale = _tx[_slot] if 0 <= _slot < len(_tx) else 0.0

        tx0 = int((ra_deg - half / cosd) / 0.5) - 1
        tx1 = int((ra_deg + half / cosd) / 0.5) + 1
        ty0 = int((dec_deg + 90 - half) / 0.5) - 1
        ty1 = int((dec_deg + 90 + half) / 0.5) + 1
        scale_px = w / fov  # px per degree (x)

        for tx in range(tx0, tx1 + 1):
            for ty in range(ty0, ty1 + 1):
                for sra, sdec, mag in self._stars_for_tile(tx, ty):
                    dx = (sra - ra_deg) * cosd
                    dy = sdec - dec_deg
                    px = w / 2 + dx * scale_px
                    py = h / 2 - dy * scale_px
                    if not (-20 < px < w + 20 and -20 < py < h + 20):
                        continue
                    flux = 10 ** (-0.4 * (mag - 16.0)) * seconds * (1 + gain / 100.0)
                    flux *= flux_scale * binning * binning
                    self._add_star(field, px, py, flux, sigma)

        field += rng.poisson(np.clip(field, 0, None)) - field  # shot noise
        return field

    @staticmethod
    def _add_star(field: np.ndarray, px: float, py: float, flux: float,
                  sigma: float) -> None:
        h, w = field.shape
        r = int(max(4, sigma * 4))
        x0, x1 = max(0, int(px) - r), min(w, int(px) + r + 1)
        y0, y1 = max(0, int(py) - r), min(h, int(py) + r + 1)
        if x0 >= x1 or y0 >= y1:
            return
        xs = np.arange(x0, x1) - px
        ys = (np.arange(y0, y1) - py)[:, None]
        psf = np.exp(-(xs**2 + ys**2) / (2 * sigma**2))
        field[y0:y1, x0:x1] += flux * psf / (2 * math.pi * sigma**2)


class SimGuideCamera(Camera):
    """Renders a single synthetic guide star at ``rig.guide_star_px`` — the
    P1-T9 closed loop: ``SimTelescope.pulse_guide`` moves ``rig._guide_offset_px``
    and the very next ``expose`` renders the star at the new position, with
    drift/periodic-error/seeing layered on top so the target isn't static.

    Deliberately NOT a sky-catalog render like ``SimCamera`` — a guide camera
    at guide cadence only needs ONE bright, unambiguous star to lock onto, so
    this renders exactly that (reusing ``SimCamera._add_star`` for the actual
    Gaussian PSF) rather than projecting ``rig.ra_hours``/``rig.dec_deg``
    through a sky-tile lookup.
    """

    #: modest guide-cam sensor (ZWO ASI220MM-class): small enough to expose
    #: fast at guide cadence, big enough that a multi-pixel pulse response is
    #: never clipped against the frame edge.
    SENSOR_WIDTH = 640
    SENSOR_HEIGHT = 480

    #: the one dedicated guide star: always present, always the brightest
    #: thing in frame.
    STAR_FLUX_PER_S = 15_000.0
    STAR_SIGMA_PX = 2.5

    #: the guide camera renders a flat noisy background + one star, so it
    #: reports the same honest linear full-well as SimCamera.
    can_report_cooler_power: bool = False
    full_well: int | None = 65535

    def __init__(self, rig: SimRig, name: str = "Sim Guide Cam 220MM"):
        super().__init__(name)
        self.rig = rig
        self.sensor_width = self.SENSOR_WIDTH
        self.sensor_height = self.SENSOR_HEIGHT
        self.pixel_size_um = 4.0
        self.can_cool = False
        self.has_dew_heater = False
        self.bayer_pattern = None
        self._abort = asyncio.Event()
        self.full_well = 65535
        # A1 (final-branch-review I2) fault-injection knob: the next N expose
        # calls raise, driving the native guider's retry envelope and
        # honest-death budget in tests. 0 = no injected faults.
        self.guide_expose_fail_next_n = 0
        # ...and WHAT they raise (#28). This was hardcoded to DeviceError, which
        # is exactly the one type the guider's retry envelope already handled —
        # so the retry suite could not fail no matter how narrow that envelope
        # was. A real adapter leaks httpx timeouts, OSError and struct.error;
        # point this at any of them to inject one.
        self.guide_expose_fail_exc: type[BaseException] = DeviceError
        # anchor the guide star's base center now, at construction — there is
        # only ever one SimGuideCamera per rig, so this is the rig's single
        # source of truth for where "zero guide error" points.
        rig._guide_base_px = (self.SENSOR_WIDTH / 2.0, self.SENSOR_HEIGHT / 2.0)

    async def connect(self) -> None:
        await asyncio.sleep(_sim_delay(0.05))
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def abort_exposure(self) -> None:
        self._abort.set()

    async def get_temperature(self) -> float | None:
        return self.rig.sensor_temp

    async def expose(self, seconds: float, gain: int, offset: int, binning: int = 1,
                     light: bool = True, save: bool = False,
                     target: str = "") -> CameraFrame:
        if self.guide_expose_fail_next_n > 0:
            self.guide_expose_fail_next_n -= 1
            raise self.guide_expose_fail_exc(
                "sim guide camera: injected exposure fault")
        self._abort.clear()
        # Fake out only the wall-clock dwell under tests (_sim_delay); the star
        # is rendered from ``time.time()`` + the requested ``seconds`` in
        # _render, so a zero-dwell frame is byte-identical to a real-dwell one.
        deadline = time.monotonic() + _sim_delay(seconds)
        while time.monotonic() < deadline:
            if self._abort.is_set():
                raise asyncio.CancelledError("exposure aborted")
            await asyncio.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        data = self._render(seconds, gain, offset, binning, light)
        return CameraFrame(
            data=data,
            exposure_s=seconds,
            gain=gain,
            offset=offset,
            binning=binning,
            bayer_pattern=None,
            temperature_c=await self.get_temperature(),
            timestamp=time.time(),
            full_well=self.full_well, data_is_linear=True,
        )

    def _render(self, seconds: float, gain: int, offset: int, binning: int,
                light: bool) -> np.ndarray:
        h, w = self.sensor_height // binning, self.sensor_width // binning
        # Read the clock ONCE and thread it through both the background-noise
        # seed and ``guide_star_px``'s own jitter seed, so a single instant
        # fully determines the frame (SimCamera's determinism convention,
        # adapted for a model that is deliberately time-varying).
        now = time.time()
        seed = abs(hash((round(now, 3), int(gain), int(offset), int(binning),
                          bool(light)))) % (2**32)
        rng = np.random.default_rng(seed)
        bias = 100.0 + offset * 2.0
        read_noise = 3.0 + gain / 80.0
        img = rng.normal(bias, read_noise, (h, w))

        if light and not self.rig.parked:
            # Shot noise is added to the star flux ALONE (not the bias/read-noise
            # pedestal) — same split SimCamera._render_stars uses, so a bright
            # star's Poisson noise doesn't also inflate the background.
            field = np.zeros((h, w), dtype=np.float64)
            # P2-T2 star-loss injection: a "cloud" renders background noise
            # only — no star signal — so guide_star_find legitimately reports
            # not-found until ``guide_star_hidden`` clears (rig.guide_star_px
            # itself is not consulted while hidden — the star's ground-truth
            # position is unaffected, it just isn't drawn).
            if not self.rig.guide_star_hidden:
                px, py = self.rig.guide_star_px(now)
                px, py = px / binning, py / binning
                flux = self.STAR_FLUX_PER_S * seconds * (1 + gain / 100.0) * binning * binning
                sigma = max(1.0, self.STAR_SIGMA_PX / binning)
                SimCamera._add_star(field, px, py, flux, sigma)
            field += rng.poisson(np.clip(field, 0, None)) - field
            img += field

        return np.clip(img, 0, 65535).astype(np.uint16)


#: Defensive mirror of ``hub.TOUCH_MAX_RATE_DEG_S`` (the authoritative server
#: clamp lives in the ``/api/mount/move`` endpoint). The sim previously stored
#: the raw rate unclamped; clamping here means even a direct ``move_axis`` call
#: that bypasses the endpoint can never drive the sim mount faster than the
#: touch cap. Kept as a literal (not imported) to avoid a hub↔sim import cycle.
TOUCH_MAX_RATE_DEG_S = 0.6


#: The sim's guide-pulse rate — 0.5x sidereal, in DEGREES/second, on BOTH
#: axes. This is the single source of truth ``SimTelescope.pulse_guide`` and
#: ``SimTelescope.guide_rates`` both read, so the mount's actual per-pulse
#: motion and its DECLARED rate can never drift apart again.
#:
#: P1-T9 RECONCILIATION (P0-T3 review finding, binding ledger amendment —
#: supersedes the P0-T3 task's own commentary on this formula): the value
#: below (``15.0 / 3600 * 0.5`` deg/s ≈ 0.0020833 deg/s) is what
#: ``guide_rates()`` has always declared. But the ORIGINAL ``pulse_guide``
#: added a bare ``ms / 1000.0 * 0.0002`` directly onto ``rig.ra_hours``
#: (HOURS) and ``rig.dec_deg`` (DEGREES) — the same numeric nudge treated as
#: two different physical units on the two axes:
#:   RA:  d(ra_hours)/dt = 0.0002 hours/s → ×15 deg/hour = 0.0030000 deg/s
#:   Dec: d(dec_deg)/dt  = 0.0002 deg/s   (already degrees) = 0.0002000 deg/s
#: Neither matched the declared 0.0020833 deg/s (RA ran ~1.44x too FAST, Dec
#: ~10.4x too SLOW) — the declared rate and the mount's actual motion
#: disagreed, so anything computing "how far did that pulse move the mount"
#: from ``guide_rates()`` (calibration, the P1-T10 convergence gate) would be
#: silently wrong by axis-dependent factors.
#:
#: Fixed here with the physically sensible choice the review suggested:
#: ``pulse_guide`` now applies ``GUIDE_RATE_DEG_S * (ms / 1000.0)`` DEGREES on
#: BOTH axes (RA is converted to hours only at the point it's written to
#: ``rig.ra_hours`` — dividing by 15), and ``guide_rates()`` returns this
#: exact constant. The declared rate and the mount's real motion are now the
#: same number by construction, so the sim's calibration/guiding closed loop
#: (P1-T8 calibration, P1-T9 SimGuideCamera, P1-T10 convergence gate) is
#: self-consistent end to end.
GUIDE_RATE_DEG_S = 15.0 / 3600 * 0.5


class SimTelescope(Telescope):
    SLEW_RATE_DEG_S = 4.0

    #: the sim mount is a German equatorial that reports DestinationSideOfPier,
    #: so the pre-slew pier guard + pier-limit enforcement are exercisable out of
    #: the box (Batch 4b).
    reports_destination_pier_side = True

    #: the sim mount always accepts PulseGuide (native guider needs no
    #: capability gating in the sim rig).
    can_pulse_guide = True

    #: the sim mount always accepts a lunar/solar drive rate (multi-rate mount
    #: tracking, 2026-07-21) -- no capability gating needed in the sim rig.
    can_set_tracking_rate = True

    #: the sim mount can always home (Home control, 2026-07-30), so the
    #: control is exercisable without hardware.
    can_find_home = True

    #: the sim mount can turn one axis on its own (TPPA clean arc, 2026-09-09),
    #: so the mechanism that keeps a measuring arc on one cone is exercisable
    #: without hardware — including against an injected goto pointing error,
    #: which is the fault it exists to survive. See ``rotate_axis`` below.
    can_rotate_axis = True

    #: GN-09: True here NOT because the sim mount trails -- it doesn't, it has
    #: no periodic error to trail with -- but so the flow doctor's
    #: needs-guiding rule (see ``ZwoAm5Telescope.needs_guiding``) is
    #: exercisable on the sim rig, where every other doctor rule is
    #: exercisable.
    needs_guiding = True

    def __init__(self, rig: SimRig, name: str = "Sim Mount EQ6-R"):
        super().__init__(name)
        self.rig = rig
        self._slewing = False
        self._move_rates = {"ra": 0.0, "dec": 0.0}
        self._move_task: asyncio.Task | None = None
        self._tracking_rate = "sidereal"

    async def connect(self) -> None:
        await asyncio.sleep(_sim_delay(0.1))
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def get_position(self) -> tuple[float, float]:
        return self.rig.ra_hours, self.rig.dec_deg

    async def is_slewing(self) -> bool:
        return self._slewing

    async def slew(self, ra_hours: float, dec_deg: float) -> None:
        if self.rig.parked:
            raise RuntimeError("mount is parked")
        # Native-TPPA sim path: when a polar misalignment is injected, a slew "in
        # RA" physically rotates the mount's (tilted) RA axis, so advance the
        # traced small-circle phase and report the resulting true pointing — the
        # sim models the CONSEQUENCE (the optics landing on the next point of the
        # tilted circle), not the servo. Recomputed at ``now`` so the reported
        # RA/Dec is sidereal-time-consistent with the timestamp the provider
        # records.
        #
        # The DIRECTION must follow the command. It used to be hard-coded to
        # advance, on the reasoning that only the spacing mattered; that stopped
        # being true once the driver started choosing which way to step (away
        # from the meridian, so a run never pier-flips mid-measure). A sim that
        # always went one way reported a westward rotation as an eastward one,
        # which is a real mount fault — the driver's arrival check rightly
        # refuses it — so the simulator was manufacturing a failure the hardware
        # would not produce.
        #
        # AND SO MUST THE MAGNITUDE, for the same reason one step later. It used
        # to be a fixed ``phase_step_deg`` (15 deg) whatever the driver asked
        # for, which meant every sim alignment rotated 15 deg in answer to a
        # command for 12 — a mount overshooting its goto by 25% on every leg.
        # Nothing graded the rotation, so nothing noticed. The driver now
        # measures the turn its three frames actually made about the axis they
        # fit and refuses a run that disagrees with the command by more than a
        # degree (``polar/native.py``'s MAX_ROTATION_DISAGREEMENT_DEG), and a
        # simulator that rotates 3 deg further than it was told is exactly the
        # fault that guard exists to catch. ``phase_step_deg`` survives as the
        # spacing a caller can still pin deliberately; the default path honours
        # the command.
        if self.rig.polar_misalignment is not None:
            self._slewing = True
            try:
                await asyncio.sleep(0.05)
                # Wrapped: a step across 0h is a small move, not a 23-hour one.
                delta = ((ra_hours - self.rig.ra_hours + 12.0) % 24.0) - 12.0
                # MEASURED, not assumed: +phase runs RA DOWN. Rotating the
                # pointing about the (near-polar) mount axis by +15 deg of phase
                # moves the reported right ascension by -14.97 deg, because the
                # right-hand rotation about the north-up axis in PolarMisalignment's
                # north/east/up frame runs the opposite way to increasing RA. So
                # the phase is the NEGATIVE of the commanded RA change.
                pinned = self.rig.polar_misalignment.phase_step_deg
                if pinned is None:
                    self.rig._polar_phase_deg += -delta * 15.0
                else:
                    self.rig._polar_phase_deg += (
                        (1.0 if delta < 0.0 else -1.0) * pinned)
                # A GOTO LANDS WITH THE MOUNT'S POINTING ERROR, on both axes.
                # Off by default; see PolarMisalignment.goto_landing_error for
                # why the declination half is the one that ruins the run. The
                # declination term is ABSOLUTE, not cumulative: the mount misses
                # by whatever its model gets wrong where it was AIMED, and would
                # miss by the same amount if sent back there.
                ph_err, dec_err = (
                    self.rig.polar_misalignment.goto_landing_error(
                        self.rig._polar_phase_deg))
                self.rig._polar_phase_deg += ph_err
                self.rig._polar_dec_axis_deg = dec_err
                self.rig._apply_polar_pointing()
            finally:
                self._slewing = False
            return
        self._slewing = True
        try:
            # Land near the target with a small pointing error (until synced).
            err = self.rig.pointing_error_deg
            tgt_ra = ra_hours + err / 15.0 * 0.7
            tgt_dec = dec_deg + err * 0.7
            dist = math.hypot((tgt_ra - self.rig.ra_hours) * 15, tgt_dec - self.rig.dec_deg)
            duration = min(8.0, max(0.5, dist / self.SLEW_RATE_DEG_S))
            steps = max(2, int(duration / 0.2))
            ra0, dec0 = self.rig.ra_hours, self.rig.dec_deg
            for i in range(1, steps + 1):
                await asyncio.sleep(duration / steps)
                f = i / steps
                # F-sim: keep ra_hours wrapped into [0, 24) so the sim's RA never
                # accumulates an out-of-range value (sim-fidelity only — altaz and
                # meridian consumers are wrap-invariant; this just keeps the value
                # sane). The Target model also requires 0 <= ra_hours < 24.
                self.rig.ra_hours = (ra0 + (tgt_ra - ra0) * f) % 24.0
                self.rig.dec_deg = dec0 + (tgt_dec - dec0) * f
        finally:
            self._slewing = False

    async def sync(self, ra_hours: float, dec_deg: float) -> None:
        self.rig.ra_hours = ra_hours
        self.rig.dec_deg = dec_deg
        self.rig.pointing_error_deg = 0.003  # synced: pointing is now tight

    async def set_tracking(self, on: bool) -> None:
        self.rig.tracking = on

    async def get_tracking(self) -> bool:
        return self.rig.tracking

    async def set_tracking_rate(self, rate: str) -> None:
        if rate not in TRACKING_RATES:
            raise DeviceError(f"{self.name}: unknown tracking rate {rate!r}")
        self._tracking_rate = rate

    async def get_tracking_rate(self) -> str:
        return self._tracking_rate

    async def find_home(self) -> None:
        """Home: go to the pole, stop tracking, stay USABLE (not parked).

        The distinction is the whole point of the control — see the contract
        note on ``Telescope.find_home``. A sim that ended parked would let a
        Home-then-slew bug pass here and only fail at the scope."""
        await self.slew(0.0, 89.5)
        self.rig.tracking = False
        self.rig.parked = False

    async def park(self) -> None:
        await self.slew(0.0, 89.5)
        self.rig.parked = True
        self.rig.tracking = False

    async def unpark(self) -> None:
        self.rig.parked = False

    async def is_parked(self) -> bool:
        return self.rig.parked

    async def pier_side(self) -> PierSide:
        """The side implied by where this mount is actually pointing.

        Returned a constant ``WEST`` until 2026-08-06, which is the
        ``SimSolver``-doesn't-solve shape: a device answering a question about
        its own geometry without consulting it. Anything that reasons from the
        pier side then works perfectly in the simulator and does the opposite of
        what it should on sky, in whichever half of the meridian the constant
        happened to be wrong for. The TPPA step-direction rule reasons from
        exactly this, so the constant would have aimed its arc at the horizon.

        Standard ASCOM convention, matching what the AM5N was measured to report
        (2026-08-06, both sides): a target EAST of the meridian is observed with
        the tube on the WEST side, and vice versa."""
        return self._side_for_ra(self.rig.ra_hours)

    def _side_for_ra(self, ra_hours: float) -> PierSide:
        """ASCOM convention: a target EAST of the meridian is observed with the
        tube on the WEST side. Shared by both pier-side oracles so they cannot
        drift apart."""
        try:
            from ..catalog.coords import lst_hours
            from ..config import config_store
            lon = float(config_store.cfg().site.longitude)
            ha = ((lst_hours(lon) - ra_hours + 12.0) % 24.0) - 12.0
        except Exception:  # noqa: BLE001 - a sim must never fail a geometry query
            return PierSide.WEST
        return PierSide.EAST if ha > 0.0 else PierSide.WEST

    async def destination_pier_side(self, ra_hours: float, dec_deg: float) -> PierSide:
        """What ``pier_side`` will report once we are pointing there.

        SAME GEOMETRY AS ``pier_side``, applied to the destination RA. It used
        to be ``(ra_hours % 24) < 12 -> EAST``, a rule on RA alone that ignores
        the hour angle and therefore the sidereal time -- so the two oracles
        contradicted each other at 8 of 24 RA hours, identically at every LST,
        which is itself the proof that one of them was not consulting the sky.

        Both are read by the same pre-slew guard, so that disagreement meant
        every meridian-flip decision taken in the simulator was graded against
        a contradiction rather than against geometry -- on a rig whose real
        flip failed its first attempt on hardware and was caught only by a
        pier-side check.
        """
        return self._side_for_ra(ra_hours)

    async def guide_rates(self) -> tuple[float, float] | None:
        """Fixed 0.5x sidereal rate on both axes (``GUIDE_RATE_DEG_S``) — the
        EXACT constant ``pulse_guide`` uses to move ``rig.ra_hours``/
        ``rig.dec_deg``, so the declared rate and the mount's actual pulse
        motion are mutually consistent (P1-T9 reconciliation of a P0-T3
        review finding — see ``GUIDE_RATE_DEG_S``'s docstring for the
        unit-mismatch this fixes)."""
        return GUIDE_RATE_DEG_S, GUIDE_RATE_DEG_S

    async def pulse_guide(self, direction: str, ms: int) -> None:
        """Nudge the mount ``GUIDE_RATE_DEG_S`` degrees/second on the pulsed
        axis for ``ms`` milliseconds (RA converted to hours only when
        writing ``rig.ra_hours`` — see ``GUIDE_RATE_DEG_S``'s docstring for
        the P1-T9 unit reconciliation this closes), AND applies the SAME
        physical delta — converted through ``rig.guide_scale_arcsec_px`` —
        to ``rig._guide_offset_px``. That second update is the P1-T9 closed
        loop: it's what makes ``SimGuideCamera``'s rendered star move when
        the mount is pulsed, using the identical magnitude that just moved
        ``ra_hours``/``dec_deg`` (not a separately-tuned pixel nudge)."""
        delta_deg = GUIDE_RATE_DEG_S * (ms / 1000.0)
        delta_px = delta_deg * 3600.0 / self.rig.guide_scale_arcsec_px
        off_x, off_y = self.rig._guide_offset_px
        if direction in ("east", "west"):
            sign = 1.0 if direction == "east" else -1.0
            # F-sim: wrap RA into [0, 24) (sim-fidelity; consumers are
            # wrap-invariant — keep just the wrap).
            self.rig.ra_hours = (self.rig.ra_hours
                                 + sign * (delta_deg / 15.0)) % 24.0
            off_x += sign * delta_px
        else:
            sign = 1.0 if direction == "north" else -1.0
            self.rig.dec_deg += sign * delta_deg
            off_y += sign * delta_px
        self.rig._guide_offset_px = (off_x, off_y)
        # PACING ONLY: the star's pixel delta above is derived from the commanded
        # ``ms`` (not from how long we sleep), so faking out the dwell under tests
        # leaves ``_guide_offset_px`` — and every frame rendered from it —
        # bit-identical.
        await asyncio.sleep(_sim_delay(ms / 1000.0))

    async def rotate_axis(self, axis: str, degrees: float) -> float:
        """Turn ONE mechanical axis by ``degrees``; the other does not move.

        The simulator can honour this exactly, so it does: no rate, no duration,
        no servo — the sim has no motors to model, and inventing a timed
        approximation would only add a second thing that could be wrong. What it
        DOES model faithfully is the property the caller is buying: the
        declination axis is untouched, and none of the goto pointing error in
        ``PolarMisalignment.goto_landing_error`` applies, because nothing here
        consults a model of where the sky is.

        Under an injected misalignment the RA axis IS the tilted axis, so the
        rotation advances the traced small circle's phase — the same conversion
        ``slew`` uses (+phase runs RA DOWN; see the comment there). Without one
        the mount is perfectly polar-aligned by construction and the RA axis is
        the celestial one, so the rotation is a right-ascension change.

        Returns ``degrees`` unchanged: this implementation is exact, and the
        return value exists for the timed implementations that are not."""
        if axis not in ("ra", "dec"):
            raise DeviceError(f"{self.name}: unknown axis {axis!r}")
        if self.rig.parked:
            raise DeviceError(f"{self.name}: mount is parked")
        await asyncio.sleep(_sim_delay(0.05))
        if axis == "dec":
            if self.rig.polar_misalignment is not None:
                self.rig._polar_dec_axis_deg += degrees
                self.rig._apply_polar_pointing()
            else:
                self.rig.dec_deg = min(90.0, max(-90.0,
                                                 self.rig.dec_deg + degrees))
            return degrees
        if self.rig.polar_misalignment is not None:
            # ``phase_step_deg`` fault injection applies here too: a mount that
            # turns further than it was told is a mount fault whichever call
            # told it, and a simulator that only misbehaved on the goto path
            # would leave the rotation-agreement guard ungraded the moment the
            # driver started using the better mechanism.
            pinned = self.rig.polar_misalignment.phase_step_deg
            if pinned is None:
                self.rig._polar_phase_deg += -degrees
            else:
                self.rig._polar_phase_deg += (
                    (1.0 if degrees < 0.0 else -1.0) * pinned)
            self.rig._apply_polar_pointing()
        else:
            self.rig.ra_hours = (self.rig.ra_hours + degrees / 15.0) % 24.0
        return degrees

    async def move_axis(self, axis: str, rate_deg_s: float) -> None:
        # Defensive clamp to the touch cap (the real clamp is server-side in the
        # /api/mount/move endpoint; this guards direct/bypass callers). NOTE:
        # SimTelescope deliberately does NOT override stop() — it inherits
        # Telescope.stop() which zeroes both axes, so the deadman/STOP path
        # clears _move_rates and _move_loop exits.
        rate_deg_s = max(-TOUCH_MAX_RATE_DEG_S,
                         min(TOUCH_MAX_RATE_DEG_S, rate_deg_s))
        self._move_rates[axis] = rate_deg_s
        if rate_deg_s != 0 and (self._move_task is None or self._move_task.done()):
            self._move_task = asyncio.create_task(self._move_loop())

    async def _move_loop(self) -> None:
        while any(r != 0 for r in self._move_rates.values()):
            self.rig.ra_hours += self._move_rates["ra"] * 0.1 / 15.0
            self.rig.dec_deg = min(90, max(-90, self.rig.dec_deg + self._move_rates["dec"] * 0.1))
            await asyncio.sleep(0.1)


class SimFocuser(Focuser):
    MOVE_RATE = 4000  # steps/s

    def __init__(self, rig: SimRig, name: str = "Sim Focuser EAF"):
        super().__init__(name)
        self.rig = rig
        self.max_position = 60_000
        self.step_size_um = 1.2
        self._halt = asyncio.Event()
        self._moving = False

    async def connect(self) -> None:
        await asyncio.sleep(_sim_delay(0.05))
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def get_position(self) -> int:
        return self.rig.focuser_pos

    async def get_temperature(self) -> float | None:
        return 4.2

    async def halt(self) -> None:
        self._halt.set()

    async def is_moving(self) -> bool:
        return self._moving

    async def move_to(self, position: int) -> None:
        position = max(0, min(self.max_position, position))
        self._halt.clear()
        step = 200 if position > self.rig.focuser_pos else -200
        self._moving = True
        try:
            while self.rig.focuser_pos != position and not self._halt.is_set():
                remaining = position - self.rig.focuser_pos
                self.rig.focuser_pos += step if abs(remaining) >= abs(step) else remaining
                await asyncio.sleep(abs(step) / self.MOVE_RATE)
        finally:
            # finally, not a trailing assignment: a cancelled or halted move
            # must not leave the sim reporting motion forever.
            self._moving = False


class SimRotator(Rotator):
    MOVE_RATE = 5.0  # deg/s

    def __init__(self, rig: SimRig, name: str = "Sim Rotator CAA"):
        super().__init__(name)
        self.rig = rig
        self._halt = asyncio.Event()
        self._moving = False

    async def connect(self) -> None:
        await asyncio.sleep(_sim_delay(0.05))
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def get_mechanical_position(self) -> float:
        return self.rig.rotator_mech_deg % 360.0

    async def is_moving(self) -> bool:
        return self._moving

    async def halt(self) -> None:
        self._halt.set()

    async def move_mechanical(self, mech_deg: float) -> None:
        target = mech_deg % 360.0
        self._halt.clear()
        self._moving = True
        try:
            # shortest signed travel, animated in ~2° steps like SimFocuser.
            # Computed as raw delta in [0, 360) folded to (-180, 180] rather than
            # the equivalent-looking ((d + 180) % 360) - 180 form: that version
            # resolves the exact-180° tie to -180 (Python's % returns [0, 360),
            # so 360 % 360 == 0 → 0 - 180 == -180), sending the rotator the "long"
            # way round for an exact opposite target. This form ties to +180.
            raw = (target - self.rig.rotator_mech_deg) % 360.0
            delta = raw if raw <= 180.0 else raw - 360.0
            steps = max(1, int(abs(delta) / 2.0))
            step = delta / steps
            for _ in range(steps):
                if self._halt.is_set():
                    return
                self.rig.rotator_mech_deg = (self.rig.rotator_mech_deg + step) % 360.0
                await asyncio.sleep(abs(step) / self.MOVE_RATE)
            self.rig.rotator_mech_deg = target
        finally:
            self._moving = False


class SimFilterWheel(FilterWheel):
    def __init__(self, rig: SimRig, name: str = "Sim Filter Wheel 8x36"):
        super().__init__(name)
        self.rig = rig
        # Slot 7 is a BLACKOUT carrier (no glass, blocks the light path) so the
        # sim exercises the dark-slot routing every real 8-slot wheel needs.
        # Appended rather than prepended on purpose: L..SII keep their indices.
        self.filter_names = ["L", "R", "G", "B", "Ha", "OIII", "SII", "Dark"]
        self.filter_offsets = [0, 12, 10, 15, 120, 110, 115, 0]  # focuser steps
        # DERIVED, not declared: a slot passes no light exactly when its
        # transmission is zero. Declaring it separately is what let the wheel
        # and the renderer disagree about slot 7.
        self.filter_opaque = [t <= 0.0 for t in rig.filter_transmission]
        self._moving = False

    async def connect(self) -> None:
        await asyncio.sleep(_sim_delay(0.05))
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def get_position(self) -> int:
        return self.rig.filter_slot

    async def is_moving(self) -> bool:
        return self._moving

    async def set_position(self, slot: int) -> None:
        moves = abs(slot - self.rig.filter_slot)
        # The slot flips at the END of the travel, exactly like a real wheel:
        # the carousel is between filters for the whole sleep, and only the
        # `moving` flag can say so. A sim that updated the position instantly
        # would make the Capture screen's pulse untestable off hardware.
        self._moving = True
        try:
            await asyncio.sleep(0.4 * min(moves, len(self.filter_names) - moves or 1))
            self.rig.filter_slot = slot
        finally:
            # finally, not a trailing assignment — a cancelled filter change must
            # not leave the sim claiming the wheel is still turning forever.
            self._moving = False


class SimSwitch(Switch):
    """Mimics a Pegasus UPBv2-style power box."""

    def __init__(self, name: str = "Sim PowerBox UPBv2"):
        super().__init__(name)
        self._ports = [
            SwitchPort(0, "Mount 12V", True, True, 1),
            SwitchPort(1, "Camera 12V", True, True, 1),
            SwitchPort(2, "Focuser 12V", True, True, 0),
            SwitchPort(3, "Accessory 12V", True, True, 0),
            SwitchPort(4, "Dew Heater A", True, False, 35, 0, 100, "%"),
            SwitchPort(5, "Dew Heater B", True, False, 0, 0, 100, "%"),
            SwitchPort(6, "Input Voltage", False, False, 13.7, 0, 15, "V"),
            SwitchPort(7, "Total Current", False, False, 2.4, 0, 10, "A"),
        ]

    async def connect(self) -> None:
        await asyncio.sleep(_sim_delay(0.05))
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def get_ports(self) -> list[SwitchPort]:
        # readings wiggle a little
        self._ports[6].value = round(13.6 + 0.2 * math.sin(time.time() / 7), 2)
        self._ports[7].value = round(2.2 + 0.4 * abs(math.sin(time.time() / 11)), 2)
        return list(self._ports)

    async def set_port(self, port_id: int, value: float) -> None:
        port = self._ports[port_id]
        if not port.can_write:
            raise RuntimeError(f"port {port.name} is read-only")
        port.value = max(port.min, min(port.max, value))


class SimSafetyMonitor(SafetyMonitor):
    """A toggleable observing-condition sensor (Batch 4b).

    Default SAFE so the dev rig images normally; a test/engine flips it via
    ``force_unsafe(reason)`` to drive the safety state machine, and
    ``force_safe()`` clears it again. ``reading()`` (inherited) wraps ``is_safe``;
    we override it only to carry the injected ``reason``."""

    def __init__(self, name: str = "Sim Safety Monitor"):
        super().__init__(name)
        self._safe = True
        self._reason = ""

    async def connect(self) -> None:
        await asyncio.sleep(_sim_delay(0.05))
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    def force_unsafe(self, reason: str = "simulated unsafe condition") -> None:
        """Make the monitor report UNSAFE with ``reason`` until ``force_safe``."""
        self._safe = False
        self._reason = reason

    def force_safe(self) -> None:
        """Clear a forced-unsafe condition; the monitor reports SAFE again."""
        self._safe = True
        self._reason = ""

    async def is_safe(self) -> bool:
        return self._safe

    async def reading(self) -> SafetyReading:
        return SafetyReading(
            is_safe=self._safe,
            source=self.name,
            reason="" if self._safe else (self._reason or "unsafe condition reported"),
        )


class SimCoverCalibrator(CoverCalibrator):
    """A simulated flat panel + motorized cover (PRO-5 / F-F).

    ``calibrator_on`` sets ``rig.flat_illumination`` so SimCamera frames actually
    brighten — a coherent rig, like the guide-loop wiring — which lets the ADU
    solver converge against the sim end to end. The cover starts CLOSED."""

    max_brightness = 255
    has_cover = True
    ADU_PER_BRIGHTNESS = 60.0    # ADU/sec per brightness unit at gain 0

    def __init__(self, rig, name: str = "Sim Flat Panel"):
        super().__init__(name)
        self.rig = rig
        self._brightness = 0
        self._on = False
        self._cover = CoverState.CLOSED

    async def connect(self) -> None:
        await asyncio.sleep(_sim_delay(0.02))
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def get_brightness(self) -> int:
        return self._brightness

    async def get_calibrator_state(self) -> str:
        return "ready" if self._on else "off"

    async def calibrator_on(self, brightness: int) -> None:
        self._brightness = max(0, min(self.max_brightness, int(brightness)))
        self._on = True
        self.rig.flat_illumination = self.ADU_PER_BRIGHTNESS * self._brightness

    async def calibrator_off(self) -> None:
        self._on = False
        self.rig.flat_illumination = 0.0

    async def get_cover_state(self) -> CoverState:
        return self._cover

    async def open_cover(self) -> None:
        self._cover = CoverState.OPEN

    async def close_cover(self) -> None:
        self._cover = CoverState.CLOSED


class SimDome(Dome):
    """A simulated roll-off roof (PRO-4) with a SELF-CHECKING collision model.

    A roll-off roof starts OPEN (imaging). The safety-critical bit:
    ``close_shutter`` reads the SHARED ``rig.parked`` and, if the mount is NOT
    parked, sets the shutter ERROR and RAISES ``DeviceError`` — because a real
    roof closing over an unparked mount crushes the OTA. This makes the
    close-ordering guard (``sequence/roof.close_observatory``) PROVABLE end to
    end: a test (or a bug) that closed while unparked would trip the sim, and
    the guard test asserts the sim was never even touched (state stayed OPEN).

    Used correctly (park first, then close) the sim is byte-identical to any
    other cooperative device — nothing here perturbs the existing sim rig, since
    ``requires_park_before_close`` gating and the collision check only fire on a
    close attempt, which no pre-PRO-4 test issues."""

    def __init__(self, rig: SimRig, name: str = "Sim Roll-Off Roof") -> None:
        super().__init__(name)
        self.rig = rig
        self._state = DomeShutterState.OPEN     # a roll-off roof starts OPEN (imaging)
        self.requires_park_before_close = True
        self.can_bind = False
        self._halt = asyncio.Event()

    async def connect(self) -> None:
        await asyncio.sleep(_sim_delay(0.02))
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def shutter_state(self) -> DomeShutterState:
        return self._state

    async def open_shutter(self) -> None:
        self._state = DomeShutterState.OPENING
        await asyncio.sleep(0.02)
        self._state = DomeShutterState.OPEN

    async def close_shutter(self) -> None:
        # SELF-CHECKING collision model: a roll-off roof closing over an unparked
        # mount crushes the OTA. The sim REFUSES with an error so the close-
        # ordering guard (roof.close_observatory) is provable end to end.
        if not self.rig.parked:
            self._state = DomeShutterState.ERROR
            raise DeviceError("roof closed onto an unparked mount (collision)")
        self._state = DomeShutterState.CLOSING
        await asyncio.sleep(0.02)
        self._state = DomeShutterState.CLOSED

    async def abort(self) -> None:
        self._halt.set()


# TODO(flows-handoff): ``build_sim_rig`` still wires the roll-off ``SimDome``, so
# this class is reachable only by direct construction (tests, a future sim
# profile). Which dome shape the default sim rig presents is a product decision
# — the roof exercises the park-before-close guard, the dome exercises slaving —
# so it is left to whoever wires the Flows sim profile rather than changed here.
class SimRotatingDome(SimDome):
    """A simulated ROTATING dome — the sim that can actually bind.

    ``SimDome`` is a roll-off ROOF: it has no azimuth, so ``can_bind`` is False
    and DOME CONTROL's DEFAULT parameter ("Slave to mount") has nothing to run
    against on a machine with no hardware. The Flows handoff requires the whole
    surface to demo on the simulator, and "the default setting is only reachable
    with a real dome bolted to the building" is not that.

    Slaving here is the boolean the ``Dome`` role actually defines — ASCOM's
    ``Slaved`` is a flag the driver acts on, and nothing in this codebase asks a
    dome for an azimuth in degrees. No geometry is invented to fill a gap the
    role does not have.

    ``requires_park_before_close`` stays True (inherited), even though a real
    rotating dome whose shutter clears the OTA may set it False: the sim models
    no dome/OTA geometry, so the fail-safe flag is the only honest one, and the
    inherited collision model keeps protecting this class too.
    """

    def __init__(self, rig: SimRig, name: str = "Sim Rotating Dome") -> None:
        super().__init__(rig, name)
        self.can_bind = True
        self._bound = False

    async def get_bound(self) -> bool:
        return self._bound

    async def set_bound(self, on: bool) -> None:
        self._bound = bool(on)


def build_sim_rig() -> dict[str, object]:
    """One coherent simulated observatory."""
    rig = SimRig()
    return {
        "camera": SimCamera(rig),
        "guide_camera": SimGuideCamera(rig),
        "telescope": SimTelescope(rig),
        "focuser": SimFocuser(rig),
        "filterwheel": SimFilterWheel(rig),
        "switch": SimSwitch(),
        "rotator": SimRotator(rig),
        "safety": SimSafetyMonitor(),
        "covercalibrator": SimCoverCalibrator(rig),
        "dome": SimDome(rig),
        "_rig": rig,
    }
