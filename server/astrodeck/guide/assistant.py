"""Guiding Assistant — pure measurement reducers, backlash state machine, and
the guide-parameter recommender (design 2026-07-24-guiding-assistant-design).

This module is **pure Python, zero hardware**: everything here is unit-testable
without a camera, a mount, or the Rust wheel. ``NativeGuider.run_guiding_assistant``
(guide/native.py) drives the actual device I/O (exposures + raw pulse-guides) and
feeds the centroids it reads into these reducers/state-machine, then hands the
result to :func:`recommend`.

Two measurement phases (design §1):

* **Phase A** (mount idle): :func:`reduce_phaseA` turns a ``(t, x_px, y_px)``
  centroid series into RMS / Dec-drift / RA periodic-error / seeing-jitter stats.
* **Phase B** (raw N/S pulses): :class:`BacklashRun` is a faithful pure-Python
  port of the dossier §10.3 ``BacklashTool`` state machine (``CLEAR_NORTH →
  STEP_NORTH → STEP_SOUTH → compute``), with :meth:`BacklashRun.compute`
  implementing the drift-corrected ``ComputeBacklashPx`` estimator.

:func:`recommend` applies the design §1.3 rules (conservative — PPEC / Lowpass2
are only ever *advanced opt-in* suggestions, never auto-selected). The **adaptive**
in-engine backlash controller (dossier §10.2) is DEFERRED to a Rust v2 follow-up
(design §7) — this module only seeds the static ``blc_pulse_ms`` the engine
already honors.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

# --------------------------------------------------------------------- constants
# Dossier §10.3 BacklashTool constants (backlash_comp.h:101-107).
BACKLASH_MIN_COUNT = 3                # consecutive good north clearing moves
BACKLASH_EXPECTED_DISTANCE = 4.0      # px — a north move must reach this to "count"
BACKLASH_EXEMPTION_DISTANCE = 40.0    # px — cumulative travel that skips clearing
MAX_CLEARING_STEPS = 100             # clearing-pulse cap
NORTH_PULSE_SIZE_MS = 500            # floor for the measurement north pulse width
MAX_NORTH_PULSES_MS = 8000          # ~8 s total north travel target
MAX_MEASUREMENT_STEPS = 40           # hard ceiling on N (or S) measurement pulses

# SAFETY GUARD (2): cap EVERY raw measurement pulse. Raw pulses bypass the
# engine's per-axis duration clamps (design §8 "Mount safety envelope"), so the
# assistant never issues a pulse longer than this ceiling (~1 s, PHD2's
# MAX_NORTH_PULSES count logic).
MAX_PULSE_MS = 1000

# The static Dec backlash seed is clamped to this ceiling before it is offered
# (matches config.py::set_guide + guideSettings.ts clampBlcPulse).
BLC_SEED_MAX_MS = 10000

# Result codes for the backlash estimate (dossier §10.3).
BL_VALID = "VALID"
BL_TOO_FEW_NORTH = "TOO_FEW_NORTH"      # usable, flagged low-confidence
BL_TOO_FEW_SOUTH = "TOO_FEW_SOUTH"
BL_NOT_CLEARED = "BL_NOT_CLEARED"
BL_SANITY = "SANITY"


# ------------------------------------------------------------------- dataclasses
@dataclass
class BacklashResult:
    """Output of :meth:`BacklashRun.compute` (dossier §10.3 ``ComputeBacklashPx``
    + sigma). ``bl_ms`` is the seed pulse (px / north_rate); ``result_code`` is
    one of the ``BL_*`` codes. ``y_rate_source`` records whether the Dec rate the
    seed was scaled by came from a real calibration or the declared guide rate
    (open decision D2)."""
    bl_px: float = 0.0
    bl_ms: int = 0
    sigma_ms: float = 0.0
    north_rate: float = 0.0            # px/ms, drift-corrected
    result_code: str = BL_TOO_FEW_NORTH
    y_rate_source: str = "declared"
    halted: bool = False               # SAFETY GUARD (1): OutOfRoom edge halt


@dataclass
class PhaseAResult:
    """Phase-A drift / periodic-error / seeing reduction (design §1.1). All
    ``*_px`` fields are guide-camera pixels; the arcsec views are derived only
    when ``image_scale_known``."""
    n: int = 0
    rms_ra_px: float = 0.0
    rms_dec_px: float = 0.0
    rms_total_px: float = 0.0
    drift_per_min_px: float = 0.0
    pe_amplitude_px: float = 0.0
    pe_period_s: float | None = None
    jitter_px: float = 0.0
    image_scale_arcsec: float = 1.0
    image_scale_known: bool = False


@dataclass
class Recommendation:
    """One before→after suggestion (design §1.3). ``key`` is a stable unique id
    (so two suggestions can target the same ``field`` — e.g. the default RA
    algorithm and the advanced PPEC opt-in); ``field`` is the applyable knob the
    UI ``buildApplyBody`` maps onto the PUT shape. ``advanced`` suggestions are
    EXCLUDED from the novice one-tap apply-all (D3)."""
    key: str
    field: str
    current: object
    recommended: object
    unit: str
    rationale: str
    confidence: str = "medium"          # "high" | "medium" | "low"
    advanced: bool = False

    def to_dict(self) -> dict:
        return {
            "key": self.key, "field": self.field, "current": self.current,
            "recommended": self.recommended, "unit": self.unit,
            "rationale": self.rationale, "confidence": self.confidence,
            "advanced": self.advanced,
        }


# ------------------------------------------------------------------ Phase A math
def _lstsq_line(t: np.ndarray, v: np.ndarray) -> tuple[float, float]:
    """Least-squares ``v = slope*t + intercept``. Returns ``(slope, intercept)``;
    ``(0.0, mean)`` when ``t`` has no spread (all samples at one instant)."""
    if t.size < 2 or float(np.ptp(t)) == 0.0:
        return 0.0, float(np.mean(v)) if v.size else 0.0
    A = np.vstack([t, np.ones_like(t)]).T
    slope, intercept = np.linalg.lstsq(A, v, rcond=None)[0]
    return float(slope), float(intercept)


def _dominant_period(t: np.ndarray, v: np.ndarray) -> float | None:
    """Best-effort dominant period (s) of a de-trended series via autocorrelation
    (design §1.1 "stretch"). Returns None unless the series spans at least ~1.5
    cycles of a clear autocorrelation peak — a conservative gate so noise never
    fakes a periodic term (which would wrongly flag PPEC-eligible)."""
    n = t.size
    if n < 12 or float(np.ptp(t)) <= 0.0:
        return None
    span = float(t[-1] - t[0])
    dt = span / (n - 1)
    if dt <= 0:
        return None
    x = v - np.mean(v)
    if float(np.std(x)) < 1e-9:
        return None
    ac = np.correlate(x, x, mode="full")[n - 1:]
    if ac[0] <= 0:
        return None
    ac = ac / ac[0]
    # First local maximum after the autocorrelation dips below zero (one full
    # phase past lag 0) — the fundamental period.
    dipped = False
    for lag in range(1, n - 1):
        if not dipped:
            if ac[lag] < 0.0:
                dipped = True
            continue
        if ac[lag] > ac[lag - 1] and ac[lag] >= ac[lag + 1] and ac[lag] > 0.3:
            period = lag * dt
            # need the sample window to actually contain ~1.5 cycles.
            if span >= 1.5 * period:
                return round(period, 1)
            return None
    return None


def reduce_phaseA(samples: list[tuple[float, float, float]], image_scale: float,
                  image_scale_known: bool = False) -> PhaseAResult:
    """Reduce a ``(t, x_px, y_px)`` centroid series into drift / PE / seeing stats
    (design §1.1). Pure. ``x`` is the RA axis, ``y`` the Dec axis (mount-frame ==
    camera-frame for the sim + the seed estimate).

    * **Dec drift** = the least-squares slope of ``y`` vs ``t`` (px/min).
    * **RA periodic error** = the de-trended ``x`` peak-to-peak / 2, plus a
      best-effort dominant period.
    * **Seeing jitter** = the high-frequency residual (successive-difference RMS)
      after de-trending — the input that sets how tight ``min_move`` can be.
    """
    if not samples:
        return PhaseAResult(image_scale_arcsec=image_scale,
                            image_scale_known=image_scale_known)
    arr = np.asarray(samples, dtype=float)
    t = arr[:, 0]
    x = arr[:, 1]
    y = arr[:, 2]
    n = t.size
    t0 = t - t[0]

    # De-trend each axis: RA carries only PE (no secular drift under tracking),
    # Dec carries the polar-alignment drift. Remove a linear fit from each.
    sx, ix = _lstsq_line(t0, x)
    sy, iy = _lstsq_line(t0, y)
    x_res = x - (sx * t0 + ix)
    y_res = y - (sy * t0 + iy)

    rms_ra = float(np.std(x_res)) if n else 0.0
    rms_dec = float(np.std(y_res)) if n else 0.0
    rms_total = math.hypot(rms_ra, rms_dec)
    drift_per_min_px = sy * 60.0     # Dec slope px/s -> px/min

    # RA periodic-error amplitude: peak-to-peak of the de-trended RA excursion.
    pe_amplitude_px = float(np.ptp(x_res)) / 2.0 if n else 0.0
    pe_period_s = _dominant_period(t0, x_res)

    # Seeing jitter: the high-frequency residual, isolated as the RMS of the
    # successive differences of the (de-trended) total offset / sqrt(2). This
    # rejects the slow PE / drift terms and keeps only frame-to-frame scatter.
    if n >= 2:
        d = np.diff(np.hypot(x_res, y_res))
        jitter_px = float(np.std(d)) / math.sqrt(2.0)
    else:
        jitter_px = 0.0

    return PhaseAResult(
        n=n,
        rms_ra_px=rms_ra, rms_dec_px=rms_dec, rms_total_px=rms_total,
        drift_per_min_px=drift_per_min_px,
        pe_amplitude_px=pe_amplitude_px, pe_period_s=pe_period_s,
        jitter_px=jitter_px,
        image_scale_arcsec=image_scale, image_scale_known=image_scale_known,
    )


# --------------------------------------------------------- Phase B state machine
@dataclass
class BacklashCommand:
    """A single instruction from :meth:`BacklashRun.step`. ``kind`` is ``"pulse"``
    (dispatch ``direction`` for ``ms``) or ``"done"`` (the run is over; call
    :meth:`BacklashRun.compute`)."""
    kind: str                          # "pulse" | "done"
    direction: str | None = None       # "north" | "south"
    ms: int | None = None


class BacklashRun:
    """Dossier §10.3 ``BacklashTool`` as a per-frame pure state machine.

    The orchestrator (``NativeGuider.run_guiding_assistant``) drives it: expose,
    read the Dec centroid, call :meth:`step` with the current camera ``(x, y)``,
    dispatch any returned pulse, repeat until ``done`` — then call
    :meth:`compute`.

    SAFETY GUARD (1): every state that would pulse the star further from its
    start FIRST checks :meth:`_out_of_room` (star within ``margin`` px of any
    frame edge) and cleanly halts the run instead of walking the star off the
    sensor. ``compute`` then reports whatever was gathered (flagged
    ``TOO_FEW_NORTH`` when the north run was cut short).
    """

    def __init__(self, y_rate: float, drift_per_sec_px: float, *,
                 frame_w: int, frame_h: int, margin: float,
                 y_rate_source: str = "declared") -> None:
        # y_rate is px/ms; guard a non-positive/absent rate so pulse widths and
        # the final divide can never blow up (the routine still runs, the seed is
        # just uninformative and the recommender floors it).
        self.y_rate = float(y_rate) if y_rate and y_rate > 0 else 0.0
        self.drift_per_sec_px = float(drift_per_sec_px)
        self.frame_w = int(frame_w)
        self.frame_h = int(frame_h)
        self.margin = float(margin)
        self.y_rate_source = y_rate_source

        self._state = "INIT"
        self._clearing_ms = self._clamp_pulse(
            (BACKLASH_EXPECTED_DISTANCE * 1.25 / self.y_rate)
            if self.y_rate > 0 else NORTH_PULSE_SIZE_MS)
        self._meas_ms = self._measurement_pulse_ms()

        self._marker: float | None = None
        self._accepted = 0
        self._last_delta_sign = 0
        self._cum_clearing = 0.0
        self._exemption = False
        self._clear_step = 0

        self._start_y: float | None = None
        self._north_ys: list[float] = []
        self._south_ys: list[float] = []
        self._north_count = 0
        self._step = 0
        self._msmt_start_t: float | None = None
        self._msmt_end_t: float | None = None
        self._halted = False

    # -- pulse sizing ---------------------------------------------------------
    def _clamp_pulse(self, ms: float) -> int:
        """SAFETY GUARD (2): floor at 50 ms (a pulse that moves nothing is
        pointless) and cap at :data:`MAX_PULSE_MS`."""
        return int(max(50, min(MAX_PULSE_MS, ms)))

    def _measurement_pulse_ms(self) -> int:
        """The measurement north/south pulse width: ``NORTH_PULSE_SIZE_MS`` floor,
        shrunk if the search region is so small that one pulse would move >70% of
        the margin (dossier §10.3), then capped by :data:`MAX_PULSE_MS`."""
        pw = float(NORTH_PULSE_SIZE_MS)
        if self.y_rate > 0:
            room_limited = 0.7 * self.margin / self.y_rate
            if room_limited < pw:
                pw = room_limited
        return self._clamp_pulse(pw)

    # -- edge guard -----------------------------------------------------------
    def _out_of_room(self, x: float, y: float) -> bool:
        """SAFETY GUARD (1): the star is within ``margin`` px of any frame edge."""
        m = self.margin
        return (x < m or x > self.frame_w - m
                or y < m or y > self.frame_h - m)

    # -- per-frame step -------------------------------------------------------
    def step(self, x: float, y: float, t: float) -> BacklashCommand:
        """Advance one frame given the current camera centroid ``(x, y)`` at time
        ``t`` (s). Returns the next :class:`BacklashCommand`."""
        if self._state in ("INIT", "CLEAR_NORTH"):
            return self._step_clear(x, y, t)
        if self._state == "STEP_NORTH":
            return self._step_north(x, y, t)
        if self._state == "STEP_SOUTH":
            return self._step_south(x, y, t)
        return BacklashCommand("done")

    def _begin_measurement(self, y: float, t: float, total_cleared: float) -> None:
        self._meas_ms = self._measurement_pulse_ms()
        pw = max(1, self._meas_ms)
        count = math.ceil(MAX_NORTH_PULSES_MS / pw)
        count = max(count, math.ceil(total_cleared * 1.5 / pw))
        self._north_count = int(min(MAX_MEASUREMENT_STEPS, max(4, count)))
        self._start_y = y
        self._msmt_start_t = t
        self._step = 0
        self._state = "STEP_NORTH"

    def _step_clear(self, x: float, y: float, t: float) -> BacklashCommand:
        if self._state == "INIT":
            self._marker = y
            self._start_y = y
            self._state = "CLEAR_NORTH"
            self._clear_step = 1
            if self._out_of_room(x, y):
                # No room even to clear — go straight to (an empty) measurement,
                # which compute() will report as TOO_FEW_NORTH.
                self._halted = True
                self._begin_measurement(y, t, 0.0)
                return self._step_north(x, y, t)
            return BacklashCommand("pulse", "north", self._clearing_ms)

        dec_delta = y - (self._marker if self._marker is not None else y)
        self._cum_clearing += dec_delta
        if abs(dec_delta) >= BACKLASH_EXPECTED_DISTANCE:
            sign = 1 if dec_delta >= 0 else -1
            if self._accepted == 0 or sign == self._last_delta_sign:
                self._accepted += 1
            else:
                self._accepted = 0
            self._last_delta_sign = sign
        self._marker = y

        if abs(self._cum_clearing) > BACKLASH_EXEMPTION_DISTANCE:
            self._exemption = True

        if (self._accepted >= BACKLASH_MIN_COUNT or self._exemption
                or self._out_of_room(x, y)):
            total_cleared = self._clear_step * self._clearing_ms
            if self._out_of_room(x, y):
                self._halted = True
            self._begin_measurement(y, t, float(total_cleared))
            return self._step_north(x, y, t)

        if self._clear_step >= MAX_CLEARING_STEPS:
            # Could not clear backlash — stop; compute() returns BL_NOT_CLEARED.
            self._state = "DONE"
            self._not_cleared = True
            return BacklashCommand("done")

        self._clear_step += 1
        return BacklashCommand("pulse", "north", self._clearing_ms)

    def _step_north(self, x: float, y: float, t: float) -> BacklashCommand:
        if self._step < self._north_count and not self._out_of_room(x, y):
            self._north_ys.append(y)
            self._step += 1
            return BacklashCommand("pulse", "north", self._meas_ms)
        # Done with the north run (count reached or ran out of room).
        self._north_ys.append(y)
        self._msmt_end_t = t
        if self._out_of_room(x, y) and self._step < self._north_count:
            self._halted = True
        # Truncate the south run to however many north steps we actually took.
        self._north_count = self._step
        self._step = 0
        self._state = "STEP_SOUTH"
        return self._step_south(x, y, t)

    def _step_south(self, x: float, y: float, t: float) -> BacklashCommand:
        if self._step < self._north_count:
            self._south_ys.append(y)
            self._step += 1
            return BacklashCommand("pulse", "south", self._meas_ms)
        self._south_ys.append(y)
        self._state = "DONE"
        return BacklashCommand("done")

    # -- estimate -------------------------------------------------------------
    def compute(self) -> BacklashResult:
        """Drift-corrected backlash estimate (dossier §10.3 ``ComputeBacklashPx``
        + ``GetBacklashSigma``). Pure — reads only the recorded N/S traces."""
        res = BacklashResult(y_rate_source=self.y_rate_source, halted=self._halted)
        if getattr(self, "_not_cleared", False):
            res.result_code = BL_NOT_CLEARED
            return res
        north = np.asarray(self._north_ys, dtype=float)
        if north.size <= 3:
            res.result_code = BL_TOO_FEW_NORTH
            return res

        # North per-step deltas (northStats): current.Y - previous.Y.
        north_deltas = np.diff(north)
        step_count = north_deltas.size
        north_delta = float(np.sum(north_deltas))       # signed total travel

        # Drift over the north run, de-trended out of the rate.
        t0 = self._msmt_start_t if self._msmt_start_t is not None else 0.0
        t1 = self._msmt_end_t if self._msmt_end_t is not None else t0
        drift_px = self.drift_per_sec_px * float(t1 - t0)
        pw = max(1, self._meas_ms)
        north_rate = abs((north_delta - drift_px) / (step_count * pw))
        res.north_rate = north_rate
        drift_per_frame = drift_px / step_count if step_count else 0.0

        expected = 0.9 * float(np.median(north_deltas))
        expected_mag = abs(expected)

        south = np.asarray(self._south_ys, dtype=float)
        good = 0
        early_south = 0.0
        last_south = 0.0
        bl_px = 0.0
        code = BL_TOO_FEW_SOUTH
        for i in range(1, south.size):
            south_move = float(south[i] - south[i - 1])
            early_south += south_move
            is_good = south_move < 0.0 and (
                abs(south_move) >= expected_mag
                or abs(south_move + last_south / 2.0) > expected_mag)
            if is_good:
                good += 1
                if good == 2:
                    bl_px = (i * expected_mag
                             - abs(early_south - i * drift_per_frame))
                    if north_rate > 0 and bl_px * north_rate < -200.0:
                        code = BL_SANITY
                    elif north_delta != 0 and bl_px >= 0.7 * abs(north_delta):
                        code = BL_TOO_FEW_NORTH
                    else:
                        code = BL_VALID
                    if bl_px < 0.0:
                        bl_px = 0.0
                    break
            elif good > 0:
                good -= 1
            last_south = south_move
        if good < 2:
            code = BL_TOO_FEW_SOUTH

        res.result_code = code
        res.bl_px = float(bl_px)
        res.bl_ms = int(bl_px / north_rate) if north_rate > 0 else 0
        res.bl_ms = max(0, min(BLC_SEED_MAX_MS, res.bl_ms))

        # Sigma: north sample-mean sigma + two south measurements in quadrature.
        if step_count >= 2 and north_rate > 0:
            var = float(np.var(north_deltas, ddof=1)) if step_count > 1 else 0.0
            sigma_px = math.sqrt(var / step_count + 2.0 * var / (step_count - 1))
            res.sigma_ms = sigma_px / north_rate
        return res


# ------------------------------------------------------------------- recommender
def _polar_verdict(drift_per_min_px: float, scale: float, known: bool) -> dict:
    """Plain-language polar-alignment band from the Dec drift (design §1.3).
    Advisory only — never applied."""
    if known and scale > 0:
        drift_am = abs(drift_per_min_px) * scale / 60.0  # arcsec/min -> arcmin/min
        drift_as_min = abs(drift_per_min_px) * scale     # arcsec/min
        if drift_as_min < 1.0:
            v, tone = "excellent", "good"
        elif drift_as_min < 3.0:
            v, tone = "good", "good"
        elif drift_as_min < 8.0:
            v, tone = "fair — a quick polar tweak would help", "warn"
        else:
            v, tone = "consider re-doing polar alignment", "bad"
        return {"verdict": v, "tone": tone,
                "drift_per_min_arcsec": round(drift_as_min, 2),
                "drift_per_min_arcmin": round(drift_am, 3)}
    # Scale unknown: express the raw pixel drift with generic wording.
    d = abs(drift_per_min_px)
    if d < 1.0:
        v, tone = "low drift", "good"
    elif d < 4.0:
        v, tone = "moderate drift", "warn"
    else:
        v, tone = "high drift — check polar alignment", "bad"
    return {"verdict": v, "tone": tone, "drift_per_min_arcsec": None,
            "drift_per_min_px": round(d, 2)}


def _smart_min_move(scale: float, known: bool) -> float:
    if known and scale > 0:
        return max(0.1515 + 0.1548 / scale, 0.15)
    return 0.2


def recommend(a: PhaseAResult, b: BacklashResult, current: dict) -> list[Recommendation]:
    """Apply the design §1.3 recommendation rules. Pure. ``current`` is the
    persisted ``GuideConfig`` as a dict (``ra_algorithm``, ``dec_algorithm``,
    ``ra_params``, ``dec_params``, ``blc_pulse_ms``). Conservative (D3): the
    recommended *defaults* are Hysteresis (RA) / ResistSwitch (Dec); PPEC and
    Lowpass2 appear only as ``advanced=True`` opt-in suggestions."""
    recs: list[Recommendation] = []
    ra_params = dict(current.get("ra_params") or {})
    dec_params = dict(current.get("dec_params") or {})
    scale = a.image_scale_arcsec
    known = a.image_scale_known

    # --- min-move (both axes) ---
    base_mm = _smart_min_move(scale, known)
    jitter = a.jitter_px
    if jitter >= base_mm:
        mm = round(min(base_mm + 0.5 * (jitter - base_mm), base_mm * 2.5), 2)
        mm_rationale = ("seeing / high-frequency jitter is large — loosened "
                        "min-move so guiding doesn't chase seeing")
        mm_conf = "medium"
    elif jitter <= 0.5 * base_mm:
        mm = round(max(0.15, base_mm * 0.85), 2)
        mm_rationale = "calm seeing — tightened min-move for finer correction"
        mm_conf = "high"
    else:
        mm = round(base_mm, 2)
        mm_rationale = ("min-move from the smart formula for your image scale"
                        if known else "default min-move (image scale unknown)")
        mm_conf = "high" if known else "low"
    cur_mm = ra_params.get("min_move")
    recs.append(Recommendation(
        key="min_move", field="min_move", current=cur_mm, recommended=mm,
        unit="px", rationale=mm_rationale, confidence=mm_conf))

    # --- RA aggression ---
    # Lower aggression when the RA error is dominated by seeing (over-correction
    # risk); keep the PHD2 default 0.7 otherwise.
    seeing_dominated = a.rms_ra_px > 0 and jitter >= 0.7 * a.rms_ra_px
    if seeing_dominated:
        agg, agg_r, agg_c = 0.5, ("RA error is mostly seeing — reduced aggression "
                                  "to avoid over-correcting noise"), "medium"
    else:
        agg, agg_r, agg_c = 0.7, "PHD2 default RA aggression", "high"
    recs.append(Recommendation(
        key="aggression", field="aggression", current=ra_params.get("aggression"),
        recommended=agg, unit="", rationale=agg_r, confidence=agg_c))

    # --- RA hysteresis ---
    recs.append(Recommendation(
        key="hysteresis", field="hysteresis",
        current=ra_params.get("hysteresis"), recommended=0.1, unit="",
        rationale="PHD2 default hysteresis", confidence="high"))

    # --- RA algorithm (default Hysteresis; PPEC advanced opt-in only) ---
    recs.append(Recommendation(
        key="ra_algorithm", field="ra_algorithm",
        current=current.get("ra_algorithm"), recommended="hysteresis", unit="",
        rationale="Hysteresis is the robust default for most mounts",
        confidence="high"))
    pe_clear = a.pe_period_s is not None and a.pe_amplitude_px >= max(0.5, base_mm)
    if pe_clear:
        recs.append(Recommendation(
            key="ra_algorithm_ppec", field="ra_algorithm", advanced=True,
            current=current.get("ra_algorithm"), recommended="ppec", unit="",
            rationale=(f"a clear periodic error (~{a.pe_period_s:.0f}s, "
                       f"±{a.pe_amplitude_px:.2f}px) was detected — Predictive "
                       f"PEC (RA-only) can cancel it"),
            confidence="medium"))

    # --- Dec algorithm (default ResistSwitch; Lowpass2 advanced opt-in only) ---
    recs.append(Recommendation(
        key="dec_algorithm", field="dec_algorithm",
        current=current.get("dec_algorithm"), recommended="resist_switch",
        unit="", rationale="Resist Switch is robust to Dec backlash",
        confidence="high"))
    low_backlash = b.result_code in (BL_VALID, BL_TOO_FEW_NORTH) and b.bl_ms < 50
    low_drift = (known and scale > 0
                 and abs(a.drift_per_min_px) * scale < 1.0)
    if low_backlash and low_drift:
        recs.append(Recommendation(
            key="dec_algorithm_lowpass2", field="dec_algorithm", advanced=True,
            current=current.get("dec_algorithm"), recommended="lowpass2",
            unit="", rationale=("very low Dec backlash and drift — Lowpass2 can "
                                "give smoother Dec correction"),
            confidence="low"))

    # --- Dec backlash seed ---
    if b.result_code in (BL_VALID, BL_TOO_FEW_NORTH):
        seed = int(max(0, min(BLC_SEED_MAX_MS, b.bl_ms)))
        if seed <= 0:
            bl_r = ("measured Dec backlash is negligible — leaving backlash "
                    "compensation off")
            bl_c = "high" if b.result_code == BL_VALID else "low"
        else:
            bl_r = (f"measured Dec backlash of ~{seed} ms "
                    f"(±{b.sigma_ms:.0f} ms) — seed the static compensation pulse")
            bl_c = "high" if b.result_code == BL_VALID else "low"
    else:
        seed = 0
        bl_r = ("couldn't measure Dec backlash reliably "
                f"({b.result_code}) — leaving it off")
        bl_c = "low"
    recs.append(Recommendation(
        key="blc_pulse_ms", field="blc_pulse_ms",
        current=current.get("blc_pulse_ms", 0), recommended=seed, unit="ms",
        rationale=bl_r, confidence=bl_c))

    return recs


# ------------------------------------------------------------------- wire shape
def report_dict(a: PhaseAResult, b: BacklashResult, recs: list[Recommendation],
                current: dict, samples: list[tuple[float, float, float]]) -> dict:
    """Assemble the JSON wire shape returned by ``GET /api/guide/assistant/report``
    (design §3.1 ``report_dict``). Includes a de-meaned sample series (px, keyed
    ``t/ra/dec``) so the advanced view can feed it to ``GuideScatter``/``GuideGraph``
    unchanged."""
    scale = a.image_scale_arcsec
    known = a.image_scale_known

    def _as(px: float) -> float | None:
        return round(px * scale, 3) if (known and scale > 0) else None

    # De-mean the sample cloud so the scatter is centred on the lock point.
    ser: list[dict] = []
    if samples:
        arr = np.asarray(samples, dtype=float)
        x0 = float(np.mean(arr[:, 1]))
        y0 = float(np.mean(arr[:, 2]))
        for t, x, y in samples:
            ser.append({"t": round(float(t), 3),
                        "ra": round(float(x) - x0, 3),
                        "dec": round(float(y) - y0, 3)})

    return {
        "measurements": {
            "n": a.n,
            "rms_ra_px": round(a.rms_ra_px, 3),
            "rms_dec_px": round(a.rms_dec_px, 3),
            "rms_total_px": round(a.rms_total_px, 3),
            "rms_ra_arcsec": _as(a.rms_ra_px),
            "rms_dec_arcsec": _as(a.rms_dec_px),
            "rms_total_arcsec": _as(a.rms_total_px),
            "drift_per_min_px": round(a.drift_per_min_px, 3),
            "drift_per_min_arcsec": _as(a.drift_per_min_px),
            "pe_amplitude_px": round(a.pe_amplitude_px, 3),
            "pe_period_s": a.pe_period_s,
            "jitter_px": round(a.jitter_px, 3),
            "image_scale_arcsec": round(scale, 4),
            "image_scale_known": known,
            "backlash": {
                "bl_px": round(b.bl_px, 3),
                "bl_ms": b.bl_ms,
                "sigma_ms": round(b.sigma_ms, 1),
                "north_rate": round(b.north_rate, 5),
                "result_code": b.result_code,
                "y_rate_source": b.y_rate_source,
                "halted": b.halted,
            },
        },
        "recommendations": [r.to_dict() for r in recs],
        "current": {
            "ra_algorithm": current.get("ra_algorithm"),
            "dec_algorithm": current.get("dec_algorithm"),
            "dec_guide_mode": current.get("dec_guide_mode", "auto"),
            "blc_pulse_ms": current.get("blc_pulse_ms", 0),
            "ra_params": dict(current.get("ra_params") or {}),
            "dec_params": dict(current.get("dec_params") or {}),
        },
        "polar": _polar_verdict(a.drift_per_min_px, scale, known),
        "samples": ser,
    }
