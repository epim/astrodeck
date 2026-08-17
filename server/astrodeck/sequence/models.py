"""Sequence plan data model (pydantic, shared with the REST API)."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

if TYPE_CHECKING:                       # pragma: no cover - typing only
    from .policy import RunPolicy


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
    # --- interleaved acquisition (additive; ignored unless the TARGET asks for
    # `acquisition="cycle"`, so every existing plan is unchanged) -------------
    #
    # How many frames to take on each VISIT to this step while cycling. The
    # night keeps returning until `count` is reached, so this is the width of
    # one pass, not a total: count=45, per_visit=1 means "one L every time
    # round, forty-five times".
    per_visit: int = Field(1, gt=0, le=1000)


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
    # What to do when a RUNNING target sinks back below `min_altitude_deg`.
    #
    # "keep" is the default and it is the behaviour every plan has always had:
    # the number above gated SELECTION only, so a target chosen at 40 deg went
    # on being imaged down through the floor and into the trees. Defaulting to
    # "advance" would silently change what every saved plan does at night, and a
    # target dropped mid-run is not a change to make on the operator's behalf.
    #
    # "advance" is a POOL's `onFloor` dial: set the target aside for TONIGHT -
    # skipped, not marked done, so the ledger brings it back tomorrow - and let
    # the next member have the sky. It is also what fires `on_altitude_floor`.
    on_floor: Literal["keep", "advance"] = "keep"
    # --- pro visibility constraints (PRO-14; additive, 0 = off => back-compat) ---
    min_moon_sep_deg: float = Field(0.0, ge=0, le=180)    # ≥ this from the Moon while up
    max_moon_illum_pct: float = Field(0.0, ge=0, le=100)  # skip while Moon > this % lit
    max_hour_angle_h: float = Field(0.0, ge=0, le=12)     # image within ±this h of meridian
    stop_mode: str = "none"            # none | dawn | time
    stop_offset_min: int = 0
    stop_time: str | None = None
    max_run_min: int = 0               # 0 = no cap
    # Literal, not str: the scheduler branches on this (a target whose start
    # window opened long ago is dropped under "skip"), so a typo must 422 at the
    # plan route rather than silently reading as "wait". Default wait — C1-25.
    on_missed: Literal["wait", "skip"] = "wait"


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
    # --- acquisition order (additive; "blocks" is what every existing plan does)
    #
    # "blocks" runs each step to completion before the next: 45 L, then 45 R,
    # then 45 G. Simple, and the way this engine has always worked.
    #
    # "cycle" round-robins: one visit to each step in turn, `per_visit` frames
    # deep, until every step has its `count`. L R G B S Ha O3, forty-five times.
    # Two reasons an imager wants it, and neither is cosmetic: every filter then
    # samples the SAME sky — the same seeing, the same transparency, the same
    # altitude — so the channels combine without one of them carrying the hour
    # the sky went soft; and a night cut short at 60% yields 60% of every
    # channel instead of three finished filters and four empty ones.
    acquisition: Literal["blocks", "cycle"] = "blocks"
    # --- atlas (additive; both nullable — existing plans deserialize unchanged) ---
    rotation_deg: float | None = None  # target camera angle (PA) — enforced when a rotator is connected; guidance otherwise
    mosaic_group: str | None = None    # groups mosaic panels in the Plan UI
    # --- autorun scheduling (Batch 4b; additive — default = run-now) ---
    schedule: Schedule = Field(default_factory=Schedule)


_HHMM_RE = re.compile(r"^(\d{2}):(\d{2})$")

TriggerKind = Literal[
    "on_hfr_above", "on_guide_rms_above", "on_frame_rejected",
    "on_target_complete", "at_time",
    # --- sky / rig conditions (ADDITIVE; a plan without them is unchanged) ---
    # Each is a LEVEL, edge-triggered by the evaluator, and each is fed from a
    # TRI-STATE reading where None means "unreadable" and fires nothing. See
    # instructions.TriggerContext for why that distinction is load-bearing.
    "on_clouds_in", "on_clouds_clear", "on_unsafe", "on_panel_ready",
    # The active target sank back below its own `min_altitude_deg`. NOT a level
    # like the four above: it fires once, at the frame boundary that detects it,
    # and the target leaves the night's rotation in the same breath - so there
    # is nothing left to re-arm against.
    #
    # Added only once `SequenceEngine._enforce_altitude_floor` could raise it.
    # `to_plan.LEGAL_TRIGGERS` reads this tuple directly, so a member listed
    # here that the engine cannot produce turns an honest "this rule will not
    # run" into silence - which is worse than the warning it replaces.
    "on_altitude_floor",
]
ActionKind = Literal[
    "notify", "pause", "refocus", "dither", "abort",
    # --- control-flow expansion (ADDITIVE): target jumps. Both need target_arg.
    "run_target", "skip_target",
    # --- the self-releasing weather hold (ADDITIVE) -----------------------
    # DELIBERATELY NOT one half of a pause/resume pair, and the reason is
    # structural rather than stylistic.
    #
    # `pause()` blocks the frame loop at `await self._checkpoint()`, which is the
    # FIRST line of that loop - above _enforce_stop_boundary (the dawn/stop
    # window), above _safety_gate, and above _frame_alerts_tick (the dead-man
    # ping). A rule-fired pause therefore disarms the dawn stop, the safety gate
    # and the watchdog heartbeat together, and a run held for weather would sit
    # through sunrise with nothing left to end it and nothing feeding the
    # deadman.
    #
    # And a `resume` RULE could never release it anyway: rules are evaluated at
    # frame boundaries, and a paused loop has none. The run would wait forever
    # for a rule that cannot fire.
    #
    # So the hold owns its own release, exactly as _park_hold_pause already does
    # for the meridian: it polls the sky itself, keeps the boundaries armed, and
    # comes back through the target's own setup.
    "hold_for_clear",
]

# Leaf predicate vocabulary for the bounded compound grammar. Deliberately the
# SAME closed vocabulary as TriggerKind (minus the "on_" prefix) — a compound is
# a composition of the existing bounded predicate set, never a scripting runtime.
PredicateKind = Literal[
    "hfr_above", "guide_rms_above", "frame_rejected", "target_complete", "at_time",
    "clouds_in", "clouds_clear", "unsafe", "panel_ready",
]


def _bad_hhmm(value: str | None) -> bool:
    m = _HHMM_RE.match(value or "")
    return not m or int(m.group(1)) >= 24 or int(m.group(2)) >= 60


class Predicate(BaseModel):
    """One leaf term of a compound condition. Mirrors a flat trigger's inputs."""
    kind: PredicateKind
    threshold: float = Field(0.0, ge=0)   # hfr_above / guide_rms_above value
    at_time: str | None = None            # "HH:MM" 24h local; required when kind==at_time

    @model_validator(mode="after")
    def _validate_at_time(self) -> "Predicate":
        if self.kind == "at_time" and _bad_hhmm(self.at_time):
            raise ValueError(
                "predicate 'at_time' requires at_time in 'HH:MM' 24h form")
        return self


class Condition(BaseModel):
    """A bounded, ONE-LEVEL compound condition: an ``all``(AND) / ``any``(OR)
    over 2..8 leaf predicates. No nesting in v1 — ``terms`` holds leaves only,
    so the grammar stays a closed vocabulary rather than an expression tree."""
    op: Literal["all", "any"]
    terms: list[Predicate] = Field(min_length=2, max_length=8)


class Instruction(BaseModel):
    """A single additive when-trigger-do-action rule (PRO-3, conditional
    sequencer v1). Flat closed-enum shape (mirrors the additive Schedule/plan
    fields, not a discriminated union, so pydantic + the TS type mirror
    trivially). A plan with no ``instructions`` runs BYTE-IDENTICAL to today.

    Triggers: ``on_hfr_above``/``on_guide_rms_above`` are edge-triggered against
    ``threshold``; ``on_frame_rejected``/``on_target_complete`` are per-event;
    ``at_time`` fires once at the first frame boundary at/after ``at_time``.
    Actions each map 1:1 to an EXISTING engine capability (no new teardown).

    Control-flow expansion (additive): ``when`` carries an optional bounded
    1-level AND/OR compound that OVERRIDES ``trigger`` when set, and
    ``run_target``/``skip_target`` actions carry their destination in
    ``target_arg``. Both default to None => the PRO-3 path, unchanged."""
    # stable identity (like Target/ExposureStep); backfilled on validation so an
    # id-less client payload is never rejected.
    id: str = Field(default_factory=lambda: uuid4().hex)
    enabled: bool = True
    trigger: TriggerKind
    threshold: float = Field(0.0, ge=0)     # on_hfr_above / on_guide_rms_above value
    at_time: str | None = None              # "HH:MM" 24h local; required when trigger==at_time
    action: ActionKind
    message: str = ""                       # notify text / log + abort reason
    level: Literal["info", "warning", "error"] = "warning"   # notify severity
    once: bool = False                      # fire at most once per run
    cooldown_s: float = Field(0.0, ge=0)    # min seconds between fires (0 = every boundary)
    only_target: str | None = None          # gate: only while this target (by name) active
    # --- control-flow expansion (ADDITIVE; both default to the PRO-3 path) ----
    # Jump DESTINATION (target name) for run_target / skip_target. Deliberately
    # NOT `only_target` (that is a GATE, not a destination) and not `message`
    # (notify text / abort reason) — overloading either is a footgun.
    target_arg: str | None = None
    # Optional bounded compound condition. None => the flat trigger path runs
    # exactly as today. When set it OVERRIDES `trigger` (one validated active
    # path per rule); `trigger` stays in the schema with its value to avoid
    # serialization churn.
    when: Condition | None = None

    @model_validator(mode="after")
    def _validate_at_time(self) -> "Instruction":
        # flat at_time trigger only matters on the flat path (when is None).
        if self.when is None and self.trigger == "at_time" and _bad_hhmm(self.at_time):
            raise ValueError(
                "trigger 'at_time' requires at_time in 'HH:MM' 24h form")
        if self.action in ("run_target", "skip_target") and not (
                self.target_arg or "").strip():
            raise ValueError(
                f"action '{self.action}' requires target_arg (a target name)")
        return self


class SequencePlan(BaseModel):
    name: str = "Tonight"
    targets: list[Target] = []
    guide: bool = True
    dither_every: int = 3              # frames; 0 = never
    # --- #239 stage A: `None` = inherit the rig's standard (config), which is
    # what a flow-compiled plan leaves them at. An explicit value - INCLUDING 0
    # or False - is a choice about tonight and beats the rig. See
    # sequence/policy.py; nothing reads these directly any more.
    dither_pixels: float | None = None
    autofocus_every: int = 0           # frames; 0 = only at target start
    # NB autofocus_every and dither_every stay per-night intent (stage C).
    # cooling
    cool_to: float | None = None       # target sensor °C; cool + stabilize before lights
    cool_timeout_s: int | None = None
    # focus
    apply_filter_offsets: bool | None = None
    refocus_on_temp_delta_c: float | None = None
    # safety
    meridian_flip: bool = True         # flip a German mount when past the meridian
    recover_guiding: bool | None = None
    hfr_reject_factor: float | None = None
    # --- multi-night quota mode (sessions spec §3; defaults preserve behavior) ---
    # Literal (Task 4 review, MINOR): a typo used to silently degrade to
    # "attempts" mode (the else-branch of every `count_mode == "accepted"`
    # check) with no feedback. Now rejected at validation.
    count_mode: Literal["attempts", "accepted"] = "attempts"
    min_stars: int | None = None
    max_guide_rms: float | None = None
    max_eccentricity: float | None = None
    max_consecutive_rejects: int | None = None
    max_consecutive_rejects_night: int | None = None
    # unattended safety (Batch 4b; global safety/escalation live in config.py —
    # the plan carries only a master toggle + the meridian-flip warning lead time)
    safety_check: bool = True          # honor the configured SafetyMonitor + floor
    meridian_flip_warn_min: float | None = None
    # wind-down
    park_when_done: bool = False
    warm_cooler_when_done: bool = False
    # --- cloud-hold calibration (ADDITIVE; default off = every existing plan
    # behaves byte-identically) ---------------------------------------------
    #
    # A weather hold is dead time with a cooled sensor and a closed sky, which
    # is exactly the condition darks want. When this is on, the hold spends its
    # waiting shooting darks that MATCH TONIGHT'S LIGHTS - same exposure, gain,
    # offset and binning as the step it interrupted - so the library grows in
    # the one dimension the night actually needs.
    #
    # An int, not a bool, because "how many" is the whole decision: it bounds
    # what a hold can spend and makes the intent visible in the plan the PLAN
    # tab renders. 0 means do not.
    cloud_hold_darks: int = Field(0, ge=0, le=200)
    # DARKS AFTER THE NIGHT, matched to the LIGHTS this plan actually shot.
    #
    # The sibling above spends a cloud hold; this one spends the dead time after
    # the run ends, in the window between the park and the warm ramp - the only
    # moment when the mount is stowed, the cover is shut and the sensor is still
    # at setpoint. A dark is indexed by temperature, so shooting it after the
    # ramp has started produces cover for nothing.
    #
    # 0 means do not, and 0 is the default: a flow that did not ask for day
    # darks must not find its camera held cold for an extra hour.
    day_darks: int = Field(0, ge=0, le=200)
    # --- conditional sequencer (PRO-3; ADDITIVE — [] => byte-identical run) ---
    # An author-editable when-trigger-do-action layer on top of the fixed
    # targets×steps plan. Empty by default so existing plans deserialize
    # unchanged and the engine's eval path is a guarded no-op.
    instructions: list[Instruction] = []

    def total_frames(self) -> int:
        return sum(s.count for t in self.targets for s in t.steps)

    def total_seconds(self) -> float:
        return sum(s.count * s.exposure_s for t in self.targets for s in t.steps)

    def total_lights(self) -> int:
        """Light frames only — excludes calibration targets (darks/bias/flats)."""
        return sum(s.count for t in self.targets for s in t.steps if not t.calibration)

    def light_seconds(self) -> float:
        """INTEGRATION — exposure summed over LIGHT steps only (UX #39).

        ``total_seconds`` counts every step's shutter time, calibration included,
        so adding a Flat 120s x10 to a plan moved the header from "5h 0m" to
        "5h 20m" of "integration". Twenty minutes of flat panel is not twenty
        minutes on the target; the finished stack's SNR does not move. The report
        already scores it this way (``report._Totals.add`` adds integration for
        accepted LIGHT frames only) — this is the plan-side twin of that rule."""
        return sum(s.count * s.exposure_s
                   for t in self.targets if not t.calibration
                   for s in t.steps
                   if (s.frame_type or "Light").strip().lower() == "light")


def quota_unbounded(plan: SequencePlan, policy: "RunPolicy") -> bool:
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

    THE GUARDS MOVED (#239 stage A): both reject guards are now resolved from
    the rig's standards unless this plan overrides them, so the question cannot
    be answered from the plan alone - hence ``policy``. A caller that passed only
    the plan would read a bare ``None`` as "no guard" and refuse every accepted-
    quota run on a rig whose standards set one.

    Calibration targets never enter the quota loop (``quota = count_mode ==
    "accepted" and not target.calibration`` in ``_run_step``), so they're
    excluded here; a plan with no non-calibration targets is never unbounded
    by this rule."""
    if plan.count_mode != "accepted":
        return False
    if policy.max_consecutive_rejects or policy.max_consecutive_rejects_night:
        return False
    return any(t.schedule.stop_mode == "none" and not t.schedule.max_run_min
               for t in plan.targets if not t.calibration)
