// filesData.test.tsx - the Files sheet's arithmetic, without a browser.
//
//   Run directly:  npx tsx src/next/hubs/session/sheets/__tests__/filesData.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT IS WORTH A TEST HERE. The fold is where the two halves of this screen
// meet, and each half can lie in a different direction:
//
//   * the LEDGER knows how many frames were shot and which were kept, and
//     knows nothing about bytes on disk;
//   * the LIBRARY knows the bytes and the paths, and knows nothing about
//     grades.
//
// So the assertions below pin exactly which side wins each column, that a
// filter with no ledger behind it reports `accepted: null` and not `0`, and
// that the SELECTION collapses to the filter form only when everything is
// ticked - because the picked form rides in a URL and `downloadPlan` refuses it
// past 6000 characters.
//
// Convention: shell-and-tests.md section 4 (inline test/eq, printed tally plus
// the `{ passed, failed, total }` export, plain-ASCII `x` on failure).

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------- minimal globals, first
// `api.ts` reads `window.location` at MODULE SCOPE, so importing anything that
// reaches it before these exist captures undefined permanently.
class MemStorage {
  private m = new Map<string, string>();
  getItem(k: string): string | null { return this.m.has(k) ? (this.m.get(k) as string) : null; }
  setItem(k: string, v: string): void { this.m.set(k, String(v)); }
  removeItem(k: string): void { this.m.delete(k); }
  clear(): void { this.m.clear(); }
  key(): string | null { return null; }
  get length(): number { return this.m.size; }
}
const g = globalThis as any;
if (typeof g.localStorage === "undefined") g.localStorage = new MemStorage();
if (typeof g.window === "undefined") {
  g.window = {
    location: { pathname: "/", protocol: "http:", host: "test" },
    setTimeout: globalThis.setTimeout.bind(globalThis),
    clearTimeout: globalThis.clearTimeout.bind(globalThis),
    addEventListener() {}, removeEventListener() {},
  };
}

const {
  buildSelection, downloadedLine, filterLabel, foldRows, indexFromSession,
  medianOf, noteDownloaded, noteThroughput, readDownloaded, readThroughput,
  rowSubtitle, selectionCost, tickableRows, totalsOf, transferNote,
  THROUGHPUT_MAX_AGE_MS, UNKNOWN_FILTER,
} = await import("../filesData");
type FilesRowT = import("../filesData").FilesRow;
type SessionFilesIndexT = import("../filesData").SessionFilesIndex;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}

// ------------------------------------------------------------------ fixtures
function frame(over: Partial<any> = {}): any {
  return {
    path: "M31/L_0001.fits", name: "L_0001.fits", folder: "M31",
    night: "2026-09-08", ts: 1_757_000_000, local_date: "2026-09-08",
    local_clock: "22:10", target: "M31", filter: "L", frame_type: "Light",
    exposure_s: 60, bytes: 50 * 1024 * 1024, mtime: 1_757_000_000,
    ...over,
  };
}

const twoFilters = [
  frame({ path: "M31/L_0001.fits", filter: "L", exposure_s: 60, bytes: 10 * 1024 * 1024 }),
  frame({ path: "M31/L_0002.fits", filter: "L", exposure_s: 60, bytes: 10 * 1024 * 1024 }),
  frame({ path: "M31/L_0003.fits", filter: "L", exposure_s: 60, bytes: 10 * 1024 * 1024 }),
  frame({ path: "M31/Ha_0001.fits", filter: "Ha", exposure_s: 300, bytes: 20 * 1024 * 1024 }),
  frame({ path: "M31/Ha_0002.fits", filter: "Ha", exposure_s: 300, bytes: 20 * 1024 * 1024 }),
];

const index: SessionFilesIndexT = {
  target: "M31",
  totals: { frames: 5, accepted: 4, bytes: 0, integration_s: 780 },
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
        { id: "f5", ts: 5, bytes: 0, accepted: true, override: "accept", hfr: 3.1, stars: 118, guide_rms: 0.4, thumb: null },
      ],
    },
  ],
};

// ================================================================= the fold

test("mixed exposures show no misleading single exposure and sum actual integration", () => {
  const frames = [frame({ exposure_s: 60 }), frame({ exposure_s: 120 }), frame({ exposure_s: 10 })];
  const row = foldRows(frames, null)[0];
  eq(row.exposureS, null, "no median presented as a common exposure");
  eq(row.integrationS, 190, "sum the exposures, not median times frame count");
  eq(foldRows(frames, index)[0].exposureS, null, "mixed library settings stay visible with a ledger");
});

test("precondition: the fixture really has two filters and five frames", () => {
  eq(twoFilters.length, 5, "fixture frame count");
  eq(new Set(twoFilters.map((f) => f.filter)).size, 2, "fixture filter count");
});

test("library only: one row per filter, with its own bytes and count", () => {
  const rows = foldRows(twoFilters, null);
  eq(rows.length, 2, "row count");
  const L = rows.find((r) => r.filter === "L") as FilesRowT;
  const Ha = rows.find((r) => r.filter === "Ha") as FilesRowT;
  eq(L.subs, 3, "L subs");
  eq(Ha.subs, 2, "Ha subs");
  eq(L.bytes, 30 * 1024 * 1024, "L bytes are the sum of its frames");
  eq(Ha.bytes, 40 * 1024 * 1024, "Ha bytes are the sum of its frames");
  eq(L.exposureS, 60, "L exposure from the headers");
  eq(Ha.exposureS, 300, "Ha exposure from the headers");
});

test("library only: integration is count x exposure, and grades are NULL not zero", () => {
  const rows = foldRows(twoFilters, null);
  const L = rows.find((r) => r.filter === "L") as FilesRowT;
  eq(L.integrationS, 180, "3 x 60 s");
  eq(L.accepted, null, "nobody graded these - null, not 0");
  eq(L.rejected, null, "nobody graded these - null, not 0");
});

test("ledger + library: the LEDGER owns counts and grades, the LIBRARY owns bytes and paths", () => {
  const rows = foldRows(twoFilters, index);
  const L = rows.find((r) => r.filter === "L") as FilesRowT;
  eq(L.subs, 3, "count from the ledger");
  eq(L.accepted, 2, "accepted from the ledger");
  eq(L.rejected, 1, "rejected derived from the ledger");
  eq(L.integrationS, 120, "integration from the ledger (2 kept x 60 s), not 3 x 60");
  eq(L.bytes, 30 * 1024 * 1024, "bytes from the library, because the ledger stat is 0 here");
  eq(L.paths.length, 3, "paths from the library");
  eq(L.paths[0], "M31/L_0001.fits", "paths are library-relative");
  eq(L.frames.length, 3, "per-frame rows come through for the grade badges");
});

test("ledger order wins, and a library-only filter is still listed", () => {
  const extra = [...twoFilters, frame({ path: "M31/OIII_0001.fits", filter: "OIII", exposure_s: 300, bytes: 1024 })];
  const rows = foldRows(extra, index);
  eq(rows.map((r) => r.filter).join(","), "L,Ha,OIII", "ledger order first, orphan last");
  eq(rows[2].accepted, null, "the orphan has no ledger behind it");
});

test("totals sum every row; tickableRows drops the un-downloadable ones", () => {
  const withEmpty: SessionFilesIndexT = {
    ...index,
    by_filter: [...index.by_filter, { filter: "SII", count: 0, accepted: 0, exposure_s: 600, bytes: 0, integration_s: 0, frames: [] }],
  };
  const rows = foldRows(twoFilters, withEmpty);
  eq(rows.length, 3, "the plan's un-started channel is still a row");
  eq(tickableRows(rows).length, 2, "but it cannot be ticked");
  const t = totalsOf(rows);
  eq(t.subs, 5, "total subs");
  eq(t.bytes, 70 * 1024 * 1024, "total bytes");
  eq(t.integrationS, 720, "total integration from the ledger");
});

test("medianOf resists one mis-stamped header", () => {
  eq(medianOf([60, 60, 6000]), 60, "median, not mean");
  eq(medianOf([]), null, "nothing to take a median of");
});

test("rowSubtitle says which of the four things is true", () => {
  const base: FilesRowT = {
    filter: "L", subs: 3, exposureS: 60, bytes: 0, integrationS: 0,
    paths: [], accepted: null, rejected: null, frames: [],
  };
  assert(/not graded/.test(rowSubtitle(base)), "ungraded rows say so");
  assert(/all passed the quality gates/.test(rowSubtitle({ ...base, accepted: 3, rejected: 0 })),
    "a clean sweep says so");
  assert(/1 rejected/.test(rowSubtitle({ ...base, accepted: 2, rejected: 1 })), "rejects are counted");
  assert(/nothing banked yet/.test(rowSubtitle({ ...base, subs: 0 })), "an empty channel says so");
  assert(/60 s/.test(rowSubtitle(base)), "the exposure is on the line");
});

test("filterLabel never renders an empty string", () => {
  eq(filterLabel(""), "NO FILTER", "a step with no filter");
  eq(filterLabel(UNKNOWN_FILTER), "STEP REMOVED", "a step the plan no longer has");
  eq(filterLabel("Ha"), "Ha", "a real filter passes through");
});

// ============================================================== selection

const scope = { q: "M31", nightFrom: "2026-09-08", nightTo: "2026-09-08" };

test("everything ticked sends the FILTER, not the list", () => {
  const rows = foldRows(twoFilters, index);
  const sel = buildSelection(rows, new Set(["L", "Ha"]), scope);
  eq(sel.mode, "filter", "the filter form");
  assert(sel.mode === "filter" && sel.q === "M31", "the search term rides along");
  assert(sel.mode === "filter" && sel.nightFrom === "2026-09-08", "so does the night bound");
});

test("a subset falls back to an explicit path list", () => {
  const rows = foldRows(twoFilters, index);
  const sel = buildSelection(rows, new Set(["Ha"]), scope);
  eq(sel.mode, "picked", "picked form");
  assert(sel.mode === "picked" && sel.paths.length === 2, "only Ha's two paths");
  assert(sel.mode === "picked" && sel.paths.every((p) => p.includes("Ha")), "and nothing else's");
});

test("forcePicked never widens to the whole library", () => {
  const rows = foldRows(twoFilters, index);
  const sel = buildSelection(rows, new Set(["L", "Ha"]), { q: "", nightFrom: "", nightTo: "" }, true);
  eq(sel.mode, "picked", "an empty filter form would mean EVERY frame on the rig");
  assert(sel.mode === "picked" && sel.paths.length === 5, "all five paths instead");
});

test("selectionCost counts what the zip will contain", () => {
  const rows = foldRows(twoFilters, index);
  const c = selectionCost(rows, new Set(["L"]));
  eq(c.count, 3, "three L paths");
  eq(c.bytes, 30 * 1024 * 1024, "their bytes");
});

// ========================================================== ledger fallback

test("indexFromSession folds a ledger the same way the server does", () => {
  const session: any = {
    id: "s1", schema_version: 1, name: "M31 LRGB", created_ts: 1, updated_ts: 2,
    status: "dormant", nights: ["2026-09-08"], auto_resume: false,
    plan: {
      name: "M31 LRGB", guide: true, dither_every: 1, dither_pixels: null,
      autofocus_every: 0, cool_to: null, cool_timeout_s: null,
      apply_filter_offsets: null, refocus_on_temp_delta_c: null,
      meridian_flip: true, recover_guiding: null, hfr_reject_factor: null,
      park_when_done: true, warm_cooler_when_done: true,
      targets: [{
        id: "t1", name: "M31", ra_hours: 0, dec_deg: 41, center: true,
        autofocus_first: true, calibration: false,
        steps: [
          { id: "st-L", filter: "L", exposure_s: 60, gain: 100, offset: 10, binning: 1, count: 10, frame_type: "Light" },
          { id: "st-Ha", filter: "Ha", exposure_s: 300, gain: 100, offset: 10, binning: 1, count: 10, frame_type: "Light" },
        ],
      }],
    },
    frames: [
      { id: "a", ts: 3, night: "2026-09-08", target_id: "t1", step_id: "st-L", thumb: "a.jpg", metrics: { hfr: 2.0 }, auto_accepted: true, override: null },
      { id: "b", ts: 1, night: "2026-09-08", target_id: "t1", step_id: "st-L", thumb: null, metrics: { hfr: 6.0 }, auto_accepted: false, override: null },
      { id: "c", ts: 2, night: "2026-09-08", target_id: "t1", step_id: "gone", thumb: null, metrics: {}, auto_accepted: true, override: null },
    ],
  };
  const idx = indexFromSession(session);
  eq(idx.target, "M31", "a single target names itself");
  const L = idx.by_filter.find((f) => f.filter === "L");
  assert(L != null, "the L bucket exists");
  eq((L as any).count, 2, "two L frames");
  eq((L as any).accepted, 1, "one kept");
  eq((L as any).integration_s, 60, "only the kept one integrates");
  eq((L as any).frames[0].id, "b", "frames are oldest-first, as shot");
  const orphan = idx.by_filter.find((f) => f.filter === UNKNOWN_FILTER);
  assert(orphan != null, "a frame whose step was edited away still costs bytes and is listed");
  eq(idx.by_filter[0].filter, "L", "order follows the plan, not the shooting order");
  eq(idx.by_filter[1].filter, "Ha", "the un-started channel keeps its place");
});

// =========================================================== per-phone state

test("the on-phone line distinguishes all four states", () => {
  eq(downloadedLine(undefined, 25, false).text, "on the rig only", "nothing recorded");
  eq(downloadedLine(undefined, 25, true).text, "recording", "a live run outranks everything");
  eq(downloadedLine({ at: 1, n: 25 }, 25, false).text, "25 subs on this phone", "complete");
  eq(downloadedLine({ at: 1, n: 10 }, 25, false).text, "10 of 25 on this phone", "partial");
  eq(downloadedLine({ at: 1, n: 10 }, 25, false).tone, "warn", "partial is a warning tone");
});

test("noteDownloaded keeps the HIGHEST count for a session", () => {
  g.localStorage.clear();
  noteDownloaded("s1", 10, 1000);
  noteDownloaded("s1", 4, 2000);
  eq(readDownloaded().s1.n, 10, "a smaller later download must not shrink the claim");
  eq(readDownloaded().s1.at, 2000, "but the timestamp moves");
});

test("a corrupt downloaded-set reads as empty rather than throwing", () => {
  g.localStorage.setItem("astrodeck-next-dl", "{not json");
  eq(Object.keys(readDownloaded()).length, 0, "unparseable storage is no storage");
  g.localStorage.clear();
});

// ============================================================== throughput

test("throughput is an EMA, and a stale or foreign sample is discarded", () => {
  g.localStorage.clear();
  noteThroughput(10, "direct", 1_000_000);
  eq(readThroughput("direct", 1_000_000)?.mbps, 10, "the first sample is the value");
  noteThroughput(20, "direct", 1_000_001);
  const ema = readThroughput("direct", 1_000_001)?.mbps ?? 0;
  assert(ema > 10 && ema < 20, `the second sample moves it partway (got ${ema})`);
  eq(readThroughput("relay", 1_000_001), null, "a relay ETA must not use a Wi-Fi measurement");
  eq(readThroughput("direct", 1_000_001 + THROUGHPUT_MAX_AGE_MS + 1), null, "a day-old sample is gone");
  g.localStorage.clear();
});

test("transferNote makes NO time claim without a measurement", () => {
  const none = transferNote({ bytes: 1024 * 1024 * 1024, via: null, mbps: null, target: "M31", date: "2026-09-08" });
  assert(!/about/.test(none.line), "no ETA without a measured rate");
  assert(/1.0 GB to transfer/.test(none.line), "the size is still stated");
  assert(none.extra != null && /no time estimate/.test(none.extra), "and it says why");
});

test("transferNote names the transport when it knows it", () => {
  const direct = transferNote({ bytes: 100 * 1024 * 1024, via: "direct", mbps: 10, target: "M31", date: "2026-09-08" });
  assert(/direct from the rig/.test(direct.line), "direct is named");
  assert(/about/.test(direct.line), "and carries an ETA");
  assert(/lands in Files > AstroDeck > M31 2026-09-08/.test(direct.line), "and where it lands");
  const relay = transferNote({ bytes: 100 * 1024 * 1024, via: "relay", mbps: 1, target: "M31", date: "" });
  assert(/over the relay/.test(relay.line), "relay is named");
  assert(relay.extra === "On the rig's own Wi-Fi this is faster.", "and the faster path is offered");
});

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`filesData.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exitCode?: number } }).process!.exitCode = 1;
}
export default { passed, failed, total };
export { passed, failed, total };
