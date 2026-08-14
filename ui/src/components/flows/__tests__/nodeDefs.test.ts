// nodeDefs.test.ts — the anti-drift test for the Flows node vocabulary.
//
// WHY THIS FILE IS UNUSUAL. `nodeDefs.ts` is a SECOND transcription of a table
// the server already owns in `server/astrodeck/flows/nodes.py`, and no endpoint
// serves it (MILESTONE2-CONTRACT.md §G-4). Two hand-maintained copies of one
// contract drift, and every way they can drift is silent:
//   * a port id off by a letter draws a wire the compiler refuses, with no
//     error in the editor at all;
//   * a default whose TYPE drifts (`"1"` → `1`) changes what `flowsSetParam`
//     does to operator input three files away — capture's binning select would
//     start coercing the chosen value away;
//   * a `°` that becomes an ASCII `deg`, or a U+2212 that becomes a hyphen,
//     produces a param the server cannot match and a night that quietly does
//     less than the canvas draws.
// So this test does not restate the table a THIRD time. It PARSES nodes.py and
// compares. A test whose expectations are typed by the same hand that typed the
// data cannot catch a transcription error — it only proves the hand was
// consistent. Do not replace the parser with a literal fixture.
//
// Run alone:  npx tsx src/components/flows/__tests__/nodeDefs.test.ts
import { NODE_DEFS } from "../nodeDefs";
import type { NodeDef, PortDef } from "../nodeDefs";
import type { FlowNodeType } from "../flowsTypes";
// @ts-ignore  no @types/node guaranteed; tsx supplies fs at runtime
import { readFileSync } from "node:fs";

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
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`);
}

// ================================================================ nodes.py
// A deliberately small parser for the ONE Python construct we need. It is not a
// Python parser: it understands double-quoted strings, `#` line comments and
// bracket nesting, which is everything `NODE_DEFS` is made of.

const NODES_PY_REL = "../../../../../server/astrodeck/flows/nodes.py";
const nodesPySrc: string = (() => {
  try {
    return readFileSync(new URL(NODES_PY_REL, import.meta.url), "utf8") as string;
  } catch (e) {
    // A MISSING nodes.py must fail, never skip. A skipped cross-check reads as
    // a green run while the only thing guarding this file's accuracy is gone.
    throw new Error(
      `cannot read ${NODES_PY_REL} — the vocabulary cross-check cannot run, and `
      + `without it nodeDefs.ts is an unchecked hand copy of the server's table: `
      + (e as Error).message,
    );
  }
})();

/** Strip `#` comments, leaving double-quoted strings intact. */
function stripComments(src: string): string {
  let out = "";
  let inStr = false;
  for (let i = 0; i < src.length; i++) {
    const c = src[i];
    if (inStr) {
      out += c;
      if (c === "\\") { out += src[++i] ?? ""; continue; }
      if (c === '"') inStr = false;
      continue;
    }
    if (c === '"') { inStr = true; out += c; continue; }
    if (c === "#") { while (i < src.length && src[i] !== "\n") i++; out += "\n"; continue; }
    out += c;
  }
  return out;
}

const CLOSE: Record<string, string> = { "{": "}", "(": ")", "[": "]" };

/** Index of the bracket matching the one at `open`, string-aware. */
function matchBracket(src: string, open: number): number {
  const stack: string[] = [];
  let inStr = false;
  for (let i = open; i < src.length; i++) {
    const c = src[i];
    if (inStr) {
      if (c === "\\") { i++; continue; }
      if (c === '"') inStr = false;
      continue;
    }
    if (c === '"') { inStr = true; continue; }
    if (c === "{" || c === "(" || c === "[") stack.push(CLOSE[c]);
    else if (c === "}" || c === ")" || c === "]") {
      assert(stack.pop() === c, `unbalanced ${c} at ${i} in nodes.py`);
      if (stack.length === 0) return i;
    }
  }
  throw new Error(`no closing bracket for index ${open} in nodes.py`);
}

/** A Python dict literal of double-quoted keys and string/number values IS
 *  JSON, with one exception: Python allows a trailing comma. Removing it is the
 *  whole conversion — deliberately not a general Python-literal evaluator, so
 *  that any dict this file cannot honestly read throws instead of guessing. */
function pyDictToJson(src: string): unknown {
  return JSON.parse(src.replace(/,(\s*)}/g, "$1}"));
}

/** Index just past `name=`, only where `name` is a whole word. */
function kwargAt(args: string, name: string): number {
  const re = new RegExp(`(^|[^A-Za-z0-9_])${name}\\s*=`, "g");
  const m = re.exec(args);
  return m ? m.index + m[0].length : -1;
}

const py = stripComments(nodesPySrc);

interface PyNode {
  type: string; label: string; cat: string;
  ins: PortDef[]; outs: PortDef[];
  params: Record<string, string | number>;
  optionalIns: string[];
  /** Every kwarg name the entry passed, so a server that grows `fields=` is
   *  noticed rather than silently duplicated here forever. */
  kwargs: string[];
}

function parsePorts(tupleSrc: string): PortDef[] {
  const out: PortDef[] = [];
  const re = /_([fe])\(\s*"([^"]*)"\s*,\s*"([^"]*)"\s*\)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(tupleSrc)) !== null) {
    out.push({ id: m[2], label: m[3], kind: m[1] === "f" ? "flow" : "event" });
  }
  return out;
}

function parseNodesPy(): Record<string, PyNode> {
  const anchor = py.indexOf("NODE_DEFS: dict[str, NodeDef] = {");
  assert(anchor >= 0, "NODE_DEFS table not found in nodes.py — did it move or get renamed?");
  const open = py.indexOf("{", anchor);
  const body = py.slice(open + 1, matchBracket(py, open));

  const out: Record<string, PyNode> = {};
  const entry = /"([a-z]+)"\s*:\s*NodeDef\(/g;
  let m: RegExpExecArray | null;
  while ((m = entry.exec(body)) !== null) {
    const parenIdx = body.indexOf("(", m.index + m[1].length);
    const args = body.slice(parenIdx + 1, matchBracket(body, parenIdx));

    const strKw = (name: string): string => {
      const at = kwargAt(args, name);
      assert(at >= 0, `nodes.py "${m![1]}" has no ${name}=`);
      const q = /^\s*"([^"]*)"/.exec(args.slice(at));
      assert(q !== null, `nodes.py "${m![1]}" ${name}= is not a plain string`);
      return q![1];
    };
    const portsKw = (name: string): PortDef[] => {
      const at = kwargAt(args, name);
      if (at < 0) return [];
      const p = args.indexOf("(", at);
      return parsePorts(args.slice(p, matchBracket(args, p) + 1));
    };

    const pAt = kwargAt(args, "params");
    assert(pAt >= 0, `nodes.py "${m[1]}" has no params=`);
    const pOpen = args.indexOf("{", pAt);
    const paramsJson = args.slice(pOpen, matchBracket(args, pOpen) + 1);

    const optRaw = /optional_ins\s*=\s*frozenset\(\{([^}]*)\}\)/.exec(args);
    const optionalIns = optRaw
      ? Array.from(optRaw[1].matchAll(/"([^"]*)"/g), (q) => q[1])
      : [];

    out[m[1]] = {
      type: strKw("type"),
      label: strKw("label"),
      cat: strKw("cat"),
      ins: portsKw("ins"),
      outs: portsKw("outs"),
      // Python's dict literal here is byte-for-byte valid JSON — double-quoted
      // keys, string and numeric values only. Parsing it (rather than
      // regex-ing values out) is what preserves the string/number DISTINCTION,
      // which is the property capture.bin depends on.
      params: pyDictToJson(paramsJson) as Record<string, string | number>,
      optionalIns,
      kwargs: Array.from(args.matchAll(/(?:^|[^A-Za-z0-9_])([a-z_]+)\s*=/g), (k) => k[1]),
    };
  }
  return out;
}

const PY = parseNodesPy();

/** nodes.py's CATEGORY_TOKEN — the per-category colour the server believes in. */
function parseCategoryToken(): Record<string, string> {
  const at = py.indexOf("CATEGORY_TOKEN = {");
  assert(at >= 0, "CATEGORY_TOKEN not found in nodes.py");
  const open = py.indexOf("{", at);
  const body = py.slice(open, matchBracket(py, open) + 1);
  return pyDictToJson(body) as Record<string, string>;
}
const PY_CATEGORY_TOKEN = parseCategoryToken();

const TYPES = Object.keys(NODE_DEFS) as FlowNodeType[];
const defOf = (t: FlowNodeType): NodeDef => NODE_DEFS[t];

// The parser is itself a thing that can be wrong, and a parser that silently
// matched nothing would make every comparison below vacuously true.
test("parser sanity: nodes.py yielded 20 entries with ports and params", () => {
  eq(Object.keys(PY).length, 20, "entries parsed out of nodes.py");
  eq(PY.capture.outs.length, 2, "capture outs parsed");
  eq(PY.dusk.params.offset, -30, "a negative numeric default survived parsing");
  eq(Object.keys(PY_CATEGORY_TOKEN).length, 5, "CATEGORY_TOKEN entries");
});

// ------------------------------------------------------- the type set itself
test("exactly 20 node types, and the set matches nodes.py's NODE_DEFS keys", () => {
  eq(TYPES.length, 20, "NODE_DEFS entry count");
  const mine = [...TYPES].sort().join(",");
  const theirs = Object.keys(PY).sort().join(",");
  eq(mine, theirs,
    "the UI vocabulary and the server vocabulary name different node types; a "
    + "type only the UI knows draws a node no run can execute, and a type only "
    + "the server knows is a capability the operator can never reach");
});

test("each entry's `type` field equals its key", () => {
  for (const t of TYPES) {
    eq(defOf(t).type, t,
      `NODE_DEFS.${t}.type — lookups go both ways (palette items carry the type, `
      + `cards read the def back); a mismatch makes one direction lie`);
  }
});

// ------------------------------------------------------------ label and cat
test("label and cat match nodes.py for all 20", () => {
  for (const t of TYPES) {
    eq(defOf(t).label, PY[t].label, `${t}.label`);
    eq(defOf(t).cat, PY[t].cat, `${t}.cat`);
  }
});

// ------------------------------------------------------------------- ports
test("every port id, label, kind and ORDER matches nodes.py, in both directions", () => {
  for (const t of TYPES) {
    for (const dir of ["ins", "outs"] as const) {
      const mine = defOf(t)[dir];
      const theirs = PY[t][dir];
      eq(mine.length, theirs.length,
        `${t}.${dir} port count — an extra port here is a socket the compiler `
        + `cannot resolve; a missing one is a wire the operator cannot draw`);
      for (let i = 0; i < mine.length; i++) {
        eq(mine[i].id, theirs[i].id, `${t}.${dir}[${i}].id`);
        eq(mine[i].label, theirs[i].label, `${t}.${dir}[${i}].label`);
        eq(mine[i].kind, theirs[i].kind,
          `${t}.${dir}[${i}].kind — flow and event run through completely `
          + `different engine machinery, so a flipped kind wires "then" into `
          + `"whenever"`);
      }
    }
  }
});

test("dome's input keeps id `run` while showing the label `open`", () => {
  // The only node in the vocabulary where id and label differ. Renaming the id
  // to match the visible word would orphan every saved edge targeting dome|run.
  eq(defOf("dome").ins[0].id, "run", "dome.ins[0].id");
  eq(defOf("dome").ins[0].label, "open", "dome.ins[0].label");
  eq(PY.dome.ins[0].id, "run", "nodes.py agrees dome's input id is `run`");
});

test("port ids are unique within each direction", () => {
  for (const t of TYPES) {
    for (const dir of ["ins", "outs"] as const) {
      const ids = defOf(t)[dir].map((p) => p.id);
      eq(new Set(ids).size, ids.length,
        `${t}.${dir} has a duplicate port id — data-port keys collide and a drop `
        + `resolves to whichever row elementFromPoint hit first`);
    }
  }
});

test("optional inputs match nodes.py's optional_ins exactly (only calib.panel)", () => {
  for (const t of TYPES) {
    const mine = defOf(t).ins.filter((p) => p.optional === true).map((p) => p.id).sort();
    const theirs = [...PY[t].optionalIns].sort();
    eq(mine.join(","), theirs.join(","),
      `${t} optional inputs — an input wrongly marked optional silences doctor `
      + `rule 1 for a wire the run actually needs; wrongly required makes a `
      + `working calibration queue look broken`);
  }
  eq(defOf("calib").ins.find((p) => p.id === "panel")?.optional, true,
    "calib.panel is the one optional input in the vocabulary");
  // No output is ever optional — `optional` describes the doctor's unwired-input
  // rule, and an unwired OUTPUT is normal everywhere.
  for (const t of TYPES) {
    eq(defOf(t).outs.some((p) => p.optional === true), false, `${t} has an optional output`);
  }
});

// ------------------------------------------------------------------ params
test("default params match nodes.py exactly — keys, ORDER, values AND types", () => {
  for (const t of TYPES) {
    const mine = defOf(t).params;
    const theirs = PY[t].params;
    eq(Object.keys(mine).join(","), Object.keys(theirs).join(","),
      `${t} param keys/order — the inspector renders in this order and the `
      + `compiler reads these keys`);
    for (const k of Object.keys(theirs)) {
      eq(typeof mine[k], typeof theirs[k],
        `${t}.params.${k} TYPE — flowsSetParam coerces user input by the type of `
        + `the default, so a drifted type changes what the control does to input`);
      eq(mine[k], theirs[k], `${t}.params.${k} value`);
    }
  }
});

test("capture.bin is the STRING \"1\", not the number 1", () => {
  // Called out on its own because it is the one default whose type is easy to
  // "tidy" into a number. Binning is a select over "1"/"2"/"4"; with a numeric
  // default, flowsSetParam's `typeof base === "number"` branch parses the
  // operator's chosen option and writes back a number, so the select can no
  // longer find its own value and shows blank.
  const v = defOf("capture").params.bin;
  eq(typeof v, "string", "typeof capture.params.bin");
  eq(v, "1", "capture.params.bin");
  eq(typeof PY.capture.params.bin, "string", "nodes.py agrees capture bin is a string");
});

test("non-ASCII defaults are preserved codepoint-for-codepoint", () => {
  // These are the characters an editor or a well-meaning "fix" silently
  // replaces. The server matches these strings literally.
  eq(defOf("target").params.dec, "+41° 16′ 09″", "target dec keeps ′ (U+2032) and ″ (U+2033)");
  eq(defOf("target").params.name, "M31 — Andromeda", "target name keeps the em dash");
  eq(defOf("duskflats").params.window, "Sun −2° … −8°",
    "dusk-flats window keeps U+2212 MINUS and U+2026 ELLIPSIS, not '-' and '...'");
  eq(defOf("pool").params.strategy, "Best available (alt × moon)",
    "pool strategy keeps U+00D7 MULTIPLICATION SIGN");
});

// ------------------------------------------------------------------ colours
const COLOR_TOKENS = ["--accent", "--accent-dim", "--sky", "--warn", "--bad", "--good"];

test("the abort node carries the danger token --bad, not the warn one", () => {
  // The ACTION category's single exception (README §"Design tokens": "actions
  // --warn, abort --bad"). The server has no way to express it — CATEGORY_TOKEN
  // maps ACTION flat — so this is exactly the value a category lookup would
  // quietly re-derive as amber, and ABORT + PARK would look like a message.
  eq(defOf("abort").colorVar, "--bad", "abort.colorVar");
  assert(defOf("abort").colorVar !== "--warn", "abort must not inherit the ACTION warn token");
  eq(PY_CATEGORY_TOKEN.ACTION, "--warn",
    "nodes.py still maps ACTION to --warn, which is why the exception lives in the UI");
});

test("every other node's colour is its category's token from nodes.py", () => {
  for (const t of TYPES) {
    if (t === "abort") continue;
    eq(defOf(t).colorVar, PY_CATEGORY_TOKEN[defOf(t).cat],
      `${t}.colorVar must equal CATEGORY_TOKEN[${defOf(t).cat}]`);
  }
});

test("colorVar is a token name, never a hex", () => {
  for (const t of TYPES) {
    const c = defOf(t).colorVar;
    assert(c.startsWith("--"), `${t}.colorVar "${c}" is not a CSS custom property name`);
    assert(COLOR_TOKENS.includes(c),
      `${t}.colorVar "${c}" is outside the six category tokens — a new colour `
      + `bypasses night mode, which works by swapping the tokens`);
  }
});

// ------------------------------------------------------------------- fields
test("fields cover the params exactly — same keys, same order", () => {
  // The inspector renders one row per field, so an uncovered param is a value
  // the operator can never change and a field with no param is a control whose
  // edits land nowhere (flowsSetParam keys its coercion off the default).
  for (const t of TYPES) {
    const fieldKeys = defOf(t).fields.map((f) => f.key);
    const paramKeys = Object.keys(defOf(t).params);
    eq(fieldKeys.join(","), paramKeys.join(","),
      `${t}: fields vs params`);
  }
});

test("select fields carry options; text fields carry none", () => {
  for (const t of TYPES) {
    for (const f of defOf(t).fields) {
      if (f.control === "select") {
        assert(!!f.options && f.options.length > 0,
          `${t}.${f.key} is a select with no options — an empty <select> cannot `
          + `even show the current value`);
      } else {
        eq(f.control, "text", `${t}.${f.key} control`);
        eq(f.options, undefined, `${t}.${f.key} is text but carries options`);
      }
    }
  }
});

test("every select's DEFAULT is one of its own options", () => {
  // The sharpest drift detector in this file: it compares the option strings
  // (which exist ONLY here — §G-4) against the defaults (which the server also
  // holds). A single wrong codepoint in "Sun −2° … −8°" or "Best available
  // (alt × moon)" breaks this and nothing else would.
  for (const t of TYPES) {
    for (const f of defOf(t).fields) {
      if (f.control !== "select") continue;
      const dflt = defOf(t).params[f.key];
      assert(f.options!.includes(String(dflt)),
        `${t}.${f.key}: default ${JSON.stringify(dflt)} is not among its options `
        + `${JSON.stringify(f.options)} — the control opens showing nothing selected`);
    }
  }
});

test("field labels and units are non-empty when present", () => {
  for (const t of TYPES) {
    for (const f of defOf(t).fields) {
      assert(f.label.trim().length > 0, `${t}.${f.key} has an empty label`);
      if ("unit" in f) assert((f.unit ?? "").length > 0, `${t}.${f.key} has an empty unit`);
    }
  }
});

test("the units in use are the ten the contract lists", () => {
  const units = new Set<string>();
  for (const t of TYPES) for (const f of defOf(t).fields) if (f.unit) units.add(f.unit);
  const expected = ["% cover", "frames", "frames each", "h", "h (0 = none)", "min", "s", "°", "′", "″"];
  eq([...units].sort().join(" | "), expected.sort().join(" | "), "distinct unit suffixes");
});

test("capture's integration-goal unit keeps its zero sentinel", () => {
  const f = defOf("capture").fields.find((x) => x.key === "goal");
  eq(f?.unit, "h (0 = none)",
    "without the sentinel, 0 reads as 'shoot nothing' instead of 'no goal'");
});

// -------------------------------------------------------------------- desc
test("every node has a one-line description", () => {
  for (const t of TYPES) {
    const d = defOf(t).desc;
    assert(d.trim().length > 20, `${t}.desc is too short to be the rail's tooltip`);
    assert(!d.includes("\n"), `${t}.desc contains a newline; it is a title= attribute`);
  }
});

// --------------------------------------------------------------------- sum
test("sum() over the defaults renders the prototype's footer lines verbatim", () => {
  const want: Record<FlowNodeType, string> = {
    dusk: "Astro dusk -30m → dawn",
    target: "M31 — Andromeda",
    safety: "Cloud + rain sensor · fail closed",
    cloudwatch: "in >40% · clear 4m",
    dome: "slave to mount · fail closed",
    flatpanel: "dust-cover panel · 28500 ADU",
    slew: "±0.5′ · ASTAP",
    autofocus: "V-curve sweep · 9 pts",
    guide: "PHD2 · settle 1.5″",
    capture: "L · 120s · g100 · ×24",
    duskflats: "translucent lens cap · Sun −2° … −8° · ×15",
    calib: "darks → bias → flats · ×20 each, then wait",
    pool: "4 candidates · best available",
    // Not from the prototype — FILTER CYCLE post-dates it. Held to the
    // same shape as its neighbours so the footer column stays uniform.
    cycle: "45 passes · as drawn",
    condition: "hfr above 3.2 · 3 frames",
    holdresume: "resume: re-center · refocus if hfr drifted",
    notify: "ntfy · rig-alerts",
    refocus: "autofocus, then resume",
    abort: "park yes · warm yes",
    report: "captures/sessions/",
  };
  for (const t of TYPES) {
    eq(defOf(t).sum({ ...defOf(t).params }), want[t], `${t}.sum(defaults)`);
  }
});

test("sum() tracks changed params", () => {
  eq(defOf("capture").sum({ ...defOf("capture").params, filter: "Ha", exposure: 180, count: 20 }),
    "Ha · 180s · g100 · ×20", "capture footer follows the edited params");
  eq(defOf("dusk").sum({ ...defOf("dusk").params, offset: 15 }),
    "Astro dusk +15m → dawn", "a non-negative offset gains the + sign");
  eq(defOf("pool").sum({ ...defOf("pool").params, members: "M8", strategy: "Round robin" }),
    "1 candidates · round robin", "pool counts comma-separated members");
});

test("KNOWN PROTOTYPE BUG, reproduced on purpose: holdresume ignores p.recenter", () => {
  // The prototype hardcodes "resume: re-center" and never reads `recenter`, so
  // a node set to "Trust tracking" still claims it will plate-solve. The
  // handoff (§C.0) names this and rules that the prototype is authoritative, so
  // it is reproduced rather than fixed. This assertion exists so the bug cannot
  // be quietly "fixed" without someone reading this comment and getting a
  // ruling — and so it stays visible in the test output.
  const p = { ...defOf("holdresume").params, recenter: "Trust tracking" };
  eq(defOf("holdresume").sum(p), "resume: re-center · refocus if hfr drifted",
    "the footer still says re-center — reproduce, do not fix");
});

test("sum() survives a param the saved graph does not carry", () => {
  // models.py validates params permissively on purpose so a vocabulary that
  // gains a field does not make every saved flow unopenable. Such a graph
  // reaches sum() with the key missing; the card must not print "undefined".
  for (const t of TYPES) {
    const s = defOf(t).sum({});
    assert(!s.includes("undefined"),
      `${t}.sum({}) printed "undefined" into the node footer: ${JSON.stringify(s)}`);
  }
});

// ---------------------------------------------------------- the G-4 tripwire
test("nodes.py still carries no fields/desc/sum — this file remains their only home", () => {
  // §G-4 is open: the server MAY grow the field vocabulary and serve it, at
  // which point this duplication should be deleted rather than maintained. This
  // assertion is how we find out, instead of keeping two copies forever.
  for (const t of TYPES) {
    for (const banned of ["fields", "desc", "sum", "options", "units"]) {
      assert(!PY[t].kwargs.includes(banned),
        `nodes.py's "${t}" now passes ${banned}= — the server has become the `
        + `source for presentation metadata (§G-4). Read it from there and `
        + `delete the copy in nodeDefs.ts rather than maintaining both.`);
    }
  }
  const cls = py.slice(py.indexOf("class NodeDef:"));
  const bodyEnd = cls.indexOf("\n    def ");
  const declared = Array.from(cls.slice(0, bodyEnd).matchAll(/^ {4}([a-z_]+)\s*:/gm), (m) => m[1]);
  eq(declared.join(","), "type,label,cat,ins,outs,params,optional_ins",
    "the server's NodeDef dataclass fields");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`nodeDefs.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
