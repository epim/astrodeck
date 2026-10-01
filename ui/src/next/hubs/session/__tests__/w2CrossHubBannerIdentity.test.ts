// w2CrossHubBannerIdentity.test.ts - WP-60 (#268): `useSessionBanners` must key
// an incident banner's dismissal on the incident's IDENTITY, not on its KIND
// alone.
//
//   Run directly:  npx tsx src/next/hubs/session/__tests__/w2CrossHubBannerIdentity.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE BUG. Every cloud card shares `kind: "cloud"` - the open hold and, since
// H3, the deferred-hold card (`sky.hold_deferred`, #221/#244) both do. The old
// id was `inc-${kind}`, so once an operator dismissed one cloud-hold banner,
// the dismissal stuck for the rest of the session: a fresh hold after midnight
// on the SAME target, or a deferred hold during a wait, never banners again on
// the Sky/Plan/Gear hubs, even though it is different news (`next/lib/
// incidents.ts` `cloudIncident`; see also `crossHub.test.ts` section 5, which
// the deferred-hold test (5b) had to dodge with a fresh root for the same
// reason - this file is a separate test, per WP-60's file-ownership rule, not a
// patch of that one).
//
// THE FIX'S IDENTITY. `pill` tells HOLDING from WAITING apart even when
// neither has dated itself (`sinceMs` null on both - no matching log line);
// `sinceMs` tells a fresh hold from the one just dismissed when the pill
// repeats (the "fresh cloud hold after midnight" case). Both are part of the
// key (backlog ruling, WP-60 fix shape: "kind plus the hold's start (sinceMs),
// or kind plus its pill").
//
// This file sets up its OWN minimal harness (jsdom, fake fetch, store) rather
// than extending `crossHub.test.ts`, because WP-60 may only add new test files
// beside the owned code, never edit the existing one.

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
win.WebSocket = class { close() {} addEventListener() {} send() {} };

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

// ------------------------------------------------------------- the fake rig
//
// No flow is ever named (`sequence.plan_name` stays unset and `resumeArm` stays
// null), so `useSessionCampaign` resolves to no campaign throughout - this file
// is about the INCIDENT banner only. `useFlowLibrary` still fires once on mount
// regardless (`now/sessionData.ts`), so the library routes need an answer.
g.fetch = async (url: string) => {
  const ok = (data: unknown) => ({
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => data,
  });
  if (url === "/api/flows") return ok([]);
  if (url === "/api/flows/folders") return ok([]);
  if (url === "/api/sessions") return ok({ sessions: [] });
  if (url === "/api/reports") return ok([]);
  return {
    ok: false, status: 404, statusText: "Not Found",
    headers: { get: () => "application/json" },
    json: async () => ({ detail: "no" }),
  };
};

// ------------------------------------------------------------------ imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { resetCampaignForTests } = await import("../now/useCampaign");
const { resetSessionDataForTests } = await import("../now/sessionData");
const cross = await import("../crossHub");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

resetCampaignForTests();
resetSessionDataForTests();

const container = win.document.getElementById("root") as any;
const settle = async () => {
  for (let i = 0; i < 10; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};

/** Fixed throughout: only the hold/logs/sky fields under test move. */
const NOW = 1_757_090_000_000;

let seen: { banners: ReturnType<typeof cross.useSessionBanners> } = { banners: [] };
function Probe(): null {
  seen = { banners: cross.useSessionBanners(NOW) };
  return null;
}

function seedIdle(): void {
  act(() => {
    useStore.setState({
      principal: {
        role: "admin", email: null,
        caps: ["view.status", "view.preview", "view.site_derived"],
      } as never,
      authGate: "open",
      sequence: { state: "idle" } as never,
      safety: null as never,
      status: null as never,
      wsPhase: "up", telemetryStale: false, wsLastEvent: NOW,
      mountOp: null as never, focus: null as never, lastAutofocusResult: null as never,
      guide: null as never, lastCaptureAtMs: null as never,
      resumeArm: null as never, lastReportId: null as never, weather: null as never,
      logs: [] as never,
    } as never);
  });
}

/** One rig log line matching `cloudIncident`'s active-hold regex
 *  (`/^holding for clear sky: /`), the only way this fixture controls
 *  `sinceMs` without touching the engine. */
function holdLogLine(tsSeconds: number) {
  return { type: "log", data: { level: "info", source: "sequence", message: "holding for clear sky: clouds" }, ts: tsSeconds };
}

function findIncident() {
  return seen.banners.find((b) => b.id.startsWith("inc-")) ?? null;
}

/** A FRESH root per test block (not a shared one): the dismissal this hook
 *  tracks is component-local `useState`, so the only way to start a block with
 *  a clean slate is a new mount, same as `crossHub.test.ts` section 5b does for
 *  the same reason. */
async function withProbe(hash: string, fn: () => Promise<void>): Promise<void> {
  const root = createRoot(container);
  act(() => { win.location.hash = hash; });
  await act(async () => { root.render(createElement(Probe)); });
  await settle();
  try {
    await fn();
  } finally {
    act(() => { root.unmount(); });
  }
}

// ============================================================ the regression

await testAsync(
  "a later cloud hold banners again after the earlier one was dismissed",
  async () => {
    seedIdle();
    await withProbe("#/weather/conditions", async () => {
      // T1: an open cloud hold, dated by the rig's own log line.
      const T1 = 1_757_000_000;
      act(() => {
        useStore.setState({
          sequence: { state: "holding", hold: "clouds" } as never,
          logs: [holdLogLine(T1)] as never,
        } as never);
      });
      await settle();

      const first = findIncident();
      assert(first != null, "precondition: the open cloud hold banners");
      assert(first!.text.startsWith("CLOUD HOLD."), `precondition: it is the cloud card, got "${first!.text}"`);

      act(() => { first!.onDismiss(); });
      await settle();
      assert(findIncident() == null, "precondition: dismissing removes it");

      // T2 > T1: a FRESH hold - the rig logs a new "holding for clear sky" line
      // later in the night (a hold that cleared and reopened, or a new
      // target's hold after midnight). Same kind ("cloud"), same pill
      // (HOLDING), but a different `sinceMs` - different news.
      const T2 = T1 + 6 * 3600; // six hours later
      act(() => {
        useStore.setState({
          logs: [holdLogLine(T1), holdLogLine(T2)] as never,
        } as never);
      });
      await settle();

      const second = findIncident();
      assert(second != null,
        "a later cloud hold must banner again: dismissing the FIRST hold must not "
        + "silence every cloud card for the rest of the session (#268)");
      assert(second!.id !== first!.id,
        `the two holds are different news and must not share a dismissal id: both read "${second?.id}"`);
    });
  },
);

// ================================================== HOLDING vs WAITING (#244)

await testAsync(
  "a deferred hold banners even though an open hold's banner was dismissed first",
  async () => {
    seedIdle();
    await withProbe("#/weather/conditions", async () => {
      // First: an open hold with NO matching log line, so `sinceMs` is null -
      // the worst case for a key that relied on `sinceMs` alone.
      act(() => {
        useStore.setState({
          sequence: { state: "holding", hold: "clouds" } as never,
          logs: [] as never,
        } as never);
      });
      await settle();
      const open = findIncident();
      assert(open != null, "precondition: the open hold banners");
      act(() => { open!.onDismiss(); });
      await settle();
      assert(findIncident() == null, "precondition: dismissing removes it");

      // Then: the hold clears but the sky stays closed with no target to
      // judge it from - the DEFERRED card (`sky.hold_deferred`). Same kind
      // ("cloud"), same null `sinceMs` (no log line matches either regex
      // here), but a different pill (WAITING, not HOLDING): different news.
      act(() => {
        useStore.setState({
          sequence: {
            state: "running",
            sky: {
              cloudy: true, holding: false,
              hold_deferred: "the frames say the sky has closed in, with no target "
                + "set up to judge it from, so no cloud hold is open",
              text: "cloudy",
            },
          } as never,
        } as never);
      });
      await settle();

      const deferred = findIncident();
      assert(deferred != null,
        "the deferred-hold card must banner even though the earlier open-hold "
        + "banner (same kind, same null sinceMs) was already dismissed (#268)");
    });
  },
);

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`w2CrossHubBannerIdentity.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exitCode?: number } }).process!.exitCode = 1;
}
export default { passed, failed, total };
export { passed, failed, total };
