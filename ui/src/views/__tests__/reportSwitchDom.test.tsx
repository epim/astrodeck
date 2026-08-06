// reportSwitchDom.test.tsx — the session-report viewer while it is switching
// nights, and while its index is unreachable.
//
//   Run directly:  npx tsx src/views/__tests__/reportSwitchDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// TWO CLAIMS THE SCREEN USED TO MAKE THAT WERE NOT TRUE (2026-08-05 #74, #75).
//
// #74 Picking a different night kept the OUTGOING report fully rendered — its
//     plan name, integration, accepted/rejected counts and per-filter table —
//     while `frames.csv` and `bundle.zip` beneath them already pointed at the
//     night just picked. Every number belonged to one session and every download
//     to another, with nothing on screen saying so.
//
// #75 One dropped request rendered "No reports yet — a report is generated
//     whenever a sequence finishes…", a positive claim about the user's data,
//     with no retry. Same false empty state flashed on every cold load.

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

// ---- the wire -------------------------------------------------------------
type Pending = { url: string; ok: (b: any) => void; fail: () => void };
const pending: Pending[] = [];
function answer(match: string, body: any): void {
  const i = pending.findIndex((p) => p.url.includes(match));
  if (i < 0) throw new Error(`nothing in flight matching "${match}" (have: ${pending.map((p) => p.url).join(", ")})`);
  const [p] = pending.splice(i, 1);
  p.ok(body);
}
function drop(match: string): void {
  const i = pending.findIndex((p) => p.url.includes(match));
  if (i < 0) throw new Error(`nothing in flight matching "${match}"`);
  const [p] = pending.splice(i, 1);
  p.fail();
}
const inFlight = (match: string) => pending.some((p) => p.url.includes(match));

win.fetch = (url: string) =>
  new Promise((resolve, reject) => {
    pending.push({
      url: String(url),
      ok: (body: any) => resolve({ ok: true, status: 200, statusText: "OK", json: () => Promise.resolve(body) }),
      fail: () => reject(new TypeError("network down")),
    });
  });

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "requestAnimationFrame", "cancelAnimationFrame", "fetch",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const ReportView = (await import("../ReportView")).default;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
async function flush(): Promise<void> {
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}

const A = "Perseus-20260730-210000";
const B = "Veil-20260805-203000";
function summary(id: string, name: string, frames: number) {
  return {
    id, plan_name: name, started_at: 1_754_000_000, ended_at: 1_754_020_000,
    end_reason: "complete", frames_captured: frames, frames_rejected: 0,
    integration_s: frames * 300,
  };
}
function report(id: string, name: string, frames: number) {
  return {
    ...summary(id, name, frames),
    by_filter: [{ filter: "Ha", frames, integration_s: frames * 300, hfr_median: 2.4, rejected: 0 }],
    targets: [],
    safety_events: [],
    trends: { hfr: [], temp: [], rms: [] },
  };
}

act(() => {
  useStore.setState({
    principal: { role: "admin", email: null, caps: ["view.status", "control.capture"] },
  } as never);
});

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
act(() => { root.render(createElement(ReportView)); });
const text = () => container.textContent || "";
/** Everything BELOW the picker panel — the header block, the tables, the trends,
 *  the bundle panel and the download links. The picker itself always lists every
 *  night's plan name (that is its job), so it is not a witness to staleness. */
const bodyText = (): string => {
  const rootDiv = container.firstElementChild;
  if (!rootDiv) return "";
  return [...rootDiv.children].slice(1).map((c: any) => c.textContent || "").join(" ");
};
const csvHref = (): string => {
  const a = [...container.querySelectorAll("a")].find((x: any) => /frames\.csv/.test(x.getAttribute("href") || ""));
  return a?.getAttribute("href") || "";
};

async function scenario(): Promise<void> {
  // ---------------------------------------------------------------- #75 first
  test("a cold load does not claim the user has no reports", () => {
    assert(!/No reports yet/.test(text()),
      "the false empty state is on screen before the index has even answered — " +
      "that is a positive claim about someone's data made from an unanswered request");
  });

  drop("/api/reports");
  await flush();

  test("a dropped index says so, and offers the retry an empty state cannot", () => {
    assert(!/No reports yet/.test(text()),
      `a failed request rendered "No reports yet": ${text().slice(0, 200)}`);
    assert(/Couldn't read the list/i.test(text()),
      `the failure is not stated: ${text().slice(0, 200)}`);
    const retry = [...container.querySelectorAll("button")].find((b: any) => /Retry/i.test(b.textContent || ""));
    assert(retry != null, "no way to try again — the screen is a dead end until a reload");
  });

  // Retry, and this time the index answers.
  const retry = [...container.querySelectorAll("button")].find((b: any) => /Retry/i.test(b.textContent || ""));
  act(() => { retry.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
  await flush();
  answer("/api/reports", [summary(B, "Veil", 5), summary(A, "Perseus", 42)]);
  await flush();
  answer(`/api/reports/${encodeURIComponent(B)}`, report(B, "Veil", 5));
  await flush();
  if (inFlight("bundle")) answer("bundle", { groups: [], warnings: [], keep_threshold: null });
  await flush();

  test("Retry actually reloads the index", () => {
    assert(/Veil/.test(bodyText()), `the newest report never rendered: ${text().slice(0, 200)}`);
    assert(!/Couldn't read the list/i.test(text()), "the error line outlived the successful retry");
  });

  // ------------------------------------------------------------------- #74
  // Switch to the older night and hold its response open — the window in which
  // the screen used to show one night's numbers over another night's downloads.
  const select = container.querySelector("select[aria-label='Select report']") as any;
  test("the picker offers both nights", () => {
    assert(select != null, "no report picker rendered");
    assert(select.options.length === 2, `expected 2 reports in the picker, got ${select.options.length}`);
  });

  // PRECONDITION: the outgoing night's numbers really are on screen right now,
  // so "they are gone" below is a fact about the fix and not about an empty page.
  test("the outgoing report's numbers are on screen before the switch", () => {
    assert(/Veil/.test(bodyText()) && /Ha/.test(bodyText()),
      `the first report is not rendered, so nothing can go stale: ${bodyText().slice(0, 200)}`);
    assert(csvHref().includes(encodeURIComponent(B)), `frames.csv points at ${csvHref()}`);
  });

  act(() => {
    select.value = A;
    select.dispatchEvent(new win.Event("change", { bubbles: true }));
  });
  await flush();

  test("while the new night loads, the OLD night's numbers are gone", () => {
    assert(!/Veil/.test(bodyText()),
      "the previous session's plan name, integration and per-filter table are still " +
      `rendered over the new night's downloads: ${bodyText().slice(0, 240)}`);
    assert(/loading/i.test(bodyText()), `nothing says the new report is loading: ${bodyText().slice(0, 200)}`);
  });

  test("and no download button is quietly pointing at the other session", () => {
    // The old failure was subtle precisely here: the csv link took the NEW id
    // the instant the picker moved, under numbers that were still the old ones.
    const href = csvHref();
    assert(href === "" || href.includes(encodeURIComponent(A)),
      `frames.csv points at ${href} while the screen shows another session`);
  });

  answer(`/api/reports/${encodeURIComponent(A)}`, report(A, "Perseus", 42));
  await flush();
  if (inFlight("bundle")) answer("bundle", { groups: [], warnings: [], keep_threshold: null });
  await flush();

  test("the picked night lands complete — numbers and download agree", () => {
    assert(/Perseus/.test(bodyText()), `the picked report never rendered: ${bodyText().slice(0, 200)}`);
    assert(csvHref().includes(encodeURIComponent(A)),
      `frames.csv still points at ${csvHref()} after the switch completed`);
  });
}

await scenario().catch((e: unknown) => {
  failed++;
  failures.push(`x the scenario could not be driven to the end: ${(e as Error).message}`);
});

// ------------------------------------------------------------------- report
act(() => { root.unmount(); });
const total = passed + failed;
console.log(`reportSwitchDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
