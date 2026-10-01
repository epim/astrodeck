// framingCatalogPick.test.tsx - a WHERE catalogue result in the Target modal
// is picked with ONE tap (#492; spec 2026-09-23 flows mosaic, 2.2 and 2.4
// WHERE).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/framing/__tests__/framingCatalogPick.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT WAS WRONG. With "M 31" typed in WHERE's search on a phone, a tap on the
// "M31 Andromeda Galaxy" result did nothing, and a second tap picked it. The
// press took focus off the search field; the sheet leaves typing mode on that
// blur (`data-typing`), so `.tfs-sky` went from 40svh back to min(100vw,
// 52svh), 337.6 to 390 px on the 390 x 844 profile, and everything under it,
// the result list included, moved 52 px down before the press ended. The
// release landed on whatever was under the pointer by then, and no click
// reached the result (measured with Playwright, mouse and touchscreen alike:
// result y 527 before the press, 579 after).
//
// WHICH FIX HOLDS. Two were on the table. Keeping typing mode while focus
// moves WITHIN the search widget (a relatedTarget test, as CatalogSearch's own
// onBlur makes) holds only where a pressed button takes focus: Chromium, and
// Firefox off macOS. In WebKit (Safari, and every browser on an iPhone) and in
// Firefox on macOS a button is not click-focusable, so the press blurs the
// field to NOTHING: relatedTarget is null, which that test cannot tell from
// focus leaving for good (it is the same null CatalogSearch's onBlur already
// has to excuse, UX-06). The other fix holds in every engine: the result
// buttons cancel their `mousedown`, whose default action is what moves focus
// (on a touch screen too: the tap's compatibility mousedown, after touchend),
// so the field keeps focus through the press, nothing blurs and nothing
// moves, and the click lands on the result it was aimed at. CatalogSearch
// then gives the field's focus up once the pick has landed, so the sky grows
// back and a phone's keyboard closes, as they did after a pick before (focus
// had gone to the button, which the pick removed). That is the fix; the
// WebKit case below is what rules the other out.
//
// HOW THIS DRIVES IT. jsdom runs no default actions and has no layout, so the
// harness plays the browser's part, and says so:
//   * THE PRESS. A tap as a touch screen delivers it (pointerdown, pointerup,
//     then the compatibility mousedown), and then, ONLY IF that mousedown was
//     not cancelled, its default action as each engine runs it: Chromium
//     focuses the pressed button, WebKit blurs the field to nothing.
//   * THE RELEASE. Where mouseup and click land is decided by the layout at
//     release. The one input to the layout that moved in #492 is the sheet's
//     `data-typing` mark (framingLayout.test.tsx grades the CSS it drives:
//     40svh while typing, min(100vw, 52svh) otherwise). If the mark changed
//     between the press and the release, the list slid under the pointer and
//     the release lands on the row that slid there, modelled as the scroller
//     itself, which picks nothing. If it did not, it lands on the result.
// The one-tap step on the real page is H4-PROBE-LAYOUT's (W4).
//
// Every mutant below was run in a private scratch copy of ui/ (scratchpad
// H4-UFRAME-mut), never in the shared tree (#254), and the failure it
// produced is quoted verbatim.

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
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.HTMLCanvasElement.prototype.getContext = () => null;
win.URL.createObjectURL = () => "blob:stub";
win.URL.revokeObjectURL = () => {};
win.fetch = async (url: string) => { throw new Error(`no network in this fixture: ${url}`); };
win.WebSocket = class { close() {} addEventListener() {} send() {} };
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement", "HTMLSelectElement",
  "HTMLTextAreaElement", "Element", "Node", "Event", "CustomEvent", "MouseEvent", "PointerEvent",
  "KeyboardEvent", "FocusEvent", "localStorage", "sessionStorage", "getComputedStyle", "matchMedia",
  "ResizeObserver", "requestAnimationFrame", "cancelAnimationFrame", "Image", "URL", "fetch", "Blob",
  "WebSocket", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------- imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { FLOWS_INIT } = await import("../../flowsSlice");
const { NODE_DEFS } = await import("../../nodeDefs");
const { raHms, decDms } = await import("../../QuickFlow");
const { api } = await import("../../../../api");
const { framingApi, framingTiming } = await import("../framingApi");
const Sheet = (await import("../TargetFramingSheet")).default;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
/** Each case mounts a fresh sheet and unmounts it in `finally`. */
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { mount(); await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
  finally { unmount(); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }

// ------------------------------------------------------------------ the mount
// Framed on another object first, so a pick that lands is plain in NAME, RA
// and DEC.
(framingApi as any).mosaic = () => new Promise(() => {});
(framingApi as any).compileDraft = () => new Promise(() => {});
framingTiming.settleMs = 0;
/** WHERE's catalogue: M31 as the probe's search found it. */
const M31_ENTRY = {
  id: "M31", name: "Andromeda Galaxy", type: "galaxy", ra_hours: 0.7123, dec_deg: 41.269,
  mag: 3.4, size_arcmin: 178,
};
(api as any).get = async (path: string) => {
  if (path.startsWith("/api/catalog?")) return { results: [M31_ENTRY], notes: [] };
  throw new Error(`no network in this fixture: ${path}`);
};
const graph = {
  nodes: [
    { id: "n2", type: "target", x: 0, y: 0, params: {
      ...NODE_DEFS.target.params, name: "NGC 891", ra: "02h 22m 33s", dec: "+42 20 57",
      rows: 2, cols: 3, overlap: 25, fovX: 2.0, fovY: 1.33, rotation: 0, angle: "Rotate to PA",
      counts: "Accepted subs" } },
    { id: "cy", type: "cycle", x: 260, y: 0, params: { ...NODE_DEFS.cycle.params } },
  ],
  edges: [{ id: "a", from: "n2", fromPort: "target", to: "cy", toPort: "run" }],
};
useStore.setState({
  principal: { role: "operator", email: null, caps: ["view.status", "view.site_derived"] },
  wsConnected: false, status: null, config: null, site: null,
  flows: { ...FLOWS_INIT, graph,
    record: { id: "f1", name: "Andromeda mosaic", folder: "", tagline: "", graph, created_ts: 0,
      updated_ts: 0, last_run: null, last_result: "", readonly: false } },
} as any);

const container = win.document.getElementById("root");
const root = createRoot(container);
function mount(): void {
  act(() => { root.render(null); });
  act(() => { root.render(createElement(Sheet, { nodeId: "n2", onClose: () => {} })); });
}
function unmount(): void { act(() => { root.render(null); }); }
async function flush(ms = 5): Promise<void> {
  await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
}
const doc = win.document;
const q = (id: string) => doc.querySelector(`[data-testid="${id}"]`) as any;
const typing = (): string | null => q("target-framing-sheet")?.getAttribute("data-typing") ?? null;
const searchField = () => doc.querySelector('[aria-label="Search the target catalog"]') as any;
const field = (id: string) => (doc.querySelector(`#${id}`) as any)?.value as string | undefined;
function typeInto(el: any, value: string): void {
  assert(el, "no field to type into");
  const setter = Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!;
  act(() => { setter.call(el, value); el.dispatchEvent(new win.Event("input", { bubbles: true })); });
}
/** The operator's steps before the tap: focus WHERE's search, type "M 31",
 *  and wait out the search's 250 ms debounce for its answer. */
async function searchM31(): Promise<any> {
  const input = searchField();
  assert(input, "no catalogue search in WHERE");
  act(() => { input.focus(); });
  typeInto(input, "M 31");
  await flush(320);
  const btn = Array.from(doc.querySelectorAll("button") as any[])
    .find((b: any) => /M31/.test(b.textContent) && /Andromeda Galaxy/.test(b.textContent)) as any;
  assert(btn, `the search listed no M31 result: ${JSON.stringify(String(q("framing-where")?.textContent).slice(0, 200))}`);
  assert(typing() === "true", `precondition: the sheet is not in typing mode with the search focused (data-typing ${JSON.stringify(typing())})`);
  return btn;
}

type Engine = "chromium" | "webkit";
const ptr = (type: string) => new win.PointerEvent(type,
  { bubbles: true, cancelable: true, pointerType: "touch", isPrimary: true, pointerId: 1 });
const mouse = (type: string) => new win.MouseEvent(type, { bubbles: true, cancelable: true, detail: 1 });

/** One tap on `el` as a touch screen delivers it, with the browser's default
 *  action for the compatibility mousedown played by the harness (see the
 *  header), and the release landing where the layout at release puts it.
 *  Answers the `data-typing` mark after each step, the way #492's trace was
 *  taken, and whether the mousedown was cancelled. */
function tap(el: any, engine: Engine): { trace: string[]; cancelled: boolean; landed: "result" | "moved" } {
  const trace: string[] = [];
  const at = typing();
  const note = (step: string) => trace.push(`${step} data-typing ${JSON.stringify(typing())}`);
  act(() => { el.dispatchEvent(ptr("pointerdown")); });
  note("pointerdown");
  act(() => { el.dispatchEvent(ptr("pointerup")); });
  note("pointerup");
  let cancelled = false;
  act(() => { cancelled = !el.dispatchEvent(mouse("mousedown")); });
  if (!cancelled) {
    act(() => {
      if (engine === "chromium") el.focus();
      else (doc.activeElement as any)?.blur?.();
    });
  }
  note(`mousedown (${cancelled ? "cancelled" : `not cancelled, ${engine} default action run`})`);
  // The release: the layout at release decides where it lands.
  const landed = typing() === at ? "result" : "moved";
  const target = landed === "result" ? el : q("framing-scroller");
  act(() => { target.dispatchEvent(mouse("mouseup")); });
  act(() => { target.dispatchEvent(mouse("click")); });
  note(`release (landed on ${landed === "result" ? "the result" : "the row that slid under the pointer"})`);
  return { trace, cancelled, landed };
}

/** What a landed pick writes: WHERE's NAME, RA and DEC from the entry, and
 *  the search cleared. */
function assertPicked(trace: string[]): void {
  const want = { name: M31_ENTRY.name, ra: raHms(M31_ENTRY.ra_hours), dec: decDms(M31_ENTRY.dec_deg) };
  const got = { name: field("tfs-name"), ra: field("tfs-ra"), dec: field("tfs-dec") };
  assert(JSON.stringify(got) === JSON.stringify(want),
    `one tap did not pick M31: WHERE reads ${JSON.stringify(got)}, search ${JSON.stringify(searchField()?.value)}; trace ${JSON.stringify(trace)}`);
  assert(searchField()?.value === "", `the pick did not clear the search: ${JSON.stringify(searchField()?.value)}`);
}

// ======================================================================
// One tap, on the engine the operator's phone and the probe run (Chromium,
// touch), where the pressed button would take focus if the press were not
// cancelled; and on WebKit, where the press would blur the field to nothing.
//
// Mutant "result takes focus on press" (CatalogSearch's result buttons no
// longer cancel their mousedown, which is #492 as reported): 1/3 passed, both
// tap cases red:
//   x one tap on a result picks M31 on Chromium, and typing mode holds until
//     the pick lands: the press moved the layout before the release:
//     ["pointerdown data-typing \"true\"","pointerup data-typing \"true\"","mousedown
//     (not cancelled, chromium default action run) data-typing null","release
//     (landed on the row that slid under the pointer) data-typing null"]
//   x one tap on a result picks M31 on WebKit, where a button is not
//     click-focusable and the press blurs the field to nothing: the press
//     moved the layout before the release: ["pointerdown data-typing
//     \"true\"","pointerup data-typing \"true\"","mousedown (not cancelled,
//     webkit default action run) data-typing null","release (landed on the
//     row that slid under the pointer) data-typing null"]
//
// Mutant "pick keeps focus" (CatalogSearch's pick no longer gives the field's
// focus up): 1/3 passed, both tap cases red:
//   x one tap on a result picks M31 on Chromium, and typing mode holds until
//     the pick lands: after the pick the search field still has focus, so a
//     phone keeps its keyboard up over the picked target
//   x one tap on a result picks M31 on WebKit, where a button is not
//     click-focusable and the press blurs the field to nothing: after the pick
//     the search field still has focus, so a phone keeps its keyboard up over
//     the picked target
//
// Mutant "clear typing on any blur" (the scroller's onBlur clears typing
// whatever focus moved to, `setTyping(false)`): 3/3 PASSED, and
// framingLayout.test.tsx 5/5 passed under it too. It is an EQUIVALENT mutant
// with this fix, which no test can kill: the press blurs nothing, so no blur
// handler is on the tap's path at all; every focus move inside the scroller is
// followed by the scroller's onFocus, which derives the mark again from where
// focus landed; and outside the scroller there is no text field for the
// relatedTarget test to find, so leaving the scroller clears the mark either
// way. It would be the guarding mutant of the relatedTarget fix, which the
// WebKit case rules out (below). What guards THIS fix is "result takes focus
// on press".
for (const engine of ["chromium", "webkit"] as const) {
  const title = engine === "chromium"
    ? "one tap on a result picks M31 on Chromium, and typing mode holds until the pick lands"
    : "one tap on a result picks M31 on WebKit, where a button is not click-focusable and the press blurs the field to nothing";
  // The WebKit case is the one that rules out a relatedTarget fix: its press,
  // uncancelled, blurs the field with relatedTarget null.
  //
  // Mutant "relatedTarget instead" (the #492 fix swapped for the other one:
  // the result buttons take focus on press again, and the scroller keeps
  // typing mode while focus moves within the search widget, on blur and on
  // focus, and clears it when a pick lands): 2/3 passed, the Chromium case
  // GREEN, the WebKit case red:
  //   x one tap on a result picks M31 on WebKit, where a button is not
  //     click-focusable and the press blurs the field to nothing: the press
  //     moved the layout before the release: ["pointerdown data-typing
  //     \"true\"","pointerup data-typing \"true\"","mousedown (not cancelled,
  //     webkit default action run) data-typing null","release (landed on the
  //     row that slid under the pointer) data-typing null"]
  // Under "result takes focus on press" this case failed the same way.
  await test(title, async () => {
    const btn = await searchM31();
    const input = searchField();
    const r = tap(btn, engine);
    assert(r.landed === "result", `the press moved the layout before the release: ${JSON.stringify(r.trace)}`);
    assert(r.trace.slice(0, 3).every((s) => s.endsWith('data-typing "true"')),
      `typing mode did not hold through the press: ${JSON.stringify(r.trace)}`);
    assertPicked(r.trace);
    // Once the pick has landed, the field gives its focus up: a phone's
    // keyboard closes, and the sky grows back to show the picked object.
    assert(doc.activeElement !== input,
      "after the pick the search field still has focus, so a phone keeps its keyboard up over the picked target");
    assert(typing() === null,
      `after the pick the sheet is still in typing mode (data-typing ${JSON.stringify(typing())}), so the sky stays shrunk over the picked target`);
  });
}

// Control: focus that really leaves the scroller still leaves typing mode.
// Graded mid-search, with the result list up, so the fix above cannot have
// made the search widget hold typing mode against a real focus-out.
//
// Mutant "blur never clears" (the scroller's onBlur a no-op): 0/3 passed:
//   x one tap on a result picks M31 on Chromium, and typing mode holds until
//     the pick lands: after the pick the sheet is still in typing mode
//     (data-typing "true"), so the sky stays shrunk over the picked target
//   x one tap on a result picks M31 on WebKit, where a button is not
//     click-focusable and the press blurs the field to nothing: after the pick
//     the sheet is still in typing mode (data-typing "true"), so the sky stays
//     shrunk over the picked target
//   x control: moving focus from the search to outside the scroller leaves
//     typing mode: focus moved to the header's CANCEL and the sheet is still
//     in typing mode (data-typing "true")
await test("control: moving focus from the search to outside the scroller leaves typing mode", async () => {
  await searchM31();
  const cancel = q("framing-header")?.querySelector("button") as any;
  assert(cancel && !q("framing-scroller").contains(cancel), "precondition: the header's button is not outside the scroller");
  act(() => { cancel.focus(); });
  assert(typing() === null,
    `focus moved to the header's CANCEL and the sheet is still in typing mode (data-typing ${JSON.stringify(typing())})`);
  // The search itself is untouched: nothing was picked.
  assert(field("tfs-name") === "NGC 891", `a focus-out picked something: NAME reads ${JSON.stringify(field("tfs-name"))}`);
});

// ------------------------------------------------------------------ report
unmount();
dom.window.close();
const total = passed + failed;
console.log(`framingCatalogPick.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
export default result;
