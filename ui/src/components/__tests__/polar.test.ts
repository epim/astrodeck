// Polar (HERO 2) pure-logic regression — the verdict tier + knob-direction
// decode that the TPPA wizard binds to. Same inline-assert / `tsx` style as the
// other suites (no jsdom, no runtime timers): these are the pure functions the
// reticle and the Align view share, so a binding regression fails loudly here.
//
// Run directly:  npx tsx src/components/__tests__/polar.test.ts
// Also type-checked by `tsc -b` in the build.

import { polarTier, knobHint } from "../polar";

function eq<T>(a: T, b: T, msg: string): void {
  if (a !== b) throw new Error(`${msg}: expected ${String(b)}, got ${String(a)}`);
}
function isNull(a: unknown, msg: string): void {
  if (a !== null) throw new Error(`${msg}: expected null, got ${JSON.stringify(a)}`);
}

// --- verdict tiers: <2′ excellent · 2–10′ good · >10′ keep going ------------
eq(polarTier(0), "excellent", "0′ excellent");
eq(polarTier(1.99), "excellent", "1.99′ excellent");
eq(polarTier(2), "good", "2′ good (boundary is good, not excellent)");
eq(polarTier(9.99), "good", "9.99′ good");
eq(polarTier(10), "keepgoing", "10′ keep going (boundary)");
eq(polarTier(45), "keepgoing", "45′ keep going");

// --- azimuth knob labels (authoritative, from the native engine) ------------
eq(knobHint("left_west", 0.7, "az")?.arrow, "◀", "left_west arrow");
eq(knobHint("left_west", 0.7, "az")?.text, "turn W", "left_west text");
eq(knobHint("left_east", 0.7, "az")?.text, "turn E", "left_east text");
eq(knobHint("right_west", 0.7, "az")?.arrow, "▶", "right_west arrow");
eq(knobHint("right_east", 0.7, "az")?.text, "turn E", "right_east text");
// wrong-axis label must be rejected, not mis-rendered
isNull(knobHint("up", 0.7, "az"), "alt label on az axis rejected");

// --- azimuth fallback (NINA/sim emit no knob label) -------------------------
eq(knobHint(null, -3, "az")?.arrow, "◀", "az<0 fallback arrow");
eq(knobHint(null, -3, "az")?.text, "turn E", "az<0 fallback text");
eq(knobHint(null, 3, "az")?.text, "turn W", "az>0 fallback text");
isNull(knobHint(null, 0, "az"), "az=0 fallback null");
isNull(knobHint(undefined, NaN, "az"), "az NaN fallback null");

// --- altitude knob labels + fallback ----------------------------------------
eq(knobHint("up", 0.6, "alt")?.arrow, "▲", "up arrow");
eq(knobHint("up", 0.6, "alt")?.text, "raise", "up text");
eq(knobHint("down", 0.6, "alt")?.arrow, "▼", "down arrow");
eq(knobHint("down", 0.6, "alt")?.text, "lower", "down text");
isNull(knobHint("left_west", 0.6, "alt"), "az label on alt axis rejected");
eq(knobHint(null, 4, "alt")?.text, "lower", "alt>0 fallback lower");
eq(knobHint(null, -4, "alt")?.text, "raise", "alt<0 fallback raise");
isNull(knobHint(null, 0, "alt"), "alt=0 fallback null");

// eslint-disable-next-line no-console
console.log("polar.test.ts OK");
