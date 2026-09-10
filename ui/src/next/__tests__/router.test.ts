// router.test.ts - the hash grammar and the nav verbs (ARCHITECTURE.md #3).
//
//   Run directly:  npx tsx src/next/__tests__/router.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT THIS GUARDS. The router is the only thing that knows where the user is,
// and every one of its failures is silent: a hub that parses as `sky` sends
// every deep link to the finder, a sub that is not recognised drops the user on
// the default section of the right hub (which LOOKS like a working app), and a
// `nav.hub` that keeps the previous hub's params hands the rig a `?target=`
// from the sky. So each test below asserts the VALUE, never just that something
// changed.
//
// Convention: printed tally plus the `{ passed, failed, total }` export
// (shell-and-tests.md section 4). A window is stubbed before the import,
// because the module attaches its one `hashchange` listener at load.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------- window stub
type L = () => void;
const hashListeners: L[] = [];
let backCalls = 0;

const fakeLocation: any = {
  _hash: "",
  href: "http://local/",
  get hash(): string { return this._hash; },
  set hash(v: string) {
    const h = v.startsWith("#") ? v : `#${v}`;
    if (h === this._hash) return;
    this._hash = h;
    fakeLocation.href = `http://local/${h}`;
    for (const l of hashListeners.slice()) l();
  },
};

const fakeWindow: any = {
  location: fakeLocation,
  history: {
    state: null,
    replaceState(_s: unknown, _t: string, url: string) {
      const s = String(url);
      const i = s.indexOf("#");
      fakeLocation._hash = i >= 0 ? s.slice(i) : "";
      fakeLocation.href = s;
    },
    back() { backCalls++; },
  },
  addEventListener(type: string, l: L) { if (type === "hashchange") hashListeners.push(l); },
  removeEventListener() { /* nothing under test unsubscribes */ },
};

const g = globalThis as any;
g.window = fakeWindow;

const {
  SUBS, parseHash, buildHash, isClassicHash, classicView, currentRoute, nav,
} = await import("../router");

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
function eqJson(got: unknown, want: unknown, msg = ""): void {
  const a = JSON.stringify(got);
  const b = JSON.stringify(want);
  if (a !== b) throw new Error(`${msg} expected ${b}, got ${a}`);
}

function setHash(h: string): void { fakeLocation.hash = h; }

// --------------------------------------------------------------- parse/build

test("every hub round-trips through build -> parse, default sub included", () => {
  for (const hub of ["sky", "weather", "session", "rig", "monitor", "settings"] as const) {
    const built = buildHash({ hub, sub: SUBS[hub][0] ?? "", sheets: [], params: {} });
    const back = parseHash(built);
    eq(back.hub, hub, `${hub} hub:`);
    eq(back.sub, SUBS[hub][0] ?? "", `${hub} sub:`);
    eqJson(back.sheets, [], `${hub} sheets:`);
  }
});

test("the canonical hash names the sub even when it is the default", () => {
  eq(buildHash({ hub: "session", sub: "now", sheets: [], params: {} }), "#/session/now");
  // ... and sky, which has no subs, gets no sub segment rather than an empty one
  eq(buildHash({ hub: "sky", sub: "", sheets: [], params: {} }), "#/sky");
});

test("a hub with no sub in the hash resolves to its FIRST sub", () => {
  eq(parseHash("#/session").sub, "now");
  eq(parseHash("#/rig").sub, "devices");
  eq(parseHash("#/weather").sub, "conditions");
  eq(parseHash("#/monitor").sub, "live");
  eq(parseHash("#/settings").sub, "general");
  eq(parseHash("#/sky").sub, "");
});

test("a segment that is not one of the hub's subs is a SHEET, not a bad sub", () => {
  const r = parseHash("#/rig/camera");
  eq(r.sub, "devices", "sub falls back:");
  eqJson(r.sheets, ["camera"], "sheets:");
});

test("an unknown hub falls back to sky and drops the path it could not read", () => {
  const r = parseHash("#/nonsense/deeper/still?target=M31");
  eq(r.hub, "sky");
  eqJson(r.sheets, [], "an unreadable path must not open a sheet:");
  eq(r.params.target, "M31", "params survive:");
});

test("the third sheet is dropped", () => {
  const r = parseHash("#/rig/devices/mount/polar/extra");
  eqJson(r.sheets, ["mount", "polar"]);
});

test("params encode and decode, including a space and a slash", () => {
  const built = buildHash({
    hub: "sky", sub: "", sheets: ["targets"],
    params: { name: "NGC 7331", note: "a/b", z: "1" },
  });
  const back = parseHash(built);
  eq(back.params.name, "NGC 7331", "space:");
  eq(back.params.note, "a/b", "slash:");
  eq(back.params.z, "1");
  eqJson(back.sheets, ["targets"], "the sheet survives the query:");
});

test("params are sorted, so one screen has exactly one URL", () => {
  const a = buildHash({ hub: "sky", sub: "", sheets: [], params: { b: "2", a: "1" } });
  const b = buildHash({ hub: "sky", sub: "", sheets: [], params: { a: "1", b: "2" } });
  eq(a, b, "key order must not change the hash:");
  eq(a, "#/sky?a=1&b=2");
});

test("an empty hash is the sky", () => {
  eq(parseHash("").hub, "sky");
  eq(parseHash("#").hub, "sky");
  eq(parseHash("#/").hub, "sky");
});

// ----------------------------------------------------------------- classic

test("classic detection", () => {
  assert(isClassicHash("#/classic"), "#/classic");
  assert(isClassicHash("#/classic/mount"), "#/classic/mount");
  assert(isClassicHash("#/classic/"), "#/classic/");
  assert(!isClassicHash("#/sky"), "#/sky is not classic");
  assert(!isClassicHash("#/classicx"), "a hub whose name merely starts with classic is not classic");
  assert(!isClassicHash(""), "an empty hash is the new root, not the old one");
});

test("classicView reads the view and only for a classic hash", () => {
  eq(classicView("#/classic/mount"), "mount");
  eq(classicView("#/classic"), null);
  eq(classicView("#/rig/devices"), null);
});

// --------------------------------------------------------------------- nav

test("nav.go pushes the CANONICAL hash for a shorthand path", () => {
  setHash("");
  nav.go("/settings/help?topic=camera-offline");
  eq(fakeLocation.hash, "#/settings/general/help?topic=camera-offline");
  const r = currentRoute();
  eq(r.hub, "settings");
  eq(r.sub, "general");
  eqJson(r.sheets, ["help"]);
  eq(r.params.topic, "camera-offline");
});

test("nav.hub clears sheets AND params", () => {
  setHash("#/rig/devices/camera?x=1");
  eq(currentRoute().sheets.length, 1, "precondition: a sheet is open");
  nav.hub("weather");
  eq(fakeLocation.hash, "#/weather/conditions");
  const r = currentRoute();
  eqJson(r.sheets, [], "sheets:");
  eqJson(r.params, {}, "params:");
});

test("nav.hub honours a named sub and ignores one the hub does not have", () => {
  nav.hub("monitor", "alerts");
  eq(fakeLocation.hash, "#/monitor/alerts");
  nav.hub("monitor", "nonsense");
  eq(fakeLocation.hash, "#/monitor/live", "an unknown sub falls back to the default:");
});

test("nav.sheet stacks, and a third replaces the second", () => {
  setHash("#/rig/devices");
  nav.sheet("mount");
  eqJson(currentRoute().sheets, ["mount"], "first:");
  nav.sheet("polar");
  eqJson(currentRoute().sheets, ["mount", "polar"], "second:");
  nav.sheet("camera");
  eqJson(currentRoute().sheets, ["mount", "camera"],
    "a third sheet replaces the second rather than being swallowed:");
});

test("nav.sheet carries params through", () => {
  setHash("#/session/gallery");
  nav.sheet("report", { id: "2026-09-09" });
  eq(currentRoute().params.id, "2026-09-09");
});

test("nav.closeSheet pops one and clears the params with the last one", () => {
  setHash("#/rig/devices/mount/polar?x=1");
  nav.closeSheet();
  eqJson(currentRoute().sheets, ["mount"], "popped to one:");
  eq(currentRoute().params.x, "1", "params stay while a sheet is still open:");
  nav.closeSheet();
  eqJson(currentRoute().sheets, [], "popped to none:");
  eqJson(currentRoute().params, {}, "the last pop clears the sheet's params:");
});

test("nav.back closes a sheet, and leaves the app only when there is none", () => {
  setHash("#/rig/devices/mount");
  const before = backCalls;
  nav.back();
  eqJson(currentRoute().sheets, [], "the sheet closed:");
  eq(backCalls, before, "history.back must NOT fire while a sheet was open:");
  nav.back();
  eq(backCalls, before + 1, "with no sheet open, BACK leaves:");
});

test("nav.replace does not fire hashchange but the route still updates", () => {
  setHash("#/session/now");
  const seen = hashListeners.length;
  assert(seen > 0, "precondition: the module registered its hashchange listener");
  nav.replace("/session/gallery");
  eq(fakeLocation.hash, "#/session/gallery", "the hash moved:");
  eq(currentRoute().sub, "gallery",
    "replaceState fires no hashchange, so the router must invalidate its own cache:");
});

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`router.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
