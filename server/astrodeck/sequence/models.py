"""Sequence plan data model (pydantic, shared with the REST API)."""
from __future__ import annotations

from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class ExposureStep(BaseModel):
    # stable identity for multi-night session ledgers (sessions spec §1).
    # Backfilled on every validation so an id-less legacy plan is never rejected.
    id: str = Field(default_factory=lambda: uuid4().hex)
    filter: str | None = None          # filter name, None = don't move wheel
    exposure_s: float = Field(gt=0, le=3600)
    gain: int = 100
    offset: int = 30
    binning: int = 1
    count: int = Field(gt=0, le=10000)
    frame_type: str = "Light"          # Light | Dark | Bias | Flat
    # --- PRO-5 flat auto-exposure (additive; 0/None = off => back-compat) ---
    adu_target: int = Field(0, ge=0, le=65535)     # >0 + Flat ⇒ solve exposure to this ADU
    panel_brightness: int | None = Field(None, ge=0)  # flat-panel level while shooting; None = don't touch


class Schedule(BaseModel):
    """Per-target autorun window. All defaults preserve current behavior (run now,
    no altitude gate, no stop), so existing saved plans deserialize unchanged.

    Two distinct altitude concepts (resolves C1-28): ``min_altitude_deg`` here is
    the per-target START gate checked against the *target* alt; the global
    pier-collision floor lives in ``SafetyConfig.min_alt_deg`` (checked against
    *mount* alt). Structured controls only — no token mini-language (C1-24)."""
    start_mode: str = "now"            # now | dusk | dawn | time
    start_offset_min: int = 0          # ± minutes relative to dusk/dawn
    start_time: str | None = None      # "HH:MM" when start_mode == "time"
    min_altitude_deg: float = 0.0      # per-target START gate (target-alt). 0 = none
    # --- pro visibility constraints (PRO-14; additive, 0 = off => back-compat) ---
    min_moon_sep_deg: float = Field(0.0, ge=0, le=180)    # ≥ this from the Moon while up
    max_moon_illum_pct: float = Field(0.0, ge=0, le=100)  # skip while Moon > this % lit
    max_hour_angle_h: float = Field(0.0, ge=0, le=12)     # image within ±this h of meridian
    stop_mode: str = "none"            # none | dawn | time
    stop_offset_min: int = 0
    stop_time: str | None = None
    max_run_min: int = 0               # 0 = no cap
    on_missed: str = "wait"            # wait | skip  (default wait — C1-25)


class Target(BaseModel):
    # stable identity for multi-night session ledgers (sessions spec §1).
    id: str = Field(default_factory=lambda: uuid4().hex)
    name: str
    ra_hours: float = Field(ge=0, lt=24)
    dec_deg: float = Field(ge=-90, le=90)
    center: bool = True                # plate-solve centering after slew
    autofocus_first: bool = True
    calibration: bool = False          # darks/bias/flats — skip slew/center/AF/guide
    steps: list[ExposureStep] = []
    # --- atlas (additive; both nullable — existing plans deserialize unchanged) ---
    rotation_deg: float | None = None  # target camera angle (PA) — enforced when a rotator is connected; guidance otherwise
    mosaic_group: str | None = None    # groups mosaic panels in the Plan UI
    # --- autorun scheduling (Batch 4b; additive — default = run-now) ---
    schedule: Schedule = Field(default_factory=Schedule)


class SequencePlan(BaseModel):
    name: str = "Tonight"
    targets: list[Target] = []
    guide: bool = True
    dither_every: int = 3              # frames; 0 = never
    dither_pixels: float = 3.0
    autofocus_every: int = 0           # frames; 0 = only at target start
    # cooling
    cool_to: float | None = None       # target sensor °C; cool + stabilize before lights
    cool_timeout_s: int = 600
    # focus
    apply_filter_offsets: bool = True  # shift focuser by per-filter offset on filter change
    refocus_on_temp_delta_c: float = 0.0   # refocus when focuser temp drifts this much (0 = off)
    # safety
    meridian_flip: bool = True         # flip a German mount when past the meridian
    recover_guiding: bool = True       # restart guiding if the star is lost
    hfr_reject_factor: float = 0.0     # warn when a frame's HFR exceeds factor × running median (0 = off)
    # --- multi-night quota mode (sessions spec §3; defaults preserve behavior) ---
    # Literal (Task 4 review, MINOR): a typo used to silently degrade to
    # "attempts" mode (the else-branch of every `count_mode == "accepted"`
    # check) with no feedback. Now rejected at validation.
    count_mode: Literal["attempts", "accepted"] = "attempts"
    min_stars: int = 0                       # star-count floor (0 = off)
    max_guide_rms: float = 0.0               # guide-RMS ceiling, arcsec (0 = off)
    max_eccentricity: float = 0.0            # per-frame median-ecc ceiling, 0..1 (0 = off)
    max_consecutive_rejects: int = 10        # per-STEP consecutive guard (0 = off)
    max_consecutive_rejects_night: int = 20  # per-NIGHT guard, crosses targets (0 = off)
    # unattended safety (Batch 4b; global safety/escalation live in config.py —
    # the plan carries only a master toggle + the meridian-flip warning lead time)
    safety_check: bool = True          # honor the configured SafetyMonitor + floor
    meridian_flip_warn_min: float = 15.0
    # wind-down
    park_when_done: bool = False
    warm_cooler_when_done: bool = False

    def total_frames(self) -> int:
        return sum(s.count for t in self.targets for s in t.steps)

    def total_seconds(self) -> float:
        return sum(s.count * s.exposure_s for t in self.targets for s in t.steps)

    def total_lights(self) -> int:
        """Light frames only — excludes calibration targets (darks/bias/flats)."""
        return sum(s.count for t in self.targets for s in t.steps if not t.calibration)


def quota_unbounded(plan: SequencePlan) -> bool:
    """True when starting ``plan`` in accepted-frame quota mode could run
    forever under persistent rejects (Task 4 review, IMPORTANT; public name —
    imported across module boundaries by the api route gates).

    ``engine._run_step``'s accepted-mode loop (spec §3) terminates a step only
    via: an accepted frame reaching ``step.count``, the per-step
    ``max_consecutive_rejects`` guard, the per-night
    ``max_consecutive_rejects_night`` guard (``NightQualityStop``), or a frozen
    stop boundary (``_enforce_stop_boundary`` -> ``StopTarget``, from
    ``schedule.stop_mode``/``max_run_min``). The no-progress watchdog only
    WARNs — it never raises. So when BOTH reject guards are disabled (0) AND
    ANY non-calibration target carries no stop boundary (fix round 2: one
    bounded target does not save a plan whose other target's step loop is
    boundary-less — that loop is just as unbounded on its own), a step whose
    quota can never be satisfied (e.g. persistent clouds) loops without any
    terminating bound.

    Calibration targets never enter the quota loop (``quota = count_mode ==
    "accepted" and not target.calibration`` in ``_run_step``), so they're
    excluded here; a plan with no non-calibration targets is never unbounded
    by this rule."""
    if plan.count_mode != "accepted":
        return False
    if plan.max_consecutive_rejects or plan.max_consecutive_rejects_night:
        return False
    return any(t.schedule.stop_mode == "none" and not t.schedule.max_run_min
               for t in plan.targets if not t.calibration)
