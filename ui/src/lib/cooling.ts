// cooling.ts — how a camera warm-down ramp reads on screen.
//
// Pure formatting for `status.camera.warm` (server: astrodeck/cooling.py +
// Hub.warm_camera). No React, no DOM — unit-tested in cooling.test.ts.
//
// WHY THIS EXISTS AT ALL. Until 2026-08-04 "Warm" was one call that switched the
// TEC off, and the panel had nothing to say afterwards because there was nothing
// to say: it was over before the button finished animating. The fix makes
// warming take ten minutes, and a button that appears to do nothing for ten
// minutes is its own bug report — the user presses it again, or presses Cool, or
// concludes the build is broken. So the ramp has to be VISIBLE: what it is
// doing, how far it has to go, and (the part that matters most) whether a ramp
// is running at all or the server quietly fell back to cutting the cooler dead.

import type { WarmInfo } from "../types";
import { fmtDuration } from "./eta";

export interface WarmReadout {
  /** 0..100 along the ramp. Drives the bar and aria-valuenow. */
  pct: number;
  /** Lead line: what is happening. */
  headline: string;
  /** Second line: the numbers behind it. Empty when there are none worth showing. */
  detail: string;
  /** True while the ramp is still running (the panel swaps Warm for Stop). */
  active: boolean;
  /** True when the cooler was switched off WITHOUT a ramp. The panel styles this
   *  as a warning, because it is the old behaviour and the user is entitled to
   *  know their camera just took the fast way down. */
  unramped: boolean;
}

/** Round for display without pretending to precision the sensor does not have. */
const c = (n: number | null | undefined): string =>
  n == null ? "—" : `${n.toFixed(1)} °C`;

export function warmReadout(warm: WarmInfo | null | undefined): WarmReadout | null {
  if (!warm) return null;

  // ---- finished (the server keeps this on status for ~3 min, on purpose) ----
  if (!warm.active) {
    const unramped = warm.ramped === false;
    return {
      pct: 100,
      // "Cooler off" and "Warm complete" are different claims and must not look
      // alike: one of them means the sensor was walked down over ten minutes,
      // the other means it was cut loose to equalise with the room.
      headline: unramped ? "Cooler off — no ramp" : "Warm complete",
      detail: warm.note ?? "",
      active: false,
      unramped,
    };
  }

  const start = warm.start_c;
  const ambient = warm.ambient_c;
  const setpoint = warm.setpoint_c;
  const eta = warm.eta_s;

  // Progress. For a hub-driven ramp the honest measure is how far the SETPOINT
  // has climbed (that is the thing we control); for a backend that owns its own
  // ramp — NINA — all we have is the clock, so say so with the same bar rather
  // than inventing a setpoint we are not the source of.
  let pct = 0;
  if (warm.delegated) {
    const elapsed = warm.elapsed_s ?? 0;
    const total = elapsed + Math.max(0, eta ?? 0);
    pct = total > 0 ? (elapsed / total) * 100 : 0;
  } else if (start != null && ambient != null && setpoint != null && ambient > start) {
    pct = ((setpoint - start) / (ambient - start)) * 100;
  }
  pct = Math.max(0, Math.min(100, Math.round(pct)));

  const left = eta != null && eta > 0 ? `about ${fmtDuration(eta)} left` : "finishing";
  const headline = `Warming — ${left}`;

  const bits: string[] = [];
  if (warm.delegated) {
    // The camera's own ramp: naming it prevents the obvious support question
    // ("why is the setpoint not moving on my ASCOM readout?").
    bits.push("the camera is running its own ramp");
  } else if (setpoint != null) {
    bits.push(`setpoint ${c(setpoint)}`);
  }
  if (warm.temp_c != null) bits.push(`sensor ${c(warm.temp_c)}`);
  if (ambient != null) {
    // The provenance rides along because "to 20 °C (assumed)" and "to 11 °C
    // (measured)" are different promises. Assumed is the common case and the
    // ramp ends early on its own when the sensor stops following — but a user
    // watching a setpoint head for 20 °C on a 4 °C night deserves to know that
    // number was a guess, not a reading.
    const from = warm.ambient_from === "assumed" ? " (assumed)"
      : warm.ambient_from === "measured" ? " (measured)"
        : "";
    bits.push(`to ${c(ambient)}${from}`);
  }
  if (warm.rate_c_per_min != null) bits.push(`${warm.rate_c_per_min} °C/min`);

  return { pct, headline, detail: bits.join(" · "), active: true, unramped: false };
}
