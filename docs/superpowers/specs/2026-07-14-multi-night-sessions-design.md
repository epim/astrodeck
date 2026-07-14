# Multi-night sessions: accepted-frame quotas, resume, review — design

**Date:** 2026-07-14 · **Status:** approved (user confirmed design 2026-07-14; "matches my intent")

Sub-project **A** of the multi-night program. Build order approved by user:
**A** (this spec) → **B** (site location UI + relay strip) → **C** (weather/
cloud integration) → **D** (dynamic cloud-dodging; deferred, hooks only).
B/C/D get their own specs later.

## Problem

Plans routinely exceed one night — because the plan is long, or because
clouds/quality-rejects eat the night. Today:

- Progress is single-slot: `CAPTURE_DIR/.sequence_resume.json`
  (engine.py:481-506), cleared on every clean finish, keyed **positionally**
  (`"ti:si"` — engine.py:157,961,1503), so resuming against an edited plan
  silently misattributes frames.
- `ExposureStep.count` is an **attempt** bound (loop
  `range(done, step.count)` engine.py:966), not an accepted-frame target.
  The HFR gate (engine.py:1725-1764) can reject frames, but `retake` is
  capped per-target and `discard` unlinks the FITS — nothing guarantees
  "N frames I'd actually stack."
- Recovery after reboot exists (`GET /api/sequence/recoverable`,
  `POST /api/sequence/recover` — app.py:2403-2430) but is single-slot and
  one-night-minded: dawn/`window_closed` finalizes the whole run
  (engine.py:570-585).
- Plan export/import endpoints exist (`GET /api/plans/{id}/export`,
  `POST /api/plans/import` — app.py:1667-1696) but the UI never exposes them.

## User decisions (recorded)

1. **Accepted =** auto gate live (HFR + optional star floor + guide-RMS
   ceiling) **plus** manual regrade UI between nights; regrades adjust the
   remaining quota.
2. **Resume mode =** both in v1: manual one-click resume AND an opt-in
   auto-resume-at-dusk toggle.
3. **Architecture =** first-class Session entity (uuid-keyed files, frame
   ledger owns per-frame grades); reports stay immutable night summaries;
   the single-slot resume file is retired.
4. **Scope =** ONE spec/plan covering everything (no A1/A2 split).
5. **Forward-compat:** optional PixInsight integration is planned later —
   the ledger schema must accommodate external graders (see §10).
6. **Review round (agent-bridge, verdict SHIP-WITH-CHANGES; user confirmed
   2026-07-14):** allow id-safe plan edits on dormant sessions; keep
   auto-resume in v1 but warn when no safety monitor is configured; add
   boot sweep for orphaned `active` sessions; add night-level reject guard;
   frame PATCH also accepts metrics.

## 1. Stable IDs

- `Target` and `ExposureStep` (server sequence/models.py:7-48, client
  types.ts:349-373) gain `id: str` — uuid4 hex.
- **Client generates** IDs at creation time (add-target, add-step, quick-add,
  Atlas `panelsToTargets`, catalog send). `loadPlan()`/`setPlan` backfill
  missing IDs on legacy localStorage plans. Cloning steps (mosaic
  "apply to all panels" in lib/planGroups.ts) assigns **fresh** step IDs to
  the clones.
- **Server backfills** any missing IDs when a Session is created (never
  rejects an id-less plan). The session's frozen snapshot is the identity
  authority thereafter.
- All session bookkeeping keys off `target.id`/`step.id`. The engine's
  in-memory `_done` map re-keys from `"ti:si"` to `"targetId:stepId"`.
- Plan-library files and export JSON carry the IDs (schema stays v1;
  pydantic fills defaults on old files).

## 2. Session entity & persistence

New module `server/astrodeck/sequence/session.py`.

```
SessionFrame:
  id: str                 # uuid4 hex
  ts: float               # epoch seconds
  night: str              # report_id this frame was captured under
  target_id: str
  step_id: str
  path: str               # saved FITS path ("" if unsaved)
  thumb: str | None       # relative thumb path under the session dir
  metrics: dict[str, float]   # extensible: hfr, stars, guide_rms,
                              # sensor_temp_c today; PixInsight adds later
  auto_accepted: bool
  override: str | None    # "accept" | "reject" | None
  # effective acceptance = override if set else auto_accepted

Session:
  id: str
  schema_version: int = 1
  name: str               # defaults to plan.name
  created_ts / updated_ts: float
  status: str             # "active" | "dormant" | "complete" | "abandoned"
  plan: SequencePlan      # frozen snapshot WITH ids
  nights: list[str]       # report ids, in order
  frames: list[SessionFrame]
  auto_resume: bool = False
```

- **Storage:** one file per session, `CAPTURE_DIR/sessions/<uuid>.json`,
  written with `write_json_atomic` (persist.py:90-118), ids validated via
  `safe_id_path` (persist.py:41-68). Thumbs under
  `CAPTURE_DIR/sessions/<uuid>/thumbs/<frame_id>.jpg`.
- `SessionStore` mirrors `PlanLibrary` (plans.py:57-166): list/load/save/
  delete, `MAX_SESSIONS = 200` (oldest **complete/abandoned** pruned first;
  never prune dormant/active).
- Derived helpers on `Session`: `accepted(step_id) -> int`,
  `remaining() -> dict[step_id, int]`, `done_map() -> dict["tid:sid", int]`
  for engine seeding — mode-aware: effective-accepted counts when
  `count_mode == "accepted"`, recorded-frame counts in `attempts` mode.
- **Every `POST /api/sequence/start` creates a Session** (status `active`);
  resume re-opens an existing one. Reports are unchanged
  (report.py FrameRecord :60-71 stays as-is); each night appends its
  report_id to `nights`.
- **Retire `.sequence_resume.json`:** `_persist`/`load_resume`/
  `_clear_resume` (engine.py:404-506) are replaced by session saves. At boot,
  if a legacy resume file exists, migrate it once into a Session
  (positional keys → ids assigned during backfill, counts mapped by
  position) and delete the file. `/api/sequence/recoverable` + `/recover`
  are re-backed by the session store (a dormant session with frames is
  "recoverable"); route paths stay for UI compatibility.

## 3. Quota engine (`count_mode`)

- `SequencePlan` gains `count_mode: str = "attempts"` (`"attempts"` |
  `"accepted"`). Default preserves existing behavior exactly.
- In `accepted` mode, `_run_step`'s predicate changes from index-vs-count to
  `session.accepted(step.id) < step.count` (loop currently at
  engine.py:958-1018). Attempts are unbounded within the night; the window/
  dawn/max_run boundaries still end the night.
- **Quality gate** extends `_check_quality` (engine.py:1725-1764) with two
  optional plan-level thresholds (0 = off, both default 0):
  - `min_stars: int` — reject if star count below floor
  - `max_guide_rms: float` — reject if guider RMS (arcsec, from
    `hub.guider.stats().rms_total`) above ceiling at capture end
  The existing HFR running-median factor (`hfr_reject_factor`) stays. All
  three AND together. Metrics come from the existing preview measurement
  (stars.py measure_frame :148-156, hub._publish_preview :1327-1429).
- **In `accepted` mode rejected frames are always kept on disk** and
  recorded in the ledger (`auto_accepted=False`); the
  `escalation.hfr_reject_action` (warn/discard/retake) applies only to
  `attempts` mode. Rationale: regrading requires the file.
- **Runaway guards** (both `accepted`-mode only, both alert via existing
  alerting; shortfalls stay in the ledger for another night):
  - per-step: `max_consecutive_rejects: int = 10` (0 = off) — after N
    consecutive rejects on one step, skip to the next step/target.
  - per-night: `max_consecutive_rejects_night: int = 20` (0 = off) — after
    N consecutive rejects **across step/target boundaries** (counter resets
    on any accepted frame), end the night early → session `dormant`,
    `end_reason="quality"`. Prevents a clouded-out sky from cascading the
    per-step guard through every target (wasted hours + hardware wear);
    also the proto cloud-detector until sub-projects C/D exist.
- **Thumbnails:** on every ledger record (accepted or rejected), render a
  ~512px-long-edge JPEG using the preview stretch pipeline, off-thread,
  best-effort (failure leaves `thumb=None`, never blocks capture).
- `_record_frame` (engine.py:1482-1506) appends the SessionFrame and saves
  the session file atomically (same per-frame write cost as the old
  `_persist`).
- Progress/ETA in quota mode: `frames_total` = Σ counts,
  `frames_done` = Σ accepted; ETA estimates remaining accepted × exposure
  (documented as optimistic under rejection).

## 4. Night boundaries, dormancy, resume

- Terminal transitions (engine `_run` :540-624 and `_run_scheduled`
  finalization :570-585):
  - quota met (or attempts done) → session `complete`
  - dawn cutoff / `window_closed` / `max_run_min` / user abort / error /
    UNSAFE wind-down, with unmet quota → session `dormant`
    (`end_reason` recorded on the state as today)
- **Manual resume:** `POST /api/sessions/{id}/resume` → engine
  `start(plan=session.plan, session=session)`; `_done` seeded from
  `session.done_map()`; a fresh report is created and appended to `nights`.
  Windows re-resolve for the new night automatically (they are frozen once
  per run — engine.py:676-682 — and resume is a new run).
- Only one session can run at a time (engine is a singleton); starting a new
  plan while another session is dormant is allowed (multiple dormant
  sessions may exist).
- **Boot sweep:** a hard power cut / process kill bypasses terminal
  transitions and leaves the session `active` on disk. At app startup (the
  engine is never running at boot), any `active` session is transitioned to
  `dormant` so it is manually resumable and ResumeArm-eligible.
- **Id-safe plan edits on dormant sessions:** `PATCH /api/sessions/{id}`
  accepts a full replacement `plan` while the session is `dormant`.
  Attribution survives via stable IDs: targets/steps whose ids persist keep
  their ledger progress; new ids start at zero; frames whose step id no
  longer exists stay recorded in the ledger but stop counting toward any
  quota. **Running sessions are never editable.** Editing the Plan panel
  alone still never mutates a session — the update is an explicit action
  (see §7).
- Dormancy is the seam sub-project D will hook (cloud-driven early
  dormancy + re-planning). Note: the snapshot fixes frame **identity**, not
  execution order — the scheduler already reorders/skips targets at run
  time (window sorting, skip-ahead), which is the layer D will act on; no
  conflict with session snapshots.

## 5. Auto-resume at dusk (opt-in)

- Per-session `auto_resume: bool`, settable only on `dormant` sessions;
  **at most one session may have it enabled** (enabling it disables it
  elsewhere, server-enforced).
- New `ResumeArm` asyncio service (started with the app, like the alert
  dispatcher): every 60s — if engine idle AND an armed dormant session
  exists AND tonight's window for its earliest-ready target has opened
  (reuse sequence/schedule.py resolve logic), attempt the same code path as
  manual resume. Existing safety gates (sun-avoidance, safety monitor,
  horizon preflight) run exactly as they do for a manual start; a refusal
  alerts (existing alerting) and retries every 10 minutes until dawn, then
  gives up until the next night.
- Success and give-up both alert. Disarming from the UI (or starting
  anything manually) stops the service's interest immediately.
- **No-safety-monitor warning:** enabling auto-resume when no safety
  monitor device is configured requires an explicit confirm in the UI and
  shows a persistent warning chip on the armed session card ("auto-resume
  armed without a safety monitor — rig may start in bad weather").
  Sub-project C will add a weather gate to ResumeArm (cloud/precip forecast
  veto); this spec only reserves the hook (ResumeArm consults a
  `resume_veto()` check that v1 implements as the existing safety gates).

## 6. API surface

All new routes follow existing RBAC patterns (`require(...)` +
`@declare(...)`; sequence-control caps for mutating routes, view caps for
reads):

- `GET /api/sessions` — rows: id, name, status, created/updated, nights
  count, accepted/total, auto_resume. (view.status)
- `GET /api/sessions/{id}` — full session incl. ledger. (view.status;
  see §8 for path redaction)
- `POST /api/sessions/{id}/resume` — manual resume. (sequence-control cap,
  same as /api/sequence/start)
- `PATCH /api/sessions/{id}` — `{auto_resume?: bool, status?: "abandoned",
  plan?: SequencePlan}`; `plan` accepted only while `dormant` (409
  otherwise), id-merge semantics per §4. (sequence-control cap)
- `PATCH /api/sessions/{id}/frames/{frame_id}` —
  `{override?: "accept"|"reject"|null, metrics?: dict[str, float]}`;
  `metrics` merges into the frame's metrics dict (float values only) — the
  external-grader write path (§10). Returns updated per-step remaining.
  (sequence-control cap)
- `DELETE /api/sessions/{id}` — remove session file + thumbs (never the
  FITS frames). (sequence-control cap)
- `GET /api/sessions/{id}/frames/{frame_id}/thumb` — the JPEG.
  (view.preview cap)
- WS `SequenceState` gains a `session` sub-state:
  `{id, name, count_mode, accepted, target}` (cleared with the same
  explicit-None semantics as `schedule` — engine.py:448-450).

## 7. UI

- **Plan view Sessions section** (SequenceView): list of non-abandoned
  sessions — name, status chip, per-target accepted/total progress bars,
  Resume button (dormant only), "Update from Plan" button (dormant only —
  pushes the current Plan-panel plan into the session via the PATCH `plan`
  route; shows a per-target diff summary of kept/new/dropped progress
  before confirming), auto-resume Toggle (with the §5 no-safety-monitor
  confirm + warning chip), Review button, delete
  (with the existing undo-toast pattern where applicable; deleting a session
  is confirm-then-delete, no undo — server state).
- **Review drawer/modal:** frame grid for one session — thumbnail,
  HFR / stars / RMS, night, filter, verdict badge. Filters: target, night,
  verdict (accepted/rejected/overridden). Click toggles a selection;
  bulk "mark accepted" / "mark rejected" writes overrides via the PATCH
  route. Per-step remaining counts update live in the drawer header.
  Night-mode safe: existing tokens/classes only.
- **Plan settings:** `count_mode` toggle ("Count = accepted frames"),
  `min_stars`, `max_guide_rms`, `max_consecutive_rejects` (advanced row) in
  the plan-level settings block of SequenceView.
- **Plan library:** Export button per saved plan (downloads the existing
  export JSON) and an Import button (file picker → POST /api/plans/import,
  surfacing name-collision errors).
- Header/progress components read the quota-aware `progress` numbers
  unchanged (server computes them).

## 8. Privacy / RBAC

- Session ledgers contain filesystem paths: `GET /api/sessions/{id}` strips
  `path` from frames for principals lacking the backend-config cap, the same
  strip pattern as `_redact_drivers_for` (redact.py:100-130). Thumbs are
  served under the existing preview cap.
- No location data lives in sessions. (Site-location strip-for-relay is
  sub-project B.)

## 9. Testing

- **Server (pytest, from `server/`):** SessionStore round-trip + atomic
  write + prune policy; id backfill (client-less plans, legacy resume-file
  migration); quota predicate incl. overrides flipping remaining counts;
  per-step AND per-night consecutive-reject guards (night counter resets on
  accept, crosses target boundaries, sets `end_reason="quality"`); dormancy
  transitions for each night-boundary cause; boot sweep (orphaned `active`
  → `dormant`); resume seeding from ledger (edited-plan reorder does NOT
  misattribute — id-keyed); dormant plan PATCH id-merge (kept/new/dropped
  progress, 409 while running); frame PATCH metrics merge; ResumeArm with
  injected clock (window-not-open, refusal-retry, disarm, singleton-arm
  enforcement); frame PATCH route RBAC + path redaction; thumb route cap.
- **UI (self-executing tsx, `npx tsx`, never in CI — run manually):** pure
  helpers for quota math / remaining computation, review-grid filtering +
  bulk-selection reducer, id backfill on plan load.

## 10. PixInsight forward-compatibility (design constraint, no code now)

- `SessionFrame.metrics` is an open `dict[str, float]` — a later PixInsight
  integration (SubframeSelector-style FWHM/eccentricity/SNR) adds keys
  without schema migration.
- `override` is the write-point for ANY external grader, human or tool —
  the PATCH frames route is the integration surface; a batch variant can be
  added later without changing the ledger model.
- `path` in the ledger is the handle an external tool uses to locate frames.

## Out of scope (v1)

- Editing a **running** session's plan (dormant sessions: id-safe edits per
  §4; running: never).
- Full-size frame preview rendering in the review UI (thumbs + metrics only).
- Session export/import (plans export/import ships; sessions are rig-local).
- Eccentricity in the auto gate (stars.py computes a placeholder 0.0 today).
- Sub-projects B (site location/privacy), C (weather), D (dynamic
  rescheduling) — separate specs.
