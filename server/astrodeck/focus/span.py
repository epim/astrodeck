"""How wide a sweep has to be, measured rather than assumed.

THE CONSTANT THIS REPLACES. ``run_autofocus`` has always swept
``steps_each_side=4`` points of ``step=350``: +/-1400 focuser steps, chosen
once and never checked against a rig. On 2026-08-17, with the detector fixed
(#219), the rig measured 27.06 and 27.38 px at +/-350 from a 2.96 px focus --
nine times the tip, on the FIRST point off centre. Everything beyond it was
spent driving the drawtube to positions where the stars were too bloated to
count: the outermost point of that sweep held 6 stars against 1172 at the tip.

WHY THIS CAN BE COMPUTED AT ALL. ``imaging.defocus.focus_from_two`` already
states the physics this rests on -- a defocused star's diameter grows LINEARLY
with distance from focus, "with no knowledge of the aperture, the f-ratio or
the step size". A V-curve is therefore the hyperbola

    size(d) = sqrt(y0**2 + (m*d)**2)

with ``y0`` the in-focus size and ``m`` the asymptotic slope in px per focuser
step. ``m`` is a property of the optical train and the focuser's step size: one
number per rig, constant across filters, temperature and target, and exactly
the number that decides how wide a sweep must be.

WE CANNOT DERIVE IT. ``Optics`` carries a focal length and no aperture, so
there is no f-ratio and so no critical-focus-zone formula (the Hocus Focus
dossier's ``2.44 * f**2 * 0.55`` um). The 2026-07-23 one-tap spec said the same
thing when it declined to make a CFZ claim: "we have no step-per-micron".

WE DO NOT NEED TO. Every sweep that succeeds measures it. Inverting last
night's own points through the hyperbola against y0 = 2.96 px:

    +/-350 steps   27.38 / 27.06 px   ->   m = 0.0777 / 0.0768
    +1050 steps    84.89 px           ->   m = 0.0808

Three points spanning a factor of three in defocus, agreeing to 5%. The model
holds on this rig, and the sweep hands it to us for free.

BINNING DOES NOT ENTER. ``y0`` and ``m`` both scale as 1/bin, so the half-span
``y0 * sqrt(F**2 - 1) / m`` is invariant. The binning is recorded for the log
line, not for the arithmetic.

Everything here is PURE and takes plain numbers, for the reason ``is_flat_sweep``
and ``curve_verdict`` are pure: the decision lives behind a camera, a focuser
and a Rust state machine, and a branch reachable only through a rig is a branch
nothing tests.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

#: The shipped sweep, kept as the fallback for a rig that has never completed a
#: sweep. Not a guess to be improved on -- it is the geometry every successful
#: focus run in this project's history used, so it is what an uncalibrated rig
#: is entitled to.
DEFAULT_STEP = 350
DEFAULT_STEPS_EACH_SIDE = 4

#: What the OUTERMOST point of the sweep should read, as a multiple of the
#: in-focus size. The one judgement call here, so it is worth showing the work.
#: Three separate pieces of shipped code bound it:
#:
#:   * the engine's flat-tip band is 0.1 px (``native.FLAT_TIP_BAND``) and a
#:     point inside it joins NEITHER trendline, so the INNERMOST point has to
#:     clear it. At 8x, the first point off centre reads
#:     sqrt(y0**2 + (m*step)**2) = 6.6 px against a 2.96 px tip -- clear by 35x.
#:   * ``curve_verdict`` needs both wings >= 1.5x the minimum (WING_RISE_FRAC).
#:     8x clears that by more than five times.
#:   * the wings have to stay MEASURABLE, which is the whole of #219. A 24 px
#:     ring is measured to within 2%; the 85 px one this replaces was carried by
#:     6 stars.
#:
#: Lower would tighten the sweep further and start crowding the flat tip;
#: higher buys nothing the fit uses and costs the range where stars stop being
#: countable.
OUTER_SIZE_FACTOR = 8.0

#: Absolute bounds on the derived step. The ceiling matches the UI's
#: ``AF_STEP_MAX``; the floor is deliberately well above one encoder step so a
#: freak slope cannot produce a sweep that never leaves the focus position.
STEP_MIN = 25
STEP_MAX = 1500

#: How many arm points a slope needs before it is a measurement. Below this it
#: is one or two frames' opinion, and the whole point of the calibration is that
#: the next sweep BELIEVES it.
MIN_SLOPE_POINTS = 4

#: A point must stand this far above the in-focus size before it says anything
#: about the slope. Near the tip the hyperbola is flat, so
#: sqrt(size**2 - y0**2) there is the difference of two nearly equal numbers --
#: all noise, divided by a small distance, which is how one tip-adjacent point
#: can dominate a median.
SLOPE_MIN_RISE_FRAC = 0.5


@dataclass(frozen=True)
class FocusCalibration:
    """What one successful sweep learned about this focuser's defocus response.

    ``slope_px_per_step`` and ``in_focus_px`` are AS MEASURED, at ``binning``;
    see the module docstring for why that needs no normalising.
    """
    slope_px_per_step: float
    in_focus_px: float
    binning: int
    n_points: int
    best_position: int
    #: Half the position range the measuring sweep actually COVERED, and a hard
    #: ceiling on what it may ask for -- see ``sweep_geometry``.
    #:
    #: A shallow slope asks for a WIDER sweep, correctly: a slow optical train
    #: defocuses gently. But an under-measured wing produces a shallow slope
    #: too, and then the next sweep is wider, its wings further out, its
    #: measurement worse -- a ratchet running away from focus. Measured on the
    #: simulator 2026-08-17: its gentle synthetic defocus reads 0.0033 px/step
    #: and asks for +/-5712 off a +/-1400 sweep. So the rule is "never wider than
    #: a sweep that worked", which is also the honest reading of a slope
    #: measured only over the range it sampled. 0 = unknown (an older record),
    #: which imposes no ceiling.
    swept_half_span: float = 0.0
    #: ISO date of the sweep that measured it. Carried so the log line can say
    #: how old the number it is acting on is, not for any decision -- an optical
    #: train's f-ratio does not go stale.
    measured_on: str = ""
    #: Focuser temperature when the sweep ran, degrees C, or None where the
    #: focuser does not report one (``Focuser.get_temperature`` defaults to
    #: None). Carried, like ``measured_on``, so the log line can say what the
    #: number was measured under -- and so the record answers "was this the same
    #: night" without anyone guessing. It decides NOTHING: the slope is a
    #: property of the optical train, and a thermal correction would be a
    #: separate measurement this project has not made.
    temperature_c: float | None = None

    def to_json(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_json(raw) -> "FocusCalibration | None":
        """``None`` for anything that is not a complete, usable record.

        Deliberately total: this reads a file a user can edit and a previous
        version can have written, and a half-parsed calibration would size a
        sweep off a fragment."""
        if not isinstance(raw, dict):
            return None
        try:
            cal = FocusCalibration(
                slope_px_per_step=float(raw["slope_px_per_step"]),
                in_focus_px=float(raw["in_focus_px"]),
                binning=int(raw.get("binning", 1)),
                n_points=int(raw.get("n_points", 0)),
                best_position=int(raw.get("best_position", 0)),
                swept_half_span=float(raw.get("swept_half_span", 0.0) or 0.0),
                measured_on=str(raw.get("measured_on", "")),
                temperature_c=(None if raw.get("temperature_c") is None
                               else float(raw["temperature_c"])),
            )
        except (KeyError, TypeError, ValueError):
            return None
        if not (math.isfinite(cal.slope_px_per_step) and cal.slope_px_per_step > 0):
            return None
        if not (math.isfinite(cal.in_focus_px) and cal.in_focus_px > 0):
            return None
        return cal


def defocus_slope(points, best_position: float, in_focus_px: float
                  ) -> float | None:
    """The asymptotic slope of this V-curve, px per focuser step, or None.

    ``points`` is ``[(position, size_px), ...]`` from a sweep that SUCCEEDED --
    a failed sweep's points describe something other than a V and would teach
    the next one a lie.

    Inverts the hyperbola point by point (``m_i = sqrt(size_i**2 - y0**2) / d_i``)
    and takes the MEDIAN, not a fit. A median needs no weighting scheme, cannot
    be dragged by the single bloated outer point that a least-squares line would
    hang off, and is the same choice the rest of this file's neighbours make for
    the same reason.

    Requires arm points on BOTH sides. A one-armed slope is measurable and
    wrong: it is exactly what a sweep that never bracketed focus produces, and
    the resulting number would then be used to make the NEXT sweep narrower
    still.
    """
    if not math.isfinite(in_focus_px) or in_focus_px <= 0:
        return None
    floor = in_focus_px * (1.0 + SLOPE_MIN_RISE_FRAC)
    left: list[float] = []
    right: list[float] = []
    for pos, size in points:
        try:
            d = float(pos) - float(best_position)
            y = float(size)
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(d) and math.isfinite(y)) or abs(d) < 1.0:
            continue
        if y < floor:
            continue
        m = math.sqrt(max(y * y - in_focus_px * in_focus_px, 0.0)) / abs(d)
        if not math.isfinite(m) or m <= 0:
            continue
        (left if d < 0 else right).append(m)
    both = left + right
    if len(both) < MIN_SLOPE_POINTS or not left or not right:
        return None
    both.sort()
    n = len(both)
    mid = (both[n // 2] if n % 2
           else 0.5 * (both[n // 2 - 1] + both[n // 2]))
    return float(mid) if mid > 0 else None


def half_span_steps(slope_px_per_step: float, in_focus_px: float,
                    factor: float = OUTER_SIZE_FACTOR) -> float:
    """How far each side of focus the sweep must reach for its outer point to
    read ``factor`` times the in-focus size. The hyperbola solved for d."""
    return in_focus_px * math.sqrt(max(factor * factor - 1.0, 0.0)) / slope_px_per_step


@dataclass(frozen=True)
class SweepGeometry:
    """The step size a sweep will use and the sentence explaining it.

    ``basis`` is not decoration. A sweep that quietly changed its own geometry
    between runs, with the log still printing the number from the signature, is
    the shape of defect this repo keeps finding in its own copy -- so the
    number and the reason for it are produced together, by one function, and
    the caller cannot log one without the other.
    """
    step: int
    steps_each_side: int
    basis: str
    #: True when a measured calibration decided this, false for the fallback.
    #: Read by the failure path: only a MEASURED span is worth discarding when a
    #: sweep comes back flat.
    measured: bool = False


def sweep_geometry(cal: FocusCalibration | None, *,
                   steps_each_side: int = DEFAULT_STEPS_EACH_SIDE,
                   focuser_max: int | None = None,
                   default_step: int = DEFAULT_STEP) -> SweepGeometry:
    """The step this rig should sweep at, and why.

    With no calibration this is the shipped default -- unchanged behaviour for
    a rig that has never completed a sweep, which is the only honest answer
    when nothing has been measured.
    """
    sides = max(1, int(steps_each_side))
    if cal is None:
        return SweepGeometry(
            int(default_step), sides,
            f"{default_step} steps, the shipped default — no completed sweep "
            f"has measured this focuser's defocus slope yet", False)

    half = half_span_steps(cal.slope_px_per_step, cal.in_focus_px)
    notes: list[str] = []
    # NEVER WIDER THAN A SWEEP THAT WORKED. See FocusCalibration.swept_half_span
    # for the ratchet this stops.
    if cal.swept_half_span > 0 and half > cal.swept_half_span:
        half = cal.swept_half_span
        notes.append(f"held to the ±{int(cal.swept_half_span)} steps that sweep "
                     f"actually covered")
    raw = half / sides
    step = int(round(raw))
    if step < STEP_MIN:
        step = STEP_MIN
        notes.append(f"raised to the {STEP_MIN}-step floor")
    elif step > STEP_MAX:
        step = STEP_MAX
        notes.append(f"held at the {STEP_MAX}-step ceiling")
    # A sweep cannot be wider than the travel it runs in. Not a fraction of it:
    # the focuser's whole range is a hard wall, and the leash in
    # ``focus.native`` already lets the engine roam twice the requested
    # half-span while bracketing.
    if focuser_max and focuser_max > 0:
        widest = int(focuser_max // (2 * sides))
        if widest >= STEP_MIN and step > widest:
            step = widest
            notes.append(f"narrowed to fit the focuser's {focuser_max}-step travel")
    when = f" on {cal.measured_on}" if cal.measured_on else ""
    if cal.temperature_c is not None:
        when += f" at {cal.temperature_c:.1f} C"
    basis = (f"{step} steps (±{step * sides}) — sized from a defocus slope of "
             f"{cal.slope_px_per_step:.4f} px/step measured{when} over "
             f"{cal.n_points} points at bin {cal.binning}, to put the outer "
             f"point at {OUTER_SIZE_FACTOR:g}× the {cal.in_focus_px:.2f} px "
             f"focus")
    if notes:
        basis += " — " + ", ".join(notes)
    return SweepGeometry(step, sides, basis, True)
