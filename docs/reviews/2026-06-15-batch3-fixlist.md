# AstroDeck Batch 3 fix list (53 confirmed)

Both confirmed: `alert-pulse`/`confirmhold-fill`/`more-sheet-in`/touch-sizing classes are entirely absent from `index.css`, and `lockAvailable` is never written to `true` (only read). The findings are accurate. I have enough verification to produce the prioritized fix list.

---

# BATCH-3 REVIEW LEAD — PRIORITIZED FIX LIST & SHIP VERDICT

## SHIP-BLOCKER VERDICT: **BLOCKED — do not ship.**

Two P0 defects, both reducing to one root cause: **the pre-flight safety gate is fully built but never wired into the Run path.** A below-horizon / unguided / disk-critical / can't-cool plan starts an unattended multi-hour run with zero readiness check. This is the headline deliverable of the batch and it is dead code. Ship is blocked until P0 + the P1 safety-of-motion cluster below are fixed.

## SAFETY-OF-MOTION DEFECTS (flagged regardless of severity, per instruction)

These touch the deadman / STOP / hold-on-wrong-control chain and are called out explicitly:

| ID | Sev | Defect | Direction |
|----|-----|--------|-----------|
| **S1** | P1 | Deadman halt is **fire-and-forget**: on a swallowed `tel.stop()` exception the watchdog zeroes `_move_rates_seen` and never retries → indefinite uncommanded travel in the exact network-loss scenario the deadman exists for (`hub.py:424-430`). | **fails UNSAFE** |
| **S2** | P1 | Multi-touch corrupts the single-axis controller model → an axis stays commanded after release; relies on the 1.2s deadman to halt (`slewController.ts` single `curAxis`/`pressIsHold`). | bounded by deadman |
| **S3** | P1 | Keepalive jitter margin too thin (1000ms keepalive vs 1200ms deadman = 200ms) → false-STOP mid-slew under latency spikes. Duplicated across 4 findings. | fails SAFE |
| **S4** | P2 | Deadman armed AFTER the move await → narrow window where a moving axis has no watchdog coverage (`app.py:749-752`). | fails UNSAFE |
| **S5** | P2 | Failed `tel.stop()` zeroes the mirror unconditionally → backstop silently disarms while axis may still drive (`hub.py:418,430`). Same root as S1. | fails UNSAFE |
| **S6** | P2 | Unvalidated `axis: str` → mistyped axis commands Alpaca DEC but never arms the deadman (`app.py:115-117`). | fails UNSAFE |
| **S7** | P2 | No `forceStop` when pad transitions to parked/disconnected mid-hold (`SlewPad.tsx`, missing effect). | bounded by deadman |
| **S8** | P2 | Blur/visibility panic paths post a single best-effort `move 0` (not `/api/mount/stop`); lost POST → relies on deadman (`SlewPad.tsx:146-159`). | bounded by deadman |
| **S9** | P2 | No STOP control reachable while screen locked; abort gated behind 60% slide (`TouchGuard.tsx:250-266`). | ergonomic delay |
| **S10** | P1 | **pointercancel unlocks the safety lock**: OS-cancelled slide at pct≥0.6 disengages the lock without a completed gesture (`TouchGuard.tsx:156`). | fails UNSAFE (lock) |
| **S11** | P1 | Screen lock is **keyboard-inoperable** → keyboard/AT user permanently trapped, cannot reach STOP/abort/park (`TouchGuard.tsx:108-166`). WCAG 2.1.1/2.1.2. | AT user trap |

**Note:** S1+S5 share the same root (unconditional mirror-zero on failed stop). S3 is reported by **four** separate findings (slewController P1 ×2, hub P3, app.py P3) — collapsed below.

---

## DEDUPE MAP

- **`app.py:749-752` arming-order** appears in 4 findings (the P1 jitter, the P2 cancel-window, the P2 first-command client mirror, and is the root the client P2 points at). → **One fix (F-A1)** closes all.
- **Keepalive-vs-deadman margin (1000 vs 1200ms)** appears in 4 findings (slewController P1, slewController P2, hub P3, "onboarding-correctness" P3). → **One fix (F-A2)**, prefer arming-before-await (F-A1) + margin change.
- **Failed `tel.stop()` durability** appears in 2 findings (hub P1, hub P2). → **One fix (F-A3)**.
- **Missing `index.css` classes** appears in 3 findings (regression P2, a11y P1, touch-size P2). → **One fix (F-C1)**.
- **`lockAvailable` never flipped** appears in 2 findings (P1 + P2, same defect). → **One fix (F-B2)**.
- **`pushConfirm` promise leak** appears in 3 findings (P3 onboarding, P3 contract, P2 a11y). → **One fix (F-D1)**.
- **`pf.alt` null/interpolation guard in MountView** appears in 2 findings (P3 ×2), both note lines 66 AND 74. → **One fix (F-D2)**.
- **BottomNav missing Monitor/Settings + no "you are here"** appears in 2 findings (P2 ×2). → **One fix (F-B3)**.
- **BottomNav vs desktop gating inconsistency** appears in 2 findings (P3 ×2). → **One fix (F-D3)**.
- **Quick-tap double-move** appears in 2 findings (P3 ×2). → **One fix (F-D4)**.
- **`confirmDialog` ReactNode-body drop** appears in 2 findings (P3 ×2). → folded into F-D1.

---

## P0 — SHIP BLOCKERS (fix first)

### F-P0.1 — Wire the pre-flight gate into Run Sequence
**File:** `ui/src/views/SequenceView.tsx` (Run button 435-438; Re-run 231-232)
**Change:** Render `<PreflightStrip plan={plan} onReview={()=>setPreflightOpen(true)}/>` above the Run button. Compute `const {verdict}=usePreflight(plan)`; disable Run when `verdict==='blocked'`. Route Run's onClick through `<PreflightModal onProceed={(force)=>act(()=>api.post('/api/sequence/start',{...}))}/>`, threading `force`. Apply identically to the Re-run path (231-232). Add a test asserting a blocked `CheckItem` disables/redirects Run.
**Why:** `PreflightStrip`/`PreflightModal`/`usePreflight` are orphaned (imported only by each other — grep-confirmed). Today a below-horizon/unguided/disk-critical/can't-cool plan runs unattended with no readiness check; the block surfaces only as a raw 409 (horizon) or not at all (camera/filters/disk are never re-checked at start). This is the batch's stated acceptance ("pre-flight gate before Run").
**Depends on:** F-P1.6 (force must be readable server-side, else accepted-low runs re-409).

---

## P1 — FIX BEFORE SHIP

### F-A1 — Arm the deadman BEFORE the move await *(safety-of-motion; closes S2-window, S4, S8-root, and removes RTT from the jitter budget)*
**File:** `server/astrodeck/api/app.py:749-752`
**Change:** Move `hub.note_move(body.axis, rate)` to **before** `await tel.move_axis(body.axis, rate)`. (Keep the clamped `rate` — matches `test_move_endpoint_clamps_rate_and_arms_deadman`.)
**Why:** `note_move` is the only writer of `last_move_ts`/`_move_rates_seen` (verified hub.py:390-399). Arming after the await means (a) a cancel/exception between await-return and note_move leaves a moving axis uncovered (S4), and (b) the stamp cadence inherits driver RTT, consuming the jitter budget. Arming first is safe: if `move_axis` raises, the next watchdog tick issues a redundant `tel.stop()` on a non-moving axis (harmless); a rate-0 stop sets `moving=False` so no spurious halt.

### F-A2 — Widen the keepalive/deadman margin *(safety-of-motion; collapses 4 jitter findings)*
**Files:** `ui/src/lib/slewController.ts:39`; optionally `server/astrodeck/hub.py:52`
**Change:** Lower `KEEPALIVE_MS` to **~600ms** (refresh at ~½ the deadman → 3 stamps per window, ~600ms jitter tolerance) so a single throttled/dropped/jittered tick is survivable. Keep `MOVE_DEADMAN_MS=1200` (preserves the documented ≤0.72° travel budget). Correct the `slewController.ts:9-11` comment to scope the ≤1.2s guarantee to **network loss only**, not client timer starvation. Add a watchdog test that re-stamps at the **real ~600-1000ms cadence** (existing test re-stamps every 200ms and never exercises the tightness).
**Why:** Steady RTT cancels; jitter does not. 200ms is below one TCP-retransmit (~200ms+) and below background-tab `setInterval` clamp jitter. F-A1 removes RTT from the equation but the timer-starvation case still needs margin. Shortening keepalive (vs widening deadman) avoids increasing true-dropout travel.

### F-A3 — Make the deadman halt durable *(safety-of-motion; closes S1 + S5)*
**File:** `server/astrodeck/hub.py:424-432`; also `server/astrodeck/devices/alpaca.py:523-531`
**Change:** Only zero `_move_rates_seen` **after** `tel.stop()` succeeds. On exception: leave rates non-zero and `last_move_ts` stale so the next 250ms tick retries (bounded backoff, error-level log). In `alpaca.stop()`, catch httpx transport exceptions (not just `DeviceError`) around `abortslew` so its failure still attempts the `move_axis(0,0)` zeroing. Add a failing-`stop()` test (current suite only exercises the happy path).
**Why:** The single best-effort stop is swallowed (`except Exception: pass`) and the mirror is zeroed unconditionally → after one failed stop the `moving` gate is False forever and the watchdog never retries. The dropped keepalive that triggers the halt is exactly the network condition that makes the stop HTTP call fail → indefinite uncommanded travel (OTA-into-pier risk) during unattended operation.

### F-A4 — Fix multi-touch axis corruption *(safety-of-motion; S2)*
**Files:** `ui/src/components/SlewPad.tsx` (pointerdown handlers); `ui/src/lib/slewController.ts:82-201`
**Change:** Simplest correct remedy: in SlewPad, track the active pointerId in `onPointerDown` and **reject a second concurrent press** while `ctrl.isHolding()` is true (the method exists at slewController.ts:103 but is never called). Alternatively key controller state by axis (`Map<Axis,{dir,pressAt,isHold}>`) and re-assert all held axes. Add a multi-touch test (none exists).
**Why:** A second `startHold` overwrites the single `curAxis`/`pressIsHold`; on the genuinely-stuck ordering (first-pressed axis released first after a real >200ms hold) `stopHold` zeroes the *wrong* axis, no tapNudge fires, and the original axis slews until the 1.2s deadman.

### F-A5 — pointercancel must NOT unlock the safety lock *(safety-of-motion; S10)*
**File:** `ui/src/components/TouchGuard.tsx:125-131, 156`
**Change:** Split `onPointerCancel` into its own handler that resets `pct`/`startX`/`activePointerId` to initial **without** evaluating the threshold (do not route it through `onUp`). Track `activePointerId` in `onDown` and ignore move/up for other pointers.
**Why:** `onPointerCancel={onUp}` evaluates `if (pct>=0.6) onUnlock()`; an OS-reclaimed gesture (scroll/palm-reject/backgrounding) at pct≥0.6 disengages the lock guarding live mount motion without a completed gesture.

### F-A6 — Keyboard-operable screen lock *(safety-of-motion / WCAG; S11)*
**File:** `ui/src/components/TouchGuard.tsx:108-166, 250-266`
**Change:** Add a keyboard unlock path: either give the slide handle `onKeyDown` (Arrow/Enter advances `pct` to threshold → `onUnlock`) or render an explicit focusable Unlock `<button>` with `onClick` (single tap is acceptable — unlocking is non-destructive). Add an ESC-to-unlock handler, set initial focus to the unlock control on engage, and trap focus inside the overlay. Drop the false `role="slider"` (no tabIndex/keyboard) or implement full slider semantics. This also resolves the standalone P2 `role="slider"` misuse finding.
**Why:** SlideToUnlock is pointer-only; auto-lock can engage without a deliberate touch and strand a keyboard/switch/AT user with no way to reach STOP/abort/park. WCAG 2.1.1/2.1.2 keyboard trap.

### F-B1 — Assertive announcement for below-horizon auto-stop *(safety a11y)*
**File:** `ui/src/components/SlewPad.tsx:304-312`
**Change:** Split the live region: keep rate/hold text `aria-live="polite"`; render the belowHorizon "BELOW HORIZON LIMIT — stopped" message in a separate `role="alert"`/`aria-live="assertive"` node (wrap the ▣ glyph in `aria-hidden`). Mirror an assertive "All motion stopped" on STOP-bar press to match `haptics.stop()`.
**Why:** A motion-stop announcement queued behind polite speech may be delayed/dropped; the SR user may not learn the mount auto-halted.

### F-B2 — Flip `lockAvailable` true (or document degraded)
**File:** `ui/src/store.ts:411` (init); update stale comments at 198-200, 289; `App.tsx:401`; `NavMoreSheet.tsx:158-171`
**Change:** Initialize `lockAvailable: true` — its documented unblock precondition (sequence-error render: `TouchGuard.tsx:174` reads `sequence.state==='error'`, StatusChip renders it) has shipped this batch. Update the now-stale "soon"/"inert" copy.
**Why:** The flag is initialized false and **never written true** (grep-confirmed: only read). The entire screen-lock/TouchGuard feature is dead — auto-lock idle timer early-returns, "Lock Screen" is permanently disabled — and `setLocked(true)`'s `/api/mount/stop` backstop can never fire from the lock UI. Fails safe (visibly disabled), hence P1 not P0.

### F-P1.6 — Accept `force` in the sequence-start body
**File:** `server/astrodeck/api/app.py:882-883` (+ unused `StartSequenceBody` at 194-196)
**Change:** Change `sequence_start` to accept `StartSequenceBody{plan,force}` and read `body.plan`/`body.force` (matching the GOTO body-param convention; the UI's `api.post` only ever sends a JSON body, never a query string). Add a regression test that `force=true` actually bypasses the horizon 409.
**Why:** `force` is currently a **query** param while the defined body model is unused; once F-P0.1 wires the gate, a natural `{...plan, force:true}` body is silently ignored → an accepted low/below-horizon run is re-blocked 409. Latent until F-P0.1 lands — fix together.

---

## P2 — SHOULD FIX (grouped)

- **F-A1-tail (S4):** covered by F-A1.
- **F-S6 — Validate move axis:** `app.py:115-117` — constrain `axis` to `Literal['ra','dec']` (422 on unknown). Prevents a mistyped axis driving Alpaca DEC with no deadman coverage. *(safety-of-motion)*
- **F-S7 — forceStop on park/disconnect:** `SlewPad.tsx` — add `useEffect(()=>{ if(padDisabled) ctrl.forceStop(); },[padDisabled,ctrl])`, mirroring the existing `locked` effect (163-165). *(safety-of-motion)*
- **F-S8 — Authoritative panic stop:** `SlewPad.tsx:146-159` — have the blur/visibility(hidden)/unmount handlers also POST `/api/mount/stop` (abort+zero both axes), as the STOP bar and `setLocked(true)` already do. (Lock path is **already** covered via `store.setLocked` — do not touch it.) *(safety-of-motion)*
- **F-S5b — Self-disarm on explicit stop:** `app.py:757-768` — after `await tel.stop()` add `hub.note_move("ra",0.0); hub.note_move("dec",0.0)` so an explicit/lock stop disarms the deadman cleanly instead of leaving stale seen-rates for a redundant watchdog fire+log. *(safety hygiene)*
- **F-S9 — STOP reachable while locked:** `TouchGuard.tsx:250-266` — add an always-visible ≥56px emergency STOP inside the locked overlay calling `/api/mount/stop` + `/api/sequence/abort`, and/or auto-surface a one-tap STOP when `sequence.state==='error'`. *(safety ergonomics)*
- **F-C1 — Land the Batch-3 `index.css` touch tokens:** define `@keyframes alert-pulse` + `.alert-pulse` **including the prefers-reduced-motion KEEP exception** (static `outline` fallback, mirroring `.led-bad`), `.more-sheet-in`, and the `.touch-ui .tap`/`.no-touch-ui .tap`/`.tap-lg`/`--tap-gap` sizing rules so the Touch-size override is live. (`.confirmhold-fill` is a no-op — its fill is drawn entirely by inline style; define it only for parity, not function.) Add a guard test/grep asserting every bare component class has a CSS definition (Tailwind v4 won't catch this). Resolves the a11y P1, regression P2, and touch-size P2 findings together.
- **F-B3 — BottomNav reaches Monitor/Settings + always shows "you are here":** `BottomNav.tsx:81`/`NavMoreSheet.tsx:30-34` — change `overflowActive = !PRIMARY_IDS.has(view)` so any non-primary view lights the More tab with a looked-up sub-label; append `monitor` (and `settings`) to `OVERFLOW_VIEWS` / the More sheet's controls block. Restores the desktop rail's coverage on mobile (Settings is otherwise unreachable on a phone; Monitor only transiently). Covers both BottomNav P2 findings.
- **F-D1a — Checklist live region:** `Checklist.tsx:125-130` — add a dedicated visually-hidden `aria-live` delta-announcer (assertive for ok/warn→blocked) rather than a region-level live on the re-sorted list. Severity is pre-flight a11y, not the e-stop path.
- **F-B-autolock-a11y — Auto-lock countdown announcement:** `TouchGuard.tsx:237-245` — add `role="alert"`/`aria-live="assertive"` to the countdown and focus the "Keep awake" cancel button.
- **F-D1 — Fix `pushConfirm` promise leak + ReactNode body:** `store.ts:554-557` — before replacing, `const prev=get().confirm; if(prev) prev.resolve(false)`. Either narrow `ConfirmOpts.body` to `string` or widen `ConfirmRequest.body` to `ReactNode` and render as-is (ConfirmHost already renders `{req.body}`). Don't advertise `ReactNode` and drop it. Collapses the 3 confirm findings. *(latent; no STOP path depends on it)*
- **F-help — Sequence Automation hints:** `SequenceView.tsx:380-433` — attach `InfoDot content={HELP.x}` to the dither/hfrReject/filterOffset/meridianFlip labels (dead help copy today), and add the missing `plan.dither_pixels` input (model already supports it) rather than dropping HELP.dither's "pixels value" sentence.
- **F-dimmer — Single-source dimmer state:** `App.tsx:144-177` / `HeaderControls.tsx:50-71` — move day/night brightness into a shared store slice both subscribe to, replacing App's MutationObserver+`getComputedStyle` round-trip.

---

## P3 — NICE TO HAVE (batch; non-blocking)

- **F-A3-test** — Add the watchdog coverage gaps: failing-`stop()`, axis-switch (arm RA→arm DEC→drop keepalive→both end 0), NINA-`move_axis`-raises-doesn't-arm. (Rewrite the proposed 1199/1201ms boundary sub-test — the 250ms tick gives ±250ms granularity; rescope to "no halt at cadence, halt by ~1450ms.")
- **F-watchdog-disarm** — `hub.py:430` set `self.last_move_ts = None` alongside zeroing rates so the watchdog returns to a clean disarmed state (currently "correct by luck"). *(safety hygiene)*
- **F-D2** — `MountView.tsx:66 AND 74` guard `${pf.alt}°` interpolation (`pf.alt != null ? ... : 'an unknown altitude'`); `alt` is `number|null`. Both branches, not just one.
- **F-D4** — Quick-tap double-move (`slewController.ts:138-158`): don't fire `tapNudge` after a real continuous-rate hold (reserve it for the pulse/NINA path), or gate the optimistic `startHold` behind a small dwell. Pick one model.
- **F-D3** — Unify desktop hard-disable vs mobile tappable-then-interstitial gating model (`App.tsx:310-348` vs `BottomNav`/`NavMoreSheet`). Pick one — likely drop the desktop hard-disable and rely on the interstitial everywhere.
- **F-sim** — `sim.py:316` add `self.rig.ra_hours %= 24.0` (and in `slew()`/`pulse_guide()`); sim-fidelity only, no effect on deadman/altaz/meridian (both consumers are wrap-invariant — the finding's "garbage" rationale is wrong, keep only the fix).
- **F-wakelock** — `useWakeLock.ts:63-65` on sentinel 'release', if `wantRef.current && document.visibilityState==='visible'`, debounced re-acquire. *(screen-only; not safety)*
- **F-focus-trap** — `NavMoreSheet.tsx` add focus trap/initial-focus/restore (reuse the `ConfirmHost` primitive). *(a11y; nav-only sheet)*
- **F-ctxmenu** — `SlewPad.tsx` add `onContextMenu={e=>e.preventDefault()}` per arrow to close the desktop right-click-during-hold window. *(defense-in-depth)*
- **F-tap-precision** — duplicate of F-D4 (same root).
- **F-interstitial-deadconfig** — `NotConnectedInterstitial.tsx:23-24` drop dead `sequence`/`monitor` VIEW_META entries (not gated).
- **F-alt-guard-doc** — `SlewPad.tsx:360`/`slewController.ts` document MIN_SLEW_ALT_DEG's ~0.6° overshoot margin (drop the spurious null-check half — `alt` is required `number`).
- **F-preflight-poll** — latent double-poll if PreflightStrip+Modal both mount; hoist `altById` to a shared hook. Currently unmounted, fix opportunistically with F-P0.1.

---

## SUMMARY

- **P0 (2 findings → 1 root):** Pre-flight gate dead-code (F-P0.1) + its `force`-body dependency (F-P1.6). **Ship blocker.**
- **P1 (after dedupe):** F-A1 (arm-before-await), F-A2 (keepalive margin), F-A3 (durable halt), F-A4 (multi-touch), F-A5 (pointercancel unlock), F-A6 (keyboard lock), F-B1 (assertive stop announce), F-B2 (lockAvailable). **All must land before ship.**
- **P2:** ~14 items, heavily clustered (F-C1 index.css covers 3 findings; F-B3 covers 2; F-D1 covers 3).
- **P3:** ~14 polish/hygiene/test items.

**Highest-leverage single fix:** F-A1 (arming order) — one ~2-line server change neutralizes the S4 coverage window, removes driver RTT from the jitter budget (helping S3), and is the shared root the client-side P2 points at.

**Most dangerous fail-UNSAFE defect:** F-A3 (S1/S5) — the deadman gives up after one failed stop in precisely the network-loss scenario it exists for. This is the one finding where the safety backstop fails *unsafe* (indefinite travel) rather than fail-safe; prioritize alongside F-P0.1.
