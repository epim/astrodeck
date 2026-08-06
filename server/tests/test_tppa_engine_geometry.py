"""TPPA engine geometry and accuracy, through the Python binding.

What this file is for
=====================
``astrodeck_native.tppa_from_three`` takes three plate solves and returns the
mount's polar-axis error. Everything downstream — the "adjust the mount" number,
the knob directions, the ``_DONE_THRESHOLD_ARCMIN`` "polar aligned" verdict —
rests on that one fit being right. The Rust crate already has forward-model
tests, but they build their synthetic solves with the ENGINE'S OWN coordinate
transforms (``astro_tppa::sky::topocentric_to_equatorial``), so a sign or
grouping error shared by the forward and the inverse transform cancels and the
test still passes.

So this file carries its own forward model, written from textbook spherical
astronomy and plain vector algebra, with nothing imported from ``astro-tppa``
and nothing borrowed from ``astrodeck.devices.sim``'s ``PolarMisalignment``.
Given a site latitude, a mount RA axis placed at a known (alt, az) offset from
the pole, a pole distance ``rho`` and three rotation phases, it computes the
three RA/Dec pairs a plate solver would report, feeds them to the engine, and
checks the engine hands back the (az, alt) that were injected.

The one thing that cannot be made independent is sidereal time: both models must
agree on where the sky is. That is checked directly against astropy in
:func:`test_sidereal_time_matches_astropy` — and a *shared* sidereal-time error
would in any case only rotate both the points and the fitted axis rigidly about
the pole, which changes the reported error by at most |error| x |LST error|
(under 0.02' for a 2 deg error and a 0.01 deg LST slip).

Why refraction is switched off for the geometry tests
=====================================================
``pressure_hpa = 0`` makes ``refco`` return A = B = 0, so the engine's
equatorial->topocentric transform collapses to exact vacuum geometry and the
injected error must come back to machine precision. That is not a way of dodging
refraction: :func:`test_refraction_is_live_and_altitude_only` is the positive
control that proves the switch is doing something, and that the refracted-pole
correction moves altitude and leaves azimuth alone.

Conventions the engine defines (``astro-tppa`` error_det.rs §6.2), which this
file tests against rather than re-derives:

* azimuth North = 0, East = 90; "northern" iff ``latitude_deg > 0`` (exactly 0
  takes the SOUTHERN branch);
* northern: ``alt_err = axis_alt - pole_alt``, ``az_err = wrap(axis_az)``;
* southern: ``alt_err = pole_alt - axis_alt``, ``az_err = wrap(axis_az + 180)``;
* both errors are reported in arcminutes and are KNOB angles, not the
  great-circle separation of the axis from the pole (see
  :func:`test_total_error_is_knob_quadrature_not_sky_separation`).
"""
from __future__ import annotations

import math
import random
import statistics

import pytest

native = pytest.importorskip("astrodeck_native")

from astrodeck.polar.native import (  # noqa: E402  (after importorskip)
    _DONE_THRESHOLD_ARCMIN,
    _RA_STEP_HOURS,
    LOW_MEASUREMENT_ALT_DEG,
    MAX_PLAUSIBLE_ERROR_DEG,
    MIN_MEASUREMENT_ALT_DEG,
    MIN_POLE_DISTANCE_DEG,
)

# The RA step the driver actually commands between measurement points.
SHIPPED_STEP_DEG = _RA_STEP_HOURS * 15.0

D2R = math.pi / 180.0
R2D = 180.0 / math.pi
UNIX_EPOCH_JD = 2440587.5
SEC_PER_DAY = 86400.0

#: A mid-northern site. Invented coordinates — no real observing site appears in
#: this file.
LAT = 45.0
LON = -122.0
T0 = 1_700_000_000.0  # a plain UTC unix timestamp; nothing depends on the date

#: Exact-geometry engine options: no atmosphere, compare against the true pole.
GEOMETRIC = {"pressure_hpa": 0.0, "correct_for_refraction": True}


# ---------------------------------------------------------------------------
# An independent forward model
# ---------------------------------------------------------------------------

def jd_from_unix(ts: float) -> float:
    return ts / SEC_PER_DAY + UNIX_EPOCH_JD


def gmst_deg(jd_utc: float) -> float:
    """Greenwich Mean Sidereal Time in degrees (IAU 1982 / Meeus eq. 12.4).

    Transcribed from the published expression, not from the engine. Pinned
    against astropy in :func:`test_sidereal_time_matches_astropy`.
    """
    d = jd_utc - 2451545.0
    t = d / 36525.0
    g = (280.46061837 + 360.98564736629 * d
         + 0.000387933 * t * t - (t * t * t) / 38710000.0)
    return g % 360.0


def lst_deg(ts: float, lon_deg: float) -> float:
    return (gmst_deg(jd_from_unix(ts)) + lon_deg) % 360.0


# --- vectors ---------------------------------------------------------------
# Topocentric frame used here: x = North, y = East, z = Up. (Deliberately NOT
# the engine's (north, west, up) frame, so a handedness slip cannot cancel.)

def neu_from_altaz(alt_deg: float, az_deg: float) -> tuple[float, float, float]:
    a, A = alt_deg * D2R, az_deg * D2R
    return (math.cos(a) * math.cos(A), math.cos(a) * math.sin(A), math.sin(a))


def altaz_from_neu(v) -> tuple[float, float]:
    n, e, u = v
    return (math.asin(max(-1.0, min(1.0, u))) * R2D,
            math.degrees(math.atan2(e, n)) % 360.0)


def _norm(v):
    m = math.sqrt(sum(c * c for c in v))
    assert m > 0.0, "zero-length vector"
    return tuple(c / m for c in v)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def radec_from_neu(v, lat_deg: float, lon_deg: float, ts: float):
    """Topocentric unit vector -> (RA deg, Dec deg), vacuum geometry.

    The equatorial frame (x toward the meridian on the equator, y toward
    HA = +6h, z toward the north celestial pole) is the topocentric N/E/U frame
    tipped about the East axis by (90 - lat):

        x_eq = -sin(lat)*N + cos(lat)*U
        y_eq = -E
        z_eq =  cos(lat)*N + sin(lat)*U

    Then Dec = asin(z_eq), HA = atan2(y_eq, x_eq), RA = LST - HA. Cross-checked
    against the scalar spherical-trig formulas in
    :func:`test_forward_model_matches_textbook_spherical_trig`; done as a
    rotation rather than through alt/az angles only so that points near the
    zenith (where azimuth is ill-conditioned) stay exact.
    """
    n, e, u = v
    sp, cp = math.sin(lat_deg * D2R), math.cos(lat_deg * D2R)
    x_eq = -sp * n + cp * u
    y_eq = -e
    z_eq = cp * n + sp * u
    dec = math.asin(max(-1.0, min(1.0, z_eq))) * R2D
    ha = math.degrees(math.atan2(y_eq, x_eq))
    return (lst_deg(ts, lon_deg) - ha) % 360.0, dec


def axis_vector(lat_deg: float, d_alt_deg: float, d_az_deg: float):
    """Mount RA axis placed so the engine's §6.2 decomposition must report
    exactly ``(d_alt_deg, d_az_deg)``."""
    if lat_deg > 0.0:  # engine: northern iff latitude_deg > 0
        return neu_from_altaz(lat_deg + d_alt_deg, d_az_deg % 360.0)
    return neu_from_altaz(abs(lat_deg) - d_alt_deg, (180.0 + d_az_deg) % 360.0)


def cone_point(axis, rho_deg: float, phase_deg: float):
    """A point at angular distance ``rho`` from ``axis``, at circle ``phase``.

    Phase 0 is the point of the circle furthest from the horizon (the reference
    perpendicular is the zenith direction with its axis component removed), so
    phases near 0 keep the synthetic pointings high in the sky.
    """
    zen = (0.0, 0.0, 1.0)
    p0 = _norm(tuple(zen[i] - _dot(zen, axis) * axis[i] for i in range(3)))
    q = _cross(axis, p0)
    r, ph = rho_deg * D2R, phase_deg * D2R
    return tuple(axis[i] * math.cos(r)
                 + p0[i] * math.sin(r) * math.cos(ph)
                 + q[i] * math.sin(r) * math.sin(ph) for i in range(3))


def displace_radially(v, axis, eps_deg: float):
    """Move ``v`` by ``eps_deg`` along the great circle toward ``axis``.

    This is the only perturbation direction that changes the plane through the
    three points: it shifts the point off its small circle, out of plane by
    ``eps * sin(rho)``. Moving the point ALONG its circle leaves the plane — and
    therefore the fitted axis — untouched (see
    :func:`test_along_circle_perturbation_does_not_move_the_fit`).
    """
    t = _norm(tuple(axis[i] - _dot(axis, v) * v[i] for i in range(3)))
    e = eps_deg * D2R
    return tuple(v[i] * math.cos(e) + t[i] * math.sin(e) for i in range(3))


def make_solves(lat_deg, lon_deg, d_alt_deg, d_az_deg, rho_deg, step_deg,
                *, phase0_deg=None, perturb=None, radial_eps_deg=None,
                ts0=T0, dt_s=90.0, pa_deg=30.0):
    """Three plate solves for a mount whose RA axis is (d_alt, d_az) off the pole.

    Returns ``(solves, altitudes_deg, axis_vector)``. ``phase0_deg`` defaults to
    ``-step`` so the three phases straddle the high point of the circle;
    ``perturb=(index, eps_deg)`` applies a radial displacement to one point, and
    ``radial_eps_deg=(e0, e1, e2)`` applies one to EVERY point — which is how a
    common bias and a linear ramp are distinguished from an isolated blunder.

    The three timestamps differ, as they do on a real run: both the commanded
    slew and sidereal tracking are rotations about the SAME mount axis, so the
    three pointings still lie on one small circle about it whatever the elapsed
    time — the engine sees only the topocentric directions.
    """
    axis = axis_vector(lat_deg, d_alt_deg, d_az_deg)
    p0 = -step_deg if phase0_deg is None else phase0_deg
    solves, alts = [], []
    for i in range(3):
        v = cone_point(axis, rho_deg, p0 + i * step_deg)
        if perturb is not None and perturb[0] == i:
            v = displace_radially(v, axis, perturb[1])
        if radial_eps_deg is not None and radial_eps_deg[i]:
            v = displace_radially(v, axis, radial_eps_deg[i])
        alt, _az = altaz_from_neu(v)
        alts.append(alt)
        ts = ts0 + i * dt_s
        ra, dec = radec_from_neu(v, lat_deg, lon_deg, ts)
        solves.append({"ra_hours": ra / 15.0, "dec_deg": dec,
                       "timestamp_unix_s": ts, "position_angle_deg": pa_deg})
    return solves, alts, axis


def site_dict(lat_deg=LAT, lon_deg=LON, elev_m=100.0):
    return {"latitude_deg": lat_deg, "longitude_deg": lon_deg,
            "elevation_m": elev_m}


def fit(solves, lat_deg=LAT, lon_deg=LON, options=GEOMETRIC, elev_m=100.0):
    return native.tppa_from_three(solves, site_dict(lat_deg, lon_deg, elev_m),
                                  options)["error"]


def measure(lat_deg, d_alt_deg, d_az_deg, rho_deg, step_deg, **kw):
    """Inject an error, run the engine, return ``(error_dict, altitudes)``."""
    solves, alts, _axis = make_solves(lat_deg, LON, d_alt_deg, d_az_deg,
                                      rho_deg, step_deg, **kw)
    return fit(solves, lat_deg), alts


def _err_distance_arcmin(a, b):
    return math.hypot(a["alt_arcmin"] - b["alt_arcmin"],
                      a["az_arcmin"] - b["az_arcmin"])


# ---------------------------------------------------------------------------
# 0. The forward model itself
# ---------------------------------------------------------------------------

def test_forward_model_matches_textbook_spherical_trig():
    """`radec_from_neu` is a rotation; check it against the scalar formulas.

    sin(dec) = sin(a)sin(phi) + cos(a)cos(phi)cos(A)
    tan(HA)  = -cos(a)sin(A) / (sin(a)cos(phi) - cos(a)sin(phi)cos(A))

    If the rotation matrix in the forward model were wrong, every recovery test
    below would be testing the wrong sky.
    """
    checked = 0
    for lat in (65.0, 45.0, 0.0, -33.0, -70.0):
        for alt, az in ((10.0, 0.0), (35.0, 73.0), (60.0, 180.0),
                        (25.0, 271.0), (80.0, 300.0)):
            v = neu_from_altaz(alt, az)
            ra, dec = radec_from_neu(v, lat, LON, T0)

            a, A, p = alt * D2R, az * D2R, lat * D2R
            dec_txt = math.degrees(math.asin(
                math.sin(a) * math.sin(p) + math.cos(a) * math.cos(p) * math.cos(A)))
            ha_txt = math.degrees(math.atan2(
                -math.cos(a) * math.sin(A),
                math.sin(a) * math.cos(p) - math.cos(a) * math.sin(p) * math.cos(A)))
            ra_txt = (lst_deg(T0, LON) - ha_txt) % 360.0

            assert abs(dec - dec_txt) < 1e-9, (lat, alt, az, dec, dec_txt)
            assert abs((ra - ra_txt + 180.0) % 360.0 - 180.0) < 1e-9
            checked += 1
    assert checked == 25, f"precondition: expected 25 sample points, got {checked}"


def test_sidereal_time_matches_astropy():
    """Pin the one shared ingredient (sidereal time) to an outside authority."""
    Time = pytest.importorskip("astropy.time").Time
    u = pytest.importorskip("astropy.units")
    worst = 0.0
    # All in the past, so astropy's leap-second table is authoritative for them.
    stamps = (946684800.0, 1.1e9, 1.3e9, 1.5e9, T0)
    for ts in stamps:
        jd = jd_from_unix(ts)
        t = Time(jd, format="jd", scale="utc")
        t.delta_ut1_utc = 0.0  # the engine assumes DUT1 = 0
        ref = t.sidereal_time("mean", "greenwich").to(u.deg).value
        worst = max(worst, abs((gmst_deg(jd) - ref + 180.0) % 360.0 - 180.0))
    assert len(stamps) == 5
    # 1 arcsec of sidereal time is 4.6e-5 deg of sky rotation; even at the
    # worst conditioning in this file that is far below the 0.02' tolerance.
    assert worst * 3600.0 < 1.0, f"GMST differs from astropy by {worst*3600:.3f}\""


def test_circle_centred_on_the_celestial_pole_reports_no_error():
    """Three points on a circle centred on the CELESTIAL pole -> no error.

    This is the joint check that the forward model's pole and the engine's pole
    are the same direction at the same instant: if the two sidereal times or the
    two latitude conventions disagreed at all, this would not come out at zero.

    Southern latitudes are given one arcminute of deliberate azimuth offset
    instead of zero, purely to keep them off the exact meridian — a southern axis
    with EXACTLY zero azimuth error trips an engine quirk that has its own test
    (:func:`test_southern_exact_zero_azimuth_is_a_bit_pattern_not_a_reachable_state`)
    and would otherwise mask what this one is checking. One arcminute is still
    far below any tolerance here, so the pole cross-check is not weakened.
    """
    checked = 0
    for lat in (68.0, LAT, 12.0):
        err, alts = measure(lat, 0.0, 0.0, 35.0, SHIPPED_STEP_DEG)
        assert min(alts) > 0.0, f"lat {lat}: synthetic points below the horizon"
        assert abs(err["alt_arcmin"]) < 0.01, (lat, err)
        assert abs(err["az_arcmin"]) < 0.01, (lat, err)
        assert err["total_arcmin"] < 0.02, (lat, err)
        checked += 1
    for lat in (-12.0, -40.0, -75.0):
        err, alts = measure(lat, 0.0, 1.0 / 60.0, 35.0, SHIPPED_STEP_DEG)
        assert min(alts) > 0.0, f"lat {lat}: synthetic points below the horizon"
        assert abs(err["alt_arcmin"]) < 0.01, (lat, err)
        assert abs(err["az_arcmin"] - 1.0) < 0.01, (lat, err)
        checked += 1
    assert checked == 6


# ---------------------------------------------------------------------------
# 1. Accuracy: does the engine give back what was injected?
# ---------------------------------------------------------------------------

# Latitudes: strongly northern, mid-northern, near-equator both signs, southern,
# strongly southern. (lat exactly 0 has its own test — the engine's hemisphere
# test is `latitude_deg > 0`, so 0 is SOUTH.)
SWEEP_LATS = (72.0, LAT, 5.0, -5.0, -33.0, -68.0)

# Injected (alt, az) error in degrees: pure azimuth, pure altitude, both
# diagonals, both signs, from 1 arcmin to 2 degrees.
SWEEP_INJECTIONS = (
    (1.0 / 60.0, 0.0), (0.0, 1.0 / 60.0), (-1.0 / 60.0, 1.0 / 60.0),
    (0.25, 0.0), (0.0, -0.25), (0.5, 0.5), (-0.75, 0.5), (2.0, -2.0),
    (-2.0, -2.0), (0.0, 2.0), (-2.0, 0.0),
)

# Pole distance: the 20 deg guard, then out to a wide arc.
SWEEP_RHOS = (MIN_POLE_DISTANCE_DEG, 30.0, 40.0, 60.0, 70.0)

# RA step: the shipped 12 deg, plus a smaller and a larger one.
SWEEP_STEPS = (6.0, SHIPPED_STEP_DEG, 30.0)

# Where on the circle the arc sits: straddling the top, starting at the top, and
# well down one side.
SWEEP_PHASE0 = (None, 0.0, 40.0)

#: Tolerance for the exact-geometry recovery, in arcminutes.
#:
#: With ``pressure_hpa = 0`` the whole chain is exact vacuum geometry and a
#: three-point fit is exact by construction, so the only error is double
#: rounding in the two independent transform chains, amplified by the fit's
#: conditioning (~184x at the 6 deg step — see the conditioning tests below).
#: Measured worst residual over the whole 2565-point matrix: 9.4e-3 arcmin
#: (0.56 arcsec), and it comes from the single grid point whose middle pointing
#: lands exactly on the zenith, where the engine's RA/Dec -> az/alt step loses a
#: few milliarcseconds of azimuth to cancellation before the 6 deg step
#: multiplies it up. Everything else is orders of magnitude below that.
#:
#: 0.05' (3 arcsec) therefore has ~5x headroom over the measured floor while
#: staying ~20x tighter than the 1.0' threshold the product cares about: a sign
#: slip, a dropped cosine or a hemisphere mix-up moves the answer by
#: arcminutes-to-degrees, so this is a bug detector, not a noise floor.
RECOVERY_TOL_ARCMIN = 0.05


@pytest.mark.parametrize("lat", SWEEP_LATS)
@pytest.mark.parametrize("d_alt,d_az", SWEEP_INJECTIONS)
def test_engine_recovers_the_injected_axis_error(lat, d_alt, d_az):
    """The headline accuracy test: inject (alt, az), get (alt, az) back.

    Each parametrised case runs the whole (rho x step x phase) grid, so the
    matrix is 2565 engine fits in total — about two seconds.
    """
    # A southern axis sitting EXACTLY on the meridian (d_az == 0) trips an
    # exact-zero engine quirk; it has its own test rather than poisoning this
    # one. See
    # test_southern_exact_zero_azimuth_is_a_bit_pattern_not_a_reachable_state.
    if lat <= 0.0 and d_az == 0.0:
        pytest.skip("covered by the southern exact-meridian test")

    cases = 0
    realistic = 0
    for rho in SWEEP_RHOS:
        for step in SWEEP_STEPS:
            for phase0 in SWEEP_PHASE0:
                err, alts = measure(lat, d_alt, d_az, rho, step,
                                    phase0_deg=phase0)
                cases += 1
                if min(alts) > 15.0:
                    realistic += 1
                d = max(abs(err["alt_arcmin"] - d_alt * 60.0),
                        abs(err["az_arcmin"] - d_az * 60.0))
                assert d < RECOVERY_TOL_ARCMIN, (
                    f"lat {lat} rho {rho} step {step} phase0 {phase0}: injected "
                    f"({d_alt*60:+.3f}', {d_az*60:+.3f}') got "
                    f"({err['alt_arcmin']:+.4f}', {err['az_arcmin']:+.4f}')")
                # total is the quadrature sum of the two knob angles
                assert abs(err["total_arcmin"]
                           - math.hypot(err["alt_arcmin"], err["az_arcmin"])) < 1e-6
    assert cases == len(SWEEP_RHOS) * len(SWEEP_STEPS) * len(SWEEP_PHASE0), cases
    # Guard against a matrix that only ever exercises unphysical geometry: at
    # least a third of the grid must have all three pointings well up in the sky.
    assert realistic >= cases // 3, (
        f"only {realistic}/{cases} grid points kept all three pointings above "
        f"15 deg altitude")


@pytest.mark.parametrize("lat", (72.0, LAT, 5.0, -5.0, -33.0, -68.0))
def test_knob_directions_follow_the_sign_of_the_error(lat):
    """The direction hints are what a user actually turns a bolt by."""
    northern = lat > 0.0
    combos = 0
    for d_alt, d_az in ((0.4, 0.3), (-0.4, 0.3), (0.4, -0.3), (-0.4, -0.3)):
        err, _alts = measure(lat, d_alt, d_az, 35.0, SHIPPED_STEP_DEG)
        combos += 1
        axis_above_pole = (d_alt > 0.0) if northern else (d_alt < 0.0)
        assert err["alt_direction"] == ("down" if axis_above_pole else "up"), \
            (lat, d_alt, err)
        if northern:
            expect_az = "left_west" if d_az >= 0.0 else "right_east"
        else:
            expect_az = "left_east" if d_az >= 0.0 else "right_west"
        assert err["az_direction"] == expect_az, (lat, d_az, err)
    assert combos == 4


def test_latitude_exactly_zero_takes_the_southern_branch_harmlessly():
    """Latitude exactly 0 is SOUTH (``northern`` is ``latitude_deg > 0``).

    That could have been a trap — a site left at the unset default of 0 would be
    decomposed against the south pole — but it is not, because the RA axis is a
    LINE and ``hemisphere_correct`` negates the fitted vector into the required
    half. An axis aimed at the north celestial pole from the equator is aimed at
    the south celestial pole too, and the engine reports it as aligned.
    """
    # Southern sign convention applies, and the injected error comes back.
    err, _ = measure(0.0, 0.5, 0.4, 35.0, SHIPPED_STEP_DEG, phase0_deg=-30.0)
    assert abs(err["alt_arcmin"] - 30.0) < RECOVERY_TOL_ARCMIN, err
    assert abs(err["az_arcmin"] - 24.0) < RECOVERY_TOL_ARCMIN, err

    # An axis on the NORTH pole at latitude 0: same line, so zero error, not 180.
    north_axis = neu_from_altaz(0.0, 0.0)
    solves = []
    for i in range(3):
        v = cone_point(north_axis, 35.0, -30.0 + i * SHIPPED_STEP_DEG)
        ts = T0 + i * 90.0
        ra, dec = radec_from_neu(v, 0.0, LON, ts)
        solves.append({"ra_hours": ra / 15.0, "dec_deg": dec,
                       "timestamp_unix_s": ts, "position_angle_deg": 30.0})
    err_n = fit(solves, 0.0)
    assert err_n["total_arcmin"] < 0.02, err_n
    assert err_n["total_arcmin"] / 60.0 < MAX_PLAUSIBLE_ERROR_DEG


def test_southern_exact_zero_azimuth_is_a_bit_pattern_not_a_reachable_state():
    """CHARACTERISATION. Was: "a southern mount with exactly zero azimuth error
    is reported as 180 degrees off" — asserted as a defect, REFUTED because the
    trigger is an exact double-precision zero, which no input path can deliver.

    The mechanism is real. ``geom::unit_vector_to_altaz`` returns azimuth 0 when
    the fitted axis vector's east component is ``0.0`` exactly, even when the
    north component is negative; ``hemisphere_correct`` forces exactly that
    negative north component on every southern fit, so such an axis reads az 0
    instead of az 180 and ``az_err = az + 180`` comes back as 180 deg = 10800'.

    What kills it as a defect is how the zero arises. It is not a rounding-scale
    quantity — it is the bit pattern +0.0, produced only when a perfectly
    symmetric synthetic geometry makes a cross-product component cancel to the
    last bit. This test pins BOTH halves: the exact-zero grid still hits it, and
    a SIXTY-BILLIONTH of an arcsecond of injected azimuth error — 9 orders of
    magnitude below the best plate solve ever made, and far below the last bit
    of any real mount's alignment — removes every single occurrence. A rig
    cannot be aligned to that; a solve cannot measure to that. There is nothing
    here to fix in the engine that a real run could ever reach.
    """
    grid = [(lat, rho, step, phase0, d_alt)
            for lat in (-2.0, -20.0, -33.0, -45.0, -65.0, -80.0)
            for rho in (MIN_POLE_DISTANCE_DEG, 25.0, 30.0, 38.0, 45.0, 60.0)
            for step in (6.0, 10.0, SHIPPED_STEP_DEG, 20.0, 30.0)
            for phase0 in (None, 0.0, 6.0, 40.0)
            for d_alt in (0.0, 0.5)]
    assert len(grid) == 6 * 6 * 5 * 4 * 2, f"grid was {len(grid)} cases"

    exact = [g for g in grid
             if abs(measure(g[0], g[4], 0.0, g[1], g[2],
                            phase0_deg=g[3])[0]["az_arcmin"]) > 1.0]
    # The quirk is real and reproducible: a minority of the exact-zero grid, and
    # always the full 180 degrees rather than something merely large.
    assert exact, "the exact-zero quirk vanished; the rest of this test is moot"
    assert len(exact) < len(grid) // 2, (
        f"{len(exact)}/{len(grid)} cases — too many for exact cancellation")
    for lat, rho, step, phase0, d_alt in exact:
        err, _ = measure(lat, d_alt, 0.0, rho, step, phase0_deg=phase0)
        assert abs(abs(err["az_arcmin"]) - 10800.0) < 1e-6, (lat, rho, step, err)

    # ...and now the refutation: perturb the injected azimuth by 1e-9 arcminutes
    # (6e-8 arcsec) and every one of them reports the truth instead.
    whisper_arcmin = 1e-9
    survivors = []
    for lat, rho, step, phase0, d_alt in exact:
        err, _ = measure(lat, d_alt, whisper_arcmin / 60.0, rho, step,
                         phase0_deg=phase0)
        if abs(err["az_arcmin"] - whisper_arcmin) > 1.0:
            survivors.append((lat, rho, step, phase0, d_alt,
                              round(err["az_arcmin"], 1)))
    assert not survivors, (
        f"{len(survivors)}/{len(exact)} cases survived a 1e-9 arcmin azimuth "
        f"perturbation, so the trigger is NOT an exact-zero bit pattern and the "
        f"claim needs re-opening: {survivors[:5]}")


def test_northern_axis_on_the_meridian_is_unaffected():
    """Control for the test above: the same grid in the north is clean.

    Northern fits are forced to x > 0, where the ``y == 0`` shortcut happens to
    return the right answer, so this must pass. If it ever fails, the defect is
    wider than the southern branch.
    """
    bad, total = [], 0
    for lat in (2.0, 20.0, LAT, 65.0, 80.0):
        for rho in (MIN_POLE_DISTANCE_DEG, 30.0, 45.0, 60.0):
            for step in (6.0, SHIPPED_STEP_DEG, 30.0):
                for phase0 in (None, 0.0, 40.0):
                    total += 1
                    err, _ = measure(lat, 0.5, 0.0, rho, step, phase0_deg=phase0)
                    if abs(err["az_arcmin"]) > 1.0:
                        bad.append((lat, rho, step, phase0,
                                    round(err["az_arcmin"], 1)))
    assert total == 5 * 4 * 3 * 3, total
    assert not bad, bad


# ---------------------------------------------------------------------------
# 2. Refraction: the positive control for GEOMETRIC, and the sign of the
#    refracted-pole correction.
# ---------------------------------------------------------------------------

def test_refraction_is_live_and_altitude_only():
    """Turning the atmosphere on must change the answer, in altitude only.

    Without this, every ``pressure_hpa = 0`` test above could be passing because
    the option silently did nothing.
    """
    solves, alts, _ax = make_solves(LAT, LON, 0.5, 0.4, 38.0, SHIPPED_STEP_DEG)
    assert min(alts) > 20.0, "precondition: refraction test needs a sane altitude"

    exact = fit(solves)                       # pressure 0, true pole
    real = fit(solves, options={})            # engine defaults: standard air,
    #                                           compared against the refracted pole
    assert abs(exact["alt_arcmin"] - 30.0) < RECOVERY_TOL_ARCMIN, exact
    assert abs(exact["az_arcmin"] - 24.0) < RECOVERY_TOL_ARCMIN, exact

    # The refracted pole sits ABOVE the true pole, so in the north the reported
    # altitude error shrinks. Standard-atmosphere refraction at 45 deg altitude
    # is around a minute of arc, and only part of it survives the differencing.
    shift = real["alt_arcmin"] - exact["alt_arcmin"]
    assert -2.0 < shift < -0.1, f"refraction moved altitude by {shift:.4f}'"
    # Azimuth is untouched: refraction is a purely vertical displacement.
    assert abs(real["az_arcmin"] - exact["az_arcmin"]) < 1e-6, (real, exact)


def test_site_elevation_is_accepted_and_then_ignored():
    """``elevation_m`` crosses the binding and changes nothing.

    Refraction scales with the pressure at the observer, and the engine already
    has the elevation that determines it, but the pressure stays at the sea-level
    default unless the caller passes ``pressure_hpa`` — and ``astrodeck.polar.
    native._options`` never does for a real rig. The second half of this test is
    the harness guard: pressure DOES change the answer, so a broken options path
    could not make the first half pass by accident.
    """
    solves, _alts, _ax = make_solves(LAT, LON, 0.5, 0.4, 38.0, SHIPPED_STEP_DEG)
    sea = fit(solves, options={}, elev_m=0.0)
    high = fit(solves, options={}, elev_m=3000.0)
    assert sea["alt_arcmin"] == high["alt_arcmin"], (sea, high)
    assert sea["az_arcmin"] == high["az_arcmin"], (sea, high)

    # 3000 m is about 700 hPa; pass it explicitly and the answer moves.
    thin = fit(solves, options={"pressure_hpa": 700.0})
    assert abs(thin["alt_arcmin"] - sea["alt_arcmin"]) > 0.05, (thin, sea)


# ---------------------------------------------------------------------------
# 3. Conditioning: how far does the answer move when one solve is off?
# ---------------------------------------------------------------------------

def amplification(lat=LAT, rho=38.0, step=SHIPPED_STEP_DEG, index=1,
                  eps_deg=0.002, phase0=None):
    """Degrees of reported-error movement per degree of radial error at one point.

    ``eps_deg`` is deliberately small (7.2 arcsec) so the number is the local
    derivative rather than a large-displacement average — the response is
    slightly super-linear.
    """
    base, _a = measure(lat, 0.0, 0.0, rho, step, phase0_deg=phase0)
    off, _b = measure(lat, 0.0, 0.0, rho, step, phase0_deg=phase0,
                      perturb=(index, eps_deg))
    return _err_distance_arcmin(off, base) / 60.0 / eps_deg


def test_middle_point_amplification_at_the_shipped_step():
    """Pin the 44x claim in ``MAX_PLAUSIBLE_ERROR_DEG``'s docstring.

    Three points 12 deg apart on a small circle make near-parallel chords, so
    the plane normal is a small cross product and a radial error at the MIDDLE
    point levers the fitted axis hard. Measured here: ~46x at the middle,
    ~23x at either end. Bounds are wide enough not to be brittle and tight
    enough that halving or doubling the conditioning shows up.
    """
    mid = amplification(index=1)
    first = amplification(index=0)
    last = amplification(index=2)
    assert 35.0 < mid < 60.0, f"middle-point amplification {mid:.2f}x"
    assert 15.0 < first < 32.0, f"first-point amplification {first:.2f}x"
    assert abs(first - last) < 0.5, (first, last)
    # The middle point is the worst one, by about a factor of two.
    assert mid > 1.7 * first

    # The docstring's worked example: 0.05 deg at the middle swings the axis a
    # couple of degrees.
    base, _ = measure(LAT, 0.0, 0.0, 38.0, SHIPPED_STEP_DEG)
    off, _ = measure(LAT, 0.0, 0.0, 38.0, SHIPPED_STEP_DEG, perturb=(1, 0.05))
    swing_deg = _err_distance_arcmin(off, base) / 60.0
    assert 1.5 < swing_deg < 3.5, f"0.05 deg at the middle swung {swing_deg:.3f} deg"


def test_conditioning_improves_as_the_square_of_the_ra_step():
    """The RA step, not the pole distance, is what sets the conditioning.

    amp x step^2 is constant to a few percent from 6 deg to 60 deg. Read this as
    a description of the geometry, NOT as an argument for widening
    ``_RA_STEP_HOURS``: what the amplification multiplies is the second
    difference of the three solve errors, which is small (see
    :func:`test_the_shipped_step_amplifies_only_the_second_difference`), and the
    wider arc is refused for altitude long before it helps (see
    :func:`test_the_wider_ra_step_that_would_help_is_refused_for_altitude`).
    """
    steps = (6.0, SHIPPED_STEP_DEG, 20.0, 30.0, 45.0, 60.0)
    amps = [amplification(step=s) for s in steps]
    assert len(amps) == 6
    # strictly improving with a longer arc
    assert all(a > b for a, b in zip(amps, amps[1:])), list(zip(steps, amps))
    invariant = [a * s * s for a, s in zip(amps, steps)]
    lo, hi = min(invariant), max(invariant)
    assert hi / lo < 1.15, f"amp*step^2 not constant: {list(zip(steps, invariant))}"
    # And concretely: the shipped step is ~6x worse than a 30 deg arc.
    shipped = amplification(step=SHIPPED_STEP_DEG)
    wide = amplification(step=30.0)
    assert shipped / wide > 5.0, (shipped, wide)


def test_conditioning_does_not_depend_on_the_pole_distance():
    """Pole distance is NOT what makes the fit ill-conditioned.

    A radial error at one point displaces it out of plane by ``eps*sin(rho)``,
    and the circle's lever arm is also proportional to ``sin(rho)``, so the two
    cancel: the amplification of an angular pointing/solve error is flat in rho.
    Measured across 5 deg to 70 deg of pole distance it varies by under 5%.

    This is a characterisation, and it contradicts the stated rationale for
    ``MIN_POLE_DISTANCE_DEG`` ("each solve's small residual is amplified when
    the circle is extrapolated to an axis direction ... at 20 degrees the lever
    arm is workable; at 5 it is not").
    """
    rhos = (5.0, 10.0, MIN_POLE_DISTANCE_DEG, 38.0, 55.0, 70.0)
    amps = [amplification(rho=r) for r in rhos]
    assert len(amps) == 6 and all(a > 0 for a in amps)
    assert max(amps) / min(amps) < 1.05, (
        f"amplification varies with pole distance: {list(zip(rhos, amps))}")


def test_along_circle_perturbation_does_not_move_the_fit():
    """Moving a point ALONG its circle changes nothing — only radial error does.

    Confirms the mechanism the amplification tests measure: the fit is a plane
    through three points, so in-plane motion is invisible and the entire
    sensitivity is to the out-of-plane component.
    """
    axis = axis_vector(LAT, 0.0, 0.0)
    base = None
    checked = 0
    for shift in (0.0, 0.05, 0.5, 2.0):
        solves = []
        for i in range(3):
            phase = -SHIPPED_STEP_DEG + i * SHIPPED_STEP_DEG
            if i == 1:
                phase += shift
            v = cone_point(axis, 38.0, phase)
            ts = T0 + i * 90.0
            ra, dec = radec_from_neu(v, LAT, LON, ts)
            solves.append({"ra_hours": ra / 15.0, "dec_deg": dec,
                           "timestamp_unix_s": ts, "position_angle_deg": 30.0})
        err = fit(solves)
        if base is None:
            base = err
        else:
            moved = _err_distance_arcmin(err, base)
            assert moved < 1e-4, f"along-circle shift {shift} deg moved {moved}'"
        checked += 1
    assert checked == 4
    # ...whereas the same displacement applied radially moves it a long way.
    off, _ = measure(LAT, 0.0, 0.0, 38.0, SHIPPED_STEP_DEG, perturb=(1, 0.05))
    assert _err_distance_arcmin(off, base) > 60.0


def test_the_shipped_step_amplifies_only_the_second_difference():
    """CHARACTERISATION of what the 12 deg step's conditioning actually costs.

    Was: "the shipped 12 deg RA step makes the 1.0 arcmin aligned verdict
    unachievable", asserted from a 5 arcsec error injected at ONE point and
    coming back x46. REFUTED — not because the 46x is wrong (it is measured, and
    pinned in :func:`test_middle_point_amplification_at_the_shipped_step`) but
    because of WHAT it multiplies. The fit is a plane through three points, so it
    is blind to any component of the solve error that a plane can absorb:

    * a COMMON offset — a shared centring/scale bias, or the site pressure the
      driver never passes — moves all three points onto a different small circle
      about the SAME axis, and contributes exactly nothing;
    * a LINEAR ramp — which is how flexure and differential refraction actually
      enter across a 24 deg arc — has zero second difference, and 10 arcsec of
      it end to end costs 0.57', inside the 1.0' verdict on its own;
    * only the uncorrelated SECOND DIFFERENCE — one point off its circle while
      its neighbours are not — meets the full 46x.

    And the size of that last term is set by the plate solve, which fits a whole
    field of stars to place one field centre: sub-arcsecond centres are routine.
    At 0.3 arcsec of independent per-point scatter the reported total sits around
    0.2' with a long tail well inside 1.0'. The verdict is reachable at the
    shipped step, so ``_RA_STEP_HOURS`` MUST NOT be widened to "fix" it — see
    :func:`test_the_wider_ra_step_that_would_help_is_refused_for_altitude` for
    what widening it would actually do.
    """
    budget = 5.0 / 3600.0                      # 5 arcsec, in degrees

    base, alts = measure(LAT, 0.0, 0.0, 38.0, SHIPPED_STEP_DEG)
    assert min(alts) > 20.0, "precondition: pointings must be in usable sky"
    assert base["total_arcmin"] < 0.01, "precondition: unperturbed fit is exact"

    # 1. Common: the same 5" (and a gross 60") at all three points -> nothing.
    for common in (budget, 60.0 / 3600.0):
        err, _ = measure(LAT, 0.0, 0.0, 38.0, SHIPPED_STEP_DEG,
                         radial_eps_deg=(common, common, common))
        assert err["total_arcmin"] < 1e-4, (common, err)

    # 2. Linear: a 10" ramp across the arc. Only the second difference counts, so
    #    it makes no difference where the ramp is centred.
    ramp = 5.0 / 3600.0
    rising, _ = measure(LAT, 0.0, 0.0, 38.0, SHIPPED_STEP_DEG,
                        radial_eps_deg=(0.0, ramp, 2.0 * ramp))
    centred, _ = measure(LAT, 0.0, 0.0, 38.0, SHIPPED_STEP_DEG,
                         radial_eps_deg=(-ramp, 0.0, ramp))
    assert 0.4 < rising["total_arcmin"] < 0.8, rising
    assert abs(rising["total_arcmin"] - centred["total_arcmin"]) < 1e-6
    assert rising["total_arcmin"] < _DONE_THRESHOLD_ARCMIN, rising

    # 3. Second difference: the same 5" at the middle point alone. THIS is the
    #    term the 46x applies to, and it does break the 1.0' verdict...
    isolated, _ = measure(LAT, 0.0, 0.0, 38.0, SHIPPED_STEP_DEG,
                          perturb=(1, budget))
    assert 3.0 < isolated["total_arcmin"] < 5.0, isolated
    assert isolated["total_arcmin"] > _DONE_THRESHOLD_ARCMIN, isolated
    # ...and it is ~46x the input, i.e. the same conditioning, not a new effect.
    assert 35.0 < isolated["total_arcmin"] / 60.0 / budget < 60.0, isolated

    # 4. ...but 5" of uncorrelated per-point error is not the regime. At 0.3"
    #    the answer is a fifth of an arcminute and almost never breaks 1.0'.
    sigma = 0.3 / 3600.0
    rng = random.Random(20260806)
    totals = []
    for _ in range(200):
        eps = tuple(rng.gauss(0.0, sigma) for _ in range(3))
        err, _ = measure(LAT, 0.0, 0.0, 38.0, SHIPPED_STEP_DEG,
                         radial_eps_deg=eps)
        totals.append(err["total_arcmin"])
    median = statistics.median(totals)
    inside = sum(t <= _DONE_THRESHOLD_ARCMIN for t in totals) / len(totals)
    assert 0.10 < median < 0.45, f"median reported total {median:.3f}'"
    assert inside >= 0.95, (
        f"only {inside:.0%} of 0.3\"-scatter runs came in under "
        f"{_DONE_THRESHOLD_ARCMIN:.1f}'")


def test_the_wider_ra_step_that_would_help_is_refused_for_altitude():
    """The 30 deg arc really is better conditioned — and unusable.

    First half: same 5 arcsec at the same middle point, only the step changes,
    and the answer falls inside ``_DONE_THRESHOLD_ARCMIN`` — with the 1/step^2
    law visible in the ratio. That is the whole of the case for widening
    ``_RA_STEP_HOURS``, and it is real.

    Second half is why it is refused anyway. The arc always steps AWAY from the
    meridian (crossing it flips a GEM mid-measurement — see
    ``polar/native.py::_ra_step_hours``), and away from the meridian is downhill.
    From the same start as the 2026-08-06 run (HA +0.69h) at this file's 45 deg
    site, two 30 deg steps put the third point at 13.8 deg altitude on the
    celestial equator and 2.7 deg at Dec -15 — below ``LOW_MEASUREMENT_ALT_DEG``
    and ``MIN_MEASUREMENT_ALT_DEG`` respectively, so ``_refuse_low_arc`` would
    refuse the run outright. The shipped 12 deg step keeps every point of both
    arcs above the warning line. A better-conditioned fit you are not allowed to
    take is not an improvement.
    """
    eps_deg = 5.0 / 3600.0
    wide, alts = measure(LAT, 0.0, 0.0, 38.0, 30.0, perturb=(1, eps_deg))
    shipped, _ = measure(LAT, 0.0, 0.0, 38.0, SHIPPED_STEP_DEG,
                         perturb=(1, eps_deg))
    assert min(alts) > 10.0, "precondition: pointings must be in usable sky"
    assert wide["total_arcmin"] <= _DONE_THRESHOLD_ARCMIN, wide
    ratio = shipped["total_arcmin"] / wide["total_arcmin"]
    assert abs(ratio - (30.0 / SHIPPED_STEP_DEG) ** 2) < 0.5, ratio

    def altitude(ha_hours, dec_deg):
        ha, dec, lat = (ha_hours * 15.0 * D2R, dec_deg * D2R, LAT * D2R)
        return math.degrees(math.asin(math.sin(dec) * math.sin(lat)
                                      + math.cos(dec) * math.cos(lat)
                                      * math.cos(ha)))

    start_ha = 0.69                       # the 2026-08-06 run's starting HA
    def arc(step_deg, dec_deg):           # noqa: E306 - reads better adjacent
        return [altitude(start_ha + n * step_deg / 15.0, dec_deg)
                for n in range(3)]

    assert min(arc(SHIPPED_STEP_DEG, 0.0)) > LOW_MEASUREMENT_ALT_DEG
    assert min(arc(SHIPPED_STEP_DEG, -15.0)) > LOW_MEASUREMENT_ALT_DEG
    assert min(arc(30.0, 0.0)) < LOW_MEASUREMENT_ALT_DEG, arc(30.0, 0.0)
    assert min(arc(30.0, -15.0)) < MIN_MEASUREMENT_ALT_DEG, arc(30.0, -15.0)


# ---------------------------------------------------------------------------
# 4. What the engine does NOT notice — the meridian-flip class of failure
# ---------------------------------------------------------------------------

def _flipped_run(cone_arcmin, lat=LAT, rho=38.0, step=SHIPPED_STEP_DEG):
    """Three solves from a run that pier-flipped after the first point.

    A flip does two things to the measurement: the camera angle rotates 180 deg
    (the only thing the engine can see) and every non-perpendicularity in the
    optical train reverses sign, so the tube's distance from the mount axis
    changes by twice the cone error. The pointings after the flip therefore sit
    on a DIFFERENT small circle from the first one, and no single circle passes
    through all three.
    """
    before, _a, _ax = make_solves(lat, LON, 0.5, 0.4, rho, step, pa_deg=30.0)
    after, _b, _bx = make_solves(lat, LON, 0.5, 0.4,
                                 rho + 2 * cone_arcmin / 60.0, step, pa_deg=210.0)
    return [before[0], after[1], after[2]]


def test_a_pier_flip_is_visible_only_through_the_position_angle_spread():
    """The one signal that would have caught the 2026-08-06 run.

    The engine cannot refuse a flipped set — three points always define a plane —
    so ``position_angle_spread_deg`` is the entire early-warning system. This
    pins that it fires at ~180 deg, which is what a caller (and the log line in
    ``_log_pa_spread``) keys off. Drop ``position_angle_deg`` from the solves and
    the spread collapses to 0 and this test fails, which is the point: it guards
    the field the driver has to keep sending.
    """
    solves = _flipped_run(3.0)
    err = fit(solves)
    assert abs(err["position_angle_spread_deg"] - 180.0) < 0.01, err
    assert "position_angle_spread_large" in err["flags"], err

    # And the number it reports alongside that flag is badly wrong: the injected
    # error is 30' of altitude but a 3' cone error reversing sign turns it into
    # degrees.
    assert err["alt_arcmin"] > 120.0, err
    # Still small enough to slip past the caller's plausibility gate, so the flag
    # really is the only thing standing between this and "adjust the mount".
    assert err["total_arcmin"] / 60.0 < MAX_PLAUSIBLE_ERROR_DEG, err


def test_a_flip_with_no_camera_rotation_leaves_no_signal_at_all():
    """If the position angles happen to match, nothing in the output objects.

    Same geometry as above with the camera angle held constant and a 1' cone
    error instead of 3', so the reported total stays under the 2 deg
    ``initial_error_large`` trip point. Every quality flag is then clear — and
    the reported altitude error is still 45 arcminutes away from the truth.
    """
    before, _a, _ax = make_solves(LAT, LON, 0.5, 0.4, 38.0, SHIPPED_STEP_DEG,
                                  pa_deg=30.0)
    after, _b, _bx = make_solves(LAT, LON, 0.5, 0.4, 38.0 + 2.0 / 60.0,
                                 SHIPPED_STEP_DEG, pa_deg=30.0)
    err = fit([before[0], after[1], after[2]])
    assert err["position_angle_spread_deg"] == 0.0, err
    assert err["flags"] == [], err
    assert err["total_arcmin"] / 60.0 < 2.0, err  # below every warning threshold
    # ...yet the altitude error is out by three quarters of a degree.
    assert abs(err["alt_arcmin"] - 30.0) > 40.0, err


@pytest.mark.parametrize("eps_deg,expect_deg", [
    (0.05, 2.4), (0.1, 5.1), (0.3, 19.0), (0.5, 39.2),
])
def test_an_out_of_plane_solve_is_reported_as_a_confident_polar_error(
        eps_deg, expect_deg):
    """A three-point fit is exact by construction, so nothing checks itself.

    One point displaced radially by ``eps_deg`` comes back as a large, clean,
    unqualified polar error. At 0.3 deg (18') of displacement the engine reports
    19 degrees of polar error — under the caller's 30 deg plausibility bound, so
    it is published to the operator with "adjust the mount".

    Documented rather than asserted-as-wrong: the engine has no fourth point and
    therefore no residual to check. The finding is that the API offers the caller
    no consistency signal for exactly the range that gets through the gate.
    """
    err, _alts = measure(LAT, 0.0, 0.0, 38.0, SHIPPED_STEP_DEG,
                         perturb=(1, eps_deg))
    got = err["total_arcmin"] / 60.0
    assert abs(got - expect_deg) < 0.4, (eps_deg, got)
    # No flag distinguishes "large but real" from "the fit lost conditioning".
    assert "degenerate_geometry" not in err["flags"], err
    if got <= MAX_PLAUSIBLE_ERROR_DEG:
        assert err["flags"] in (["initial_error_large"], ["initial_error_huge"]), err


def test_repeated_measurement_points_are_refused():
    """The engine's only hard refusal: a zero-length plane normal.

    ``determine_plane_vector`` is ``cross(b - a, c - b)``, so ANY repeated pair
    collapses it — three identical points, and either adjacent pair. A mount
    that failed one of the two slews is therefore caught rather than fitted.
    """
    s = {"ra_hours": 3.0, "dec_deg": 52.0, "timestamp_unix_s": T0,
         "position_angle_deg": 12.0}
    other = dict(s, ra_hours=3.8)
    triples = ([s, s, s], [s, s, other], [other, s, s], [s, other, s])
    for triple in triples:
        with pytest.raises(ValueError, match="mount did not move"):
            native.tppa_from_three(triple, site_dict(), GEOMETRIC)
    assert len(triples) == 4

    # That is the whole of it. Three DISTINCT directions on a sphere are never
    # collinear, so the cross product never vanishes and the engine always
    # answers — including for three solves that are plainly not one RA rotation.
    not_a_rotation = [
        {"ra_hours": 3.0, "dec_deg": 52.0, "timestamp_unix_s": T0,
         "position_angle_deg": 12.0},
        {"ra_hours": 9.0, "dec_deg": 10.0, "timestamp_unix_s": T0 + 90.0,
         "position_angle_deg": 12.0},
        {"ra_hours": 20.0, "dec_deg": -30.0, "timestamp_unix_s": T0 + 180.0,
         "position_angle_deg": 12.0},
    ]
    err = native.tppa_from_three(not_a_rotation, site_dict(), GEOMETRIC)["error"]
    assert math.isfinite(err["total_arcmin"])
    assert err["total_arcmin"] > 0.0


# ---------------------------------------------------------------------------
# 5. What the reported numbers actually mean
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lat", (20.0, LAT, 65.0, 80.0))
def test_total_error_is_knob_quadrature_not_sky_separation(lat):
    """``total_arcmin`` is hypot(alt knob, az knob), not the axis-pole angle.

    An azimuth knob angle subtends ``cos(latitude)`` on the sky, so at high
    latitude the reported total overstates how far the axis really is from the
    pole — 2.4x at 65 deg, 5.8x at 80 deg for a pure azimuth error. That is the
    right quantity for "how far to turn the bolt" and the wrong one for "how far
    off am I", and ``_DONE_THRESHOLD_ARCMIN`` is applied to it.
    """
    d_az = 1.0  # degree of pure azimuth knob error
    err, _alts = measure(lat, 0.0, d_az, 35.0, SHIPPED_STEP_DEG)
    axis = neu_from_altaz(lat, d_az)
    pole = neu_from_altaz(lat, 0.0)
    sep_arcmin = math.degrees(math.acos(max(-1.0, min(1.0, _dot(axis, pole))))) * 60.0

    assert abs(err["total_arcmin"] - 60.0) < RECOVERY_TOL_ARCMIN, err
    # The reported total is the knob angle; the sky separation is cos(lat) of it.
    assert abs(sep_arcmin - 60.0 * math.cos(lat * D2R)) < 0.2, sep_arcmin
    assert err["total_arcmin"] > sep_arcmin
    ratio = err["total_arcmin"] / sep_arcmin
    assert abs(ratio - 1.0 / math.cos(lat * D2R)) < 0.02, ratio

    # A pure ALTITUDE error, by contrast, is reported one-for-one.
    err_alt, _ = measure(lat, 1.0, 0.0, 35.0, SHIPPED_STEP_DEG)
    assert abs(err_alt["total_arcmin"] - 60.0) < RECOVERY_TOL_ARCMIN, err_alt


def test_reported_error_is_independent_of_the_order_of_the_three_solves():
    """Reversing the measurement order must not change the answer."""
    solves, _alts, _ax = make_solves(LAT, LON, 0.8, -0.6, 38.0, SHIPPED_STEP_DEG)
    fwd = fit(solves)
    rev = fit(list(reversed(solves)))
    assert _err_distance_arcmin(fwd, rev) < 1e-6, (fwd, rev)
