// verdict.test.ts - the Conditions headline, against seeded forecast series.
//
//   Run directly:  npx tsx src/next/hubs/weather/__tests__/verdict.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT EACH CASE GUARDS, and what goes wrong without it:
//
//  * The four verdict branches in their priority order. The override has to beat
//    a standing alert, or a rig imaging deliberately through cloud reads as one
//    that is about to stop.
//  * A BREACH OUTSIDE THE DARK WINDOW IS NOT TONIGHT'S PROBLEM. The series runs
//    24 h, so tomorrow lunchtime is in it; without the dark-window overlap test
//    a clear night reads "GOOD UNTIL 13:40" and an operator plans around cloud
//    that arrives after sunrise.
//  * The three empty states are distinct. "off", "no forecast yet" and "nothing
//    in this window" have three different next actions, and the pre-fix panel
//    rendered two of them identically.
//  * STALE NEVER READS AS LIVE. `normalizeWeather` is fail-closed at 45 min; the
//    verdict has to carry that forward or a 6-hour-old forecast is shown as a
//    verdict about tonight.
//  * The meteorological turnaround. `drift(225)` must be SW -> NE.

import { deriveVerdict, drift, compass, windowSamples } from "../conditions/verdict";
import type { WeatherAlert, WeatherState } from "../../../../types";

let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg = ""): void {
  if (got !== want) throw new Error(`${msg} expected ${String(want)}, got ${String(got)}`);
}

const NOW = 1_757_000_000;           // a fixed instant; every case is relative to it
const STEP = 900;                    // the Open-Meteo 15-minute grid
const N = 96;                        // 24 h of it

function iso(ts: number): string { return new Date(ts * 1000).toISOString(); }

/** A weather payload with a 24 h series; `cloudAt` decides each sample. */
function wx(cloudAt: (i: number) => number, over: Partial<WeatherState> = {}): WeatherState {
  const times: string[] = [];
  const cloud: number[] = [];
  for (let i = 0; i < N; i++) {
    times.push(iso(NOW + i * STEP));
    cloud.push(cloudAt(i));
  }
  return {
    enabled: true,
    fetched_ts: NOW - 120,
    stale: false,
    ignore_tonight: false,
    threshold_pct: 50,
    sustain_minutes: 30,
    site_lat: null,
    site_lon: null,
    forecast: {
      times,
      cloud,
      cloud_low: cloud.map(() => 0),
      cloud_mid: cloud.map(() => 0),
      cloud_high: cloud.map(() => 0),
    },
    astrospheric: null,
    alert: null,
    ...over,
  };
}

// dark 02:00 -> 10:00 after NOW, in the middle of the series
const DARK = { start_iso: iso(NOW + 2 * 3600), end_iso: iso(NOW + 10 * 3600) };
const CLEAR = () => 5;
/** A sustained breach from +5 h to +7 h - inside the dark window. */
const NIGHT_BREACH = (i: number) => (i >= 20 && i <= 28 ? 80 : 5);
/** A sustained breach from +20 h to +22 h - after the dark window closes. */
const DAY_BREACH = (i: number) => (i >= 80 && i <= 88 ? 80 : 5);

function verdict(w: WeatherState | null, dark = DARK as { start_iso: string; end_iso: string } | null) {
  const win = w ? windowSamples(w, NOW) : null;
  return deriveVerdict({ weather: w, win, dark, darkNote: dark ? null : "no dark window", nowTs: NOW });
}

// ------------------------------------------------------------- empty states

test("no weather slice at all reads as off", () => {
  eq(verdict(null).kind, "off");
});

test("weather disabled names the sheet that turns it on", () => {
  const v = verdict(wx(CLEAR, { enabled: false }));
  eq(v.kind, "off");
  assert(/Turn it on in Weather settings\./.test(v.sub), `off copy: got "${v.sub}"`);
  assert(!/Settings . Connect/.test(v.sub), "the off copy still names a route this IA does not have");
});

test("enabled with no forecast is WAITING, not GOOD", () => {
  const v = verdict(wx(CLEAR, { forecast: null }));
  eq(v.kind, "waiting");
  assert(/waiting for first forecast/.test(v.sub), `waiting copy: got "${v.sub}"`);
});

test("a forecast that misses the window is its own state", () => {
  // every sample two days old: present, but nothing in [now-450s, now+24h]
  const stale = wx(CLEAR);
  stale.forecast!.times = stale.forecast!.times.map((_, i) => iso(NOW - 48 * 3600 + i * STEP));
  const v = verdict(stale);
  eq(v.kind, "nowindow");
  assert(/no forecast data for the current window/.test(v.sub), `window copy: got "${v.sub}"`);
});

// ---------------------------------------------------------------- verdicts

test("a clear night is GOOD ALL NIGHT and says how much cloud there is now", () => {
  const v = verdict(wx(CLEAR));
  eq(v.kind, "goodAllNight");
  eq(v.headline, "GOOD ALL NIGHT");
  eq(v.tone, "good");
  assert(/5% now/.test(v.sub), `sub should carry the current cover, got "${v.sub}"`);
});

test("a sustained breach inside the dark window is GOOD UNTIL that hour", () => {
  const v = verdict(wx(NIGHT_BREACH));
  eq(v.kind, "goodUntil");
  assert(/^GOOD UNTIL \d\d:\d\d$/.test(v.headline), `headline: got "${v.headline}"`);
  eq(v.tone, "warn");
  assert(/high cloud \d\d:\d\d - \d\d:\d\d/.test(v.sub), `sub should span the breach, got "${v.sub}"`);
  // The engine's cloud gate is gone; the sub-line must not promise a hold.
  assert(!/hold armed/.test(v.sub), "the sub-line promises a hold the engine does not arm");
  assert(/frames show/.test(v.sub), `sub should say what actually decides, got "${v.sub}"`);
});

test("a breach OUTSIDE the dark window leaves the night good", () => {
  const v = verdict(wx(DAY_BREACH));
  eq(v.kind, "goodAllNight", "cloud after sunrise is not tonight's problem:");
});

test("without a dark window the verdict says it is reading the whole 24 h", () => {
  const v = verdict(wx(CLEAR), null);
  eq(v.kind, "goodAllNight");
  eq(v.headline, "NO CLOUD BREACH IN 24 H");
  assert(/no dark window/.test(v.sub), `the reason must be carried, got "${v.sub}"`);
});

test("a server alert outranks a computed breach", () => {
  const alert: WeatherAlert = {
    kind: "high_cloud",
    start_iso: iso(NOW + 5 * 3600),
    end_iso: iso(NOW + 7 * 3600),
    peak_pct: 82,
    dominant_layer: "high",
  };
  const v = verdict(wx(NIGHT_BREACH, { alert }));
  eq(v.kind, "alert");
  eq(v.headline, "HIGH CLOUD TONIGHT");
  eq(v.tone, "bad");
  assert(/peak 82% \(high layer\)/.test(v.sub), `sub: got "${v.sub}"`);
  assert(/at or above your 50% threshold/.test(v.sub), `sub: got "${v.sub}"`);
});

test("the override outranks everything, and names what it actually disarms", () => {
  const alert: WeatherAlert = {
    kind: "high_cloud", start_iso: iso(NOW), end_iso: iso(NOW + 3600),
    peak_pct: 90, dominant_layer: "mid",
  };
  const v = verdict(wx(NIGHT_BREACH, { alert, ignore_tonight: true }));
  eq(v.kind, "override");
  eq(v.headline, "OVERRIDE · IMAGING THROUGH CLOUD");
  // Verified against weather.py:721 (`_ignore_active` short-circuits the RAIN
  // veto) and :632-639 (keyed to tonight's dusk, so it expires at the next one).
  assert(/rain veto disarmed until the next dusk/.test(v.sub), `override copy: got "${v.sub}"`);
  assert(/safety monitor still stops the run/.test(v.sub), `override copy: got "${v.sub}"`);
});

test("a stale forecast is dimmed and led by its age", () => {
  const v = verdict(wx(CLEAR, { stale: true, fetched_ts: NOW - 6 * 3600 }));
  eq(v.tone, "dim", "a stale forecast must not be shown in a live tone:");
  assert(/^STALE/.test(v.sub), `the age must lead the sub-line, got "${v.sub}"`);
});

// ------------------------------------------------------------------ window

test("windowSamples clips to [now-450s, now+24h]", () => {
  const w = wx(CLEAR);
  w.forecast!.times = w.forecast!.times.map((_, i) => iso(NOW - 3600 + i * STEP));
  const win = windowSamples(w, NOW);
  assert(win.ts.length > 0, "the window is empty - the fixture is wrong, not the code");
  assert(win.ts[0] >= NOW - 450, `first sample ${win.ts[0] - NOW}s from now is behind the clip`);
  assert(win.ts[win.ts.length - 1] <= NOW + 24 * 3600, "a sample past the 24 h horizon survived");
});

// ----------------------------------------------------------------- compass

test("the wind is turned around: 225 (from SW) drifts to NE", () => {
  const d = drift(225);
  eq(d.from, "SW", "meteorological end:");
  eq(d.toward, "NE", "drift end:");
  eq(d.towardDeg, 45, "drift bearing:");
});

test("the turnaround wraps both ways", () => {
  eq(drift(0).toward, "S");
  eq(drift(0).towardDeg, 180);
  eq(drift(350).towardDeg, 170);
  eq(compass(359), "N");
  eq(compass(90), "E");
});

console.log(`verdict.test: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}

const total = passed + failed;
export default { passed, failed, total };
export { passed, failed, total };
