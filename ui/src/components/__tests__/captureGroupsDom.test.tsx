import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
const dom = new JSDOM('<div id="root"></div>', { url: "http://local/" });
Object.assign(globalThis, { window: dom.window, document: dom.window.document,
  IS_REACT_ACT_ENVIRONMENT: true });
const { createRoot } = await import("react-dom/client");
const { act } = await import("react");
const { default: CaptureGroups } = await import("../gallery/CaptureGroups");
const root = createRoot(document.getElementById("root")!);
const groups = [
  { target: "M31", filter: "L", frame_type: "Light", width: 6252, height: 4176,
    bin_x: 1, bin_y: 1, exposure_s: 60, count: 204, bytes: 100 },
  { target: "M31", filter: "L", frame_type: "Light", width: 3124, height: 2088,
    bin_x: 2, bin_y: 2, exposure_s: 120, count: 183, bytes: 100 },
  { target: "M31", filter: "L", frame_type: "Light", width: 3124, height: 2088,
    bin_x: 2, bin_y: 2, exposure_s: 10, count: 59, bytes: 100 },
];
await act(async () => { root.render(<CaptureGroups groups={groups} />); });
assert.match(document.body.textContent!, /Mixed capture settings/);
assert.equal(document.querySelectorAll("li").length, 0, "collapsed groups cost no list DOM");
assert.equal(document.querySelector("button")!.getAttribute("aria-expanded"), "false");
await act(async () => { document.querySelector("button")!.click(); });
assert.equal(document.querySelector("button")!.getAttribute("aria-expanded"), "true");
assert.equal(document.querySelectorAll("li").length, 3);
assert.match(document.body.textContent!, /204 frames · 6252 × 4176 px · bin 1 × 1 · 60 s/);
assert.match(document.body.textContent!, /183 frames · 3124 × 2088 px · bin 2 × 2 · 120 s/);
assert.match(document.body.textContent!, /59 frames · 3124 × 2088 px · bin 2 × 2 · 10 s/);
await act(async () => { root.render(<CaptureGroups groups={[{ ...groups[0], bin_x: null, bin_y: null }]} incomplete />); });
assert.match(document.body.textContent!, /binning unknown/);
assert.match(document.body.textContent!, /counts are incomplete/);
assert.doesNotMatch(document.body.textContent!, /Mixed capture settings/);
await act(async () => { root.render(<CaptureGroups groups={Array.from({ length: 100 }, (_, i) => ({ ...groups[0], target: `Target ${i}` }))} />); });
assert.equal(document.querySelectorAll("li").length, 40, "bound the expanded DOM on phones");
await act(async () => { Array.from(document.querySelectorAll("button")).find(b => b.textContent === "Show more groups")!.click(); });
assert.equal(document.querySelectorAll("li").length, 80);
await act(async () => { root.unmount(); });
dom.window.close();
console.log("captureGroupsDom.test: 14/14 passed");
export const results = { passed: 14, failed: 0, total: 14 };
