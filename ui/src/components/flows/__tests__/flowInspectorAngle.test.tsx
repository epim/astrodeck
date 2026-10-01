// flowInspectorAngle.test.tsx — the classic inspector shows the camera angle a
// TARGET's rotation MEANS when the node stores no `angle` of its own.
//
//   Run directly:  npx tsx src/components/flows/__tests__/flowInspectorAngle.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// Mosaic S3, spec 3.1: `angle` has no missing-key default. The server derives
// it from `rotation` (nodes.py `target_angle`), so a TARGET saved before S3
// with a real position angle carries no `angle` key and the run commands
// "Rotate to PA". `nodeDefs.fieldValue` is the one rule for what a row shows;
// until the integration of S3 this inspector passed the raw param instead, so
// such a block read as a blank select (FlowFieldRow's `selectOptions` pads an
// unknown value with "") while the rotator was being turned (S3-V's verifier,
// item 4). The phone editor's twin is in inspectorDom.test.tsx, section 10.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "localStorage", "getComputedStyle", "matchMedia",
  "WebSocket",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.requestAnimationFrame = (cb: (t: number) => void) => setTimeout(() => cb(0), 0);
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------- imports
import React from "react";
import { createRoot } from "react-dom/client";
import { act } from "react";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
const assert = {
  ok(cond: unknown, msg?: string) { if (!cond) throw new Error(msg ?? "not ok"); },
  equal(a: unknown, b: unknown, msg?: string) {
    if (a !== b) throw new Error(msg ?? `${String(a)} !== ${String(b)}`);
  },
};

const { useStore } = await import("../../../store");
const { NODE_DEFS } = await import("../nodeDefs");
const FlowInspector = (await import("../FlowInspector")).default;

const root = createRoot(win.document.getElementById("root"));
const container = win.document.getElementById("root");

/** Mount the inspector on one TARGET whose params are exactly `params`. */
function mountTarget(params: Record<string, string | number>): void {
  act(() => { root.render(null); });
  act(() => {
    useStore.setState({
      flows: {
        ...useStore.getState().flows,
        graph: { nodes: [{ id: "t", type: "target", x: 0, y: 0, params }], edges: [] },
        sel: { kind: "node", id: "t" },
        compiled: null,
      },
    } as any);
  });
  act(() => { root.render(React.createElement(FlowInspector, {})); });
}

/** The angle row's `<select>`: the one that offers "Camera fixed at PA". */
function angleSelect(): any {
  const sel = [...container.querySelectorAll("select")].find((s: any) =>
    [...s.options].some((o: any) => o.value === "Camera fixed at PA"));
  assert.ok(sel, "precondition: the TARGET's angle select did not render");
  return sel;
}

const storedParams = (): Record<string, string | number> =>
  useStore.getState().flows.graph.nodes.find((n) => n.id === "t")!.params;

// --------------------------------------------------------------------- tests

// RED under mutant "the inspector passes the raw param" (in a private scratch
// copy, FlowInspector.tsx's `value={fieldValue(f, node.params)}` put back to
// `value={node.params[f.key]}`), observed, with the next case red too:
//
//   x a stored PA and no angle key reads "Rotate to PA": the select shows ""
//     for a block the run rotates to PA 23.4
//   x no PA and no angle key reads "Any angle": a block with no PA must read
//     as any angle
test("a stored PA and no angle key reads \"Rotate to PA\"", () => {
  const stored = { ...NODE_DEFS.target.params, rotation: 23.4 };
  assert.ok(!("angle" in stored), "precondition: the fixture carries an angle key");
  mountTarget(stored);
  const shown = angleSelect().value;
  assert.equal(shown, "Rotate to PA",
    `the select shows "${shown}" for a block the run rotates to PA 23.4`);
  assert.ok(!("angle" in storedParams()),
    "showing the derived angle wrote it into the node - it is display only");
});

// The derivation's other arm: with no PA the row reads "Any angle", not blank.
test("no PA and no angle key reads \"Any angle\"", () => {
  mountTarget({ ...NODE_DEFS.target.params, rotation: -1 });
  assert.equal(angleSelect().value, "Any angle",
    "a block with no PA must read as any angle");
});

// CONTROL: a stored angle is shown as stored, with the raw param or without
// (it stayed green under the mutant above).
test("CONTROL: the node's own angle beats the derivation", () => {
  mountTarget({ ...NODE_DEFS.target.params, rotation: 23.4, angle: "Camera fixed at PA" });
  assert.equal(angleSelect().value, "Camera fixed at PA",
    "a chosen angle was overridden by the one its rotation would derive");
});

// ------------------------------------------------------------------- report
act(() => { root.unmount(); });
const total = passed + failed;
console.log(`flowInspectorAngle.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
