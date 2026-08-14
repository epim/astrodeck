"""Sequence engine: runs an imaging plan autonomously.

Per target: slew → (center) → (autofocus) → start guiding → for each step:
set filter (+ focus offset) → expose ×count, with dithering, periodic/thermal
refocus, meridian-flip handling, and guiding-loss recovery. Pause/resume/abort
safe at frame boundaries; progress is persisted so a crashed run can resume.

Calibration targets (darks/bias/flats) skip slewing, centering, focus and
guiding — they just expose.

Batch 4b (automation/safety §1.9) wires the engine to the landed collaborators
WITHOUT changing any existing ETA/resume bookkeeping:

* a unified **safety gate** (fail-closed: a stale/None reading is UNSAFE) runs
  after every frame-boundary ``_checkpoint`` and before every slew;
* the **mount-altitude floor** (independent of the safety device) guards every
  slew against a pier-collision / setting target;
* **quality-before-record** so a rejected frame can discard/retake its FITS
  *before* ``_done`` advances;
* a **window-sorted skip-ahead scheduler** that walks the first ready target
  (default ``start_mode="now"`` plans behave EXACTLY as before — all ready, in
  insertion order, no waiting);
* a **SessionReporter** recording every frame + safety event, finalized on every
  terminal path with a ``report`` bus publish;
* a **no-progress watchdog** + honest **live ETA** sub-object.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from statistics import median
from typing import Any

from ..config import config_store
from ..devices.base import DeviceError, DomeShutterState, PierSide
from ..events import bus
from ..focus import run_autofocus
from ..guide.base import rms_total_arcsec
from ..hub import Hub
from ..imaging.processing import to_jpeg
from . import schedule
from .cloudstate import CloudState, verdict_from_info
from .instructions import (
    FireRecord, FiredAction, TriggerContext, evaluate_instructions,
)
from .models import ExposureStep, SequencePlan, Target
from .report import FrameRecord, SessionReporter
from .session import Session, SessionFrame, session_store

# --- Monitor / ETA shared constants (single source of truth) ---------------
# The cooler "at target" band. Defined ONCE here (master plan §A.7); the hub
# imports it so the Monitor's at_target flag and the engine's cooling-wait gate
# never drift. The mirror is exported to the UI via lib/eta.ts.
COOLER_AT_TARGET_C = 1.0

#: How long the sensor must STAY inside the band before capture may start.
#:
#: The 2026-08-14 handoff: "no capture stage (loop or cycle) may start or resume
#: unless the sensor is at setpoint and stable (tolerance e.g. ±0.5°C held
#: ~2 min)". The tolerance stays at the measured 1.0 above - the handoff hedges
#: it with "e.g." and that number was tuned against this rig - but the HOLD is
#: the part that was missing, and it is the part that matters: a camera on its
#: way down crosses the band, and a check that returned on the first in-band
#: sample declared "stable" about a sensor still falling.
COOLER_STABLE_S = 120.0
#: How long a sensor may bounce in and out of the band before the settle is
#: declared a failure rather than waited on for ever.
COOLER_SETTLE_MAX_S = 900.0
#: Seconds between temperature probes while cooling.
COOLER_PROBE_EVERY_S = 5.0

ETA_MIN_FRAMES = 3          # ETA stays low-confidence (~) below this
OVERHEAD_EMA_ALPHA = 0.1    # per-frame overhead EMA (low α: one slow frame
                            # doesn't whipsaw the finish clock)
DEFAULT_OVERHEAD_S = 12.0   # seed overhead until real frames are measured
DITHER_COST_S = 8.0         # seed event costs; replaced by measured averages
AF_COST_S = 45.0
FLIP_COST_S = 90.0

# States the run has FINISHED in. "aborting" is deliberately not one: it names a
# teardown that is still running, and every consumer that treats it as terminal
# (the run panel, the Abort control, the monitor banner) blanks itself over a rig
# that has not stopped yet.
_TERMINAL_STATES = frozenset({"complete", "aborted", "error"})

# --- safety/scheduling constants (Batch 4b §1.9) ---------------------------
SAFETY_PAUSE_POLL_S = 5.0       # re-read cadence while paused-for-safety

# --- weather hold (Flows cloud dodge) --------------------------------------
# How long between probe frames while holding for cloud. 120 s is a compromise:
# short enough that a clearing sky is noticed within a couple of minutes, long
# enough that a two-hour hold costs sixty exposures rather than a thousand.
CLOUD_PROBE_EVERY_S = 120.0
# The probe exposure. Short on purpose - the cloud verdict comes from bright
# stars and contrast, both of which a 10 s sub shows plainly, and a held run
# should not be quietly taking science-length exposures nobody asked for.
CLOUD_PROBE_EXPOSURE_S = 10.0
# Consecutive CLEAR probes before the run goes back to work. The frame-level
# debounce in CloudState already suppresses a single lucky gap; this is the
# second, slower gate - it is the difference between resuming on a hole in the
# cloud and resuming on a clearing.
CLOUD_RESUME_CLEAR_PROBES = 2
# Default cap on a hold, in minutes. The HOLD/RESUME node ships 45 with
# "Abort + park" as its timeout action, and that is what this honours.
CLOUD_MAX_HOLD_MIN = 45.0
SAFETY_SEED_WAIT_S = 1.0        # max wait for the own-cadence poller's first read
SAFETY_SEED_STEP_S = 0.05       # poll-cache step while seeding
SLEW_PROJECT_S = 180.0          # pre-slew floor projection margin (slew+solve)
SCHEDULE_WAIT_STEP_S = 5.0      # cancel-responsive sleep while waiting on a window
WATCHDOG_TICK_S = 10.0          # no-progress watchdog wake cadence
RECONNECT_BACKOFF_S = 5.0       # between reconnect_resume attempts on one role
# --- target-jump budget (control-flow expansion) ---------------------------
# Hard per-run ceiling on EXECUTED run_target/skip_target jumps. This is the
# real backstop against a mutual-jump cycle (A -> run B, B -> run A, neither
# ever completing): once the budget is spent, further jumps are IGNORED with a
# warning and the scheduler degrades to normal ordering. Degrade-and-warn, never
# abort — finishing the plan beats killing a real imaging night over a rule typo.
MAX_JUMPS = 64
# --- meridian-flip trigger (server HA countdown) ---------------------------
# Slack added to the next exposure when deciding "would this frame cross the flip
# point?": we never START an exposure that cannot finish (plus download/settle
# slop) before the meridian, matching NINA's wait-don't-straddle semantics.
FLIP_FRAME_MARGIN_S = 30.0
FLIP_WAIT_STEP_S = 5.0          # cancel/pause-responsive hold step near the flip

# --- device-I/O timeout bounds (P0-2) --------------------------------------
# Every engine await on a device call is bounded so a wedged Alpaca/NINA/PHD2
# transport can never hang the night on an unbounded await (review §8d: "the
# single most likely overnight-hang source"). A timeout escalates through the
# normal abort+park path (SafetyAbort → shielded wind-down), never a silent hang.
#
# CAPTURE budget is exposure-relative: the exposure itself plus a generous fixed
# margin for download/save/solve. SLEW/GOTO/PARK get a few minutes (a long meridian
# slew + plate-solve loop, or a harmonic-mount park, can legitimately take minutes).
CAPTURE_MARGIN_S = 120.0        # added to the exposure for download/save/detect
SLEW_TIMEOUT_S = 300.0          # plain slew (+settle)
GOTO_TIMEOUT_S = 420.0          # slew + iterated solve→sync→re-slew centering
PARK_TIMEOUT_S = 240.0          # park / unpark
FLIP_TIMEOUT_S = 420.0          # meridian flip = re-slew + solve + restart guiding
GUIDE_START_TIMEOUT_S = 180.0   # start_guiding incl. settle
GUIDE_OP_TIMEOUT_S = 120.0      # dither / stop-guiding / quick guider ops

#: Guider phases in which the guider is DELIBERATELY COMMANDING THE MOUNT, so a
#: shutter must stay shut. Calibration is the one that cost frames on sky
#: (2026-08-10): it pulses the mount a measured distance on each axis, which is
#: a slew under any other name. "finding" and "guiding" are NOT here — finding
#: only reads frames, and guiding's whole job is to hold the star still.
GUIDE_MOUNT_BUSY_PHASES = frozenset({"calibrating", "settling"})
GUIDE_QUIET_TIMEOUT_S = 240.0   # cap on waiting for the guider to stop pulsing
GUIDE_QUIET_POLL_S = 2.0
COOLER_CMD_TIMEOUT_S = 30.0     # a single set_cooler / get_temperature call
MOUNT_QUERY_TIMEOUT_S = 30.0    # is_parked / set_tracking / time_to_meridian_flip
MOUNT_RECONNECT_TIMEOUT_S = 30.0  # reopening a dropped link so a wind-down can park
FILTER_MOVE_TIMEOUT_S = 90.0    # a filter-wheel slot change (incl. settle)
FOCUSER_MOVE_TIMEOUT_S = 180.0  # a focuser offset move (a big Ha offset can crawl)
CALIBRATOR_CMD_TIMEOUT_S = 30.0  # flat panel on/off / cover move (PRO-5)
FLAT_METER_MAX_S = 8            # trial metering captures cap (belt-and-braces)

#: Re-meter a flat set every N frames when the light source is NOT a panel.
#: A panel is constant and one solve holds for the whole set; the sky is not.
#: Twilight changes brightness by roughly a factor of two every few minutes, so
#: an exposure solved for frame 1 is wrong by frame 20 — the set drifts from
#: correctly exposed to saturated (dawn) or to noise (dusk), and every frame in
#: between is a different flat. 5 keeps the drift inside the solver's own target
#: band without spending more time metering than exposing.
FLAT_RESOLVE_EVERY = 5
DOME_OPEN_TIMEOUT_S = 180.0    # roll-off roof reopen (PRO-4 D3); mirrors roof.py's
                               # DOME_CLOSE_TIMEOUT_S — a real motor run is minutes
DOME_QUERY_TIMEOUT_S = 30.0    # a single shutter_state query during reopen

# --- inter-target teardown -------------------------------------------------
# Before a scheduler wait longer than this we stop tracking (park-hold) so the
# finished target isn't tracked down into the pier/tripod for hours; the next
# ready target's _setup_target restores tracking + re-slews (review §1.9).
WAIT_TEARDOWN_S = 120.0

# --- "if missed: skip" grace (Schedule.on_missed, C1-25) -------------------
# How far a target's frozen start may already be in the past before "skip if
# missed" drops it. MUST be non-zero: a target selected at the very instant its
# window opens is not missed, and float jitter / the 5 s re-evaluation cadence
# must not eat it. Minutes, not seconds, because the PREVIOUS target's last
# exposure + download legitimately runs a few minutes past this one's start —
# a window missed by three minutes is not a missed night.
MISSED_GRACE_S = 300.0

# --- review-thumbnail back-pressure (Task 6 review, Important #2) ----------
# Thumbs are best-effort and MUST NEVER block the capture loop, so there is no
# queue to wait on: once this many renders are already in flight, ``_spawn_
# thumb`` just drops the new one (frame's thumb stays None). This bounds a
# fast calibration burst (sub-second bias/darks) to at most this many
# in-memory full-res ndarray closures at once instead of piling up unbounded.
_MAX_PENDING_THUMBS = 4


async def _bounded(awaitable, timeout_s: float, what: str):
    """Await ``awaitable`` under ``asyncio.wait_for`` (P0-2). On timeout, raise a
    ``SafetyAbort`` so the run tears down through the existing shielded park/warm
    wind-down instead of hanging on an unbounded await. ``CancelledError`` (a real
    user/engine abort) propagates untouched."""
    try:
        return await asyncio.wait_for(awaitable, timeout_s)
    except asyncio.TimeoutError:
        bus.log("error", f"{what} timed out after {timeout_s:.0f}s — aborting", "sequence")
        raise SafetyAbort(f"{what} timed out after {timeout_s:.0f}s")


def _mint_report_id(plan_name: str, started_at: float, taken: list[str]) -> str:
    """Collision-safe per-night report id (spec §4): ``slug-YYYYMMDD-HHMMSS``,
    suffixed ``-2``/``-3``/... when this session already minted that id (same
    plan name resumed within the same second)."""
    rid = SessionReporter._make_id(plan_name or "Tonight", started_at)
    if rid not in taken:
        return rid
    n = 2
    while f"{rid}-{n}" in taken:
        n += 1
    return f"{rid}-{n}"


def _frame_altitude(target, site: dict, when: float) -> float | None:
    """Best-effort target altitude (deg) at capture time; ``None`` on any gap.

    Reuses the grounded ``altaz`` pattern (the mount-floor guard below) with the
    site's lat/lon. Never raises — a missing site key, a ``None`` target, or a bad
    coordinate all fall through to ``None`` so report recording stays best-effort
    (PRO-10 §1.6 Task B)."""
    try:
        from ..catalog import altaz
        lat = site["latitude"]
        lon = site["longitude"]
        alt, _ = altaz(target.ra_hours, target.dec_deg, lat, lon, when)
        return round(float(alt), 2)
    except Exception:
        return None


class SafetyAbort(DeviceError):
    """Raised by the safety gate / mount-floor guard to tear the run down through
    the shielded park/warm wind-down (§1.9-G). A subclass of ``DeviceError`` so
    the existing ``except Exception`` chain would still catch it — but ``_run``
    catches it FIRST, above ``except Exception``, to drive the unsafe path."""


class StopTarget(Exception):
    """Raised to advance the SCHEDULER to the next target (window closed / never
    rises / max-run hit). NEVER aborts the night — the engine catches it in the
    skip-ahead loop, marks the target skipped, and moves on (§1.9-C)."""


class JumpTarget(Exception):
    """Raised by a fired ``run_target``/``skip_target`` instruction to redirect
    the SCHEDULER (control-flow expansion). Mirrors :class:`StopTarget` exactly:
    raised only at a frame boundary in ``_dispatch_actions``, unwinds out of
    ``_run_step`` and the step loop, and is caught by ``_run_scheduled``'s
    per-target try — it NEVER aborts the night."""

    def __init__(self, kind: str, name: str):
        super().__init__(f"{kind} target {name!r}")
        self.kind = kind        # "run" | "skip"
        self.name = name


class NightQualityStop(Exception):
    """Per-night consecutive-reject guard tripped (spec §3): end the night
    early -> session dormant, ``end_reason="quality"``. Caught in ``_run`` as
    its own terminal arm (like SafetyAbort) — never treated as an error."""


class SequenceEngine:
    def __init__(self, hub: Hub):
        self.hub = hub
        # register so the hub's poll_status can read the active plan's
        # meridian_flip setting (Monitor meridian block) without importing here.
        hub.engine = self
        self.plan: SequencePlan | None = None
        self._task: asyncio.Task | None = None
        self._paused = asyncio.Event()
        self._paused.set()  # set = not paused
        self.state: dict[str, Any] = {"state": "idle"}
        self._frames_done = 0
        self._frames_since_dither = 0
        self._frames_since_focus = 0
        self._last_focus_temp: float | None = None
        self._recent_hfr: list[float] = []
        self._rejected = 0
        self._night_rejects = 0   # per-night consecutive-reject counter (spec §3)
        # one-shot latch for the "guide RMS gate can't be judged in arcsec" notice
        self._rms_unit_warned = False
        # Meridian-flip arming latch. A GEM flip is owed only when a target is
        # tracked from EAST across the meridian; a target acquired already-west
        # was slewed counterweight-down on the correct side and needs no flip.
        # Armed per-target in _setup_target (only if acquired east) and cleared
        # after a flip, so the server HA countdown — which stays negative for the
        # whole ~12h the target is west — flips at most ONCE per crossing instead
        # of re-flipping every frame.
        self._flip_armed = False
        self._done: dict[str, int] = {}   # "targetId:stepId" -> frames completed
        self._session: Session | None = None   # live ledger (sessions spec §2)
        # In-flight ~512px review-thumbnail renders (Task 6 review, Important
        # #1). Tracked so abort()/teardown can cancel + await them instead of
        # orphaning fire-and-forget tasks; capped (see _MAX_PENDING_THUMBS) so
        # a fast burst drops renders instead of queuing unbounded ndarrays.
        self._thumb_tasks: set[asyncio.Task] = set()
        self._started_at = 0.0
        # --- paused-aware elapsed + deterministic ETA bookkeeping (spec §5) ---
        self._paused_accum_s = 0.0
        self._pause_started_at: float | None = None
        self._cur_exposure_s = 0.0          # exposure of the in-flight step
        self._frame_started_at = 0.0        # wall time the in-flight exposure began
        self._last_frame_done = 0.0         # wall time the last frame completed
        self._frame_had_event = False       # this frame carried a dither/AF/flip
        self._active_step: tuple[int, int] | None = None
        self._overhead_ema = DEFAULT_OVERHEAD_S
        self._overhead_samples = 0          # real per-frame overhead samples seen
        self._event_costs: dict[str, list[float]] = {}  # measured event durations
        # --- automation / safety collaborators (Batch 4b §1.9) ---------------
        self.reporter: SessionReporter | None = None
        self._report_finalized = False      # idempotency guard (P3-18)
        self._unsafe_streak = 0
        self._safe_streak = 0
        # "armed safety, no monitor" is said once per RUN; initialised here too
        # because _safety_gate is reachable on an engine that was constructed
        # but never started.
        self._warned_no_safety_source = False
        #: "nothing can report a failure" is said once per RUN, same as above.
        self._warned_no_destination = False
        # Set by the no-progress watchdog task, consumed by the RUN task's
        # safety gate (the watchdog cannot act on its own — see _watchdog_check).
        self._watchdog_tripped: str | None = None
        self._last_frame_at = 0.0           # wall time of the last recorded frame
        # Watchdog gate: True ONLY while frames are expected to be flowing (inside
        # the active capture loop). During a slew/center/AF setup, a scheduled
        # inter-target wait, or cooling — all of which legitimately produce no
        # frames for minutes-to-hours while state stays 'running' — this is False,
        # so the no-progress watchdog can't page a false UNSAFE (review §1.9-F).
        self._progress_expected = False
        # Per-run FROZEN {id(target): (start_ts, stop_ts)} windows, set by
        # _run_scheduled. Consulted at every FRAME boundary so a stop/dawn/max-run
        # boundary that passes mid-target actually stops the target (it was only
        # checked at target SELECTION before — the running target shot into dawn).
        self._frozen: dict[int, tuple[float | None, float | None]] = {}
        self._watchdog_task: asyncio.Task | None = None
        self._retakes_per_target: dict[int, int] = {}   # ti -> retakes spent
        # PRO-3 conditional sequencer: per-instruction fire bookkeeping (edge/
        # once/cooldown state), keyed by instruction id. Empty + never touched
        # when plan.instructions == [] (the byte-identical no-op path).
        self._fire_state: dict[str, FireRecord] = {}
        # The debounced sky verdict. Fed by every linear science frame (each one
        # already carries a CloudResult from hub._publish_preview) and by the
        # probe frames a hold takes while the science camera is otherwise idle.
        self._clouds = CloudState()
        self._holding_for_clear = False
        # The step a cloud probe borrows its filter/gain/binning from. Set on
        # every frame so a hold entered mid-step probes the sky through the SAME
        # optics the science frames used - a probe through a different filter
        # scores differently and the debounced state would read that as weather.
        self._hold_step = None
        # Measured ONCE per hold and counted down, so the library walk happens
        # once rather than before every frame. None = not yet measured.
        self._hold_darks_want: int | None = None
        self._hold_darks_taken = 0
        # Executed target-jumps this run (control-flow expansion). Inert at 0
        # unless a run_target/skip_target action actually fires; capped by
        # MAX_JUMPS so a mutual-jump cycle can never spin forever.
        self._jumps_spent = 0
        # Names queued by a ``skip_target`` aimed at a FUTURE target. Skipping a
        # target you are not currently shooting must NOT abandon the one you are
        # (that would silently throw away the rest of its subs), so those skips
        # are recorded here and drained by the scheduler when the target comes
        # up, instead of unwinding the frame loop. A set => idempotent, so a rule
        # that re-fires every frame can never queue work or loop.
        self._pending_skips: set[str] = set()
        self._cfg = None                    # config snapshot taken at start()
        self._dawn_cutoff = False           # scheduler ran out of open windows
        # A teardown is in flight (``abort()`` between its first publish and the
        # terminal one). NOT the same question as ``self.running``, which stays
        # True for the whole wind-down and so cannot tell a live run from one
        # that is being killed. Read by the second-caller guard and by the ETA
        # suppression — see abort() and _set_state().
        self._aborting = False
        # AlertDispatcher for the external dead-man's-switch + progress heartbeat
        # (§1.8/§1.9-F). Injected by the app wiring (set hub.dispatcher); the
        # engine looks it up lazily via self.hub so there's no import cycle. When
        # unset, every dispatcher call is a guarded no-op.
        self.dispatcher = None

    # ----------------------------------------------------------------- control

    def start(self, plan: SequencePlan, *, session: Session | None = None) -> None:
        """Start a run. EVERY start owns a Session (spec §2): a fresh one when
        ``session`` is None (ids were backfilled by pydantic during plan
        validation — the server-side backfill seam), or a re-opened dormant one
        on resume. Resume seeds ``_done`` from the ledger's id-keyed
        ``done_map()`` and appends a FRESH report to ``session.nights`` — one
        immutable report per night (spec §4); windows re-resolve naturally
        because resume is a new run."""
        if self.running:
            raise DeviceError("a sequence is already running")
        self.plan = plan
        resume = session is not None
        if session is None:
            session = Session(name=plan.name or "Tonight",
                              created_ts=time.time(), status="active", plan=plan)
        else:
            session.status = "active"

        # ARMED BY DEFAULT, AND THE RULE IS ABOUT THE ACTIVE SESSION, NOT ABOUT
        # HOW IT GOT THERE. An opt-in flag that must be remembered before every
        # night is a flag that is not set on the night it was needed — and the
        # night it is needed is the one where the PC restarts at 2am and nobody
        # is awake to arm anything.
        #
        # This used to sit inside the `session is None` branch, so only a FRESH
        # run was armed and a RESUMED one inherited whatever flag it happened to
        # carry. Combined with the singleton below, that is worse than it
        # sounds: starting any new session disarms every other one, so a session
        # that was superseded once stayed disarmed forever, and resuming it
        # never put the flag back. A multi-night run — the mode this rig is
        # actually used in — could therefore never auto-resume after its first
        # night.
        #
        # MEASURED 2026-08-11: session 810d46ee was disarmed on 08-10 when a
        # fresh session started, resumed on 08-11 to shoot 165 frames, and after
        # a 01:42 restart sat dormant and unarmed for 25 minutes. resume_arm's
        # tick returns silently when session_store.armed() is None, so there was
        # not even a log line to notice — the whole feature was absent, quietly,
        # on exactly the path it exists for.
        session.auto_resume = True
        try:
            # Server-enforced singleton, same rule as the PATCH route: the
            # active session is THE armed one, so arming it disarms the rest.
            for other in session_store.load_all():
                if other.id != session.id and other.auto_resume:
                    other.auto_resume = False
                    session_store.save(other)
        except Exception:  # noqa: BLE001 - never block a run over bookkeeping
            pass
        self._session = session
        self._done = dict(session.done_map()) if resume else {}
        self._frames_done = sum(self._done.values())
        self._frames_since_dither = 0
        self._frames_since_focus = 0
        self._last_focus_temp = None
        self._recent_hfr = []
        self._rejected = 0
        self._night_rejects = 0
        self._rms_unit_warned = False
        self._flip_armed = False
        self._paused.set()
        self._started_at = time.time()
        self._paused_accum_s = 0.0
        self._pause_started_at = None
        self._cur_exposure_s = 0.0
        self._frame_started_at = 0.0
        self._last_frame_done = 0.0
        self._frame_had_event = False
        self._active_step = None
        self._overhead_ema = DEFAULT_OVERHEAD_S
        self._overhead_samples = 0
        self._event_costs = {}
        # --- automation / safety run state (snapshot config ONCE at run start) ---
        self._cfg = config_store.cfg()
        rid = _mint_report_id(plan.name or "Tonight", self._started_at,
                              session.nights)
        self.reporter = SessionReporter(plan, report_id=rid,
                                        started_at=self._started_at)
        session.nights.append(self.reporter.id)
        session_store.save(session)
        self._report_finalized = False
        self._unsafe_streak = 0
        self._safe_streak = 0
        self._warned_no_safety_source = False
        self._warned_no_destination = False
        self._watchdog_tripped = None
        self._last_frame_at = self._started_at
        self._progress_expected = False
        self._frozen = {}
        self._retakes_per_target = {}
        self._fire_state = {}
        self._clouds = CloudState()
        self._holding_for_clear = False
        self._jumps_spent = 0
        self._pending_skips = set()
        self._dawn_cutoff = False
        self._task = asyncio.create_task(self._run())

    def pause(self) -> None:
        # A teardown is not pausable. ``self.running`` is still True inside it,
        # so without this the route would publish state="paused" over a run
        # whose task is already cancelled — the client's is-live predicates
        # would light Resume on a rig that is winding down and never comes back.
        if self._aborting:
            return
        self._paused.clear()
        if self._pause_started_at is None:
            self._pause_started_at = time.time()
        # SAY WHO PAUSED IT. This used to pass state alone, and `_set_state`
        # merges — so a manual pause kept whatever sentence was already on
        # screen. Reported from the rig 2026-08-11, and the misleading case is
        # not the harmless one: `_pause_unsafe` writes "paused (unsafe): <reason>",
        # so pressing Pause after a weather hold had cleared left a rig that is
        # waiting for a PERSON reading as one waiting for the SKY. The natural
        # response to that sentence is to go to bed and let it recover itself,
        # and it never will.
        #
        # "you" rather than "the operator": whoever is reading this is the one
        # who pressed it, and a run does not resume until they say so.
        self._set_state(state="paused", detail="paused by you — it waits here "
                                               "until you resume it")

    def resume(self) -> None:
        # Same window, worse consequence: a resume during the teardown would
        # publish state="running" (with a full live ETA) for a run that has been
        # cancelled, which un-hides every control the abort just took away and
        # restarts a finish clock counting down to nothing.
        if self._aborting:
            return
        if self._pause_started_at is not None:
            self._paused_accum_s += time.time() - self._pause_started_at
            self._pause_started_at = None
        self._paused.set()
        # And clear it on the way out: a reason must not outlive the pause it
        # explains, or a running rig carries "paused by you" until the next
        # frame boundary overwrites it. Same discipline `schedule=None` already
        # has — an explicit clear, not a hope that something else will.
        self._set_state(state="running", detail="")

    # ------------------------------------------------------- elapsed / ETA math

    def _elapsed_s(self) -> float:
        """Wall-clock seconds since start, EXCLUDING time spent paused, so the
        progress bar's elapsed/rate never ticks through a pause (spec §5.1)."""
        paused = self._paused_accum_s
        if self._pause_started_at is not None:
            paused += time.time() - self._pause_started_at
        return max(0.0, time.time() - self._started_at - paused)

    def _event_cost(self, kind: str, default: float) -> float:
        """Measured rolling average for an event type, or the seed until one is
        observed (so a guessed flip cost is replaced by the real one — spec §5.2)."""
        seen = self._event_costs.get(kind)
        if seen:
            return sum(seen) / len(seen)
        return default

    def _record_event_cost(self, kind: str, seconds: float) -> None:
        if seconds <= 0:
            return
        self._event_costs.setdefault(kind, []).append(float(seconds))
        self._event_costs[kind] = self._event_costs[kind][-10:]

    def _remaining_capture_s(self) -> float:
        """Pure capture seconds still owed, EXCLUDING the in-flight frame (that
        is counted once as ``in_flight``). Off-by-one guard for the ETA assembly
        (spec §5.3): for the step in flight, only frames *after* the current one
        are summed here."""
        if not self.plan:
            return 0.0
        total = 0.0
        for ti, target in enumerate(self.plan.targets):
            for si, step in enumerate(target.steps):
                done = self._done.get(f"{target.id}:{step.id}", 0)
                remaining = max(0, step.count - done)
                # the in-flight frame belongs to the step currently capturing; we
                # subtract one frame's worth there because it is accounted as the
                # `in_flight` term in the ETA (off-by-one guard, spec §5.3).
                # _is_active_step precisely identifies that one step.
                if remaining and self._frame_started_at > 0 \
                        and self._is_active_step(ti, si):
                    remaining -= 1
                total += remaining * step.exposure_s
        return total

    def _is_active_step(self, ti: int, si: int) -> bool:
        """True for the single step whose frame is currently exposing."""
        return getattr(self, "_active_step", None) == (ti, si)

    def compute_eta(self) -> dict:
        """Deterministic ETA assembly (spec §5.2/§5.3). Split into pure capture
        seconds + a low-α per-frame overhead EMA + analytically-counted event
        costs (dither/AF/flip), each measured once observed. Returns the progress
        sub-dict's ETA fields; never raises."""
        if not self.plan:
            return {}
        total = self.plan.total_frames()
        frames_remaining = max(0, total - self._frames_done)

        remaining_capture_s = self._remaining_capture_s()
        in_flight = 0.0
        if self._frame_started_at > 0 and self._cur_exposure_s > 0:
            in_flight = max(0.0, self._cur_exposure_s
                            - (time.time() - self._frame_started_at))

        d_every = self.plan.dither_every or 0
        af_every = self.plan.autofocus_every or 0
        dithers_remaining = (frames_remaining // d_every) if d_every else 0
        refocus_remaining = (frames_remaining // af_every) if af_every else 0
        # only count a flip that actually falls inside the remaining run window
        # (a meridian hours away must not inflate a short run's ETA — spec §5.2).
        remaining_window_s = (remaining_capture_s + in_flight
                              + frames_remaining * self._overhead_ema
                              + dithers_remaining * self._event_cost("dither", DITHER_COST_S)
                              + refocus_remaining * self._event_cost("autofocus", AF_COST_S))
        flip_pending = 1 if self._flip_pending(remaining_window_s) else 0
        events_cost_s = (
            dithers_remaining * self._event_cost("dither", DITHER_COST_S)
            + refocus_remaining * self._event_cost("autofocus", AF_COST_S)
            + flip_pending * self._event_cost("flip", FLIP_COST_S))

        eta_s = (remaining_capture_s + in_flight
                 + frames_remaining * self._overhead_ema + events_cost_s)

        events_measured_ok = not (
            (dithers_remaining and "dither" not in self._event_costs)
            or (refocus_remaining and "autofocus" not in self._event_costs)
            or (flip_pending and "flip" not in self._event_costs))
        eta_confident = (self._overhead_samples >= ETA_MIN_FRAMES
                         and events_measured_ok)
        return {
            "eta_s": round(eta_s),
            "eta_confident": bool(eta_confident),
            "server_now_ms": round(time.time() * 1000),
            "current_exposure_s": round(self._cur_exposure_s, 3),
            "frame_started_at_ms": round(self._frame_started_at * 1000)
            if self._frame_started_at > 0 else None,
            "remaining_capture_s": round(remaining_capture_s),
            "events_cost_s": round(events_cost_s),
        }

    def _flip_pending(self, remaining_window_s: float) -> bool:
        """Whether a meridian flip is owed *within* the remaining run window.

        Reads the hub's last server-computed meridian (sync, no device I/O): a
        flip counts only when the plan enables it, the mount is a GEM, and the
        flip is due before the run would finish — so a meridian hours away never
        inflates a short run's ETA (spec §5.2)."""
        if not (self.plan and self.plan.meridian_flip):
            return False
        mer = getattr(self.hub, "last_meridian", None) or {}
        if not mer.get("flip_enabled"):
            return False
        ttf_h = mer.get("hours_to_flip")
        if ttf_h is None:
            return False
        # Strictly the spec §5.2 window gate: a flip is a *future* cost only when
        # it falls after now and before the run would finish. A flip "due" in the
        # past (ttf < 0, target already west of the meridian) is not a pending
        # cost for this run — the engine flips at most once at the boundary, and
        # only when the device reports a crossing; an already-passed meridian on
        # sim/Alpaca never fires. This keeps a far-away meridian from inflating
        # the ETA of a short run.
        return 0 < (ttf_h * 3600.0) <= remaining_window_s

    async def abort(self) -> None:
        # SAY IT BEFORE DOING IT. Everything below -- cancelling the run, its
        # `_safe_stop` (abort the exposure, stop the guider, panel/cover off),
        # the report finalize, then draining every in-flight thumbnail render --
        # is bounded but generously so: ~210 s of device I/O worst case, against
        # the 15 s cap on the POST that is awaiting it. Publishing only the
        # terminal state meant the caller's sole feedback was its own timeout, so
        # an abort that WORKED came back as "server not responding".
        #
        # Same two-step as the polar session's pausing/paused (polar/session.py):
        # publish what is true NOW -- a teardown is running and the rig has not
        # stopped yet -- and let the terminal state land when it actually has.
        # NOTHING below this block changes: the wind-down's ordering and its
        # timeouts are safety-critical and hardware-verified.
        #
        # ALREADY TEARING DOWN -> NO-OP. `self._task and not self._task.done()`
        # stays true for the WHOLE wind-down, so it cannot tell a first abort
        # from a second one. And a second one is not hypothetical: the lock
        # screen's emergency STOP posts this route on every press and is
        # deliberately never disabled (TouchGuard.tsx), and the POST's own 15 s
        # cap expires long before the teardown does, so the operator sees a
        # "failed" abort and presses again.
        #
        # A second `cancel()` lands on a task that is already inside
        # `except asyncio.CancelledError: await self._safe_stop()`, and raises a
        # BaseException that `_safe_stop`'s `except (TimeoutError, Exception)`
        # cannot catch: the teardown dies after abort_exposure and never reaches
        # stop_guiding / the flat panel / _finalize_report. The guider keeps
        # guiding, the panel stays lit, the session is never marked dormant --
        # a second press of STOP would leave the rig in a worse state than one.
        #
        # Same guard as PolarAlignSession.pause()'s `if self._native_paused:
        # return` (polar/session.py), for the same reason: a REST client or a
        # double tap through a slow round trip must not re-arm the state.
        if self._aborting:
            return
        self._aborting = True
        try:
            # The intermediate state is guarded on a LIVE task, like `pause()`'s
            # "nothing to pause": announcing a teardown that isn't running
            # renders a wind-down over an idle rig...
            if self._task and not self._task.done():
                # ...unless the run has ALREADY published a terminal state and is
                # only finishing a shielded wind-down (the SafetyAbort path parks
                # the mount inside `asyncio.shield`). Rewriting that frame back to
                # "aborting" would replace `aborted / end_reason=unsafe` with a
                # detail that disclaims the very park that is in flight.
                if self.state.get("state") not in _TERMINAL_STATES:
                    self._set_state(
                        state="aborting",
                        detail="stopping the run — ending the exposure and "
                               "the guider. A device that has stopped "
                               "answering can hold this for a few minutes.")
                self._task.cancel()
                try:
                    await self._task
                except (asyncio.CancelledError, Exception):
                    pass
            # the main run is now fully stopped, so no NEW thumb can be spawned
            # concurrently -- this always drains exactly the pending set (Task 6
            # review, Important #1: no orphaned renders / "destroyed but pending"
            # warnings at interpreter exit).
            await self._drain_thumb_tasks()
            self._set_state(state="aborted", detail="sequence aborted",
                            schedule=None, session=None)
        finally:
            # Cleared only after the TERMINAL state is on the wire, so the window
            # the flag names is exactly the window the clients see "aborting" in.
            self._aborting = False

    async def _drain_thumb_tasks(self) -> None:
        """Cancel and await every in-flight thumbnail render (Task 6 review,
        Important #1). Bounded: ``return_exceptions=True`` means a wedged or
        erroring render can never hang teardown -- cancellation of an
        ``asyncio.to_thread`` await resolves promptly even while its
        underlying OS thread keeps running to completion in the background."""
        if not self._thumb_tasks:
            return
        pending = list(self._thumb_tasks)
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        self._thumb_tasks.difference_update(pending)

    async def drain_thumbs_for_session(self, session_id: str) -> None:
        """Cancel + await any in-flight thumb renders writing into
        ``session_id`` (Task 7 DELETE race). Natural completion — unlike
        ``abort()`` — does NOT drain thumb tasks, so a render can still be
        mid-write when the DELETE route rmtree's the thumbs dir; worse, the
        render's trailing ``session_store.save`` would RESURRECT the JSON we
        are about to delete. The DELETE route calls this BEFORE removing
        anything. Only ever drains renders left from a finished/dormant run —
        the actively-running session is 409'd by the route, never deleted.

        Cancelling before the render's next ``await`` means it never reaches the
        save (``CancelledError`` is a ``BaseException``, uncaught by the render's
        ``except Exception``); a render already past its last await completes,
        and ``gather`` waits for it, so the save-then-delete order can't race."""
        pending = [t for t in list(self._thumb_tasks)
                   if getattr(t, "_astro_session_id", None) == session_id]
        if not pending:
            return
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        self._thumb_tasks.difference_update(pending)

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def paused(self) -> bool:
        return not self._paused.is_set()

    # ------------------------------------------------------------------- state

    def _sky_state(self) -> dict:
        """The sky verdict, as the UI is allowed to see it.

        PUBLISHED BECAUSE IT IS OTHERWISE INVISIBLE. The verdict lives in the
        capture payload and in this engine's CloudState, and nothing carried it
        anywhere a client could read - so a run that held for cloud could say
        "holding" and give the operator no way to see WHY, or to tell a hold
        that is watching a real overcast from one sitting on a reading that went
        stale twenty minutes ago.

        `cloudy` is tri-state and null means UNKNOWN, never "clear". `age_s` is
        published beside it precisely so a client can render the difference; a
        UI that showed a bare "clear" for a reading nobody had taken would be
        the same lie in a different place.
        """
        now = time.time()
        return {
            "cloudy": self._clouds.cloudy(now),
            "age_s": (round(a) if (a := self._clouds.age_s(now)) is not None
                      else None),
            "score": self._clouds.last_score,
            "reason": self._clouds.last_reason,
            "text": self._clouds.describe(now),
            "holding": self._holding_for_clear,
        }

    def _set_state(self, **kw: Any) -> None:
        # A HOLD OUTRANKS A ROUTINE "running" PUBLISH.
        #
        # Observed live on 2026-08-12 23:15, under solid overcast: the run read
        # `state: running` while it was holding for cloud and shooting hold
        # darks. `_hold_for_clear` had published state="holding", and then the
        # calibration frame loop's own per-frame publish overwrote it — so the
        # ONE field every client keys off said the night was proceeding
        # normally. Only the `sky.holding` flag disagreed, and a watchdog
        # watching `state` for a hold could never have fired.
        #
        # Gated on the caller NOT naming `hold`: the deliberate resume passes
        # `hold=None` to say the hold is over, and that has to get through while
        # `_holding_for_clear` is still True (it is cleared in a `finally`).
        # A publish that says nothing about the hold is not entitled to end it.
        if (getattr(self, "_holding_for_clear", False)
                and kw.get("state") == "running" and "hold" not in kw):
            kw["state"] = "holding"
            kw.setdefault("hold", "clouds")
        if self.plan:
            total = self.plan.total_frames()
            kw.setdefault("sky", self._sky_state())
            progress = {
                "frames_done": self._frames_done,
                "frames_total": total,
                "percent": round(100 * self._frames_done / total, 1) if total else 0,
                "elapsed_s": round(self._elapsed_s()),   # paused-aware (spec §5.1)
                "rejected": self._rejected,
            }
            # deterministic ETA fields — only while a run is live (idle/complete
            # carry no honest finish). compute_eta never raises.
            #
            # A teardown is live by `self.running` (the task is inside its own
            # cancellation) but nothing about the plan will finish, so a finish
            # clock there counts down to something already cancelled.
            #
            # Keyed on the TEARDOWN, not on this call's state string: a
            # _set_state that omits `state=` merges into a state that is still
            # "aborting", and re-attached a full live eta_s to it.
            if self.running and not self._aborting:
                try:
                    progress.update(self.compute_eta())
                except Exception:
                    pass
            kw.setdefault("progress", progress)
            kw.setdefault("plan_name", self.plan.name)
            # live ETA chips sub-object (honest temps; meridian ETA in seconds —
            # ttf is HOURS, so * 3600, only when within the warn lead time).
            live = self._live_block()
            if live:
                kw.setdefault("live", live)
        # ``_first_running`` is a ONE-SHOT edge flag for the run_start alert — it
        # must NOT be persisted into self.state (else every later _set_state
        # re-publishes it and the dispatcher fires "Run started" on every frame).
        # Pop it before merging and add it ONLY to this single publish payload.
        first_running = kw.pop("_first_running", False)
        # session sub-state (sessions spec §6): {id, name, count_mode, accepted,
        # target}, cleared with the same explicit-None semantics as `schedule`
        # so it never outlives the run (terminal transitions pass session=None).
        if "session" in kw and kw["session"] is None:
            kw = {k: v for k, v in kw.items() if k != "session"}
            self.state.pop("session", None)
        elif self._session is not None and self.plan is not None:
            kw.setdefault("session", {
                "id": self._session.id,
                "name": self._session.name,
                "count_mode": getattr(self.plan, "count_mode", "attempts"),
                "accepted": self._session.total_accepted(),
                "target": kw.get("target", self.state.get("target")),
            })
        # schedule=None is an explicit CLEAR (wave-3 §2): the waiting sub-state
        # must not outlive the wait it describes (GET /api/sequence/state and the
        # monitor snapshot both serve this dict verbatim).
        if "schedule" in kw and kw["schedule"] is None:
            kw = {k: v for k, v in kw.items() if k != "schedule"}
            self.state.pop("schedule", None)
        self.state = {**self.state, **kw}
        payload = dict(self.state)
        if first_running:
            payload["_first_running"] = True
        bus.publish("sequence", **payload)

    def _live_block(self) -> dict | None:
        """The ``live`` sub-object (§1.9-E): a meridian-flip ETA in *seconds*
        (``ttf * 3600`` — ttf is reported in HOURS) when the flip falls within
        ``meridian_flip_warn_min``, plus honest current temps (no cooling ETA).
        Best-effort, sync, no device I/O (reads the hub's cached meridian)."""
        if not self.plan:
            return None
        live: dict[str, Any] = {}
        mer = getattr(self.hub, "last_meridian", None) or {}
        if self.plan.meridian_flip and mer.get("flip_enabled"):
            ttf_h = mer.get("hours_to_flip")
            if ttf_h is not None and ttf_h > 0:
                warn_min = getattr(self.plan, "meridian_flip_warn_min", 15.0) or 0.0
                if ttf_h * 60.0 <= warn_min:
                    # ttf is HOURS — convert to seconds for the chip (C2-17).
                    live["meridian_eta_s"] = round(ttf_h * 3600.0)
        # honest current sensor temp only (no cooling ETA — C2-4). Read from the
        # last frame if available, else omit.
        frame = getattr(self.hub, "last_frame", None)
        t = getattr(frame, "temperature_c", None) if frame else None
        if t is not None:
            live["sensor_temp_c"] = round(float(t), 1)
        return live or None

    async def _checkpoint(self) -> None:
        """Frame-boundary gate: honors pause and cancellation."""
        await self._paused.wait()

    def _get_dispatcher(self):
        """Resolve the AlertDispatcher (injected on the engine or the hub). Returns
        None when unset, so all dead-man's-switch / heartbeat calls are no-ops."""
        return self.dispatcher or getattr(self.hub, "dispatcher", None)

    async def _frame_alerts_tick(self) -> None:
        """Per-frame §1.9-F: GET the external dead-man's-switch each frame (its
        ABSENCE is what pages the user — C2-9) and emit a progress heartbeat (the
        dispatcher self-gates it per-sink by ``heartbeat_min``). Best-effort: both
        dispatcher calls are already hardened never to raise, but guard anyway so
        an alerting hiccup can never break the capture loop."""
        disp = self._get_dispatcher()
        if disp is None:
            return
        try:
            await disp.deadman_ping()
        except Exception:
            pass
        try:
            await disp.emit_heartbeat(
                f"{self.plan.name if self.plan else 'run'}: "
                f"{self._frames_done}/"
                f"{self.plan.total_frames() if self.plan else 0} frames")
        except Exception:
            pass

    # ------------------------------------------------------------------- run

    async def _run(self) -> None:
        plan = self.plan
        assert plan is not None
        try:
            self._set_state(state="running", detail=f"starting plan '{plan.name}'",
                            _first_running=True)
            # "integration" = LIGHT exposure only (UX #39); calibration steps are
            # shutter time, not signal on the target.
            bus.log("info", f"sequence '{plan.name}' started: {plan.total_frames()} frames, "
                            f"{plan.light_seconds() / 60:.0f} min integration", "sequence")
            self._start_watchdog()

            if plan.cool_to is not None:
                cooled = await self._cool_and_wait(plan.cool_to, plan.cool_timeout_s)
                # P1-7: require_cooling + cooling_action == "skip" → don't shoot
                # warm lights. (abort raised SafetyAbort inside _cool_and_wait;
                # warn/not-required returned and we proceed as before.)
                cfg = self._cfg
                if (not cooled and cfg and cfg.escalation.require_cooling
                        and cfg.escalation.cooling_action == "skip"):
                    self._set_state(state="complete",
                                    detail="skipped: camera did not reach target temp",
                                    end_reason="cooling_skip", schedule=None,
                                    session=None)
                    bus.log("warning", f"sequence '{plan.name}' skipped — cooling "
                                       "required but not reached", "sequence")
                    self._finalize_report("cooling_skip")
                    await self._wind_down(
                        plan.park_when_done, plan.warm_cooler_when_done,
                        close_dome=bool(self._cfg
                                        and self._cfg.safety.close_dome_when_done))
                    return

            await self._run_scheduled(plan)

            if self._dawn_cutoff:
                # the scheduler ran out of open windows (every remaining target's
                # window closed / never rose) — finalize as a dawn cutoff (§1.9-C).
                self._set_state(state="complete", detail="stopped at dawn (windows closed)",
                                end_reason="dawn_cutoff", schedule=None, session=None)
                bus.log("info", f"sequence '{plan.name}' stopped at dawn: "
                                f"{self._frames_done} frames", "sequence")
                self._finalize_report("dawn_cutoff")
            else:
                self._set_state(state="complete", detail="all targets complete",
                                schedule=None, session=None)
                bus.log("info", f"sequence '{plan.name}' complete: {self._frames_done} frames"
                                + (f", {self._rejected} flagged" if self._rejected else ""),
                        "sequence")
                self._finalize_report("complete")
            await self._wind_down(
                plan.park_when_done, plan.warm_cooler_when_done,
                close_dome=bool(self._cfg and self._cfg.safety.close_dome_when_done))
        except SafetyAbort as e:
            # UNSAFE teardown (§1.9-G): aborted + end_reason=unsafe, finalize the
            # report, then a SHIELDED park/warm that ACTUALLY COMPLETES before the
            # cancellation takes effect — a concurrent UI abort (self._task.cancel)
            # must not orphan the park mid-slew. We run the wind-down as an explicit
            # task and re-await it across any CancelledError raised into this frame,
            # so the inner park/warm finishes before we re-raise (§1.9-G "completes
            # before cancellation takes effect").
            bus.log("error", f"sequence stopped (unsafe): {e}", "sequence")
            self._set_state(state="aborted", detail=str(e), end_reason="unsafe",
                            schedule=None, session=None)
            self._finalize_report("unsafe")
            wind = asyncio.ensure_future(self._wind_down(
                park=True,
                warm=(self._cfg is not None and self._cfg.safety.on_unsafe == "abort_park_warm"),
                close_dome=bool(self._cfg and self._cfg.safety.close_dome_on_unsafe)))
            cancelled = False
            while not wind.done():
                try:
                    await asyncio.shield(wind)
                except asyncio.CancelledError:
                    # a UI abort cancelled THIS frame — keep waiting on the shielded
                    # wind-down so park/warm completes, then propagate the cancel.
                    cancelled = True
                except Exception:
                    break
            if cancelled:
                raise asyncio.CancelledError()
        except NightQualityStop as e:
            # per-night reject guard (spec §3): end the night early — the same
            # complete+end_reason terminal shape as dawn_cutoff, alerting via
            # the existing log→dispatcher path (error level reaches all sinks).
            bus.log("error", f"sequence '{plan.name}' stopped early — {e} "
                             "(clouds?)", "sequence")
            self._set_state(state="complete",
                            detail="stopped early: consecutive quality rejects",
                            end_reason="quality", schedule=None, session=None)
            self._finalize_report("quality")
            await self._wind_down(
                plan.park_when_done, plan.warm_cooler_when_done,
                close_dome=bool(self._cfg and self._cfg.safety.close_dome_when_done))
        except asyncio.CancelledError:
            bus.log("warning", "sequence aborted", "sequence")
            await self._safe_stop()
            self._finalize_report("aborted")
            raise
        except Exception as e:
            bus.log("error", f"sequence failed: {e}", "sequence")
            self._set_state(state="error", detail=str(e), schedule=None, session=None)
            await self._safe_stop()
            self._finalize_report("error")
        finally:
            self._stop_watchdog()

    def _finalize_report(self, reason: str) -> None:
        """Finalize the session report (guard None) and publish a ``report``
        event on EVERY terminal path (§1.9-G).

        Idempotent per run (P3-18): if ``_wind_down`` raises AFTER a successful
        finalize on the complete/dawn path, the outer ``except Exception`` would
        otherwise re-finalize with end_reason='error' — overwriting 'complete' and
        publishing a contradictory second ``report`` event. The first finalize
        wins; later calls are no-ops."""
        if self._report_finalized:
            return
        self._report_finalized = True
        rid = None
        if self.reporter is not None:
            try:
                self.reporter.finalize(reason)
            except Exception as e:
                bus.log("warning", f"report finalize failed: {e}", "sequence")
            rid = self.reporter.id
        bus.publish("report", id=rid)
        # ---- session terminal transition (spec §4) ---------------------------
        # 'complete' ONLY when the run finished naturally with no unmet quota
        # (in accepted mode); EVERY other cause — dawn_cutoff / window_closed /
        # max_run (both surface as dawn_cutoff here) / aborted / error / unsafe
        # / cooling_skip / quality — leaves unmet work -> dormant + resumable.
        if self._session is not None:
            quota = getattr(self._session.plan, "count_mode", "attempts") == "accepted"
            unmet = quota and any(v > 0 for v in self._session.remaining().values())
            self._session.status = ("complete"
                                    if reason == "complete" and not unmet
                                    else "dormant")
            try:
                session_store.save(self._session)
            except Exception as e:
                bus.log("warning", f"session save failed: {e}", "sequence")
            self._session = None

    async def _run_scheduled(self, plan: SequencePlan) -> None:
        """Window-sorted skip-ahead scheduler (§1.9-C).

        BACKWARD COMPAT: every default-schedule target (``start_mode="now"``,
        no stop, no altitude gate) resolves ``gating_status`` to ``ready`` at
        ``now`` immediately, ``schedule_order`` is a stable sort that preserves
        insertion order for equal (all-"now") starts, so a legacy plan walks its
        targets in the original order with no waiting — identical to the old
        ``for ti, target in enumerate(plan.targets)`` loop. The per-target
        ``_done`` skip and calibration handling are preserved inside.
        """
        cfg = self._cfg
        site = self.hub.site
        twilight = cfg.safety.twilight_deg if cfg else -12.0

        # stable original index per target object so reporter/_set_state keep
        # referring to the PLAN order, not the sorted order (resume + skip
        # semantics depend on the plan index). ``_done`` itself is keyed by
        # "<target.id>:<step.id>" (id-keyed, not by this index).
        index_of = {id(t): i for i, t in enumerate(plan.targets)}
        self._warn_if_nothing_can_report_a_failure()
        run_start = time.time()
        order = schedule.schedule_order(plan.targets, site, twilight, run_start)
        remaining = list(order)
        # Freeze each target's [start, stop] window ONCE, at run start (§1.6). The
        # scheduler compares the LIVE now against this frozen pair for the rest of
        # the night — so a dawn/stop boundary that passes mid-run actually CLOSES
        # the window. Re-resolving every tick (the bug) rolls a past dawn forward
        # to tomorrow, so window_closed / dawn_cutoff / max_run were unreachable
        # and a dusk-start evaluated after dusk waited ~23h. resolve_window now
        # searches backward too, so a dusk already past still opens tonight.
        frozen = {id(t): schedule.resolve_window(t.schedule, site, twilight,
                                                 run_start)
                  for t in plan.targets}
        # publish the frozen windows so _run_step can re-check each target's stop
        # boundary at every frame boundary (not just at selection — the running
        # target used to shoot straight through dawn / past max_run_min).
        self._frozen = frozen

        while remaining:
            await self._checkpoint()
            # Drain skips queued by a skip_target aimed at a FUTURE target (the
            # current target was left running on purpose). Pure list surgery, no
            # device I/O. Names are matched against what is still remaining, so
            # an unknown/already-gone name is a harmless no-op.
            if self._pending_skips:
                for _t in [t for t in remaining if t.name in self._pending_skips]:
                    bus.log("info", f"{_t.name}: skipped by instruction", "sequence")
                    if self.reporter:
                        self.reporter.mark_skipped(_t)
                    remaining.remove(_t)
                self._pending_skips.clear()
                if not remaining:
                    break
            now = time.time()
            ready = None
            earliest = None         # (start_ts, target) of the soonest waiter
            all_closed = True
            for target in remaining:
                gs = schedule.gating_status(target, site, twilight, now,
                                            window=frozen.get(id(target)))
                state = gs["state"]
                if state == "ready":
                    ready = target
                    all_closed = False
                    break
                if state in ("window_closed", "never_rises"):
                    continue        # this one will be skipped below
                # waiting (clock or altitude)
                all_closed = False
                start_ts = gs.get("start_ts")
                if start_ts is not None and (earliest is None or start_ts < earliest[0]):
                    earliest = (start_ts, target, gs)

            if ready is not None:
                ti = index_of[id(ready)]
                complete = self._target_complete(ti, ready)
                # "if missed: skip" (Schedule.on_missed). The target is runnable
                # NOW, but its own window opened long ago and the night moved on —
                # the user asked us to move on with it. Pure list surgery, no
                # device I/O, so it is safe here. An already-complete target is
                # reported as complete, not as missed.
                if not complete and self._missed_start(ready, now, site, twilight):
                    late_min = (now - (frozen[id(ready)][0] or now)) / 60.0
                    bus.log("info", f"{ready.name}: start window opened "
                                    f"{late_min:.0f} min ago — skipping (if missed: "
                                    f"skip)", "sequence")
                    if self.reporter:
                        self.reporter.mark_skipped(ready)
                    remaining.remove(ready)
                    continue
                try:
                    if complete:
                        bus.log("info", f"{ready.name}: already complete — skipping",
                                "sequence")
                    elif ready.calibration:
                        await self._run_calibration(ti, ready)
                    else:
                        await self._setup_target(ti, ready)
                        await self._run_steps(ti, ready)
                        # PRO-3: on_target_complete eval for a finished
                        # non-calibration target. Guarded (no-op when empty).
                        if self.plan and self.plan.instructions:
                            await self._run_instructions(
                                TriggerContext(now_ts=time.time(),
                                               target_complete=True,
                                               active_target=ready.name),
                                ready, None)
                except StopTarget as e:
                    # scheduling-only stop: skip this target, keep the night going.
                    bus.log("info", f"{ready.name}: skipped — {e}", "sequence")
                    if self.reporter:
                        self.reporter.mark_skipped(ready)
                except JumpTarget as j:
                    # control-flow expansion: a fired run_target/skip_target
                    # instruction redirects the scheduler. Like StopTarget this
                    # NEVER ends the night — an unknown/degenerate jump is a
                    # logged no-op and the normal ordering carries on. _apply_jump
                    # answers whether the ACTIVE target was consumed: on a no-op
                    # it is NOT, so it stays in `remaining` and is re-selected
                    # (resuming from its persisted per-step counts) instead of
                    # being silently dropped by the removal below.
                    if not self._apply_jump(plan, j, ready, remaining):
                        continue
                remaining.remove(ready)
                continue

            # none ready: every remaining target is either waiting or past.
            if all_closed:
                # all remaining windows are closed / never rise — mark them and
                # finalize the night as a dawn cutoff (§1.9-C).
                for target in remaining:
                    if self.reporter:
                        self.reporter.mark_skipped(target)
                    bus.log("info", f"{target.name}: window closed — skipping", "sequence")
                self._dawn_cutoff = True
                return

            # at least one target is waiting on its window: announce + wait.
            if earliest is not None:
                start_ts, wtarget, gs = earliest
                ti = index_of[id(wtarget)]
                self._set_state(
                    state="running", target=wtarget.name, target_index=ti,
                    detail=f"waiting for {wtarget.name}",
                    schedule={"state": "waiting", "reason": gs.get("reason", ""),
                              "eta_s": round(gs.get("eta_s", 0.0)),
                              "start_ts": gs.get("start_ts"),
                              "stop_ts": gs.get("stop_ts")})
                # Between-target teardown (review §1.9): a previous target left the
                # mount TRACKING its coordinates. Before a long wait, stop tracking
                # (park-hold) so we don't track a finished, setting target down into
                # the pier/tripod for hours; the next ready target's _setup_target
                # re-slews + restores tracking. Skip the teardown for short re-eval
                # waits (the mount is about to move again).
                # THE ANCHOR MAY ALREADY BE PAST. ``start_ts`` is when the
                # target's window OPENED, and a target can sit un-ready long
                # after that — its time window is open while it is still below
                # its altitude gate. Waiting until an opening that has already
                # happened is not a wait, so fall forward onto the scheduler's
                # own estimate of when the target becomes usable.
                wait_ts = max(start_ts, now + float(gs.get("eta_s") or 0.0))
                # Teardown keys off the EFFECTIVE wait for the same reason: with
                # the past anchor this test read "0 seconds from now" and never
                # park-held, so the mount kept tracking a finished target while
                # the scheduler span.
                if wait_ts - now > WAIT_TEARDOWN_S:
                    await self._park_hold()
                await self._wait_until(wait_ts)
            else:
                # waiting but no resolvable start_ts (e.g. below-alt with unknown
                # ETA): a short bounded, cancel-responsive sleep then re-evaluate.
                await self._wait_until(time.time() + SCHEDULE_WAIT_STEP_S)

        # loop exhausted naturally → all targets ran (or were skipped above).

    def _apply_jump(self, plan: SequencePlan, j: "JumpTarget", ready: Target,
                    remaining: list[Target]) -> bool:
        """Apply a caught :class:`JumpTarget` to the scheduler's ``remaining``
        queue (control-flow expansion). Pure list surgery + logging — no device
        I/O, so it is safe in the scheduler's except arm.

        RETURNS whether the ACTIVE target was consumed, i.e. whether the caller
        should drop ``ready`` from ``remaining``. The jump arrives as an
        exception at a frame boundary, so the active target's frame loop has
        already unwound; only this answer decides whether it is abandoned or
        picked back up (per-step counts live in ``self._done``, so re-selecting
        it RESUMES rather than restarts, and ``MAX_JUMPS`` bounds a jump that
        keeps re-firing).

        * ``True``  — ``run`` to a different target (abandon-and-jump) and
          ``skip`` aimed at the target being shot. Both mark ``ready`` skipped.
        * ``False`` — the documented no-ops: an unknown target name (a typo must
          not silently cost the user the target that is shooting) and ``run`` to
          the ACTIVE target (the trivial self-loop). The night carries on with
          ``ready`` exactly where it was.

        A ``skip`` aimed at some OTHER (future) target never reaches here at all
        — ``_dispatch_actions`` queues it in ``_pending_skips`` and the scheduler
        drains it, so the running target keeps shooting.

        ``skip``: drop the named target from the night (no-op when it is already
        gone or unknown). ``run``: move the named target to the FRONT so it is
        the next one selected; a jump to the ACTIVE target is a deliberate no-op
        (the trivial self-loop). Names are matched by identity within
        ``plan.targets`` so duplicate-valued targets can never be confused."""
        name = (j.name or "").strip()
        dest = next((t for t in plan.targets if t.name == name), None)

        def _drop(t: Target) -> None:
            for k, cur in enumerate(remaining):
                if cur is t:
                    del remaining[k]
                    return

        if dest is None:
            bus.log("warning",
                    f"instruction {j.kind}_target: no target named {name!r} "
                    f"— ignored ({ready.name} continues)", "sequence")
            return False
        if j.kind == "skip":
            bus.log("info", f"instruction: skipping target {name!r}", "sequence")
            if self.reporter:
                self.reporter.mark_skipped(dest)
            if dest is not ready:
                _drop(dest)
                return False
            return True
        # kind == "run"
        if dest is ready:
            bus.log("info",
                    f"instruction run_target: {name!r} is already running — no-op",
                    "sequence")
            return False
        _drop(dest)
        remaining.insert(0, dest)
        bus.log("info",
                f"instruction: jumping to target {name!r} "
                f"(abandoning {ready.name})", "sequence")
        if self.reporter:
            # the jump itself in the report timeline, next to the skip it causes
            self.reporter.record_safety(
                f"instruction run_target {name!r} (abandoning {ready.name})",
                "jump")
            self.reporter.mark_skipped(ready)
        return True

    async def _wait_until(self, deadline_ts: float) -> None:
        """Bounded, cancel- and pause-responsive wait until ``deadline_ts`` (or a
        re-evaluation tick).

        The caller stops tracking (park-hold) before a long wait, so the mount is
        idle here rather than tracking a finished target into the pier. We also run
        the safety gate every tick — a wait used to be a safety blind spot (the gate
        only ran per frame/slew), so rain during a multi-hour inter-target wait
        produced no reaction. No frames flow while waiting, so clear the watchdog's
        progress-expected flag (else it pages a false 'no progress' UNSAFE).

        AT LEAST ONE PASS, ALWAYS — a do-while, not a while. This used to test
        the deadline first, so a deadline already in the PAST returned instantly
        with no sleep, no checkpoint and no safety gate. The scheduler's
        earliest-waiter branch passes the window's ``start_ts``, which is in the
        past whenever a target's time window has opened while the target is
        still below its altitude gate; the scheduler then re-evaluated, found
        the same waiter with the same past anchor, and called straight back in.
        Measured: 14,729 calls in 2 s, zero safety-gate calls, and the event
        loop starved — which takes down the safety poller this very function
        exists to consult. One target is enough to reach it.

        The single guaranteed sleep is what makes the loop impossible to spin,
        and the guaranteed gate is what keeps a wait from being a weather blind
        spot no matter how the caller computed its deadline."""
        self._progress_expected = False
        first = True
        while first or time.time() < deadline_ts:
            first = False
            await self._checkpoint()
            # weather still matters while idle between targets — pause/abort on a
            # sustained unsafe reading at the poll cadence (target=None: no floor
            # check, nothing to re-acquire yet).
            await self._safety_gate(context="frame")
            # A FLOOR ON THE SLEEP, not an early return. Returning here on a
            # non-positive remainder is what let a past deadline spin: the
            # caller's next re-evaluation lands on the same anchor immediately.
            # Yielding for one step instead turns that into a 5 s re-evaluation
            # cadence — which is what the scheduler wanted from it all along.
            remaining = deadline_ts - time.time()
            step = SCHEDULE_WAIT_STEP_S if remaining <= 0 else min(
                SCHEDULE_WAIT_STEP_S, remaining)
            await asyncio.sleep(step)
            if remaining <= 0:
                return

    def _target_complete(self, ti: int, target: Target) -> bool:
        total = sum(s.count for s in target.steps)
        done = sum(self._done.get(f"{target.id}:{s.id}", 0) for s in target.steps)
        return total > 0 and done >= total

    def _enforce_stop_boundary(self, target: Target) -> None:
        """Raise :class:`StopTarget` when the target's FROZEN stop boundary has
        passed (§1.6). ``resolve_window`` folds ``stop_mode`` (dawn/time) AND
        ``max_run_min`` into a single frozen ``stop_ts`` at run start; the scheduler
        only consulted it when SELECTING a target, so once a target was running the
        stop boundary was dead — the engine shot every remaining frame straight
        through dawn into daylight. Re-checking it here, at each frame boundary,
        stops the target the moment its window closes (the in-flight exposure is
        allowed to finish; no new one starts)."""
        win = self._frozen.get(id(target))
        if not win:
            return
        stop_ts = win[1]
        if stop_ts is not None and time.time() >= stop_ts:
            raise StopTarget("observing window closed (stop time / max run / dawn)")

    def _missed_start(self, target: Target, now: float, site: dict,
                      twilight_deg: float) -> bool:
        """Whether ``target``'s start window opened long enough ago that its
        ``on_missed="skip"`` applies (C1-25). ``"wait"`` — the default — always
        answers False; that path is untouched.

        Reads the FROZEN window, never a re-resolved one: re-resolving rolls a
        past dusk forward to tomorrow, which puts the start in the FUTURE and
        makes this branch unreachable (the §1.6 bug).

        Two starts are not "missed", and both are reachable from the shipped UI
        (the toggle is offered for every start mode):

        * ``start_mode="now"`` freezes its start at RUN start, so in a 5-target
          all-now plan every target after the first is hours "late" by
          construction. Now has no window to miss — the user's out-of-night
          control is ``stop_mode``/``max_run_min``.
        * a target still climbing to its own ``min_altitude_deg`` (or held by a
          moon/hour-angle constraint) when its clock window opened. The engine
          was waiting for it exactly as configured; only a target that was
          RUNNABLE at its frozen start and did not get run was missed.

        A target with frames already in the ledger is likewise not missed: a
        RESUMED session re-freezes its windows at the new run start, so a target
        that was shooting when the app died has an hours-old start by
        construction, and dropping half a target on restart is not what "skip the
        ones we missed" means.
        """
        if target.schedule.on_missed != "skip":
            return False
        if target.schedule.start_mode == "now":
            return False
        if any(self._done.get(f"{target.id}:{s.id}", 0) for s in target.steps):
            return False
        win = self._frozen.get(id(target))
        st = win[0] if win else None
        if st is None or now - st <= MISSED_GRACE_S:
            return False
        try:
            was_open = schedule.gating_status(target, site, twilight_deg, st,
                                              window=win)["state"] == "ready"
        except Exception:
            # never drop a target over a failed gate computation — "wait" is the
            # safe default and the shipped one.
            return False
        return was_open

    async def _setup_target(self, ti: int, target: Target) -> None:
        # A slew + plate-solve + initial autofocus legitimately produces no frames
        # for minutes; keep the no-progress watchdog quiet until capture begins.
        self._progress_expected = False
        # this target is now actually starting — clear any stale waiting sub-state
        # a prior gated wait published (wave-3 §2).
        self._set_state(target=target.name, target_index=ti, detail=f"slewing to {target.name}",
                        schedule=None)
        bus.log("info", f"target {ti + 1}/{len(self.plan.targets)}: {target.name}", "sequence")
        # pre-slew safety + mount-floor gate (mount-alt floor is enforced even
        # with no safety device configured — §1.9-A).
        await self._safety_gate(context="slew", target=target)

        # Sun-exclusion cone (W1.10) at the engine MOTION boundary. The centered
        # branch re-checks inside goto_and_center, but the NON-centered slew below
        # (tel.slew) bypasses goto_and_center — and a target's window can open
        # hours after the route-level sequence-start pre-flight, by which time the
        # Sun has marched ~15 deg/hr into a once-clear field. So re-check HERE, at
        # the moment of motion, for BOTH branches. Unlike _safety_gate this is NOT
        # gated on plan.safety_check: _check_solar self-gates on solar_avoidance
        # (ON by default; inert only for a deliberate solar session) and on the
        # cone half-angle, so a daytime/dawn target is caught even when the weather
        # safety monitor is off. A hit raises SafetyAbort to tear the run down
        # through the shielded park/warm wind-down (never silently skip the Sun).
        if "telescope" in self.hub.devices:
            try:
                self.hub._check_solar(target.ra_hours, target.dec_deg)
            except DeviceError as e:
                raise SafetyAbort(f"slew blocked by sun-exclusion cone: {e}") from e

        if "telescope" in self.hub.devices:
            if target.center:
                # GOTO+center is the slew + iterated solve→sync→re-slew loop —
                # bounded so a hung solve/slew can't stall the night (P0-2).
                result = await _bounded(
                    self.hub.goto_and_center(target.ra_hours, target.dec_deg,
                                             rotation_deg=target.rotation_deg),
                    GOTO_TIMEOUT_S + (300 if target.rotation_deg is not None else 0),
                    f"goto+center {target.name}")
                if not result["centered"]:
                    # error_arcmin is None on the solve-failure and motion-fence
                    # abort paths (hub.goto_and_center degrades to a raw GoTo) —
                    # formatting None with :.1f would raise TypeError and kill the
                    # whole run at its first target. Only format a real number.
                    err = result.get("error_arcmin")
                    detail = (f"converged to {err:.1f}'" if err is not None
                              else "plate solve failed — used raw GoTo")
                    bus.log("warning", f"{target.name}: centering {detail} — "
                                       "continuing", "sequence")
            else:
                tel = self.hub.require("telescope")
                if await _bounded(tel.is_parked(), MOUNT_QUERY_TIMEOUT_S,
                                  "mount is_parked query"):
                    await _bounded(tel.unpark(), PARK_TIMEOUT_S, "mount unpark")
                await _bounded(tel.set_tracking(True), MOUNT_QUERY_TIMEOUT_S,
                               "mount set_tracking")
                await _bounded(tel.slew(target.ra_hours, target.dec_deg),
                               SLEW_TIMEOUT_S, f"slew to {target.name}")

        if target.autofocus_first and "focuser" in self.hub.devices:
            await self._autofocus("initial autofocus")

        # THE DECISION IS MADE WHENEVER THE PLAN ASKED FOR GUIDING — not only
        # when a guider happens to be present. This whole block used to sit
        # inside ``and self.hub.guider and self.hub.guider.connected``, so
        # ``require_guiding`` fired ONLY when a guider existed, was connected,
        # and start_guiding() then threw. The one state the setting advertises
        # — "guiding is unavailable" — is a guider that is absent or never came
        # up, and that state skipped the entire block in silence and shot the
        # night unguided. The abort existed for the case it could not see.
        if self.plan.guide:
            cfg = self._cfg
            require_guiding = bool(cfg and cfg.escalation.require_guiding)
            action = (cfg.escalation.guiding_action if cfg else "warn")
            guider = self.hub.guider
            if guider is None or not getattr(guider, "connected", False):
                why = ("no guider is connected" if guider is None else
                       f"the guider ({getattr(guider, 'name', 'guider')}) "
                       "is not connected")
                if require_guiding and action == "abort":
                    bus.log("error", f"guiding required but {why}", "sequence")
                    raise SafetyAbort(f"guiding required but {why}")
                if require_guiding and action == "skip":
                    bus.log("warning", f"guiding required but {why} — skipping "
                                       f"{target.name}", "sequence")
                    raise StopTarget(f"guiding required but {why}")
                # Falls through to the meridian-flip arming below: the target
                # still runs (unguided), and a GEM crossing the meridian still
                # needs its flip armed. An early return here would have traded
                # one silent hazard for another.
                bus.log("warning", f"this plan asks for guiding but {why} — "
                                   "continuing UNGUIDED", "sequence")
            else:
                self._set_state(detail="starting guiding")
                # P1-7: honor escalation.require_guiding / guiding_action. The
                # start is bounded (P0-2) so a guider that never settles can't
                # hang the night. The default action is "warn" → log + continue
                # unguided (legacy behavior unchanged). "abort" → SafetyAbort
                # (no all-night trailed run). "skip" → skip this target.
                try:
                    await _bounded(self.hub.guider.start_guiding(),
                                   GUIDE_START_TIMEOUT_S, "start guiding")
                except SafetyAbort:
                    raise
                except Exception as e:
                    if require_guiding and action == "abort":
                        bus.log("error", f"guiding required but failed to start: {e}",
                                "sequence")
                        raise SafetyAbort(
                            f"guiding required but failed to start: {e}")
                    if require_guiding and action == "skip":
                        bus.log("warning", f"guiding required but failed to start: "
                                           f"{e} — skipping {target.name}", "sequence")
                        raise StopTarget("guiding required but could not start")
                    bus.log("warning", f"guiding failed to start: {e} — continuing "
                                       "unguided", "sequence")

        # Arm the meridian flip for THIS target iff we acquired it east of the
        # meridian (server HA countdown > 0), so a GEM tracking east→west across
        # the meridian flips exactly once when it crosses. A target acquired
        # already-west is on the correct pier side and stays disarmed (arming on
        # HA sign alone would otherwise flip it, or re-flip every frame — the
        # server countdown stays negative for the whole ~12h it is west).
        self._flip_armed = False
        if self.plan.meridian_flip and "telescope" in self.hub.devices:
            try:
                lon = self.hub.site["longitude"]
                self._flip_armed = schedule.hours_to_meridian_flip(
                    target.ra_hours, lon) > 0
            except Exception:
                self._flip_armed = False

        # setup complete — capture is about to begin. Arm the no-progress watchdog
        # and anchor its clock to NOW so a slow slew/solve/AF that just finished
        # doesn't instantly read as a stall against the last target's frame stamp.
        self._last_frame_at = time.time()
        self._progress_expected = True

    async def _capture(self, step, target: Target, *, exposure_s=None) -> dict:
        """Bounded ``hub.capture`` (P0-2). The timeout is exposure-relative: the
        exposure itself plus a generous fixed margin for download/save/detect, so
        a wedged camera/transport can never hang on an unbounded await — it
        escalates through the abort+park path like any other stuck device I/O.

        ``exposure_s`` overrides ``step.exposure_s`` (PRO-5: a solved flat
        exposure); None keeps the step's fixed exposure (every existing path)."""
        exp = float(exposure_s if exposure_s is not None else step.exposure_s)
        budget = exp + CAPTURE_MARGIN_S
        return await _bounded(
            self.hub.capture(exp, step.gain, step.offset, step.binning,
                             save=True, target=target.name, frame_type=step.frame_type),
            budget, f"capture {exp:g}s")

    async def _solve_flat_exposure(self, step, target: Target,
                                   start_exposure_s: float | None = None
                                   ) -> tuple[float, bool]:
        """Meter the light source to ``step.adu_target`` (PRO-5). Returns
        ``(exposure_s, converged)``. Trial captures are ``save=False`` (never
        written to disk); each is bounded like any device I/O. The measurement is
        the frame ``median`` — robust to the stars/dust a flat renders in.

        ``start_exposure_s`` seeds the solver for a RE-solve mid-set: the sky has
        drifted a little since the last one, so the previous answer is the best
        guess available and converges in a capture or two. Starting from the
        plan's original number instead would re-walk the whole ladder every five
        frames and spend more of the twilight window metering than exposing."""
        from ..imaging.flats import FlatExposureSolver
        solver = FlatExposureSolver(
            step.adu_target,
            initial_exposure_s=(step.exposure_s if start_exposure_s is None
                                else start_exposure_s))
        st = solver.first()
        exp = st.exposure_s
        for _ in range(FLAT_METER_MAX_S):
            info = await _bounded(
                self.hub.capture(exp, step.gain, step.offset, step.binning,
                                 save=False, frame_type="Flat"),
                float(exp) + CAPTURE_MARGIN_S, "flat metering capture")
            med = float(info["stats"]["median"])
            st = solver.update(med)
            self._set_state(detail=f"{target.name}: metering flat "
                                   f"{med:.0f} ADU @ {exp:g}s")
            if st.done:
                break
            exp = st.exposure_s
        if not st.converged:
            bus.log("warning", f"{target.name}: flat exposure did not converge "
                               f"({st.reason}); using {st.exposure_s:g}s", "sequence")
        return st.exposure_s, st.converged

    async def _run_calibration(self, ti: int, target: Target) -> None:
        # this target is now actually starting — clear any stale waiting sub-state
        # a prior gated wait published (wave-3 §2).
        self._set_state(target=target.name, target_index=ti, detail=f"calibration: {target.name}",
                        schedule=None)
        bus.log("info", f"calibration target: {target.name}", "sequence")
        # calibration frames flow immediately — arm the watchdog + anchor its clock.
        self._last_frame_at = time.time()
        self._progress_expected = True
        for si, step in enumerate(target.steps):
            # PRO-5: a Flat step with adu_target > 0 turns the panel on, solves the
            # per-filter exposure via bounded trial captures, then shoots the count
            # at the SOLVED exposure. adu_target == 0 keeps the fixed-exposure path
            # (every existing plan) verbatim. Panel-off is issued after the step and,
            # on any abort/teardown, by _panel_off_safe (never leave the panel lit).
            solved_exp = None
            flat_auto = (step.frame_type.upper() == "FLAT" and step.adu_target > 0)
            panel_lit = False
            # try/finally, not a trailing call: the frame loop below can leave via
            # StopTarget (the stop boundary), which the SCHEDULER CATCHES — the
            # night carries on to the next target, so the abort/teardown
            # _panel_off_safe never runs and the panel would burn through every
            # following target's frames.
            try:
                key = f"{target.id}:{step.id}"
                # A calibration step carries a filter exactly like a light step
                # does, and this call used to live in _run_step ALONE — so for
                # the one target type calibration frames are actually shot
                # through, none of it reached the wheel: per-filter flats all
                # went through whichever slot happened to be loaded, and darks
                # and bias never drove to the blackout slot that exists for
                # them. Placed ahead of the flat metering below, because a trial
                # exposure solved through the wrong filter solves the wrong
                # filter. Skipped for a step a resume has already finished, so
                # recovery does not move the wheel for frames it will not shoot.
                if self._done.get(key, 0) < step.count:
                    await self._apply_filter(step)
                if flat_auto:
                    # METERING IS NOT THE PANEL'S JOB. This whole block used to
                    # sit behind `"covercalibrator" in self.hub.devices`, so on
                    # a rig with no panel a step that ASKED for ADU metering
                    # silently shot `step.exposure_s` instead and said nothing.
                    # That is backwards: a panel is a constant light source and
                    # barely needs metering, while the sky — which is what a
                    # panel-less rig actually shoots flats against, through a
                    # translucent cap or at twilight — changes by the minute and
                    # needs it most.
                    #
                    # Only the panel COMMANDS stay gated on a panel existing.
                    if ("covercalibrator" in self.hub.devices
                            and step.panel_brightness is not None):
                        await _bounded(self.hub.calibrator_on(step.panel_brightness),
                                       CALIBRATOR_CMD_TIMEOUT_S, "calibrator on")
                        panel_lit = True
                        cc = self.hub.calibrator
                        if getattr(cc, "has_cover", False):
                            await _bounded(self.hub.open_cover(),
                                           CALIBRATOR_CMD_TIMEOUT_S, "open cover")
                    self._set_state(detail=f"{target.name}: solving flat exposure")
                    solved_exp, _ = await self._solve_flat_exposure(step, target)
                for i in range(self._done.get(key, 0), step.count):
                    await self._checkpoint()
                    # CALIBRATION HAS A STOP BOUNDARY TOO (§1.6). Same per-FRAME
                    # placement as _run_step: the frozen dawn / stop-time / max-run
                    # boundary was consulted only when the scheduler SELECTED this
                    # target, so a 200-frame dark set shot every remaining frame
                    # hours past it (and a flat block ran on into daylight). Per
                    # step is not enough — one step IS the whole 200 frames.
                    self._enforce_stop_boundary(target)
                    # WEATHER APPLIES TO CALIBRATION TOO. This was the one capture
                    # loop with a _checkpoint and no _safety_gate, while the module
                    # docstring promised the gate ran "after every frame-boundary
                    # _checkpoint" — and every unsafe ACTUATION (park-hold, the
                    # SafetyAbort wind-down, close_dome_on_unsafe) lives only behind
                    # this call, so a calibration block ran weather-blind from its
                    # first frame to its last. Flats are shot with the roof open at
                    # dusk and a dark set can run for hours.
                    #
                    # context="frame", not "slew": calibration produces no mount
                    # motion, so the mount-limit half stays inert (it is reached
                    # only under context == "slew") while the monitor half runs.
                    await self._safety_gate(context="frame", target=target)
                    # §1.9-F: ping the external dead-man's-switch + heartbeat each frame.
                    await self._frame_alerts_tick()
                    # heal a dropped device before the exposure that needs it
                    await self._reconnect_gate()
                    # RE-METER A SKY FLAT AS THE SKY MOVES. With a panel the
                    # source is constant and the opening solve holds for the
                    # whole set; without one the source is the twilight sky,
                    # which changes by roughly a factor of two every few
                    # minutes. Solving once would give a set whose first frame
                    # is correctly exposed and whose last is saturated or noise,
                    # with every frame in between a different flat — and nothing
                    # in the FITS to say so.
                    #
                    # Skipped entirely when a panel is lit, and when the set is
                    # short enough not to drift.
                    if (flat_auto and not panel_lit and i > 0
                            and i % FLAT_RESOLVE_EVERY == 0):
                        self._set_state(detail=f"{target.name}: re-metering flat "
                                               f"(the sky has moved)")
                        solved_exp, _ = await self._solve_flat_exposure(
                            step, target, start_exposure_s=solved_exp)
                    exp = solved_exp if solved_exp is not None else step.exposure_s
                    self._begin_frame(ti, si, exp)
                    self._set_state(state="running",
                                    detail=f"{target.name}: {step.frame_type} {exp:g}s "
                                           f"[{i + 1}/{step.count}]")
                    info = await self._capture(step, target, exposure_s=solved_exp)
                    # calibration frames always record + advance (no quality gate on
                    # darks/bias/flats) — but they still go in the report.
                    accepted = self._check_quality(info, calibration=True)
                    self._reporter_record(target, step, info, accepted=accepted)
                    if accepted:
                        self._record_frame(key, i, target, step, info)
                    else:
                        if not await self._handle_reject(info, key, i, target, step):
                            self._record_frame(key, i, target, step, info, accepted=False)
            finally:
                if panel_lit:
                    await self._panel_off_safe()

    def _step_complete(self, target: Target, step) -> bool:
        """Has this step got everything it asked for?

        The two count modes disagree about what "everything" means, and the
        cycle driver must not have to know which is in force: ``accepted``
        counts the ledger's accepted frames, ``attempts`` counts frames taken.
        Asking the wrong one is how a night either stops early or never stops.
        """
        plan = self.plan
        if plan is not None and plan.count_mode == "accepted" \
                and not target.calibration and self._session is not None:
            return self._session.accepted(step.id) >= step.count
        return self._done.get(f"{target.id}:{step.id}", 0) >= step.count

    async def _run_steps(self, ti: int, target: Target) -> None:
        """Drive a target's steps, in blocks or round-robin.

        BLOCKS is what this engine has always done and stays the default:
        each step runs to completion, then the next.

        CYCLE visits each step in turn, ``per_visit`` frames deep, and keeps
        going round until every step has its count. L R G B S Ha O3, forty-five
        times over. Two reasons, and neither is cosmetic: every filter samples
        the SAME sky, so the channels combine without one of them carrying the
        hour the seeing went soft; and a night cut short at 60 percent leaves
        60 percent of every channel instead of three finished filters and four
        empty ones.

        The filter change is not extra work bolted on here — ``_run_step``
        already applies the step's filter on entry, so a visit moves the wheel
        by construction. Dither and refocus cadence are plan-level counters that
        span steps, so they keep their meaning across a cycle rather than
        resetting seven times a pass.

        A pass that manages to shoot NOTHING ends the loop. Without that, a step
        that can never complete — a filter the wheel does not have, a quota the
        night cannot reach — would spin this loop forever between frames,
        which is the one failure a driver like this must not have.
        """
        steps = list(enumerate(target.steps))
        if target.acquisition != "cycle":
            for si, step in steps:
                await self._run_step(ti, si, target, step)
            return

        rounds = 0
        while True:
            pending = [(si, s) for si, s in steps
                       if not self._step_complete(target, s)]
            if not pending:
                return
            rounds += 1
            before = self._frames_done
            for si, step in pending:
                await self._run_step(ti, si, target, step,
                                     max_frames=max(1, int(step.per_visit or 1)))
            if self._frames_done == before:
                names = ", ".join(s.filter or "no filter" for _, s in pending)
                bus.log("warning",
                        f"{target.name}: a full pass took no frames with "
                        f"{len(pending)} step(s) still short ({names}) — "
                        f"stopping the cycle rather than spinning", "sequence")
                return

    async def _run_step(self, ti: int, si: int, target: Target, step,
                        max_frames: int | None = None) -> None:
        """Shoot this step. ``max_frames`` bounds ONE VISIT, not the step.

        Interleaved acquisition is built on the fact that this method was
        already resumable: ``i`` starts from the persisted ``_done`` count, so a
        step can be left half-finished and picked up later without knowing it
        was interrupted. Bounding a visit therefore needs no new bookkeeping —
        the cycle driver simply calls back until every step reports complete.

        ``None`` means run to completion, which is what block acquisition does
        and what every existing caller gets.
        """
        plan = self.plan
        assert plan is not None
        key = f"{target.id}:{step.id}"
        taken_this_visit = 0
        # accepted-frame quota mode (spec §3): the predicate is the LEDGER's
        # effective-accepted count, not the attempt index. Attempts are
        # unbounded within the night; window/dawn/max_run still end it.
        quota = plan.count_mode == "accepted" and not target.calibration

        def _quota_met() -> bool:
            return (self._session is not None
                    and self._session.accepted(step.id) >= step.count)

        if quota:
            if _quota_met():
                return
        elif self._done.get(key, 0) >= step.count:
            return
        await self._apply_filter(step)

        step_rejects = 0                 # per-step consecutive guard (spec §3)
        i = self._done.get(key, 0)

        def _visit_done() -> bool:
            # BOUNDED BY ATTEMPTS, not by accepted frames. A step whose frames
            # keep getting rejected must not hold the cycle: the next pass comes
            # back to it anyway, and every other filter is waiting.
            return max_frames is not None and taken_this_visit >= max_frames

        while (((not _quota_met()) if quota else (i < step.count))
                and not _visit_done()):
            await self._checkpoint()
            # stop the target the instant its FROZEN window closes (dawn / stop_mode
            # time / max_run_min) — checked between frames so the in-flight exposure
            # finishes but no new one starts into daylight (§1.6). Raises StopTarget
            # → the scheduler skips ahead / finalizes the night at dawn.
            self._enforce_stop_boundary(target)
            await self._safety_gate(context="frame", target=target)
            # §1.9-F: ping the external dead-man's-switch + heartbeat each frame.
            await self._frame_alerts_tick()
            # heal a dropped device before the exposure that needs it
            await self._reconnect_gate()
            await self._maybe_meridian_flip(target, step.exposure_s)
            await self._maybe_recover_guiding(target)

            if plan.dither_every and self._frames_since_dither >= plan.dither_every \
                    and self.hub.guider and self.hub.guider.connected:
                self._set_state(detail="dithering")
                _t0 = time.time()
                try:
                    await _bounded(self.hub.guider.dither(plan.dither_pixels),
                                   GUIDE_OP_TIMEOUT_S, "dither")
                    self._frames_since_dither = 0
                    self._record_event_cost("dither", time.time() - _t0)
                    self._frame_had_event = True
                except SafetyAbort:
                    raise
                except Exception as e:
                    bus.log("warning", f"dither failed: {e}", "sequence")

            if await self._refocus_due():
                await self._autofocus("refocus")
                self._frame_had_event = True

            # LAST GATE BEFORE THE SHUTTER. The recovery path above already
            # waits, so in the ordinary run this is a no-op that returns on its
            # first check. It is here for the calibration this engine did NOT
            # start — an operator pressing "calibrate" on the Guide screen while
            # a plan is running — which no amount of care inside the recovery
            # path can see coming. Cheap to ask, and the frame it saves is one
            # nobody would have known to look for.
            await self._await_guider_quiet("this frame")

            self._begin_frame(ti, si, step.exposure_s)
            shown = (self._session.accepted(step.id) + 1
                     if quota and self._session is not None else i + 1)
            self._set_state(state="running",
                            detail=f"{target.name}: {step.filter or 'no filter'} "
                                   f"{step.exposure_s:g}s  [{shown}/{step.count}]")
            self._hold_step = step
            info = await self._capture(step, target)
            self._frames_since_dither += 1
            self._frames_since_focus += 1
            # quality-before-record (§1.9-D, C2-8): decide accept BEFORE _done
            # advances. EVERY frame goes in the report.
            accepted = self._check_quality(info)
            self._reporter_record(target, step, info, accepted=accepted)
            # Every linear sub is already a cloud measurement - hub's preview
            # path runs cloud_score on it and puts the verdict in info["cloud"].
            # Reading it here costs nothing and makes the sky verdict exactly as
            # fresh as the last frame. A frame with NO verdict (a stretched one)
            # feeds nothing rather than counting as clear.
            self._observe_clouds(info)
            # PRO-3: per-frame instruction eval (accepted + info["hfr"] known).
            # Guarded so an empty instruction list makes this path dead —
            # byte-identical to the pre-PRO-3 loop.
            if plan.instructions:
                _now = time.time()
                ctx = TriggerContext(
                    now_ts=_now,
                    frame_hfr=(info.get("hfr") if isinstance(info, dict) else None),
                    guide_rms=self._guide_rms(),
                    frame_rejected=(not accepted),
                    target_complete=False,
                    active_target=target.name,
                    # Tri-state, all three. None means nobody can currently say,
                    # and the evaluator treats that as indeterminate rather than
                    # as a "no" - see instructions.TriggerContext.
                    cloudy=self._clouds.cloudy(_now),
                    unsafe=await self._unsafe_now(),
                    panel_ready=self._panel_ready_now())
                await self._run_instructions(ctx, target, step)
            if accepted:
                step_rejects = 0
                self._night_rejects = 0            # resets on ANY accepted frame
                self._record_frame(key, i, target, step, info)
                i += 1
                taken_this_visit += 1
                continue
            if quota:
                # accepted mode (spec §3): rejects are ALWAYS kept on disk and
                # ledger-recorded (regrading needs the file) — escalation's
                # hfr_reject_action applies to attempts mode ONLY. _done does
                # not advance; the frame still needs cadence bookkeeping so it
                # can't pollute the next frame's overhead sample.
                self._record_session_frame(target, step, info,
                                           auto_accepted=False)
                self._end_discarded_frame()
                step_rejects += 1
                self._night_rejects += 1
                if plan.max_consecutive_rejects_night \
                        and self._night_rejects >= plan.max_consecutive_rejects_night:
                    raise NightQualityStop(
                        f"{self._night_rejects} consecutive rejects across targets")
                if plan.max_consecutive_rejects \
                        and step_rejects >= plan.max_consecutive_rejects:
                    bus.log("warning",
                            f"{target.name}: {step_rejects} consecutive rejects — "
                            "skipping to the next step (shortfall stays in the "
                            "ledger for another night)", "sequence")
                    return
                continue
            # attempts mode: legacy escalation path (warn / discard / retake).
            if not await self._handle_reject(info, key, i, target, step):
                self._record_frame(key, i, target, step, info, accepted=False)
            i += 1
            taken_this_visit += 1

    @staticmethod
    def _effective_filter(step, info: dict) -> str | None:
        """The filter this frame was ACTUALLY taken through (UX #1).

        ``info["filter"]`` is the wheel's physical position, resolved by
        ``hub.capture`` at exposure time — the exact string it wrote into the FITS
        ``FILTER`` card and the ``$$FILTER$$`` filename token. Preferring it here
        is what makes the report, the ``by_filter`` headline, the stacking-bundle
        folder and ``frames.csv`` agree with the file on disk. Falls back to the
        plan's step text when there is no wheel (or it couldn't be read), and to
        ``None`` when there is genuinely no filter — never to a stale plan value
        that contradicts the header."""
        resolved = info.get("filter") if isinstance(info, dict) else None
        if resolved:
            return str(resolved)
        return (step.filter or None)

    def _reporter_record(self, target: Target, step, info: dict, *, accepted: bool) -> None:
        """Record one frame to the session report (every frame, with its accepted
        flag). Best-effort; never lets a report-write hiccup break the run."""
        if self.reporter is None:
            return
        hfr = info.get("hfr") if isinstance(info, dict) else None
        ecc = info.get("ecc") if isinstance(info, dict) else None
        rms = None
        try:
            if self.hub.guider and self.hub.guider.connected:
                rms = getattr(self.hub.guider.stats(), "rms_total", None)
        except Exception:
            rms = None
        temp = None
        frame = getattr(self.hub, "last_frame", None)
        if frame is not None:
            temp = getattr(frame, "temperature_c", None)
        saved = info.get("saved_path") if isinstance(info, dict) else None
        try:
            self.reporter.record_frame(FrameRecord(
                ts=time.time(), target=target.name,
                filter=self._effective_filter(step, info),
                frame_type=step.frame_type, exposure_s=step.exposure_s,
                accepted=accepted, hfr=hfr, sensor_temp_c=temp,
                guide_rms_total=rms, saved_path=saved,
                gain=getattr(step, "gain", None), offset=getattr(step, "offset", None),
                binning=getattr(step, "binning", None), ecc=ecc,
                altitude_deg=_frame_altitude(target, self.hub.site, time.time())))
        except Exception as e:
            bus.log("warning", f"report record failed: {e}", "sequence")

    async def _handle_reject(self, info: dict, key: str, i: int, target: Target,
                             step) -> bool:
        """Act on a quality-rejected frame per ``cfg.escalation.hfr_reject_action``
        (§1.9-D). Returns True when the rejection has been fully handled (the
        ``_done`` slot was advanced or the frame was discarded WITHOUT advancing);
        False to fall back to a normal ``_record_frame`` (the "warn" / keep path).

        * ``warn``    → keep the frame; caller records it normally (return False).
        * ``discard`` → unlink the saved FITS, do NOT advance ``_done`` (return
          True — the same slot index re-exposes on the next loop iteration is not
          how the for-loop works, so we re-expose inline below for retake; for a
          plain discard the loop's ``range`` would skip ahead, so discard simply
          drops the file and lets the loop continue — the frame count for this
          step is reduced by one capture, accepted-count unaffected).
        * ``retake``  → unlink + re-expose (capped per target by
          ``hfr_retake_limit_per_target``); on success record the retake; once the
          cap is hit, fall back to discard.
        """
        cfg = self._cfg
        action = cfg.escalation.hfr_reject_action if cfg else "warn"
        ti = self._index_of_target(target)

        if action == "warn":
            return False        # keep + record normally

        # discard / retake both unlink the rejected FITS so it never pollutes a
        # stack (C2-8).
        self._unlink_saved(info)

        if action == "discard":
            bus.log("warning", f"{target.name}: discarded a poor frame (HFR reject)",
                    "sequence")
            # do NOT advance _done — the slot is consumed by this capture attempt,
            # the loop moves to the next index; accepted frames are what count.
            self._end_discarded_frame()
            return True

        if action == "retake":
            spent = self._retakes_per_target.get(ti, 0)
            cap = cfg.escalation.hfr_retake_limit_per_target if cfg else 0
            if cap and spent >= cap:
                bus.log("warning", f"{target.name}: retake cap reached — discarding",
                        "sequence")
                self._end_discarded_frame()
                return True
            self._retakes_per_target[ti] = spent + 1
            self._set_state(detail=f"retaking (HFR reject) [{spent + 1}/{cap or '∞'}]")
            bus.log("warning", f"{target.name}: retaking a poor frame "
                               f"({spent + 1}/{cap or 'unlimited'})", "sequence")
            self._frame_had_event = True   # retake wall-time is not per-frame overhead
            self._begin_frame(*(self._active_step or (ti, 0)), step.exposure_s)
            new_info = await self._capture(step, target)
            # the rejected original was NOT folded into the running median (only
            # ACCEPTED frames anchor it now), so let an accepted retake contribute
            # its single good sample — one logical frame, at most one median sample.
            accepted = self._check_quality(new_info)
            self._reporter_record(target, step, new_info, accepted=accepted)
            if accepted:
                self._record_frame(key, i, target, step, new_info)
                return True
            # retaken frame still bad → discard and stop retaking this one.
            self._unlink_saved(new_info)
            self._end_discarded_frame()
            return True

        return False

    def _end_discarded_frame(self) -> None:
        """Common bookkeeping for a frame that was discarded (or retaken-then-
        discarded / capped) WITHOUT going through ``_record_frame``.

        Three things must be reset so the discard doesn't pollute the next frame
        (P3-16): clear the in-flight marker, clear the per-frame event flag (else
        a dither/AF set this iteration leaks into the NEXT clean frame and wrongly
        excludes it from the overhead EMA), and advance the cadence anchor so the
        next clean frame's overhead is measured from the discard point rather than
        spanning the discarded capture (which would otherwise admit an inflated,
        ~one-exposure-too-long overhead sample)."""
        self._frame_started_at = 0.0
        self._frame_had_event = False
        self._last_frame_done = time.time()

    def _index_of_target(self, target: Target) -> int:
        if self.plan:
            for ti, t in enumerate(self.plan.targets):
                if t is target:
                    return ti
        return 0

    @staticmethod
    def _unlink_saved(info: dict) -> None:
        """Delete the FITS a rejected frame saved (sim/Alpaca local saves only —
        NINA saved_paths live on the imaging host and are not local). Never
        raises."""
        if not isinstance(info, dict):
            return
        path = info.get("saved_path")
        if not path:
            return
        # The docstring above stated the NINA rule from the day this was
        # written; nothing enforced it. For a NINA rig `saved_path` is whatever
        # string the imaging host returned (devices/nina.py pulls it straight
        # out of the status payload), and this method then unlinks it — so a
        # network peer at a user-typed host:port could name any file the service
        # account can delete. It fires whenever a frame fails the HFR/eccentricity
        # gate and the escalation action is `discard` or `retake`.
        #
        # The guard already existed and was already applied to the READ of this
        # exact field: `_is_local_save` gates the FITS download route and the
        # bundle's source selection. Only the delete side was missing it.
        if not Hub._is_local_save(path):
            return
        try:
            p = Path(path)
            if p.is_file():
                p.unlink(missing_ok=True)
        except OSError:
            pass

    # --------------------------------------------------------- safety gate (§1.9)

    async def _safety_gate(self, *, context: str, target: Target | None = None) -> None:
        """Unified safety gate (§1.9-A).

        TWO GATES, and they are not the same kind of thing:

        * The MONITOR gate (rain, cloud, an unsafe verdict) is what
          ``cfg.safety.enabled`` and ``plan.safety_check`` govern. Fail-CLOSED:
          a connected monitor whose cached reading is missing or stale is
          treated as UNSAFE.
        * The MOUNT LIMITS (altitude floor, horizon, no-go wedges, pier
          collision, zenith keep-out) are enforced on every slew regardless.
          They describe the rig's own geometry, and nothing about "I have no
          cloud sensor tonight" implies "my tripod moved".

        That separation is what the comment below the limits call had always
        claimed and the early return above it had always prevented: unticking a
        plan's "Safety check" — whose tooltip said only "Off runs without the
        safety abort" — silently disarmed the pier guard and the zenith
        keep-out too. Safe to enforce unconditionally because every limit is
        INERT until configured (min_alt 0, max_alt 90, horizon/nogo None, pier
        limits off), so a rig that set none sees no change."""
        cfg = self._cfg
        if cfg is None:
            return
        # The no-progress watchdog's trip, consumed in the RUN task so on_unsafe
        # can actually pause/park/abort (see _watchdog_check). Ahead of the
        # monitor_armed early-return below: a stall is not a weather verdict, and
        # a rig with no cloud sensor still wants its stalled night ended.
        tripped, self._watchdog_tripped = self._watchdog_tripped, None
        if tripped:
            await self._on_unsafe(tripped, target=target)
        monitor_armed = bool(cfg.safety.enabled and self.plan
                             and self.plan.safety_check)
        if not monitor_armed:
            if context == "slew" and target is not None:
                await self._enforce_mount_floor(projected=True, target=target)
            return

        mon = self.hub.devices.get("safety")
        if mon is None:
            # ARMED WITH NOTHING BEHIND IT. This branch did not exist: the whole
            # monitor block hung off `mon is not None`, so safety.enabled=True +
            # on_unsafe="pause" permitted everything on a rig with no monitor
            # assigned — which is the shipped default and the state this rig is
            # in. A monitor that EXISTS and is disconnected fails closed one
            # branch below. Absent and disconnected are the same situation to an
            # operator and were opposite situations to this code.
            await self._no_safety_source(target)
        elif not getattr(mon, "connected", False):
            await self._on_unsafe("safety monitor disconnected", stale=True,
                                  target=target)
        else:
            reading = await self._read_safety()
            if reading is None or reading.stale:
                await self._on_unsafe("safety read stale/unavailable", stale=True,
                                      target=target)
            elif not reading.is_safe:
                # Debounce at the SAFETY-POLLER cadence, NOT the frame cadence.
                # This gate only runs once per frame boundary, and a light sub
                # is minutes long — so counting one unsafe reading per frame
                # meant ``unsafe_consecutive`` FRAMES (10+ min of rain on open
                # gear) before acting, not the few seconds of glitch-absorption
                # it was meant to be. Confirm the verdict by re-sampling the
                # hub's own-cadence cache ``unsafe_consecutive`` times at the
                # poll cadence right here, so ~N × poll seconds of sustained
                # unsafe (not N frames) trips the action.
                if await self._confirm_unsafe(cfg):
                    await self._on_unsafe(reading.reason or reading.source or
                                          "unsafe condition reported",
                                          target=target)
            else:
                self._unsafe_streak = 0
                self._safe_streak += 1

        # The mount limits — floor, horizon, no-go wedges, pier collision and
        # the zenith keep-out. Enforced on every slew independently of the
        # safety MONITOR and of the toggles that arm it; see the docstring, and
        # the early-return branch above that runs this same call when the
        # monitor gate is off.
        if context == "slew" and target is not None:
            await self._enforce_mount_floor(projected=True, target=target)

    async def _reconnect_gate(self) -> None:
        """Heal a device that has dropped out, before the next exposure needs it.

        ``escalation.reconnect_resume`` and ``reconnect_retries`` were stored,
        echoed by the API, rendered as a working Settings toggle — and read by
        no production code at all. ``hub.reconnect_role`` had no caller either,
        and the ``_last_connect`` map was maintained at three sites to feed a
        method nobody called. The whole feature was a claim nothing kept.

        Runs at the frame boundary rather than in an exception handler: a
        dropped USB device is visible as ``connected == False`` before the
        capture that would fail on it, so healing here costs one poll and saves
        the run, whereas healing after the throw means unwinding a teardown that
        has already parked the mount.

        Only roles the RUN needs. A rotator that drops out of a plan with no
        rotation angle must not end the night, and reconnect attempts against a
        device nothing is going to use are pure delay on the critical path.

        Off by default, and silent when off — the abort path is unchanged for
        anyone who has not asked for this."""
        cfg = self._cfg
        if cfg is None or not cfg.escalation.reconnect_resume:
            return
        plan = self.plan
        needed = ["camera"]
        if plan is not None:
            if any(s.filter for t in plan.targets for s in t.steps):
                needed.append("filterwheel")
            if plan.guide:
                needed.append("guider")
            if plan.autofocus_every or any(t.autofocus_first for t in plan.targets):
                needed.append("focuser")
            if any(not t.calibration for t in plan.targets):
                needed.append("telescope")
        for role in needed:
            dev = self.hub.devices.get(role)
            if dev is None or getattr(dev, "connected", False):
                continue
            tries = max(1, cfg.escalation.reconnect_retries)
            bus.log("warning", f"{role} has dropped out — reconnecting "
                               f"(up to {tries} attempt{'s' if tries > 1 else ''})",
                    "sequence")
            self._set_state(detail=f"reconnecting {role}")
            for attempt in range(1, tries + 1):
                await self._checkpoint()
                bus.publish("reconnect", role=role, attempt=attempt)
                if await self.hub.reconnect_role(role):
                    bus.log("info", f"{role} is back after {attempt} "
                                    f"attempt{'s' if attempt > 1 else ''} — "
                                    f"the run continues", "sequence")
                    break
                if attempt < tries:
                    await asyncio.sleep(RECONNECT_BACKOFF_S)
            else:
                # Out of attempts. Fall through to the ordinary teardown rather
                # than inventing a second abort path: the run cannot shoot
                # without this device, and SafetyAbort is what parks and warms.
                raise SafetyAbort(
                    f"{role} dropped out and did not come back after {tries} "
                    f"reconnect attempt{'s' if tries > 1 else ''}")

    def _warn_if_nothing_can_report_a_failure(self) -> None:
        """Say, at run start, when a failure tonight would reach nobody.

        Same shape and the same reasoning as ``_no_safety_source`` below: the
        run proceeds, because an unattended destination is not part of a working
        rig and refusing to image without one would be absurd — but it is SAID,
        once, instead of being silently permitted.

        This is the gap the unattended-guards audit put first: "alerts + deadman
        first, because without them no other failure is even observable". The
        dispatcher, the sinks and the wall-clock dead-man's-switch are all built
        and running; the rig simply has ``alerts: []`` and an empty
        ``deadman_url``, so every escalation path this engine can take ends in a
        log line on a machine nobody is looking at.

        The watchdog is named separately because it is a different silence: 0
        means the no-progress task is never even started, so a run that quietly
        stops producing frames is not merely unreported, it is undetected.

        WARNING, not error, for the reason spelled out in ``_no_safety_source``:
        the level IS the alert type, and a default install with no sinks must not
        page itself about having no sinks.
        """
        cfg = self._cfg
        if cfg is None or self._warned_no_destination:
            return
        self._warned_no_destination = True
        esc = cfg.escalation
        sinks = [s for s in (getattr(cfg, "alerts", None) or [])
                 if getattr(s, "enabled", True)]
        deadman = (getattr(esc, "deadman_url", "") or "").strip()
        if sinks or deadman:
            return
        watchdog_s = int(getattr(esc, "no_progress_watchdog_s", 0) or 0)
        extra = ("" if watchdog_s else
                 " The no-progress watchdog is also off (0), so a run that "
                 "stops producing frames will not even be noticed.")
        bus.log("warning",
                "nothing can report a failure tonight: no alert sinks are "
                "configured and no dead-man's-switch URL is set, so if this run "
                "aborts, stalls or is paused by weather, it will say so only in "
                "this log." + extra,
                "alert")

    async def _no_safety_source(self, target: Target | None) -> None:
        """Armed safety with no monitor assigned at all.

        ``escalation.require_safety_monitor`` decides which of the two honest
        answers this rig wants:

        * ON — an absent monitor is a disconnected monitor, drives ``on_unsafe``
          exactly like one, and an unattended night stops rather than running
          unguarded. This is what an operator reading "safety: enabled, on
          unsafe: pause" already believes is happening.
        * OFF (the default) — the run proceeds, because a weather monitor is not
          part of a working rig and the shipped default arms safety with nothing
          assigned; failing closed here would refuse to image out of the box.
          But it is SAID, once per run, at warning level and through the alert
          sinks, instead of being silently permitted. A gate that reads as
          protection and permits everything is worse than no gate.

        Once per run, not per frame: this is a configuration fact, not an event,
        and it does not change between frames. Repeating it every boundary would
        bury the night's real warnings.

        WARNING level, deliberately, not error. A log alert carries the level as
        its alert TYPE, and the default sink subscribes to
        ``run_start/run_end/safety/error`` — so error here would page every
        default install that simply has no weather monitor, every run. The three
        surfaces that do carry it are the night's log, the pre-flight go/no-go
        screen, and ``require_safety_monitor`` for an operator who wants it
        enforced rather than reported. Anyone who wants it pushed adds
        ``warning`` to their sink's events list, which is an existing knob."""
        cfg = self._cfg
        if cfg is not None and cfg.escalation.require_safety_monitor:
            await self._on_unsafe(
                "no safety monitor is assigned, and require_safety_monitor is on",
                stale=True, target=target)
            return
        if self._warned_no_safety_source:
            return
        self._warned_no_safety_source = True
        bus.log("warning",
                "safety is armed but no monitor is assigned — nothing is watching "
                "the weather for this run. Assign a safety monitor, or turn safety "
                "off so the run does not claim a guard it does not have.",
                "safety")

    async def _read_safety(self):
        """The cached SafetyReading from the hub's own-cadence poller. When the
        monitor is freshly connected but the poller hasn't cached its first read
        yet, wait briefly (bounded, cancel-responsive) for it before fail-closing
        — so a normal start isn't spuriously flagged unsafe."""
        reading = await self.hub.safety_reading()
        if reading is not None:
            return reading
        deadline = time.time() + SAFETY_SEED_WAIT_S
        while reading is None and time.time() < deadline:
            await asyncio.sleep(SAFETY_SEED_STEP_S)
            reading = await self.hub.safety_reading()
        return reading

    async def _confirm_unsafe(self, cfg) -> bool:
        """Confirm a sustained-unsafe verdict by re-sampling the hub's cached safety
        reading at the POLLER cadence, up to ``unsafe_consecutive`` readings. The
        already-taken unsafe reading counts as sample 1; each further sample waits
        ``SAFETY_PAUSE_POLL_S`` (the poll cadence) and re-reads. Returns ``True``
        only when every sample stays unsafe (a disconnected/stale/failed read is
        fail-closed to unsafe); a single safe re-read means a transient glitch and
        returns ``False`` (do not act).

        This makes the debounce measure ~``unsafe_consecutive × poll`` seconds of
        real weather — the intended glitch filter — instead of that many FRAME
        boundaries, which at minutes-per-sub let genuine rain run for many
        exposures on open equipment (the bug)."""
        need = max(1, cfg.safety.unsafe_consecutive)
        self._unsafe_streak = 1
        self._safe_streak = 0
        while self._unsafe_streak < need:
            await self._checkpoint()
            await asyncio.sleep(SAFETY_PAUSE_POLL_S)
            mon = self.hub.devices.get("safety")
            if mon is not None and not getattr(mon, "connected", False):
                self._unsafe_streak += 1            # disconnected → unsafe
                continue
            reading = await self._read_safety()
            if reading is None or reading.stale or not reading.is_safe:
                self._unsafe_streak += 1
            else:
                self._unsafe_streak = 0             # glitch cleared → not sustained
                self._safe_streak = 1
                return False
        return True

    async def _on_unsafe(self, reason: str, *, stale: bool = False,
                         action: str | None = None,
                         target: Target | None = None) -> None:
        """Handle an UNSAFE verdict (§1.9-A/D). Records to the reporter, publishes
        a ``safety`` bus edge + an error log, then acts per ``cfg.safety.on_unsafe``:

        * ``pause`` → stop tracking / park-hold, then loop re-reading safety until
          ``resume_safe_consecutive`` clean reads (re-check horizon+pier, return)
          OR ``max_pause_min`` elapses → escalate to a park wind-down + SafetyAbort.
        * ``abort_park_warm`` / ``park`` → raise SafetyAbort (the run's except
          chain does the shielded wind-down).
        * ``warn`` → log + alert, continue.

        UX #8: the action RECORDED is the action we are about to TAKE. This used
        to stamp ``cfg.safety.on_unsafe`` into the report before the dome branch
        below escalated, so a night that aborted, parked the mount and shut the
        roof was filed as ``{"action": "pause"}`` with no roof event at all — the
        morning-after artefact actively misinformed. The escalation is now
        resolved FIRST (``_escalated_action``) and the roof outcome is recorded as
        its own event where it actually happens.
        """
        cfg = self._cfg
        act = action or (cfg.safety.on_unsafe if cfg else "pause")
        # PRO-4: a CLOSEABLE roof must CLOSE over the gear, not pause-hold under
        # open sky. When close_dome_on_unsafe is set and a dome is connected we do
        # NOT fall through to the warn/pause/abort branches below.
        dome = self.hub.devices.get("dome")
        closing = bool(cfg and cfg.safety.close_dome_on_unsafe
                       and dome is not None and getattr(dome, "connected", False))
        act = self._escalated_action(act, closing=closing, cfg=cfg)
        self._record_safety(reason, act)
        # ONE producer per verdict edge (UX #33): the hub's own-cadence poller has
        # usually already announced this exact reason, and a second identical
        # event raised a second sticky UNSAFE toast while overwriting the UI's
        # safety state with a partial payload. publish_safety merges the cached
        # reading and suppresses the repeat; a genuinely new reason (the
        # no-progress watchdog) still publishes.
        self.hub.publish_safety({"is_safe": False, "reason": reason,
                                 "action": act, "stale": stale})
        bus.log("error", f"UNSAFE: {reason} → {act}", "safety")

        if closing:
            if not cfg.safety.reopen_dome_when_safe:
                # Reopen OPT-IN OFF (default) ⇒ BYTE-IDENTICAL to before: ESCALATE
                # every on_unsafe action (INCLUDING pause) to the shielded
                # park-and-close teardown by raising SafetyAbort here — the roof
                # close rides _wind_down's fenced-park + close ordering and the RUN
                # ENDS. No reopen.
                raise SafetyAbort(f"{reason} — closing roof")
            # Reopen ENABLED (PRO-4 D3): instead of ending the run, CLOSE the roof
            # over the parked gear (park-first, never-crush via close_observatory),
            # wait for safe-again (debounced), REOPEN, re-acquire, and RESUME.
            if await self._close_for_reopen(dome, reason):
                # UX #8: the roof MOVING is its own event in the report timeline —
                # the artefact used to say "we paused for cloud" and never mention
                # that the observatory shut.
                self._record_safety(f"roof closed over parked gear: {reason}",
                                    "close_roof")
                await self._await_safe_and_reopen(dome, reason, target=target)
                return
            # INVARIANT 2: the never-crush close REFUSED / failed (couldn't confirm a
            # parked mount). Do NOT enter the wait-reopen loop over a still-open roof
            # — FALL BACK to the existing open-sky park-hold pause so the gear is
            # never left in a bad state (the roof was never actuated → still OPEN).
            bus.log("warning", "auto-reopen: roof close refused/failed — holding "
                               "under open sky (park-hold pause)", "safety")
            self._record_safety("roof close refused/failed — gear still under "
                                "open sky", "close_roof_failed")
            await self._park_hold_pause(reason, target)
            return

        if act == "warn":
            return
        if act in ("abort_park_warm", "park"):
            raise SafetyAbort(reason)

        # act == "pause": stop tracking / park-hold and wait for safe-again.
        await self._park_hold_pause(reason, target)

    @staticmethod
    def _escalated_action(act: str, *, closing: bool, cfg) -> str:
        """The action actually TAKEN for an unsafe verdict, after the dome branch
        escalates (UX #8). Pure — the report, the bus event and the log line all
        stamp THIS, so none of them can claim a pause that was really a
        park-and-close.

        * closeable roof + reopen OFF  → ``abort_park_close`` (the run ENDS, the
          shielded wind-down parks then closes the roof), whatever ``on_unsafe``
          said — including ``pause`` and ``warn``.
        * closeable roof + reopen ON   → ``close_roof_wait`` (close over parked
          gear, wait for safe-again, reopen, resume).
        * otherwise                    → the configured action, unchanged."""
        if not closing:
            return act
        return ("close_roof_wait" if (cfg and cfg.safety.reopen_dome_when_safe)
                else "abort_park_close")

    def _record_safety(self, reason: str, action: str) -> None:
        """Append one safety event to the session report. Best-effort — a report
        hiccup must never break the safety path."""
        if self.reporter is None:
            return
        try:
            self.reporter.record_safety(reason, action)
        except Exception:
            pass

    async def _park_hold_pause(self, reason: str, target: Target | None) -> None:
        """The open-sky safety pause (§1.9-A): stop tracking / park-hold, then loop
        re-reading safety until ``resume_safe_consecutive`` clean reads → re-check
        the mount floor+pier and RESUME (re-acquire the target), or ``max_pause_min``
        elapses → SafetyAbort. Extracted verbatim from the old inline pause path so
        it can also serve as the close-refused fallback for the auto-reopen feature
        (INVARIANT 2). Behavior is unchanged for the plain pause caller."""
        cfg = self._cfg
        await self._park_hold()
        self._set_state(state="paused", detail=f"paused (unsafe): {reason}")
        resume_n = max(1, cfg.safety.resume_safe_consecutive if cfg else 3)
        max_pause_s = (cfg.safety.max_pause_min * 60.0) if cfg else 0.0
        pause_started = time.time()
        self._safe_streak = 0
        while True:
            await self._checkpoint()
            await asyncio.sleep(SAFETY_PAUSE_POLL_S)
            reading = await self._read_safety()
            if reading is not None and not reading.stale and reading.is_safe:
                self._safe_streak += 1
                if self._safe_streak >= resume_n:
                    # safe-again: re-check the mount-alt floor + pier BEFORE resuming
                    # (§1.9-A "on resume re-check horizon+pier"). During a long pause
                    # the pointed target can drift below the floor / toward a pier
                    # limit — an unsafe destination escalates to SafetyAbort/park
                    # rather than resuming exposures into the tree.
                    if target is not None:
                        await self._enforce_mount_floor(projected=True, target=target)
                    bus.log("info", "conditions safe again — resuming", "safety")
                    self._record_safety("conditions safe again", "resume")
                    self.hub.publish_safety({"is_safe": True,
                                             "reason": "safe again",
                                             "action": "resume", "stale": False})
                    self._unsafe_streak = 0
                    # CRITICAL: _park_hold turned TRACKING OFF (and stopped
                    # guiding) when we paused, and the sky kept moving while the
                    # mount sat fixed — the target has drifted out of frame. Just
                    # returning into the capture loop would shoot the rest of the
                    # night with tracking off (star trails, wrong field) and every
                    # guiding-recovery attempt failing against a static mount. So
                    # re-run the full target setup: unpark + tracking on, re-center
                    # (goto_and_center) or re-slew, and restart guiding per plan —
                    # exactly as if we were acquiring the target fresh. For a
                    # calibration target (no mount) there is nothing to restore.
                    #
                    # THE COOLER FIRST, same rule as the cloud hold. A safety
                    # pause is exactly as long as the weather says, and every
                    # reason one lasts is a reason the sensor may have drifted.
                    await self._cooler_gate("resumed after a safety pause")
                    if target is not None and not target.calibration:
                        ti = self._index_of_target(target)
                        bus.log("info", f"re-acquiring {target.name} after pause "
                                        "(tracking on, re-center, restart guiding)",
                                "sequence")
                        await self._setup_target(ti, target)
                    self._set_state(state="running", detail="resumed (safe)")
                    return
            else:
                self._safe_streak = 0
            if max_pause_s > 0 and (time.time() - pause_started) >= max_pause_s:
                bus.log("error", "max pause elapsed while unsafe — parking", "safety")
                raise SafetyAbort(f"unsafe for over {cfg.safety.max_pause_min} min: {reason}")

    async def _close_for_reopen(self, dome, reason: str) -> bool:
        """Auto-reopen (PRO-4 D3) — CLOSE step. Park the mount clear (fenced, like
        the wind-down) then close the roof over it via the never-crush
        ``close_observatory`` guard. Returns ``True`` iff the shutter is CONFIRMED
        CLOSED. INVARIANT 1: the close ALWAYS goes through ``close_observatory`` —
        we never command ``close_shutter`` directly. On any refusal/failure returns
        ``False`` (INVARIANT 2: the caller falls back to an open-sky park-hold
        pause) — ``close_observatory`` never touches the shutter when it refuses,
        so the roof stays OPEN and nothing is ever crushed."""
        bus.log("info", f"auto-reopen: closing roof over parked gear — {reason}",
                "safety")
        # Stop guiding + tracking, then PARK (so close_observatory can confirm
        # parked before the roof travels through the mount's volume).
        await self._park_hold()
        await self._fenced_park()
        from .roof import close_observatory
        tel = self.hub.devices.get("telescope")
        return await close_observatory(dome, tel, log=bus.log)

    async def _fenced_park(self) -> None:
        """Park the mount under the hub motion fence+lock (best-effort; never
        raises), mirroring the wind-down park so ``close_observatory`` can confirm
        parked before moving the roof. A park failure/timeout is swallowed here —
        ``close_observatory`` then RE-CONFIRMS ``is_parked`` and REFUSES to move the
        shutter (fail-safe), so a failed park never crushes the mount."""
        tel = self.hub.devices.get("telescope")
        if not (tel and getattr(tel, "connected", False)):
            return
        bus.log("info", "parking mount", "sequence")
        bump = getattr(self.hub, "bump_motion_epoch", None)
        if callable(bump):
            bump()
        try:
            lock = getattr(self.hub, "_motion_lock", None)
            if lock is not None:
                async with lock:
                    await asyncio.wait_for(tel.park(), PARK_TIMEOUT_S)
            else:
                await asyncio.wait_for(tel.park(), PARK_TIMEOUT_S)
        except asyncio.TimeoutError:
            bus.log("warning", f"park timed out after {PARK_TIMEOUT_S:.0f}s during "
                               "auto-reopen close — continuing", "sequence")
        except Exception as e:
            bus.log("warning", f"park failed during auto-reopen close: {e}",
                    "sequence")

    async def _await_safe_and_reopen(self, dome, reason: str, *,
                                     target: Target | None) -> None:
        """Auto-reopen (PRO-4 D3) — WAIT → REOPEN → RESUME step, entered ONLY after
        the roof is confirmed CLOSED. Mirrors the park-hold pause loop but reopens
        the roof instead of resuming under open sky:

        * Debounce (INVARIANT 4): resume ONLY after ``resume_safe_consecutive``
          CONSECUTIVE safe reads — a single safe read then unsafe resets the streak,
          so a passing cloud never cycles the roof.
        * max_pause (INVARIANT 5): still unsafe after ``max_pause_min`` → SafetyAbort
          with the roof LEFT CLOSED (fail-safe; the run's teardown re-confirms closed,
          which is idempotent). 0 = no cap.
        * Reopen (INVARIANT 3): ONLY ``dome.open_shutter()`` (opening never crushes
          the mount — no park gate), then ``_setup_target`` to unpark/re-acquire."""
        cfg = self._cfg
        self._set_state(state="paused",
                        detail=f"roof closed (unsafe): {reason}")
        resume_n = max(1, cfg.safety.resume_safe_consecutive if cfg else 3)
        max_pause_s = (cfg.safety.max_pause_min * 60.0) if cfg else 0.0
        pause_started = time.time()
        self._safe_streak = 0
        while True:
            await self._checkpoint()
            await asyncio.sleep(SAFETY_PAUSE_POLL_S)
            reading = await self._read_safety()
            if reading is not None and not reading.stale and reading.is_safe:
                self._safe_streak += 1
                if self._safe_streak >= resume_n:
                    self._unsafe_streak = 0
                    bus.log("info", "conditions safe again — reopening roof",
                            "safety")
                    # INVARIANT 3: reopen is ONLY open_shutter (opening is safe at
                    # any mount state). Bound + CONFIRM OPEN before we unpark: if the
                    # roof does not confirm OPEN, do NOT slew the OTA under a closed
                    # roof — SafetyAbort and leave the gear parked/closed (fail-safe).
                    try:
                        await asyncio.wait_for(dome.open_shutter(),
                                               DOME_OPEN_TIMEOUT_S)
                        st = await asyncio.wait_for(dome.shutter_state(),
                                                    DOME_QUERY_TIMEOUT_S)
                    except Exception as e:
                        raise SafetyAbort(
                            f"roof reopen failed ({e}) — gear left parked, roof "
                            "not confirmed open")
                    if st is not DomeShutterState.OPEN:
                        raise SafetyAbort(
                            "roof reopen did not confirm OPEN — gear left parked")
                    self._record_safety("roof reopened — conditions safe again",
                                        "reopen_roof")
                    self.hub.publish_safety({"is_safe": True,
                                             "reason": "safe again",
                                             "action": "reopen", "stale": False})
                    # Roof is OPEN again: re-check the mount floor+pier, then re-run
                    # the full target setup (unpark + tracking on, re-center/re-slew,
                    # restart guiding) — the mount was PARKED while we waited, so we
                    # must re-acquire exactly as if fresh. Calibration targets (no
                    # mount) have nothing to restore.
                    if target is not None:
                        await self._enforce_mount_floor(projected=True, target=target)
                    if target is not None and not target.calibration:
                        ti = self._index_of_target(target)
                        bus.log("info", f"re-acquiring {target.name} after roof "
                                        "reopen (unpark, re-center, restart guiding)",
                                "sequence")
                        await self._setup_target(ti, target)
                    self._set_state(state="running",
                                    detail="resumed (roof reopened)")
                    return
            else:
                self._safe_streak = 0
            if max_pause_s > 0 and (time.time() - pause_started) >= max_pause_s:
                # INVARIANT 5: fail-safe — the roof STAYS CLOSED, the run ends.
                bus.log("error", "max pause elapsed while roof closed — leaving "
                                 "roof CLOSED, ending run", "safety")
                raise SafetyAbort(
                    f"unsafe for over {cfg.safety.max_pause_min} min "
                    f"(roof stays closed): {reason}")

    # ------------------------------------------------- sky / rig conditions

    def _observe_clouds(self, info: dict) -> None:
        """Feed one frame's cloud verdict into the debounced state.

        A frame that carries NO verdict feeds nothing. That happens for a
        stretched frame - the contrast metric needs unstretched pixels - and the
        distinction matters: "nobody judged this frame" must not decay into "the
        sky was clear", or a rig whose preview path changed would shoot happily
        through an overcast while the state quietly aged out.
        """
        got = verdict_from_info(info if isinstance(info, dict) else {})
        if got is None:
            return
        cloudy, score, reason = got
        now = time.time()
        before = self._clouds.cloudy(now)
        after = self._clouds.observe(cloudy, now, score=score, reason=reason)
        if after is not before and after is not None:
            # Only on a CHANGE, and only to a definite state. Logging every
            # frame's verdict would bury the night log; logging the transitions
            # is what a human reading it back actually wants.
            bus.log("warning" if after else "info",
                    f"sky verdict: {self._clouds.describe(now)}", "sequence")

    async def _unsafe_now(self) -> bool | None:
        """Tri-state safety, for the ``on_unsafe`` trigger only.

        Deliberately does NOT re-implement the safety policy. ``_safety_gate``
        still owns what happens on an unsafe reading, including the fail-closed
        rule for a stale one. This is a read for RULE EVALUATION, and a reading
        that is missing or stale reports None so a rule fires on evidence rather
        than on silence.
        """
        try:
            reading = await self.hub.safety_reading()
        except Exception:
            return None
        if reading is None or bool(getattr(reading, "stale", False)):
            return None
        return not bool(getattr(reading, "is_safe", True))

    def _panel_ready_now(self) -> bool | None:
        """Tri-state flat-panel readiness. None when there is no panel to ask."""
        cc = self.hub.devices.get("covercalibrator")
        if cc is None or not getattr(cc, "connected", False):
            return None
        try:
            ready = getattr(cc, "ready", None)
        except Exception:
            return None
        return None if ready is None else bool(ready)

    async def _hold_for_clear(self, reason: str, target: Target | None) -> None:
        """Hold the run until the sky clears, and release itself.

        A TWIN OF ``_park_hold_pause``, NOT of ``pause()``. ``pause()`` blocks
        the frame loop at ``_checkpoint`` - the FIRST line of that loop, above
        ``_enforce_stop_boundary``, ``_safety_gate`` and ``_frame_alerts_tick``.
        A weather hold built on it would disarm the dawn stop, the safety gate
        and the dead-man ping together, and sit through sunrise with the
        watchdog silent. It could not release itself either: rules are evaluated
        at frame boundaries and a paused loop has none, so a "clouds clear" rule
        would wait for a boundary that never comes.

        So this loop keeps those three armed itself, and owns its release.

        WHAT IT DOES TO THE RIG, per the HOLD/RESUME node's own defaults
        ("Keep tracking, park guider"):

        * guiding stops - there is nothing to guide on through cloud, and a
          guider hunting a star it cannot see walks the mount;
        * TRACKING STAYS ON, the deliberate difference from the safety hold. The
          target is still up and still ours, and parking would cost a re-slew
          and a re-solve for weather that may pass in ten minutes.
          ``_enforce_stop_boundary`` still ends the night at dawn and
          ``_safety_gate`` still parks the rig if conditions turn genuinely
          unsafe, so "keep tracking" is not "keep tracking no matter what".

        HOW IT SEES THE SKY. It takes a short probe frame every
        ``CLOUD_PROBE_EVERY_S``. That is not an optimisation, it is the whole
        mechanism: the cloud verdict comes from frames, and a hold that took
        none would age its last reading out to "unknown" and then hold forever
        on no evidence at all.
        """
        max_hold_s = 60.0 * CLOUD_MAX_HOLD_MIN
        started = time.time()
        clear_streak = 0
        self._holding_for_clear = True
        self._hold_darks_want = None
        self._hold_darks_taken = 0
        try:
            await self._stand_down_guider()
            bus.log("warning", f"holding for clear sky: {reason}", "sequence")
            self._set_state(
                state="holding", hold="clouds",
                detail=(f"held for cloud - {reason}. Probing every "
                        f"{CLOUD_PROBE_EVERY_S / 60:.0f} min; parks after "
                        f"{max_hold_s / 60:.0f} min"))
            while True:
                # THE THREE THINGS A PAUSE WOULD HAVE DISARMED, kept armed.
                await self._checkpoint()             # a manual pause still works
                self._enforce_stop_boundary(target)  # dawn still ends the night
                await self._safety_gate(context="frame", target=target)
                await self._frame_alerts_tick()      # the deadman still gets fed

                if max_hold_s > 0 and (time.time() - started) >= max_hold_s:
                    # The node's own default is Abort + park, and SafetyAbort is
                    # the engine's existing shielded wind-down: it parks, warms
                    # and finalises the report rather than merely stopping.
                    raise SafetyAbort(
                        f"cloud hold exceeded {max_hold_s / 60:.0f} min - parking")

                # SPEND THE DEAD TIME, if the plan asked for it. A hold is a
                # cooled sensor under a closed sky, which is the condition darks
                # want - and darks matched to the step this hold interrupted are
                # the ones tonight's frames will actually be calibrated by.
                #
                # ONE FRAME PER ITERATION, never a batch. The first version shot
                # the whole shortfall in a single call, and at 180 s x 20 that is
                # sixty minutes inside ONE pass of this loop: the timeout above
                # could not fire (so "parks after 45 min", which this hold tells
                # the operator in its own detail line, was not a bound anything
                # kept), and worse, the sky went unwatched for an hour - so a sky
                # that cleared in minute five would not have been noticed until
                # minute sixty, which is the opposite of what a self-releasing
                # hold is for.
                #
                # The guards and the timeout are re-evaluated between every
                # frame, and the exposure itself becomes the wait: a 180 s dark
                # already exceeds the probe interval, so sleeping afterwards
                # would only delay the next look at the sky.
                took_dark = await self._hold_darks(target)

                if not took_dark:
                    await asyncio.sleep(CLOUD_PROBE_EVERY_S)
                verdict = await self._cloud_probe(target)
                if verdict is None or verdict:
                    clear_streak = 0            # unknown is not clear
                    continue
                clear_streak += 1
                if clear_streak < CLOUD_RESUME_CLEAR_PROBES:
                    continue

                held_min = (time.time() - started) / 60.0
                bus.log("info", f"sky cleared after {held_min:.0f} min - "
                                "re-acquiring the target", "sequence")
                # THE COOLER FIRST, before the mount moves. The handoff's resume
                # checklist is ordered, and this is its head: a hold long enough
                # to matter is long enough for the sensor to have drifted, and
                # re-centring a warm camera just points it accurately at frames
                # no dark will match.
                await self._cooler_gate(f"resumed after {held_min:.0f} min of cloud")
                if target is not None and not target.calibration:
                    # The same restore the safety hold uses, for the same
                    # reason: the sky moved while we sat. _setup_target
                    # re-centres by plate solve and restarts guiding per plan.
                    await self._setup_target(self._index_of_target(target), target)
                self._set_state(state="running", hold=None,
                                detail=f"resumed after {held_min:.0f} min of cloud")
                return
        finally:
            self._holding_for_clear = False

    def _hold_darks_shortfall(self, step, quota: int) -> tuple[int, int]:
        """``(to_shoot, already_banked)`` for a hold at ``step``'s settings.

        THE QUEUE'S if-stale POLICY, and the reason it can exist at all: the
        calibration matrix counts RAW SUBS now, not just stacked masters, so
        "what does the library already have at exactly these settings" is finally
        an answerable question. Asked through ``health_matrix`` — the same call
        the Calibration Matrix panel renders — so the panel and the engine cannot
        give the operator two different answers about one library.

        Matched on ``plan.cool_to``, because a dark is indexed by temperature and
        a warm one is cover for nothing.

        FAILS TO THE OLD BEHAVIOUR, not to zero. If the library cannot be read —
        no walk, a broken header, a missing hub attribute — this returns the full
        quota, because a hold that cannot count is still a hold that should build
        darks. Returning 0 would let one unreadable file quietly cancel the
        feature, which is the silent-loss shape this codebase keeps finding.
        """
        lib = getattr(self.hub, "master_library", None)
        if lib is None:
            return quota, 0
        try:
            from ..calibration.matcher import LightNeed
            from ..flows.calibration_health import (CalNeed, frame_from_header,
                                                    health_matrix)
            need = CalNeed(LightNeed(
                exposure_s=float(step.exposure_s), gain=int(step.gain),
                offset=int(step.offset),
                temp_c=getattr(self.plan, "cool_to", None),
                binning=int(step.binning), filter=str(step.filter or "")))
            frames = [f for f in (frame_from_header(h, ts=ts, path=str(p))
                                  for p, h, ts in lib.iter_cal_headers())
                      if f is not None]
            rows = health_matrix([need], frames, masters=lib.list_masters(),
                                 quota=quota, kinds=("DARK",))
            if not rows:
                return quota, 0
            return max(0, rows[0].need - rows[0].have), rows[0].have
        except Exception as exc:                  # noqa: BLE001 - reported
            bus.log("warning", f"cloud hold: could not read the dark library "
                               f"({exc}) - shooting the full quota of {quota}",
                    "sequence")
            return quota, 0

    async def _hold_darks(self, target: Target | None) -> bool:
        """Shoot ONE dark matched to the interrupted step. ``True`` if it did.

        ONE FRAME, NOT THE SET. The caller runs this once per pass of the hold
        loop, so the hold's timeout and all four guards are re-evaluated between
        every frame and the sky is probed between every frame. A batch call here
        (the first version) put the whole shortfall inside a single loop pass:
        at 180 s x 20 that is an hour in which the 45-minute cap could not fire
        and nothing looked up.

        The shortfall is measured ONCE per hold and then counted down, so the
        library is read once rather than re-walked before every frame.

        Everything is best-effort: a failure logs and returns False, because the
        hold's job is to survive the weather and a calibration hiccup must not
        turn a passing cloud into an aborted night.
        """
        quota = int(getattr(self.plan, "cloud_hold_darks", 0) or 0)
        step = self._hold_step
        if quota <= 0 or step is None:
            return False

        if self._hold_darks_want is None:
            want, have = self._hold_darks_shortfall(step, quota)
            self._hold_darks_want = want
            if want <= 0:
                # THE QUEUE'S "if library stale" HALF. Shooting the quota on
                # every hold would spend a cloudy night re-taking darks the rig
                # already owns, on a disk that fills.
                bus.log("info", f"cloud hold: the library already holds {have} "
                                f"darks at {step.exposure_s:g}s g{step.gain} - "
                                f"holding without shooting", "sequence")
                self._set_state(detail="held for cloud - dark library already full")
            else:
                bus.log("info", f"cloud hold: building {want} darks at "
                                f"{step.exposure_s:g}s g{step.gain} one at a "
                                f"time ({have} of {quota} already banked)",
                        "sequence")

        if self._hold_darks_taken >= (self._hold_darks_want or 0):
            return False
        try:
            dark = Target(
                name=f"cloud-hold darks {step.exposure_s:g}s g{step.gain}",
                ra_hours=0.0, dec_deg=0.0,
                calibration=True, center=False, autofocus_first=False,
                steps=[ExposureStep(
                    # MATCHED TO THE STEP THIS HOLD INTERRUPTED. A dark library
                    # is indexed by exposure, gain, offset, binning and
                    # temperature; anything else is a dark for a different night.
                    # No filter named, which is what drives the wheel to the
                    # blackout slot - see _apply_filter.
                    exposure_s=step.exposure_s, gain=step.gain,
                    offset=step.offset, binning=step.binning,
                    count=1, frame_type="Dark")])
            self._hold_darks_taken += 1
            self._set_state(
                detail=f"held for cloud - dark {self._hold_darks_taken}"
                       f"/{self._hold_darks_want} at {step.exposure_s:g}s")
            await self._run_calibration(self._index_of_target(target)
                                        if target is not None else 0, dark)
            return True
        except SafetyAbort:
            raise
        except Exception as exc:                  # noqa: BLE001 - reported
            bus.log("warning", f"cloud-hold darks failed ({exc}) - still holding",
                    "sequence")
            return False

    async def _stand_down_guider(self) -> None:
        """Stop guiding, leave the mount tracking. Best-effort and never raises:
        a wedged guider must not be able to prevent a weather hold."""
        try:
            if self.hub.guider and self.hub.guider.connected:
                await asyncio.wait_for(self.hub.guider.stop_guiding(),
                                       GUIDE_OP_TIMEOUT_S)
        except (asyncio.TimeoutError, Exception):
            pass

    async def _cloud_probe(self, target: Target | None) -> bool | None:
        """One short exposure, judged. Returns cloudy / clear / unknown.

        A failure returns None rather than raising: a probe that could not be
        taken is not a clear sky, and a hold that aborted because one exposure
        failed would turn a passing cloud into a lost night.
        """
        step = self._probe_step()
        if step is None:
            return None
        try:
            info = await self._capture(step, target,
                                       exposure_s=CLOUD_PROBE_EXPOSURE_S)
        except Exception as e:                    # noqa: BLE001 - reported
            bus.log("warning", f"cloud probe failed ({e}) - still holding",
                    "sequence")
            return None
        self._observe_clouds(info)
        got = verdict_from_info(info if isinstance(info, dict) else {})
        return None if got is None else got[0]

    def _probe_step(self):
        """The step a probe frame is taken with.

        The CURRENT step, so the probe sees the sky through the same filter the
        science frames do. A probe through a different filter would score
        differently and the debounced state would read an optical change as
        weather.
        """
        return self._hold_step

    async def _park_hold(self) -> None:
        """Stop tracking (park-hold) when pausing for safety so the mount isn't
        left tracking a target into the pier (C2-6). Best-effort; never raises.
        Both calls are bounded (P0-2) so a wedged guider/mount can't hang the
        safety-pause hold; a timeout here is swallowed like any other failure."""
        try:
            if self.hub.guider and self.hub.guider.connected:
                await asyncio.wait_for(self.hub.guider.stop_guiding(),
                                       GUIDE_OP_TIMEOUT_S)
        except (asyncio.TimeoutError, Exception):
            pass
        try:
            tel = self.hub.devices.get("telescope")
            if tel and tel.connected:
                await asyncio.wait_for(tel.set_tracking(False), MOUNT_QUERY_TIMEOUT_S)
        except (asyncio.TimeoutError, Exception):
            pass

    async def current_safety(self):
        """The cached safety verdict, seed-wait included — the same read this
        class gates a run on, exposed for callers that MOVE THE MOUNT before
        ``start()`` runs. ResumeArm's recovery ladder is one: it plate-solves and
        re-centers first, so gating only inside the run left those motions
        unguarded."""
        return await self._read_safety()

    async def check_slew_limits(self, target: Target, *, cfg=None, plan=None,
                                projected: bool = True) -> None:
        """Raise ``SafetyAbort`` if slewing to ``target`` breaks the altitude
        floor, horizon, no-go wedges, pier limits or the zenith keep-out.

        The public seam over :meth:`_enforce_mount_floor`, which reads the run's
        config snapshot. Pass ``cfg`` when there is no run yet (recovery after a
        restart) so the gate uses live configuration instead of ``None`` — which
        is a silent no-op.

        PASS ``plan`` FOR THE SAME REASON, and it is not optional in practice
        for a no-run caller: the pier-collision branch reads
        ``plan.meridian_flip``, so with ``self.plan`` still None in a fresh
        post-reboot process the pier half of this gate silently did nothing
        while the altitude half ran — a partial guard that looks like a whole
        one. ResumeArm has the plan in hand and passes it."""
        await self._enforce_mount_floor(projected=projected, target=target,
                                        cfg=cfg, plan=plan)

    async def _enforce_mount_floor(self, *, projected: bool, target: Target,
                                   cfg=None, plan=None) -> None:
        """Mount-altitude floor + pier guard before a slew (§1.9-A/E).

        Evaluates the slew DESTINATION (``target.ra_hours``/``dec_deg``) → alt/az
        against ``max(min_alt_deg, interp(horizon, az))``. ``projected`` advances
        time by a slew+solve margin so a setting target isn't accepted right as it
        drops below the floor during the slew. A definite unsafe destination pier
        side (when ``enforce_pier_limits`` and the mount reports it) is an active
        pier-collision risk → SafetyAbort.

        Historically this read the mount's CURRENT pointing (``get_position``),
        which is the WRONG end of the slew: it could pass a slew to a low target
        (mount currently high) AND abort the whole night when the mount was parked
        horizon-pointing (alt ~0). Guarding the destination fixes both.

        ``cfg`` and ``plan`` override the run's snapshots for callers that gate
        a slew with no run in flight (see :meth:`check_slew_limits`)."""
        cfg = cfg if cfg is not None else self._cfg
        plan = plan if plan is not None else self.plan
        if cfg is None:
            return
        floor_base = float(cfg.safety.min_alt_deg or 0.0)
        horizon = cfg.safety.horizon
        nogo = cfg.safety.nogo_box
        pier = cfg.safety.enforce_pier_limits

        tel = self.hub.devices.get("telescope")
        if tel is None or not getattr(tel, "connected", False):
            return

        # pier-collision guard: a mount that reports a definite unsafe destination
        # side blocks the slew (only when enabled + the mount supports it).
        if pier and getattr(tel, "reports_destination_pier_side", False):
            try:
                side = await tel.destination_pier_side(target.ra_hours, target.dec_deg)
            except Exception:
                side = PierSide.UNKNOWN
            # the "unsafe" side is the one the mount cannot reach without a flip;
            # we only hard-stop on a *definite* east/west report (UNKNOWN passes).
            cur = PierSide.UNKNOWN
            try:
                cur = await tel.pier_side()
            except Exception:
                cur = PierSide.UNKNOWN
            if (side in (PierSide.EAST, PierSide.WEST)
                    and cur in (PierSide.EAST, PierSide.WEST)
                    # `plan is not None`, NOT truthiness: a plan object that
                    # happens to be falsy would silently disarm this guard the
                    # same way `self.plan` being None already did on the
                    # post-restart recovery path.
                    and side != cur
                    and plan is not None and not plan.meridian_flip):
                # a pier-side change with flips disabled is a collision risk.
                raise SafetyAbort(
                    f"slew to {target.name} would require a pier flip but meridian "
                    "flip is disabled")

        # The CEILING is read here, alongside the floor, because the early return
        # below used to sit between the two: a rig with no floor configured
        # (min_alt_deg = 0 — "I have no tree line", an ordinary setting) returned
        # before the ceiling check and had its zenith keep-out silently disarmed,
        # while the Settings panel went on promising it. The two ends of the sky
        # are independent limits and neither may gate the other.
        ceiling = schedule.effective_ceiling(
            getattr(getattr(cfg, "safety", None), "max_alt_deg", None))
        has_floor = floor_base > 0.0 or bool(horizon) or bool(nogo)
        has_ceiling = ceiling < schedule.NO_CEILING_DEG
        if not has_floor and not has_ceiling:
            return      # neither end configured → nothing to enforce

        # DESTINATION alt/az now (and projected forward across the slew+solve), so
        # the guard blocks a slew TO a low target and never trips on where the
        # mount happens to point right now (e.g. a horizon-pointing park position).
        from ..catalog import altaz
        lat = self.hub.site["latitude"]
        lon = self.hub.site["longitude"]
        ra, dec = target.ra_hours, target.dec_deg
        now = time.time()
        alt_now, az_now = altaz(ra, dec, lat, lon, now)
        worst_alt, worst_az = alt_now, az_now
        if projected:
            alt_p, az_p = altaz(ra, dec, lat, lon, now + SLEW_PROJECT_S)
            if alt_p < worst_alt:
                worst_alt, worst_az = alt_p, az_p
        floor = schedule.effective_floor(floor_base, horizon, worst_az, nogo)
        if worst_alt < floor:
            raise SafetyAbort(
                f"target {target.name} altitude {worst_alt:.0f}° below safety floor "
                f"{floor:.0f}° (az {worst_az:.0f}°)")
        # And the CEILING. A mount can foul its own tripod at HIGH altitude with
        # the optics still on open sky; every other limit here is a minimum, so
        # nothing had an opinion about it (#101, observed on the AM5N). Checked
        # against the HIGHEST altitude across the slew window, which is the
        # opposite end from the floor's worst case — a target rising toward the
        # keep-out must be refused before it gets there, not after.
        best_alt, best_az = alt_now, az_now
        if projected:
            alt_p, az_p = altaz(ra, dec, lat, lon, now + SLEW_PROJECT_S)
            if alt_p > best_alt:
                best_alt, best_az = alt_p, az_p
        if best_alt > ceiling:
            raise SafetyAbort(
                f"target {target.name} altitude {best_alt:.0f}° above the "
                f"zenith keep-out {ceiling:.0f}° (az {best_az:.0f}°) — the mount "
                "can reach its own tripod up there")

    # ----------------------------------------------------------- watchdog (§1.9-F)

    def _start_watchdog(self) -> None:
        cfg = self._cfg
        if cfg is None or cfg.escalation.no_progress_watchdog_s <= 0:
            return
        if self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = asyncio.create_task(self._watchdog_loop())

    def _stop_watchdog(self) -> None:
        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
        self._watchdog_task = None

    async def _watchdog_loop(self) -> None:
        """No-progress watchdog (§1.9-F): wakes every WATCHDOG_TICK_S and, while
        the run is RUNNING and not paused, fires a safety-style ``warn`` event +
        log when no frame has been recorded within ``no_progress_watchdog_s``.
        Non-fatal (warn-level) so it never on its own aborts the night — it raises
        the alarm the unattended operator needs."""
        cfg = self._cfg
        threshold = cfg.escalation.no_progress_watchdog_s if cfg else 0
        if threshold <= 0:
            return
        warned = False
        try:
            while True:
                await asyncio.sleep(WATCHDOG_TICK_S)
                warned = self._watchdog_check(threshold, warned)
        except asyncio.CancelledError:
            raise

    def _watchdog_check(self, threshold: float, warned: bool) -> bool:
        """One watchdog evaluation (pure of sleeping — unit-testable). Returns the
        next ``warned`` latch state, publishing the no-progress ``warn`` edge once
        per stall.

        Only fires while frames are EXPECTED to flow (``_progress_expected``): the
        engine deliberately stays state='running' with no frames during a scheduled
        inter-target wait, a slew/center/AF setup, and cooling. Firing there paged
        a false 'UNSAFE: no progress' at 1am mid-plan AND flipped the AlertDispatcher
        safe/unsafe latch, corrupting later real safety alerts (review §1.9-F)."""
        if self.paused or self.state.get("state") != "running" \
                or not self._progress_expected:
            return False
        idle = time.time() - self._last_frame_at
        if idle > threshold:
            if not warned:
                action = (self._cfg.safety.on_unsafe if self._cfg else "warn")
                bus.log("error",
                        f"no frame in {idle / 60:.0f} min — possible stall",
                        "safety")
                self.hub.publish_safety(
                    {"is_safe": False,
                     "reason": f"no progress in {idle / 60:.0f} min",
                     "action": action, "stale": False})
                self._record_safety("no-progress watchdog", action)
                # HAND THE TRIP TO THE RUN TASK. This used to end here: a log, a
                # warn-shaped publish, and nothing else, whatever on_unsafe said
                # — while the setting's own UI copy promised "Ends the run after
                # N min with nothing saved." A stalled unattended night stayed
                # stalled, tracking, until dawn.
                #
                # Set a flag rather than acting here, because acting means
                # _on_unsafe, and _on_unsafe's pause/abort paths only work from
                # the run task: SafetyAbort has to reach _run's except chain to
                # get the shielded park/warm wind-down, and an exception raised
                # in this background task reaches nobody. The run task consumes
                # it at the next frame boundary.
                #
                # That is sufficient because every device await in the run is
                # _bounded (P0-2), so a genuinely wedged device surfaces as a
                # timeout exception rather than an unbounded stall. What this
                # watchdog is actually for is a loop that keeps cycling without
                # recording frames — persistent rejects, an accepted-mode quota
                # clouds will never satisfy — and that loop reaches a boundary
                # every pass.
                self._watchdog_tripped = f"no progress in {idle / 60:.0f} min"
            return True
        return False

    def _begin_frame(self, ti: int, si: int, exposure_s: float) -> None:
        """Mark the in-flight exposure for the sub-frame bar + ETA off-by-one
        guard (set immediately before ``hub.capture``).

        NB: ``_frame_had_event`` is intentionally NOT reset here. The dither/AF/
        flip blocks set it True *before* this runs, and ``_record_frame`` must
        still see it True so the event wall-time is excluded from the overhead
        EMA (it is accounted analytically). The flag is reset in ``_record_frame``
        AFTER it is read (P2-1)."""
        self._active_step = (ti, si)
        self._cur_exposure_s = float(exposure_s)
        self._frame_started_at = time.time()

    def _record_frame(self, key: str, i: int, target: Target, step, info: dict,
                      *, accepted: bool = True) -> None:
        now = time.time()
        # Per-frame overhead EMA: cadence minus exposure, EXCLUDING any frame that
        # carried a dither/AF/flip (those are accounted analytically, so folding
        # them into the per-frame overhead would double-count and whipsaw the
        # finish clock). α=0.1 keeps one cloud-slowed frame from whipsawing it
        # (spec §5.2).
        if self._last_frame_done > 0 and self._cur_exposure_s > 0 \
                and not getattr(self, "_frame_had_event", False):
            overhead = (now - self._last_frame_done) - self._cur_exposure_s
            if overhead > 0:
                self._overhead_ema = ((1 - OVERHEAD_EMA_ALPHA) * self._overhead_ema
                                      + OVERHEAD_EMA_ALPHA * overhead)
                self._overhead_samples += 1
        # reset the flag only AFTER reading it above, so the dither/AF/flip set
        # earlier this frame is honored for this frame's overhead and cleared for
        # the next (P2-1). Final order: begin → set-flag → capture → read → reset.
        self._frame_had_event = False
        self._last_frame_done = now
        self._last_frame_at = now              # watchdog progress stamp (§1.9-F)
        self._frame_started_at = 0.0   # frame complete — no longer in flight
        self._done[key] = i + 1
        self._frames_done += 1
        # ledger append + atomic session save replaces the retired resume-file
        # _persist (same per-frame write cost — sessions spec §3).
        self._record_session_frame(target, step, info, auto_accepted=accepted)
        self._set_state()

    def _record_session_frame(self, target: Target, step, info: dict,
                              *, auto_accepted: bool) -> SessionFrame | None:
        """Append one ledger entry (metrics: hfr / stars / guide_rms /
        sensor_temp_c — floats only, absent when unmeasured) + save the session
        atomically. Best-effort: a ledger hiccup must never break capture."""
        if self._session is None:
            return None
        metrics: dict[str, float] = {}
        if isinstance(info, dict):
            if info.get("hfr") is not None:
                metrics["hfr"] = float(info["hfr"])
            if info.get("stars") is not None:
                metrics["stars"] = float(info["stars"])
        try:
            if self.hub.guider and self.hub.guider.connected:
                rms = getattr(self.hub.guider.stats(), "rms_total", None)
                if rms is not None:
                    metrics["guide_rms"] = float(rms)
        except Exception:
            pass
        frame = getattr(self.hub, "last_frame", None)
        temp = getattr(frame, "temperature_c", None) if frame is not None else None
        if temp is not None:
            metrics["sensor_temp_c"] = float(temp)
        saved = info.get("saved_path") if isinstance(info, dict) else None
        sf = SessionFrame(ts=time.time(),
                          night=self.reporter.id if self.reporter else "",
                          target_id=target.id, step_id=step.id,
                          path=str(saved) if saved else "", metrics=metrics,
                          auto_accepted=auto_accepted)
        try:
            self._session.frames.append(sf)
            session_store.save(self._session)
        except Exception as e:
            bus.log("warning", f"session ledger write failed: {e}", "sequence")
        self._spawn_thumb(sf)
        return sf

    def _spawn_thumb(self, sf: SessionFrame | None) -> None:
        """Fire-and-forget ~512px review thumbnail (spec §3). Best-effort by
        design: NINA frames carry no raw array (data is None) and any render
        failure simply leaves ``thumb=None`` — capture is never blocked.

        Back-pressure (Task 6 review, Important #2): the render holds a
        full-res ndarray closure, so a fast burst must never queue them
        unbounded. Once ``_MAX_PENDING_THUMBS`` renders are already in
        flight this DROPS the new one outright (thumb stays None, logged at
        debug) rather than awaiting/semaphore-queuing — nothing here may
        block the capture loop. The task is tracked in ``self._thumb_tasks``
        so abort()/teardown can cancel + await it instead of orphaning it."""
        if self._session is None or sf is None:
            return
        frame = getattr(self.hub, "last_frame", None)
        data = getattr(frame, "data", None)
        if data is None:
            return
        if len(self._thumb_tasks) >= _MAX_PENDING_THUMBS:
            bus.log("debug", "thumb render dropped: pending queue saturated "
                             f"(>= {_MAX_PENDING_THUMBS} in flight)", "sequence")
            return
        try:
            task = asyncio.create_task(self._render_thumb(self._session, sf, data))
        except RuntimeError:
            return                            # no running loop (defensive)
        # Tag the render with its session id so DELETE (Task 7) can drain ONLY
        # the renders writing into the session being removed — a natural
        # completion leaves these fire-and-forget tasks live (only abort()
        # drains), and their trailing save would resurrect a just-deleted JSON.
        task._astro_session_id = self._session.id
        self._thumb_tasks.add(task)
        task.add_done_callback(self._thumb_tasks.discard)

    async def _render_thumb(self, session: Session, sf: SessionFrame,
                            data) -> None:
        """Encode + write the thumb off-thread, then stamp the relative path
        onto the ledger frame and re-save the session (event-loop-serialized
        with the capture loop's own saves, so writes never interleave)."""
        try:
            jpeg, _w, _h = await asyncio.to_thread(
                to_jpeg, data, max_width=512)  # auto-stretch, ~512px long edge
            tdir = session_store.thumbs_dir(session.id)
            await asyncio.to_thread(tdir.mkdir, parents=True, exist_ok=True)
            path = tdir / f"{sf.id}.jpg"
            await asyncio.to_thread(path.write_bytes, jpeg)
            sf.thumb = f"thumbs/{sf.id}.jpg"
            session_store.save(session)
        except Exception as e:
            bus.log("warning", f"thumb render failed: {e}", "sequence")

    # ----------------------------------------------------------- sub-routines

    async def _cool_and_wait(self, target_c: float, timeout_s: int) -> bool:
        """Cool the camera to ``target_c`` and wait for it to stabilize.

        Returns ``True`` when the cooler reached the band (or there is no
        coolable camera — nothing to wait on), ``False`` on a cool-timeout / a
        failed cooler command.

        P1-7 escalation (``cfg.escalation.require_cooling`` + ``cooling_action``):
        the default ``cooling_action == "warn"`` keeps the historical fail-open
        behavior (log + continue, shoot whatever the temp is). When cooling is
        REQUIRED, a cool-timeout no longer silently shoots warm lights:
        ``abort`` → ``SafetyAbort`` (tear the night down through the wind-down);
        ``skip`` → return ``False`` so the caller skips the light run. All cooler
        device calls are bounded (P0-2)."""
        cfg = self._cfg
        require = bool(cfg and cfg.escalation.require_cooling)
        action = (cfg.escalation.cooling_action if cfg else "warn")

        cam = self.hub.devices.get("camera")
        # THREE DIFFERENT SITUATIONS, and one `return True` used to cover all of
        # them in silence. Measured cost on the rig, 2026-08-12: a redeploy at
        # 23:39 restarted the server, the run was started seconds later while
        # the camera was still connecting, this guard answered "nothing to wait
        # on", and 53 of the night's 83 frames were shot at +16 °C against a
        # -5 °C dark library. Nothing in the log said cooling had been skipped,
        # because the log line sat BELOW this return.
        if cam is not None and cam.connected \
                and not getattr(cam, "can_cool", False):
            # A camera that genuinely has no cooler. Proceeding is right; doing
            # it silently is not, because the plan asked for a temperature.
            bus.log("info", f"this camera cannot cool — shooting at ambient "
                            f"though the plan asked for {target_c:g}°C",
                    "sequence")
            return True
        if cam is None or not cam.connected:
            # NOT the same thing, and the difference is the whole defect: a
            # camera that has not finished connecting is a TRANSIENT state,
            # commonest in the first seconds after a restart — exactly when a
            # resumed or restarted run begins. Heal it the way every frame
            # boundary does, then look again.
            await self._reconnect_gate()
            cam = self.hub.devices.get("camera")
        if cam is None or not cam.connected:
            return self._cooling_failed(
                require, action,
                f"the plan asked to cool to {target_c:g}°C and the camera is "
                f"not connected")
        if not getattr(cam, "can_cool", False):
            bus.log("info", f"this camera cannot cool — shooting at ambient "
                            f"though the plan asked for {target_c:g}°C",
                    "sequence")
            return True
        self._set_state(state="running", detail=f"cooling to {target_c:g}°C")
        bus.log("info", f"cooling camera to {target_c:g}°C", "sequence")
        try:
            # Through the hub, not straight at the device: a warm-down ramp from
            # the last run can still be walking the setpoint up (it is a hub-owned
            # background task and deliberately outlives the run that started it),
            # and hub.cool_camera CANCELS it before commanding. Without that, the
            # ramp's next step — at most 15 s away — would quietly raise the
            # setpoint we just set, and the plan's cooling wait would sit there
            # watching the sensor climb away from its target.
            cooler = getattr(self.hub, "cool_camera", None)
            if callable(cooler):
                await asyncio.wait_for(cooler(target_c), COOLER_CMD_TIMEOUT_S)
            else:
                await asyncio.wait_for(cam.set_cooler(True, target_c),
                                       COOLER_CMD_TIMEOUT_S)
        except (asyncio.TimeoutError, Exception) as e:
            bus.log("warning", f"cooler command failed: {e}", "sequence")
            return self._cooling_failed(require, action,
                                        f"cooler command failed: {e}")
        # TWO CLOCKS, because "reached the band" and "stayed in it" are different
        # questions and one deadline cannot ask both. `deadline` bounds the
        # descent, which is what `cool_timeout_s` was always about. Entering the
        # band then buys COOLER_STABLE_S of settle on its own budget - otherwise
        # a tight cool_timeout_s would make the settle unsatisfiable and the gate
        # would fail runs that were doing exactly the right thing.
        deadline = time.time() + timeout_s
        in_band_since: float | None = None
        # A SENSOR THAT WAS NEVER OUT OF BAND NEVER CROSSED IT. The settle exists
        # to catch a camera sampled mid-descent; if every probe since the cooler
        # was commanded has read in-band, there was no descent to be caught in
        # the middle of, and a full two minutes of waiting proves nothing that
        # the readings have not already shown.
        #
        # This is what keeps an already-cold rig - and every simulator run, which
        # reports the setpoint immediately - from paying two minutes per run and
        # two more per cloud hold. It does not weaken the #204 case: the cooler
        # is commanded ON before the first probe, so a warm-but-still-cold sensor
        # after a restart is actively cooling by the time it reads in band.
        saw_out_of_band = False
        while True:
            now = time.time()
            if in_band_since is None and now >= deadline:
                return self._cooling_failed(require, action,
                                            "cooler did not reach its band in time")
            if in_band_since is not None and now - in_band_since >= COOLER_SETTLE_MAX_S:
                # In the band, but bouncing across it for longer than any real
                # settle takes. Reporting stable would be a guess.
                return self._cooling_failed(require, action,
                                            "cooler reached its band but never settled")
            await self._checkpoint()
            try:
                t = await asyncio.wait_for(cam.get_temperature(), COOLER_CMD_TIMEOUT_S)
            except (asyncio.TimeoutError, Exception):
                t = None
            if t is not None and abs(t - target_c) <= COOLER_AT_TARGET_C:
                if in_band_since is None:
                    in_band_since = now
                held = now - in_band_since
                # STABLE MEANS HELD, NOT TOUCHED. A sensor on its way down
                # crosses the band, and the old check returned on that first
                # sample - so "cooler stable at -5.0°C" could be printed about a
                # camera still falling through -5 towards -12, and the frames
                # that followed carried a SET-TEMP nothing had settled at.
                need = COOLER_STABLE_S if saw_out_of_band else 0.0
                if held >= need:
                    bus.log("info",
                            f"cooler stable at {t:.1f}°C"
                            + (f" (held {held:.0f}s)" if saw_out_of_band
                               else " (never left the band)"),
                            "sequence")
                    return True
                self._set_state(
                    detail=f"cooler settling at {t:.1f}°C "
                           f"({held:.0f}/{need:.0f}s)")
            else:
                # Left the band again: the settle starts over, because a
                # half-completed hold either side of an excursion is not a hold.
                saw_out_of_band = True
                in_band_since = None
                self._set_state(
                    detail=(f"waiting on cooler: {t:.1f}°C → {target_c:g}°C"
                            if t is not None else "waiting on cooler"))
            await asyncio.sleep(COOLER_PROBE_EVERY_S)

    async def _cooler_gate(self, why: str) -> None:
        """Block until the sensor is back at setpoint and stable. THE gate.

        The handoff's HOLD / RESUME checklist leads with this, and the order is
        the point: cooler, then filter, then re-center, then refocus, then
        guiding. Pointing and focus are worth nothing on a frame the temperature
        already ruined, and a night's darks are indexed by temperature - a warm
        light has no matching dark in the library and never will.

        WHY A HOLD NEEDS ITS OWN GATE AT ALL. A run cools once at start, and
        every reason a hold exists is also a reason cooling might have stopped
        inside it: a daybreak park warms the camera BY DESIGN, a power cycle
        drops the TEC, a cooler fault drifts. The hold ends and the loop resumes
        into whatever the sensor happens to read. That is not hypothetical: on
        2026-08-12 this rig put 63 frames on disk at +16 °C against a -5 °C
        library, and nothing in the night log said cooling had stopped.

        NO-OP WITHOUT COOLING INTENT. A plan with `cool_to=None` never asked for
        a temperature, and blocking such a run on a cooler it does not use would
        stop uncooled rigs dead. `_cool_and_wait` owns every other decision,
        including the escalation policy, so this cannot disagree with the
        start-of-run gate about what "cool enough" means.
        """
        target = getattr(self.plan, "cool_to", None) if self.plan else None
        if target is None:
            return
        bus.log("info", f"cooler gate ({why}): checking the sensor before capture",
                "sequence")
        await self._cool_and_wait(
            target, getattr(self.plan, "cool_timeout_s", 900))

    def _cooling_failed(self, require: bool, action: str, reason: str) -> bool:
        """Apply ``cfg.escalation.cooling_action`` on a cooling failure (P1-7).
        Returns ``False`` (cooling did not succeed). ``abort`` raises SafetyAbort;
        ``warn`` (default) and a non-required failure just log + continue."""
        if require and action == "abort":
            bus.log("error", f"cooling required but {reason} — aborting", "sequence")
            raise SafetyAbort(f"cooling required but {reason}")
        if require and action == "skip":
            bus.log("warning", f"cooling required but {reason} — skipping lights",
                    "sequence")
        else:
            # warn / not-required: legacy fail-open (shoot whatever temp we have).
            bus.log("warning", f"{reason} — continuing", "sequence")
        return False

    async def _apply_filter(self, step) -> None:
        if "filterwheel" not in self.hub.devices:
            return
        # `require` RAISES when a device is registered but not connected, and
        # the two cases want opposite things from that:
        #
        #   a step that NAMES a filter cannot be honoured without the wheel, so
        #   a disconnected wheel is a real error and is raised, as it always was;
        #
        #   a step with no filter never needed the wheel at all. Raising there
        #   would turn a wheel that dropped out mid-night into an aborted run,
        #   when the old behaviour — keep shooting, the wheel is not in the way —
        #   is both safe and what the user expects. (Introduced by the blackout
        #   slot work, which moved this call ahead of the empty-filter check.)
        if not step.filter:
            fw = self.hub.devices.get("filterwheel")
            if fw is None or not getattr(fw, "connected", False):
                return
        else:
            fw = self.hub.require("filterwheel")
        if step.filter:
            if step.filter not in fw.filter_names:
                bus.log("warning", f"filter '{step.filter}' not in wheel — skipping move", "sequence")
                return
            new_slot = fw.filter_names.index(step.filter)
            label = step.filter
        else:
            # No filter named. A dark or a bias wants NO light path, so a wheel
            # with a blackout slot drives to it — that is the whole reason the
            # slot exists. Every other frame type (and every wheel without a
            # blackout slot) stays where it is, exactly as before.
            dark = fw.dark_slot()
            if dark is None or (step.frame_type or "Light").strip().lower() \
                    not in ("dark", "bias"):
                return
            new_slot = dark
            label = fw.filter_names[dark] or f"slot {dark}"
        # P0-2: every device await here is BOUNDED. AlpacaFilterWheel.set_position
        # polls ``while position == -1`` where each HTTP request SUCCEEDS, so a
        # jammed wheel reporting -1 forever never trips a transport timeout — it
        # would hang the whole night inside this await. A _bounded timeout escalates
        # through the SafetyAbort teardown like any other wedged device I/O.
        old_slot = await _bounded(fw.get_position(), FILTER_MOVE_TIMEOUT_S,
                                  "filter get_position")
        if new_slot == old_slot:
            return
        self._set_state(detail=f"filter → {label}")
        await _bounded(fw.set_position(new_slot), FILTER_MOVE_TIMEOUT_S,
                       f"filter → {label}")
        # shift focus by the per-filter offset delta (offsets are relative, so
        # an incremental delta keeps focus correct as long as we step through
        # changes). Skipped when either end of the move is a blackout slot: its
        # offset is a placeholder zero, not a measurement, so honouring it would
        # yank the focuser to the reference position and back for a dark.
        offsets = getattr(fw, "filter_offsets", []) or []
        if self.plan.apply_filter_offsets and "focuser" in self.hub.devices \
                and len(offsets) > max(new_slot, old_slot) \
                and not fw.is_opaque(new_slot) and not fw.is_opaque(old_slot):
            delta = offsets[new_slot] - offsets[old_slot]
            if delta:
                foc = self.hub.require("focuser")
                pos = await _bounded(foc.get_position(), FOCUSER_MOVE_TIMEOUT_S,
                                     "focuser get_position")
                await _bounded(foc.move_to(pos + delta), FOCUSER_MOVE_TIMEOUT_S,
                               "focuser offset move")
                bus.log("info", f"applied filter offset {delta:+d} for {label}", "sequence")

    async def _maybe_meridian_flip(self, target: Target,
                                   next_exposure_s: float = 0.0) -> None:
        if not self.plan.meridian_flip or not self._flip_armed:
            return
        tel = self.hub.devices.get("telescope")
        if not tel or not tel.connected:
            return
        # authoritative countdown = server HA math (the device value alone never
        # goes negative, so it can't detect the crossing — the live bug).
        try:
            lon = self.hub.site["longitude"]
            ttf_h = schedule.hours_to_meridian_flip(target.ra_hours, lon)
        except Exception:
            return
        # fold in the device's own value ONLY when it reports a sooner positive
        # countdown (a mount enforcing a tighter minutes-after-meridian limit
        # must be allowed to flip earlier — never later, so a wrapped ~12h device
        # value can't push the flip past the meridian).
        try:
            dev = await _bounded(tel.time_to_meridian_flip(),
                                 MOUNT_QUERY_TIMEOUT_S, "meridian-flip query")
        except SafetyAbort:
            raise
        except Exception:
            dev = None
        if dev is not None and dev > 0 and dev < ttf_h:
            ttf_h = dev

        ttf_s = ttf_h * 3600.0
        # frame-window gate: a flip comfortably beyond the next exposure (+ slack)
        # is not our concern this frame — expose normally.
        window_s = max(0.0, float(next_exposure_s)) + FLIP_FRAME_MARGIN_S
        if ttf_s > window_s:
            return
        # the flip falls within the upcoming frame. If the target has NOT yet
        # crossed (ttf still > 0), WAIT it out rather than starting an exposure
        # that would straddle the meridian (NINA PassMeridian semantics) — a
        # premature flip while still east of the meridian would swing the mount
        # counterweight-up on the far side. The mount keeps tracking through this
        # short (≤ one frame) cancel/pause-responsive hold.
        if ttf_s > 0:
            await self._wait_for_flip_point(target)

        # pre-flip safety + mount-floor gate (the flip is a slew — §1.9-B).
        await self._safety_gate(context="slew", target=target)
        self._set_state(detail="meridian flip")
        _t0 = time.time()
        # the flip = stop-guide + re-slew + solve + restart-guide; bound it (P0-2)
        # so a wedged flip can't hang the night mid-slew across the meridian.
        await _bounded(self.hub.meridian_flip(target.ra_hours, target.dec_deg),
                       FLIP_TIMEOUT_S, "meridian flip")
        # one flip per meridian crossing: the target now tracks counterweight-down
        # on the far side and the server countdown stays negative for hours, so
        # disarm until the next target re-arms in _setup_target.
        self._flip_armed = False
        self._record_event_cost("flip", time.time() - _t0)
        # the flip wall-time is accounted analytically (events_cost_s), so flag
        # this frame to exclude it from the per-frame overhead EMA — matching the
        # dither/AF blocks (P2-1).
        self._frame_had_event = True
        if "focuser" in self.hub.devices:
            await self._autofocus("post-flip autofocus")

    async def _wait_for_flip_point(self, target: Target) -> None:
        """Hold (cancel/pause-responsive) until the target actually reaches the
        meridian flip point (server HA countdown ``<= 0``).

        Entered only when the flip falls inside the upcoming frame window but the
        target has not yet crossed, so we never START an exposure that would
        straddle the meridian and we never flip a GEM counterweight-up while it is
        still east of the meridian (NINA PassMeridian). Bounded by construction:
        the caller only waits when the remaining countdown is ≤ one frame window."""
        try:
            lon = self.hub.site["longitude"]
        except Exception:
            return
        while True:
            await self._checkpoint()            # honor a concurrent pause
            ttf_h = schedule.hours_to_meridian_flip(target.ra_hours, lon)
            if ttf_h <= 0:
                return
            self._set_state(detail=f"holding for meridian "
                                   f"({ttf_h * 60.0:.1f} min)")
            # sleep the smaller of the step and the remaining time, with a small
            # floor so we don't busy-spin as the countdown approaches zero.
            await asyncio.sleep(max(0.2, min(FLIP_WAIT_STEP_S, ttf_h * 3600.0)))

    def _guider_phase(self) -> str:
        """The guider's own narration phase, or "" when it cannot be read.

        Never raises: this is consulted on the path to opening a shutter, and a
        guider that cannot answer must not be able to stall or crash a run."""
        g = self.hub.guider
        if not g:
            return ""
        try:
            return str(getattr(g.stats(), "phase", "") or "")
        except Exception:
            return ""

    async def _await_guider_quiet(self, why: str,
                                  timeout_s: float = GUIDE_QUIET_TIMEOUT_S) -> bool:
        """Block until the guider is not DELIBERATELY MOVING THE MOUNT.

        MEASURED ON SKY 2026-08-10, and the reason this exists. After the
        meridian flip the guider's flipped calibration diverged, the engine's
        recovery called ``start_guiding()`` — which RETURNS AS SOON AS THE LOOP
        STARTS, not when it has settled — and the caller opened the shutter on
        the next line. Calibration's whole job is to pulse the mount a known
        distance and measure the star, so every one of those exposures was taken
        while the mount was being deliberately slewed. The frames came out with
        ~35-pixel diagonal streaks and the galaxy smeared into a blob.

        The invariant is physical, not procedural: **never open the shutter
        while something is commanding the mount on purpose.** Phrased that way
        it also covers a calibration the OPERATOR starts from the UI mid-run,
        which a "wait for recovery to finish" guard would have missed.

        Bounded, and a timeout is NOT fatal: it returns False and the caller
        decides. A guider stuck in ``calibrating`` forever (tonight's case, when
        the guide star was too faint at gain 20) must not freeze the night — the
        run continues unguided, having said so, which is worth strictly more
        than nothing.
        """
        if not self._guider_phase():
            return True
        deadline = time.monotonic() + max(0.0, timeout_s)
        announced = False
        while self._guider_phase() in GUIDE_MOUNT_BUSY_PHASES:
            if time.monotonic() >= deadline:
                bus.log("warning",
                        f"guider still {self._guider_phase()} after "
                        f"{timeout_s:.0f}s — continuing {why} without waiting "
                        f"further; frames may be affected", "sequence")
                return False
            if not announced:
                bus.log("info",
                        f"holding {why}: the guider is {self._guider_phase()} "
                        f"and is moving the mount", "sequence")
                self._set_state(detail="waiting for the guider")
                announced = True
            await asyncio.sleep(GUIDE_QUIET_POLL_S)
        return True

    async def _maybe_recover_guiding(self, target=None) -> None:
        if not (self.plan.guide and self.plan.recover_guiding):
            return
        g = self.hub.guider
        if not g or not g.connected:
            return
        try:
            if await g.is_active():
                return
        except Exception:
            return
        bus.log("warning", "guiding lost — attempting recovery", "sequence")
        self._set_state(detail="recovering guiding")

        # RE-CENTRE BEFORE RESUMING, not after. While guiding was down the field
        # was free to walk, and on 2026-08-10 it walked 128 ARCMIN (2.1°) — the
        # target left a 101'x67' frame entirely and the run kept dutifully
        # exposing an empty patch of Cepheus. Nothing noticed, because centring
        # only ever ran at target start and after a flip.
        #
        # ``target.center`` is the existing statement of intent — "this target is
        # to be plate-solved onto the sensor" — so honouring it again here adds
        # no new policy and no new plan field. A target that opted out of
        # centring still opts out. The re-centre goes FIRST because it slews, and
        # a slew would tear down guiding we had just paid to restart.
        if target is not None and getattr(target, "center", False) \
                and not getattr(target, "calibration", False):
            try:
                self._set_state(detail="re-centring after guiding loss")
                await self.hub.goto_and_center(target.ra_hours, target.dec_deg,
                                               rotation_deg=target.rotation_deg)
            except Exception as e:
                # Non-fatal by design: a failed re-centre leaves the mount where
                # it was, which is exactly where it would have been without this
                # block. Recovery still proceeds.
                bus.log("warning",
                        f"re-centring after guiding loss failed ({e}); "
                        f"resuming guiding at the current pointing", "sequence")

        try:
            await g.start_guiding()
        except Exception as e:
            bus.log("warning", f"guiding recovery failed: {e}", "sequence")
            return
        # ...and do not hand control back until the guider has stopped pulsing.
        await self._await_guider_quiet("the next frame")

    async def _refocus_due(self) -> bool:
        plan = self.plan
        if "focuser" not in self.hub.devices:
            return False
        if plan.autofocus_every and self._frames_since_focus >= plan.autofocus_every:
            return True
        if plan.refocus_on_temp_delta_c > 0:
            try:
                t = await self.hub.require("focuser").get_temperature()
            except Exception:
                t = None
            if t is not None and self._last_focus_temp is None:
                # SEED ON FIRST USE. The baseline used to be writable only by a
                # completed autofocus, so a plan with autofocus_every = 0 and no
                # target running an initial AF had nothing to compare against and
                # the drift trigger sat dead all night. For a user who focused by
                # hand, "refocus when the focuser drifts this much" means drift
                # from where it is NOW — arm from the first reading.
                self._last_focus_temp = t
                return False
            if t is not None and abs(t - self._last_focus_temp) >= plan.refocus_on_temp_delta_c:
                bus.log("info", f"focuser temp drifted to {t:.1f}°C — refocusing", "sequence")
                return True
        return False

    async def _run_instructions(self, ctx: TriggerContext, target: Target,
                                step) -> None:
        """PRO-3: evaluate the plan's conditional instructions against ``ctx``
        and dispatch whatever fired. Pure eval (Task 2) + thin dispatch to the
        EXISTING capabilities. Only reached when ``plan.instructions`` is
        non-empty (the caller guards it), so the empty-plan run is untouched."""
        if not (self.plan and self.plan.instructions):
            return
        fired, self._fire_state = evaluate_instructions(
            self.plan.instructions, ctx, self._fire_state)
        if fired:
            await self._dispatch_actions(fired, target, step)

    async def _dispatch_actions(self, fired: list[FiredAction], target: Target,
                                step) -> None:
        """Map each fired action to its EXISTING engine capability (§1.2). No new
        teardown code. ``abort`` is raised OUTSIDE the try so it always
        propagates to ``_run``'s SafetyAbort arm (the shielded wind-down); every
        other action is individually guarded so a notify/dither/refocus hiccup
        never breaks capture. ``refocus``/``dither`` set ``_frame_had_event`` so
        their wall-time is excluded from the overhead EMA (like the built-in
        dither/AF blocks).

        ``run_target``/``skip_target`` (control-flow expansion) likewise raise
        OUTSIDE the try — a :class:`JumpTarget` that ``_run_scheduled`` catches —
        and are the ONLY new branch here, spending from the ``MAX_JUMPS``
        budget. With neither action authored this method behaves exactly as
        before."""
        for fa in fired:
            if fa.action == "abort":
                raise SafetyAbort(fa.message or "aborted by sequence instruction")
            if fa.action == "skip_target":
                # Skipping a target we are NOT shooting must not abandon the one
                # we are: queue the name and let the scheduler drop it when it
                # comes up (no unwind, current target keeps shooting). Skipping
                # the ACTIVE target still falls through to the jump raise below,
                # where abandoning it IS the requested behavior.
                _skip_name = (fa.target_arg or "").strip()
                if _skip_name and _skip_name != target.name:
                    if _skip_name not in self._pending_skips:
                        self._pending_skips.add(_skip_name)
                        bus.log("info",
                                f"instruction: target {_skip_name!r} queued to be "
                                "skipped (current target continues)", "sequence")
                    continue
            if fa.action in ("run_target", "skip_target"):
                # Raised OUTSIDE the try (like abort) so it always unwinds
                # cleanly out of _run_step's frame loop to _run_scheduled. This
                # is a FRAME BOUNDARY — the same safe point StopTarget uses — so
                # no per-frame cleanup can be skipped. Budget-exhausted jumps
                # degrade to a warning and normal scheduling (never a hang).
                if self._jumps_spent >= MAX_JUMPS:
                    bus.log("warning",
                            f"target-jump budget exhausted ({MAX_JUMPS}) — "
                            f"ignoring '{fa.action}' to {fa.target_arg!r}; "
                            "continuing with normal scheduling", "sequence")
                    continue
                self._jumps_spent += 1
                raise JumpTarget("run" if fa.action == "run_target" else "skip",
                                 fa.target_arg or "")
            try:
                if fa.action == "notify":
                    bus.log(fa.level, fa.message or "sequence instruction", "sequence")
                elif fa.action == "pause":
                    bus.log("warning", fa.message or "paused by sequence instruction",
                            "sequence")
                    self.pause()
                elif fa.action == "hold_for_clear":
                    # The self-releasing weather hold. Runs INLINE - it does not
                    # return until the sky clears, the night ends at the stop
                    # boundary, or the hold times out into an abort+park - so the
                    # frame loop simply continues underneath it afterwards.
                    await self._hold_for_clear(
                        fa.message or "clouds", target)
                elif fa.action == "refocus":
                    if "focuser" in self.hub.devices:
                        await self._autofocus("triggered refocus")
                        self._frame_had_event = True
                elif fa.action == "dither":
                    if self.hub.guider and self.hub.guider.connected:
                        await _bounded(self.hub.guider.dither(self.plan.dither_pixels),
                                       GUIDE_OP_TIMEOUT_S, "instruction dither")
                        self._frames_since_dither = 0
                        self._frame_had_event = True
            except SafetyAbort:
                raise
            except Exception as e:
                bus.log("warning", f"instruction action '{fa.action}' failed: {e}",
                        "sequence")

    def _guide_rms(self) -> float | None:
        """Current total guide RMS in ARCSEC, or None when unguided/unreadable
        **or when the guider is reporting pixels with no known image scale**.

        UX #11: this used to return ``stats().rms_total`` raw, which is arcsec
        only when ``GuideStats.is_arcsec`` — and with no guide-scope focal length
        configured (the default) the native guider reports guide-camera PIXELS.
        Every caller here compares against an arcsec-labelled threshold, so the
        raw value silently loosened the gate by the image scale. Returning None
        makes an unjudgeable frame un-gated (never wrongly rejected) and makes
        the ``on_guide_rms_above`` predicate indeterminate, which the instruction
        evaluator already handles as "don't fire"."""
        try:
            if self.hub.guider and self.hub.guider.connected:
                return rms_total_arcsec(self.hub.guider.stats())
        except Exception:
            pass
        return None

    def _guiding_now(self) -> bool:
        """True when a guider is connected and actually guiding (so an unreadable
        RMS is a UNIT problem worth reporting, not simply 'unguided')."""
        try:
            g = self.hub.guider
            return bool(g and g.connected and getattr(g.stats(), "guiding", False))
        except Exception:
            return False

    def _warn_rms_unit_once(self) -> None:
        """Say ONCE per run why an armed max-guide-RMS gate isn't judging frames,
        instead of silently passing everything (the failure mode that made #11
        invisible). Idempotent; never raises."""
        if getattr(self, "_rms_unit_warned", False):
            return
        self._rms_unit_warned = True
        bus.log("warning",
                "max guide RMS is set in arcsec, but the guider is reporting "
                "guide-camera pixels with no image scale — set the guide scope's "
                "focal length in Settings so this gate can be judged. Frames are "
                "NOT being rejected on guide RMS.", "sequence")

    def _check_quality(self, info: dict, *, record: bool = True,
                       calibration: bool = False) -> bool:
        """Return whether a frame is ACCEPTED per the auto gate (spec §3): the
        existing HFR running-median factor AND an optional star floor
        (``min_stars``) AND an optional guide-RMS ceiling (``max_guide_rms``)
        AND an optional per-frame eccentricity ceiling (``max_eccentricity``) —
        all AND together; 0 disables each. Star/RMS/ecc gates skip
        calibration frames (darks/bias/flats have no stars and no guiding).

        Preserves the legacy HFR logic exactly: gate against the median of the
        ACCEPTED window only, fold this frame's HFR in only when accepted and
        ``record`` (a rejected/cloudy HFR must never drift the median).
        ``record=False`` is a pure read-only check."""
        plan = self.plan
        factor = plan.hfr_reject_factor
        hfr = info.get("hfr") if isinstance(info, dict) else None
        accepted = True
        if factor and hfr is not None:
            window = self._recent_hfr
            if len(window) >= 4:
                med = median(window)
                if med > 0 and hfr > med * factor:
                    self._rejected += 1
                    bus.log("warning", f"frame HFR {hfr:.2f} >> median {med:.2f} — "
                                       "possible cloud / poor frame", "sequence")
                    accepted = False
        if accepted and not calibration and plan.min_stars > 0:
            stars = info.get("stars") if isinstance(info, dict) else None
            if stars is not None and int(stars) < plan.min_stars:
                self._rejected += 1
                bus.log("warning", f"frame stars {int(stars)} below floor "
                                   f"{plan.min_stars}", "sequence")
                accepted = False
        if accepted and not calibration and plan.max_guide_rms > 0:
            rms = self._guide_rms()
            if rms is None and self._guiding_now():
                self._warn_rms_unit_once()
            if rms is not None and rms > plan.max_guide_rms:
                self._rejected += 1
                bus.log("warning", f'guide RMS {rms:.2f}" above ceiling '
                                   f'{plan.max_guide_rms:.2f}"', "sequence")
                accepted = False
        if accepted and not calibration and plan.max_eccentricity > 0:
            ecc = info.get("ecc") if isinstance(info, dict) else None
            if ecc is not None and float(ecc) > plan.max_eccentricity:
                self._rejected += 1
                bus.log("warning", f"frame eccentricity {float(ecc):.2f} above ceiling "
                                   f"{plan.max_eccentricity:.2f} — trailing/tilt", "sequence")
                accepted = False
        if accepted and record and factor and hfr is not None:
            self._recent_hfr.append(float(hfr))
            self._recent_hfr = self._recent_hfr[-12:]
        return accepted

    async def _capture_focus_temp(self) -> None:
        """Re-anchor the temperature-drift baseline to the focuser's CURRENT
        reading. Called after every autofocus ATTEMPT, successful or not: the
        trigger's question is "how far has it drifted since we last tried", not
        "since we last succeeded". Requires the focuser itself (the caller's may
        be unbound on the error path) and swallows everything — a focuser with no
        temperature probe leaves the baseline exactly as it was."""
        try:
            self._last_focus_temp = await self.hub.require("focuser").get_temperature()
        except Exception:
            pass

    async def _autofocus(self, label: str) -> None:
        self._set_state(detail=label)
        _t0 = time.time()
        failed_reason: str | None = None
        try:
            cam = self.hub.require("camera")
            foc = self.hub.require("focuser")
            result = await run_autofocus(cam, foc, hub=self.hub)
            if not result.success:
                bus.log("warning", f"{label} failed: {result.message}", "sequence")
                failed_reason = result.message or "autofocus failed"
            self._frames_since_focus = 0
            self._record_event_cost("autofocus", time.time() - _t0)
            await self._capture_focus_temp()
        except SafetyAbort:
            raise
        except Exception as e:
            bus.log("warning", f"{label} error: {e}", "sequence")
            failed_reason = str(e)
            # A RAISED autofocus must re-anchor the baseline too — otherwise the
            # drift that triggered it is still there at the next frame boundary
            # and the same failing autofocus fires between every single frame.
            await self._capture_focus_temp()
        # P1-7: honor cfg.escalation.af_failure_action on a failed/errored focus.
        # Default "warn" is the legacy behavior (log above + continue). "abort"
        # tears the night down; "skip" advances the scheduler past this target
        # rather than shooting it out of focus.
        if failed_reason is not None:
            cfg = self._cfg
            action = (cfg.escalation.af_failure_action if cfg else "warn")
            if action == "abort":
                raise SafetyAbort(f"autofocus failed: {failed_reason}")
            if action == "skip":
                raise StopTarget(f"autofocus failed: {failed_reason}")

    async def _panel_off_safe(self) -> None:
        """Best-effort flat-panel-off (PRO-5): an aborted/failed run must NEVER
        leave the panel lit. Bounded + swallows every error (we may already be
        tearing down); a missing/absent calibrator is a no-op."""
        if "covercalibrator" not in self.hub.devices:
            return
        try:
            await _bounded(self.hub.calibrator_off(), CALIBRATOR_CMD_TIMEOUT_S,
                           "calibrator off")
            cc = self.hub.calibrator
            if getattr(cc, "has_cover", False):
                await _bounded(self.hub.close_cover(), CALIBRATOR_CMD_TIMEOUT_S,
                               "close cover")
        except Exception as e:
            bus.log("warning", f"panel-off failed: {e}", "sequence")

    async def _safe_stop(self) -> None:
        """Leave the rig in a safe state after abort/error. Bounded (P0-2): a
        wedged camera/guider can't hang the abort/error teardown."""
        try:
            cam = self.hub.devices.get("camera")
            if cam and cam.connected:
                await asyncio.wait_for(cam.abort_exposure(), COOLER_CMD_TIMEOUT_S)
        except (asyncio.TimeoutError, Exception):
            pass
        try:
            if self.hub.guider and self.hub.guider.connected:
                await asyncio.wait_for(self.hub.guider.stop_guiding(),
                                       GUIDE_OP_TIMEOUT_S)
        except Exception:
            pass
        # never leave the flat panel lit after an abort/error.
        await self._panel_off_safe()

    async def _reopen_mount_for_park(self, tel) -> None:
        """Best-effort: bring a dropped mount link back so the park can run.

        The wind-down park is the last thing standing between a finished run and
        an unattended mount tracking until sunrise, so "the link is down" is not
        an acceptable place to stop — reopening it is one call, and on
        2026-08-09 that one call was what fixed a five-hour outage. Mirrors the
        dawn-park net, which does the same thing for the same reason.

        Never raises: a wind-down step that throws would strand the cooler warm
        and the roof open behind it. A failure here falls through to the park
        below, which logs its own refusal honestly."""
        bus.log("warning", "the mount's link is down at wind-down — reopening "
                           "it so the park can run", "sequence")
        try:
            await asyncio.wait_for(tel.connect(), MOUNT_RECONNECT_TIMEOUT_S)
        except Exception as e:      # noqa: BLE001 — includes the timeout
            bus.log("error", f"could not reopen the mount's link ({e}) — the "
                             f"park below will almost certainly fail and the "
                             f"mount may be left tracking", "sequence")

    async def _wind_down(self, park: bool, warm: bool,
                         close_dome: bool = False) -> None:
        # NB: every device call here is BOUNDED (P0-2) but a timeout is handled
        # LOCALLY (log + continue), never re-raised as SafetyAbort — we are
        # already tearing down, and a hung park must not stop the cooler from
        # warming (or orphan the shielded teardown with an unretrieved exception).
        # PRO-5: never leave the flat panel lit through a normal/abort wind-down.
        await self._panel_off_safe()
        try:
            if self.hub.guider and self.hub.guider.connected:
                await asyncio.wait_for(self.hub.guider.stop_guiding(),
                                       GUIDE_OP_TIMEOUT_S)
        except (asyncio.TimeoutError, Exception):
            pass
        if park:
            tel = self.hub.devices.get("telescope")
            if tel is None:
                # NEVER SILENT. This branch used to be an implicit else that
                # wrote nothing at all, so a run whose park was skipped for
                # want of a mount looked exactly like a run that parked.
                bus.log("error", "PARK SKIPPED: this run asked to park when "
                                 "done and no telescope is connected — if the "
                                 "mount is powered it is still tracking",
                        "sequence")
            else:
                if not getattr(tel, "connected", False):
                    # A telescope OBJECT that reports not-connected is a link
                    # that died under us, and since ``connected`` became a
                    # measurement rather than a memory (#208) this branch is
                    # REACHABLE for the first time — before that the stale flag
                    # sent us into the park below, where the failure at least
                    # got logged. Skipping quietly here would have turned that
                    # fix into a regression: on 2026-08-09 the mount's link died
                    # at 02:38 and the wind-down is the last thing that runs
                    # before hours of unattended tracking.
                    await self._reopen_mount_for_park(tel)
                bus.log("info", "parking mount", "sequence")
                # Motion fence (W3.7): the wind-down park is an abort -- BUMP the
                # hub motion epoch FIRST so any in-flight (or just-accepted) slew is
                # fenced out and cannot drive the mount AFTER we begin parking. Then
                # park under the hub motion lock so the park itself is serialized
                # with every other device-touching motion path. Best-effort: a
                # missing bump primitive (older hub) degrades to the raw park.
                bump = getattr(self.hub, "bump_motion_epoch", None)
                if callable(bump):
                    bump()
                # a park failure/timeout must NOT abort the wind-down (else the
                # cooler would never warm and, on an orphaned shielded teardown,
                # this would surface as an 'exception never retrieved') — log +
                # continue.
                try:
                    lock = getattr(self.hub, "_motion_lock", None)
                    if lock is not None:
                        async with lock:
                            await asyncio.wait_for(tel.park(), PARK_TIMEOUT_S)
                    else:
                        await asyncio.wait_for(tel.park(), PARK_TIMEOUT_S)
                except asyncio.TimeoutError:
                    bus.log("warning", f"park timed out after {PARK_TIMEOUT_S:.0f}s "
                                       "during wind-down — continuing", "sequence")
                except Exception as e:
                    bus.log("warning", f"park failed during wind-down: {e}", "sequence")
                else:
                    # AFTER the await, so the line means "parked" rather than
                    # "asked to". /api/mount/park documents this rule at length
                    # ("PARK IS THE ONE EVENT AN UNATTENDED NIGHT MUST BE ABLE
                    # TO PROVE") and the wind-down — the park that actually runs
                    # at the end of every unattended night — was not following
                    # it: "parking mount" above is written BEFORE the command,
                    # so on its own it proves only intent.
                    bus.log("info", "mount parked", "sequence")
        elif self._frames_done:
            # A run that deliberately leaves the mount live must SAY so. On
            # 2026-08-09 a 150-frame unattended run ended "complete: 150 frames"
            # at 03:48 with park_when_done off, and nothing anywhere recorded
            # that the mount was still tracking — it then tracked for two more
            # hours until the dawn net tried, and failed, to catch it.
            #
            # Level follows the actual exposure. Dawn park is the net under this
            # choice, so when that net can act this is ordinary information;
            # when it cannot, nothing at all is watching and that is news.
            from ..dawn_park import dawn_park_disabled
            netless = (dawn_park_disabled()
                       or bool((self.hub.site or {}).get("is_default", True)))
            bus.log("warning" if netless else "info",
                    "this run was set not to park when done, so the mount is "
                    "still tracking. " + ("Dawn park is NOT active on this rig, "
                                          "so nothing will stop it"
                                          if netless else
                                          "Dawn park will park it after sunrise"),
                    "sequence")
        # PRO-4: with the mount now fenced-and-parked ABOVE, close the roof over
        # the parked gear (end-of-night or unsafe teardown). This runs AFTER the
        # park block by construction; ``close_observatory`` additionally
        # RE-CONFIRMS ``is_parked`` and REFUSES to move the shutter if the park
        # failed/timed out — so the never-crush-the-mount invariant holds even if
        # the park above did not complete. Best-effort like every other wind-down
        # step: it never raises (returns False), and a failed/refused close pages
        # loudly via an error-level bus.log.
        if close_dome:
            dome = self.hub.devices.get("dome")
            if dome is not None and getattr(dome, "connected", False):
                from .roof import close_observatory
                tel = self.hub.devices.get("telescope")
                ok = await close_observatory(dome, tel, log=bus.log)
                # UX #8: the roof state at the END of the night belongs in the
                # report. Without this the only artefact of a run that shut the
                # observatory was a "pause" line — a billing and facility-safety
                # question for a hosted remote rig.
                self._record_safety(
                    "roof closed over parked gear (wind-down)" if ok
                    else "AUTOMATED ROOF CLOSE FAILED — gear may be exposed",
                    "close_roof" if ok else "close_roof_failed")
                if not ok:
                    # An error-level bus.log IS the loud page: the AlertDispatcher
                    # routes warning/error logs to every configured sink
                    # (alerting._on_bus_event), so a failed/refused auto-close
                    # reaches the user without a bespoke dispatcher call.
                    bus.log("error", "AUTOMATED ROOF CLOSE FAILED — gear may be "
                                     "exposed", "safety")
        if warm:
            cam = self.hub.devices.get("camera")
            if cam and cam.connected and getattr(cam, "can_cool", False):
                # THIS is the path the warm-ramp fix was written for. Until
                # 2026-08-04 it was a bare set_cooler(False): the unattended
                # "park and warm" teardown — the one SafetyLimitsPanel promises
                # ramps safely, the one that runs when a rain trip ends a night
                # with nobody there — cut the TEC dead and let the sensor
                # equalise with the air at ~5 °C/min (measured on the rig).
                #
                # hub.warm_camera STARTS the ramp and returns: the ramp itself
                # runs on a hub-owned background task that outlives this run.
                # That ordering is not incidental. A wind-down fires when
                # something is already wrong, and the mount park and roof close
                # above it are the time-critical parts — a ten-minute blocking
                # warm here would delay both.
                warmer = getattr(self.hub, "warm_camera", None)
                try:
                    if callable(warmer):
                        state = await asyncio.wait_for(
                            warmer(source="wind-down"), COOLER_CMD_TIMEOUT_S)
                        # Report what actually started, including the fallbacks:
                        # "warming camera" over a cooler that was just cut dead
                        # is the log line that let this bug live in the product.
                        if state.get("active") and state.get("ramped"):
                            bus.log("info",
                                    f"warming camera in the background — "
                                    f"{state.get('start_c')} → "
                                    f"{state.get('ambient_c')} °C at "
                                    f"{state.get('rate_c_per_min')} °C/min "
                                    f"(the wind-down does not wait for it)",
                                    "sequence")
                        else:
                            bus.log("info", f"cooler off during wind-down — "
                                            f"{state.get('note') or 'no ramp ran'}",
                                    "sequence")
                    else:
                        # Defensive: a hub double (tests, an embedder) without the
                        # routine still warms — just without the ramp, and it says so.
                        bus.log("warning", "this hub has no warm-ramp routine — "
                                           "switching the cooler off outright",
                                "sequence")
                        await asyncio.wait_for(cam.set_cooler(False),
                                               COOLER_CMD_TIMEOUT_S)
                except asyncio.TimeoutError:
                    bus.log("warning", "warm-cooler command timed out during "
                                       "wind-down — continuing", "sequence")
                except Exception as e:
                    # Never let the warm abort the rest of the wind-down (the
                    # same rule the park block above follows).
                    bus.log("warning", f"could not start the camera warm during "
                                       f"wind-down: {e}", "sequence")
