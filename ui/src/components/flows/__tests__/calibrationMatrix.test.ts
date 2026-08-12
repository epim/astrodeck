// calibrationMatrix.test.ts — the honesty rules of the LIBRARY HEALTH block.
//   Run:  npx tsx src/components/flows/__tests__/calibrationMatrix.test.ts   (from ui/)
//
// There is exactly one piece of real logic in CalibrationMatrix and it is not
// the grid: it is the decision about what an EMPTY matrix means. The route
// returns rows only for lights that are planned, so "no rows" is the same
// pixels for three different facts — nothing planned yet (`planned: false`), a
// request that failed (`calHealth === null`), and a library that genuinely has
// everything. calibration_health.py's docstring is explicit that the first must
// never be drawn as the third, and `flowsApi.calibrationHealth`'s comment says
// it again. A regression here does not throw and does not look wrong; it just
// tells the operator their darks are fine on a night they have none.
//
// The other rule pinned here is that the UI never re-words the server. §G-10(a)
// records that `summary` cannot reproduce the screenshot's BIAS and FLAT strings
// and rules that we must not synthesise a third phrasing — so the row reader is
// checked for passing text through untouched, including an empty one.
/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------- globals first
// CalibrationMatrix imports ../../store -> lib/api -> lib/base, and base.ts
// reads `window.location.pathname` AT MODULE SCOPE. So the import has to happen
// after a window exists, which means a dynamic import. Nothing here fakes
// behaviour the tests then assert on - no request is made and no component is
// rendered.
(globalThis as any).window = {
  location: { pathname: "/", origin: "http://local" },
  matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
};
(globalThis as any).localStorage = {
  getItem: () => null, setItem() {}, removeItem() {},
};
// store.ts reflects the persisted night / touch-sizing / brightness prefs onto
// <html> at module scope. With localStorage empty these are all no-ops, so the
// stub only has to exist for the assignments to land somewhere.
(globalThis as any).document = {
  documentElement: {
    classList: { add() {}, remove() {}, toggle() {} },
    style: { setProperty() {} },
  },
};

const { verdictVar, calHealthNotes, readCalRow } =
  await import("../CalibrationMatrix");
type FlowCalHealth = import("../flowsTypes").FlowCalHealth;

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

function health(over: Partial<FlowCalHealth> = {}): FlowCalHealth {
  return {
    rows: [], planned: true, counts_masters_only: false,
    assumed: { offset: 30, temp_c: null },
    ...over,
  };
}

// ────────────────────────────────────────────────────── the verdict → token

test("the three verdicts map to the three status tokens, not to hex", () => {
  assert(verdictVar("OK") === "var(--good)", "OK must be --good");
  assert(verdictVar("STALE") === "var(--warn)", "STALE must be --warn");
  assert(verdictVar("MISSING") === "var(--bad)", "MISSING must be --bad");
});

test("an unrecognised verdict is never drawn green", () => {
  // The server's verdict vocabulary could grow. Anything not on the list is
  // neutral ink: a new verdict painted --good would be reported as a healthy
  // library by a UI that has no idea what it is looking at.
  const v = verdictVar("PARTIAL");
  assert(v !== "var(--good)", `unknown verdict took the healthy colour: ${v}`);
  assert(v === "var(--text-dim)", `expected neutral ink, got ${v}`);
});

// ───────────────────────────────────────────────── the empty-matrix problem

test("a failed request says so — it is NOT rendered as an empty library", () => {
  const notes = calHealthNotes(null);
  assert(notes.length === 1, `expected one line, got ${notes.length}`);
  assert(/could not read/i.test(notes[0].text),
    `a null health must state the read failed, got: ${notes[0].text}`);
  assert(notes[0].tone === "warn",
    "an unanswered question must not be drawn in the quietest ink available");
});

test("planned:false is stated, so an empty matrix cannot read as healthy", () => {
  const notes = calHealthNotes(health({ planned: false }));
  const planned = notes.find((n) => n.key === "planned");
  assert(!!planned, "planned:false produced no line at all — the empty grid then "
    + "says the library has everything, which is the exact lie the route's "
    + "docstring forbids");
  assert(planned!.tone === "warn", "the no-lights-planned line is the one flag that "
    + "would otherwise be read as health; it must not be a faint aside");
});

test("planned:true adds no no-lights-planned line", () => {
  const notes = calHealthNotes(health({ planned: true }));
  assert(!notes.some((n) => n.key === "planned"),
    "a planned flow must not be told there is nothing planned");
});

test("counts_masters_only is always shown, and says what it costs", () => {
  const notes = calHealthNotes(health({ planned: true, counts_masters_only: true }));
  const m = notes.find((n) => n.key === "masters");
  assert(!!m, "counts_masters_only:true produced no line — `have` then reads as a "
    + "frame count it is not, and a row shows MISSING over a disk full of subs");
  assert(/MISSING/.test(m!.text),
    `the line must name the consequence, got: ${m!.text}`);
});

test("both honesty flags survive together", () => {
  const notes = calHealthNotes(health({ planned: false, counts_masters_only: true }));
  assert(notes.some((n) => n.key === "planned") && notes.some((n) => n.key === "masters"),
    "one flag must not suppress the other — they are separate facts");
});

test("the assumed offset and temperature are named as assumptions", () => {
  const unknown = calHealthNotes(health({ assumed: { offset: 30, temp_c: null } }));
  const a = unknown.find((n) => n.key === "assumed");
  assert(!!a, "assumed produced no line");
  assert(a!.text.indexOf("30") >= 0, `the offset must be the one that was used: ${a!.text}`);
  assert(/unknown/.test(a!.text),
    `a null sensor temperature is unknown, not 0: ${a!.text}`);

  const known = calHealthNotes(health({ assumed: { offset: 50, temp_c: -5 } }));
  const b = known.find((n) => n.key === "assumed")!;
  assert(b.text.indexOf("50") >= 0 && b.text.indexOf("-5") >= 0,
    `a known temperature is printed as given: ${b.text}`);
});

// ──────────────────────────────────────────────────────── the row passthrough

test("the four columns are read straight off the row", () => {
  const r = readCalRow({
    kind: "DARK", label: "DARKS", summary: "180s g100 · −5°C",
    quantity: "14/20", verdict: "STALE", age_days: 41, master_id: "m1",
  }, 0);
  assert(r.label === "DARKS", `label: ${r.label}`);
  // Including U+2212. The DARK row matches the design fixture byte-for-byte and
  // an "ASCII fix" anywhere on this path would break that.
  assert(r.summary === "180s g100 · −5°C", `summary was re-worded: ${r.summary}`);
  assert(r.quantity === "14/20", `quantity: ${r.quantity}`);
  assert(r.verdict === "STALE", `verdict: ${r.verdict}`);
});

test("a missing field renders empty, never as invented text", () => {
  const r = readCalRow({ kind: "FLAT", label: "FLATS", verdict: "OK" }, 0);
  assert(r.summary === "", `an absent summary must stay absent, got: "${r.summary}"`);
  assert(r.quantity === "", `an absent quantity must stay absent, got: "${r.quantity}"`);
});

test("two rows the server could both emit get distinct keys", () => {
  // Row count is unbounded server-side (one per kind x distinct need), so the
  // list can carry near-duplicates. A shared React key drops one of them from
  // the DOM with no error anywhere.
  const a = readCalRow({ kind: "BIAS", summary: "g100" }, 0);
  const b = readCalRow({ kind: "BIAS", summary: "g100" }, 1);
  assert(a.key !== b.key, "duplicate row keys silently render one row");
});

// ────────────────────────────────────────────────────────────────── report
failures.forEach((f) => console.log(f));
const total = passed + failed;
console.log(`calibrationMatrix: ${passed}/${total} passed`);
export default { passed, failed, total };
