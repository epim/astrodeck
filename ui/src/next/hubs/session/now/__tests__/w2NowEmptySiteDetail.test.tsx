// w2NowEmptySiteDetail.test.tsx - WP-17 (b) on #/next Session/Now's empty
// state (#258).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/session/now/__tests__/w2NowEmptySiteDetail.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// `GET /api/sequence/resume-arm`'s `hold.site_detail` carries the numbers
// behind a words-only `hold.reason` (#233). NowEmpty's "Holding: ..." line
// rendered `reason` alone, so an operator entitled to the numbers (the server
// already withholds the key from a viewer) never saw them here either.
//
// NAMED MUTANT, run from a byte copy of NowEmpty.tsx and restored
// byte-identical afterwards (sha256 checked). The observed failure is quoted
// at the test it turns red.
//   N1 "show reason alone"   the site_detail branch removed from the line

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
win.WebSocket = class {
  static OPEN = 1;
  readyState = 0;
  close() {} send() {} addEventListener() {} removeEventListener() {}
};
win.scrollTo = () => {};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// Every GET answers an inert, empty body - none of this test's loaders are
// under test, so the seeded store value is what the empty state shows.
g.fetch = async (url: any) => {
  const u = String(url);
  const body = u.includes("/api/reports") ? [] : u.includes("/api/sessions") ? { sessions: [] }
    : u.includes("/api/plans") ? [] : u.includes("/api/flows") ? [] : { ok: true };
  return {
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => body, text: async () => JSON.stringify(body),
  };
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { NowEmpty } = await import("../NowEmpty");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void> | void): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const settle = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const byId = (id: string) => container.querySelector(`[data-testid="${id}"]`) as any;

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.site_derived", "control.capture", "control.mount"],
};

const ARMED = {
  id: "sess-1", name: "M31 LRGB", owed: 58, accepted: 132, total: 190,
  origin: "plan", origin_id: "",
};
const STALE_HOLD = {
  reason: "M31 is below its start floor; not slewing yet",
  since: 1_700_000_000, retry_at: 1_700_000_600,
  session_id: "sess-1", session_name: "M31 LRGB", owed: 58,
};
// A made-up target and made-up numbers - not the real site (project rule).
const SITE_DETAIL =
  "M31 is at 12 deg, below its 20 deg start floor (it reaches 20 deg in about 2 h)";

async function mount(hold: Record<string, unknown> | null): Promise<void> {
  await act(async () => { root.render(createElement("div")); });
  await act(async () => {
    const st = useStore.getState() as any;
    useStore.setState({
      principal: OPERATOR,
      sequence: { state: "idle" },
      resumeArm: { armed: ARMED, hold, recovering: false, recovery: null },
      safety: { connected: true, streak: 0, reading: null },
      site: null,
      masters: [],
      toasts: [],
      status: {
        connected: { camera: { connected: true, name: "sim" } },
        looping: false, busy_lanes: [],
      },
      flows: {
        ...st.flows, cards: [], libraryLoaded: true, libraryError: null,
        record: null, run: { ...st.flows.run, phase: "idle" },
      },
    } as never);
  });
  await act(async () => { root.render(createElement(NowEmpty)); });
  await settle();
}

await testAsync("precondition: the empty state is up and names the armed session", async () => {
  await mount(STALE_HOLD);
  assert(byId("now-empty") != null, "no now-empty: the fixture is wrong, not the component");
  const text = String(byId("now-empty").textContent);
  assert(text.includes(ARMED.name), `the armed session is not named: "${text.slice(0, 200)}"`);
});

await testAsync("control: a stale hold with no site_detail shows the reason alone", async () => {
  await mount(STALE_HOLD);
  const text = String(byId("now-empty").textContent);
  assert(text.includes(`Holding: ${STALE_HOLD.reason}.`),
    `the plain hold (no site_detail) changed shape: "${text}"`);
  assert(!text.includes(SITE_DETAIL), "a site_detail string appeared with no field on the wire");
});

await testAsync("a hold's site_detail is shown beside the reason, for an entitled principal (#258)", async () => {
  // N1 "show reason alone", observed:
  //   x a hold's site_detail is shown beside the reason, for an entitled
  //   principal (#258): site_detail is missing even though the server sent it
  await mount({ ...STALE_HOLD, site_detail: SITE_DETAIL });
  const text = String(byId("now-empty").textContent);
  assert(text.includes(`Holding: ${STALE_HOLD.reason} - ${SITE_DETAIL}.`),
    `site_detail is missing even though the server sent it: "${text}"`);
});

act(() => { root.unmount(); });

// ------------------------------------------------------------------ summary
const total = passed + failed;
console.log(`w2NowEmptySiteDetail: ${passed}/${total} passed`);
for (const f of failures) console.error(f);
export const result = { passed, failed, total };
