# Plan Scheduling & Target Tools (Wave 3) — Design

**Requirements source:** `docs/superpowers/reviews/2026-07-12-atlas-plan-ux-review.md`
(5eaa131), Wave 3 items + design decisions 3-4 (user approved 2026-07-12: "build the
full schedule sub-panel" + "parity in place" + "Frame in Atlas" on plan cards).

**ERRATUM that reshapes this wave (2026-07-13, engine seam exploration):** the review
doc's claim "the sequence engine ignores Schedule entirely" is FALSE. Server-side
scheduling shipped in Batch 4b (commit 17f7609) and is live at HEAD:
`server/astrodeck/sequence/models.py` has `Schedule` (9 fields), `Target.schedule`,
`SequencePlan.safety_check` + `meridian_flip_warn_min`; `sequence/schedule.py` (417
lines) resolves windows; `engine.py` gates starts (`_wait_until`, cancel/pause-aware,
5 s ticks), enforces stop boundaries, honors `on_missed`, and publishes a WS
`SequenceState.schedule` sub-state (`waiting|ready|window_closed|never_rises`, reason,
eta_s, start/stop_ts). Existing engine tests cover stop-boundary, closed-window, and
waiting states. The client `Schedule` interface (types.ts:630-640) is field-for-field
identical to the server model — parity verified 2026-07-13.

**What is genuinely missing (grep-verified: zero `.schedule` renders in ui/src/*.tsx;
`defaultSchedule()` exported but never called; `safety_check`/`meridian_flip_warn_min`
appear only in types.ts):** the entire UI surface, plus small server polish. The
user's chosen outcome — a full schedule sub-panel wired so the engine honors it —
is therefore achieved by building the UI onto the already-working engine.

**Goal:** Per-target schedule editing in Plan, live "waiting/gated" runtime display,
tonight-awareness (sparklines + ordering), quick-add parity with Atlas sends, and a
Plan→Atlas framing path.

**Non-goals:** engine semantics changes (window resolution, on_missed behavior, safety
gating all stay as shipped); Mount/Atlas searches; renaming the Plan/sequence naming
split (review doc: rename only if cheap — it is not; skip).

**Seams:** `.superpowers/sdd/seams/wave3-engine.md` + `wave3-plan-ui.md` (verbatim,
current disk).

---

## 1. Schedule sub-panel (per-target editor in SequenceView)

- Each target card gains a collapsed **Schedule** disclosure. Collapsed state shows a
  summary chip derived from the schedule (pure helper, tsx-tested):
  `"Runs immediately"` (all defaults) / `"Dusk +30m → dawn"` / `"22:00 → max 90m ·
  skip if missed"` etc.
- Expanded: structured controls binding 1:1 to the nine `Schedule` fields:
  - Start: segmented `now | dusk | dawn | time`; when dusk/dawn an offset stepper
    (±min); when time an `HH:MM` input (the shipped server model validates).
  - Start gate: `min_altitude_deg` stepper (0 = none; help text distinguishes it from
    the global safety floor, mirroring the server docstring).
  - Stop: segmented `none | dawn | time` (+ offset / `HH:MM`), `max_run_min` stepper
    (0 = no cap).
  - Missed window: `wait | skip` toggle with the engine's real semantics in help text.
- **Backfill:** `defaultSchedule()` (exported, currently uncalled) is finally wired:
  applied on plan load and on every add path (quick-add, Atlas sends via
  `addTargetsToPlan`) so `target.schedule` is always present. Mutation uses
  SequenceView's existing `patchTarget` map-spread idiom.
- The plan already round-trips: `Target.schedule?` is additive client-side and a
  declared field server-side — no API work.

## 2. Runtime schedule display + engine polish

- **Plan run strip / target cards:** while the engine reports
  `SequenceState.schedule`, render it: `waiting` → "Waiting — {reason} · starts
  ~HH:MM" (from `start_ts`, with `eta_s` countdown via the existing eta lib);
  `window_closed` / `never_rises` → warn-toned chips with the reason. The active
  target's card gets the same chip.
- **Server polish (the one engine change):** the engine never clears the `schedule`
  sub-object after a wait ends — clear it when the gated target actually starts
  running (and on abort), so stale "waiting" state can't linger in the WS status.
  Pytest: drive the sim engine past a resolved wait and assert the field clears.

## 3. Tonight tools in Plan

- **Altitude sparkline per target card:** new pure mini-geometry helper in
  `ui/src/lib/visibility.ts` (`buildSparkGeometry(night, w, h)` — the existing
  `buildGeometry` is hard-sized to VIS_W/VIS_H; the mini variant emits just the
  target-alt path + alt-limit line + dark-band rect for a ~120×28 viewBox; tsx-tested).
  Data: `GET /api/visibility` per target, fetched lazily and cached in a module map
  keyed by rounded (ra,dec,altLimit) so re-renders and duplicate coords don't refetch.
  Rendered inside the card next to the PA badge; a `never_rises_above_limit` night
  renders the warn chip instead of a curve.
- **"Order by tonight" button** in the Targets panel header: `POST
  /api/visibility/order` (exists; keeps mosaic groups atomic via
  `_group_atomic_order`), then `setPlan` with the returned order. Disabled while a
  sequence is running (engine snapshots its plan at start — same rule as adding).

## 4. Quick-add parity (decision 4)

- Plan's catalog quick-add keeps its flow but gains the Atlas send's guarantee:
  after picking a result, fetch `GET /api/visibility` for it; if
  `never_rises_above_limit`, show the same `confirmDialog` copy Atlas uses before
  adding. (Rotation default stays `rotation_deg: undefined` — "no enforcement", which
  is the sensible default the decision asked for; the PA badge already reads "auto"
  only when a rotator is present.)
- Every added target gets `schedule: defaultSchedule()` (§1 backfill).

## 5. "Frame in Atlas" on plan target cards

- Each target card (and mosaic-group header) gains a small "Frame in Atlas"
  IconButton: constructs a minimal `CatalogEntry` (`{id: name, name, type: "", ra_hours,
  dec_deg, mag: 0, size_arcmin: 0, alt: 0, az: 0}`), calls `openFraming(entry)` (which
  seeds the session and switches view), then `setFraming({rotation_deg:
  target.rotation_deg ?? 0})` so the box opens at the target's planned angle.
  For a mosaic group, frame the group's first panel and seed the group's rows/cols?
  NO — YAGNI: mosaic re-framing already round-trips via Atlas Send (replace-by-group);
  the group header button frames the group's central coordinates with a 1×1 box, which
  is enough to eyeball the field. Per-panel buttons are omitted.

## 6. Automation panel additions

- `SequencePlan.safety_check` (bool) and `meridian_flip_warn_min` (number) are engine-
  consumed but have zero UI. Add to the existing Automation panel: a "Safety monitor
  gate" Toggle and a "Meridian warn lead" stepper (minutes), with InfoDots explaining
  the engine behavior. Both persist through the existing `setPlan` path.

## 7. Testing

- **UI (`npx tsx`):** schedule summary-chip helper (all mode combinations incl.
  offsets/caps); `buildSparkGeometry` (path present, limit line position, dark band,
  never-rises → null path); plus the three Wave-1 regression files.
- **Server (pytest):** the §2 clear-on-start/abort polish (extend the existing
  scheduled-engine test file; sim rig, no real waits — follow
  `test_mount_stops_tracking_while_waiting_for_next_target`'s time handling).
- `npm run build` per task; full server suite before push (server file touched).

## 8. Compatibility

- All Plan changes ride existing persistence (`PLAN_KEY`) and the declared
  `Target.schedule` field — old saved plans backfill via `defaultSchedule()` exactly
  as the C1-27 comment always intended.
- The WS schedule sub-state shape is unchanged (the polish only clears it sooner).
