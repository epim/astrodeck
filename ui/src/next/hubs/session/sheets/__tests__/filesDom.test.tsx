// filesDom.test.tsx - the FILES sheet, MOUNTED, with a stubbed rig.
//
//   Run directly:  npx tsx src/next/hubs/session/sheets/__tests__/filesDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE FIVE PROMISES THIS SCREEN MAKES THAT COULD SILENTLY BREAK
//
//   1. It rendered at all. Every assertion below is worthless over a blank
//      page, so the first test asserts the screen marker and a real row - the
//      `verify-on-the-real-thing` guard.
//   2. THE LINK IS THE SELECTION. The href on DOWNLOAD must be exactly what
//      `selectionQuery` builds for the ticked set: a button that prices one set
//      and fetches another is the worst kind of correct-looking bug, because
//      the zip arrives and looks fine.
//   3. FITS ARE GATED AND SAY SO. An operator - the person who ran the night -
//      does NOT hold `view.media`. The option must dim, keep `aria-disabled`,
//      and carry "syncer or admin access" (from `accessPhrase`, never a
//      hand-written role name).
//   4. A RUNNING SESSION CANNOT BE REGRADED. The server answers 409; this must
//      say so BEFORE the tap and fire no PATCH at all.
//   5. AN OVER-LONG PICK IS REFUSED, NOT TRUNCATED. A hand-picked selection
//      rides in the URL; past `PICKED_URL_BUDGET` the sheet prints the refusal
//      and offers the whole-filter escape.
//
// Convention: shell-and-tests.md section 4 - jsdom by hand, createRoot + act,
// native events, printed tally plus the `{ passed, failed, total }` export.

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
  "HTMLSelectElement", "Element", "Node", "Event", "CustomEvent", "MouseEvent",
  "KeyboardEvent", "localStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- the fake rig
interface Fixture {
  sessionStatus: "active" | "dormant";
  frames: any[];
  index: any;
}

const NIGHT = "2026-09-08";

function libFrame(filter: string, n: number, bytes = 10 * 1024 * 1024, prefix = "M31"): any {
  return {
    path: `${prefix}/${filter}_${String(n).padStart(4, "0")}.fits`,
    name: `${filter}_${n}.fits`, folder: prefix, night: NIGHT,
    ts: 1_757_000_000 + n, local_date: NIGHT, local_clock: "22:10",
    target: "M31", filter, frame_type: "Light",
    exposure_s: filter === "Ha" ? 300 : 60, bytes, mtime: 1_757_000_000 + n,
  };
}

const INDEX = {
  target: "M31",
  totals: { frames: 5, accepted: 4, bytes: 0, integration_s: 720 },
  by_filter: [
    {
      filter: "L", count: 3, accepted: 2, exposure_s: 60, bytes: 0, integration_s: 120,
      frames: [
        { id: "f1", ts: 1, bytes: 0, accepted: true, override: null, hfr: 2.1, stars: 400, guide_rms: 0.5, thumb: null },
        { id: "f2", ts: 2, bytes: 0, accepted: true, override: null, hfr: 2.2, stars: 380, guide_rms: 0.6, thumb: null },
        { id: "f3", ts: 3, bytes: 0, accepted: false, override: null, hfr: 5.9, stars: 40, guide_rms: 2.4, thumb: null },
      ],
    },
    {
      filter: "Ha", count: 2, accepted: 2, exposure_s: 300, bytes: 0, integration_s: 600,
      frames: [
        { id: "f4", ts: 4, bytes: 0, accepted: true, override: null, hfr: 3.0, stars: 120, guide_rms: 0.4, thumb: null },
        { id: "f5", ts: 5, bytes: 0, accepted: true, override: null, hfr: 3.1, stars: 118, guide_rms: 0.4, thumb: null },
      ],
    },
  ],
};

const SESSION = {
  id: "s1", schema_version: 1, name: "M31 LRGB", created_ts: 1_756_900_000,
  updated_ts: 1_757_000_100, status: "dormant", nights: [NIGHT],
  auto_resume: false, frames: [],
  plan: {
    name: "M31 LRGB", guide: true, dither_every: 1, dither_pixels: null,
    autofocus_every: 0, cool_to: null, cool_timeout_s: null,
    apply_filter_offsets: null, refocus_on_temp_delta_c: null, meridian_flip: true,
    recover_guiding: null, hfr_reject_factor: null, park_when_done: true,
    warm_cooler_when_done: true,
    targets: [{
      id: "t1", name: "M31", ra_hours: 0.7, dec_deg: 41, center: true,
      autofocus_first: true, calibration: false,
      steps: [
        { id: "st-L", filter: "L", exposure_s: 60, gain: 100, offset: 10, binning: 1, count: 10, frame_type: "Light" },
        { id: "st-Ha", filter: "Ha", exposure_s: 300, gain: 100, offset: 10, binning: 1, count: 10, frame_type: "Light" },
      ],
    }],
  },
};

const fixture: Fixture = {
  sessionStatus: "dormant",
  frames: [
    libFrame("L", 1), libFrame("L", 2), libFrame("L", 3),
    libFrame("Ha", 1, 20 * 1024 * 1024), libFrame("Ha", 2, 20 * 1024 * 1024),
  ],
  index: INDEX,
};

const asked: { url: string; method: string }[] = [];

g.fetch = async (url: string, init?: { method?: string }) => {
  const method = init?.method ?? "GET";
  asked.push({ url, method });
  const json = (data: unknown) => ({
    ok: true, status: 200, statusText: "OK", json: async () => data,
  });
  const miss = () => ({
    ok: false, status: 404, statusText: "Not Found", json: async () => ({ detail: "no" }),
  });

  if (url.startsWith("/api/sessions/current/files") || /\/api\/sessions\/[^/]+\/files/.test(url)) {
    return fixture.index ? json(fixture.index) : miss();
  }
  if (/\/api\/sessions\/[^/]+\/frames\/[^/]+$/.test(url) && method === "PATCH") {
    return json({ frame: {}, remaining: {} });
  }
  if (url === "/api/sessions") {
    return json({
      sessions: [{
        id: "s1", name: "M31 LRGB", status: fixture.sessionStatus,
        created_ts: SESSION.created_ts, updated_ts: SESSION.updated_ts,
        nights: 1, accepted: 4, total: 20, auto_resume: false,
      }],
    });
  }
  if (/^\/api\/sessions\/[^/]+$/.test(url)) {
    return json({ ...SESSION, status: fixture.sessionStatus });
  }
  if (url.startsWith("/api/gallery/nights")) {
    return json({ current: NIGHT, nights: [{ night: NIGHT, frames: 5, bytes: 70 * 1024 * 1024 }], truncated: false });
  }
  if (url.startsWith("/api/gallery/frames")) {
    return json({
      frames: fixture.frames,
      total: fixture.frames.length,
      bytes: fixture.frames.reduce((a: number, f: any) => a + f.bytes, 0),
      offset: 0, limit: 500, truncated: false, scan_ms: 4,
    });
  }
  if (url.startsWith("/api/reports")) return json([]);
  if (url.startsWith("/api/remote/status")) {
    return json({ enabled: true, home_id: "h", relay_host: null, connected: true, last_error: null, since_unix: null, gen: 1, via: "direct" });
  }
  if (url.startsWith("/api/sequence/stack")) {
    return json({
      enabled: true, target: "M31", seq: 7, channels: [], frames: 4, integrated_s: 720,
      rejected: 1, mode: "mono", downsample: 2, has_image: true, render_age_s: 1,
      backfill: { running: false, total: 0, done: 0, added: 0, skipped: 0, failed: 0, channel: "", error: "", started_ts: null, finished_ts: null, available: 0 },
    });
  }
  return miss();
};

// ------------------------------------------------------------------ imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { selectionQuery } = await import("../../../../../lib/gallery");
const { parseHash, resetRouterCacheForTests } = await import("../../../../router");
const { FilesSheet } = await import("../files");

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
  for (let i = 0; i < 6; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const tid = (t: string): any => container.querySelector(`[data-testid="${t}"]`);
const click = (el: any) => {
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};
const text = (): string => container.textContent ?? "";

const CAPS_ADMIN = ["view.status", "view.preview", "view.media", "control.mount", "control.capture"];
const CAPS_OPERATOR = ["view.status", "view.preview", "control.mount", "control.capture"];
const CAPS_VIEWER = ["view.status", "view.preview"];

function seed(role: string, caps: string[], state: string): void {
  act(() => {
    useStore.setState({
      principal: { role, email: null, caps } as never,
      sequence: state === "idle"
        ? ({ state: "idle" } as never)
        : ({ state: "running", target: "M31", session: { id: "s1", name: "M31 LRGB", count_mode: "attempts", accepted: 4 } } as never),
      status: null, equipConnected: true, wsPhase: "up",
    } as never);
  });
}

async function mount(src = "s1"): Promise<void> {
  await act(async () => {
    root.render(createElement(FilesSheet as any, { params: { src }, depth: 0 }));
  });
  await settle();
}

// ============================================================ 1. it rendered

seed("admin", CAPS_ADMIN, "idle");
await act(async () => { root.render(createElement("div")); });
await mount();

test("precondition: the sheet rendered with its marker and both filter rows", () => {
  assert(tid("session-files") != null, "no session-files marker - the fixture is wrong, not the sheet");
  assert(tid("files-row-L") != null, "the L row never rendered");
  assert(tid("files-row-Ha") != null, "the Ha row never rendered");
  assert(/M31/.test(text()), "the target never reached the header");
});

test("the row says what is in it and how it was graded", () => {
  const l = tid("files-row-L");
  assert(/3 subs/.test(l.textContent), `L row should count 3 subs, got "${l.textContent}"`);
  assert(/60 s/.test(l.textContent), "the exposure is on the row");
  assert(/1 rejected/.test(l.textContent), "the ledger's reject count reached the row");
});

// ============================================== 2. the href IS the selection

test("everything ticked: the DOWNLOAD href is the FILTER selection, byte for byte", () => {
  const a = tid("files-download");
  eq(a.tagName, "A", "with view.media held the control is a real link, not a locked button");
  const want = "/api/gallery/download.zip" + selectionQuery({
    mode: "filter", q: "M31", nightFrom: NIGHT, nightTo: NIGHT,
  });
  eq(a.getAttribute("href"), want, "the link must name exactly the set the button priced");
  assert(/DOWNLOAD 5 SUBS/.test(a.textContent), `the label counts the ticked frames, got "${a.textContent}"`);
});

await testAsync("un-ticking a filter switches to an explicit path list", async () => {
  click(tid("files-tick-Ha"));
  await settle();
  const a = tid("files-download");
  const want = "/api/gallery/download.zip" + selectionQuery({
    mode: "picked",
    paths: ["M31/L_0001.fits", "M31/L_0002.fits", "M31/L_0003.fits"],
  });
  eq(a.getAttribute("href"), want, "a subset must send the paths, not the filter");
  assert(/DOWNLOAD 3 SUBS/.test(a.textContent), "and re-price to the three that are left");
  click(tid("files-tick-Ha"));
  await settle();
});

// ================================================ 3. the view.media gate

await testAsync("an operator sees FITS originals honest-disabled, with the derived role phrase", async () => {
  seed("operator", CAPS_OPERATOR, "idle");
  await settle();
  const seg = tid("files-format");
  assert(seg != null, "the format picker vanished");
  eq(seg.getAttribute("aria-disabled"), "true", "the picker must be honest-disabled, not absent");
  const note = tid("files-fits-locked");
  assert(note != null, "no reason was rendered for the locked FITS option");
  assert(/syncer or admin access/.test(note.textContent),
    `the reason must come from accessPhrase, got "${note.textContent}"`);
  const dl = tid("files-download");
  eq(dl.tagName, "BUTTON", "without view.media the download is a locked button, never a live link");
  assert(/syncer or admin access/.test(dl.getAttribute("title") ?? ""),
    "the download button states the same capability");
});

await testAsync("a viewer sees the screen, the lock, and fires nothing", async () => {
  seed("viewer", CAPS_VIEWER, "idle");
  await settle();
  assert(tid("session-files") != null, "a viewer must see the same screen, not an empty one");
  const dl = tid("files-download");
  eq(dl.getAttribute("aria-disabled"), "true", "the download is locked for a viewer");
  const before = asked.length;
  click(dl);
  await settle();
  eq(asked.length, before, "a locked press must not reach the network");
});

// ======================================= 4. a running session cannot regrade

await testAsync("regrade on an ACTIVE session states the reason and fires no PATCH", async () => {
  fixture.sessionStatus = "active";
  seed("admin", CAPS_ADMIN, "running");
  await act(async () => { root.render(createElement("div")); });
  await mount("current");

  click(tid("files-row-L").querySelector("button[aria-expanded]"));
  await settle();
  const mark = tid("files-mark-accepted");
  assert(mark != null, "the regrade control never rendered");
  eq(mark.getAttribute("title"),
    "Session is running - regrades are read-only until it finishes",
    "the server's own refusal must be said before the tap");
  assert(/Session is running/.test(text()), "and printed where the user is looking");

  const before = asked.filter((a) => a.method === "PATCH").length;
  click(mark);
  await settle();
  eq(asked.filter((a) => a.method === "PATCH").length, before,
    "a read-only regrade must issue no PATCH at all");
});

// ====================================== 5. an over-long pick is refused

await testAsync("a picked selection past the URL budget prints downloadPlan's refusal", async () => {
  // 120 frames under a long folder name: the picked query passes 6000
  // characters, which proxies refuse. Nothing may be truncated.
  const long = "M31-mosaic-panel-03-north-east-long-folder-name";
  fixture.sessionStatus = "dormant";
  fixture.frames = [
    ...Array.from({ length: 120 }, (_, i) => libFrame("L", i + 1, 1024 * 1024, long)),
    libFrame("Ha", 1, 1024 * 1024, long),
  ];
  fixture.index = {
    target: "M31",
    totals: { frames: 121, accepted: 121, bytes: 0, integration_s: 0 },
    by_filter: [
      { filter: "L", count: 120, accepted: 120, exposure_s: 60, bytes: 0, integration_s: 7200, frames: [] },
      { filter: "Ha", count: 1, accepted: 1, exposure_s: 300, bytes: 0, integration_s: 300, frames: [] },
    ],
  };
  seed("admin", CAPS_ADMIN, "idle");
  await act(async () => { root.render(createElement("div")); });
  await mount();

  // Untick Ha so the selection can no longer be expressed as a filter.
  click(tid("files-tick-Ha"));
  await settle();

  const refusal = tid("files-refusal");
  assert(refusal != null, "the refusal panel never rendered - the link would have been sent over-long");
  assert(/hand-picked frames need a/.test(refusal.textContent),
    `downloadPlan's own words must be shown, got "${refusal.textContent}"`);
  assert(/Select all in filter/.test(refusal.textContent), "including the escape it names");
  assert(/DOWNLOAD ALL 121 INSTEAD/.test(refusal.textContent), "and the one-tap way to take it");
  const dl = tid("files-download");
  eq(dl.tagName, "BUTTON", "the over-long link must NOT be offered as a live href");
});

// ================================== 6. the source picker SWITCHES (review #17)
//
// `nav.sheet("files", ...)` called from inside the files sheet pushed a SECOND
// "files" onto the stack; params are shared across the stack, so the sheet
// underneath rendered as the identical screen and BACK was a no-op to the eye.
// The proof is the hash: one "files" segment, and the new src on it.

await testAsync("switching the source replaces this sheet instead of stacking a second one", async () => {
  fixture.sessionStatus = "dormant";
  fixture.frames = [
    libFrame("L", 1), libFrame("L", 2), libFrame("L", 3),
    libFrame("Ha", 1, 20 * 1024 * 1024), libFrame("Ha", 2, 20 * 1024 * 1024),
  ];
  fixture.index = INDEX;
  // The route the sheet is actually reached on: one sheet deep, with a src.
  win.location.hash = "#/session/gallery/files?src=s1";
  resetRouterCacheForTests();
  seed("admin", CAPS_ADMIN, "idle");
  await act(async () => { root.render(createElement("div")); });
  await mount();

  const picker = tid("files-source");
  assert(picker != null, "no source picker - the assertion below would be vacuous");
  const manual = picker.querySelector('[data-value="manual"]');
  assert(manual != null, "the MANUAL option is missing from the source picker");

  click(manual);
  await settle();

  const route = parseHash(win.location.hash);
  eq(route.sheets.filter((s: string) => s === "files").length, 1,
    `the sheet stacked a duplicate of itself: ${win.location.hash}`);
  eq(route.sheets.length, 1, `the stack grew: ${win.location.hash}`);
  eq(route.params.src, "manual", "the new source never reached the route params");
});

// ============================= 7. the JPEG half is delivered (review #74)
//
// Withholding FITS from a role without `view.media` is right. Leaving that role
// with a picker stuck on JPEG, a locked button, a reason line about a
// capability instead of about where their pictures ARE, and a transfer size for
// a zip that will never be fetched, is not.

await testAsync("an operator forced onto JPEG is told where the JPEGs are, not just what they lack", async () => {
  win.location.hash = "";
  resetRouterCacheForTests();
  fixture.index = {
    target: "M31",
    totals: { frames: 2, accepted: 2, bytes: 0, integration_s: 120 },
    by_filter: [{
      filter: "L", count: 2, accepted: 2, exposure_s: 60, bytes: 0, integration_s: 120,
      frames: [
        { id: "f1", ts: 1, bytes: 0, accepted: true, override: null, hfr: 2.1, stars: 400, guide_rms: 0.5,
          thumb: "/api/sessions/s1/frames/f1/thumb" },
        { id: "f2", ts: 2, bytes: 0, accepted: true, override: null, hfr: 2.2, stars: 380, guide_rms: 0.6,
          thumb: null },
      ],
    }],
  };
  seed("operator", CAPS_OPERATOR, "idle");
  await act(async () => { root.render(createElement("div")); });
  await mount();

  const dl = tid("files-download");
  assert(dl != null, "no download control - the assertions below would be vacuous");
  const why = dl.getAttribute("title") ?? "";
  assert(/JPEG previews instead/.test(why),
    `the reason must say what this role DOES get, got "${why}"`);
  assert(/SAVE JPG/.test(why), `and where to get it, got "${why}"`);
  assert(/syncer or admin access/.test(why),
    "and it still names the capability that withheld the originals");

  // A locked button may not be priced: there is no zip to weigh.
  assert(!/to transfer/.test(text()),
    "a transfer size was printed beside a button that can never fetch one");
  assert(/no zip to weigh/.test(text()),
    `the note must say why there is no size, got "${text().slice(0, 400)}"`);
});

await testAsync("SAVE JPG is a real link at the view.preview route, per frame", async () => {
  click(tid("files-row-L").querySelector("button[aria-expanded]"));
  await settle();

  const a = tid("files-save-jpg-f1");
  assert(a != null, "the per-frame JPEG save never rendered - the JPEG option is still a dead end");
  eq(a.tagName, "A", "an operator holds view.preview, so this must be a live link");
  eq(a.getAttribute("href"), "/api/sessions/s1/frames/f1/thumb",
    "the link must point at the route the server actually serves at view.preview");
  assert((a.getAttribute("download") ?? "").endsWith(".jpg"),
    `the save must name a .jpg, got "${a.getAttribute("download")}"`);

  // The frame with no rendered preview says so rather than linking to a 404.
  const none = tid("files-save-jpg-f2");
  assert(none != null, "the un-rendered frame lost its control entirely");
  eq(none.tagName, "BUTTON", "a frame with no preview must be honest-disabled, not a dead link");
  assert(/rendered no preview/.test(none.getAttribute("title") ?? ""),
    `and say which of the two reasons it is, got "${none.getAttribute("title")}"`);
});

await testAsync("with view.media held the FITS path is unchanged - JPEG did not take it over", async () => {
  seed("admin", CAPS_ADMIN, "idle");
  await settle();
  const dl = tid("files-download");
  eq(dl.tagName, "A", "an admin must still get the live zip link");
  assert(/DOWNLOAD/.test(dl.textContent), "and its priced label");
  // The frame list is still open, so "no SAVE JPG" is a statement about the
  // format and not about a collapsed row.
  assert(tid("files-frames-L") != null,
    "the frame list closed - the absence below would be vacuous");
  assert(tid("files-save-jpg-f1") == null,
    "SAVE JPG is the JPEG format's control and must not ride the FITS selection");
});

act(() => { root.unmount(); });

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`filesDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exitCode?: number } }).process!.exitCode = 1;
}
export default { passed, failed, total };
export { passed, failed, total };
