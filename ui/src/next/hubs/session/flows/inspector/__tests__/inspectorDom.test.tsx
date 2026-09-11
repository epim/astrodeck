// inspectorDom.test.tsx - SESSION / FLOWS: the inspector, the field rows, the
// cycle-plan table, the add-stage palette and the stage sheet, MOUNTED.
//
//   Run directly:  npx tsx src/next/hubs/session/flows/inspector/__tests__/inspectorDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by
//   `npx tsc --noEmit -p tsconfig.json`.
//
// WHAT IS WORTH GUARDING HERE
//
//   1. IT RENDERED, WITH REAL ROWS. An inspector that produced nothing would let
//      every assertion below pass over a blank page, so the first test names the
//      marker AND a field row built from the shared node vocabulary.
//   2. PARITY. The set of field keys on screen for a seeded node type equals the
//      key set `NODE_DEFS` declares for it. A field quietly dropped in the
//      rebuild - the one failure mode a 77-component re-skin actually has -
//      turns this file red rather than turning up on a rig at 3 a.m.
//   3. AN EDIT REACHES THE STORE AS THE RIGHT TYPE. `flowsSetParam` coerces by
//      the type of the DEFAULT, so a numeric field must land as a number and a
//      select must land as the string it offered.
//   4. A READ-ONLY (EXAMPLE) FLOW REFUSES EVERY EDIT AND SAYS SO. Honest-disabled:
//      `aria-disabled` plus the reason, never the native `disabled` attribute,
//      and not one `flowsSetParam` gets through.
//   5. FILTER NAMES COME FROM THE WHEEL. Blackout and unnamed slots are not
//      offered; a name this wheel does not have cannot be entered at all.
//   6. THE PALETTE DROPS A STAGE WHERE THE CANVAS SAYS - and at the documented
//      fallback point when no canvas is mounted, which is every phone and every
//      test.
//   7. THE STAGE SHEET OPENS ON `flows.editNode` AND BACK CLEARS IT. A stale
//      `editNode` re-opens the sheet on the next route change.
//
// Convention: shell-and-tests.md section 4 - jsdom by hand, createRoot + act,
// native events, printed tally plus the `{ passed, failed, total }` export.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// The three roots of this area import `./inspector.css` (wave R7: each rebuilt
// area owns its own stylesheet). Node has no idea what a `.css` file is, so a
// synchronous load hook answers with an empty module.
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

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;

// Everything answers false, so `useBreakpoint()` says PHONE - the layout the
// design is written for, and the tier with no canvas mounted, which is exactly
// the case the palette's fallback drop point exists for.
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "FocusEvent", "localStorage", "sessionStorage", "getComputedStyle", "matchMedia",
  "WebSocket", "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// Nothing on this surface fetches. Anything that tries is a bug, and a 404 that
// records the attempt is how it shows up.
const asked: string[] = [];
g.fetch = async (url: any, init?: any) => {
  asked.push(`${init?.method ?? "GET"} ${String(url)}`);
  return {
    ok: false, status: 404, statusText: "Not Found",
    headers: { get: () => "application/json" }, json: async () => ({}), text: async () => "",
  };
};

// ------------------------------------------------------------------ imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../../store");
const { NODE_DEFS } = await import("../../../../../../components/flows/nodeDefs");
const { PALETTE_FALLBACK_DROP } = await import("../../../../../../components/flows/FlowPalette");
const { resetRouterCacheForTests } = await import("../../../../../router");
const {
  FlowInspectorColumn, FLOW_READONLY_REASON, FlowPaletteRail,
  flowInspectorSheets, splitUnmapped,
} = await import("../index");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg}\n  expected ${String(want)}\n  got      ${String(got)}`);
}

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const settle = async () => {
  for (let i = 0; i < 4; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const tid = (t: string): any => container.querySelector(`[data-testid="${t}"]`);
const click = (el: any) => {
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};
/** Type into a controlled input the way a user does: React listens to the
 *  native `input` event, so the value has to go through the prototype setter it
 *  patched or React never sees the change. */
const typeInto = (el: any, value: string) => {
  act(() => {
    Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!.call(el, value);
    el.dispatchEvent(new win.Event("input", { bubbles: true }));
  });
};
/** React 18 implements `onBlur` on the native `focusout`, which bubbles; a bare
 *  `blur` event never reaches the root listener and the commit would not fire. */
const blur = (el: any) => {
  act(() => { el.dispatchEvent(new win.Event("focusout", { bubbles: true })); });
};

const NODE_CAPTURE = {
  id: "n-cap", type: "capture" as const, x: 10, y: 20,
  params: { ...NODE_DEFS.capture.params },
};
const NODE_TARGET = {
  id: "n-tgt", type: "target" as const, x: 200, y: 20,
  params: { ...NODE_DEFS.target.params },
};
const NODE_CYCLE = {
  id: "n-cyc", type: "cycle" as const, x: 400, y: 20,
  params: { ...NODE_DEFS.cycle.params },
};
const EDGE = { id: "e1", from: "n-cap", fromPort: "complete", to: "n-tgt", toPort: "arm" };

/** The rig's wheel: one usable slot, one narrowband slot, one UNNAMED slot and
 *  one BLACKOUT slot. The last two must not be offered - an opaque slot passes
 *  no light, and "Slot 3" names nothing that can go in a FITS header. */
const WHEEL = {
  position: 0,
  names: ["Lum", "Ha", "Slot 3", "Dark"],
  opaque: [false, false, false, true],
};

function seed(opts: { readonly?: boolean; sel?: string | null; wheel?: boolean } = {}): void {
  act(() => {
    const s = useStore.getState();
    useStore.setState({
      authGate: "open",
      wsPhase: "up",
      principal: { role: "admin", email: null, caps: ["control.capture"] } as never,
      status: (opts.wheel ? { filterwheel: WHEEL } : {}) as never,
      flows: {
        ...s.flows,
        record: {
          id: "f1", name: "M31 LRGB", folder: "My flows", tagline: "",
          graph: { nodes: [], edges: [] }, created_ts: 0, updated_ts: 0,
          last_run: null, last_result: "", readonly: opts.readonly ?? false,
        },
        graph: {
          nodes: [
            { ...NODE_CAPTURE, params: { ...NODE_CAPTURE.params } },
            { ...NODE_TARGET, params: { ...NODE_TARGET.params } },
            { ...NODE_CYCLE, params: { ...NODE_CYCLE.params } },
          ],
          edges: [{ ...EDGE }],
        },
        sel: opts.sel === undefined || opts.sel === null
          ? null : { kind: "node" as const, id: opts.sel },
        editNode: null,
        compiled: null,
      } as never,
    } as never);
  });
}

const paramsOf = (id: string): Record<string, string | number> =>
  useStore.getState().flows.graph.nodes.find((n) => n.id === id)!.params;

async function mount(el: any): Promise<void> {
  await act(async () => { root.render(createElement("div")); });
  await act(async () => { root.render(el); });
  await settle();
}

/** The `flow-field-<key>` markers on screen, in DOM order. */
const fieldKeys = (): string[] =>
  Array.from(container.querySelectorAll('[data-testid^="flow-field-"]'))
    .map((el: any) => String(el.getAttribute("data-testid")).slice("flow-field-".length));

// ================================================== 1. it rendered, with rows

seed({ sel: "n-cap" });
await mount(createElement(FlowInspectorColumn as any));

test("precondition: the inspector rendered the selected stage, not an empty box", () => {
  assert(tid("flow-inspector") != null,
    "no flow-inspector marker - the fixture is wrong, not the screen");
  assert(/CAPTURE LOOP/.test(container.textContent),
    "the node's own label from NODE_DEFS never rendered");
  assert(tid("flow-field-exposure") != null,
    "not one field row rendered, so every assertion below would pass over a blank column");
  eq(asked.length, 0, "the inspector fetched something; it reads the store and nothing else");
});

// ============================================================== 2. PARITY

test("PARITY: every field NODE_DEFS declares for this node type is on screen", () => {
  const want = NODE_DEFS.capture.fields.map((f) => f.key);
  const got = fieldKeys();
  eq(got.join(","), want.join(","),
    "the rendered field keys are not the node vocabulary's field keys - a parameter "
    + "was dropped, renamed or reordered in the rebuild");
  // The brief's own wording: the COUNT is `Object.keys(...)` of the field list.
  eq(got.length, Object.keys(NODE_DEFS.capture.fields).length,
    "the rendered field count does not match NODE_DEFS.capture.fields");
});

await testAsync("PARITY: the same holds for a node whose fields include the cycle plan", async () => {
  seed({ sel: "n-cyc", wheel: true });
  await mount(createElement(FlowInspectorColumn as any));
  eq(fieldKeys().join(","), NODE_DEFS.cycle.fields.map((f) => f.key).join(","),
    "FILTER CYCLE lost or reordered a parameter");
});

// ============================== 3. an edit reaches the store as the right type

await testAsync("a numeric field commits the PARSED NUMBER, at blur and not before", async () => {
  seed({ sel: "n-cap" });
  await mount(createElement(FlowInspectorColumn as any));
  const box = tid("flow-field-exposure").querySelector("input");
  assert(box != null, "the exposure field is not an input");

  typeInto(box, "300");
  eq(paramsOf("n-cap").exposure, 120,
    "the draft committed itself on a keystroke - clearing the box to retype would "
    + "then slam the default back under the cursor");

  blur(box);
  eq(paramsOf("n-cap").exposure as number, 300, "blur did not commit the typed exposure");
  eq(typeof paramsOf("n-cap").exposure, "number",
    "the exposure landed as a string; flowsSetParam coerces by the DEFAULT's type "
    + "and the compiler reads a number");
});

await testAsync("a blank numeric field is REFUSED, never read as 0", async () => {
  const box = tid("flow-field-exposure").querySelector("input");
  typeInto(box, "");
  blur(box);
  eq(paramsOf("n-cap").exposure as number, 300,
    "an empty box committed - a 0 s exposure is a plausible, finite, wrong number");
});

await testAsync("a closed option list commits the STRING it offered", async () => {
  // `bin` is three options, so it is a Segmented radiogroup; its value must stay
  // the string "1"/"2"/"4" or the coercion rule would rewrite the choice.
  const opt = tid("flow-field-bin").querySelector('[data-value="2"]');
  assert(opt != null, "BINNING did not render its options");
  click(opt);
  eq(paramsOf("n-cap").bin, "2", "the binning choice did not reach the store");
  eq(typeof paramsOf("n-cap").bin, "string",
    "binning landed as a number, which is the defect the string default exists to prevent");
});

await testAsync("a long option list is the design's dial, and it commits too", async () => {
  // `filter` is seven wheel slots - past the four a segmented control can hold.
  const stop = tid("flow-field-filter").querySelector('[data-value="Ha"]');
  assert(stop != null, "the filter field offered no Ha stop");
  click(stop);
  eq(paramsOf("n-cap").filter, "Ha", "the dial did not commit the filter");
});

// ==================================== 4. a read-only flow refuses every edit

await testAsync("an example flow renders EVERY field locked, and says why", async () => {
  seed({ readonly: true, sel: "n-cap" });
  await mount(createElement(FlowInspectorColumn as any));

  const rows = Array.from(container.querySelectorAll('[data-testid^="flow-field-"]'));
  assert(rows.length > 0, "precondition: no field rows, so 'all locked' is vacuous");
  for (const row of rows as any[]) {
    const key = row.getAttribute("data-testid");
    const locked = row.querySelector('[aria-disabled="true"]');
    assert(locked != null, `${key} rendered live on a read-only flow`);
    eq(locked.getAttribute("title"), FLOW_READONLY_REASON,
      `${key} is locked but does not carry the reason`);
    assert(row.querySelector("[disabled]") == null,
      `${key} used the native disabled attribute, which takes the reason out of the `
      + "accessibility tree along with the control");
  }
  assert(tid("flow-node-readonly-note") != null,
    "the stage block never printed the read-only note");
  assert(/example flows are not saved/.test(container.textContent),
    "the note does not say what read-only means for a flow");
});

await testAsync("and not one edit gets through", async () => {
  const before = JSON.stringify(paramsOf("n-cap"));
  const box = tid("flow-field-exposure").querySelector("input");
  typeInto(box, "900");
  blur(box);
  click(tid("flow-field-bin").querySelector('[data-value="4"]'));
  click(tid("flow-field-filter").querySelector('[data-value="SII"]'));
  eq(JSON.stringify(paramsOf("n-cap")), before,
    "a read-only flow accepted an edit that flowsSave would then silently discard");
});

await testAsync("a string field is locked the same way, read-only and not disabled", async () => {
  seed({ readonly: true, sel: "n-tgt" });
  await mount(createElement(FlowInspectorColumn as any));
  const box = tid("flow-field-name").querySelector("input");
  assert(box != null, "TARGET's Name field is not an input");
  eq(box.getAttribute("aria-disabled"), "true", "the name box rendered live");
  eq(box.hasAttribute("readonly"), true,
    "a locked text box must stay readable and copyable, so readOnly - not disabled");
  typeInto(box, "M42");
  eq(paramsOf("n-tgt").name, NODE_DEFS.target.params.name,
    "the locked name box wrote to the store");
});

// ============================ 5. filter names come from the wheel, never typed

await testAsync("the cycle plan offers the rig's wheel and nothing else", async () => {
  seed({ sel: "n-cyc", wheel: true });
  await mount(createElement(FlowInspectorColumn as any));

  assert(tid("cycle-slot-Lum") != null, "the wheel's own slot name is missing");
  assert(tid("cycle-slot-Ha") != null, "the wheel's narrowband slot is missing");
  assert(tid("cycle-slot-Dark") == null,
    "a BLACKOUT slot was offered - a light frame through it is a black frame with "
    + "IMAGETYP=Light on it");
  assert(tid("cycle-slot-Slot 3") == null,
    "an unnamed slot was offered; 'Slot 3' names nothing a FITS header can carry");
  assert(tid("cycle-slot-L") == null,
    "the fallback wheel is still on screen over a rig that reported its own");
});

await testAsync("ticking a slot writes wheel order and drops what this wheel lacks", async () => {
  // The shipped plan names L, R, G, B, Ha, OIII and SII. This wheel has Lum and
  // Ha, so only Ha starts ticked; ticking Lum must write BOTH in wheel order and
  // silently drop the five slots this rig does not have - a plan saved on another
  // wheel must not shoot a filter that is not in the tray.
  eq(tid("cycle-slot-Ha").getAttribute("aria-checked"), "true",
    "a slot the stored plan names did not read as ticked");
  eq(tid("cycle-slot-Lum").getAttribute("aria-checked"), "false",
    "a slot the stored plan does not name read as ticked");

  click(tid("cycle-slot-Lum"));
  eq(paramsOf("n-cyc").plan, "Lum 60, Ha 180",
    "the tick did not write the slot table in wheel order, or kept a filter name "
    + "this wheel does not have");
});

await testAsync("clearing an exposure box commits nothing; a real one commits", async () => {
  const box = tid("cycle-exp-Lum");
  assert(box != null && box.tagName === "INPUT", "the ticked slot has no exposure box");
  typeInto(box, "");
  blur(box);
  eq(paramsOf("n-cyc").plan, "Lum 60, Ha 180",
    "an empty box committed a zero-second slot, which to_plan refuses at RUN on a "
    + "graph that looked fine on screen");
  typeInto(box, "180");
  blur(box);
  eq(paramsOf("n-cyc").plan, "Lum 180, Ha 180", "a real exposure never reached the plan");
});

// ==================================== 6. the palette drops a stage where it says

await testAsync("the palette adds the stage at the fallback drop point with no canvas", async () => {
  seed({ sel: null });
  await mount(createElement(FlowPaletteRail as any));
  assert(tid("flow-palette") != null, "no flow-palette marker");
  const before = useStore.getState().flows.graph.nodes.length;

  click(tid("palette-type-notify"));
  const nodes = useStore.getState().flows.graph.nodes;
  eq(nodes.length, before + 1, "the palette added no stage");
  const added = nodes[nodes.length - 1];
  eq(added.type, "notify", "the palette added the wrong stage type");
  eq(added.x, PALETTE_FALLBACK_DROP.x, "the stage landed off the documented fallback x");
  eq(added.y, PALETTE_FALLBACK_DROP.y, "the stage landed off the documented fallback y");
});

await testAsync("a read-only flow's palette is locked, and adds nothing", async () => {
  seed({ readonly: true, sel: null });
  await mount(createElement(FlowPaletteRail as any));
  const chip = tid("palette-type-notify");
  eq(chip.getAttribute("aria-disabled"), "true", "the palette stayed live on an example flow");
  const before = useStore.getState().flows.graph.nodes.length;
  click(chip);
  eq(useStore.getState().flows.graph.nodes.length, before,
    "a read-only flow gained a stage that flowsSave would discard");
});

// ================== 7. the stage sheet opens on editNode and BACK clears it

await testAsync("the flowNode sheet opens on flows.editNode and BACK clears it", async () => {
  seed({ sel: null });
  win.location.hash = "#/session/flows/flowNode";
  resetRouterCacheForTests();
  await act(async () => { useStore.getState().flowsSetEditNode("n-cap"); });

  // The registry is `{ id, load }` since the cutover (D-FU-2 code-splitting),
  // so the sheet is FETCHED rather than named - which is also the assertion that
  // the loader really resolves to this area's component.
  const Sheet = (await flowInspectorSheets.flowNode.load()).default;
  await mount(createElement(Sheet as any, { params: {}, depth: 0 }));

  assert(tid("session-flow-node") != null, "no session-flow-node marker");
  assert(/CAPTURE LOOP/.test(container.textContent),
    "the sheet does not name the stage it is editing");
  assert(tid("flow-field-exposure") != null, "the sheet rendered no field rows");

  const back = container.querySelector(".nx-sheet-back");
  assert(back != null, "the sheet has no BACK");
  click(back);
  eq(useStore.getState().flows.editNode, null,
    "BACK left editNode set, so the sheet re-opens itself on the next route change");
});

await testAsync("with nothing being edited the sheet says so instead of rendering blank", async () => {
  await act(async () => { useStore.getState().flowsSetEditNode(null); });
  const Sheet = (await flowInspectorSheets.flowNode.load()).default;
  await mount(createElement(Sheet as any, { params: {}, depth: 0 }));
  assert(tid("flow-node-empty") != null, "an empty stage sheet rendered nothing at all");
});

await testAsync("the flowPalette sheet renders the palette", async () => {
  const Sheet = (await flowInspectorSheets.flowPalette.load()).default;
  await mount(createElement(Sheet as any, { params: {}, depth: 0 }));
  assert(tid("session-flow-palette") != null, "no session-flow-palette marker");
  assert(tid("palette-type-capture") != null, "the palette sheet offered no stages");
});

// ================ 8. the overview: counts, and the three unmapped statements

await testAsync("the overview counts the graph it is looking at", async () => {
  seed({ sel: null });
  await mount(createElement(FlowInspectorColumn as any));
  eq(tid("flow-stage-count").textContent, "3", "the stage count is not the graph's");
  eq(tid("flow-wire-count").textContent, "1", "the wire count is not the graph's");
  assert(/ALL INPUTS WIRED/.test(tid("flow-checks").textContent),
    "a clean compile does not say so in words");
});

test("unmapped splits into three statements, not one list", () => {
  const split = splitUnmapped([
    { key: "nodes.safety", detail: "the monitor gate is global", level: "warn" },
    { key: "cooling.setpoint_c", detail: "no target temperature", level: "note" },
    { key: "nodes.cloud", detail: "the hold releases itself", level: "note" },
  ]);
  eq(split.losses.length, 1, "a warn-level entry is a LOSS, not a note");
  eq(split.advisories.map((u) => u.key).join(","), "cooling.setpoint_c",
    "the rig advisory was filed under ANSWERED ANOTHER WAY, where it contradicts "
    + "its own heading");
  eq(split.notes.map((u) => u.key).join(","), "nodes.cloud",
    "a note about a wire is not an advisory about this rig");
});

await testAsync("each of the three lists gets its own heading on screen", async () => {
  act(() => {
    const s = useStore.getState();
    useStore.setState({
      flows: {
        ...s.flows,
        compiled: {
          plan: {}, structural: ["TARGET has no wire into CAPTURE LOOP"],
          issues: [{ text: "the guider is unwired", level: "warn" }],
          unmapped: [
            { key: "nodes.abort", detail: "no per-flow abort rule reaches the engine", level: "danger" },
            { key: "cooling.setpoint_c", detail: "this flow will run with no target temperature", level: "note" },
            { key: "nodes.cloud", detail: "the cloud hold releases itself", level: "note" },
          ],
        },
      } as never,
    } as never);
  });
  await settle();

  const text = (marker: string, heading: string): string => {
    const el = tid(marker);
    if (el == null) throw new Error(`the ${heading} list has no heading on screen at all`);
    return String(el.textContent);
  };
  assert(/WARNING/.test(text("flow-checks", "CHECKS")),
    "a warn-level check carries its tone and no word, so the night palette is the "
    + "only channel left");
  const losses = text("flow-losses", "NOT HONOURED BY A RUN");
  assert(/TARGET has no wire/.test(losses), "a structural refusal is missing");
  assert(/DANGER/.test(losses) && /no per-flow abort rule/.test(losses),
    "a danger-level loss lost its word or its sentence");
  assert(/no target temperature/.test(text("flow-advisories", "BEFORE YOU RUN")),
    "BEFORE YOU RUN is missing the rig advisory");
  assert(/cloud hold releases itself/.test(text("flow-notes", "ANSWERED ANOTHER WAY")),
    "ANSWERED ANOTHER WAY is missing its note");
});

// ------------------------------------------------------------------ tally
await act(async () => { root.unmount(); });

const total = passed + failed;
for (const f of failures) console.error(f);
console.log(`inspectorDom: ${passed}/${total} passed`);

export { passed, failed, total };
export const result = { passed, failed, total };
