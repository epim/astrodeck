// TouchGuard.tsx — monitor-safe screen lock (touch spec §8, R11/R12/R13).
//
// NOT a translucent scrim (the draft's wash could hide the one alert you must
// see). This is a full-viewport INPUT BLOCKER with a SOLID `bg-panel` status chip
// (full contrast, deterministic) carrying the live readout. Critical-alert
// passthrough is GATED: while `lockAvailable` is false (reliability's sequence-
// error render not yet shipped) the lock simply does not exist, so it can never
// mask an alert. The Lock control elsewhere is disabled-with-tooltip until then.
//
// Behaviors:
//   - rendered once at App root with a NARROW selector (useLocked) — no widening.
//   - slide-to-unlock (~60% of a 200px track), NOT an 800ms cold-glove hold (R12).
//   - auto-lock: off / 3min / 5min (1-min removed — R12); never within 30s of a
//     manual control interaction; a dismissible "locking in 5s" countdown precedes
//     engage (its own element, NOT the error-toast slot).
//   - setLocked(true) forceStops the slew (store does it; we also fire on engage).
//   - WakeLock is NOT coupled here (R13) — locking lets the screen sleep.
//   - the ONE animation kept in night mode is the safety alert pulse (R22).
//
// `monitorAwake` (the explicit wake-as-monitor toggle) lives in the More sheet;
// this component only handles the lock overlay + the auto-lock idle timer.

import { useCallback, useEffect, useRef, useState } from "react";
import type { PointerEvent as RPointerEvent, KeyboardEvent as RKeyboardEvent } from "react";
import { api, ApiError } from "../api";
import { useStore } from "../store";
import { Icon } from "./icons";
import { haptics } from "../lib/haptics";
import { accessPhrase } from "../lib/caps";
import {
  useLocked,
  useLockAvailable,
  useTouchSettings,
  useSetLocked,
} from "../lib/touchStore";

const SLIDE_TRACK_PX = 200;
const SLIDE_THRESHOLD = 0.6; // 60% of the track
const MANUAL_GRACE_MS = 30000; // never auto-lock within 30s of an interaction (R12)
const COUNTDOWN_MS = 5000; // dismissible "locking in 5s" pre-engage

// ----------------------------------------------------------------- status chip
function StatusChip({ alert }: { alert: boolean }) {
  // Narrow selectors so the chip re-renders on telemetry but the rest of the app
  // (already input-blocked) doesn't care.
  const mount = useStore((s) => s.status?.mount ?? null);
  const guider = useStore((s) => s.status?.guider ?? null);
  const seq = useStore((s) => s.sequence);
  const camTemp = useStore((s) => s.status?.camera?.temperature ?? null);

  const state = mount
    ? mount.parked
      ? "PARKED"
      : mount.slewing
        ? "SLEWING"
        : mount.tracking
          ? "TRACKING"
          : "IDLE"
    : "—";

  return (
    <div
      className={`panel bg-panel px-4 py-3 mono text-sm text-ink flex flex-col gap-1.5 min-w-[260px]
        ${alert ? "border-bad alert-pulse" : ""}`}
    >
      {alert && (
        <div className="flex items-center gap-2 text-bad font-display tracking-wider">
          <Icon name="alert" size={16} /> SEQUENCE ERROR
        </div>
      )}
      <div className="flex items-center justify-between gap-6">
        <span className="text-dim text-xs">STATE</span>
        <span className={mount?.tracking ? "text-good" : "text-warn"}>{state}</span>
      </div>
      {mount && (
        <>
          <div className="flex items-center justify-between gap-6">
            <span className="text-dim text-xs">RA / DEC</span>
            <span>
              {mount.ra_str} {mount.dec_str}
            </span>
          </div>
        </>
      )}
      {guider?.guiding && (
        <div className="flex items-center justify-between gap-6">
          <span className="text-dim text-xs">RMS</span>
          <span className="text-good">{guider.rms_total.toFixed(2)}&quot;</span>
        </div>
      )}
      {seq.progress && (
        <div className="flex items-center justify-between gap-6">
          <span className="text-dim text-xs">SEQ</span>
          <span>
            {seq.progress.frames_done}/{seq.progress.frames_total}
          </span>
        </div>
      )}
      {camTemp != null && (
        <div className="flex items-center justify-between gap-6">
          <span className="text-dim text-xs">SENSOR</span>
          <span>{camTemp.toFixed(1)}°C</span>
        </div>
      )}
    </div>
  );
}

// ----------------------------------------------------------------- slide unlock
// Pointer users slide the handle (~60% of the track). Keyboard / switch / AT users
// get an explicit focusable Unlock <button> beside the track (F-A6) — a single
// activation unlocks (unlocking is non-destructive). The slide is now a pointer
// affordance only; the false role="slider" (no keyboard model) is GONE (F-A6).
function SlideToUnlock({
  onUnlock,
  buttonRef,
}: {
  onUnlock: () => void;
  buttonRef?: (el: HTMLButtonElement | null) => void;
}) {
  const [pct, setPct] = useState(0);
  const startX = useRef<number | null>(null);
  // F-A5: bind the slide to ONE pointer; ignore any other pointer's move/up/cancel.
  const slidePointerId = useRef<number | null>(null);

  const reset = useCallback(() => {
    setPct(0);
    startX.current = null;
    slidePointerId.current = null;
  }, []);

  // The gesture that ends without a pointer event at all. pointerup /
  // pointercancel / lostpointercapture cover palm-reject, a scroll stealing the
  // gesture and a drag released off the handle — but backgrounding the app, the
  // tablet's screen timeout and an OS focus steal deliver NONE of them. Left
  // that way the handle stays translated mid-track with the accent fill behind
  // it, and `slidePointerId` still owns the slider, so `onDown` returns at its
  // guard on every later touch: the primary unlock affordance looks half-slid
  // and is dead for the rest of the locked session. Exactly the latch SlewPad
  // documents at SlewPad.tsx:205-228 and HoldButton guards at ui.tsx:415-430;
  // this is the same three-line effect.
  //
  // reset() only ever CLEARS — it can never complete an unlock, because the 0.6
  // threshold is read solely inside onUp. Losing focus must not open the lock.
  useEffect(() => {
    const onVis = () => {
      if (document.visibilityState === "hidden") reset();
    };
    window.addEventListener("blur", reset);
    document.addEventListener("visibilitychange", onVis);
    return () => {
      window.removeEventListener("blur", reset);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [reset]);

  const onDown = (e: RPointerEvent) => {
    if (slidePointerId.current != null) return; // a slide already owns the handle
    slidePointerId.current = e.pointerId;
    startX.current = e.clientX;
    try {
      (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
    } catch {
      /* ok */
    }
  };
  const onMove = (e: RPointerEvent) => {
    if (startX.current == null || slidePointerId.current !== e.pointerId) return;
    const dx = e.clientX - startX.current;
    setPct(Math.max(0, Math.min(1, dx / SLIDE_TRACK_PX)));
  };
  // A real, completed slide gesture: only here do we evaluate the threshold.
  const onUp = (e: RPointerEvent) => {
    if (slidePointerId.current !== e.pointerId) return;
    const reached = pct >= SLIDE_THRESHOLD;
    reset();
    if (reached) onUnlock();
  };
  // F-A5 / S10: pointercancel is an OS-RECLAIMED gesture (scroll / palm-reject /
  // backgrounding), NOT a deliberate unlock. It must reset state WITHOUT ever
  // evaluating the threshold — an interrupted slide at pct≥0.6 must NOT unlock the
  // safety lock guarding live mount motion.
  const onCancel = (e: RPointerEvent) => {
    if (slidePointerId.current !== e.pointerId) return;
    reset();
  };

  return (
    <div className="flex flex-col items-center gap-3">
      <div
        className="relative h-12 rounded-full border border-line2 bg-raise overflow-hidden select-none"
        style={{ width: SLIDE_TRACK_PX, touchAction: "none" }}
        aria-hidden /* pointer affordance only; the Unlock button below is the a11y path */
      >
        <div
          className="absolute inset-y-0 left-0 bg-accent/20"
          style={{ width: `${pct * 100}%` }}
        />
        <span className="absolute inset-0 flex items-center justify-center text-[11px] tracking-[0.25em] text-dim pointer-events-none font-display">
          SLIDE TO UNLOCK
        </span>
        <button
          type="button"
          tabIndex={-1} /* keyboard reaches the explicit Unlock button below */
          onPointerDown={onDown}
          onPointerMove={onMove}
          onPointerUp={onUp}
          onPointerCancel={onCancel}
          onLostPointerCapture={onCancel}
          className="absolute top-1 bottom-1 left-1 aspect-square rounded-full bg-accent
            flex items-center justify-center text-black/90 cursor-grab active:cursor-grabbing"
          style={{ transform: `translateX(${pct * (SLIDE_TRACK_PX - 48)}px)` }}
        >
          <Icon name="unlock" size={18} className="!text-black/90" />
        </button>
      </div>
      {/* F-A6: keyboard-operable unlock — focusable, single activation, non-destructive. */}
      <button
        type="button"
        ref={buttonRef}
        onClick={onUnlock}
        className="btn tap min-h-[44px] px-5 inline-flex items-center gap-2 font-display tracking-wider"
      >
        <Icon name="unlock" size={16} /> UNLOCK
      </button>
    </div>
  );
}

// ----------------------------------------------------------------- TouchGuard
export default function TouchGuard() {
  const locked = useLocked();
  const lockAvailable = useLockAvailable();
  const setLocked = useSetLocked();
  const { autoLockMs } = useTouchSettings();
  const seqError = useStore((s) => s.sequence.state === "error");

  // --- auto-lock idle timer (R12) ----------------------------------------------
  // Watches pointerdown/keydown; never engages within MANUAL_GRACE_MS of one. A
  // dismissible countdown precedes the actual lock.
  const [countdown, setCountdown] = useState(false);
  const lastInteractRef = useRef(Date.now());
  const idleTimer = useRef<number | null>(null);
  const countdownTimer = useRef<number | null>(null);
  // Mirrors `countdown` for the interval's `check` closure WITHOUT being a
  // dependency of the arming effect below — see the effect's comment.
  const countdownRef = useRef(false);

  const setLockedRef = useRef(setLocked);
  setLockedRef.current = setLocked;
  const lockNow = useCallback(() => {
    haptics.warn();
    setLockedRef.current(true); // store forceStops the slew; overlay gates on `locked`
    setCountdown(false);
  }, []);

  useEffect(() => {
    // disabled when no auto-lock configured, gate not available, or already locked.
    if (!autoLockMs || !lockAvailable || locked) {
      countdownRef.current = false;
      setCountdown(false);
      return;
    }
    const bump = () => {
      lastInteractRef.current = Date.now();
      countdownRef.current = false;
      setCountdown(false);
      if (countdownTimer.current != null) {
        clearTimeout(countdownTimer.current);
        countdownTimer.current = null;
      }
    };
    window.addEventListener("pointerdown", bump);
    window.addEventListener("keydown", bump);

    const check = () => {
      const idle = Date.now() - lastInteractRef.current;
      // require the full grace AND the configured idle window before counting down.
      // `countdownRef` (not the `countdown` state) gates re-arming: reading state
      // here would need `countdown` in the dep array below, and the resulting
      // re-run's cleanup would clearTimeout() the 5s lock timer the INSTANT it's
      // armed (React tears down the previous effect before the new one runs) —
      // lockNow() would then be unreachable via the idle path. The ref lets the
      // single long-lived interval track countdown state without re-arming.
      if (idle >= Math.max(autoLockMs, MANUAL_GRACE_MS) && !countdownRef.current) {
        countdownRef.current = true;
        setCountdown(true);
        countdownTimer.current = window.setTimeout(lockNow, COUNTDOWN_MS);
      }
    };
    idleTimer.current = window.setInterval(check, 1000);

    return () => {
      window.removeEventListener("pointerdown", bump);
      window.removeEventListener("keydown", bump);
      if (idleTimer.current != null) clearInterval(idleTimer.current);
      if (countdownTimer.current != null) clearTimeout(countdownTimer.current);
    };
  }, [autoLockMs, lockAvailable, locked, lockNow]);

  const cancelCountdown = () => {
    countdownRef.current = false;
    setCountdown(false);
    lastInteractRef.current = Date.now();
    if (countdownTimer.current != null) {
      clearTimeout(countdownTimer.current);
      countdownTimer.current = null;
    }
  };

  // The pre-engage "locking in 5s" affordance (its own element, NOT the toast slot).
  const countdownEl =
    countdown && !locked ? (
      <div
        className="fixed bottom-20 left-1/2 -translate-x-1/2 z-50 panel bg-panel px-4 py-2 flex items-center gap-3 text-xs"
        role="alert"
        aria-live="assertive"
      >
        <span className="text-warn font-display tracking-wider">LOCKING SCREEN…</span>
        <button
          className="btn !py-1 !px-3 min-h-[44px]"
          onClick={cancelCountdown}
          ref={(el) => el?.focus()}
        >
          Keep awake
        </button>
      </div>
    ) : null;

  if (!locked) return countdownEl;

  return <LockedOverlay seqError={seqError} onUnlock={() => setLocked(false)} />;
}

// ----------------------------------------------------------------- locked overlay
/** What the last EMERGENCY STOP press actually achieved. `sending` while the
 *  two POSTs are in flight; then the resolved answer, which is what the buzz
 *  and the line under the button both come from. */
type StopOutcome =
  | { phase: "sending" }
  | { phase: "stopped" }
  | { phase: "failed"; detail: string };

/** A refusal in words the person holding the tablet can act on. The server's
 *  own 403 detail is "capability not held", which names nothing. */
function stopFailureDetail(what: string, err: unknown): string {
  if (err instanceof ApiError && err.status === 403) {
    return `${what}: this account can only watch — stopping needs ${accessPhrase("control.mount")}`;
  }
  return `${what}: ${err instanceof Error ? err.message : String(err)}`;
}

// Split out so the focus-trap / ESC / initial-focus hooks (F-A6) only mount while
// the lock is up. Full-viewport input blocker + opaque chip + keyboard-operable
// unlock + an always-reachable emergency STOP (F-S9).
function LockedOverlay({ seqError, onUnlock }: { seqError: boolean; onUnlock: () => void }) {
  const overlayRef = useRef<HTMLDivElement | null>(null);
  const unlockBtnRef = useRef<HTMLButtonElement | null>(null);
  const [stop, setStop] = useState<StopOutcome | null>(null);
  // The engine's own word for "the teardown is running and the rig has not
  // stopped yet". Read LIVE rather than latched into the outcome, so the line
  // under the button upgrades itself the moment the wind-down finishes instead
  // of describing the press forever.
  const seqAborting = useStore((s) => s.sequence.state === "aborting");

  // F-A6: set initial focus to the unlock control so a keyboard/switch user lands
  // somewhere actionable the instant the lock engages.
  useEffect(() => {
    unlockBtnRef.current?.focus();
  }, []);

  // F-A6: ESC unlocks (non-destructive) + focus trap. Keeps Tab inside the overlay
  // so a keyboard user can never tab out into the input-blocked app behind.
  const onKeyDown = (e: RKeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      onUnlock();
      return;
    }
    if (e.key !== "Tab") return;
    const root = overlayRef.current;
    if (!root) return;
    const focusables = Array.from(
      root.querySelectorAll<HTMLElement>(
        'button:not([tabindex="-1"]), [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      ),
    ).filter((el) => !el.hasAttribute("disabled"));
    if (focusables.length === 0) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    const active = document.activeElement as HTMLElement | null;
    if (e.shiftKey && active === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && active === last) {
      e.preventDefault();
      first.focus();
    }
  };

  // F-S9: authoritative emergency stop — abort the sequence AND zero both axes.
  //
  // UX-2026-08-05 #25. Three things were wrong with the old version, all of the
  // same shape: it reported the PRESS, not the rig.
  //   * it buzzed the confirming `stop` pattern before either POST had left, so
  //     a stop that 403'd or never reached the box felt identical to one that
  //     zeroed both axes;
  //   * it swallowed 403 silently, which is exactly the case where someone
  //     stands there believing the mount was told to stop;
  //   * its error channel was `showToast`, and Toasts is a z-40 layer while this
  //     input blocker is z-50 over it with `pointerEvents: auto` — so the one
  //     message on this screen that MUST arrive painted underneath the lock and
  //     could not be read or dismissed.
  // The outcome is therefore rendered inside the overlay, and the haptic waits
  // for the answer: a light `tap` acknowledges the press, `stop` confirms the
  // rig stopped, `error` says it did not.
  //
  // Deliberately NOT disabled while one is in flight — /api/mount/stop is
  // idempotent, and a 15s timeout on a bad link must never leave the emergency
  // control unpressable. `stopReq` keeps a slow first press from overwriting the
  // answer of the one after it.
  const stopReq = useRef(0);
  const emergencyStop = () => {
    haptics.tap();
    const req = ++stopReq.current;
    setStop({ phase: "sending" });
    // ALREADY TEARING DOWN. This button is deliberately never disabled, so the
    // second press of a stop that is still winding down is the normal case, not
    // the edge one. The engine no-ops a second abort now (sequence/engine.py) —
    // don't post it anyway, because its answer would overwrite the answer from
    // the press that actually did it. The mount stop still goes on every press:
    // it is idempotent and it is the reason this control exists.
    const abortAlreadyRunning = useStore.getState().sequence.state === "aborting";
    void (async () => {
      const [motion, seq] = await Promise.allSettled([
        api.post("/api/mount/stop"),
        abortAlreadyRunning ? Promise.resolve(null) : api.post("/api/sequence/abort"),
      ]);
      if (req !== stopReq.current) return;
      // A TIMED-OUT abort is not a failed abort. /api/sequence/abort awaits the
      // whole ~210 s wind-down against api.ts's 15 s cap, so on a real teardown
      // the abort that WORKED came back rejected — and this overlay is the only
      // surface a locked screen has, so it read "STOP DID NOT LAND" over a rig
      // that was stopping exactly as asked. The engine's "aborting" state is the
      // answer (see the line under the button); the request never was.
      const seqTimedOut = seq.status === "rejected"
        && seq.reason instanceof ApiError && seq.reason.timedOut;
      const failures = [
        motion.status === "rejected" ? stopFailureDetail("mount", motion.reason) : null,
        seq.status === "rejected" && !seqTimedOut
          ? stopFailureDetail("sequence", seq.reason) : null,
      ].filter((s): s is string => s != null);
      if (failures.length === 0) {
        haptics.stop();
        setStop({ phase: "stopped" });
        return;
      }
      haptics.error();
      setStop({ phase: "failed", detail: failures.join(" · ") });
    })();
  };

  return (
    <div
      ref={overlayRef}
      className="fixed inset-0 z-50 flex flex-col items-center justify-center gap-6 bg-bg/40"
      style={{ pointerEvents: "auto" }}
      role="dialog"
      aria-modal="true"
      aria-label="Screen locked"
      onKeyDown={onKeyDown}
      // swallow every tap to the app beneath (input blocker — not a visual scrim)
      onPointerDown={(e) => e.stopPropagation()}
    >
      <div className="font-display tracking-[0.3em] text-dim text-sm flex items-center gap-2">
        <Icon name="lock" size={16} /> SCREEN LOCKED
      </div>
      <StatusChip alert={seqError} />

      {/* F-S9: always-visible emergency STOP — reachable WITHOUT completing the
          unlock gesture. ≥56px, authoritative /api/mount/stop + /api/sequence/abort. */}
      <div className="flex flex-col items-center gap-2 w-[260px]">
        <button
          type="button"
          onClick={emergencyStop}
          aria-busy={stop?.phase === "sending"}
          aria-label="Emergency stop all motion and abort sequence"
          className="w-full min-h-[56px] flex items-center justify-center gap-2
            font-display tracking-[0.2em] text-[14px] text-black/90
            bg-bad border border-bad active:translate-y-px"
        >
          <Icon name="stop" size={18} className="!text-black/90" />
          {stop?.phase === "sending" ? "STOPPING…" : "EMERGENCY STOP"}
        </button>
        {/* The stop's own answer, in the overlay — see emergencyStop for why it
            cannot be a toast. `alert` on failure: nothing else on a locked
            screen will tell them the mount was never told to stop. */}
        {stop != null && stop.phase !== "sending" && (
          <p
            role={stop.phase === "failed" ? "alert" : "status"}
            className={`text-[11px] leading-snug text-center ${
              stop.phase === "failed" ? "text-bad"
                : seqAborting ? "text-warn" : "text-good"}`}
          >
            {stop.phase === "stopped"
              ? seqAborting
                // NOT "aborted": the engine is still ending the exposure and
                // stopping the guider, and this is the screen someone reads
                // before walking out to the scope.
                ? "Motion stopped. The sequence is still stopping — ending the "
                  + "exposure and the guider."
                : "Motion stopped, sequence aborted."
              : `STOP DID NOT LAND — ${stop.detail}`}
          </p>
        )}
      </div>

      <SlideToUnlock onUnlock={onUnlock} buttonRef={(el) => (unlockBtnRef.current = el)} />
    </div>
  );
}
