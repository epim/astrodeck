"""Sequence plan data model (pydantic, shared with the REST API)."""
from __future__ import annotations

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
