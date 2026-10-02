// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// vitalsBandTruths.test.tsx - `dawnFace` (the TO DAWN vitals tile) must not
// state a fact about the sky it cannot compute.
//
//   Run directly:  npx tsx src/next/hubs/monitor/live/__tests__/vitalsBandTruths.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc`.
//
// Two truths pinned here (plan WP-75, backlog ruling D-nn owner-approved
// 2026-09-30 is not needed - the fix shape is dictated by the issues
// themselves, #633 and #640):
//
//  1. #633 (the #24 is_default class): with no site saved, the tile must say
//     so and point at the site pill - never compute darkness from the
//     placeholder (0,0) site. Named mutant: the is_default branch removed.
//  2. #640: `dark_window` is always the NEXT dark interval from "now" (server
//     `coords.dark_window` docstring), so outside darkness the window named is
//     tomorrow's, up to ~24h away. Counting down to its END reads as "dark
//     now, until dawn tomorrow" when it is not dark at all. Named mutant: the
//     not-dark branch removed.
//
// All times below are built from a made-up site and a made-up clock (no real
// coordinates, per project rule); `dawnFace` never reads lat/lon anyway - it
// takes a pre-computed window plus "now".

// ------------------------------------------------------------ browser stubs
// `VitalsBand.tsx` imports the store (for `useSite`/`useCamera`/etc.), and the
// store seeds its initial state from `localStorage` at module load
// (`loadPlan()`); `api.ts` reads `window.location.pathname` at module load too
// (`lib/base.ts`'s `BASE`). Same minimal stub set as
// `ui/src/__tests__/storeSiteMirror.test.ts` - no jsdom, because nothing here
// renders a DOM, it only calls a pure function.
class MemStorage {
  private m = new Map<string, string>();
  getItem(k: string): string | null { return this.m.has(k) ? (this.m.get(k) as string) : null; }
  setItem(k: string, v: string): void { this.m.set(k, String(v)); }
  removeItem(k: string): void { this.m.delete(k); }
  clear(): void { this.m.clear(); }
}
const g = globalThis as unknown as {
  localStorage?: Storage; document?: unknown; window?: unknown; fetch?: unknown;
};
if (typeof g.localStorage === "undefined") {
  g.localStorage = new MemStorage() as unknown as Storage;
}
if (typeof g.window === "undefined") {
  g.window = { location: { pathname: "/", host: "localhost", protocol: "http:" } };
}
if (typeof g.document === "undefined") {
  const classList = {
    toggle(_c: string, _on?: boolean): void {},
    add(_c: string): void {},
    remove(_c: string): void {},
    contains(_c: string): boolean { return false; },
  };
  const style = {
    setProperty(_k: string, _v: string): void {},
    getPropertyValue(_k: string): string { return ""; },
  };
  g.document = { documentElement: { classList, style } };
}
g.fetch = async (): Promise<unknown> => ({ ok: true, status: 200, json: async () => ({}) });

const { dawnFace } = await import("../VitalsBand");
const { fmtClock, fmtDuration } = await import("../../../../lib/format");

// ---------------------------------------------------------------- harness
let passed = 0, failed = 0; const failures: string[] = [];
function test(n: string, fn: () => void) {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${n}: ${(e as Error).message}`); }
}
function eq<T>(a: T, b: T, m = ""): void { if (a !== b) throw new Error(`${m} expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`); }
function assert(cond: boolean, m: string): void { if (!cond) throw new Error(m); }

// A local-time instant, used both to BUILD the fixture windows and to label
// the expected clock strings - `fmtClock` reads local hours/minutes, so this
// stays correct under whatever timezone CI runs in.
const at = (y: number, mo: number, d: number, h: number, mi = 0): number =>
  new Date(y, mo, d, h, mi, 0).getTime();

// A window well into a made-up June night, used as the "dark exists" fixture
// across several cases.
const TONIGHT = { start_iso: new Date(at(2026, 5, 14, 22, 0)).toISOString(),
                   end_iso: new Date(at(2026, 5, 15, 5, 30)).toISOString() };

// ------------------------------------------------------- #633: no site saved
test("#633: a default (unset) site says so, and never reports a dark window", () => {
  const face = dawnFace(TONIGHT, true, at(2026, 5, 15, 2, 0), /* siteIsDefault */ true);
  eq(face.value, "no site set");
  assert(/site pill/.test(face.sub), `the reason must point at the site pill: "${face.sub}"`);
  assert(!/dark/i.test(face.value), `the value still named a dark window: "${face.value}"`);
});

test("#633: a default site with NO dark window reported still says the site is missing, not that there is no dark tonight", () => {
  const face = dawnFace(null, true, at(2026, 5, 15, 2, 0), true);
  eq(face.value, "no site set");
});

test("#633 control: a saved (non-default) site with the same window still computes darkness normally", () => {
  const nowMs = at(2026, 5, 15, 2, 0);
  const face = dawnFace(TONIGHT, true, nowMs, /* siteIsDefault */ false);
  eq(face.value, fmtDuration((Date.parse(TONIGHT.end_iso) - nowMs) / 1000));
  assert(/dark until/.test(face.sub), `a real site lost its darkness readout: "${face.sub}"`);
});

test("#633 control: a saved site with genuinely no darkness tonight (high-latitude summer) keeps its own true answer", () => {
  const face = dawnFace(null, true, at(2026, 5, 15, 2, 0), /* siteIsDefault */ false);
  eq(face.value, "no dark tonight");
});

test("#633: the capability gate still comes before the site check (withheld beats unset)", () => {
  const face = dawnFace(TONIGHT, /* canSiteDerived */ false, at(2026, 5, 15, 2, 0), /* siteIsDefault */ true);
  eq(face.value, "--");
  assert(/needs /.test(face.sub), `a withheld tile stopped naming the capability: "${face.sub}"`);
});

// --------------------------------------------- #640: outside darkness truths
//
// `dark` below is always the NEXT dark interval relative to each case's "now"
// (mirroring what `coords.dark_window` actually returns), never "tonight's"
// window re-used verbatim - that is the bug.

test("#640: mid-night, actually dark now - a countdown to dawn, unchanged", () => {
  const nowMs = at(2026, 5, 15, 2, 0); // between TONIGHT's start and end
  const face = dawnFace(TONIGHT, true, nowMs, false);
  const endMs = Date.parse(TONIGHT.end_iso);
  eq(face.value, fmtDuration((endMs - nowMs) / 1000), "still a countdown while it is dark");
  eq(face.sub, `dark until ${fmtClock(endMs, nowMs)}`);
});

test("#640: just after dawn began - says when tonight's darkness begins, not a countdown to tomorrow's dawn", () => {
  const nowMs = at(2026, 5, 15, 5, 35); // 5 min after TONIGHT's own dark ended
  // The next dark interval the server would report from here: THIS evening,
  // ending the following morning - the exact shape of the reported bug (a
  // window that has not started is treated as already running).
  const next = { start_iso: new Date(at(2026, 5, 15, 21, 0)).toISOString(),
                 end_iso: new Date(at(2026, 5, 16, 4, 45)).toISOString() };
  const face = dawnFace(next, true, nowMs, false);
  assert(!/^\d/.test(face.value), `the value is still a countdown, not a state: "${face.value}"`);
  eq(face.sub, `dark from ${fmtClock(Date.parse(next.start_iso), nowMs)}`);
  eq(face.sub, "dark from 21:00", "same calendar day as now - no (+1d)");
  assert(!/dark until/.test(face.sub), `still counting to the END of a window that has not started: "${face.sub}"`);
});

test("#640: mid-afternoon, hours before dusk - the same not-dark-yet shape, not special-cased to just after dawn", () => {
  const nowMs = at(2026, 5, 15, 14, 0);
  const next = { start_iso: new Date(at(2026, 5, 15, 20, 45)).toISOString(),
                 end_iso: new Date(at(2026, 5, 16, 5, 10)).toISOString() };
  const face = dawnFace(next, true, nowMs, false);
  eq(face.value, "not dark yet");
  eq(face.sub, "dark from 20:45");
});

test("#640: a window starting just after local midnight gets the (+1d) marker on when darkness begins", () => {
  const nowMs = at(2026, 5, 15, 23, 50);
  const next = { start_iso: new Date(at(2026, 5, 16, 0, 15)).toISOString(),
                 end_iso: new Date(at(2026, 5, 16, 6, 0)).toISOString() };
  const face = dawnFace(next, true, nowMs, false);
  eq(face.sub, "dark from 00:15 (+1d)");
});

test("#640 reproduction: the exact reported shape (23h59m, dark until HH:MM (+1d)) must not appear outside darkness", () => {
  // Minutes after dawn twilight began; the server's next window is ~24h out.
  const nowMs = at(2026, 5, 15, 5, 22);
  const next = { start_iso: new Date(at(2026, 5, 15, 19, 20)).toISOString(),
                 end_iso: new Date(at(2026, 5, 16, 11, 20)).toISOString() };
  const face = dawnFace(next, true, nowMs, false);
  assert(!/23h\s*59m/.test(face.value), `reproduced the reported countdown: "${face.value}"`);
  assert(!/dark until/.test(face.sub), `reproduced the reported "dark until ... (+1d)" claim: "${face.sub}"`);
});

// ---------------------------------------------------------------- report
console.log(`${passed} passed, ${failed} failed`);
if (failed) {
  for (const f of failures) console.error(f);
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
export { passed, failed };
export const total = passed + failed;
