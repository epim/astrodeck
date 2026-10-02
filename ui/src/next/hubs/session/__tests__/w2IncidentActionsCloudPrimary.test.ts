// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w2IncidentActionsCloudPrimary.test.ts - WP-17 (c), the hub half (#260).
//
//   Run directly:  node --import tsx src/next/hubs/session/__tests__/w2IncidentActionsCloudPrimary.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// `actionsFor`'s "cloud" case OVERRODE whatever `next/lib/incidents.ts` said
// about `primary`: it forced `ignore_weather` primary in both its states
// (unpressed "IGNORE WEATHER TONIGHT" and pressed "UNDO - STOP IGNORING
// WEATHER") and forced WAIT to never be primary. So fixing the lib alone
// (w2CloudHoldIgnoreWeatherNotPrimary.test.ts) would not have fixed the card
// an operator actually sees - this file is what proves the fix reaches the
// rendered action list, since `IncidentStack.tsx` always calls `actionsFor`
// before drawing a card.
//
// NAMED MUTANT, run from a byte copy of incidentActions.ts and restored
// byte-identical afterwards (sha256 checked). The observed failure is quoted
// at the test it turns red.
//   A1 "restore the override"   the cloud-case map put back to forcing
//                                ignore_weather primary and wait non-primary

/* eslint-disable @typescript-eslint/no-explicit-any */

const { JSDOM } = await import("jsdom");
const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "http://local/" });
const win = dom.window as any;
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} send() {} addEventListener() {} removeEventListener() {} };
const g = globalThis as any;
for (const k of ["window", "document", "navigator", "localStorage", "sessionStorage",
  "location", "history", "Element", "Node", "Event", "CustomEvent", "WebSocket",
  "matchMedia", "getComputedStyle"]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}

const { actionsFor, INCIDENT_ACTIONS } = await import("../now/incidentActions");
import type { Incident } from "../../../lib/incidents";
import type { RefineContext } from "../now/incidentActions";

let passed = 0, failed = 0; const failures: string[] = [];
function test(n: string, fn: () => void) { try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${n}: ${(e as Error).message}`); } }
function assert(cond: boolean, m: string): void { if (!cond) throw new Error(m); }

const CTX: Partial<RefineContext> = { weatherIgnored: false, wide: false, coolerTargetC: null };

/** A minimal cloud-hold incident, as `next/lib/incidents.ts` now emits it
 *  (WAIT primary, ignore_weather not), so these tests exercise `actionsFor`'s
 *  OWN behaviour rather than just echoing the lib's values back. */
function cloudIncident(): Incident {
  return {
    kind: "cloud",
    pill: "HOLDING",
    color: "#7fd4ff",
    title: "CLOUD HOLD",
    sinceMs: null,
    resolvesItself: true,
    engine: "held for cloud - waiting for clear sky",
    next: "watching the star count and the cloud score",
    actions: [
      { id: "wait", label: "WAIT", primary: true },
      { id: "ignore_weather", label: "IGNORE FORECAST RAIN TONIGHT" },
    ],
  };
}

test("ignore_weather is not primary before it has been pressed (#260)", () => {
  const out = actionsFor(cloudIncident(), CTX as RefineContext);
  const ignore = out.find((a) => a.id === "ignore_weather")!;
  assert(ignore != null, "ignore_weather dropped entirely");
  assert(!ignore.primary, "ignore_weather is primary before being pressed");
});

test("ignore_weather is STILL not primary once it has been pressed (#260)", () => {
  // A1 "restore the override", observed:
  //   x ignore_weather is STILL not primary once it has been pressed (#260):
  //   the pressed UNDO state is primary on a hold it cannot touch
  const out = actionsFor(cloudIncident(), { ...CTX, weatherIgnored: true } as RefineContext);
  const ignore = out.find((a) => a.id === "ignore_weather")!;
  assert(ignore != null, "ignore_weather dropped entirely");
  assert(!ignore.primary, "the pressed UNDO state is primary on a hold it cannot touch");
});

test("the pressed label says what is actually on - stop ignoring forecast rain, not weather", () => {
  const out = actionsFor(cloudIncident(), { ...CTX, weatherIgnored: true } as RefineContext);
  const ignore = out.find((a) => a.id === "ignore_weather")!;
  assert(/forecast rain/i.test(ignore.label), `the UNDO label does not name forecast rain: "${ignore.label}"`);
  assert(!/UNDO - STOP IGNORING WEATHER$/i.test(ignore.label),
    `the old false-affordance UNDO label is back: "${ignore.label}"`);
});

test("WAIT carries the card's primary affordance instead", () => {
  const out = actionsFor(cloudIncident(), CTX as RefineContext);
  const wait = out.find((a) => a.id === "wait")!;
  assert(!!wait.primary, "no action on the cloud card is primary");
});

test("the table's own fallback label also names the forecast rain veto", () => {
  assert(/forecast rain/i.test(INCIDENT_ACTIONS.ignore_weather.label),
    `INCIDENT_ACTIONS.ignore_weather.label: "${INCIDENT_ACTIONS.ignore_weather.label}"`);
});

console.log(`${passed} passed, ${failed} failed`);
for (const f of failures) console.error(f);
export const result = { passed, failed, total: passed + failed };
