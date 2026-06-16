"""Sequence plan data model (pydantic, shared with the REST API)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ExposureStep(BaseModel):
    filter: str | None = None          # filter name, None = don't move wheel
    exposure_s: float = Field(gt=0, le=3600)
    gain: int = 100
    offset: int = 30
    binning: int = 1
    count: int = Field(gt=0, le=10000)
    frame_type: str = "Light"          # Light | Dark | Bias | Flat


class Target(BaseModel):
    name: str
    ra_hours: float = Field(ge=0, lt=24)
    dec_deg: float = Field(ge=-90, le=90)
    center: bool = True                # plate-solve centering after slew
    autofocus_first: bool = True
    calibration: bool = False          # darks/bias/flats — skip slew/center/AF/guide
    steps: list[ExposureStep] = []
    # --- atlas (additive; both nullable — existing plans deserialize unchanged) ---
    rotation_deg: float | None = None  # target camera angle (PA) — guidance only, no rotator
    mosaic_group: str | None = None    # groups mosaic panels in the Plan UI


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
    # wind-down
    park_when_done: bool = False
    warm_cooler_when_done: bool = False

    def total_frames(self) -> int:
        return sum(s.count for t in self.targets for s in t.steps)

    def total_seconds(self) -> float:
        return sum(s.count * s.exposure_s for t in self.targets for s in t.steps)
