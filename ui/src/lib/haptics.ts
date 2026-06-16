// haptics.ts — additive, leaf vibration wrappers (touch spec §7, R26).
//
// Design rules baked in here:
//  - PURELY ADDITIVE. The numeric °/s + large label is the PRIMARY feedback; a
//    buzz is a bonus a sighted user never depends on. Every method no-ops when
//    unsupported, so callers never branch on `navigator.vibrate`.
//  - `supported` is computed once (iOS/iPad Safari have NO `navigator.vibrate` —
//    the More-sheet toggle reads this to HIDE itself rather than ship a dead
//    control on the field-dominant device).
//  - `error()` is DEBOUNCED (>=1.5s) so a flaky-network night can't buzz like a
//    constant alarm (alarm fatigue is worse than no haptic).
//  - NO buzz on rate-selector change (the caller simply doesn't call us there) —
//    a band change is a deliberate visual choice, not an event.
//
// Mirrored to `haptics.enabled` from the store's `touch.hapticsEnabled` (the
// store's `setTouch` writes `haptics.enabled = patch.hapticsEnabled`).

export type Pattern = "tap" | "start" | "stop" | "confirm" | "warn" | "error";

// Millisecond patterns. Leading 0 = "wait 0ms then vibrate" — keeps the array
// form (vibrate(number[]) is wider-supported than the scalar on some engines).
const PATTERNS: Record<Pattern, number | number[]> = {
  tap: 10,
  start: [0, 20],
  stop: [0, 12, 40, 12],
  confirm: [0, 15, 30, 15],
  warn: [0, 30],
  error: [0, 50, 40, 50],
};

const ERROR_DEBOUNCE_MS = 1500;

function detectSupported(): boolean {
  return typeof navigator !== "undefined" && typeof navigator.vibrate === "function";
}

export interface Haptics {
  /** Master gate, mirrored from store.touch.hapticsEnabled. */
  enabled: boolean;
  /** Capability flag — false on iOS/iPad (the More-sheet hides the toggle there).
   *  Computed once at load from `navigator.vibrate`; left writable purely as a
   *  test seam (app code never assigns it). */
  supported: boolean;
  /** Fire a named pattern (no-op when disabled/unsupported; error is debounced). */
  fire(p: Pattern): void;
  tap(): void;
  start(): void;
  stop(): void;
  confirm(): void;
  warn(): void;
  error(): void;
}

let lastError = 0;

export const haptics: Haptics = {
  enabled: true,
  supported: detectSupported(),

  fire(p) {
    if (!this.enabled || !this.supported) return;
    if (p === "error") {
      const now = Date.now();
      if (now - lastError < ERROR_DEBOUNCE_MS) return; // debounce (R26)
      lastError = now;
    }
    try {
      navigator.vibrate(PATTERNS[p]);
    } catch {
      /* some embedded WebViews throw on vibrate; never let a buzz crash UI */
    }
  },

  tap() { this.fire("tap"); },
  start() { this.fire("start"); },
  stop() { this.fire("stop"); },
  confirm() { this.fire("confirm"); },
  warn() { this.fire("warn"); },
  error() { this.fire("error"); },
};

/** Test seam — reset the error debounce clock between cases. Not used by app code. */
export function __resetHapticsDebounce(): void {
  lastError = 0;
}
