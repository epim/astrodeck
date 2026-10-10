// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w22PhotosphereSharedVideoDom.test.tsx - WP-208 / #1003: the real HorizonSheet,
// one <video> element, two scans. A scan that was cancelled while its camera
// preview was still starting must not blank the picture of the scan that took
// the element after it.
//
//   Run directly:  npx tsx src/next/hubs/sky/sheets/__tests__/w22PhotosphereSharedVideoDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHY THE SHEET AND NOT THE SWEEP. w22PhotosphereStartSupersede.test.ts drives
// `PhotosphereSweep` directly, and a sweep alone never shows the harm: the late
// `stop()` that blanks the picture is the CALLER's. `startCapture` (horizon.tsx)
// returns from `await sweep.start(...)` and, finding that it no longer owns the
// sweep, runs `sweep.stop()`. The sheet keeps ONE <video> for every scan, so
// that stop() reaches the element the newer scan is showing. A review probe
// found exactly this after a first fix that only made `start()` return quietly:
// the picture went blank under a sweep that still reported ready, and a test
// that called `start()` directly passed. So this one goes through the sheet.
//
// THE SEQUENCE (the order the browser produces it, with the one thing the
// browser decides - when it rejects the pending play() - done by hand):
//   1. open the camera; its play() is still pending
//   2. Cancel scan (sweep.stop(): the element is cleared, the camera released)
//   3. open the camera again: a NEW sweep takes the same element; its play()
//      resolves and the preview is up
//   4. the FIRST sweep's play() now rejects (AbortError)
// After 4 the element must still show the second scan's camera, that camera
// must not have been stopped, and the sheet must show no error.
//
// A witness keeps the case on the path it grades: `PhotosphereSweep.start` and
// `.stop` are wrapped (calling through) so the test can say that the first
// sweep's start() really did end and that something really did stop it after
// the rejection. A case whose rejection never reached the caller would pass
// whatever stop() did.
//
// Named mutant (an edit to photosphere.ts, run from a byte backup and restored
// byte-identically); the first failing assertion is quoted:
//   w22m6_stop_clears_unconditionally -- stop() back to
//     `if (this.video) this.video.srcObject = null;`: "the late stop() of the
//     cancelled scan cleared the element under the second scan's picture".

import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

const dom = new JSDOM('<html><body><div id="root"></div></body></html>', { url: "https://localhost/", pretendToBeVisual: true });
const w = dom.window as any, g = globalThis as any;
for (const k of ["window", "document", "navigator", "HTMLElement", "HTMLVideoElement", "Element", "Node", "Event", "CustomEvent", "MouseEvent", "localStorage", "sessionStorage", "getComputedStyle", "requestAnimationFrame", "cancelAnimationFrame"]) {
  Object.defineProperty(g, k, { value: k === "window" ? w : w[k], writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;
w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
g.matchMedia = w.matchMedia;
g.WebSocket = w.WebSocket = class { close() {} addEventListener() {} send() {} };
w.DeviceOrientationEvent = class {};
Object.defineProperty(w, "isSecureContext", { value: true });
w.HTMLCanvasElement.prototype.getContext = () => ({ drawImage() {}, getImageData: (_x: number, _y: number, width: number, height: number) =>
  ({ data: new Uint8ClampedArray(width * height * 4) }), putImageData() {}, createImageData: (width: number, height: number) =>
  ({ data: new Uint8ClampedArray(width * height * 4) }) });
// No scan timer ticks in the background; nothing here needs a frame.
g.setInterval = () => 1;
g.clearInterval = () => undefined;

// ------------------------------------------------------------------- camera
type Track = { stops: number; stop(): void; getSettings(): { deviceId: string }; addEventListener(): void };
type Stream = { tracks: Track[]; getTracks(): Track[]; getVideoTracks(): Track[] };
const streams: Stream[] = [];
Object.defineProperty(w.navigator, "mediaDevices", { value: {
  enumerateDevices: async () => [{ kind: "videoinput", deviceId: "cam", label: "Back camera" }],
  getUserMedia: async () => {
    const track: Track = { stops: 0, stop() { this.stops++; }, getSettings: () => ({ deviceId: "cam" }), addEventListener() {} };
    const stream: Stream = { tracks: [track], getTracks: () => [track], getVideoTracks: () => [track] };
    streams.push(stream);
    return stream;
  },
}, configurable: true });
// Every play() hands back a promise the test settles, in the order they were asked.
type Play = { resolve(): void; reject(e: unknown): void };
const plays: Play[] = [];
w.HTMLVideoElement.prototype.play = function () { return new Promise<void>((resolve, reject) => { plays.push({ resolve, reject }); }); };
const aborted = () => new w.DOMException("The play() request was interrupted by a new load request.", "AbortError");

g.fetch = async (url: string) => ({ ok: true, status: 200, json: async () => String(url).includes("/api/site") ? { site: { name: "Phone test", horizon_points: [] } } : {} });

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { HorizonSheet } = await import("../horizon");
const { PhotosphereSweep } = await import("../photosphere");

// The witness: every sweep the sheet builds, and every stop() each one gets.
type Sweep = InstanceType<typeof PhotosphereSweep>;
const started: Sweep[] = [];
const stopped: Sweep[] = [];
const realStart = PhotosphereSweep.prototype.start;
const realStop = PhotosphereSweep.prototype.stop;
PhotosphereSweep.prototype.start = function (this: Sweep, ...args: Parameters<typeof realStart>) {
  started.push(this);
  return realStart.apply(this, args);
};
PhotosphereSweep.prototype.stop = function (this: Sweep) { stopped.push(this); realStop.call(this); };
const stopsOf = (s: Sweep) => stopped.filter((x) => x === s).length;

useStore.setState({ principal: { role: "admin", caps: ["config.safety", "config.site_optics"] }, wsPhase: "up",
  equipConnected: true, status: { mode: "sim", connected: {}, busy_lanes: [] }, config: { safety: { horizon: [] } },
} as never);
const root = createRoot(document.getElementById("root")!);
const settle = async () => { await act(async () => { await Promise.resolve(); await Promise.resolve(); }); };
const flush = () => act(async () => { await new Promise<void>((r) => setTimeout(r, 0)); });
const byTest = (id: string) => document.querySelector<HTMLElement>(`[data-testid="${id}"]`)!;
const cancelButton = () => [...document.querySelectorAll<HTMLElement>("button")].find((b) => b.textContent?.trim() === "Cancel scan")!;
const click = async (el: HTMLElement) => { assert.ok(el, "the control is not on the page"); await act(async () => el.click()); await settle(); };
async function until(cond: () => boolean, what: string): Promise<void> {
  for (let i = 0; i < 200; i++) { if (cond()) return; await flush(); }
  throw new Error(`never happened: ${what}`);
}

let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}

await act(async () => root.render(createElement(HorizonSheet, { depth: 0, params: { site: "current" }, guided: true })));
await settle();

await test("a cancelled scan's late play() rejection leaves the next scan's picture on the shared video", async () => {
  const video = byTest("photosphere-video") as HTMLVideoElement;
  // 1. open the camera; play() is pending
  await click(byTest("capture-photosphere"));
  await until(() => plays.length === 1, "the first scan reached play()");
  // 2. cancel while it pends
  await click(cancelButton());
  assert.equal(streams[0].tracks[0].stops, 1, "Cancel scan did not release the first camera");
  // 3. open it again: a new sweep takes the same element
  await click(byTest("capture-photosphere"));
  await until(() => plays.length === 2, "the second scan reached play()");
  assert.equal(started.length, 2, "the sheet did not build a second sweep");
  assert.equal(streams.length, 2, "the second scan did not open its own camera");
  await act(async () => { plays[1].resolve(); }); await settle();
  assert.equal(video.srcObject, streams[1], "the second scan is not showing its camera before the rejection");
  assert.equal(started[1].previewReady, true, "the second scan never became ready");
  assert.equal(document.querySelector(".photosphere-opening"), null, "the second scan is still opening");

  // 4. the browser rejects the FIRST scan's pending play() now
  const stopsBefore = stopsOf(started[0]);
  await act(async () => { plays[0].reject(aborted()); }); await flush(); await settle();
  assert.ok(stopsOf(started[0]) > stopsBefore,
    "nothing stopped the first sweep after its rejection: the case is not on the caller's path");

  assert.equal(video.srcObject, streams[1],
    "the late stop() of the cancelled scan cleared the element under the second scan's picture");
  assert.equal(streams[1].tracks[0].stops, 0, "the second scan's camera was stopped");
  assert.equal(started[1].previewReady, true, "the second scan stopped reading as ready");
  assert.equal(document.querySelector('[role="alert"]'), null, "the sheet is showing an error");
  assert.equal(document.querySelector(".photosphere-opening"), null, "the sheet went back to opening the camera");
  assert.equal(byTest("photosphere-capturing").hidden, false, "the scan panel closed under the second scan");
  await click(cancelButton());
  assert.equal(video.srcObject, null, "Cancel scan did not clear the element it owns");
  assert.equal(streams[1].tracks[0].stops, 1, "Cancel scan did not release the second camera");
});

await act(async () => root.unmount());
console.log(`w22PhotosphereSharedVideoDom: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total: passed + failed };
