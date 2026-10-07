// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w15SafetyDeadman.test.tsx - the SAFETY sheet's dead-man row says PINGING only
// for a monitor that ACCEPTED a ping lately (#125, found by wave 14's verifier).
//
//   Run directly:  npx tsx src/next/hubs/rig/__tests__/w15SafetyDeadman.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT THIS IS ABOUT. `deadmanWord` / `deadmanTone` / `deadmanLine` read
// `health.deadman.healthy` alone, and `healthy` only means "no failure has been
// WARNED about yet", which is also true before the first request leaves, so a
// pasted typo read PINGING, in green, until its first attempt failed. Wave 14
// taught `lib/alertSinks.ts`'s `deadmanVerdict` to read `last_ok_age_s`, the
// seconds since the monitor last ACCEPTED a ping; the Monitor hub's pill and the
// legacy Settings panel use it. This row did not, so the same rig read Waiting
// on two screens and PINGING on the third. The three functions now derive from
// the one verdict, and the last block pins that they cannot drift from it.
//
// Convention: the functions are exported from the sheet for this test, which
// needs the sheet's own import chain (a stylesheet, the store), hence the css
// hook and jsdom globals below, the same ones rigSafetyDom.test.tsx installs.
//
// MUTANTS RUN, each from a byte backup restored byte-identically (sha256
// compared, the mutant text grepped out), and what each printed:
//
//   M1, deadmanTone reads `healthy` alone again (`dm.healthy ? "good" : "bad"`).
//   4 of 8 failed, the first: "an unconfirmed monitor is toned as if confirmed
//   (expected dim, got good)".
//
//   M2, deadmanWord returns "PINGING" for any healthy monitor. 5 of 8 failed,
//   the first: "an absent last_ok_age_s read as a live monitor (expected
//   WAITING, got PINGING)".
//
//   M3, deadmanLine prints `last_ping_age_s` (the last ATTEMPT) as the age,
//   which is the line this sheet had. 5 of 8 failed, the first: "the row
//   claims a ping nothing accepted (expected configured · no ping accepted
//   yet, got configured · healthy · last ping 42s ago)".
//
// Before the change, with the functions merely exported: 6 of 8 failed, the
// first "an absent last_ok_age_s read as a live monitor (expected WAITING, got
// PINGING)".

/* eslint-disable @typescript-eslint/no-explicit-any */

import type { AlertHealth } from "../../../../types";

{
  const { registerHooks } = await import("node:module");
  registerHooks({
    load(url: string, context: any, nextLoad: any) {
      if (url.endsWith(".css")) {
        return { format: "module", shortCircuit: true, source: "export default {};" };
      }
      return nextLoad(url, context);
    },
  } as any);
}

const { JSDOM } = await import("jsdom");
const dom = new JSDOM(`<!doctype html><html><body></body></html>`,
  { url: "http://local/#/rig/devices/safety", pretendToBeVisual: true });
const win = dom.window as any;
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "localStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}

const { deadmanWord, deadmanTone, deadmanLine } = await import("../sheets/safety");
const { deadmanVerdict } = await import("../../../../lib/alertSinks");
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}

type Dm = AlertHealth["deadman"];
const health = (dm: Partial<Dm>): AlertHealth => ({
  undelivered: 0,
  undelivered_by_sink: {},
  deadman: { configured: true, healthy: true, last_ping_age_s: 5, ...dm },
});

test("the precondition: the three functions are the sheet's own and they exist", () => {
  // Vacuity guard: a rename that left them undefined would fail every case
  // below with a TypeError, but this names the cause.
  eq(typeof deadmanWord, "function", "deadmanWord is not exported");
  eq(typeof deadmanTone, "function", "deadmanTone is not exported");
  eq(typeof deadmanLine, "function", "deadmanLine is not exported");
});

test("healthy alone is NOT pinging: a pasted typo that has not failed yet reads Waiting", () => {
  // The bug: `healthy: true` with no accepted ping known, which is what a
  // server older than `last_ok_age_s` sends, and what a fresh paste looks like.
  const h = health({ healthy: true, last_ping_age_s: 42 });          // field absent
  eq(deadmanWord(h), "WAITING", "an absent last_ok_age_s read as a live monitor");
  eq(deadmanTone(h), "dim", "an unconfirmed monitor is toned as if confirmed");
  eq(deadmanLine(h), "configured · no ping accepted yet", "the row claims a ping nothing accepted");
});

test("a monitor that never accepted a ping is Waiting, whatever else the health says", () => {
  for (const age of [null, undefined, -1, Number.NaN, Number.POSITIVE_INFINITY]) {
    const h = health({ healthy: true, last_ping_age_s: 3, last_ok_age_s: age as number | null });
    eq(deadmanWord(h), "WAITING", `last_ok_age_s=${String(age)}`);
    eq(deadmanTone(h), "dim", `last_ok_age_s=${String(age)}`);
  }
});

test("a recent accepted ping is PINGING, in good tone, and the line says how long ago", () => {
  const h = health({ healthy: true, last_ping_age_s: 5, last_ok_age_s: 42 });
  eq(deadmanWord(h), "PINGING", "an accepted ping did not read PINGING");
  eq(deadmanTone(h), "good", "an accepted ping is not good");
  eq(deadmanLine(h), "configured · last ping accepted 42s ago", "the line does not carry the accepted age");
});

test("an accepted ping that is old is STALE and not good", () => {
  // The server pings every 60 s; three missed is where an outside monitor's own
  // grace has run out. 179 s is still fine, 180 s is not.
  eq(deadmanWord(health({ last_ok_age_s: 179 })), "PINGING", "179 s is inside three missed pings");
  const stale = health({ last_ok_age_s: 180 });
  eq(deadmanWord(stale), "STALE", "180 s of silence still reads PINGING");
  eq(deadmanTone(stale), "warn", "a stale monitor is not warned about");
  eq(deadmanLine(stale), "configured · last ping accepted 180s ago", "the stale line lost its age");
  eq(deadmanWord(health({ last_ok_age_s: 4000 })), "STALE", "an hour of silence reads PINGING");
});

test("a failure that was warned about wins over an old accepted ping", () => {
  const h = health({ healthy: false, last_ok_age_s: 10 });
  eq(deadmanWord(h), "UNREACHABLE", "a warned-about monitor reads as pinging");
  eq(deadmanTone(h), "bad", "an unreachable monitor is not bad");
  eq(deadmanLine(h), "configured · monitor URL is not being reached - check it",
    "the unreachable line does not say what to do");
});

test("not configured says so, and nothing outside the rig is checking it", () => {
  const h = health({ configured: false, healthy: false, last_ok_age_s: 10 });
  eq(deadmanWord(h), "NOT SET", "an absent monitor has a verdict word");
  eq(deadmanTone(h), "dim", "an absent monitor is toned");
  eq(deadmanLine(h), "not configured - nothing outside this rig is checking it is alive",
    "the not-configured sentence changed");
  eq(deadmanWord(null), "NOT SET", "no health at all is not 'not set'");
  eq(deadmanTone(null), "dim", "no health at all is toned");
});

test("the row and the Monitor hub cannot disagree: all three derive from deadmanVerdict", () => {
  // Every shape the server can send, crossed with every age that matters.
  const ages: Array<number | null | undefined> = [undefined, null, -3, 0, 1, 59, 179, 180, 181, 3600, Number.NaN];
  for (const configured of [true, false]) {
    for (const healthy of [true, false]) {
      for (const age of ages) {
        const h = health({ configured, healthy, last_ok_age_s: age as number | null });
        const v = deadmanVerdict(h);
        const tag = `configured=${configured} healthy=${healthy} last_ok_age_s=${String(age)}`;
        eq(deadmanWord(h), v.label.toUpperCase(), `word drifted from the verdict: ${tag}`);
        eq(deadmanTone(h), v.tone, `tone drifted from the verdict: ${tag}`);
        // The one place the sheet words a line of its own: a configured rig's
        // line carries the verdict's own detail, never a stronger claim.
        if (configured) {
          eq(deadmanLine(h).startsWith("configured · "), true, `line lost its prefix: ${tag}`);
          eq(deadmanLine(h).toLowerCase().includes("healthy"), false, `line says healthy: ${tag}`);
        }
      }
    }
  }
});

const total = passed + failed;
console.log(`w15SafetyDeadman.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
