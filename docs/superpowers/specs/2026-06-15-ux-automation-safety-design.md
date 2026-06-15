# AstroDeck Surface Spec — Unattended Safety, Autorun Scheduling & Session Report

**Date:** 2026-06-15
**Surface owner:** lead designer
**Status:** build-ready (revised after 3 adversarial UX critiques)
**Targets P0/P1s:** unattended safety (no weather/cloud abort, no horizon/pier limit), autorun scheduling (start-at-dusk / min-alt / dawn cutoff), end-of-night session report, push/ntfy alerting, and the dependent Site-config P0 (lat/lon hardcoded to SF in `hub.py`).

---

## 0. What changed vs. the draft (critique resolutions)

The draft was architecturally sound but (a) exposed ~30 raw expert knobs to a beginner, (b) had several engine-correctness bugs, and (c) asserted a11y/touch claims the actual tokens/components contradict. This spec resolves every **valid** critique. The philosophy is inverted: **ship safe gentle defaults behind named presets; hide every debounce/escalation knob behind "Advanced"; fix the fail-open and target-coords bugs; stop scattering status indicators.**

### Accepted and fixed

| # | Critique | Resolution (section) |
|---|----------|----------------------|
| C1-1, C1-2, C1-3 | 30 raw knobs, doubled Automation panel, 2 new nav items | **Safety/escalation presets** (`Backyard`/`Remote`/`Custom`); all numerics behind **Advanced**; plan shows one read-only line "Safety: Backyard — edit in Settings"; Report removed from primary nav (run-complete link + overflow only). §2.3, §2.4, §2.6 |
| C1-4, C1-26, C2-13 | min-alt 20° + 422 blocks low targets; "your floor"; astronomical twilight too late | Floor default **off until a SafetyMonitor or custom horizon is set**; when on, default **10°**; pre-flight is a *warning* defaulting to "Run anyway"; copy never says "your"; twilight default **nautical −12°** with clock time shown. §1.4, §1.6, §2.4 |
| C1-5, C1-6, C2-2, C2-6 | `abort_park_warm`+`unsafe_consecutive=1` ends night on one cloud; pause strands forever; pause keeps tracking | `Backyard` preset = **pause + auto-resume**, `unsafe_consecutive=3`; `resume_when_safe` forced **on** when action is pause; pause **stops tracking / park-holds**, max-pause timeout → escalate; resume re-checks horizon+pier. §1.4, §1.9-A |
| C1-7 | `retake` fights clouds | `retake` is **Advanced-only**, never a plain peer; default `warn`; retake capped **per target** not just per frame; cost explained inline. §1.5, §1.9-D |
| C1-8, C2-1, C2-5 | `SafetyAbort` vs `task.cancel()`; wind-down interruptible; resume desync | Single shutdown path; wind-down **shielded** (`asyncio.shield`) so park/warm completes; explicit except ordering; reporter + schedule cursor persisted in resume JSON and re-attached. §1.9-G, §1.7, §1.9-C |
| C1-10, C2-1(b), C2-3 | horizon gates target catalog coords not mount pointing; no mid-exposure / mid-slew check; az-wrap broken | Horizon checked against **mount-reported alt/az** for the pier-collision case; target-alt gate is a **schedule** decision; pre-slew projects alt forward by slew+solve margin; horizon array is **sorted (az,alt) control points with wrap interpolation**. §1.6, §1.9-A |
| C1-11, C2-14 | pier-limit soft-warning is theater on mounts without `DestinationSideOfPier` | Pier-side toggle **gated on capability**; real guard is the **mount-alt floor before every slew** plus an optional **no-go alt/az box**; UI states the dependency honestly. §1.9-A, §2.3 |
| C1-12, C2 | per-2s `is_safe()` blocks status loop | Safety polled on its **own task** with `asyncio.wait_for` timeout; a timed-out read is **stale, not safe**. §1.9-F, §1.11 |
| C1-13, C3-7e | 5 competing header indicators | **One consolidated status region**: a single dominant chip (`UNSAFE — parking`) with everything else subordinate; WS-stale greys **all** confidence indicators. §2.6 |
| C1-14, C3-1a, C3-1d | grey = both "no monitor" and "stale"; green/red same hue at night | Safety state driven by **shape + text label**, not hue: filled disc=SAFE, hollow ring+triangle=UNSAFE, dashed ring=STALE, outline+"no device"=none. §2.6, §3 |
| C1-15, C2 | safety monitor drop mid-run fails open silently | **Fail-safe/closed**: a disconnected/timed-out monitor during a run that requested safety triggers the unsafe path (or a loud persistent alert if action=warn). §1.9-A, §1.9-F |
| C1-16, C2-10 | "Test" proves POST not delivery; offline rig | Test does a **real round-trip** where possible; ntfy is the recommended primary; **persistent "Alerts not verified" banner** until a test passes; **undelivered-alert queue with retry**. §1.8, §2.3 |
| C1-17 | SMTP password in plaintext JSON is a beginner trap | **SMTP dropped from v1**; channels are ntfy / webhook / Telegram-bot-token; no credential collection; config file documents no secrets-at-rest beyond a bot token (noted). §1.8, §2.3 |
| C1-18, C2-9 | dedupe suppresses the alert that matters | **Never dedupe state-change alerts** (started/abort/park/complete/unsafe↔safe transitions); dedupe only repetitive warnings. §1.8 |
| C1-19, C1-20, C2-12 | report over-built; full per-frame log; LIVE duplicates Sequence | v1 report = **summary header + per-target/per-filter table + 3 separate sparklines**, leading with **per-filter integration**. Trends **derived from frames at read time** (no duplicate arrays), frames list **downsampled/append-only**, CSV is a power-user export. **No LIVE report** — Sequence/Monitor is the only live surface; Report is strictly post-hoc. §1.7, §2.5 |
| C1-21, C3-3b | HoldButton 800ms fails with gloves; no keyboard path; conflates "cancel plan" with "stop motors" | HoldButton is for **Abort sequence** (deliberate) only; **emergency mount STOP stays a single tap**; HoldButton has a keyboard/SR confirm-dialog fallback. §3, §2.4 |
| C1-22 | critical info behind 2-tap "More" sheet | Safety LED tap **deep-links straight to Settings→Safety** bypassing the sheet; run-stop reason is always in the header + run-complete panel, never only in overflow. §2.6 |
| C1-23, C2-7 | per-target windows but strictly-sequential engine stalls the queue | Engine **skips ahead to the first ready target** instead of blocking on a waiting head; targets **sorted by window** at run start; plan-time conflict warning. §1.6, §1.9-C |
| C1-24 | `dusk-30` token mini-language vs segmented UI mismatch | Structured controls only: a **Dusk/Dawn/Time** selector + a separate **± minutes** stepper; no free-text token a user must format. §1.5, §2.4 |
| C1-25 | `skip_if_missed=True` silently drops the one target | Default **wait/run-anyway** for the common single-target case; skip only when the user opts a multi-target queue into it. §1.5 |
| C1-27 | `loadPlan()` doesn't backfill per-target `schedule` → crash | `loadPlan()` and `addTarget()` **backfill `schedule: defaultSchedule()`** for every target. §2.4 |
| C1-28 | `min_altitude_deg` lives in 3 places | Collapsed to **two clearly-scoped concepts**: `SafetyConfig.min_alt_deg` (global pier-collision floor, mount-alt) and `Schedule.min_altitude_deg` (per-target *start gate*, target-alt). Plan-level override removed. §1.4, §1.5 |
| C2-3 | floor checked only at boundary; 600s sub dips below | Effective floor = `max(min_alt_deg, interp(horizon, az))`; pre-slew check **projects to slew+solve+exposure end**; documented limitation: no in-exposure interrupt (frame-boundary only). §1.6 |
| C2-4 | linear cooling ETA is worse than none | Cooling ETA **dropped**; show honest `−3.2°C → −10°C` like today (optional exponential fit deferred). §1.9-E |
| C2-8 | retake double-counts `_done`; saved bad FITS pollutes stack | Quality check moves **before `_record_frame`**; rejected/retaken frames **unlink the FITS** (or aren't saved until accepted); `_done` slot unchanged until accepted. §1.9-D |
| C2-9, C2-15 | no run-started / heartbeat / dead-man's-switch; reconnect false confidence | Add **run-started** + opt-in **heartbeat** alerts + external **dead-man's-switch ping URL** (healthchecks-style); **alert on every reconnect attempt**; reconnect scoped to Alpaca, retries configurable. §1.8, §1.9-F |
| C2-11 | pre-flight 422 only checks "now", fights scheduling | Pre-flight runs **after schedule resolution**, checks each target **at its window**; warns only "never rises above floor tonight"; 422 reserved for "no frames", below-horizon returns a **non-blocking warning payload**. §1.10 |
| C2-13(b) | polar-latitude sun solver infinite loop | `next_sun_event` returns `None` when the sun never reaches the target altitude (guarded). §1.6 |
| C2-17 | meridian ETA unit (hours vs seconds) | `live.meridian_eta_s = ttf * 3600` with an explicit comment. §1.9-E |
| C3-1b, C3-7d | ~9 new unicode glyphs re-commit the panel's glyph sin; tofu on Android | **All new state icons use Lucide** (`shield-check`/`shield-alert`/`shield-off`, `git-commit`/`flip-vertical`, `snowflake`, `clock`/`hourglass`, `check-circle`/`octagon-x`/`cloud`/`sunrise`, `rotate-cw`, `wifi-off`). No new unicode glyphs. §3 |
| C3-1c | claims night palette satisfied while deferring it | This surface **ships a minimal night-palette fix**: differentiate `--good`/`--warn`/`--bad` by luminance + a desaturated amber, and never rely on hue. §3, §5 |
| C3-1d-banner | red-text-on-red-fill | Banners = red **border + Lucide icon**, body text in `--text`. Exact tokens specified. §2.6 |
| C3-2a | load-bearing readouts in `--text-dim` (fails AA) | Countdowns / resolved windows / ETA chips render in `--text`; `--text-dim` bumped to AA-passing; reserved for truly secondary labels. §3, §5 |
| C3-2b | running-state schedule fields at 0.35 opacity unreadable | Running state renders schedule/automation values as **read-only full-contrast text**, not faded disabled inputs. §2.4, §3 |
| C3-2c, C3-2d | 8–9px SVG labels; overlaid trends collapse at night | `Trend` uses **≥12px** font in viewBox, **no `preserveAspectRatio="none"` on text**, HTML-overlaid axis labels; **three separate** charts (color not load-bearing). §2.5, §3 |
| C3-3 | no focus rings; Segmented/HoldButton/dialogs not accessible | Add global `:focus-visible`; `Segmented` = real `radiogroup` (roving tabindex, arrows, `aria-checked`); `HoldButton` keyboard/SR confirm path; dialogs `role="alertdialog"` + focus trap + Escape + default focus on safe choice. §3 |
| C3-4 | tooltips invisible on touch | No safety-critical info in `title`; header safety chip is **tappable → sheet** with `{is_safe,reason,source,ts}`. §2.6, §3 |
| C3-5 | ≥44px claim false for 20px `Toggle` / dense grids | `Toggle` wrapped in a **44px pressable label**; schedule/alert/report touch controls **don't inherit `!py-1`**; report list rows ≥48px full-row tap. §3 |
| C3-6 | blink/pulse violate reduced-motion + night vision | All infinite animations gated behind `@media (prefers-reduced-motion: reduce)`; LIVE/running uses a **static** label in night mode. §3, §5 |
| C3-6c | single overwriting toast loses the UNSAFE alert | Safety/terminal events use a **persistent, non-overwriting** channel (queued toast + LOG badge), not the transient slot. §2.2 |
| C3-7a, C3-7c | Settings (safety config) buried in overflow; `Stat` truncates end-reason | Header safety chip deep-links to Settings→Safety; end-reason rendered via a dedicated **Badge** (icon + wrapping label), not truncating `Stat`. §2.5, §2.6 |
| C3-8a..f | slider precision, countdown cadence, CSV editor validation, geolocation fallback, aria-live, empty-state alt text | All specified. §2.3, §2.4, §3 |

### Rejected (with reason)

- **C1-3 "drop Settings from nav too":** Rejected. Settings is the home of the **Site P0** (lat/lon) and the safety config; it must be a real destination. Mitigation: it's the only *new* primary nav item; Report is demoted. The safety chip deep-links to it.
- **C2-7 "full time-slice interleaving scheduler":** Partially rejected for v1. Real ASIAIR-style time-slicing/revisiting is a large feature. v1 ships **window-sort + skip-ahead-to-first-ready** (resolves the queue-stall, the actual bug). Full interleaving is a follow-up card, noted in §6.
- **C2-15 "reconnect needs a power cycle, don't bother":** Partially rejected. We keep **Alpaca-only** software reconnect (genuinely helps transient USB/network blips) but make it **off by default**, retries configurable, and **alert on every attempt** so a flapping rig is visible. We do not claim it fixes hardware faults.
- **C3-7b "Segmented on all viewports":** Accepted (it was a draft inconsistency). Single `Segmented` component on all viewports; no `<select>` fork.

---

## 1. Backend

All new gating sits inside the engine's existing per-frame loop, immediately after `await self._checkpoint()` (engine.py:230) and before every slew, so pause/resume/crash-resume (`_done` map, `.sequence_resume.json`) semantics are preserved.

### 1.1 `devices/base.py` — `SafetyMonitor` role + capability flag (MODIFY)

```python
@dataclass
class SafetyReading:
    is_safe: bool
    reason: str = ""              # human string when unsafe, e.g. "cloud sensor"
    source: str = ""              # device name
    detail: dict[str, Any] = field(default_factory=dict)
    stale: bool = False           # set when the read timed out / device disconnected
    ts: float = field(default_factory=time.time)


class SafetyMonitor(Device):
    kind = "safety"

    @abstractmethod
    async def is_safe(self) -> bool: ...

    async def reading(self) -> SafetyReading:
        safe = await self.is_safe()
        return SafetyReading(is_safe=safe, source=self.name,
                             reason="" if safe else "unsafe condition reported")
```

Add to `Telescope` a capability flag + destination-pier helper (default UNKNOWN so non-GEM mounts are unaffected). The flag drives whether the UI even offers pier-limit enforcement (C1-11):

```python
class Telescope(Device):
    reports_destination_pier_side: bool = False   # set True by backends that implement it

    async def destination_pier_side(self, ra_hours: float, dec_deg: float) -> "PierSide":
        return PierSide.UNKNOWN
```

### 1.2 `devices/alpaca.py` — `AlpacaSafetyMonitor` + pier-side (MODIFY)

```python
class AlpacaSafetyMonitor(_AlpacaDevice, SafetyMonitor):
    dev_type = "safetymonitor"
    async def is_safe(self) -> bool:
        return bool(await self._get("issafe"))
```

Register `"safetymonitor": AlpacaSafetyMonitor` in `DEVICE_CLASSES`. Implement `AlpacaTelescope.destination_pier_side` via the `destinationsideofpier` call wrapped in try/except → `PierSide.UNKNOWN`; set `reports_destination_pier_side = True` only after a successful probe at connect.

### 1.3 `devices/sim.py` — `SimSafetyMonitor` (MODIFY)

Toggleable; default safe. Exposes `force_unsafe(reason: str | None)`. `build_sim_rig()` adds it under key `"safety"` and the rig surfaces its state so the header chip and the `abort_park_warm` end-to-end path are demoable without hardware (C2-16). `SimTelescope.reports_destination_pier_side = True`, `destination_pier_side` returns a deterministic side so the pier path is exercisable.

### 1.4 `config.py` — persisted app config (NEW)

Replaces the in-memory SF-hardcoded `hub.site`. **No secrets at rest** beyond an optional ntfy/Telegram token (documented). Atomic write.

```python
CONFIG_FILE = CAPTURE_DIR.parent / "astrodeck-config.json"

class SiteConfig(BaseModel):
    latitude: float = 37.77
    longitude: float = -122.42
    elevation_m: float = 0.0

# Named presets are the primary UX; numerics are Advanced-only.
SAFETY_PRESETS = {
    "backyard": dict(on_unsafe="pause", unsafe_consecutive=3,
                     resume_when_safe=True, resume_safe_consecutive=3,
                     max_pause_min=120),
    "remote":   dict(on_unsafe="abort_park_warm", unsafe_consecutive=2,
                     resume_when_safe=False, max_pause_min=0),
    # "custom" => user-edited, preset="custom"
}

class SafetyConfig(BaseModel):
    enabled: bool = True
    preset: str = "backyard"               # backyard | remote | custom
    poll_each_frame: bool = True
    # floor is OFF until a SafetyMonitor or a custom horizon is configured (C1-4)
    min_alt_deg: float = 0.0               # 0 = disabled; UI sets 10 when enabling
    horizon: list[tuple[float, float]] | None = None  # sorted (az,alt) control pts
    nogo_box: list[dict] | None = None     # optional [{az_min,az_max,alt_max}] pier guard
    enforce_pier_limits: bool = False      # only settable if mount reports pier side
    twilight_deg: float = -12.0            # nautical default (C1-26)
    # advanced (driven by preset unless preset == custom)
    on_unsafe: str = "pause"               # abort_park_warm | park | pause | warn
    unsafe_consecutive: int = 3
    resume_when_safe: bool = True
    resume_safe_consecutive: int = 3
    max_pause_min: int = 120               # 0 = no cap; escalates to park on timeout

class EscalationConfig(BaseModel):
    # all default to the gentle "warn" — never silently downgrade, but never abort by default
    require_cooling: bool = False; cooling_action: str = "warn"   # warn|abort|skip
    require_guiding: bool = False; guiding_action: str = "warn"   # warn|abort|skip
    af_failure_action: str = "warn"                               # warn|abort|skip
    hfr_reject_action: str = "warn"                               # warn|discard|retake (retake=Advanced)
    hfr_retake_limit_per_target: int = 4                          # cap per target (C1-7)
    no_progress_watchdog_s: int = 0                               # 0 = off
    reconnect_resume: bool = False                                # Alpaca-only; off by default (C2-15)
    reconnect_retries: int = 1

class AlertSink(BaseModel):
    id: str
    kind: str                              # ntfy | webhook | telegram
    enabled: bool = True
    url: str = ""                          # ntfy topic url / webhook url
    token: str = ""                        # telegram bot token (only secret we store)
    chat_id: str = ""                      # telegram chat id
    min_level: str = "warning"             # warning | error
    events: list[str] = ["run_start", "run_end", "safety", "error"]
    verified: bool = False                 # set True only by a successful round-trip test
    heartbeat_min: int = 0                 # 0 = off; periodic progress ping

class AppConfig(BaseModel):
    site: SiteConfig = SiteConfig()
    safety: SafetyConfig = SafetyConfig()
    escalation: EscalationConfig = EscalationConfig()
    alerts: list[AlertSink] = []
    deadman_url: str = ""                  # external healthcheck ping URL (C2-9)
    schedule_defaults: "ScheduleDefaults | None" = None

def load_config() -> AppConfig: ...        # returns defaults if file absent
def save_config(c: AppConfig) -> None: ... # atomic temp-write + os.replace
def redacted(c: AppConfig) -> dict: ...    # blanks tokens for WS/REST
```

### 1.5 `sequence/models.py` — per-target schedule + plan escalation (MODIFY)

All defaults preserve current behavior, so existing saved plans deserialize unchanged.

```python
class Schedule(BaseModel):
    # structured, no token mini-language (C1-24)
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
    # ... existing fields ...
    schedule: Schedule = Schedule()

class SequencePlan(BaseModel):
    # ... existing fields ...
    # safety/escalation are GLOBAL (config.py); plan carries only a master toggle.
    safety_check: bool = True          # honor the configured SafetyMonitor + floor
    meridian_flip_warn_min: float = 15.0

    def total_lights(self) -> int:
        return sum(s.count for t in self.targets for s in t.steps if not t.calibration)
```

Note (resolves C1-28): plan-level `min_altitude_deg` and `on_unsafe` overrides from the draft are **removed**. There are now exactly two altitude concepts — global pier-collision floor (`SafetyConfig.min_alt_deg`, checked against mount alt) and per-target start gate (`Schedule.min_altitude_deg`, checked against target alt).

### 1.6 `sequence/schedule.py` — pure window resolution (NEW)

No I/O; unit-testable. Reuses `coords.lst_hours`/`altaz` math style.

```python
def sun_altitude(lat, lon, t) -> float: ...
def next_sun_event(lat, lon, alt_deg, after_t, rising: bool) -> float | None:
    """Unix ts the sun next crosses alt_deg. Returns None at polar latitudes
    where the sun never reaches alt_deg (guarded loop, max 1 sidereal day) — C2-13."""

def resolve_window(sched: Schedule, site: dict, twilight_deg: float, now: float
                   ) -> tuple[float | None, float | None]:
    """(start_ts, stop_ts) for tonight. dusk/dawn use twilight_deg; None if N/A."""

def target_max_altitude(ra_h, dec_deg, lat, lon, start_ts, stop_ts) -> float:
    """Peak altitude across the window — used by pre-flight 'never rises' check."""

def gating_status(target, site, twilight_deg, now, target_alt) -> dict:
    """{state: 'ready'|'waiting'|'window_closed'|'never_rises',
        reason, eta_s, start_ts, stop_ts}"""
```

The horizon array is **sorted `(az, alt)` control points**; effective floor `= max(min_alt_deg, interp_wrap(horizon, az))` with linear interpolation across the 0↔360 seam (C2-3).

### 1.7 `sequence/report.py` — session report (NEW)

v1 is a summary, not an analytics product (C1-19, C2-12). Trends are **derived from `frames` at read time** (no duplicate arrays); the frames list is **append-only/downsampled**; the report header **leads with per-filter integration** (the headline number).

```python
class FrameRecord(BaseModel):
    ts: float; target: str; filter: str | None; frame_type: str
    exposure_s: float; accepted: bool
    hfr: float | None; sensor_temp_c: float | None; guide_rms_total: float | None
    saved_path: str | None

class FilterBreakdown(BaseModel):
    filter: str; frames: int; rejected: int; integration_s: float; hfr_median: float | None

class TargetBreakdown(BaseModel):
    name: str; frames: int; rejected: int; integration_s: float; by_filter: list[FilterBreakdown]

class SessionReport(BaseModel):
    id: str                         # "<plan>-<YYYYMMDD-HHMMSS>"
    plan_name: str; started_at: float; ended_at: float | None
    end_reason: str | None          # complete|aborted|error|unsafe|dawn_cutoff
    frames_captured: int; frames_rejected: int; integration_s: float
    by_filter: list[FilterBreakdown]            # plan-wide per-filter totals (headline)
    targets: list[TargetBreakdown]
    safety_events: list[dict]                   # [{ts, reason, action}]
    frames: list[FrameRecord]                   # downsampled if huge

class SessionReporter:
    def __init__(self, plan): ...
    def record_frame(self, rec: FrameRecord) -> None: ...   # append-only file write via to_thread
    def record_safety(self, reason, action) -> None: ...
    def finalize(self, end_reason: str) -> SessionReport: ...
    @staticmethod
    def list_reports() -> list[dict]: ...        # summaries only
    @staticmethod
    def load(report_id: str) -> SessionReport | None: ...
    @staticmethod
    def attach_existing(report_id: str) -> "SessionReporter | None": ...  # resume re-attach (C2-5)
    @staticmethod
    def trends(report: SessionReport, max_points: int = 200) -> dict:
        """Derive {hfr:[(ts,v)], temp:[...], rms:[...]} from frames at read time."""
```

Persisted to `captures/reports/<id>.json`. Writes go through `asyncio.to_thread` (the existing `_persist` sync-in-loop is already flagged; we do not add a second blocking full-file rewrite — appends only).

### 1.8 `alerting.py` — outbound dispatcher (NEW)

Bus subscriber started in `create_app()` lifespan. Resolves C1-16/18, C2-9/10.

```python
class AlertDispatcher:
    def __init__(self, bus, get_config): ...
    async def run(self) -> None: ...           # long-running

    # event mapping (the unattended-anxiety set):
    #   run_start  — sequence -> running first transition
    #   run_end    — sequence state in {complete, aborted, error}  (NEVER deduped)
    #   safety     — is_safe transitions (NEVER deduped)
    #   error/warning logs matching sink.min_level (deduped within a short window)
    #   heartbeat  — every sink.heartbeat_min: "142/400, HFR 1.8, RMS 0.6\""
    #   reconnect  — every reconnect attempt (NEVER deduped) — C2-15

    async def _send(self, sink, ev) -> None:
        # ntfy: POST topic, Title/Priority/Tags headers
        # webhook: POST json {type,level,message,source,ts,plan,...}
        # telegram: bot token + chat_id
        # on failure: bus.publish("alert", sink=..., ok=False, error=...)
        #             AND enqueue to a persistent undelivered queue with retry
    async def deadman_ping(self) -> None:       # GET deadman_url each frame; absence => their alert
    async def test(self, sink_id) -> dict:      # real round-trip; sets sink.verified on success
```

`httpx.AsyncClient` reused; failures logged via `bus.log("warning", ...)`, never raised. Undelivered queue flushes when connectivity returns.

### 1.9 `sequence/engine.py` — hooks (MODIFY)

New `__init__` collaborators: `self.reporter`, `self._unsafe_streak = 0`, `self._safe_streak = 0`, `self._last_frame_at = 0.0`, `self._watchdog_task`, `self._retakes_per_target: dict[int,int]`. Config snapshot at run start: `self._cfg = load_config()`.

**A. Unified safety gate** — after every `_checkpoint()` and before every slew. Three concerns kept **distinct**:

```python
async def _safety_gate(self, *, context: str, target: Target | None = None) -> None:
    cfg = self._cfg
    if not (cfg.safety.enabled and self.plan.safety_check):
        return
    mon = self.hub.devices.get("safety")
    # fail-safe/closed: if the run requested safety but the monitor is gone/stale, treat as unsafe (C1-15)
    if mon is None:
        return                       # never connected -> floor still enforced below
    if not mon.connected:
        self._on_unsafe("safety monitor disconnected", stale=True); return
    reading = await self.hub.safety_reading()   # own-task cached read, may be stale (C1-12)
    if reading.stale:
        self._on_unsafe("safety read timed out", stale=True); return
    if not reading.is_safe:
        self._unsafe_streak += 1; self._safe_streak = 0
        if self._unsafe_streak >= cfg.safety.unsafe_consecutive:
            self._on_unsafe(reading.reason or reading.source)
    else:
        self._unsafe_streak = 0; self._safe_streak += 1
    # pier-collision guard uses MOUNT-reported alt/az, not target coords (C1-10)
    if context == "slew" and target is not None:
        await self._enforce_mount_floor(projected=True, target=target)
```

`_on_unsafe(reason, stale=False)` records to reporter, publishes a `safety` bus event (`is_safe=false, reason, action, stale`), logs `error`, then per `on_unsafe`:
- `pause` → **stop tracking / park-hold**, loop on the gate with `sleep` until `_safe_streak >= resume_safe_consecutive`; on resume **re-check horizon + pier** before continuing; if `max_pause_min` elapses → escalate to `park` (C1-6, C2-6).
- `abort_park_warm` / `park` → raise `SafetyAbort(reason)`; wind-down is **shielded** (below).
- `warn` → log + alert only, continue.

`_enforce_mount_floor` reads `tel.get_position()` → `altaz()` and compares against `max(min_alt_deg, interp(horizon, az), nogo_box)`. `projected=True` advances time by a slew+solve margin (default 180 s) so a setting target isn't accepted right as it drops (C2-1b). If `enforce_pier_limits` and the mount reports a definite unsafe destination side, hard-stop the slew.

**B. Wire into the loop** (engine.py `_run_step`, after line 230):
```python
await self._checkpoint()
await self._safety_gate(context="frame", target=target)
# schedule gate handled at the target level (skip-ahead), not here
...
info = await self.hub.capture(...)
accepted = self._check_quality(info)          # CHANGED: returns bool, BEFORE record (C2-8)
self._reporter_record(target, step, info, accepted)
self._last_frame_at = time.time()
if accepted:
    self._record_frame(key, i)
else:
    self._handle_reject(info, key, i)          # discard(unlink)/retake(unlink+reexpose)/warn
```
Pre-slew gates added in `_setup_target` (before each slew) and `_maybe_meridian_flip` (before `hub.meridian_flip`).

**C. Autorun scheduling with skip-ahead** (resolves the queue stall, C1-23/C2-7). Replace the strict `for ti, target` loop with a **window-sorted, skip-ahead** scheduler:

```python
order = schedule_order(plan.targets, site, twilight, now)   # sort by start_ts
remaining = list(order)
while remaining:
    # pick the first target whose window is OPEN now; if none, sleep to the
    # earliest start, but a target that becomes ready first preempts the wait.
    ready = first_ready(remaining, ...)
    if ready is None:
        target = earliest_start(remaining)
        self._set_state(state="running", detail=f"waiting for {target.name}",
                        schedule={"state":"waiting","eta_s":..., "start_ts":..., "stop_ts":...})
        await self._wait_until_ready(remaining)   # checkpoint + bounded sleep; cancel-responsive
        continue
    target = ready
    try:
        await self._run_target(...)
    except StopTarget:        # window closed / max_run / below start-gate-for-good
        self._reporter.mark_skipped(target); continue
    remaining.remove(target)
# if all remaining are past dawn -> finalize(end_reason="dawn_cutoff")
```

`StopTarget` is for **scheduling only** — below-start-altitude or window-closed advances to the next target, it does **not** abort the night (C2-2). `SafetyAbort` is reserved for the safety device and pier collisions.

**D. Escalation (no silent downgrade, gentle defaults).** `_cool_and_wait`, guiding-start, `_autofocus`, and `_check_quality` consult `cfg.escalation`. `_check_quality` now returns `bool`; `_handle_reject` implements `warn` (flag, keep), `discard` (unlink FITS, `accepted=False`, `_done` unchanged), `retake` (unlink + re-expose, capped by `hfr_retake_limit_per_target`, then fall back to discard). Quality runs **before** `_record_frame` so `_done` and the saved file stay consistent (C2-8).

**E. Live ETA chips.** `_set_state` attaches a `live` sub-object when relevant:
- `meridian_eta_s = ttf * 3600` (ttf is **hours** — C2-17) when within `meridian_flip_warn_min`.
- Cooling: honest current/target temp only; **no ETA** (C2-4).

**F. Watchdog + reconnect + own-cadence safety poll.**
- `no_progress_watchdog_s > 0` → `_watchdog_task` wakes every 10 s; if running, not paused, and `now - _last_frame_at > threshold` → `safety`-style event + `on_unsafe`.
- `hub.safety_reading()` is served from a **dedicated poller** (`asyncio.wait_for(mon.is_safe(), timeout)`); a timeout marks the cached reading `stale=True` (C1-12, C1-15).
- Reconnect: `DeviceError` mid-frame, if `reconnect_resume` (Alpaca only, off by default), pause → `hub.reconnect_role(role)` → retry up to `reconnect_retries`; **alert on every attempt** (C2-15).

**G. SafetyAbort plumbing + shielded wind-down** (C1-8, C2-1). Define `class SafetyAbort(DeviceError)` and `class StopTarget(Exception)`. `_run`'s except chain inserts `except SafetyAbort` **above** `except Exception`:

```python
except SafetyAbort as e:
    self._set_state(state="aborted", detail=str(e), end_reason="unsafe")
    if self.reporter: self.reporter.finalize("unsafe")
    # shield so a concurrent UI abort (task.cancel) can't interrupt park/warm mid-slew
    await asyncio.shield(self._wind_down(park=True, warm=(self._cfg.safety.on_unsafe=="abort_park_warm")))
    bus.publish("report", id=self.reporter.id if self.reporter else None)
```
The UI `abort()` path remains `task.cancel()` → `_safe_stop()`; precedence is defined: a `SafetyAbort` wind-down in progress is shielded and completes before cancellation takes effect. All terminal paths (`complete`/`aborted`/`error`/`unsafe`/`dawn_cutoff`) call `reporter.finalize(reason)` and publish `report`.

### 1.10 `api/app.py` — endpoints (MODIFY)

```
GET  /api/config                       -> redacted AppConfig (site, safety, escalation, alerts, deadman_url)
POST /api/config                       -> partial-merge; persists; re-reads hub.site
POST /api/site                         -> (existing; now writes config + elevation_m)

GET  /api/safety/state                 -> {connected, reading|null, streak, stale}
POST /api/safety/simulate {unsafe,reason} -> sim-only (404 if mode != sim)

GET  /api/alerts                       -> list[AlertSink] (tokens blanked)
POST /api/alerts                       -> upsert by id (verified reset to false on url/token change)
DELETE /api/alerts/{id}
POST /api/alerts/{id}/test             -> real round-trip; {ok, error?, verified}

GET  /api/reports                      -> [summaries]
GET  /api/reports/{id}                 -> SessionReport (+ derived trends) (404 if missing)
GET  /api/reports/{id}/frames.csv      -> text/csv (power-user export)

POST /api/sequence/preflight  (plan)   -> {ok, warnings:[{target, kind, message}]}
```

**Pre-flight is a separate non-blocking endpoint** (C2-11): it resolves each target's window via `schedule.py` and warns only when a target **never rises above the floor across its window** (`never_rises`). `/api/sequence/start` keeps its existing `422 "plan has no frames"`; it does **not** 422 on below-horizon. The UI calls `/preflight`, and if warnings exist, shows a confirm dialog defaulting to "Run anyway" (C1-4). Safety monitor connects via the existing `/api/connect/alpaca` with `role="safety", dev_type="safetymonitor"` (no new connect route).

WS `hello` (`hub.summary()`) gains `"safety"` snapshot and `"config"` (redacted) so the UI hydrates on connect.

### 1.11 `hub.py` — additions (MODIFY)

- `ROLES = (..., "safety")`.
- `self.site` loaded from `config.load_config()` → `{latitude, longitude, elevation_m}`.
- `self.safety` property → `self.devices.get("safety")`; `safety_reading()` returns the cached own-cadence read.
- `reconnect_role(role)` + `self._last_connect: dict[str, dict]` populated in each `connect_*`.
- `summary()` adds `"safety"` + redacted `"config"`.
- `poll_status()` adds a **cheap** `safety` block from the **cached** reading (no inline `is_safe()` call — C1-12).
- A dedicated safety poller task started by `ensure_status_poller()`.

---

## 2. Frontend

### 2.1 `ui/src/types.ts` (MODIFY)

```ts
export interface SafetyReading { is_safe: boolean; reason: string; source: string; detail?: Record<string, number>; stale: boolean; ts: number; }
export interface SafetyState  { connected: boolean; reading: SafetyReading | null; streak: number; }

export interface Schedule {
  start_mode: "now"|"dusk"|"dawn"|"time"; start_offset_min: number; start_time: string | null;
  min_altitude_deg: number;
  stop_mode: "none"|"dawn"|"time"; stop_offset_min: number; stop_time: string | null;
  max_run_min: number; on_missed: "wait"|"skip";
}
// Target gains: schedule: Schedule
// SequencePlan gains: safety_check: boolean; meridian_flip_warn_min: number
//   (plan-level safety/escalation overrides REMOVED — global in config)

export interface SequenceState {                 // extend existing
  state: "idle"|"running"|"paused"|"complete"|"aborted"|"error";
  detail?: string; target?: string; plan_name?: string;
  progress?: { frames_done: number; frames_total: number; percent: number; elapsed_s: number; rejected?: number };
  schedule?: { state: "waiting"|"ready"|"window_closed"|"never_rises"; reason: string; eta_s: number; start_ts?: number; stop_ts?: number };
  live?: { meridian_eta_s?: number; sensor_temp_c?: number; guide_rms?: number };
  end_reason?: "complete"|"aborted"|"error"|"unsafe"|"dawn_cutoff";
}

export interface AlertSink { id: string; kind: "ntfy"|"webhook"|"telegram"; enabled: boolean;
  url: string; chat_id?: string; min_level: "warning"|"error"; events: string[];
  verified: boolean; heartbeat_min: number; }   // token never sent to client
export interface SafetyConfig { enabled: boolean; preset: "backyard"|"remote"|"custom";
  min_alt_deg: number; horizon: [number, number][] | null; enforce_pier_limits: boolean;
  twilight_deg: number; on_unsafe: "abort_park_warm"|"park"|"pause"|"warn";
  unsafe_consecutive: number; resume_when_safe: boolean; resume_safe_consecutive: number; max_pause_min: number; }
export interface EscalationConfig { require_cooling: boolean; cooling_action: "warn"|"abort"|"skip";
  require_guiding: boolean; guiding_action: "warn"|"abort"|"skip"; af_failure_action: "warn"|"abort"|"skip";
  hfr_reject_action: "warn"|"discard"|"retake"; hfr_retake_limit_per_target: number;
  no_progress_watchdog_s: number; reconnect_resume: boolean; reconnect_retries: number; }
export interface SiteConfig { latitude: number; longitude: number; elevation_m: number; }
export interface AppConfig { site: SiteConfig; safety: SafetyConfig; escalation: EscalationConfig; alerts: AlertSink[]; deadman_url: string; }

export interface FilterBreakdown { filter: string; frames: number; rejected: number; integration_s: number; hfr_median: number | null; }
export interface TargetBreakdown { name: string; frames: number; rejected: number; integration_s: number; by_filter: FilterBreakdown[]; }
export interface SessionReportSummary { id: string; plan_name: string; started_at: number; ended_at: number | null; end_reason: string | null; frames_captured: number; frames_rejected: number; integration_s: number; }
export interface SessionReport extends SessionReportSummary {
  by_filter: FilterBreakdown[]; targets: TargetBreakdown[];
  safety_events: { ts: number; reason: string; action: string }[];
  trends: { hfr: [number, number][]; temp: [number, number][]; rms: [number, number][] };
}
```

### 2.2 `ui/src/store.ts` (MODIFY)

Add to `AppState`: `safety: SafetyState | null`, `config: AppConfig | null`, `alert: { sink: string; ok: boolean; error?: string; key: number } | null`, `lastReportId: string | null`, `unseenErrors: number`, and a **queued** `toasts: Toast[]` (replace the single slot — C3-6c) with auto-dismiss; safety/terminal toasts are **persistent** (no auto-dismiss, not overwritten). New `handleEvent` cases:

```ts
case "safety":
  set({ safety: { ...(get().safety ?? empty), reading: ev.data as any, connected: !(ev.data as any).stale } });
  if ((ev.data as any).is_safe === false || (ev.data as any).stale)
    get().pushToast({ level: "error", message: `UNSAFE: ${(ev.data as any).reason}`, persistent: true });
  break;
case "alert":  set({ alert: { ...(ev.data as any), key: ++k } }); break;
case "report": set({ lastReportId: (ev.data as any).id }); break;
case "config": set({ config: ev.data as any }); break;
case "hello":  set({ config: (ev.data as any).config, safety: (ev.data as any).safety, /*...*/ }); break;
```
`log` case: increment `unseenErrors` when `level==="error"` and the drawer is closed; `setView`/opening the drawer resets it. The status `safety` sub-block updates `safety.reading` at the 2 s cadence.

### 2.3 `ui/src/views/SettingsView.tsx` (NEW) + nav entry

New primary view `settings` (Lucide `settings` icon). Four `Panel`s, single-column on mobile. Persist via debounced `POST /api/config` (400 ms).

1. **Site** — lat/lon/elevation numeric fields (manual entry **visible by default**), a "Use my location" button (`navigator.geolocation`, with explicit denied/blocked state and the manual fields as the keyboard-reachable fallback — C3-8d). Shows tonight's resolved dusk/dawn **clock time** prominently (C1-26). Fixes the P0 SF hardcode.
2. **Safety** — leads with `enabled` master toggle + a **preset `Segmented`**: *Backyard (pause & wait)* / *Remote (abort, park, warm)* / *Custom*. A one-line plain-language summary of the active preset. A floor control (off by default; enabling sets 10°) as a **numeric field + slider + live "currently NN°" readout** (C3-8a). Pier-limit toggle is **disabled with an explanatory note unless the mount reports `DestinationSideOfPier`** (C1-11). Everything else (`unsafe_consecutive`, `resume_*`, `max_pause_min`, `twilight_deg`, horizon CSV) lives behind a collapsed **Advanced** disclosure; choosing any of them flips `preset="custom"`. Horizon editor is an explicit Advanced stub: a textarea of `(az,alt)` pairs with **inline count/range validation** and an `aria-describedby` error region (C3-8c).
3. **Escalation** (Advanced section) — three `warn|abort|skip` `Segmented`s (cooling/guiding/AF); `hfr_reject_action` `Segmented` with `retake` shown only here, with an inline cost note and the per-target cap; `no_progress_watchdog_s`; `reconnect_resume` (Alpaca-only note) + retries.
4. **Alerts** — list of `AlertSink` **tap-to-expand cards** (collapsed: name + enabled + verified status; expanded: 44px controls — C3-5d). ntfy (topic URL, recommended), webhook (URL), telegram (bot token + chat id). Per-card **Test** does a real round-trip and sets `verified`. A **persistent "Alerts not verified — you may not be notified" banner** shows while any enabled sink is unverified (C1-16). Plus `deadman_url` field for the external dead-man's-switch (C2-9). SMTP intentionally absent (C1-17).

### 2.4 `ui/src/views/SequenceView.tsx` (MODIFY)

- **`loadPlan()` and `addTarget()` backfill `schedule: defaultSchedule()` on every target** (C1-27); `defaultSchedule()` = `{start_mode:"now", ...}`.
- **Per-target Schedule sub-panel** (collapsible, 44px-min controls, not `!py-1` — C3-5b): a **Start `Segmented`** (Now / Dusk / Dawn / Time) + a **± minutes stepper** when Dusk/Dawn, or a time input when Time (no token strings — C1-24); `min_altitude_deg` with a live "currently NN°" readout; **Stop `Segmented`** (None / Dawn / Time); `max_run_min`; `on_missed` (Wait / Skip, default Wait — C1-25). An inline **resolved-window badge** in `--text` ("21:42 → 04:58 · 7.3 h") — never `--text-dim` (C3-2a).
- **Automation panel** does **not** grow with safety knobs. It gains one **read-only line**: `Safety: Backyard — edit in Settings →` (links to Settings→Safety) (C1-2). The HFR row keeps the existing flag-factor input only.
- **Run-time progress panel:**
  - `schedule.state === "waiting"` → a full-width **scheduling banner** ("Waiting for M31 — dusk in 23 min · safe to leave; mount is parked", live countdown). Copy explicitly states the mount is **not yet tracking** (C2-9-novice / C1-9).
  - **Meridian-flip ETA chip** from `live.meridian_eta_s` (Lucide `flip-vertical`), `--text`, shown only when present. **No cooling-ETA chip** (C2-4).
  - The **Abort** button becomes a `HoldButton` (hold 800 ms) for *Abort sequence* — with a keyboard/SR confirm-dialog fallback (C1-21, C3-3b). The emergency **mount STOP** (MountView) stays a single tap (out of scope here, noted).
- **Pre-flight**: `Run Sequence` first calls `POST /api/sequence/preflight`; if warnings, open a `role="alertdialog"` confirm (focus trapped, Escape closes, default focus on the **safe** choice) listing offenders ("M51 never rises above 10° tonight"), with **"Run anyway"** and "Cancel" (C1-4, C2-11, C3-3c). Copy never says "your floor".
- Running-state schedule/automation values render as **read-only full-contrast text**, not 0.35-opacity disabled inputs (C3-2b).

### 2.5 `ui/src/views/ReportView.tsx` (NEW)

`report` view (Lucide `bar-chart-2`). **Not in primary nav** — reached from the run-complete panel's "View session report →" link (store `lastReportId`) and the mobile overflow sheet (C1-20, C1-3). **No LIVE mode** — strictly post-hoc (C1-20).

- **List**: `GET /api/reports` → rows ≥48px full-row tap (C3-5c). Empty: ghost illustration **with text alternative** + copy; skeleton rows carry `aria-busy` (C3-8f). Error: "Couldn't load reports — retry."
- **Detail**: `GET /api/reports/{id}` →
  - **Header**: leads with **per-filter integration** ("3h12m Ha · 2h08m OIII"), then frames / total integration / rejects (% in warn tone). End-reason as a dedicated **Badge** (Lucide icon + wrapping label — not truncating `Stat`, C3-7c): `check-circle` complete / `octagon-x` aborted / `cloud` unsafe / `sunrise` dawn_cutoff.
  - **Per-target → per-filter table** (read-only dense grid OK here).
  - **Three separate `Trend` sparklines** (HFR, temp, RMS) — color not load-bearing, ≥12px labels, no `preserveAspectRatio="none"` on text (C3-2c/d). Added to `components/graphs.tsx`.
  - **Safety events** list if any. **Export CSV / JSON** buttons.

### 2.6 `ui/src/App.tsx` (MODIFY) — consolidated status, LOG badge, nav

- **One consolidated status region** in the header (C1-13): a single dominant **safety chip** with **shape + text**, not hue (C3-1a/d):
  - SAFE → filled disc + "SAFE" (subtle).
  - UNSAFE → hollow ring + Lucide `shield-alert` + "UNSAFE" (and during a run, "UNSAFE — parking").
  - STALE (WS down or read timeout) → dashed ring + "STALE".
  - NO MONITOR → outline + "no safety device".
  The chip is **tappable** → opens a sheet `{is_safe, reason, source, ts}` on touch (no `title`-only info — C3-4), and **deep-links to Settings→Safety** (C1-22). Everything else (mode chip, NINA/link LED, mount/temp/RMS) is subordinate and **greys out together when `!wsConnected`** (C3-7e).
- **LOG badge**: count bubble of `unseenErrors`, resets on open (panel ask).
- **Nav**: add **Settings** to primaries. Report is **not** a primary. On `<sm`, a 44px "More" sheet holds Settings + Report + Align/Power so the bottom bar stays ≤7 readable items.
- **Reconnecting bar**: thin amber bar under the header with a Lucide `wifi-off` icon (distinct from the schedule-waiting `clock`/`hourglass` — C3-7d), gated behind reduced-motion for any animation.

### 2.7 `ui/src/components/ui.tsx` (MODIFY) — shared primitives

- **`Toggle`**: keep the 20px visual control but wrap it (or its row) in a **44px-tall pressable `<label>`** so the hit area meets the minimum; the standalone alert-row toggles get the same (C3-5a).
- **`Segmented`** (NEW): `role="radiogroup"`, roving `tabindex`, arrow-key nav, `aria-checked`, visible `:focus-visible` ring; `min-h-[44px]`; used on **all viewports** (C3-3a, C3-7b).
- **`HoldButton`** (NEW): hold-to-confirm with a fill ring; `Enter`/`Space` (and a secondary tap) trigger a standard `role="alertdialog"` confirm instead of requiring a hold (C3-3b); `aria-label` describes the hold; fill respects reduced-motion (falls back to the dialog).
- **`Badge`** (NEW): icon + wrapping label for end-reasons / states (replaces truncating `Stat` for these).
- **`StatusChip`** (NEW): the consolidated safety/status chip (shape+text).

### 2.8 `ui/src/components/graphs.tsx` (MODIFY) — `Trend`

`Trend` (NEW): one metric per chart, single `<path>`, **`fontSize={12}`** in viewBox, **no `preserveAspectRatio="none"`** on any text element (axis labels HTML-overlaid), `h-24` responsive. Used by ReportView.

---

## 3. Shared contracts (authoritative — parallel implementers align to this)

**REST endpoints** (all under `/api`):
`GET/POST /config`, `POST /site` (now writes elevation), `GET /safety/state`, `POST /safety/simulate`, `GET/POST /alerts`, `DELETE /alerts/{id}`, `POST /alerts/{id}/test`, `GET /reports`, `GET /reports/{id}`, `GET /reports/{id}/frames.csv`, `POST /sequence/preflight`. `/sequence/start` unchanged contract (422 only for "no frames").

**New WS event types** (through the existing generic bus — `events.py` needs **no change**): `safety`, `alert`, `report`, `config`. `sequence` payload extended with `schedule`, `live`, `end_reason`, `progress.rejected`. `hello` payload gains `safety` + redacted `config`.

**Backend model fields** (pydantic, defaults preserve behavior):
- `config.py`: `SiteConfig`, `SafetyConfig`, `EscalationConfig`, `AlertSink`, `AppConfig`, `SAFETY_PRESETS` (§1.4).
- `sequence/models.py`: `Schedule`; `Target.schedule`; `SequencePlan.safety_check`, `.meridian_flip_warn_min`; `SequencePlan.total_lights()`.
- `sequence/report.py`: `FrameRecord`, `FilterBreakdown`, `TargetBreakdown`, `SessionReport` (§1.7).
- `devices/base.py`: `SafetyReading`, `SafetyMonitor`, `Telescope.reports_destination_pier_side`, `Telescope.destination_pier_side`.
- `hub.py`: `ROLES += ("safety",)`; `site` is `{latitude, longitude, elevation_m}`; `safety_reading()`, `reconnect_role()`, `_last_connect`.

**TS types** (`types.ts`): `SafetyReading`, `SafetyState`, `Schedule`, extended `SequenceState`, `AlertSink`, `SafetyConfig`, `EscalationConfig`, `SiteConfig`, `AppConfig`, `FilterBreakdown`, `TargetBreakdown`, `SessionReportSummary`, `SessionReport`. `Target` += `schedule`; `SequencePlan` += `safety_check`, `meridian_flip_warn_min` (and **remove** the draft's plan-level safety/escalation override fields — they were never added).

**Store slices** (`store.ts`): `safety`, `config`, `alert`, `lastReportId`, `unseenErrors`, queued `toasts` (replaces single `toast`), `pushToast(toast)`/`dismissToast(key)`; `handleEvent` cases `safety|alert|report|config|hello` + `log` badge increment.

**CSS tokens** (`index.css`) — minimal night-palette + a11y fix that this surface owns (C3-1c/2a/3/6):
- Bump `--text-dim` to AA-passing (`#7c8bab` day; a lighter coral night).
- Differentiate night `--good`/`--warn`/`--bad` by luminance and shift `--warn` to a desaturated amber distinguishable from red; **status is never hue-only** (paired with shape/Lucide icon/label everywhere).
- Add `:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px }`.
- Wrap `.blink` and any new fill/pulse in `@media (prefers-reduced-motion: reduce) { animation: none }`; running/LIVE labels are static in night mode.

---

## 4. Disjoint file ownership (parallel-safe)

Each file has exactly one owner lane. Shared files (`types.ts`, `store.ts`, `index.css`, `api/app.py`, `hub.py`, `engine.py`, `models.py`) are edited per the **Shared contracts** above; to avoid collisions, the lane that **creates** the contract lands first (build order §7), others import.

| Lane | Creates | Modifies (owned regions) |
|------|---------|--------------------------|
| **A. Config + Site** | `server/astrodeck/config.py`, `ui/src/views/SettingsView.tsx` (Site panel) | `hub.py` (site load), `api/app.py` (`/config`,`/site`), `types.ts` (Site/App/Safety/Escalation/AlertSink + config), `store.ts` (`config`,`hello`), `App.tsx` (Settings nav) |
| **B. Safety device + gate** | `devices/sim.py` `SimSafetyMonitor` (in-file), Alpaca `AlpacaSafetyMonitor` (in-file) | `devices/base.py` (SafetyMonitor/SafetyReading/pier), `devices/alpaca.py`, `hub.py` (role, `safety_reading`, poller), `engine.py` (`_safety_gate`,`_on_unsafe`,`_enforce_mount_floor`,`SafetyAbort`, shielded wind-down), `api/app.py` (`/safety/*`), `store.ts` (`safety`), `App.tsx` (StatusChip), `components/ui.tsx` (StatusChip/Badge), `index.css` (palette+focus+motion) |
| **C. Schedule** | `server/astrodeck/sequence/schedule.py` | `sequence/models.py` (`Schedule`,`Target.schedule`), `engine.py` (skip-ahead loop, `StopTarget`), `api/app.py` (`/sequence/preflight`), `types.ts` (`Schedule`,`SequenceState.schedule`), `SequenceView.tsx` (schedule sub-panel, preflight dialog, loadPlan backfill), `components/ui.tsx` (`Segmented`) |
| **D. Alerts** | `server/astrodeck/alerting.py`, SettingsView Alerts panel (in-file) | `api/app.py` (`/alerts/*`, lifespan start), `config.py` (AlertSink — coordinated w/ A), `store.ts` (`alert`), `types.ts` (AlertSink) |
| **E. Report** | `server/astrodeck/sequence/report.py`, `ui/src/views/ReportView.tsx` | `engine.py` (reporter hooks, finalize, `report` publish, quality-before-record), `api/app.py` (`/reports/*`), `types.ts` (report types), `store.ts` (`lastReportId`,`report`), `components/graphs.tsx` (`Trend`), `App.tsx` (overflow sheet + run-complete link), `SequenceView.tsx` (report link) |
| **F. Touch/a11y/chrome** | `components/ui.tsx` `HoldButton` (in-file) | `App.tsx` (consolidated status region, LOG badge, More sheet, reconnect bar), `store.ts` (queued toasts), `index.css` (Toggle 44px, focus, motion), `SequenceView.tsx` (HoldButton swap) |

`sequence/__init__.py` exports `Schedule`, `SessionReport` (lane C/E coordinate). `events.py` is **unchanged**.

---

## 5. UI states (loading / empty / error / success)

**Safety chip / Settings:**
- *No monitor*: outline + "no safety device" (Settings shows "Connect an Alpaca SafetyMonitor on the Rig page"). Floor still enforced if set.
- *Safe*: filled disc + "SAFE". *Unsafe*: hollow ring + `shield-alert` + "UNSAFE" (+ "— parking" during wind-down). *Stale*: dashed ring + "STALE" + reconnect bar (never SAFE-while-stale).

**Autorun schedule (per target):**
- *Waiting*: `clock`/`hourglass` icon + countdown ("starts in 23m at 21:42 · mount parked, safe to leave"). *Ready/running*: normal progress. *Window closed / never_rises*: struck-through name + "skipped — window closed" / "never rises above floor" badge. *Dawn cutoff*: complete-panel "Stopped at dawn (04:58)".

**Alerts:**
- *Empty*: "No alert channels — add ntfy/webhook/Telegram to be notified when a run finishes or aborts." *Unverified*: persistent "Alerts not verified" banner. *Testing*: "Testing…"; *ok*: `check-circle` "Verified"; *fail*: inline error. *Runtime send fail*: `warning` log + `alert` toast ("ntfy alert failed: timeout"); queued for retry.

**Session Report:**
- *List loading*: skeleton rows (`aria-busy`). *List empty*: ghost illustration (with alt text) + copy. *List error*: retry. *Detail loading*: skeleton header + chart placeholders. *Detail*: per-filter header + breakdown + 3 sparklines + export. *404*: "This report is no longer available." (No LIVE mode.)

**Sequence run-time:** *Cooling*: honest "−3.2°C → −10°C" (no ETA). *Flip imminent*: `flip-vertical` "flip in 8m". *HFR retake*: `rotate-cw` "retaking (HFR 4.2 ≫ 1.8)" (capped per target). *Watchdog trip*: persistent "No frame in 6m — aborting" + alert. *Unsafe*: persistent banner (red border + `shield-alert`, body text in `--text`).

---

## 6. Night mode + 375 px phone

- **Night mode**: this surface ships the minimal palette fix (§3). **No state relies on hue** — safety = shape+label, schedule = `clock` icon + amber (desaturated, distinct from red), end-reasons = distinct Lucide icon + text. Banners are border+icon, body text `--text`. The dimmed starfield is preserved (existing). All blink/pulse/fill respect `prefers-reduced-motion`; running/LIVE labels are static at night.
- **375 px layout**: Settings and Report are single-column `flex flex-col gap-4` stacks. SequenceView's existing `xl:grid-cols-[1fr_320px]` collapses to one column; schedule sub-panels and the safety read-only line stack. `Trend` charts are `width:100%`, `h-24`.
- **Touch**: every new interactive control ≥44 px (Toggle wrapped to 44px, Segmented `min-h-[44px]`, schedule/alert fields not `!py-1`, report rows ≥48px). Abort = HoldButton (with keyboard/SR confirm fallback); emergency mount STOP stays single-tap. Countdowns are `mono` and update at a **coarse cadence** (≥1 s, ≥1 min when >2 min out — C3-8b), not per animation frame.
- **Nav overflow**: bottom bar ≤7 readable primaries; Settings + Report + Align/Power behind a 44px "More" sheet. The safety chip and run-complete link are always reachable without the sheet.
- **aria-live**: a polite region for the scheduling countdown and retake chip; an **assertive** region for unsafe/terminal state changes (C3-8e). Skeletons use `aria-busy`; ghost illustrations have text alternatives.

**Deferred (out of scope, noted for follow-up cards):** full ASIAIR-style time-slice/revisit scheduler (v1 = window-sort + skip-ahead); exponential cooling-ETA; full polar horizon editor (v1 = floor + CSV stub); deeper night-palette overhaul beyond the minimal fix.

---

## 7. Implementation checklist (build order; contract-creators land first)

1. **Lane A — Config + Site.** `config.py` (`AppConfig`, `SAFETY_PRESETS`, load/save/redacted) → `GET/POST /api/config`, `/api/site` writes elevation → `hub.site` from config → `types.ts` config types → `store.ts` `config`/`hello` → SettingsView **Site** panel + Settings nav. *(Unblocks geo-correctness; resolves the SF P0 — C1 dependency.)*
2. **Lane F — a11y/touch primitives + chrome.** `index.css` (palette+`:focus-visible`+reduced-motion+Toggle 44px), `Segmented`/`HoldButton`/`Badge`/`StatusChip` in `ui.tsx`, queued toasts in `store.ts`, consolidated status region + LOG badge + reconnect bar + More sheet in `App.tsx`. *(Everything else depends on these primitives — resolves C3 + C1-13/14/21/22.)*
3. **Lane B — Safety core.** `SafetyMonitor` ABC + Alpaca + sim + `"safety"` role + own-cadence poller (`safety_reading`, stale-not-safe) → `_safety_gate`/`_on_unsafe`/`_enforce_mount_floor` (mount-alt, fail-closed) → `SafetyAbort` + shielded wind-down → presets wiring → `/api/safety/*` → header chip live. *(P0 unattended-safety; resolves C1-4/5/6/8/10/11/15, C2-1/2/3/6.)*
4. **Lane C — Autorun.** `schedule.py` (window resolution, polar guard, wrap-interp) → `Schedule`/`Target.schedule` → engine **skip-ahead** loop + `StopTarget` → `/api/sequence/preflight` → SequenceView schedule sub-panel (structured controls), loadPlan backfill, preflight confirm dialog. *(Resolves C1-23/24/25/27/28, C2-7/11/13.)*
5. **Lane D — Alerts.** `alerting.py` (event mapping, no-dedupe-on-state-change, undelivered queue, deadman ping, heartbeat) → `/api/alerts/*` + lifespan → SettingsView **Alerts** + **Escalation** panels + "not verified" banner. *(Resolves C1-16/17/18, C2-9/10/15.)*
6. **Lane E — Report.** `report.py` (append-only, per-filter headline, read-time trends) → engine reporter hooks (quality-before-record, finalize all paths, `report` publish) → `/api/reports/*` → `Trend` in graphs.tsx → ReportView (list/detail, no LIVE) → run-complete deep-link + overflow entry. *(Resolves C1-19/20, C2-8/12.)*
7. **Cross-lane finish.** Meridian ETA chip (`ttf*3600`), watchdog, Alpaca reconnect (off by default, alert-per-attempt), escalation actions live, mobile More sheet wiring, aria-live regions. *(Resolves C2-17, C1-7.)*

### Files created
`server/astrodeck/config.py`, `server/astrodeck/alerting.py`, `server/astrodeck/sequence/schedule.py`, `server/astrodeck/sequence/report.py`, `ui/src/views/SettingsView.tsx`, `ui/src/views/ReportView.tsx`.

### Files modified
`server/astrodeck/devices/base.py`, `server/astrodeck/devices/alpaca.py`, `server/astrodeck/devices/sim.py`, `server/astrodeck/hub.py`, `server/astrodeck/sequence/models.py`, `server/astrodeck/sequence/engine.py`, `server/astrodeck/sequence/__init__.py`, `server/astrodeck/api/app.py`, `ui/src/types.ts`, `ui/src/store.ts`, `ui/src/App.tsx`, `ui/src/views/SequenceView.tsx`, `ui/src/components/ui.tsx`, `ui/src/components/graphs.tsx`, `ui/src/index.css`. (`server/astrodeck/events.py` — no change; bus is already generic.)
