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
  fmtFrameCost, frameSubtitle, framesQuery, framesPath,
  nightOptionLabel, nightRangeLabel, nightVsFilename, partialFailureNote,
  pickedTotals, purgeConfirmCopy, purgesIn, scanNote, selectionQuery,
  selectRange, thumbFailure, thumbPath, tileFailureCopy, togglePath,
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
    // Rig-local, from the server — never derived from `ts` on this side. See
    // the night-vs-filename tests below for why the distinction is the feature.
    local_date: "2026-06-15",
    local_clock: "23:50",
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

test("selectRange ticks everything between the two ends, inclusive", () => {
  const order = ["a", "b", "c", "d", "e"];
  const got = selectRange(new Set<string>(), order, "b", "d");
  eq([...got].sort().join(","), "b,c,d");
});

test("selectRange works in either direction", () => {
  const order = ["a", "b", "c", "d", "e"];
  eq([...selectRange(new Set<string>(), order, "d", "b")].sort().join(","), "b,c,d");
});

test("selectRange is ADDITIVE - it never clears ticks outside the range", () => {
  // The flow this exists for is "sweep the bad run, then untick the good ones".
  // A range that replaced the selection would throw away the earlier sweep.
  const order = ["a", "b", "c", "d", "e"];
  const got = selectRange(new Set(["a"]), order, "c", "d");
  eq([...got].sort().join(","), "a,c,d");
});

test("selectRange follows RENDERED order, not the order the paths arrived in", () => {
  // The grid is filtered and sorted; "everything in between" means between on
  // screen. Reversing the rendered order must reverse which rows are caught.
  const got = selectRange(new Set<string>(), ["e", "d", "c", "b", "a"], "e", "c");
  eq([...got].sort().join(","), "c,d,e");
});

test("selectRange leaves the set alone when an endpoint is not on screen", () => {
  // A row that scrolled out of a re-filtered page has no position, and a span
  // guessed from one endpoint would tick rows the operator never saw.
  const before = new Set(["a"]);
  const got = selectRange(before, ["a", "b"], "zzz", "b");
  eq([...got].sort().join(","), "a");
  eq(before.size, 1, "the input set must not be mutated");
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
  eq(nightVsFilename(frame({ local_date: "2026-06-15", night: "2026-06-15" })), null);
});

test("a frame that crossed midnight EXPLAINS itself", () => {
  const msg = nightVsFilename(frame({
    night: "2026-06-15", local_date: "2026-06-16", local_clock: "00:12",
  }));
  assert(msg !== null, "a night/calendar mismatch must be explained, not left to look like a bug");
  has(msg as string, "2026-06-15", "the night the frame is filed under");
  has(msg as string, "2026-06-16", "the calendar date its FILENAME carries");
  has(msg as string, "00:12", "the clock it was actually shot at");
  has(msg as string, "noon to noon");
});

test("the rollover sentence speaks the RIG's clock, never the browser's", () => {
  // The sharp end of GROUNDED #1. `night` was computed with the RIG's
  // localtime; a browser reaching the rig through the relay is in its own
  // timezone. A UI that derived the calendar date from `ts` would, for a rig in
  // Arizona and a user in London, put EVERY frame's date a day ahead of its
  // night — announcing a rollover on the whole library, at a wall-clock time no
  // frame was taken at. So `ts` here is deliberately absurd (the epoch) while
  // the server-supplied rig-local pair is real: anything that reads `ts` prints
  // 1970 and fails.
  const msg = nightVsFilename(frame({
    ts: 0, night: "2026-06-15", local_date: "2026-06-16", local_clock: "00:12",
  }));
  has(msg as string, "00:12 on 2026-06-16",
    "the clock and date must be the ones the server sent");
  assert(!(msg as string).includes("1970"),
    "the sentence was derived from `ts` in the VIEWER's timezone — both dates in this " +
    "comparison have to be the observatory's or the comparison means nothing");
});

test("a frame the server priced without a local date says nothing rather than guessing", () => {
  // Defensive: an older server (or a hand-built row) has no local_date. Silence
  // is the only honest answer — the alternative is inventing the browser's date.
  eq(nightVsFilename(frame({ local_date: "" })), null);
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

test("NOTHING in the gallery UI derives a date from a filename or the browser's clock", () => {
  // GROUNDED #1 has TWO ways to break, and the first spelling of this guard
  // only watched one of them — which is why the other one shipped underneath it.
  //
  //   (a) PARSE THE NAME. The default template writes the CALENDAR date, so a
  //       filename-derived filter splits every real session at midnight and
  //       returns half a night while looking like it worked.
  //   (b) FORMAT `ts` HERE. `night` was computed with the RIG's localtime. A
  //       browser on the relay is in its own timezone, so a client-side
  //       calendar date compares Arizona's night against London's date and
  //       declares a rollover on every frame in the library.
  //
  // Both dates come from the server (`night` and `local_date`, computed one
  // line apart from the same localtime). So neither a date-shaped pattern nor a
  // Date field accessor belongs in these three files — and the previous check
  // matched only the literal text `\d{4}`, which `[0-9]{4}`, a runtime-built
  // RegExp, or a plain `f.name.split("_")[3]` all walk straight past.
  const banned: [RegExp, string][] = [
    [/\\d\{\d/, "a \\d{n} date-shaped regex"],
    [/\[0-9\]/, "a [0-9] character class — the same regex, spelled around the last guard"],
    [/new RegExp/, "a regex assembled at runtime, which no source scan can read"],
    [/\.(name|path)\.split\(/, "a filename/path split — the likeliest parser of all (a)"],
    [/getFullYear|getMonth\(|getDate\(|getHours|getMinutes/,
      "the BROWSER's calendar/clock, which is not the observatory's (b)"],
  ];
  for (const rel of ["../gallery.ts", "../../views/GalleryView.tsx", "../../components/gallery/FrameTile.tsx"]) {
    const src = fs.readFileSync(pathOf(rel), "utf8")
      // Strip comments: this file's own prose (and the view's header) explains
      // the trap at length, and that must not trip the check.
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/(^|[^:])\/\/[^\n]*/g, "$1");
    for (const [re, why] of banned) {
      assert(!re.test(src),
        `${rel} contains ${why}. The observing night and the frame's local date must BOTH ` +
        `come from the server (night_key / local_date) — they are the only two computed in ` +
        `the observatory's timezone, and comparing them against anything else is meaningless.`);
    }
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
