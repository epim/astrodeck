// gallery.test.ts — the pure half of the image gallery (lib/gallery.ts).
//
//   Run directly:  npx tsx src/lib/__tests__/gallery.test.ts
//   Also type-checked by `tsc -b` in the build.
//
// Same dependency-free inline-assert harness as bundleView.test.ts — no vitest,
// no jsdom, NO LAYOUT. Nothing below asserts a rendered pixel; everything here
// is either arithmetic, a URL, or a sentence a user reads before losing a file.
//
// The four groups that carry the design, and what each one stops:
//
//   * NIGHT vs FILENAME. The whole feature turns on the fact that an observing
//     night runs noon to noon while the filename carries the calendar date. The
//     source guard at the bottom fails if anyone ever reaches for a date regex
//     in this UI, which is the obvious implementation and the wrong one.
//   * DOWNLOAD REFUSAL. A hand-picked selection rides in the URL; the same
//     frames named by the FILTER do not. The pair of tests below pins that the
//     big case is downloadable exactly when it is expressed as a filter, and
//     that the other case is REFUSED rather than silently truncated into a zip
//     that looks complete.
//   * MISSING vs UNRENDERABLE. 404 and 422 are opposite verdicts, and only one
//     of them takes the download away.
//   * DESTRUCTIVE COPY. Count and size in the words, and the ledger-orphan
//     consequence stated where the decision is made.

import {
  LEDGER_ORPHAN_NOTE, PICKED_URL_BUDGET, TRASH_BATCH_CAP,
  deletedAgo, downloadPath, downloadPlan, filePath, fmtBytes, fmtCount,
  fmtFrameCost, frameSubtitle, framesQuery, framesPath, localDate,
  nightOptionLabel, nightRangeLabel, nightVsFilename, partialFailureNote,
  pickedTotals, purgeConfirmCopy, purgesIn, scanNote, selectionQuery,
  summaryPath, thumbFailure, thumbPath, tileFailureCopy, togglePath,
  tonightHasFrames, trashBatchReason, trashConfirmCopy, truncatedNote,
  type GallerySelection,
} from "../gallery";
import type { GalleryFrame } from "../../types";

// The source guard at the end reads two files off disk. Same dependency-free
// node:fs access idiom as cssClasses.test.ts (the project installs no
// @types/node, and `tsc -b` type-checks everything under src/).
interface NodeFsLike { readFileSync(path: string, encoding: string): string }
const nodeImport = (s: string): Promise<unknown> =>
  (Function("m", "return import(m)") as (m: string) => Promise<unknown>)(s);
const fs = (await nodeImport("node:fs")) as NodeFsLike;
const pathOf = (rel: string): string =>
  decodeURIComponent(new URL(rel, import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, "$1");

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} — expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`);
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function has(hay: string, needle: string, msg = ""): void {
  if (!hay.includes(needle)) throw new Error(`${msg} — ${JSON.stringify(hay)} does not contain ${JSON.stringify(needle)}`);
}

function frame(over: Partial<GalleryFrame> = {}): GalleryFrame {
  return {
    path: "M42/Light_M42_L_2026-06-15_235000_0001.fits",
    name: "Light_M42_L_2026-06-15_235000_0001.fits",
    folder: "M42",
    night: "2026-06-15",
    ts: 1781234000,
    target: "M42",
    filter: "L",
    frame_type: "Light",
    exposure_s: 300,
    bytes: 524880,
    mtime: 1781234567.5,
    ...over,
  };
}

// ================================================================ sizes/counts

test("fmtBytes matches the design's own worked example, 1024-based", () => {
  // 41017345024 bytes is 41.0 GB decimal and 38.2 GB binary. The design writes
  // "38.2 GB", and so does every file manager the user will compare it against.
  eq(fmtBytes(41017345024), "38.2 GB");
});

test("fmtBytes: bytes stay whole, kB stay whole, MB+ get one decimal", () => {
  eq(fmtBytes(0), "0 bytes");
  eq(fmtBytes(512), "512 bytes");
  eq(fmtBytes(1024), "1 kB");
  eq(fmtBytes(524880), "513 kB");
  eq(fmtBytes(21504000), "20.5 MB");
  eq(fmtBytes(5 * 1024 ** 3), "5.0 GB");
});

test("fmtBytes refuses to invent a number it does not have", () => {
  eq(fmtBytes(null), "—");
  eq(fmtBytes(undefined), "—");
  eq(fmtBytes(Number.NaN), "—");
});

test("fmtCount groups with a plain comma, locale-independently", () => {
  eq(fmtCount(0), "0");
  eq(fmtCount(999), "999");
  eq(fmtCount(1284), "1,284");
  eq(fmtCount(1000000), "1,000,000");
});

test("fmtFrameCost is the phrase the buttons wear, and it counts in singular", () => {
  eq(fmtFrameCost(1284, 41017345024), "1,284 frames, 38.2 GB");
  eq(fmtFrameCost(1, 524880), "1 frame, 513 kB");
});

// ================================================================== queries

test("framesQuery omits every empty parameter", () => {
  eq(framesQuery({}), "");
  eq(framesQuery({ q: "", nightFrom: "", nightTo: "", offset: 0 }), "");
  eq(framesQuery({ q: "M42", limit: 200 }), "?q=M42&limit=200");
});

test("framesPath sends the NIGHT key parameters the server documents", () => {
  const p = framesPath({ nightFrom: "2026-06-15", nightTo: "2026-06-15" });
  has(p, "night_from=2026-06-15");
  has(p, "night_to=2026-06-15");
});

test("a filter selection names the set by the filter, not by a list of paths", () => {
  const sel: GallerySelection = { mode: "filter", q: "M42", nightFrom: "2026-06-15", nightTo: "" };
  const s = selectionQuery(sel);
  has(s, "q=M42");
  has(s, "night_from=2026-06-15");
  assert(!s.includes("path="), `a filter selection must never send paths: ${s}`);
});

test("a picked selection repeats `path`, and spaces survive the round trip", () => {
  // The real capture tree has target folders with spaces in them.
  const sel: GallerySelection = { mode: "picked", paths: ["Barnard 33/a.fits", "M42/b.fits"] };
  const s = selectionQuery(sel);
  eq(s, "?path=Barnard+33%2Fa.fits&path=M42%2Fb.fits");
  // "+" is form-encoding for a space, which Starlette's parse_qsl decodes back.
  eq(decodeURIComponent(s.split("path=")[1].split("&")[0].replace(/\+/g, " ")), "Barnard 33/a.fits");
});

test("summary and download take the IDENTICAL query — that is what stops them disagreeing", () => {
  const sel: GallerySelection = { mode: "filter", q: "Ha", nightFrom: "2026-06-15", nightTo: "2026-06-17" };
  eq(summaryPath(sel).split("?")[1], downloadPath(sel).split("?")[1]);
});

test("thumb and file URLs escape the path (a target folder can contain anything)", () => {
  has(thumbPath("Barnard 33/a.fits"), "path=Barnard%2033%2Fa.fits");
  has(filePath("Barnard 33/a.fits"), "path=Barnard%2033%2Fa.fits");
});

test("the thumbnail URL carries the mtime, or a re-capture shows the old picture for an hour", () => {
  // The server keys its thumbnail cache on path+mtime, but the response is
  // max-age=3600 — so without a changing URL the browser keeps the stale image
  // and the server-side correctness never reaches the screen.
  const a = thumbPath("M42/a.fits", 256, 1000);
  const b = thumbPath("M42/a.fits", 256, 2000);
  assert(a !== b, "the same path at two mtimes must produce two URLs");
  has(b, "v=2000");
});

// =============================================================== download plan

test("a huge selection IS downloadable when it is expressed as a filter", () => {
  // 50 000 frames, one short URL. This is the whole reason "select all in
  // filter" exists as a distinct mode.
  const plan = downloadPlan(
    { mode: "filter", q: "", nightFrom: "2026-06-15", nightTo: "2026-06-17" },
    50000,
  );
  assert(plan.ok, "a filter-mode download must never be refused for length");
  if (plan.ok) has(plan.href, "/api/gallery/download.zip?");
});

test("the same frames hand-picked are REFUSED, with the way out named", () => {
  const paths = Array.from({ length: 400 }, (_, i) => `M42/Light_M42_L_2026-06-15_2350${i}_00${i}.fits`);
  const plan = downloadPlan({ mode: "picked", paths }, paths.length);
  assert(!plan.ok, "a 400-path URL is past every proxy's limit and must be refused");
  if (!plan.ok) {
    has(plan.reason, "Select all in filter", "the refusal must name the action that works");
    has(plan.reason, "400");
  }
});

test("the refusal never truncates — a short zip that looks complete is the worst outcome", () => {
  const paths = Array.from({ length: 400 }, (_, i) => `M42/x${i}.fits`.padEnd(40, "y"));
  const plan = downloadPlan({ mode: "picked", paths }, paths.length);
  assert(!plan.ok || downloadPath({ mode: "picked", paths }).length <= PICKED_URL_BUDGET,
    "downloadPlan must either refuse or hand over a link that carries EVERY path");
});

test("an empty selection is refused before a request is made", () => {
  const plan = downloadPlan({ mode: "picked", paths: [] }, 0);
  assert(!plan.ok, "nothing selected must not produce a link");
  if (!plan.ok) has(plan.reason, "Nothing is selected");
});

test("a small picked selection is fine", () => {
  const plan = downloadPlan({ mode: "picked", paths: ["M42/a.fits", "M42/b.fits"] }, 2);
  assert(plan.ok, "two ticked frames must be downloadable");
});

// ================================================================== selection

test("togglePath adds then removes, and never mutates its input", () => {
  const a = new Set<string>(["x"]);
  const b = togglePath(a, "y");
  eq(a.size, 1, "the input set must not be mutated");
  eq(b.has("y"), true);
  eq(togglePath(b, "y").has("y"), false);
});

test("pickedTotals sums only the ticked rows", () => {
  const rows = [frame({ path: "a", bytes: 100 }), frame({ path: "b", bytes: 200 }), frame({ path: "c", bytes: 400 })];
  const t = pickedTotals(new Set(["a", "c"]), rows);
  eq(t.count, 2);
  eq(t.bytes, 500);
});

test("the trash batch cap refuses rather than half-deleting", () => {
  eq(trashBatchReason(TRASH_BATCH_CAP), null);
  const r = trashBatchReason(TRASH_BATCH_CAP + 1);
  assert(r !== null, "past the cap must produce a reason");
  has(r as string, fmtCount(TRASH_BATCH_CAP));
});

// ================================================================ tile states

test("404 and 422 are DIFFERENT verdicts, and only one loses the download", () => {
  eq(thumbFailure(404), "missing");
  eq(thumbFailure(422), "unrenderable");
  eq(thumbFailure(500), "error");
  eq(thumbFailure(403), "error");

  const missing = tileFailureCopy("missing");
  eq(missing.downloadable, false, "a file that is gone has nothing to download");
  has(missing.label, "GONE");

  const unrenderable = tileFailureCopy("unrenderable");
  eq(unrenderable.downloadable, true, "a frame we cannot RENDER is not a frame that is MISSING");
  has(unrenderable.hint, "intact");

  const err = tileFailureCopy("error", 503);
  eq(err.downloadable, true);
  has(err.hint, "503", "an unexplained failure must at least report its status");
});

test("frameSubtitle prints what the header said, and says so when it said nothing", () => {
  eq(frameSubtitle(frame()), "L · 300s · Light");
  eq(frameSubtitle(frame({ filter: "", frame_type: "", exposure_s: null })), "header unreadable");
  eq(frameSubtitle(frame({ filter: "", exposure_s: null })), "Light");
});

// ========================================================= night vs filename

test("a frame whose night and calendar date agree says nothing extra", () => {
  const f = frame();
  // Build a ts that is definitely on the frame's own local calendar date.
  const cal = localDate(f.ts);
  eq(nightVsFilename(frame({ ts: f.ts, night: cal })), null);
});

test("a frame that crossed midnight EXPLAINS itself", () => {
  const f = frame();
  const cal = localDate(f.ts);
  const msg = nightVsFilename(frame({ ts: f.ts, night: "1999-01-01" }));
  assert(msg !== null, "a night/calendar mismatch must be explained, not left to look like a bug");
  has(msg as string, "1999-01-01", "the night the frame is filed under");
  has(msg as string, cal, "the calendar date its FILENAME carries");
  has(msg as string, "noon to noon");
});

test("localDate is a YYYY-MM-DD calendar date", () => {
  assert(/^\d{4}-\d{2}-\d{2}$/.test(localDate(1781234000)), `got ${localDate(1781234000)}`);
});

// ================================================================ night filter

test("a night option says what picking it would get you", () => {
  eq(nightOptionLabel({ night: "2026-06-17", frames: 41, bytes: 21504000 }),
    "2026-06-17 · 41 frames · 20.5 MB");
  eq(nightOptionLabel({ night: "2026-06-18", frames: 1, bytes: 1024 }),
    "2026-06-18 · 1 frame · 1 kB");
});

test("the range reads as words, and an equal pair is 'that one night'", () => {
  eq(nightRangeLabel("", ""), "All nights");
  eq(nightRangeLabel("2026-06-15", "2026-06-15"), "Night of 2026-06-15");
  eq(nightRangeLabel("2026-06-15", "2026-06-17"), "Nights 2026-06-15 to 2026-06-17, inclusive");
  eq(nightRangeLabel("2026-06-15", ""), "2026-06-15 and later");
  eq(nightRangeLabel("", "2026-06-17"), "Up to and including 2026-06-17");
});

test("tonight is only offered when tonight has produced something", () => {
  const nights = [{ night: "2026-08-02", frames: 3, bytes: 30 }];
  eq(tonightHasFrames("2026-08-02", nights), true);
  eq(tonightHasFrames("2026-08-03", nights), false);
});

// ============================================================ destructive copy

test("the delete confirmation states the cost AND the ledger consequence", () => {
  const c = trashConfirmCopy(1284, 41017345024, 30);
  has(c.title, "1,284 frames, 38.2 GB", "the confirmation must price the action");
  has(c.body, "30 days");
  has(c.body, LEDGER_ORPHAN_NOTE,
    "deleting a frame orphans the report that references it — that must be said where the decision is made");
});

test("the ledger note names the actual consequence, not a vague warning", () => {
  has(LEDGER_ORPHAN_NOTE, "missing in the report");
  has(LEDGER_ORPHAN_NOTE, "nothing repairs that");
});

test("the purge confirmation puts the count and size in the title and does not soften it", () => {
  const c = purgeConfirmCopy(1284, 41017345024);
  has(c.title, "Permanently delete");
  has(c.title, "1,284 files");
  has(c.title, "38.2 GB");
  has(c.body, "no restore after this");
  has(c.body, "Hold the button", "the second step of the two-step is named in the first");
  eq(purgeConfirmCopy(1, 1024).title, "Permanently delete 1 file, 1 kB?");
});

test("a partial failure is reported as a partial failure", () => {
  eq(partialFailureNote(3, []), null);
  const n = partialFailureNote(3, [
    { path: "M42/a.fits", reason: "no longer on disk" },
    { path: "M42/b.fits", reason: "not a frame file" },
  ]);
  has(n as string, "3 done, 2 refused");
  has(n as string, "M42/a.fits: no longer on disk");
  has(n as string, "and 1 more");
});

test("a truncated walk says it is a prefix, with the number", () => {
  has(truncatedNote(5000), "5,000");
  has(truncatedNote(5000), "prefix of the");
});

test("the cold-scan note only fires when the scan was actually slow", () => {
  eq(scanNote(4.2, 293), null);
  const n = scanNote(1381, 293);
  assert(n === null, "1.4s is under the threshold and must stay quiet");
  const slow = scanNote(4000, 50000);
  assert(slow !== null, "a 4s scan must be explained rather than looking hung");
  has(slow as string, "4.0s");
  has(slow as string, "50,000");
});

test("trash deadlines are stated in days, not epochs", () => {
  const now = 1781234000;
  eq(purgesIn(now + 12 * 86400, now), "purges in 12 days");
  eq(purgesIn(now + 86400 * 1.5, now), "purges tomorrow");
  eq(purgesIn(now + 3600 * 3, now), "purges in 3 hours");
  // The auto-purge is a 6-hourly sweep, not an instant: an already-expired item
  // can still be sitting in the list, and the copy admits it.
  eq(purgesIn(now - 10, now), "purges on the next sweep");
});

test("deletion times are relative, because 'when did I delete this' is a relative question", () => {
  const now = 1781234000;
  eq(deletedAgo(now - 10, now), "just now");
  eq(deletedAgo(now - 600, now), "10 minutes ago");
  eq(deletedAgo(now - 3600 * 5, now), "5 hours ago");
  eq(deletedAgo(now - 86400 * 12, now), "12 days ago");
});

// ============================================================== source guards

test("NOTHING in the gallery UI parses a date out of a filename", () => {
  // GROUNDED #1. The default naming template writes the CALENDAR date, so a
  // filename-derived filter splits every real session at midnight and returns
  // half a night while looking like it worked. The night always comes from the
  // server, which derives it from the capture instant. A four-digit-year regex
  // appearing anywhere in this UI is the tell that someone reached for the
  // obvious implementation.
  for (const rel of ["../gallery.ts", "../../views/GalleryView.tsx", "../../components/gallery/FrameTile.tsx"]) {
    const src = fs.readFileSync(pathOf(rel), "utf8")
      // Strip comments: this file's own prose (and the view's header) explains
      // the trap at length, and that must not trip the check.
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/(^|[^:])\/\/[^\n]*/g, "$1");
    assert(!/\\d\{4\}|\\d\{2\}-\\d\{2\}/.test(src),
      `${rel} contains a date-shaped regex. The observing night must come from the ` +
      `server's night_key (capture instant), never from the frame's name.`);
  }
});

test("the bulk download is a link, never a fetch-into-a-Blob", () => {
  const view = fs.readFileSync(pathOf("../../views/GalleryView.tsx"), "utf8");
  assert(!/createObjectURL|new Blob|res\.blob\(\)/.test(view),
    "a streamed multi-GB archive fetched into a Blob is the whole archive back in memory — " +
    "the download must stay a plain navigation (the session is a cookie, so it authenticates).");
  assert(/href=\{`\$\{BASE\}/.test(view), "the download must be an <a href> built on BASE");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\ngallery.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
