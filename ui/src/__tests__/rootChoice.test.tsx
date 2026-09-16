// rootChoice.test.tsx - WHICH root a hash opens, and that the choice is really
// a switch.
//
//   Run directly:  npx tsx src/__tests__/rootChoice.test.tsx
//   Also run by `npm test` and type-checked by `npx tsc --noEmit -p tsconfig.json`.
//
// WHAT THIS GUARDS. The app ships two complete front ends and mounts exactly one
// (ARCHITECTURE.md section 2). On 2026-09-15 the bare root went back to the
// classic app, with the six-hub UI kept at its own routes and at `#/next`. Three
// things can go wrong and all three look fine from a unit test of the parser:
//
//   1. A root that opens the wrong app. Every bookmark the operator has is a
//      bare `#/` or a legacy view name; landing them on the other UI is the
//      whole of what this change is about.
//   2. A `DEFAULT_ROOT` that is a comment rather than a switch. If flipping the
//      constant does not actually swap the roots, the "one constant, one place"
//      claim is false and the next person to flip it ships a no-op. So both
//      settings are exercised IN THIS FILE, through the test hatch.
//   3. A hashchange that only works one way. Typing `#/next` must leave the
//      classic root, and typing `#/` must come back, with no reload. The
//      listener is easy to wire once and never re-test in the other direction.
//
// HOW THE ROOTS ARE STUBBED. `Root` renders `App` or `NextApp`, each of which
// boots a whole application (WebSocket, auth gate, the atlas renderer). This
// file is about the CHOICE, so a module load hook answers those two specifiers
// with a one-element stub carrying a marker. The same hook answers `.css`,
// which Node cannot parse, and which `NextApp` imports by contract as the
// single style entry point. Stubbing is only sound because the markers are
// asserted positively below (see the vacuity guard at the end): a hook that
// silently stopped matching would otherwise turn every assertion green by
// rendering nothing at all.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------- module stubs
{
  const { registerHooks } = await import("node:module");
  registerHooks({
    load(url: string, context: any, nextLoad: any) {
      if (url.endsWith(".css")) {
        return { format: "module", shortCircuit: true, source: "export default {};" };
      }
      if (url.endsWith("/src/next/NextApp.tsx")) {
        return {
          format: "module",
          shortCircuit: true,
          source:
            'import { createElement } from "react";\n' +
            'export default function NextApp() {\n' +
            '  return createElement("div", { "data-testid": "stub-next-root" }, "next");\n' +
            '}\n',
        };
      }
      if (url.endsWith("/src/App.tsx")) {
        return {
          format: "module",
          shortCircuit: true,
          source:
            'import { createElement } from "react";\n' +
            'export default function App() {\n' +
            '  return createElement("div", { "data-testid": "stub-classic-root" }, "classic");\n' +
            '}\n',
        };
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

win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} send() {} addEventListener() {} removeEventListener() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;
g.fetch = async () => ({ ok: false, status: 404, statusText: "Not Found", json: async () => ({}) });

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../store");
const {
  DEFAULT_ROOT, classicViewForHash, isLegacyViewName, rootForHash,
  setDefaultRootForTests,
} = await import("../rootChoice");
const { LEGACY_VIEW_ROUTE } = await import("../next/legacyBridge");
const { resetRouterCacheForTests } = await import("../next/router");
const RootMod = await import("../AppRoot");
const Root = RootMod.default;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg = ""): void {
  if (got !== want) throw new Error(`${msg} expected ${String(want)}, got ${String(got)}`);
}

/** A fresh container per mount. React warns (and the second root silently wins)
 *  when `createRoot` is called twice on one element, even across an unmount. */
function freshContainer(): any {
  const el = win.document.createElement("div");
  win.document.body.appendChild(el);
  return el;
}

/** Mount `Root` at a hash and report which stub rendered. Fresh root per call,
 *  so nothing carries over from the previous case. */
function mountAt(hash: string): "classic" | "next" {
  win.location.hash = hash;
  resetRouterCacheForTests();
  const container = freshContainer();
  const r = createRoot(container);
  act(() => { r.render(createElement(Root)); });
  const isClassic = !!container.querySelector('[data-testid="stub-classic-root"]');
  const isNext = !isClassic && !!container.querySelector('[data-testid="stub-next-root"]');
  act(() => { r.unmount(); });
  container.remove();
  assert(isClassic !== isNext, `exactly one root must render for ${hash}`);
  return isClassic ? "classic" : "next";
}

// ------------------------------------------------- the shipped setting first

test("DEFAULT_ROOT ships as classic", () => {
  eq(DEFAULT_ROOT, "classic",
    "the operator's bookmarks are bare roots; this is the whole change:");
  eq(rootForHash("#/"), "classic");
});

test("the bare root, in all three spellings, is the classic app", () => {
  for (const h of ["", "#", "#/"]) {
    eq(rootForHash(h), "classic", `${JSON.stringify(h)}:`);
  }
});

test("every legacy view name at the root picks classic, except the two hub collisions", () => {
  // `monitor` and `settings` are BOTH a legacy view name and a new-UI hub. The
  // hub wins: the new UI's own routes must keep working unchanged, and the
  // classic pair is still addressable as `#/classic/monitor`, `#/classic/settings`.
  const collisions = new Set(["monitor", "settings"]);
  let checked = 0;
  for (const name of Object.keys(LEGACY_VIEW_ROUTE)) {
    const want = collisions.has(name) ? "next" : "classic";
    eq(rootForHash(`#/${name}`), want as any, `#/${name}:`);
    checked++;
  }
  eq(checked, 16, "all 16 legacy views were checked:");
  eq(rootForHash("#/classic/monitor"), "classic", "the classic pair is still reachable:");
  eq(rootForHash("#/classic/settings"), "classic");
});

test("the new UI keeps every one of its own routes", () => {
  for (const h of [
    "#/sky", "#/sky?mode=atlas", "#/weather/radar", "#/session/flows",
    "#/rig/devices/camera", "#/monitor/live", "#/settings/general/setup",
  ]) {
    eq(rootForHash(h), "next", `${h}:`);
  }
});

test("#/next is the alias for the new UI's home", () => {
  eq(rootForHash("#/next"), "next");
  eq(classicViewForHash("#/next"), null, "it is not a classic view name:");
  assert(!isLegacyViewName("next"), "`next` must not collide with a legacy view name");
});

test("#/classic and #/classic/<view> are unchanged", () => {
  eq(rootForHash("#/classic"), "classic");
  eq(rootForHash("#/classic/mount"), "classic");
  eq(classicViewForHash("#/classic/mount"), "mount");
  eq(classicViewForHash("#/classic"), null, "bare #/classic keeps the store's own view:");
});

test("a bare legacy name at the root carries its view, and junk does not", () => {
  eq(classicViewForHash("#/atlas"), "atlas");
  eq(classicViewForHash("#/mount"), "mount");
  eq(classicViewForHash("#/tonight"), "tonight");
  eq(classicViewForHash("#/"), null, "the bare root is the store's default view:");
  eq(classicViewForHash("#/not-a-view"), null,
    "an unknown name must not be written into store.view:");
  eq(classicViewForHash("#/sky"), null, "a hub route is not a classic view:");
});

// ----------------------------------------------------- the mounted component

test("#/ mounts the classic root", () => { eq(mountAt("#/"), "classic"); });

test("#/atlas mounts the classic root WITH the atlas view applied", () => {
  useStore.getState().setView("connect");
  eq(mountAt("#/atlas"), "classic");
  eq(useStore.getState().view, "atlas",
    "the deep link is delivered by writing store.view before App renders:");
});

test("#/classic/mount mounts the classic root with the mount view", () => {
  useStore.getState().setView("connect");
  eq(mountAt("#/classic/mount"), "classic");
  eq(useStore.getState().view, "mount");
});

test("#/sky mounts the new root", () => { eq(mountAt("#/sky"), "next"); });

test("#/next mounts the new root AND canonicalises onto its home", () => {
  eq(mountAt("#/next"), "next");
  eq(win.location.hash, "#/sky",
    "the address bar must name the screen the user is looking at:");
});

test("a bare legacy name does NOT bounce into the new UI", () => {
  win.location.hash = "#/atlas";
  resetRouterCacheForTests();
  const r = createRoot(freshContainer());
  act(() => { r.render(createElement(Root)); });
  eq(win.location.hash, "#/atlas",
    "nothing may rewrite an #/atlas bookmark to #/sky?mode=atlas under DEFAULT_ROOT=classic:");
  act(() => { r.unmount(); });
});

// ------------------------------------------------------- hashchange, BOTH ways

test("hashchange switches roots in both directions without a reload", () => {
  win.location.hash = "#/";
  resetRouterCacheForTests();
  const container = freshContainer();
  const r = createRoot(container);
  act(() => { r.render(createElement(Root)); });
  assert(!!container.querySelector('[data-testid="stub-classic-root"]'),
    "precondition: started on the classic root");

  act(() => {
    win.location.hash = "#/sky";
    win.dispatchEvent(new win.Event("hashchange"));
  });
  assert(!!container.querySelector('[data-testid="stub-next-root"]'),
    "classic -> next: typing a hub route must swap roots in place");
  assert(!container.querySelector('[data-testid="stub-classic-root"]'),
    "and the classic root must be gone, not both mounted at once");

  act(() => {
    win.location.hash = "#/";
    win.dispatchEvent(new win.Event("hashchange"));
  });
  assert(!!container.querySelector('[data-testid="stub-classic-root"]'),
    "next -> classic: typing #/ must come back");

  // And the classic deep-link rule survives a switch, not only a cold load.
  useStore.getState().setView("connect");
  act(() => {
    win.location.hash = "#/polar";
    win.dispatchEvent(new win.Event("hashchange"));
  });
  assert(!!container.querySelector('[data-testid="stub-classic-root"]'), "still classic");
  eq(useStore.getState().view, "polar", "applyClassicView ran on the switch too:");

  act(() => { r.unmount(); });
  container.remove();
});

// -------------------------------------------- the constant is a REAL switch
//
// Everything above is one half of the claim. If `DEFAULT_ROOT` did not actually
// decide anything, every assertion above would still pass with the choice
// hard-coded. So flip it and prove the OLD behaviour comes back intact.

test("with DEFAULT_ROOT = next, the pre-2026-09-15 behaviour holds", () => {
  setDefaultRootForTests("next");
  try {
    eq(rootForHash("#/"), "next", "the bare root is the new UI again:");
    eq(rootForHash("#/atlas"), "next",
      "an old view name falls through to the new UI (parseHash lands it on sky):");
    eq(rootForHash("#/mount"), "next");
    eq(rootForHash("#/sky"), "next");
    eq(classicViewForHash("#/atlas"), null,
      "and nothing is written into store.view for it:");

    // The two explicit forms are explicit under BOTH settings.
    eq(rootForHash("#/classic"), "classic");
    eq(rootForHash("#/classic/mount"), "classic");
    eq(classicViewForHash("#/classic/mount"), "mount");
    eq(rootForHash("#/next"), "next");

    eq(mountAt("#/"), "next", "and it is the mounted component that changes, not only the parser:");
    eq(mountAt("#/atlas"), "next");
    eq(mountAt("#/classic/mount"), "classic");
  } finally {
    setDefaultRootForTests(DEFAULT_ROOT);
  }
  eq(rootForHash("#/"), "classic", "the hatch was restored:");
});

// ------------------------------------------------------------ vacuity guard
//
// Every assertion above is read through `container.querySelector` on a stub
// module. If the load hook stopped matching - a moved file, a renamed
// specifier - `Root` would mount the REAL apps, which throw in jsdom, or render
// nothing recognisable, and `mountAt`'s "exactly one root" assert would fire.
// This makes the same thing a test rather than a side effect: both markers must
// be reachable, so a green run cannot mean "nothing rendered".

test("both stub markers really render (the tests above are not vacuous)", () => {
  win.location.hash = "#/";
  resetRouterCacheForTests();
  const ca = freshContainer();
  const a = createRoot(ca);
  act(() => { a.render(createElement(Root)); });
  eq(ca.querySelector('[data-testid="stub-classic-root"]').textContent, "classic");
  act(() => { a.unmount(); });
  ca.remove();

  win.location.hash = "#/sky";
  resetRouterCacheForTests();
  const cb = freshContainer();
  const b = createRoot(cb);
  act(() => { b.render(createElement(Root)); });
  eq(cb.querySelector('[data-testid="stub-next-root"]').textContent, "next");
  act(() => { b.unmount(); });
  cb.remove();
});

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`rootChoice.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
