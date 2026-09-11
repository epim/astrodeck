// campaignFetch.test.tsx - ONE `GET /api/flows/{id}/tonight` for five readers,
// and a campaign that is actually re-read during the night.
//
//   Run directly:  npx tsx src/next/hubs/session/__tests__/campaignFetch.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `npx tsc --noEmit`.
//
// WHY THIS IS WORTH A FILE OF ITS OWN.
//
//   1. FIVE CONSUMERS, ONE REQUEST. `useCampaign` is module-level shared state,
//      like `sessionData.ts`, and it is read by the ledger card, the run header,
//      the vitals band, the Now screen's empty state and the cross-hub strip.
//      They mount in the same tick, and the freshness check they shared was
//      written BEFORE the first response landed - so every one of them saw a
//      stale `fetchedAt` and fired. `/tonight` is an astropy ephemeris pass per
//      resolved target on the rig's own CPU, which on a deployed appliance is an
//      Orange Pi on the pier. Five of them is the defect.
//   2. AND IT IS RE-READ. `REFETCH_MS` only ever SUPPRESSED: nothing re-ran the
//      fetch, so the banked figure an operator reads at 02:00 to decide whether
//      to cut the night short was the figure from dusk. Crossing into `running`
//      is the edge that must re-ask.
//
// Convention: shell-and-tests.md section 4 - jsdom by hand, createRoot + act,
// printed tally plus the `{ passed, failed, total }` export.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
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
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} send() {} addEventListener() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- the fake rig
const asks: string[] = [];

const TONIGHT = {
  ok: true,
  reason: "",
  budget: [
    { filter: "Ha", goal_h: 30, banked_h: 8.5, tonight_h: 3, has_ledger: true },
    { filter: "Oiii", goal_h: 15, banked_h: 2, tonight_h: 1.5, has_ledger: true },
  ],
  night: {
    dusk_unix: 1_757_000_000, dawn_unix: 1_757_030_000,
    dark_start_unix: 1_757_002_000, dark_end_unix: 1_757_028_000,
  },
};

g.fetch = async (url: any, init: any) => {
  const u = String(url);
  asks.push(`${(init?.method ?? "GET").toUpperCase()} ${u}`);
  const body = u.includes("/tonight") ? TONIGHT
    : u.startsWith("/api/reports") ? []
      : u === "/api/sessions" ? { sessions: [] }
        : { ok: true };
  return {
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => body,
    text: async () => "",
  };
};

// ------------------------------------------ imports, AFTER the globals are set
const { createElement, act, Fragment } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { useCampaign, resetCampaignForTests } = await import("../now/useCampaign");
const { resetSessionDataForTests } = await import("../now/sessionData");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void> | void): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg = ""): void {
  if (got !== want) throw new Error(`${msg} expected ${String(want)}, got ${String(got)}`);
}
const settle = async () => {
  for (let i = 0; i < 8; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const tonightAsks = (): number =>
  asks.filter((a) => a === "GET /api/flows/f1/tonight").length;

/** One reader. Five of these is the shape of the Now screen: the ledger card,
 *  the run header, the vitals band, the empty state's flow id and the shell's
 *  cross-hub strip all call `useCampaign()` independently, by design (no
 *  component on that screen takes its data as a prop - plan section E.4). */
function Probe({ id }: { id: string }): any {
  const { campaign, loading, error } = useCampaign();
  return createElement("span", { "data-testid": `probe-${id}` },
    error ? `error ${error}` : campaign ? campaign.summary : loading ? "loading" : "none");
}

const CARD = {
  id: "f1", name: "NGC 7331 Ha", updated_ts: 10, nodes: 4, edges: 3,
  folder: "", readonly: false,
};

function seed(state: string): void {
  act(() => {
    const st = useStore.getState() as any;
    useStore.setState({
      principal: {
        role: "operator", email: "op@rig",
        caps: ["view.status", "view.preview", "view.site_derived"],
      },
      authGate: "open",
      equipConnected: true,
      wsPhase: "up",
      resumeArm: null,
      // Rule 2 of `useCampaignFlowId`: a saved flow whose name is the running
      // plan's name. No session ledger is needed for the fetch itself.
      sequence: { state, plan_name: "NGC 7331 Ha" },
      flows: { ...st.flows, cards: [CARD], libraryLoaded: true, libraryError: null },
    } as never);
  });
}

resetCampaignForTests();
resetSessionDataForTests();
seed("idle");

await act(async () => {
  root.render(createElement(Fragment, null,
    ...["a", "b", "c", "d", "e"].map((k) => createElement(Probe, { key: k, id: k }))));
});
await settle();

// ------------------------------------------------------- 0. the vacuity guard
await testAsync("all five readers mounted and got the campaign", async () => {
  for (const k of ["a", "b", "c", "d", "e"]) {
    const el = container.querySelector(`[data-testid="probe-${k}"]`);
    assert(el != null, `probe ${k} never mounted - the count below is vacuous`);
    assert(/of ~/.test(el.textContent ?? ""),
      `probe ${k} has no campaign on it ("${el.textContent}") - the fetch never `
      + "resolved, so one request would also be zero requests");
  }
  assert(tonightAsks() > 0, "nothing ever asked /tonight");
});

// ------------------------------------------------- 1. ONE request, not five
await testAsync("five readers cost ONE GET /api/flows/f1/tonight", async () => {
  // SABOTAGE: delete the `inFlight` promise from `useCampaign.ts` (or the
  // freshness check in front of it) and this reads 5.
  eq(tonightAsks(), 1,
    "each reader fired its own ephemeris pass on the rig's CPU:");
});

// ------------------------------------- 2. a sixth reader mounting costs nothing
await testAsync("a reader mounting later reuses the held answer", async () => {
  const before = tonightAsks();
  await act(async () => {
    root.render(createElement(Fragment, null,
      ...["a", "b", "c", "d", "e", "f"].map((k) => createElement(Probe, { key: k, id: k }))));
  });
  await settle();
  eq(tonightAsks(), before,
    "a component mounting inside the freshness window re-asked:");
  assert(container.querySelector('[data-testid="probe-f"]') != null,
    "the sixth probe never mounted, so it could not have asked anything");
});

// ------------------------------------------- 3. the run state edge DOES re-ask
await testAsync("crossing into running re-reads the campaign", async () => {
  // SABOTAGE: take `seq.state` out of the effect's dependency list and this
  // stays at 1 - which is the shipped behaviour: the ledger card an operator
  // reads at 02:00 still shows what was banked at dusk.
  const before = tonightAsks();
  seed("running");
  await settle();
  eq(tonightAsks(), before + 1,
    "the campaign was never re-read after the run started:");
});

await testAsync("and the same state again does not", async () => {
  const before = tonightAsks();
  seed("running");
  await settle();
  eq(tonightAsks(), before,
    "a re-render with no state change fired a second ephemeris pass:");
});

// --------------------------------------- 4. no capability, no request at all
await testAsync("a viewer without view.site_derived fires nothing", async () => {
  const before = tonightAsks();
  act(() => {
    useStore.setState({
      principal: { role: "viewer", email: null, caps: ["view.status", "view.preview"] },
    } as never);
  });
  await settle();
  eq(tonightAsks(), before,
    "the site-derived route was fired for a principal that may not read it:");
  const el = container.querySelector('[data-testid="probe-a"]');
  eq(el.textContent, "none", "a viewer must get no campaign, not a stale one:");
});

await act(async () => { root.unmount(); });
resetCampaignForTests();

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`campaignFetch.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exitCode?: number } }).process!.exitCode = 1;
}
export default { passed, failed, total };
export { passed, failed, total };
