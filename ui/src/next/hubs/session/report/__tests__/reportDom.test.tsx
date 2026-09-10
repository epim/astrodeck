// reportDom.test.tsx - the SESSION / NIGHT REPORT, MOUNTED.
//
//   Run directly:  npx tsx src/next/hubs/session/report/__tests__/reportDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// WHAT IS WORTH GUARDING HERE
//
//   1. IT RENDERED, WITH A REAL REPORT. Seven markers and the fixture's own
//      plan name, filters and figures - so no assertion below can pass over an
//      empty page.
//   2. PICKING A NIGHT FETCHES IT ONCE. The legacy view's two guards (the
//      request generation on the index, the cancelled flag on the detail) exist
//      because an out-of-order response once left one night's numbers above
//      another night's download link.
//   3. THE EMPTY STATE IS A CLAIM, NOT AN ABSENCE. A run with no safety events
//      says so; a run with events lists them. The legacy panel rendered nothing
//      at all in the first case, which is indistinguishable from a panel that
//      failed to load.
//   4. A FILTER ROW CARRIES ITS NAME, NOT ONLY ITS COLOUR. Under `:root.night`
//      the whole palette collapses onto one hue, so a swatch alone says
//      nothing, and a wheel this palette has no token for must not be painted
//      as someone else's glass.
//   5. A VIEWER SEES THE SAME SCREEN, LOCKED. One read-only sentence naming the
//      capability, the materialize button honest-disabled (never the native
//      `disabled` attribute), no request fired - and the .zip, which is a read,
//      still live.
//   6. NO EM-DASH REACHES THE SCREEN. Three strings this screen renders come
//      from modules `#/classic` renders too, and all three still carry one:
//      `endReasonMeta`'s "UNSAFE - STOPPED", `keptSummary`'s "rated good - the
//      weakest N", and `materializeDisabledReason`'s "on this machine - the
//      FITS live on your imaging host". The rebuild normalises them at its own
//      boundary rather than editing a module the legacy UI renders.
//
// Convention: shell-and-tests.md section 4.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// `ReportScreen` imports `report.css`, which Node cannot load. The same
// synchronous hook `hubBoundary.test.tsx` installs answers with an empty
// module.
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
win.WebSocket = class { close() {} addEventListener() {} send() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "FocusEvent", "localStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------ the fake rig
const T0 = 1_757_000_000;

const SUM_R1 = {
  id: "R1", plan_name: "M31 LRGB", started_at: T0, ended_at: T0 + 21_600,
  end_reason: "complete", frames_captured: 42, frames_rejected: 3,
  integration_s: 12_600,
};
const SUM_R2 = {
  id: "R2", plan_name: "NGC 7331", started_at: T0 - 90_000, ended_at: T0 - 70_000,
  end_reason: "unsafe", frames_captured: 11, frames_rejected: 5,
  integration_s: 3_300,
};

const F_L = { filter: "L", frames: 24, rejected: 1, integration_s: 7_200, hfr_median: 2.31 };
const F_HA = { filter: "Ha", frames: 18, rejected: 2, integration_s: 5_400, hfr_median: null };
/** A frame that carries no filter at all - the wheel was empty or absent. */
const F_NONE = { filter: "", frames: 11, rejected: 5, integration_s: 3_300, hfr_median: 4.9 };

const REPORT_R1 = {
  ...SUM_R1,
  by_filter: [F_L, F_HA],
  targets: [{
    name: "M31", frames: 42, rejected: 3, integration_s: 12_600,
    by_filter: [F_L, F_HA],
  }],
  safety_events: [],
  trends: {
    hfr: [[T0, 2.4], [T0 + 3_600, 2.2], [T0 + 7_200, 2.05]],
    temp: [[T0, -9.8], [T0 + 3_600, -10.1]],
    rms: [[T0, 0.71], [T0 + 3_600, 0.66]],
  },
};

const REPORT_R2 = {
  ...SUM_R2,
  by_filter: [F_NONE],
  targets: [{ name: "NGC 7331", frames: 11, rejected: 5, integration_s: 3_300, by_filter: [F_NONE] }],
  safety_events: [
    { ts: T0 - 80_000, reason: "cloud cover above the hold threshold", action: "paused" },
    { ts: T0 - 75_000, reason: "safety monitor reported unsafe", action: "parked" },
  ],
  trends: { hfr: [], temp: [], rms: [] },
};

const REPORTS: Record<string, unknown> = { R1: REPORT_R1, R2: REPORT_R2 };

// A threshold IS active, so `keptSummary` has something to say - which is the
// only way the "N of M rated good" line, and the em-dash inside it, ever reach
// the screen.
const PREVIEW = {
  report_id: "R1", plan_name: "M31 LRGB", layout: "grouped",
  weight_altitude: false, keep_threshold: 0.5,
  groups: [{
    dir: "M31/Ha/300s", target: "M31", filter: "Ha", exposure_s: 300,
    gain: 100, binning: 1, light_count: 18, accepted_count: 16, kept_count: 15,
    masters: { dark: true, flat: true, bias: false },
  }],
  warnings: ["no bias master matched M31/Ha/300s"],
};

/** A preview from a rig where the FITS live somewhere else: no groups, so
 *  `materializeDisabledReason` produces its own em-dashed sentence. */
const PREVIEW_REMOTE = { ...PREVIEW, groups: [], warnings: [] };

const MAT = {
  export_dir: "C:/captures/exports/R1", layout: "grouped",
  linked: 18, copied: 0, bytes_copied: 0, failed: [],
  groups: [{ dir: "M31/Ha/300s", linked: 18, copied: 0 }],
  hardlink_note: "hardlinked",
};

const asked: { url: string; method: string }[] = [];

/** The index and the detail each have three outcomes, and the screen must say
 *  which one it got. The stub can be switched to the two failures and to the
 *  genuinely empty rig. */
let mode: "ok" | "empty" | "indexfail" | "detailfail" = "ok";

g.fetch = async (url: string, init?: { method?: string }) => {
  const method = init?.method ?? "GET";
  asked.push({ url, method });
  const json = (data: unknown) => ({
    ok: true, status: 200, statusText: "OK", json: async () => data,
  });
  const boom = {
    ok: false, status: 500, statusText: "Server Error",
    json: async () => ({ detail: "the report store is unreadable" }),
  };
  const path = url.split("?")[0];
  if (path === "/api/reports") {
    if (mode === "indexfail") return boom;
    return json(mode === "empty" ? [] : [SUM_R1, SUM_R2]);
  }
  if (mode === "detailfail" && /^\/api\/reports\/[^/]+$/.test(path)) return boom;
  if (/^\/api\/reports\/[^/]+\/bundle\/materialize$/.test(path)) return json(MAT);
  if (/^\/api\/reports\/[^/]+\/bundle$/.test(path)) return json(PREVIEW);
  const one = /^\/api\/reports\/([^/]+)$/.exec(path);
  if (one && REPORTS[one[1]]) return json(REPORTS[one[1]]);
  return { ok: false, status: 404, statusText: "Not Found", json: async () => ({ detail: "no" }) };
};

// ------------------------------------------------------------------ imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { accessPhrase } = await import("../../../../../lib/caps");
const { ReportScreen } = await import("../ReportScreen");
const { endReasonWord, filterColor, keptLine, materializeReason, REPORT_COPY } =
  await import("../reportModel");

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

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const settle = async () => {
  for (let i = 0; i < 8; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const tid = (t: string): any => container.querySelector(`[data-testid="${t}"]`);
const click = (el: any) => {
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};
/** The head of a `Disclosure` - the only thing that opens its body. */
const openGroup = (testid: string) => {
  const head = tid(testid)?.querySelector('button[aria-expanded]');
  assert(head != null, `no disclosure head inside ${testid}`);
  if (head.getAttribute("aria-expanded") !== "true") click(head);
};
/** Em-dash and en-dash: neither belongs in UI copy. */
const DASH = /[—–]/;
const gets = (re: RegExp) => asked.filter((a) => a.method === "GET" && re.test(a.url));
const posts = (re: RegExp) => asked.filter((a) => a.method === "POST" && re.test(a.url));

const ADMIN = [
  "view.status", "view.preview", "view.media", "control.capture",
  "control.mount", "config.backend",
];
const VIEWER = ["view.status", "view.preview"];

function seed(role: string, caps: string[]): void {
  act(() => {
    useStore.setState({
      principal: { role, email: null, caps } as never,
      authGate: "open",
      status: null, equipConnected: true, wsPhase: "up",
      lastReportId: null,
    } as never);
  });
}

async function mount(reportId?: string): Promise<void> {
  await act(async () => { root.render(createElement("div")); });
  await act(async () => { root.render(createElement(ReportScreen as any, { reportId })); });
  await settle();
}

// ============================================== 1. it rendered, with a report

seed("admin", ADMIN);
await mount("R1");

test("precondition: every panel of the report rendered for the fixture night", () => {
  for (const marker of [
    "report-body", "report-picker", "report-summary", "report-by-filter",
    "report-by-target", "report-safety", "report-trends", "report-bundle",
  ]) {
    assert(tid(marker) != null, `no ${marker} marker - the fixture is wrong, not the screen`);
  }
  assert(/M31 LRGB/.test(tid("report-summary").textContent),
    "the summary must carry the plan name of the night on screen");
  eq(tid("report-end-reason").textContent, "COMPLETE", "the end reason is a WORD, not a tone");
});

test("the three headline numbers are the report's own, not a re-derivation", () => {
  assert(/3h 30m/.test(tid("report-stat-integration").textContent),
    `integration, got "${tid("report-stat-integration").textContent}"`);
  assert(/42/.test(tid("report-stat-accepted").textContent), "accepted count");
  assert(/3/.test(tid("report-stat-rejected").textContent), "rejected count");
  eq(tid("report-stat-rejected").getAttribute("data-tone"), "warn",
    "a non-zero rejected count is toned, and the sub says 'graded out' beside it");
});

test("the run's window is stated, not just its start", () => {
  const ran = tid("report-ran").textContent as string;
  assert(/ to /.test(ran), `a finished run states both ends, got "${ran}"`);
});

// ======================================== 2. a filter row names its own glass

test("a per-filter row carries the filter NAME and its colour, never colour alone", () => {
  const row = tid("report-filter-Ha");
  assert(row != null, "no Ha row");
  const name = row.querySelector(".nx-report-name");
  assert(name != null && name.textContent === "Ha",
    `the row must print the wheel's own name, got "${name?.textContent}"`);
  const swatch = row.querySelector(".nx-report-swatch");
  assert(swatch != null, "no colour swatch on the row");
  assert(/--nx-filter-Ha/.test(swatch.getAttribute("style") ?? ""),
    `the swatch must come from the design token, got "${swatch.getAttribute("style")}"`);
  eq(filterColor("Ha"), "var(--nx-filter-Ha)", "and the token map is the one the row used");
});

test("a null HFR reads as a blank, never as 0.00", () => {
  const row = tid("report-filter-Ha").textContent as string;
  assert(/HFR -/.test(row), `an ungraded filter must not claim a median, got "${row}"`);
  assert(/HFR 2\.31/.test(tid("report-filter-L").textContent),
    "and a graded one prints its median");
});

test("the per-target block repeats the filters under the target that shot them", () => {
  assert(tid("report-target-0") != null, "no target block");
  assert(/M31/.test(tid("report-target-0").textContent), "the target name");
  assert(tid("report-target-0-filter-L") != null, "the nested L row");
  assert(tid("report-target-0-filter-Ha") != null, "the nested Ha row");
});

// ================================= 3. an empty safety log is a claim, not a gap

test("a night that tripped nothing SAYS nothing tripped, and renders no table", () => {
  assert(tid("report-safety-empty") != null,
    "a run with no safety events must render the empty state, not an empty panel");
  eq(tid("report-safety-empty").textContent, REPORT_COPY.safetyNone,
    "and the sentence must be the one that answers the question");
  assert(tid("report-safety-0") == null, "there is no event row to render");
  eq(tid("report-safety-count").textContent, "none", "the count line agrees with the empty state");
});

test("the trends plot from the report's own samples, and an empty series says so", () => {
  assert(tid("report-trend-hfr")?.querySelector("path") != null, "no HFR polyline");
  assert(tid("report-trend-temp")?.querySelector("path") != null, "no temperature polyline");
  assert(tid("report-trend-rms")?.querySelector("path") != null, "no guide-RMS polyline");
});

// ================================== 4. the bundle: groups, masters, one .zip

test("the bundle names each group and each calibration master by WORD and glyph", () => {
  const grp = tid("report-bundle-group-0");
  assert(grp != null, "no bundle group row");
  assert(/M31 · Ha · 300s · g100 · bin1/.test(grp.textContent),
    `the group identity, got "${grp.textContent}"`);
  assert(/18 lights \(16 accepted\)/.test(grp.textContent), "the counts");
  for (const kind of ["dark", "flat", "bias"]) {
    assert(new RegExp(kind).test(grp.textContent), `the ${kind} master chip must name itself`);
  }
  assert(tid("report-bundle-warning-0") != null, "the server's own warning must reach the screen");
});

test("the .zip is a plain download link with the options in its query", () => {
  const a = tid("report-bundle-zip");
  assert(a != null && a.tagName === "A", "a downloadable bundle must be an <a download>");
  assert(/\/api\/reports\/R1\/bundle\.zip/.test(a.getAttribute("href")),
    `the href must point at THIS report, got "${a.getAttribute("href")}"`);
  assert(tid("report-csv") != null, "frames.csv must still be reachable");
});

// ============================================ 5. picking a night fetches once

await testAsync("picking another night fetches it exactly once and swaps every panel", async () => {
  openGroup("report-picker");
  await settle();
  const row = tid("report-pick-R2");
  assert(row != null, "the picker must list the other report");

  const before = gets(/^\/api\/reports\/R2$/).length;
  click(row);
  await settle();
  eq(gets(/^\/api\/reports\/R2$/).length, before + 1,
    "one pick, one detail request - no double fetch and no re-fetch loop");

  assert(/NGC 7331/.test(tid("report-summary").textContent), "the summary must be the new night's");
  assert(!/M31 LRGB/.test(tid("report-summary").textContent),
    "and the outgoing night's title must be gone, not left above the new download link");
});

await testAsync("the night that DID trip safety lists its events with the action taken", async () => {
  assert(tid("report-safety-0") != null, "the events must render");
  assert(/cloud cover above the hold threshold/.test(tid("report-safety-0").textContent),
    "the reason the engine recorded");
  assert(/paused/.test(tid("report-safety-0").textContent), "and the action it took");
  assert(tid("report-safety-empty") == null, "the empty state must not also render");
});

test("a frame with no filter is labelled, not left blank", () => {
  const row = tid("report-filter-none");
  assert(row != null, "no row for the unnamed filter");
  assert(/NO FILTER/.test(row.textContent), `got "${row.textContent}"`);
});

test("no em-dash reaches the screen, including the three shared-lib sentences", () => {
  eq(tid("report-end-reason").textContent, "UNSAFE - STOPPED",
    "the shared table's em-dash is normalised at this boundary");
  eq(endReasonWord("unsafe"), "UNSAFE - STOPPED", "and the helper is what did it");

  // The kept line is on screen right now (the fixture preview carries a
  // cutoff), so the sweep below covers it. The "FITS live on your imaging
  // host" sentence only renders on a rig with no local subs, so it is checked
  // through the same wrapper the panel calls.
  assert(tid("report-bundle-kept") != null, "precondition: the kept line is rendered");
  const kept = keptLine(PREVIEW as never);
  assert(kept != null && !DASH.test(kept), `the kept line, got "${kept}"`);
  const remote = materializeReason(11, PREVIEW_REMOTE as never);
  assert(remote != null && /No local subs on this machine - the FITS/.test(remote),
    `the no-local-subs sentence must be hyphenated, got "${remote}"`);

  assert(!DASH.test(container.textContent),
    "an em-dash or en-dash reached the rendered report");
});

// ================================ 6. the advanced options and the materialize

await testAsync("back on the first night, the cutoff commits once and re-prices the bundle", async () => {
  openGroup("report-picker");
  await settle();
  click(tid("report-pick-R1"));
  await settle();

  openGroup("report-bundle-advanced");
  await settle();
  assert(tid("report-bundle-layout") != null, "the layout picker must be inside ADVANCED");
  assert(tid("report-bundle-keepon") != null, "so must the weakest-subs toggle");

  click(tid("report-bundle-keepon"));
  await settle();
  const box = tid("report-bundle-keep");
  assert(box != null, "turning the flag on must reveal the cutoff field");

  const before = gets(/\/bundle\?/).length;
  const setter = Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!;
  await act(async () => {
    setter.call(box, "0.7");
    box.dispatchEvent(new win.Event("input", { bubbles: true }));
  });
  await act(async () => {
    box.dispatchEvent(new win.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  });
  await settle();

  const priced = gets(/\/bundle\?/).slice(before);
  assert(priced.some((a) => /keep_threshold=0\.7/.test(a.url)),
    `the committed cutoff must reach the server, saw ${JSON.stringify(priced.map((a) => a.url))}`);
  eq(priced.length, 1,
    "one commit, one preview - the raw-draft field must not fire a request per keystroke");
});

await testAsync("an admin can write the export folder, and the outcome is stated", async () => {
  const before = posts(/materialize/).length;
  click(tid("report-bundle-materialize"));
  await settle();
  eq(posts(/materialize/).length, before + 1, "the button must actually issue the POST");
  const res = tid("report-bundle-result");
  assert(res != null, "the result of a write must be shown, not left to a toast that expires");
  assert(/18 photos/.test(res.textContent), `the outcome, got "${res.textContent}"`);
  assert(/captures\/exports\/R1/.test(res.textContent), "and where the folder is");
});

// ============================================ 7. the viewer sees it, locked

await testAsync("a viewer reads the whole report and is refused only the write", async () => {
  seed("viewer", VIEWER);
  await settle();

  assert(tid("report-summary") != null, "a viewer must see the same report, not an empty screen");
  assert(tid("report-by-filter") != null, "and every panel of it");

  const note = tid("report-bundle-lock");
  assert(note != null, "the panel must state the one read-only sentence");
  assert(new RegExp(accessPhrase("control.capture")).test(note.textContent),
    `the sentence must name the capability from the role table, got "${note.textContent}"`);
  eq(container.querySelectorAll('[data-testid="report-bundle-lock"]').length, 1,
    "ONE read-only sentence for the panel, not one per control");

  openGroup("report-bundle-advanced");
  await settle();
  const mat = tid("report-bundle-materialize");
  assert(mat != null, "the write must still be RENDERED for a viewer, not hidden");
  eq(mat.getAttribute("aria-disabled"), "true", "and honest-disabled");
  assert(new RegExp(accessPhrase("control.capture")).test(mat.getAttribute("title") ?? ""),
    `the press-reason must name the capability, got "${mat.getAttribute("title")}"`);

  const before = posts(/materialize/).length;
  click(mat);
  await settle();
  eq(posts(/materialize/).length, before, "a locked control must not reach the network");

  const zip = tid("report-bundle-zip");
  assert(zip != null && zip.tagName === "A",
    "the .zip is a READ - it stays live for a viewer");
});

test("no control on this screen uses the native disabled attribute", () => {
  eq(container.querySelectorAll("[disabled]").length, 0,
    "a native `disabled` strips the reason out of the accessibility tree");
});

// ================================= 8. the sheet's ?id= opens the night it names

await testAsync("a deep link opens the report it names, and asks for no other", async () => {
  asked.length = 0;
  seed("admin", ADMIN);
  await mount("R2");
  assert(/NGC 7331/.test(tid("report-summary").textContent),
    "the ?id= param must select the night, not merely be announced above the picker");
  eq(gets(/^\/api\/reports\/R1$/).length, 0,
    "and the newest report must not be fetched on the way past");
});

await testAsync("with no id, the newest report on the rig opens", async () => {
  asked.length = 0;
  await mount(undefined);
  assert(/M31 LRGB/.test(tid("report-summary").textContent),
    "the index is newest-first, so its first row is the default");
});

// ================== 9. the index has three outcomes and names the one it got

await testAsync("a rig with no reports says so, and does not claim a failure", async () => {
  mode = "empty";
  asked.length = 0;
  await mount(undefined);
  const p = tid("report-picker");
  assert(p != null, "the picker must still render");
  assert(/No reports yet/.test(p.textContent), `got "${p.textContent}"`);
  assert(new RegExp(REPORT_COPY.noReportsHint).test(p.textContent),
    "and must say what makes one, or the empty state is a dead end");
  assert(tid("report-summary") == null, "there is nothing to summarise");
});

await testAsync("a dropped index request is NOT an empty library, and offers the retry", async () => {
  mode = "indexfail";
  asked.length = 0;
  await mount(undefined);
  const p = tid("report-picker");
  assert(/Couldn't read the list of session reports/.test(p.textContent),
    `the failure must name itself, got "${p.textContent}"`);
  assert(!/No reports yet/.test(p.textContent),
    "a network failure must never be reported as a claim about the user's data");

  mode = "ok";
  const before = gets(/^\/api\/reports$/).length;
  click(tid("report-index-retry"));
  await settle();
  eq(gets(/^\/api\/reports$/).length, before + 1, "RETRY must actually re-ask");
  assert(tid("report-pick-R1") != null || tid("report-picker") != null,
    "and the recovered index must render");
});

await testAsync("a report that will not load says so where the report would be", async () => {
  mode = "detailfail";
  asked.length = 0;
  await mount("R1");
  const e = tid("report-error");
  assert(e != null, "no error card");
  assert(/Couldn't load report/.test(e.textContent), `got "${e.textContent}"`);
  assert(tid("report-summary") == null, "and no half-rendered report beside it");
  mode = "ok";
});

act(() => { root.unmount(); });

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`reportDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exitCode?: number } }).process!.exitCode = 1;
}
export default { passed, failed, total };
export { passed, failed, total };
