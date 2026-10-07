// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w16PanelCsv.test.tsx - the Target modal's EXPORT PANELS and IMPORT PANELS
// (#178, WP-130, backlog wave 16).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/framing/__tests__/w16PanelCsv.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE UI ONLY MOVES TEXT. Every number in the panel file and the angle
// convention (AstroDeck's rotation runs north through west, the file's
// Position Angle (East) north through east) live in one server module,
// catalog/panel_csv.py, which server/tests/test_w16_panel_csv.py grades. So
// this file grades the movement and nothing else: what the export posts (the
// layout and the block's skip text), that it saves what the server wrote and
// shows the server's convention sentence, that an import posts the file's
// text and merges the server's draft into the modal's draft through
// `draftFromParams`/`framingPatch` (so the normal re-frame question at DONE
// still applies), and that a locked or refused press says why. No angle is
// converted and no convention is stated anywhere in the UI, so no test here
// could be satisfied by a client copy of either.
//
// The sheet is wired to `GridSection` by a host that passes the three props
// the section takes (`panelCsv.request`, `.exportLock`, `.onImported`) from
// framingModel (`panelCsvRequest`, `panelCsvExportLock`,
// `mergeImportedPanels`); the host below is that wiring, written out.
//
// MUTANTS, each run from a byte backup of the file named, inside this
// worktree, sha256 compared on restore and the mutant text grepped out:
//   U1 "import drops skip" (framingModel PANEL_CSV_KEYS without "skip"). Observed:
//     w16PanelCsv.test: 11/14 passed
//     x an import merges the file's geometry and leaves the block's own settings alone: a file with no missing cell CLEARS the skip the block had: expected "", got "1-2"
//   U2 "export posts no skip" (panelCsvRequest sends skip: ""). Observed:
//     w16PanelCsv.test: 12/14 passed
//     x EXPORT PANELS posts the layout, saves what the server wrote and shows the server's sentence once: the skip text went with it: expected "1-2", got ""
//   U3 "any angle kept" (mergeImportedPanels writes next.angle = mode). Observed:
//     w16PanelCsv.test: 13/14 passed
//     x an imported grid has an angle: ANY ANGLE becomes ROTATE TO, or CAMERA FIXED AT with no rotator: with a rotator: expected "Rotate to PA", got "Any angle"
//   U4 "no-angle lock dropped" (panelCsvExportLock without its rotation_deg === null line). Observed:
//     w16PanelCsv.test: 12/14 passed
//     x every case the server refuses to write is a lock with its reason before the press: any angle: expected "the file carries a position angle: choose a camera angle first", got null
//   U5 "stale note" (GridSection importPanels without its setNote(null)). Observed:
//     w16PanelCsv.test: 13/14 passed
//     x a file the server refuses changes nothing, says why, and the last file's note is gone: the first file's convention and warnings are not left beside the refusal of the second
//   U6 "unread answer merged anyway" (GridSection without its `if (!read.ok)` line). Observed:
//     w16PanelCsv.test: 13/14 passed
//     x an answer the modal cannot read changes nothing and names the key: the key is named: expected ["could not import the panels: the draft's rotation is not a number or text"], got ["could not import the panels: Cannot read properties of undefined (reading 'draft')"]
//   U8 "export lock ignored" (GridSection passes reason={null} to EXPORT PANELS). Observed:
//     w16PanelCsv.test: 13/14 passed
//     x a locked EXPORT PANELS says why when pressed and posts nothing: EXPORT PANELS is live with no angle
//   U7 "camera field not imported" (PANEL_CSV_KEYS without "fovX", "fovY", "fovFrom"; the verifier found it
//     SURVIVING an earlier form of the merge test, which looped over the list it was grading). Observed:
//     w16PanelCsv.test: 13/14 passed
//     x an import merges the file's geometry and leaves the block's own settings alone: the keys an import writes: expected ["cols","dec","fovFrom","fovX","fovY","overlap","ra","rotation","rows","skip"], got ["cols","dec","overlap","ra","rotation","rows","skip"]
//   U11 "merge skips fovX" (mergeImportedPanels copies every key but "fovX"). Observed:
//     w16PanelCsv.test: 13/14 passed
//     x an import merges the file's geometry and leaves the block's own settings alone: the file's geometry, key by key: expected [ra,dec,2,2,20,2,1.33,"imported from a panel file",330,""], got [ra,dec,2,2,20,1.5,1.33,"imported from a panel file",330,""]  (ra and dec elided here: dec holds a degree sign)

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
win.fetch = async (url: string) => { throw new Error(`no network in this fixture: ${url}`); };
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement", "HTMLSelectElement",
  "HTMLTextAreaElement", "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "FocusEvent", "localStorage", "sessionStorage", "getComputedStyle", "matchMedia",
  "requestAnimationFrame", "cancelAnimationFrame", "URL", "fetch", "Blob", "File", "FileReader",
  "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------- imports
const { createElement, act, useState } = await import("react");
const { createRoot } = await import("react-dom/client");
const { NODE_DEFS } = await import("../../nodeDefs");
const model = await import("../framingModel");
const { GridSection, panelCsvApi } = await import("../sections/GridSection");
type FramingDraft = import("../framingModel").FramingDraft;
type RigBlock = import("../framingModel").RigBlock;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
  finally { act(() => { root.render(null); }); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    throw new Error(`${msg}: expected ${JSON.stringify(want)}, got ${JSON.stringify(got)}`);
  }
}

const container = win.document.getElementById("root");
const root = createRoot(container);
const doc = win.document;
const q = (id: string) => doc.querySelector(`[data-testid="${id}"]`) as any;
const qa = (id: string) => Array.from(doc.querySelectorAll(`[data-testid="${id}"]`) as any[]) as any[];
const locked = (b: any) => b?.getAttribute("aria-disabled") === "true";
function buttonByText(text: string): any {
  return Array.from(doc.querySelectorAll("button") as any[]).find((b: any) => b.textContent.includes(text));
}
function click(el: any): void {
  assert(el, "no element to click");
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}
async function flush(ms = 10): Promise<void> {
  await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
}

// ------------------------------------------------------------------ fixtures
/** A 3 x 2 of 2.0 x 1.33 deg at 25%, its panel 1-2 skipped, rotation 30,
 *  as the sheet's draft holds it. The centre is M31's, a public object. */
const STORED = {
  ...NODE_DEFS.target.params, name: "M31", ra: "00h 42m 44s", dec: "+41 00 00",
  rows: 2, cols: 3, overlap: 25, fovX: 2.0, fovY: 1.33, rotation: 30,
  angle: "Rotate to PA", counts: "Accepted subs", frameAnchor: "", skip: "1-2",
};
const draft0 = (over: Record<string, string | number> = {}): FramingDraft =>
  model.draftFromParams({ ...STORED, ...over });

/** The server's answer to a file of a 2 x 2 at rotation 330 with one warning,
 *  in the node's own shape. The UI does not care that 330 is what a file at
 *  position angle 30 means: it merges what it is handed. */
const IMPORTED = {
  draft: {
    ra: "00h 42m 44.30000s", dec: "+41\u00b0 16' 09.0000\"", rows: 2, cols: 2, overlap: 20.0,
    fovX: 2.0, fovY: 1.33, fovFrom: "imported from a panel file", rotation: 330.0, skip: "",
  },
  warnings: ["the file has no unit for width and height", "second warning"],
  convention: "THE SERVER'S SENTENCE about the angle, which the UI shows and does not own.",
};

// ------------------------------------------------------------- the network
let exportCalls: any[] = [];
let importCalls: string[] = [];
let saved: Array<[string, string]> = [];
let exportAnswer: any = { text: "Pane,RA\n1,2\n", convention: "THE SERVER'S SENTENCE about the angle" };
let importAnswer: any = IMPORTED;
let importFails: Error | null = null;
const fakeExport = async (req: any) => { exportCalls.push(req); return exportAnswer; };
(panelCsvApi as any).importCsv = async (text: string) => {
  importCalls.push(text);
  if (importFails) throw importFails;
  return importAnswer;
};
(panelCsvApi as any).save = (name: string, text: string) => { saved.push([name, text]); };
function reset(): void {
  exportCalls = []; importCalls = []; saved = [];
  exportAnswer = { text: "Pane,RA\n1,2\n", convention: "THE SERVER'S SENTENCE about the angle" };
  importAnswer = IMPORTED; importFails = null;
  (panelCsvApi as any).exportCsv = fakeExport;
}

// -------------------------------------------------------------------- host
/** What the sheet does with the three props, written out: the model decides
 *  the request, the lock and the merge. */
const seen: { draft: FramingDraft } = { draft: draft0() };
let explained: string[] = [];
function Host(props: { initial: FramingDraft; rig?: RigBlock | null; wired?: boolean }) {
  const [draft, setDraft] = useState(props.initial);
  seen.draft = draft;
  const { rows, cols } = model.gridOf(draft);
  return createElement(GridSection, {
    cols, rows, overlap: Math.round(model.layoutOf(draft).overlap * 100),
    lock: null, cameraLine: model.cameraFieldLine(draft), matchLock: null, drift: null,
    onCols() {}, onRows() {}, onOverlap() {}, onMatchCamera() {},
    panelCsv: props.wired === false ? undefined : {
      request: model.panelCsvRequest(draft),
      exportLock: model.panelCsvExportLock(draft),
      onImported: (imported: any) => setDraft((d) => model.mergeImportedPanels(d, imported, props.rig)),
    },
    explain: (r: string) => explained.push(r),
  } as any);
}
function mount(initial: FramingDraft, o: { rig?: RigBlock | null; wired?: boolean } = {}): void {
  explained = []; reset(); seen.draft = initial;
  act(() => { root.render(createElement(Host, { initial, ...o })); });
}
async function pick(text: string, name = "plan.csv"): Promise<void> {
  const input = q("framing-panel-csv-file");
  assert(input, "no file input");
  const file = new win.File([text], name, { type: "text/csv" });
  Object.defineProperty(input, "files", { value: [file], configurable: true });
  act(() => { input.dispatchEvent(new win.Event("change", { bubbles: true })); });
  await flush();
}
const RIG = (over: Partial<RigBlock> = {}): RigBlock => ({
  fov_deg: [2, 1.33], fov_from: "profile", has_rotator: true,
  hop_s: null, hop_samples: 0, hop_measured: false, ...over,
});

// ======================================================================
// the model: the request, the lock, the merge

await test("the export request is the layout and the block's skip text, in the server's own words", () => {
  const req = model.panelCsvRequest(draft0());
  eq(req, {
    ra_hours: model.parseRaHours("00h 42m 44s") as number, dec_deg: 41, rows: 2, cols: 3, overlap: 0.25,
    rotation_deg: 30, fov_x_deg: 2, fov_y_deg: 1.33, skip: "1-2",
  }, "the request");
  assert(!("anchor" in (req as object)), "the export posts no anchor");
  eq(model.panelCsvExportLock(draft0()), null, "an unlocked draft");
});

await test("every case the server refuses to write is a lock with its reason before the press", () => {
  eq(model.panelCsvExportLock(draft0({ ra: "", dec: "" })), model.NO_COORDINATES_TO_EXPORT, "no coordinates");
  eq(model.panelCsvExportLock(draft0({ fovX: 0, fovY: 0 })), model.NO_FIELD_TO_EXPORT, "no camera field");
  eq(model.panelCsvExportLock(draft0({ rotation: -1, angle: "Any angle" })), model.NO_ANGLE_TO_EXPORT, "any angle");
  eq(model.panelCsvExportLock(draft0({ skip: "1-1, 1-2, 1-3, 2-1, 2-2, 2-3" })), model.NOTHING_TO_EXPORT, "every panel skipped");
  const lockedDrafts: Array<Record<string, string | number>> = [{ ra: "" }, { fovX: 0 }, { rotation: -1, angle: "Any angle" }];
  for (const over of lockedDrafts) {
    eq(model.panelCsvRequest(draft0(over)), null, `no request while locked: ${JSON.stringify(over)}`);
  }
  // An unreadable skip entry names no panel, so it skips none (as the server reads it).
  eq(model.panelCsvExportLock(draft0({ skip: "9-9" })), null, "an entry that names no panel");
});

await test("an import merges the file's geometry and leaves the block's own settings alone", () => {
  // A block that differs from the file on EVERY key the file carries, so a
  // key that is not merged shows as the block's own value.
  const base = draft0({ passes: 3, minVisit: 12, order: "Grid order", rows: 3, fovX: 1.5, fovY: 1.0, fovFrom: "profile" });
  const d = IMPORTED.draft;
  const geometry = (x: FramingDraft) =>
    [x.ra, x.dec, x.rows, x.cols, x.overlap, x.fovX, x.fovY, x.fovFrom, x.rotation, x.skip];
  const fileGeometry = [d.ra, d.dec, d.rows, d.cols, d.overlap, d.fovX, d.fovY, d.fovFrom, d.rotation, d.skip];
  geometry(base).forEach((v, i) => assert(v !== fileGeometry[i], `the block and the file agree on key ${i}: a merge of it would not show`));
  const merged = model.mergeImportedPanels(base, IMPORTED.draft, RIG());
  // The keys are written out here, and each value is read from the answer by
  // name, not by looping over PANEL_CSV_KEYS: a key dropped from that list
  // (the camera field's, say) would otherwise drop out of this check with it.
  eq([...model.PANEL_CSV_KEYS].sort(),
    ["cols", "dec", "fovFrom", "fovX", "fovY", "overlap", "ra", "rotation", "rows", "skip"], "the keys an import writes");
  eq(geometry(merged), fileGeometry, "the file's geometry, key by key");
  eq(merged.skip, "", "a file with no missing cell CLEARS the skip the block had");
  eq(merged.name, "M31", "the name");
  eq([merged.passes, merged.minVisit, merged.order], [3, 12, "Grid order"], "the run settings");
  eq(model.layoutOf(merged).rotation_deg, 330, "the layout reads the file's rotation, as given");
  eq(model.gridOf(merged), { rows: 2, cols: 2 }, "the grid");
});

await test("an imported grid has an angle: ANY ANGLE becomes ROTATE TO, or CAMERA FIXED AT with no rotator", () => {
  const any = draft0({ rotation: -1, angle: "Any angle" });
  eq(model.angleOf(model.mergeImportedPanels(any, IMPORTED.draft, RIG())), "Rotate to PA", "with a rotator");
  eq(model.angleOf(model.mergeImportedPanels(any, IMPORTED.draft, null)), "Rotate to PA", "a rig nobody asked about");
  eq(model.angleOf(model.mergeImportedPanels(any, IMPORTED.draft, RIG({ has_rotator: false }))), "Camera fixed at PA", "no rotator");
  eq(model.angleOf(model.mergeImportedPanels(draft0({ angle: "Camera fixed at PA" }), IMPORTED.draft, RIG())),
    "Camera fixed at PA", "a mode the operator chose stays");
  // The grid has an angle, so DONE is not locked for lack of one.
  eq(model.gridAngleLock(model.mergeImportedPanels(any, IMPORTED.draft, RIG())), null, "no angle owed");
});

await test("the merged draft goes through framingPatch and the usual re-frame question", () => {
  const merged = model.mergeImportedPanels(draft0(), IMPORTED.draft, RIG());
  const patch = model.framingPatch(STORED as any, merged);
  eq([patch.cols, patch.rotation, patch.skip], [2, 330, ""], "the patch DONE writes");
  assert(!("rows" in patch), "rows (2 before, 2 after) is not written");
  assert(!("name" in patch) && !("passes" in patch), "nothing the file does not carry is written");
  const view = { request: null, answer: null, offline: true, canViewSiteDerived: false };
  const asked = model.reframeDecision({ stored: STORED as any, draft: merged, progress: { banked: 12 }, view });
  assert(asked.ask, "a new grid over banked frames asks before it commits");
  assert(String(asked.question).includes("3x2 to 2x2"), `the question names both grids: ${asked.question}`);
  const quiet = model.reframeDecision({ stored: STORED as any, draft: merged, progress: { banked: 0 }, view });
  assert(!quiet.ask, "nothing banked asks nothing");
});

await test("an answer that is not a draft is named, key by key", () => {
  const bad = (v: unknown) => { const r = model.readPanelCsvImport(v); assert(!r.ok, `read ${JSON.stringify(v)}`); return (r as any).why as string; };
  assert(model.readPanelCsvImport(IMPORTED).ok, "the good answer reads");
  assert(bad(null).includes("not an object"), "null");
  assert(bad({}).includes("no draft"), "no draft");
  assert(bad({ ...IMPORTED, draft: { ...IMPORTED.draft, rotation: "330" as any, rows: null } }).includes("rows"), "a null number");
  assert(bad({ ...IMPORTED, draft: { ...IMPORTED.draft, overlap: NaN } }).includes("overlap"), "a NaN");
  assert(bad({ ...IMPORTED, draft: { ra: "x" } }).includes("dec"), "a missing key");
  assert(bad({ ...IMPORTED, warnings: [1] }).includes("warnings"), "a warning that is not text");
  assert(bad({ ...IMPORTED, convention: 5 }).includes("convention"), "no convention");
});

// ======================================================================
// the section

await test("without the panel file's props the section has no panel file buttons", () => {
  mount(draft0(), { wired: false });
  assert(!buttonByText("EXPORT PANELS") && !buttonByText("IMPORT PANELS"), "buttons with nothing to drive them");
  assert(!q("framing-panel-csv-file"), "a file input with nothing to read it");
});

await test("a locked EXPORT PANELS says why when pressed and posts nothing", async () => {
  mount(draft0({ rotation: -1, angle: "Any angle" }));
  const btn = buttonByText("EXPORT PANELS");
  assert(locked(btn), "EXPORT PANELS is live with no angle");
  eq(btn.getAttribute("title"), model.NO_ANGLE_TO_EXPORT, "its reason");
  click(btn);
  await flush();
  eq(explained, [model.NO_ANGLE_TO_EXPORT], "pressing it says why");
  eq(exportCalls.length, 0, "nothing posted");
  assert(!q("framing-panel-csv-note"), "no note for a press that did nothing");
  assert(!locked(buttonByText("IMPORT PANELS")), "IMPORT PANELS needs no angle: the file brings its own");
});

await test("EXPORT PANELS posts the layout, saves what the server wrote and shows the server's sentence once", async () => {
  mount(draft0());
  click(buttonByText("EXPORT PANELS"));
  await flush();
  eq(exportCalls, [model.panelCsvRequest(draft0())], "what was posted");
  eq(exportCalls[0].skip, "1-2", "the skip text went with it");
  eq(saved, [[model.PANEL_CSV_FILENAME, "Pane,RA\n1,2\n"]], "the file saved is the server's text, byte for byte");
  eq(qa("framing-panel-csv-convention").map((e) => e.textContent), ["THE SERVER'S SENTENCE about the angle"], "the convention, once");
  eq(qa("framing-panel-csv-warning").length, 0, "an export has no warnings");
  eq(explained, [], "nothing was refused");
});

await test("a refused export says the server's reason and saves nothing", async () => {
  mount(draft0());
  (panelCsvApi as any).exportCsv = async () => { throw new Error("every panel is skipped, so there is nothing to write"); };
  click(buttonByText("EXPORT PANELS"));
  await flush();
  eq(explained, ["could not export the panels: every panel is skipped, so there is nothing to write"], "the reason");
  eq(saved.length, 0, "nothing saved");
  assert(!q("framing-panel-csv-note"), "no note");
});

await test("IMPORT PANELS posts the file's text, merges the draft and shows the convention and each warning once", async () => {
  mount(draft0());
  await pick("Pane,RA,DEC\n1,2,3\n");
  eq(importCalls, ["Pane,RA,DEC\n1,2,3\n"], "the file's text, as it was");
  eq(seen.draft.rows, 2, "rows merged");
  eq(seen.draft.cols, 2, "columns merged");
  eq(seen.draft.rotation, 330, "the rotation as the server gave it");
  eq(seen.draft.skip, "", "the skip the file implies");
  eq(seen.draft.name, "M31", "the block's name is its own");
  eq(model.angleOf(seen.draft), "Rotate to PA", "the angle mode");
  eq(qa("framing-panel-csv-convention").map((e) => e.textContent), [IMPORTED.convention], "the convention, once");
  eq(qa("framing-panel-csv-warning").map((e) => e.textContent), IMPORTED.warnings, "each warning, once");
  eq(explained, [], "nothing refused");
});

await test("the next press replaces the last block: warnings do not outlive the file they were about", async () => {
  mount(draft0());
  await pick("x");
  eq(qa("framing-panel-csv-warning").length, 2, "warnings after the import");
  click(buttonByText("EXPORT PANELS"));
  await flush();
  eq(qa("framing-panel-csv-warning").length, 0, "no warnings after the export that followed");
  eq(qa("framing-panel-csv-convention").length, 1, "still one convention");
});

await test("a file the server refuses changes nothing, says why, and the last file's note is gone", async () => {
  mount(draft0());
  await pick("x");
  assert(q("framing-panel-csv-note"), "the first file's note is up");
  importFails = new Error("the file has no RA, DEC column; it needs RA, DEC, width, height, Row, Column");
  const before = JSON.stringify(seen.draft);
  await pick("Name,Notes\nx,y\n");
  eq(explained, ["could not import the panels: the file has no RA, DEC column; it needs RA, DEC, width, height, Row, Column"], "the reason");
  eq(JSON.stringify(seen.draft), before, "the draft is as the first file left it");
  assert(!q("framing-panel-csv-note"), "the first file's convention and warnings are not left beside the refusal of the second");
});

await test("an answer the modal cannot read changes nothing and names the key", async () => {
  mount(draft0());
  importAnswer = { ...IMPORTED, draft: { ...IMPORTED.draft, rotation: null } };
  const before = JSON.stringify(seen.draft);
  await pick("x");
  eq(explained, ["could not import the panels: the draft's rotation is not a number or text"], "the key is named");
  eq(JSON.stringify(seen.draft), before, "the draft is as it was");
});

// ------------------------------------------------------------------ report
const total = passed + failed;
console.log(`w16PanelCsv.test: ${passed}/${total} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total };
export default result;
