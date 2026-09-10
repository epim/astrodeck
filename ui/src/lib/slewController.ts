// slewController.ts — framework-agnostic manual-slew controller (touch spec §4.2).
//
// THE REVISED HEADLINE MODEL (post-critique R1): there is NO time-based
// acceleration ramp. "Hold longer = faster" was removed because it causes
// overshoot-oscillate when centering. Instead:
//   - TAP (quick press+release, no slide)  -> one fixed pulse/nudge.
//   - HOLD (finger held down)              -> slew at the CURRENTLY SELECTED fixed
//                                             rate ONLY, never auto-accelerating.
// A ~1.7Hz keepalive (KEEPALIVE_MS≈600) re-asserts the rate to feed the server
// move-axis deadman (hub._move_watchdog, 1200ms): refreshing at ~½ the deadman
// lands ~3 stamps per window, so a single throttled/dropped/jittered tick is
// survivable (~600ms jitter tolerance) without a false mid-slew STOP.
// SCOPE OF THE ≤1.2s GUARANTEE: it covers NETWORK loss only — on a dropped
// connection mid-hold the server halts the mount within ≤1.2s. The TRAVEL that
// costs is the ceiling times 1.2s, so it is per-mount since D-RIG-4: ≤0.72° at
// the 0.6°/s fallback, ≤1.73° on an AM5N reporting 1.44°/s. The mount sheet
// states whichever applies (`rig/lib/slewStops.ts deadmanNote`). It does NOT
// cover client timer starvation (backgrounded tab / setInterval clamp); the
// widened margin is what keeps a merely-jittered foreground tick from tripping
// the deadman early.
//
// Safety invariants this controller guarantees:
//   - rate is clamped to +/- THE DRIVER'S OWN CEILING when it reports one
//     (`getMaxRate`, from status.mount.max_rate_deg_s), and to
//     +/-TOUCH_MAX_RATE_DEG_S when it does not. Both halves mirror the server
//     clamp, which since D-RIG-4 reads `getattr(tel, "max_rate_deg_s", None) or
//     TOUCH_MAX_RATE_DEG_S` (hub.py:221-232): 0.6 is what a mount that cannot
//     say gets, not what every mount gets. A caller that passes no getMaxRate -
//     `#/classic` - is clamped at 0.6 exactly as before.
//   - reverse-RA / reverse-Dec flip the commanded sign at post time.
//   - an alt-guard checks status.mount.alt every keepalive tick; below
//     MIN_SLEW_ALT_DEG it forceStops and emits a `belowHorizon` state.
//   - forceStop() is idempotent and POSTs rate 0 (panic path for lock/blur/
//     visibility-hidden). Calling it when already stopped is harmless.
//   - in NINA mode hold is disabled for ALL rates (NINA move_axis raises); a tap
//     becomes a small relative GOTO. The `pulse` rate is tap-only in every mode.

import type { SlewRateOption } from "../types";
import { haptics } from "./haptics";

// ----------------------------------------------------------------- constants
// Rate labels computed from the REAL sidereal rate 15.041 arcsec/s = 0.004178°/s
// (R23 — the draft's labels were ~4x off). "8x SID" = 8 * 0.004178 = 0.0334°/s.
export const SIDEREAL_DEG_S = 0.004178;

export const SLEW_RATES: SlewRateOption[] = [
  { id: "pulse", label: "GUIDE", rateDegS: 0, pulseMs: 250 }, // tap-only fine nudge
  { id: "fine", label: "8× SID", rateDegS: 0.0334 },           // 8 × sidereal
  { id: "set", label: "0.5°/s", rateDegS: 0.5 },
];

export const TOUCH_MAX_RATE_DEG_S = 0.6; // mirror of server clamp (R2/R24)
export const MIN_SLEW_ALT_DEG = 10;      // client alt-guard (R30)
// ~½ the 1200ms server deadman (F-A2): ~3 stamps per window so one throttled/
// dropped/jittered foreground tick survives. NOT an 8Hz tick storm; NOT 1000ms
// (only 200ms of jitter margin → false mid-slew STOP under a TCP retransmit).
export const KEEPALIVE_MS = 600;         // re-assert at ~½ the deadman (R6/F-A2)
export const TAP_MAX_MS = 200;           // press shorter than this (no slide) = tap

export type Axis = "ra" | "dec";
export type Dir = -1 | 1;

// The controller's externally-observable state, surfaced for the UI label and the
// alt-guard flash. `belowHorizon` is the latched alt-guard trip (cleared on the
// next successful start/tap).
export type SlewMode = "idle" | "holding" | "belowHorizon";

export interface SlewState {
  mode: SlewMode;
  axis: Axis | null;
  dir: Dir | null;
  rate: SlewRateOption;
}

export interface SlewControllerOpts {
  getRate: () => SlewRateOption;                       // current selection
  reverseRa: () => boolean;
  reverseDec: () => boolean;
  getAlt: () => number | null;                         // status.mount.alt for the guard
  /** How fast THIS mount will actually slew, deg/s (D-RIG-4), from
   *  `status.mount.max_rate_deg_s`. `null` means the driver did not say, which
   *  is NOT "no limit": the clamp falls back to TOUCH_MAX_RATE_DEG_S, exactly
   *  as the server's does. Optional, so a caller that never passes it (the
   *  classic MountView) behaves byte-identically to before. */
  getMaxRate?: () => number | null;
  isNina?: () => boolean;                              // mode === "nina" -> hold disabled
  postMove: (axis: Axis, rateDegS: number) => Promise<void>;
  postNudge: (axis: Axis, dir: Dir) => Promise<void>;  // pulse OR small relative GOTO
  onStateChange?: (s: SlewState) => void;              // UI label + alt-guard flash
  onError?: (e: unknown) => void;                      // toast + debounced haptics.error()
  // injectable clock/timers for deterministic tests (default to real globals).
  now?: () => number;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (h: unknown) => void;
}

export class SlewController {
  private o: Required<Pick<SlewControllerOpts,
    "getRate" | "reverseRa" | "reverseDec" | "getAlt" | "postMove" | "postNudge">> &
    SlewControllerOpts;

  private holding = false;
  private mode: SlewMode = "idle";
  private curAxis: Axis | null = null;
  private curDir: Dir | null = null;

  private keepalive: unknown = null;
  private pressIsHold = false;

  // `now` is no longer needed: tap-vs-hold is decided on PRESS by rate (F-D4), not
  // by a release-time delta, so the controller never reads the clock. It remains an
  // accepted (ignored) opt for backward compatibility with existing call sites/tests.
  private setTimer: (fn: () => void, ms: number) => unknown;
  private clearTimer: (h: unknown) => void;

  constructor(opts: SlewControllerOpts) {
    this.o = opts as typeof this.o;
    this.setTimer = opts.setTimer ?? ((fn, ms) => setInterval(fn, ms));
    this.clearTimer = opts.clearTimer ?? ((h) => clearInterval(h as ReturnType<typeof setInterval>));
  }

  // --------------------------------------------------------------- introspection
  isHolding(): boolean {
    return this.holding;
  }

  getState(): SlewState {
    return {
      mode: this.mode,
      axis: this.curAxis,
      dir: this.curDir,
      rate: this.o.getRate(),
    };
  }

  private emit(): void {
    this.o.onStateChange?.(this.getState());
  }

  private fail(e: unknown): void {
    this.o.onError?.(e);
  }

  // The ceiling this clamp uses, in the server's own order of preference: the
  // driver's measured number when it has one, 0.6 when it does not.
  //
  // `> 0` and `isFinite` are not belt-and-braces, they are the `or` in the
  // server's `getattr(tel, "max_rate_deg_s", None) or TOUCH_MAX_RATE_DEG_S`
  // (hub.py:227-231): a driver that answers 0.0 is a driver that did not say,
  // and taking it literally would clamp every command to zero and leave a pad
  // that lights up, posts, and never moves the mount.
  private ceiling(): number {
    const said = this.o.getMaxRate?.() ?? null;
    return said != null && Number.isFinite(said) && said > 0 ? said : TOUCH_MAX_RATE_DEG_S;
  }

  private clampRate(r: number): number {
    const cap = this.ceiling();
    return Math.max(-cap, Math.min(cap, r));
  }

  // The signed, clamped, reverse-applied rate for a held axis/dir.
  private signedRate(axis: Axis, dir: Dir): number {
    const base = this.o.getRate().rateDegS;
    const rev = axis === "ra" ? this.o.reverseRa() : this.o.reverseDec();
    const sign = (rev ? -1 : 1) * dir;
    return this.clampRate(base * sign);
  }

  // --------------------------------------------------------------- press lifecycle
  // The SlewPad wires pointerdown -> beginPress and pointerup -> endPress. The press
  // model is decided ONCE on press by the selected rate (touch spec §4.3):
  //   - continuous rate (alpaca, non-pulse): beginPress starts a real hold; endPress
  //     just stops it. A quick stab is therefore a brief continuous slew, NOT a
  //     pulse — we no longer fire an extra tapNudge on a fast release (F-D4: that
  //     produced a double-move). TAP_MAX_MS now documents the tap-vs-slide intent
  //     only; it no longer branches the release.
  //   - tap-only rate (pulse) / NINA: no hold ever starts; endPress fires one nudge.
  beginPress(axis: Axis, dir: Dir): void {
    // pulse rate and NINA mode are tap-only: never start a hold for them.
    this.pressIsHold = this.o.getRate().rateDegS > 0 && !this.isNina();
    if (this.pressIsHold) this.startHold(axis, dir);
  }

  endPress(axis: Axis, dir: Dir): void {
    const wasHold = this.pressIsHold;
    if (wasHold) {
      // F-D4 (one model): a continuous-rate press already commanded a real slew on
      // beginPress. Releasing it just stops — we DO NOT also fire a tapNudge. The
      // old "quick stab still nudges at any rate" branch produced a double-move (a
      // brief continuous slew AND a pulse nudge from one tap). tapNudge is now
      // reserved for the genuine tap-only path (pulse rate / NINA) below.
      this.stopHold();
    } else {
      // tap-only path (pulse rate / NINA): always a single nudge.
      this.tapNudge(axis, dir);
    }
    this.pressIsHold = false;
  }

  private isNina(): boolean {
    return this.o.isNina?.() ?? false;
  }

  // --------------------------------------------------------------- hold (slew)
  startHold(axis: Axis, dir: Dir): void {
    if (this.isNina()) return;                 // NINA: no continuous slew (R8)
    if (this.o.getRate().rateDegS <= 0) return; // pulse rate is tap-only
    // Alt-guard at the gate: don't even start below the horizon limit.
    const alt = this.o.getAlt();
    if (alt != null && alt < MIN_SLEW_ALT_DEG) {
      this.tripBelowHorizon();
      return;
    }
    this.holding = true;
    this.mode = "holding";
    this.curAxis = axis;
    this.curDir = dir;
    haptics.start();
    void this.postMove(axis, this.signedRate(axis, dir));
    this.armKeepalive();
    this.emit();
  }

  stopHold(): void {
    if (!this.holding && this.mode !== "holding") {
      // still ensure no dangling keepalive
      this.disarmKeepalive();
      return;
    }
    const axis = this.curAxis;
    this.holding = false;
    this.disarmKeepalive();
    if (axis) {
      haptics.stop();
      void this.postMove(axis, 0);
    }
    this.curAxis = null;
    this.curDir = null;
    if (this.mode === "holding") this.mode = "idle";
    this.emit();
  }

  private armKeepalive(): void {
    this.disarmKeepalive();
    this.keepalive = this.setTimer(() => this.keepaliveTick(), KEEPALIVE_MS);
  }

  private disarmKeepalive(): void {
    if (this.keepalive != null) {
      this.clearTimer(this.keepalive);
      this.keepalive = null;
    }
  }

  // Re-assert the current rate once per second (feeds the server deadman) AND
  // re-check the alt-guard — a slew that drifts below the horizon auto-stops.
  private keepaliveTick(): void {
    if (!this.holding || !this.curAxis || !this.curDir) return;
    const alt = this.o.getAlt();
    if (alt != null && alt < MIN_SLEW_ALT_DEG) {
      this.tripBelowHorizon();
      return;
    }
    void this.postMove(this.curAxis, this.signedRate(this.curAxis, this.curDir));
  }

  private tripBelowHorizon(): void {
    // forceStop first (posts 0, clears keepalive) THEN latch the trip state so the
    // UI flashes "below horizon limit." forceStop resets mode to idle, so we set
    // belowHorizon after and emit once.
    this.forceStop();
    this.mode = "belowHorizon";
    this.emit();
  }

  // --------------------------------------------------------------- tap (nudge)
  // One fixed move: a pulse-guide-sized nudge (pulse rate) or, in NINA mode, a
  // small relative GOTO. The component's postNudge closure encodes which.
  tapNudge(axis: Axis, dir: Dir): void {
    // clear a latched below-horizon flash on a fresh deliberate action
    if (this.mode === "belowHorizon") {
      this.mode = "idle";
      this.emit();
    }
    haptics.tap();
    Promise.resolve(this.o.postNudge(axis, dir)).catch((e) => this.fail(e));
  }

  // --------------------------------------------------------------- panic
  // External stop: lock engaged, window blur/visibility-hidden, move POST failure.
  // Idempotent — posts 0 for whatever axis was last commanded (and clears state).
  forceStop(): void {
    const axis = this.curAxis;
    const wasHolding = this.holding;
    this.holding = false;
    this.disarmKeepalive();
    if (axis) {
      void this.postMove(axis, 0);
    }
    this.curAxis = null;
    this.curDir = null;
    this.mode = "idle";
    if (wasHolding) haptics.stop();
    this.emit();
  }

  // Wrap postMove so a failed command never leaves the mount "thinking it moves":
  // on error we forceStop (post 0) and surface via onError (toast + debounced buzz).
  private async postMove(axis: Axis, rate: number): Promise<void> {
    try {
      await this.o.postMove(axis, rate);
    } catch (e) {
      // Avoid recursion: only escalate to a stop if this was a non-zero command.
      if (rate !== 0) {
        this.holding = false;
        this.disarmKeepalive();
        this.curAxis = null;
        this.curDir = null;
        this.mode = "idle";
        // THE ONE EXIT THAT USED TO GO QUIET. Every other transition out of a
        // hold — stopHold, forceStop, tripBelowHorizon — ends in emit(), which
        // is the only thing that repaints the pad. This one did not, so a move
        // POST that failed left the arrow lit and the feedback line reading
        // "HOLD · 0.50°/s" over a mount that had already stopped: the error
        // toast said one thing and the control said another. Emitted BEFORE the
        // best-effort zero so the pad tells the truth even if that POST is lost
        // as well.
        this.emit();
        try {
          await this.o.postMove(axis, 0);
        } catch {
          /* best-effort stop */
        }
      }
      this.fail(e);
    }
  }
}

/** Convenience: look up a rate option by id (UI segmented control). */
export function rateById(id: SlewRateOption["id"]): SlewRateOption {
  return SLEW_RATES.find((r) => r.id === id) ?? SLEW_RATES[0];
}

/** Speed glyph (R21 — encode by shape, never color): pulse=▰ fine=▰▰ set=▰▰▰. */
export function rateGlyph(id: SlewRateOption["id"]): string {
  return id === "pulse" ? "▰" : id === "fine" ? "▰▰" : "▰▰▰";
}
