// sessionSheets.test.tsx - the SESSION hub's sheet registry, and the file whose
// existence would silently empty it.
//
//   Run directly:  npx tsx src/next/hubs/session/__tests__/sessionSheets.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE SHADOWING TRAP, WHICH IS WHY THIS FILE EXISTS.
//
// `hubs/index.ts` composes the global registry with
// `import { sheets } from "./session/sheets"`. That specifier has two possible
// answers: the FILE `session/sheets.ts` and the DIRECTORY `session/sheets/`
// (via its `index.ts`). Every resolver in this toolchain - tsc's `bundler`
// mode, Vite, tsx - prefers the file. So while T0.1's empty placeholder
// `session/sheets.ts` existed beside a fully-populated `session/sheets/`
// directory, the SESSION hub registered ZERO sheets: FILES, the night report,
// the archive and the plan editor all resolved to `MissingSheet`, the build was
// clean, the types were clean, and the only symptom was a sheet that said it
// had not been built yet.
//
// Deleting the placeholder is the fix. Asserting it STAYS deleted is this test,
// because re-creating it is a one-line mistake - a merge, a "the export must
// exist now" reflex, a scaffold - with no compile error and no runtime error to
// find it by.
//
// The other half is the names. A registry that resolves to an object is not
// evidence it holds the right screens.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// The sheets reach area roots that import their own stylesheet (e.g.
// `session/report/report.css`, `session/flows/tonight/tonight.css`). Node has
// no idea what a `.css` file is, so a synchronous load hook answers with an
// empty module - the same stub `shellDom.test.tsx` and `hubBoundary.test.tsx`
// install.
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
// The sheets mount `views/SequenceView`, `views/ReportView` and the gallery
// panels, and the module graph under them reads `window.location` at import
// time (`api.ts` via `lib/base.ts`). Globals first, imports after.
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;

let viewportW = 390;
win.matchMedia = (q: string) => {
  const m = /min-width:\s*(\d+)px/.exec(q);
  return {
    matches: m ? viewportW >= Number(m[1]) : false,
    addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {},
  };
};
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };
win.scrollTo = function () {};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;
g.fetch = async () => ({
  ok: false, status: 404, statusText: "Not Found",
  headers: { get: () => "application/json" },
  json: async () => ({}),
});

// ------------------------------------------------------------------ imports
const { existsSync, readFileSync } = await import("node:fs");
const { fileURLToPath } = await import("node:url");
const { dirname, join } = await import("node:path");

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { sheets } = await import("../sheets");
const { PLAN_EDITOR_PHONE_REASON } = await import("../sheets/planEditor");

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

const here = dirname(fileURLToPath(import.meta.url));
const hubDir = join(here, "..");

// ====================================================== 1. the shadowing file

test("session/sheets.ts does not exist - it would shadow session/sheets/", () => {
  assert(!existsSync(join(hubDir, "sheets.ts")),
    "session/sheets.ts is back. `import \"./session/sheets\"` resolves to the FILE, "
    + "so the whole sheets/ directory - files, report, archive, planEditor - stops "
    + "being registered, with no compile error and no runtime error. Delete it; the "
    + "registry lives in session/sheets/index.ts.");
  assert(!existsSync(join(hubDir, "sheets.tsx")),
    "same trap, .tsx spelling");
  assert(existsSync(join(hubDir, "sheets", "index.ts")),
    "precondition: the registry this test is about is where it says it is");
});

// ========================================================= 2. the ten names

test("the registry holds exactly the SESSION hub's ten sheets", () => {
  const names = Object.keys(sheets).sort();
  // Six of the ten arrived with wave R7's Flows cutover (T-R7-20), each from its
  // own area's `flow*Sheets` export rather than from an edit to the registry
  // file - which is why a lost half is a compile error here and not a name that
  // is quietly absent from the map.
  eq(names.join(","),
    "archive,files,flowNew,flowNode,flowPalette,flowQuick,flowStages,flowTonight,planEditor,report",
    "one name missing here is one screen the router cannot reach");
});

test("every entry names a module and a way to fetch it", () => {
  // Sheets are code-split (D-FU-2): a registry entry is `{ id, load }`, not the
  // component. The id is what `hubs/index.ts` compares when two hubs register
  // one name, so an entry without one would silently opt out of that check.
  for (const [name, entry] of Object.entries(sheets)) {
    assert(entry != null && typeof entry === "object", `${name} must be a registry entry`);
    assert(typeof entry.id === "string" && entry.id.length > 0, `${name} has no module id`);
    eq(typeof entry.load, "function", `${name} must carry a loader`);
  }
});

// ============================ 2b. what the registry drags into the entry chunk

test("the registry imports component-free modules, not the flows area barrels", () => {
  // `hubs/index.ts` is in the entry chunk and imports this registry
  // SYNCHRONOUSLY, so every static import it makes is paid for before first
  // paint (D-FU-2). Three of the four flows imports used to point at an area
  // BARREL - `../flows/inspector/sheets`, `../flows/tonight`,
  // `../flows/create` - each of which re-exports that area's whole component
  // tree and imports that area's stylesheet. T-R7-20 measured the cost at
  // +42.94 kB raw / +13.46 kB gzip for the CSS alone; splitting the `{ id,
  // load }` entries into a component-free `reg.ts` took this build's entry
  // chunk from 655.59 kB / 215.42 kB gzip to 623.82 kB / 205.68 kB.
  //
  // A SOURCE scan, because a bundle size is not observable from a DOM test and
  // an eager import shows no symptom at all until someone measures first paint.
  const src = readFileSync(join(hubDir, "sheets", "index.ts"), "utf8");
  const specs = Array.from(src.matchAll(/^import\s[^;]*?from\s+"([^"]+)";/gm))
    .map((m) => m[1])
    .filter((spec) => spec.includes("/flows/"));
  eq(specs.length, 4, "the four flows areas each publish one registry export; found");
  for (const spec of specs) {
    assert(/\/(reg|sheets)$/.test(spec),
      `the registry statically imports "${spec}", an area barrel: that pulls the whole `
      + "area - components, models and its stylesheet - into the entry chunk to register "
      + "a name. Import the area's component-free reg module instead.");
  }
});

const loadedSheets: Record<string, any> = {};
for (const [name, entry] of Object.entries(sheets)) {
  loadedSheets[name] = (await entry.load()).default;
}

await testAsync("every loader really resolves to a component", async () => {
  for (const [name, comp] of Object.entries(loadedSheets)) {
    eq(typeof comp, "function", `${name} loaded something that is not a component`);
  }
});

// ================================================ 3. the plan editor renders

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const settle = async () => {
  for (let i = 0; i < 4; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};

await testAsync("planEditor renders the phone reason instead of the editor below 768 px", async () => {
  act(() => {
    useStore.setState({
      principal: { role: "admin", email: null, caps: ["view.status", "control.mount"] } as never,
      authGate: "open",
      sequence: { state: "idle" } as never,
      wsPhase: "up",
    } as never);
  });
  const Comp = loadedSheets.planEditor as any;
  await act(async () => { root.render(createElement(Comp, { params: {}, depth: 0 })); });
  await settle();

  assert(container.querySelector('[data-testid="session-plan-editor"]') != null,
    "the sheet did not render - the fixture is wrong, not the sheet");
  assert(container.querySelector('[data-testid="plan-editor-phone"]') != null,
    "the phone card is missing");
  assert(container.textContent.includes(PLAN_EDITOR_PHONE_REASON),
    "a phone must be told where the editor opens, not shown an empty sheet");
  // matchMedia answers false, so this is the phone: the heavy editor must be
  // ABSENT, not rendered and hidden. The rebuilt editor's automation section
  // carries the `plan-automation` marker - if it is on screen, so is the editor.
  assert(container.querySelector('[data-testid="plan-automation"]') == null,
    "the plan editor must not be mounted on a phone");

  await act(async () => { root.unmount(); });
});

await testAsync("at 768 px and up it renders the rebuilt plan editor", async () => {
  // The POSITIVE CONTROL for the assertion above: without this, "Automation is
  // absent on a phone" would also pass over a sheet that never renders the
  // editor at any width, which is the failure mode the phone branch is meant to
  // be the only cause of.
  viewportW = 1024;
  const root2 = createRoot(container);
  const Comp = loadedSheets.planEditor as any;
  await act(async () => { root2.render(createElement(Comp, { params: {}, depth: 0 })); });
  await settle();

  assert(container.querySelector('[data-testid="session-plan-editor"]') != null,
    "precondition: the sheet rendered");
  assert(container.querySelector('[data-testid="plan-automation"]') != null,
    "the automation section is not mounted - the quotas and the guards "
    + "live inside it and are lost with it");
  assert(container.querySelector('[data-testid="plan-targets"]') != null,
    "and its targets editor came along");
  assert(!container.textContent.includes(PLAN_EDITOR_PHONE_REASON),
    "the phone reason must not be shown where the editor actually opens");

  await act(async () => { root2.unmount(); });
});

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`sessionSheets.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exitCode?: number } }).process!.exitCode = 1;
}
export default { passed, failed, total };
export { passed, failed, total };
