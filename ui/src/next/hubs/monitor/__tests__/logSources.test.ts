// logSources.test.ts - the ring/night switch, the level filter and the export
// pre-check, with no DOM and no server.
//
//   Run directly:  npx tsx src/next/hubs/monitor/__tests__/logSources.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc`.
//
// WHAT EACH GROUP GUARDS:
//  * TONIGHT MUST NOT ASK. `logQueryPath("")` returning a string would put a
//    request behind every filter tap for rows already in the store.
//  * ONE LEVEL MEANING. The ring filters here and a night filters on the wire;
//    if this function and the `level=` parameter ever disagree, the same night
//    reads differently depending on which chip you came from.
//  * THE EXPORT LOCK. Every reason the route 404s is knowable before the tap.

import type { LogLine } from "../../../../types";
import {
  RING, exportLockedReason, exportPath, filterByLevel, logQueryPath, newestFirst,
  nightOptions, parseLevel, resolveNight, type NightsIndex,
} from "../log/logSources";

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

const line = (ts: number, level: string, message: string, source = "hub"): LogLine =>
  ({ type: "log", ts, data: { level, message, source } });

const IDX: NightsIndex = {
  current: "2026-09-10",
  persisted: true,
  nights: [
    { night: "2026-09-08", bytes: 120 },
    { night: "2026-09-09", bytes: 340 },
    { night: "2026-09-10", bytes: 12 },
  ],
};

// ------------------------------------------------------------ the ring/night switch
test("TONIGHT asks for nothing - the ring is already in the store", () => {
  eq(logQueryPath(RING, ""), null);
  eq(logQueryPath(RING, "error"), null, "a level filter must not turn the ring into a request:");
});

test("a past night carries the night AND the level on the wire", () => {
  eq(logQueryPath("2026-09-09", "error"), "/api/logs?night=2026-09-09&level=error");
  eq(logQueryPath("2026-09-09", ""), "/api/logs?night=2026-09-09");
  eq(logQueryPath("2026-09-09", "warning", 500), "/api/logs?night=2026-09-09&level=warning&limit=500");
});

test("a night id is encoded, never interpolated raw", () => {
  eq(logQueryPath("a b/c", ""), "/api/logs?night=a%20b%2Fc");
});

// ------------------------------------------------------------------ the level filter
test("the level filter keeps only that level, and ALL keeps everything", () => {
  const rows = [line(1, "info", "a"), line(2, "warning", "b"), line(3, "error", "c")];
  eq(filterByLevel(rows, "").length, 3);
  eq(filterByLevel(rows, "error").length, 1);
  eq(filterByLevel(rows, "error")[0].data.message, "c");
  eq(filterByLevel(rows, "warning")[0].data.message, "b");
});

test("filterByLevel does not mutate the store's array", () => {
  const rows = [line(1, "info", "a"), line(2, "error", "b")];
  filterByLevel(rows, "error");
  eq(rows.length, 2, "the ring was filtered in place:");
});

test("a corrupted stored level falls back to ALL, never to a level nobody sends", () => {
  eq(parseLevel("error"), "error");
  eq(parseLevel(""), "");
  eq(parseLevel(null), "");
  eq(parseLevel("EROR"), "");
});

// ------------------------------------------------------------------- newest first
test("rows render newest first, and the source array is left alone", () => {
  const rows = [line(1, "info", "old"), line(2, "info", "new")];
  eq(newestFirst(rows)[0].data.message, "new");
  eq(rows[0].data.message, "old", "newestFirst reversed the caller's array in place:");
});

// ------------------------------------------------------------------- night chips
test("TONIGHT is first, the nights are newest-first, and the current night is not doubled", () => {
  const opts = nightOptions(IDX);
  eq(opts[0].id, RING);
  eq(opts[0].label, "TONIGHT");
  eq(opts.length, 3, "the current night appeared twice (as TONIGHT and as a date):");
  eq(opts[1].id, "2026-09-09");
  eq(opts[2].id, "2026-09-08");
});

test("with persistence off there is only TONIGHT", () => {
  eq(nightOptions({ current: "2026-09-10", persisted: false, nights: [] }).length, 1);
  eq(nightOptions(null).length, 1, "an unread index still offers the ring:");
});

// ------------------------------------------------------------------ the export lock
test("TONIGHT exports the CURRENT night, which only the server can name", () => {
  eq(resolveNight(RING, IDX), "2026-09-10");
  eq(resolveNight("2026-09-08", IDX), "2026-09-08");
  eq(exportPath("2026-09-08", "jsonl"), "/api/logs/export?night=2026-09-08&format=jsonl");
  eq(exportPath("2026-09-08", "txt"), "/api/logs/export?night=2026-09-08&format=txt");
});

test("every 404 the route can raise is refused before the tap", () => {
  eq(exportLockedReason("2026-09-09", IDX), null, "a night on disk must export:");
  assert(
    /never written to disk/.test(exportLockedReason("2026-09-01", IDX) ?? ""),
    "a night with no file offered a download anyway",
  );
  assert(
    /persistence is off/.test(
      exportLockedReason(RING, { current: "2026-09-10", persisted: false, nights: [] }) ?? "",
    ),
    "a rig with the log store off offered a download anyway",
  );
  assert(exportLockedReason(RING, null) != null, "the buttons went live before the index arrived");
});

console.log(`logSources.test: ${passed} passed, ${failed} failed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}

const total = passed + failed;
export default { passed, failed, total };
export { passed, failed, total };
