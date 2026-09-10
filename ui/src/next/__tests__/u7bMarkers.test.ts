// u7bMarkers.test.ts - the probe markers wave 2 (U7b) promised, and a scan
// that proves the tree still emits every one of them.
//
//   Run directly:  npx tsx src/next/__tests__/u7bMarkers.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// WHY THIS FILE EXISTS. Each U7b feature task's report named a set of new
// `data-testid` markers for T-R7-21b to add to `tools/ui_probe/routes_next.json`
// - the probe's route/marker list is a hand-maintained JSON file, and a marker
// promised in a report but never actually rendered (a rename, a dropped
// branch, a merge conflict that took the wrong side) would sit in that file
// forever pointing at nothing, which is invisible until someone reads the
// probe's screenshots and finds a click that never fires. `U7B_MARKERS` below
// is the single collected list - grouped by the route each marker's owning
// screen mounts at - so T-R7-21b has one source to transcribe from, and this
// test is what keeps that source honest: every marker in it must appear as a
// real `data-testid` in the tree, not just in a report.
//
// TEMPLATE-LITERAL FAMILIES. A handful of markers are not fixed strings in the
// source - `port-settings-${p.id}`, `video-roi-preset-${p.label.toLowerCase()}`
// - because the row they mark is per-port or per-preset. `U7B_MARKERS` still
// lists these as concrete strings (`port-settings-<id>` for the id-suffixed
// family; the four literal preset names for the enum-suffixed one), because
// that is what the wave's reports actually asked for and what a reader of
// this file expects to see. `isEmitted` tries an exact match first, then ONE
// strip - the literal `<id>` suffix, or the marker's own trailing `-word`
// segment - and checks whether what is left is a template's own static
// prefix. ONE strip only, not a cascade: a cascading strip walked
// `port-lockout-<id>` (a marker that does not exist, planted while testing
// this file) down through `port-lockout-` to the real but UNRELATED `port-`
// prefix that the plain port row also emits, and passed it - which would
// have defeated the very sabotage check this file exists to run. See
// `isEmitted`'s own comment for the fixed, single-strip version.
//
// SABOTAGE CHECK (described here, not left red in the committed file - the
// harness runs this file as part of `npm test` and a red file fails the
// build): add a marker no component renders, e.g. push `"port-lockout-<id>"`
// onto the `#/rig/devices/power` group below, and re-run. "every U7B_MARKERS
// entry is emitted somewhere under ui/src/next" goes red naming that exact
// string, because no source file anywhere defines a `port-lockout-` prefix or
// the literal string. Remove the line to restore green. Verified by hand
// while writing this file, then reverted before committing.

const { readFileSync, readdirSync, statSync } = await import("node:fs");
const { fileURLToPath } = await import("node:url");

/** `ui/src/next/`, with a trailing separator, in native path form. */
const NEXT = fileURLToPath(new URL("../", import.meta.url));
const SEP = NEXT.includes("\\") ? "\\" : "/";
const join = (...parts: string[]): string => parts.join(SEP).replace(/[\\/]+/g, SEP);

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try {
    fn();
    passed++;
  } catch (e) {
    failed++;
    failures.push(`x ${name}: ${(e as Error).message}`);
  }
}
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}

// ================================================================ the list
//
// Collected from every "landed - routed" section of WAVE2-RULINGS.md for
// U7b-1 through U7b-8, plus U7b-9's two (`optics-aperture`/`optics-reducer`
// were the brief's placeholder names; the sheet already had `optics-tile-
// aperture`/`optics-tile-reducer` for the readouts, so what actually shipped
// new is the unsupported-engine card and the migration note - see
// `DEVIATIONS.md`'s Settings table, D-SET-1). U7b-10 (the OSC colour label)
// and U7b-11 (storage migration) requested no new markers - their own briefs
// say so verbatim.
//
// Grouped by the hash route the owning screen mounts at (ARCHITECTURE.md
// section 3's grammar), because that is the shape `routes_next.json` wants.

export const U7B_MARKERS: Record<string, readonly string[]> = {
  // T-U7b-1: satellites, comets, passes (D-SKY-1). PassesCard and the
  // satellite/comet target rows live in the Sky targets sheet.
  "#/sky/targets": [
    "sky-passes", "sky-pass-row", "sky-pass-sunlit", "sky-passes-stale",
    "sky-passes-note", "sky-passes-none", "sky-passes-withheld",
    "sky-passes-retry", "target-row-satellite", "target-row-comet",
    "targets-ephemeris", "ephemeris-note", "ephemeris-settings-link",
    "comet-pick", "comet-geocentric",
  ],
  // T-U7b-1: the ephemeris card, renamed into the SKY DATA settings sheet.
  "#/settings/general/skyPack": [
    "settings-ephemeris", "ephemeris-refresh", "ephemeris-row-satellites",
    "ephemeris-row-comets", "ephemeris-note-satellites",
    "ephemeris-note-comets", "ephemeris-message", "ephemeris-error",
    "ephemeris-lock", "ephemeris-absent",
  ],
  // T-U7b-2: the channel strip's real per-channel preview (D-SES-1).
  // `channel-tint-note` was REMOVED (the CSS tint it named is gone) and is
  // deliberately absent from this list.
  "#/session/now": [
    "channel-missing-note", "channel-ledger-note",
  ],
  // T-U7b-3: the promote route, SAVE TO GALLERY (D-SES-4).
  "#/rig/capture": [
    "result-save-gallery", "result-save-as-target", "result-promote-note",
    "result-saved-path",
  ],
  // T-U7b-6: FOLLOW DEW and the dew ramp on the camera sheet's sensor window
  // (D-RIG-3, camera half).
  "#/rig/devices/camera": [
    "camera-follow-dew", "dew-loop-line", "dew-manual-note", "dew-ramp",
    "dew-margin-full", "dew-margin-off", "dew-min-power", "dew-max-power",
    "dew-override", "dew-refusal",
  ],
  // T-U7b-6: the dew-loop status line under Weather's Conditions band.
  // Renders only once the server has ticked a `status.dew` node, per the
  // ruling.
  "#/weather/conditions": [
    "wx-dew-loop",
  ],
  // T-U7b-7: per-port PROTECT DURING RUN and FOLLOW DEW (D-RIG-5, D-RIG-3
  // power half). All three are per-port families; the scan below resolves
  // the `<id>` suffix against the port row's own template literal.
  "#/rig/devices/power": [
    "port-settings-<id>", "port-protect-<id>", "port-follow-dew-<id>",
  ],
  // T-U7b-5: temperature compensation (D-RIG-2).
  "#/rig/devices/focuser": [
    "focuser-tempcomp", "tempcomp-switch", "tempcomp-coefficient",
    "tempcomp-sign", "tempcomp-effect", "tempcomp-next", "tempcomp-reason",
    "tempcomp-reanchor", "tempcomp-advanced", "tempcomp-maxstep",
    "tempcomp-deadband", "tempcomp-precedence", "tempcomp-absent",
  ],
  // T-U7b-8: the real slew ceiling and the RA/DEC step tiles (D-RIG-4).
  "#/rig/devices/mount": [
    "mount-slew-rate", "mount-ra-step", "mount-dec-step", "mount-rate-note",
  ],
  // T-U7b-4: VIDEO mode (D-RIG-1) - ROI, controls, progress, refusals,
  // recordings.
  "#/rig/capture?mode=video": [
    "rig-capture-video", "capture-still", "video-roi",
    "video-roi-preset-full", "video-roi-preset-1024", "video-roi-preset-640",
    "video-roi-preset-320", "video-roi-x", "video-roi-y", "video-roi-w",
    "video-roi-h", "video-roi-bin", "video-controls", "video-fps",
    "video-exposure", "video-gain", "video-duration", "video-estimate",
    "video-clamp", "video-progress", "video-progress-bar", "video-error",
    "video-record", "video-stop", "video-stop-other", "video-refusal",
    "video-server-refusal", "video-disk", "video-files-link",
    "video-no-route", "video-recordings", "video-recording-row",
    "video-download", "video-download-locked", "video-stack",
    "video-stack-image", "video-stack-locked", "video-delete",
    "video-all-recordings",
  ],
  // T-U7b-4: the recordings library sheet.
  "#/rig/capture/videoLibrary": [
    "rig-video-library", "video-library-body", "video-library-total",
  ],
  // T-U7b-9: the aperture/reducer migration (D-SET-1, closing D-FU-1).
  "#/settings/general/optics": [
    "optics-aperture-unsupported", "optics-legacy-note",
  ],
};

/** Every marker, flattened, with its `<id>`-family suffix (if any) stripped
 *  to nothing so duplicate checks and the emission scan share one shape. */
const ALL_MARKERS: readonly string[] =
  Object.values(U7B_MARKERS).flat();

// ============================================================== the corpus
//
// Same shape as `r7Css.test.ts`'s scan: walk every module under `next/`,
// excluding tests (a test file references a marker in an assertion string
// without ever rendering it, which would make the scan trivially pass a
// marker nothing actually emits - the one failure mode this file exists to
// catch).

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else out.push(p);
  }
  return out;
}

const ALL = walk(NEXT.slice(0, -1));
const isTest = (p: string): boolean =>
  p.includes(`${SEP}__tests__${SEP}`) || /\.test\.tsx?$/.test(p);
const MODULES = ALL.filter((p) => /\.tsx?$/.test(p) && !p.endsWith(".d.ts") && !isTest(p));

const src = new Map<string, string>();
const read = (p: string): string => {
  let s = src.get(p);
  if (s === undefined) { s = readFileSync(p, "utf8"); src.set(p, s); }
  return s;
};

/** Comments out of a TS/TSX source, line comments first (a block-comment
 *  strip run first would swallow a line comment containing a stray `/*`
 *  inside a path, as `r7Css.test.ts` documents finding). */
const stripComments = (s: string): string =>
  s.replace(/(^|[^:])\/\/[^\n]*/g, "$1").replace(/\/\*[\s\S]*?\*\//g, "");

/** Every `data-testid` literal a module emits: `exact` for a plain string (or
 *  a template literal with no interpolation), `prefix` for a template
 *  literal's static lead-in up to its first `${`. */
interface Emission { exact: Set<string>; prefix: Set<string> }

const TESTID_RE = /data-testid=(?:"([^"]*)"|'([^']*)'|\{`([^`]*)`\})/g;

function emissionsOf(p: string): Emission {
  const text = stripComments(read(p));
  const exact = new Set<string>();
  const prefix = new Set<string>();
  for (const m of text.matchAll(TESTID_RE)) {
    const plain = m[1] ?? m[2];
    if (plain !== undefined) { if (plain) exact.add(plain); continue; }
    const tpl = m[3] ?? "";
    const at = tpl.indexOf("${");
    if (at < 0) { if (tpl) exact.add(tpl); continue; }
    if (at > 0) prefix.add(tpl.slice(0, at));
  }
  return { exact, prefix };
}

const EMITS = new Map<string, Emission>(MODULES.map((p) => [p, emissionsOf(p)]));

const EXACT_TESTIDS = new Set<string>();
const PREFIX_TESTIDS = new Set<string>();
for (const e of EMITS.values()) {
  for (const c of e.exact) EXACT_TESTIDS.add(c);
  for (const c of e.prefix) PREFIX_TESTIDS.add(c);
}

/** Is `marker` a real `data-testid` somewhere under `ui/src/next`? An exact
 *  literal match wins outright. Otherwise ONE strip only - either the literal
 *  `<id>` placeholder suffix (`port-settings-<id>` -> `port-settings-`) or
 *  the marker's own trailing `-word` segment (`video-roi-preset-full` ->
 *  `video-roi-preset-`) - and the remaining prefix must match a template
 *  literal's own static lead-in exactly.
 *
 *  ONE strip, not a cascade: a second strip would keep shortening toward
 *  common short prefixes real but UNRELATED templates also start with
 *  (`power.tsx` also emits `` `port-${p.id}` `` for the port row itself, so a
 *  cascading strip would walk `port-lockout-<id>` down through
 *  `port-lockout-` to `port-` and find that instead - which is exactly the
 *  false pass that would defeat the sabotage check below). A marker with no
 *  template behind it at all, or whose one candidate prefix is not a real
 *  emission, returns false. */
function isEmitted(marker: string): boolean {
  if (EXACT_TESTIDS.has(marker)) return true;
  if (marker.endsWith("<id>")) return PREFIX_TESTIDS.has(marker.slice(0, -"<id>".length));
  const dash = marker.lastIndexOf("-");
  if (dash < 0) return false;
  return PREFIX_TESTIDS.has(marker.slice(0, dash + 1));
}

// =========================================================== 1. the corpus
// Same floor-not-fixture shape as the css scan: a scan that finds nothing
// passes every other test here, so the numbers are checked first.

test("the scan found modules and data-testid emissions", () => {
  assert(MODULES.length >= 200, `only ${MODULES.length} modules found under next/ - is the walk working?`);
  assert(EXACT_TESTIDS.size >= 200, `only ${EXACT_TESTIDS.size} exact data-testid literals found`);
  assert(PREFIX_TESTIDS.size >= 5, `only ${PREFIX_TESTIDS.size} template data-testid prefixes found`);
  assert(EXACT_TESTIDS.has("rig-capture-video"), "VideoMode.tsx's rig-capture-video did not parse");
  assert(PREFIX_TESTIDS.has("port-settings-"), "power.tsx's port-settings-${p.id} did not parse to a prefix");
});

// ==================================================== 2. the list itself
// U7B_MARKERS is a flat record of arrays; catch a typo'd duplicate (the same
// marker filed under two routes) before it reaches the emission check, where
// it would just look like two passing assertions for one real control.

test("no marker is listed under two routes", () => {
  const seenAt = new Map<string, string>();
  const dupes: string[] = [];
  for (const [route, markers] of Object.entries(U7B_MARKERS)) {
    for (const m of markers) {
      const first = seenAt.get(m);
      if (first) dupes.push(`${m} listed under both ${first} and ${route}`);
      else seenAt.set(m, route);
    }
  }
  assert(dupes.length === 0, dupes.join("; "));
});

test("the collected list has the expected shape", () => {
  assert(Object.keys(U7B_MARKERS).length === 12, `expected 12 routes, found ${Object.keys(U7B_MARKERS).length}`);
  assert(ALL_MARKERS.length === 106, `expected 106 markers total, found ${ALL_MARKERS.length}`);
  assert(!ALL_MARKERS.includes("channel-tint-note"),
    "channel-tint-note was REMOVED by T-U7b-2 and must not be in the collected list");
});

// ================================================ 3. every marker is real
//
// The test this file exists for. Every U7B_MARKERS entry must be a
// `data-testid` a component actually emits, whether a plain literal or a
// template literal's static prefix.
//
// Sabotage (see the file header): add a marker no component renders and this
// goes red naming it.

test("every U7B_MARKERS entry is emitted somewhere under ui/src/next", () => {
  const missing = ALL_MARKERS.filter((m) => !isEmitted(m));
  assert(missing.length === 0,
    `${missing.length} marker(s) not found as a data-testid anywhere under next/: ${missing.join(", ")}`);
});

// ---------------------------------------------------------------- report
const total = passed + failed;
console.log(`u7bMarkers.test: ${passed}/${total} passed`);
for (const f of failures) console.error(f);
if (failed > 0) process.exitCode = 1;

export { passed, failed, total };
