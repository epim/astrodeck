// coordsDom.test.tsx - the COORDINATES sheet, mounted and driven
// (T-SKY-4 plan G). Nothing here re-implements the server's coordinate
// parser (`catalog/objects.py:parse_coordinates`) - both the sexagesimal and
// decimal-degree forms are exercised purely by what the MOCKED server
// answers for that exact query string, matching how the real component
// works (it renders the response, never a local parse).
//
//   Run directly:  npx tsx src/next/hubs/sky/sheets/__tests__/coordsDom.test.tsx
//   Also run by `npm test` and type-checked by `tsc -b`.

/* eslint-disable @typescript-eslint/no-explicit-any */

const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/sky/coords" },
);
const win = dom.window as any;

win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------- network
// Keyed by the EXACT combined "ra dec" query string the sheet sends -
// the server's job (parse_coordinates), never re-derived here.
const CATALOG: Record<string, { results: any[]; notes?: string[] }> = {
  "05h 35m 17s -05 23 28": {
    results: [{
      id: "5.5881h -5.391°", name: "Typed position", type: "Coordinates", kind: "coordinates",
      ra_hours: 5.5881, dec_deg: -5.391, mag: 99, size_arcmin: 0, alt: 42.3, az: 118.7,
    }],
  },
  "83.82 -5.39": {
    results: [{
      id: "5.5880h -5.390°", name: "Typed position", type: "Coordinates", kind: "coordinates",
      ra_hours: 5.588, dec_deg: -5.39, mag: 99, size_arcmin: 0, alt: 40.1, az: 120.2,
    }],
  },
};

const asked: string[] = [];
const ok = (data: unknown) => ({ ok: true, status: 200, statusText: "OK", json: async () => data });
g.fetch = async (url: string) => {
  const u = String(url);
  asked.push(u);
  const m = /\/api\/catalog\?q=([^&]+)&explain=1/.exec(u);
  if (m) {
    const q = decodeURIComponent(m[1].replace(/\+/g, " "));
    return ok(CATALOG[q] ?? { results: [], notes: [`Nothing in the catalogue matches "${q}".`] });
  }
  return ok({});
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { CoordsSheet } = await import("../coords");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const settle = async (ms = 320) => {
  await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

useStore.setState({
  principal: {
    role: "admin", email: "admin@example.test",
    caps: ["view.status", "control.capture", "control.mount"],
  },
  wsPhase: "up",
  equipConnected: true,
  status: { connected: {}, looping: false, mode: "sim", busy: null, busy_lanes: [], mount: { ra_hours: 6, dec_deg: 10, ra_str: "06h", dec_str: "+10", alt: 50, az: 90, tracking: true, parked: false, slewing: false } },
  site: { latitude: 47.6, longitude: -122.3, is_default: false, horizon_min_deg: 15 },
} as never);

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
await act(async () => {
  root.render(createElement(CoordsSheet, { params: {}, depth: 0 } as any));
});

const raInput = () => container.querySelector('[data-testid="coords-ra-input"]') as any;
const decInput = () => container.querySelector('[data-testid="coords-dec-input"]') as any;
const readback = () => (container.querySelector('[data-testid="coords-readback"]') as any)?.textContent ?? "";
const type = (el: any, value: string) => {
  Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!.call(el, value);
  el.dispatchEvent(new win.Event("input", { bubbles: true }));
};
const button = (re: RegExp): any =>
  Array.from(container.querySelectorAll("button")).find((b: any) => re.test(b.textContent || ""));
const click = (el: any) => act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
/** Decode every `/api/catalog?q=...&explain=1` request back to the plain
 *  "ra dec" string it carried - the same decode the mock keys off, so the
 *  assertion reads as "was THIS position asked for", not "did some URL
 *  vaguely resemble it". */
const decodedCatalogQueries = (): string[] =>
  asked
    .map((u) => /\/api\/catalog\?q=([^&]+)&explain=1/.exec(u))
    .filter((m): m is RegExpExecArray => m != null)
    .map((m) => decodeURIComponent(m[1].replace(/\+/g, " ")));

await test("precondition: RA and Dec inputs render with the documented placeholders", () => {
  assert(raInput() != null, "no RA input - the fixture is wrong, not the component");
  assert(decInput() != null, "no Dec input - the fixture is wrong, not the component");
  assert(raInput().getAttribute("placeholder") === "22h 57m 54s", "the RA placeholder is not verbatim");
  assert(decInput().getAttribute("placeholder") === "+62° 37′ 06″", "the Dec placeholder is not verbatim");
});

await test("a sexagesimal position resolves and the read-back names alt/az", async () => {
  await act(async () => { type(raInput(), "05h 35m 17s"); type(decInput(), "-05 23 28"); });
  await settle();
  assert(decodedCatalogQueries().includes("05h 35m 17s -05 23 28"),
    `no debounced GET carried the combined sexagesimal query: ${JSON.stringify(decodedCatalogQueries())}`);
  assert(/Typed position/.test(readback()), `the read-back did not resolve: "${readback()}"`);
  assert(/alt 42°/.test(readback()), `the read-back altitude is wrong: "${readback()}"`);
  assert(/az 119°/.test(readback()), `the read-back azimuth is wrong: "${readback()}"`);
});

await test("a decimal-degree pair resolves too - nothing here re-parses either format", async () => {
  const before = asked.length;
  await act(async () => { type(raInput(), "83.82"); type(decInput(), "-5.39"); });
  await settle();
  assert(asked.length > before, "typing a new position did not fire a fresh lookup");
  assert(/Typed position/.test(readback()), `the decimal pair did not resolve: "${readback()}"`);
  assert(/alt 40°/.test(readback()), `the decimal pair's altitude is wrong: "${readback()}"`);
});

await test("IMAGE THIS POSITION sends the resolved ra/dec on to the quick sheet", async () => {
  await click(button(/IMAGE THIS POSITION/));
  await settle(0);
  assert(win.location.hash.startsWith("#/sky/quick?"), `the hash did not open the quick sheet: "${win.location.hash}"`);
  assert(/ra=/.test(win.location.hash) && /dec=/.test(win.location.hash),
    `ra/dec did not travel on the URL - a name-only quick sheet slews to the wrong target: "${win.location.hash}"`);
});

await act(async () => { root.unmount(); });

// ------------------------------------------------------------------- report
const total = passed + failed;
console.log(`coordsDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
