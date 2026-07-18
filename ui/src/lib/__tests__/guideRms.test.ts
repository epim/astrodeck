// guideRms.test.ts — pure tests for lib/guideRms.ts's tagGuideRms +
// selectGuideWindows, the same-night per-provider RMS ingest/read-back logic
// GuideView's provider-switch panel depends on (P5-T1 fix round C1/I2 — the
// client half of C1 lives here). Copies the apiError.test.ts idiom exactly: a
// tiny in-file test()/assert() harness, no test runner dependency, run under
// tsx (`npx tsx src/lib/__tests__/guideRms.test.ts`).
import {
  tagGuideRms,
  selectGuideWindows,
  type GuideRmsByKind,
  type GuideProviderTag,
} from "../guideRms";
import type { GuideStats } from "../../types";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

// A GuideStats with `n` samples in its rolling window and the given total RMS.
const stats = (rmsTotal: number, n = 40): GuideStats => ({
  guiding: true,
  rms_ra: rmsTotal * 0.7,
  rms_dec: rmsTotal * 0.5,
  rms_total: rmsTotal,
  snr: 30,
  recent: Array.from({ length: n }, (_, i) => ({ t: i, ra: 0, dec: 0 })),
});
const astrodeck: GuideProviderTag = { kind: "astrodeck", label: "AstroDeck native" };
const sim: GuideProviderTag = { kind: "sim", label: "Simulator" };
const backend: GuideProviderTag = { kind: "backend", label: "PHD2" };

// ---- tagGuideRms -----------------------------------------------------------

test("tags a tick under the resolved provider kind with its label", () => {
  const out = tagGuideRms({}, stats(0.4), astrodeck, 1000);
  assert(!!out.astrodeck, "astrodeck entry present");
  assert(out.astrodeck!.rms_total === 0.4, `rms_total: ${out.astrodeck!.rms_total}`);
  assert(out.astrodeck!.providerLabel === "AstroDeck native", `label: ${out.astrodeck!.providerLabel}`);
  assert(out.astrodeck!.atMs === 1000, `atMs: ${out.astrodeck!.atMs}`);
});

test("a tick with no resolved provider is DROPPED (never filed under a kind)", () => {
  const prev: GuideRmsByKind = { backend: { ...stats(0.5), providerLabel: "PHD2", atMs: 1 } };
  assert(tagGuideRms(prev, stats(0.4), null, 2) === prev, "null choice returns prev unchanged");
  assert(tagGuideRms(prev, stats(0.4), undefined, 2) === prev, "undefined choice returns prev unchanged");
  // a malformed choice with an empty kind is also dropped, not filed under "".
  const emptyKind = tagGuideRms(prev, stats(0.4), { kind: "", label: "x" }, 2);
  assert(emptyKind === prev, "empty-kind choice returns prev unchanged");
});

test("is pure — does not mutate the previous map", () => {
  const prev: GuideRmsByKind = {};
  const out = tagGuideRms(prev, stats(0.4), astrodeck, 1);
  assert(prev.astrodeck === undefined, "prev was not mutated");
  assert(out !== prev, "a new map is returned");
});

test("keeps distinct provider windows side-by-side across a switch", () => {
  let m: GuideRmsByKind = {};
  m = tagGuideRms(m, stats(0.4), astrodeck, 1);
  m = tagGuideRms(m, stats(0.9), backend, 2); // user switched, restarted guiding
  assert(m.astrodeck!.rms_total === 0.4, "native window survives the switch");
  assert(m.backend!.rms_total === 0.9, "backend window recorded");
});

test("latest tick per kind wins (rolling replacement, not accumulation)", () => {
  let m: GuideRmsByKind = {};
  m = tagGuideRms(m, stats(0.8), backend, 1);
  m = tagGuideRms(m, stats(0.3), backend, 2);
  assert(m.backend!.rms_total === 0.3, `latest wins: ${m.backend!.rms_total}`);
  assert(m.backend!.atMs === 2, `atMs latest: ${m.backend!.atMs}`);
});

// ---- selectGuideWindows ----------------------------------------------------

test("native window comes from the astrodeck bucket, backend from backend", () => {
  let m: GuideRmsByKind = {};
  m = tagGuideRms(m, stats(0.4, 33), astrodeck, 1);
  m = tagGuideRms(m, stats(0.9, 22), backend, 2);
  const { native, backend: back } = selectGuideWindows(m);
  assert(native?.label === "AstroDeck native", `native label: ${native?.label}`);
  assert(native?.rmsTotal === 0.4, `native rms: ${native?.rmsTotal}`);
  assert(native?.samples === 33, `native samples (== recent.length): ${native?.samples}`);
  assert(back?.label === "PHD2" && back?.rmsTotal === 0.9, `backend window: ${JSON.stringify(back)}`);
  assert(back?.samples === 22, `backend samples: ${back?.samples}`);
});

test("native window falls back to the sim bucket when astrodeck is absent", () => {
  const m = tagGuideRms({}, stats(0.5, 40), sim, 1);
  const { native } = selectGuideWindows(m);
  assert(native?.label === "Simulator", `native label: ${native?.label}`);
  assert(native?.rmsTotal === 0.5, `native rms: ${native?.rmsTotal}`);
});

test("astrodeck takes precedence over sim for the native window", () => {
  let m: GuideRmsByKind = {};
  m = tagGuideRms(m, stats(0.5), sim, 1);
  m = tagGuideRms(m, stats(0.3), astrodeck, 2);
  const { native } = selectGuideWindows(m);
  assert(native?.label === "AstroDeck native", `native label: ${native?.label}`);
});

test("a provider that hasn't guided yields an undefined window", () => {
  const m = tagGuideRms({}, stats(0.4), astrodeck, 1);
  const { native, backend: back } = selectGuideWindows(m);
  assert(native !== undefined, "native present");
  assert(back === undefined, "backend undefined until it guides");
});

test("empty map yields two undefined windows", () => {
  const { native, backend: back } = selectGuideWindows({});
  assert(native === undefined && back === undefined, "both undefined");
});

console.log(`guideRms.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
