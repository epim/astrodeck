// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
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
import {
  COUNT_MODES, LEGACY_TYPES, NODE_DEFS, ROWLESS_PARAMS, TARGET_ANGLES,
  createParams, fieldValue, targetAngle,
} from "../nodeDefs";
import type { FieldDef, NodeDef, PortDef } from "../nodeDefs";
import type { FlowNodeType } from "../flowsTypes";
import { DEFAULT_OVERLAP } from "../../../lib/framing";
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
 *  JSON, with two exceptions. Python allows a trailing comma, which is removed.
 *  And a value may be `Derived(<function>)` (#461): a missing-key default
 *  nodes.py reads from another module when its table is read, TARGET's
 *  `overlap` being framing's DEFAULT_OVERLAP in percent. It reads as the
 *  marker `{"derived": "<function>"}`, which `resolveDerived` turns into the
 *  value nodeDefs.ts must hold. That is the whole conversion — deliberately
 *  not a general Python-literal evaluator, so that any dict this file cannot
 *  honestly read throws instead of guessing. */
function pyDictToJson(src: string): unknown {
  return JSON.parse(src.replace(/,(\s*)}/g, "$1}")
    .replace(/\bDerived\((\w+)\)/g, '{"derived": "$1"}'));
}

/** What each `Derived` function of nodes.py is, read on the UI's side: the
 *  same product from lib/framing's mirror of the server constant, which
 *  server/tests/test_overlap_constant_one.py holds equal to the server's.
 *  A function this map does not name throws, so a new derived default cannot
 *  pass the parity test unread. */
const DERIVED_AS: Record<string, number> = {
  // nodes.py `target_overlap_pct`: framing.DEFAULT_OVERLAP x 100.
  target_overlap_pct: DEFAULT_OVERLAP * 100,
};

/** A parsed params dict with each `Derived` marker read as `DERIVED_AS`
 *  says, and the keys that were derived, by the function that derives them. */
function resolveDerived(
  type: string, parsed: Record<string, string | number | { derived: string }>,
): { params: Record<string, string | number>; derived: Record<string, string> } {
  const params: Record<string, string | number> = {};
  const derived: Record<string, string> = {};
  for (const [k, v] of Object.entries(parsed)) {
    if (typeof v !== "object") { params[k] = v; continue; }
    assert(v.derived in DERIVED_AS,
      `nodes.py "${type}" derives ${k} with ${v.derived}, which DERIVED_AS does not name`);
    params[k] = DERIVED_AS[v.derived];
    derived[k] = v.derived;
  }
  return { params, derived };
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
  /** `params=`, each `Derived` default read as the UI must hold it. */
  params: Record<string, string | number>;
  /** The keys of `params` nodes.py derives, by the function that derives
   *  them (`Derived`, #461), `{}` when it derives none. */
  derived: Record<string, string>;
  /** `created_as=` — the Created-as column, `{}` when the entry has none. */
  createdAs: Record<string, string | number>;
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

    // `created_as=` is optional on the server's NodeDef (default `{}`), and
    // parsed the same way as `params=`, preserving the string/number split.
    const cAt = kwargAt(args, "created_as");
    let createdAs: Record<string, string | number> = {};
    if (cAt >= 0) {
      const cOpen = args.indexOf("{", cAt);
      createdAs = pyDictToJson(args.slice(cOpen, matchBracket(args, cOpen) + 1)) as
        Record<string, string | number>;
    }

    const optRaw = /optional_ins\s*=\s*frozenset\(\{([^}]*)\}\)/.exec(args);
    const optionalIns = optRaw
      ? Array.from(optRaw[1].matchAll(/"([^"]*)"/g), (q) => q[1])
      : [];

    // Python's dict literal here is byte-for-byte valid JSON — double-quoted
    // keys, string and numeric values only, bar a `Derived` default. Parsing
    // it (rather than regex-ing values out) is what preserves the
    // string/number DISTINCTION, which is the property capture.bin depends on.
    const { params, derived } = resolveDerived(m[1], pyDictToJson(paramsJson) as
      Record<string, string | number | { derived: string }>);

    out[m[1]] = {
      type: strKw("type"),
      label: strKw("label"),
      cat: strKw("cat"),
      ins: portsKw("ins"),
      outs: portsKw("outs"),
      params,
      derived,
      createdAs,
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

/** The double-quoted strings inside the bracketed literal that follows `NAME`
 *  and its `=` in nodes.py: `frozenset({...})` or a `(...)` tuple. Throws when
 *  the name is missing, so a rename cannot turn a comparison into [] vs []. */
function pyStringsAfter(name: string): string[] {
  const m = new RegExp(`^${name}\\b[^=\\n]*=\\s*`, "m").exec(py);
  assert(m !== null, `${name} not found in nodes.py`);
  const from = m!.index + m![0].length;
  const open = py.slice(from).search(/[({[]/) + from;
  const body = py.slice(open, matchBracket(py, open) + 1);
  const out = Array.from(body.matchAll(/"([^"]*)"/g), (q) => q[1]);
  assert(out.length > 0, `${name} parsed to no strings`);
  return out;
}
const PY_LEGACY = pyStringsAfter("LEGACY_TYPES");
const PY_TARGET_ANGLES = pyStringsAfter("TARGET_ANGLES");
const PY_COUNT_MODES = pyStringsAfter("COUNT_MODES");

const TYPES = Object.keys(NODE_DEFS) as FlowNodeType[];
const defOf = (t: FlowNodeType): NodeDef => NODE_DEFS[t];

// The parser is itself a thing that can be wrong, and a parser that silently
// matched nothing would make every comparison below vacuously true.
test("parser sanity: nodes.py yielded 21 entries with ports and params", () => {
  eq(Object.keys(PY).length, 21, "entries parsed out of nodes.py");
  // DELIBERATE PIN CHANGE (mosaic S3, spec 1.3): capture gained `pass`.
  eq(PY.capture.outs.length, 3, "capture outs parsed");
  eq(PY.dusk.params.offset, -30, "a negative numeric default survived parsing");
  eq(Object.keys(PY_CATEGORY_TOKEN).length, 5, "CATEGORY_TOKEN entries");
  // The Created-as parse must have found something, or the createdAs parity
  // below compares {} with {} for every type and proves nothing.
  eq(PY.target.createdAs.counts, "Accepted subs", "target created_as parsed");
  eq(PY.target.createdAs.rotation, -1, "a negative number survived the created_as parse");
  eq(Object.keys(PY.dusk.createdAs).length, 0, "an entry with no created_as parses to {}");
  // DELIBERATE PIN CHANGE (S7, #461): nodes.py's TARGET no longer writes
  // `"overlap": 25` but `"overlap": Derived(target_overlap_pct)`, framing's
  // DEFAULT_OVERLAP in percent, read when the table is read. The parser before
  // this change could not read it, and the whole file died at load, observed:
  //   SyntaxError: Unexpected token 'D', ..."overlap": Derived(ta"... is not valid JSON
  // It now reads a `Derived` default as the value the UI must hold, and this
  // checks one was read, so the parity below is not vacuously about numbers.
  // Only TARGET's overlap is derived: any other key the server starts
  // deriving, or stops, changes this line on purpose.
  // Mutant "literal 25" in nodes.py (the TARGET's overlap written 25 again),
  // observed:
  //   x parser sanity: nodes.py yielded 21 entries with ports and params: the
  //   defaults nodes.py derives expected "target.overlap=target_overlap_pct", got ""
  const derived = TYPES.flatMap((t) => Object.entries(PY[t].derived).map(([k, f]) => `${t}.${k}=${f}`));
  eq(derived.join(","), "target.overlap=target_overlap_pct", "the defaults nodes.py derives");
});

// ------------------------------------------------------- the type set itself
test("exactly 21 node types, and the set matches nodes.py's NODE_DEFS keys", () => {
  eq(TYPES.length, 21, "NODE_DEFS entry count");
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
test("label and cat match nodes.py for all 21", () => {
  for (const t of TYPES) {
    eq(defOf(t).label, PY[t].label, `${t}.label`);
    eq(defOf(t).cat, PY[t].cat, `${t}.cat`);
  }
});

// ------------------------------------------------------------------- ports
// The mosaic slice's ports (TARGET `next` "next panel", `target` relabelled
// "each panel"; CAPTURE LOOP and FILTER CYCLE `pass` "pass done", `complete`
// relabelled "all done") are held by this test like every other port.
// Mutant 'nodeDefs.ts pass port missing' (FILTER CYCLE's `_e("pass", "pass
// done")` removed from nodeDefs.ts), observed:
//   x every port id, label, kind and ORDER matches nodes.py, in both
//   directions: cycle.outs port count — an extra port here is a socket the
//   compiler cannot resolve; a missing one is a wire the operator cannot draw
//   expected 3, got 2
// AUTOFOCUS's and GUIDE's `pass` "pass done" (S4, #331), listed after
// `focused` and `guiding`, are held the same way, from either side. Mutant
// 'AUTOFOCUS has no pass' in nodeDefs.ts alone, observed:
//   x every port id, label, kind and ORDER matches nodes.py, in both
//   directions: autofocus.outs port count — an extra port here is a socket
//   the compiler cannot resolve; a missing one is a wire the operator cannot
//   draw expected 2, got 1
// and in nodes.py alone the same line ending "expected 1, got 2". Mutant
// 'pass first' (nodeDefs.ts: AUTOFOCUS's outs as `pass`, `focused`, which
// would move every saved `focused` wire's socket), observed:
//   x every port id, label, kind and ORDER matches nodes.py, in both
//   directions: autofocus.outs[0].id expected "focused", got "pass"
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

test("optional inputs match nodes.py's optional_ins exactly", () => {
  for (const t of TYPES) {
    const mine = defOf(t).ins.filter((p) => p.optional === true).map((p) => p.id).sort();
    const theirs = [...PY[t].optionalIns].sort();
    eq(mine.join(","), theirs.join(","),
      `${t} optional inputs — an input wrongly marked optional silences doctor `
      + `rule 1 for a wire the run actually needs; wrongly required makes a `
      + `working calibration queue look broken`);
  }
  // Spot-check the original one by name as well. It was "the one optional
  // input in the vocabulary" until the doctor stopped demanding four wires the
  // engine does not consult; the loop above is what keeps that list honest now.
  eq(defOf("calib").ins.find((p) => p.id === "panel")?.optional, true,
    "calib.panel is optional — a flat panel is not required to fill a library");
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

test("the Created-as column matches nodes.py exactly — keys, ORDER, values AND types", () => {
  // Spec 3.1 / 3.7: `createParams` is mirrored and the parity test covers it.
  // A drifted Created-as value is the worst kind of drift: it changes what
  // every NEW block means (a palette drop counting rejects again, or carrying
  // an angle nobody chose) while every saved flow still reads correctly.
  // Mutant 'nodeDefs.ts createdAs drift' (TARGET's createdAs counts set to
  // "Every sub taken"), observed:
  //   x the Created-as column matches nodes.py exactly — keys, ORDER, values
  //   AND types: target.createdAs.counts value expected "Accepted subs", got
  //   "Every sub taken"
  for (const t of TYPES) {
    const mine = defOf(t).createdAs ?? {};
    const theirs = PY[t].createdAs;
    eq(Object.keys(mine).join(","), Object.keys(theirs).join(","), `${t} createdAs keys/order`);
    for (const k of Object.keys(theirs)) {
      eq(typeof mine[k], typeof theirs[k], `${t}.createdAs.${k} TYPE`);
      eq(mine[k], theirs[k], `${t}.createdAs.${k} value`);
    }
  }
});

test("createParams(type) is the defaults overlaid with the Created-as column", () => {
  // Computed from the PARSED server table, not from nodeDefs.ts, so this is a
  // cross-check of the overlay and not the same hand agreeing with itself.
  for (const t of TYPES) {
    const want: Record<string, string | number> = { ...PY[t].params, ...PY[t].createdAs };
    const got = createParams(t);
    eq(Object.keys(got).join(","), Object.keys(want).join(","), `${t} createParams keys`);
    for (const k of Object.keys(want)) eq(got[k], want[k], `${t} createParams.${k}`);
  }
  eq(createParams("target").counts, "Accepted subs", "a new TARGET counts accepted subs (ruling 2)");
  eq(createParams("target").angle, "Any angle", "a new TARGET carries no angle nobody chose (ruling 9)");
  eq(createParams("target").name, "", "a new TARGET has no inherited name (#190)");
  eq(createParams("pool").counts, "Accepted subs", "a new POOL counts accepted subs too");
  eq(defOf("target").params.counts, "Every sub taken",
    "the missing-key default keeps the old meaning: a saved TARGET still counts every sub");
  assert(!("angle" in defOf("target").params),
    "angle has NO missing-key default — it is derived from rotation");
  const a = createParams("target");
  a.name = "edited";
  eq(createParams("target").name, "", "createParams hands out a fresh object");
});

// Mutant 'nodeDefs.ts legacy flag dropped' (`legacy: true` removed from
// slew), observed:
//   x the legacy flag, LEGACY_TYPES and nodes.py's LEGACY_TYPES agree: defs
//   flagged legacy vs nodes.py expected "slew", got ""
test("the legacy flag, LEGACY_TYPES and nodes.py's LEGACY_TYPES agree", () => {
  const flagged = TYPES.filter((t) => defOf(t).legacy === true).sort();
  eq(flagged.join(","), [...PY_LEGACY].sort().join(","), "defs flagged legacy vs nodes.py");
  eq([...LEGACY_TYPES].sort().join(","), [...PY_LEGACY].sort().join(","), "LEGACY_TYPES vs nodes.py");
  eq(PY_LEGACY.join(","), "slew", "SLEW + CENTER is the one legacy type (spec 1.7)");
});

test("the angle options and the counts values are nodes.py's vocabularies", () => {
  const angle = defOf("target").fields.find((f) => f.key === "angle");
  eq((angle?.options ?? []).join("|"), PY_TARGET_ANGLES.join("|"), "angle options vs TARGET_ANGLES");
  eq([...TARGET_ANGLES].join("|"), PY_TARGET_ANGLES.join("|"), "TARGET_ANGLES mirror");
  eq([...COUNT_MODES].join("|"), PY_COUNT_MODES.join("|"), "COUNT_MODES mirror");
  for (const t of ["target", "pool"] as const) {
    assert(PY_COUNT_MODES.includes(String(defOf(t).params.counts)), `${t} counts default`);
    assert(PY_COUNT_MODES.includes(String(createParams(t).counts)), `${t} counts created`);
  }
});

test("the derived angle mirrors nodes.py target_angle", () => {
  // The same table as the server's TestTheDerivedAngle. 0 is a real PA
  // (north up, #150); unparseable or empty is "no constraint", never PA 0.
  // Mutant 'targetAngle reads 0 as any angle' (`< 0` changed to `<= 0`),
  // observed:
  //   x the derived angle mirrors nodes.py target_angle:
  //   targetAngle({"rotation":0}) expected "Rotate to PA", got "Any angle"
  // Mutant 'targetAngle reads an empty rotation as PA 0' (the DECIMAL guard
  // replaced by a bare `Number(...)`), observed:
  //   x the derived angle mirrors nodes.py target_angle:
  //   targetAngle({"rotation":""}) expected "Any angle", got "Rotate to PA"
  const cases: [Record<string, string | number>, string][] = [
    [{ rotation: -1 }, "Any angle"],
    [{ rotation: -0.5 }, "Any angle"],
    [{ rotation: 0 }, "Rotate to PA"],
    [{ rotation: 23.4 }, "Rotate to PA"],
    [{ rotation: "23.4" }, "Rotate to PA"],
    [{ rotation: "" }, "Any angle"],
    [{ rotation: "abc" }, "Any angle"],
    [{}, "Any angle"],
    [{ rotation: 30, angle: "Camera fixed at PA" }, "Camera fixed at PA"],
    [{ rotation: 30, angle: "" }, "Rotate to PA"],
  ];
  for (const [p, want] of cases) eq(targetAngle(p), want, `targetAngle(${JSON.stringify(p)})`);
});

test("fieldValue shows the derived angle for a node with no angle key, and writes nothing", () => {
  // A TARGET saved before `angle` existed: rotation 23.4 and no angle key.
  // The row must read "Rotate to PA" (what the run will do), not a blank
  // select that `selectOptions` would pad with "" and write in on the next
  // edit.
  //
  // THIS REACHES `fieldValue`, NOT THE INSPECTORS. Both hand their rows
  // `fieldValue(f, node.params)` since the integration of mosaic S3 (they
  // passed `node.params[f.key]` until then); what each SHOWS is held by
  // flowInspectorAngle.test.tsx (FlowInspector.tsx) and inspectorDom.test.tsx
  // section 10 (the next UI's FlowNodeEditor.tsx).
  //
  // Mutant 'derive dropped from the angle field' (`derive: targetAngle`
  // removed from the field), observed:
  //   x fieldValue shows the derived angle for a node with no angle key, and
  //   writes nothing: derived for a stored PA expected "Rotate to PA", got
  //   undefined
  const angle = defOf("target").fields.find((f) => f.key === "angle")!;
  const stored: Record<string, string | number> = { name: "M31", rotation: 23.4 };
  eq(fieldValue(angle, stored), "Rotate to PA", "derived for a stored PA");
  eq(fieldValue(angle, { rotation: -1 }), "Any angle", "derived for no angle");
  eq(fieldValue(angle, { rotation: 23.4, angle: "Camera fixed at PA" }), "Camera fixed at PA",
    "the node's own choice wins");
  assert(!("angle" in stored), "reading the row wrote an angle into the node");
  // Control: a field with no derivation shows exactly what the node holds.
  const name = defOf("target").fields.find((f) => f.key === "name")!;
  eq(fieldValue(name, { name: "" }), "", "an empty name stays empty");
  eq(fieldValue(name, {}), undefined, "a missing name stays missing");
  // fieldValue's OWN precedence, through a derivation that ignores the node.
  // `targetAngle` already returns a stored angle, so the angle field alone
  // cannot tell "the node's value wins" from "the derivation always runs".
  // Mutant 'fieldValue derives over a stored value' (the guard reduced to
  // `if (field.derive)`) passed every case above; with these, observed:
  //   x fieldValue shows the derived angle for a node with no angle key, and
  //   writes nothing: a node's own value beats the derivation expected "own",
  //   got "derived"
  const synthetic: FieldDef = { key: "k", label: "K", control: "text", derive: () => "derived" };
  eq(fieldValue(synthetic, { k: "own" }), "own", "a node's own value beats the derivation");
  eq(fieldValue(synthetic, { k: 0 }), 0, "a stored 0 is a value, not an absence");
  eq(fieldValue(synthetic, { k: "" }), "derived", "an empty value shows the derivation");
  eq(fieldValue(synthetic, {}), "derived", "a missing value shows the derivation");
  const onlyDerived = TYPES.flatMap((t) =>
    defOf(t).fields.filter((f) => f.derive).map((f) => `${t}.${f.key}`));
  eq(onlyDerived.join(","), "target.angle", "fields with a derivation");
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
  // THE ONE THIS TEST WAS WATCHING FOR, changed on purpose. It used to pin an
  // em dash here precisely because an editor would swap it for a hyphen behind
  // your back. The 2026-08-14 do-not list then banned em dashes from every
  // shipped string, so the export itself writes "M31 - Andromeda" and nodes.py
  // follows. The assertion did its job either way: this could not change
  // silently, and the hyphen is now pinned as hard as the em dash was.
  eq(defOf("target").params.name, "M31 - Andromeda",
    "target name uses a HYPHEN - the do-not list bans em dashes in shipped strings");
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
test("fields cover the created params exactly, less the declared rowless ones", () => {
  // The inspector renders one row per field, so an uncovered param is a value
  // the operator can never change and a field with no param is a control whose
  // edits land nowhere (flowsSetParam keys its coercion off the default).
  //
  // DELIBERATE PIN CHANGE (mosaic S3, spec 3.1): this was "fields = params,
  // same order". Two things broke it honestly. TARGET's `angle` is created but
  // has no missing-key default (it is derived), so rows are compared against
  // `createParams`; and `counts` / `frameAnchor` deliberately have no row,
  // which ROWLESS_PARAMS must say by name. The ORDER rule is kept for every
  // key the params table holds; a derived key may sit where it reads best.
  for (const t of TYPES) {
    const rowless = ROWLESS_PARAMS[t] ?? [];
    const fieldKeys = defOf(t).fields.map((f) => f.key);
    const want = Object.keys(createParams(t)).filter((k) => !rowless.includes(k));
    eq([...fieldKeys].sort().join(","), [...want].sort().join(","), `${t}: fields vs created params`);
    const paramOrder = Object.keys(defOf(t).params).filter((k) => fieldKeys.includes(k));
    eq(fieldKeys.filter((k) => k in defOf(t).params).join(","), paramOrder.join(","),
      `${t}: field order vs params order`);
    for (const k of rowless) {
      assert(k in createParams(t), `${t} declares rowless ${k}, which it does not have`);
    }
  }
  eq(Object.keys(ROWLESS_PARAMS).sort().join(","), "pool,target", "types with a rowless param");
});

test("every control is one of the three, and only selects carry options", () => {
  // `cycleplan` is the third, added by the 2026-08-14 export. It carries no
  // `options` for the same reason `text` does not: its choices are the rig's
  // filter wheel, read at render time, not a list frozen into this file.
  for (const t of TYPES) {
    for (const f of defOf(t).fields) {
      if (f.control === "select") {
        assert(!!f.options && f.options.length > 0,
          `${t}.${f.key} is a select with no options - an empty <select> cannot `
          + `even show the current value`);
      } else {
        assert(f.control === "text" || f.control === "cycleplan",
          `${t}.${f.key} control is ${JSON.stringify(f.control)}, which is not one `
          + `of select / text / cycleplan - FlowFieldRow renders nothing for it`);
        eq(f.options, undefined,
          `${t}.${f.key} is ${f.control} but carries options`);
      }
    }
  }
});

test("exactly one field in the whole vocabulary is a cycleplan", () => {
  // Pinned because the control is bespoke: it reads the rig's filter wheel and
  // writes a comma-separated slot string. A second one appearing means somebody
  // reused it for a field that is not a slot table, and it would write garbage
  // into that param.
  const found = TYPES.flatMap((t) =>
    defOf(t).fields.filter((f) => f.control === "cycleplan").map((f) => `${t}.${f.key}`));
  eq(found.join(","), "cycle.plan", "cycleplan fields");
});

test("every select's DEFAULT is one of its own options", () => {
  // The sharpest drift detector in this file: it compares the option strings
  // (which exist ONLY here — §G-4) against the defaults (which the server also
  // holds). A single wrong codepoint in "Sun −2° … −8°" or "Best available
  // (alt × moon)" breaks this and nothing else would.
  //
  // Both the missing-key default (when the key has one) and the created value
  // are checked: a new block and a loaded one must each open on an option.
  for (const t of TYPES) {
    for (const f of defOf(t).fields) {
      if (f.control !== "select") continue;
      const values = [createParams(t)[f.key]];
      if (f.key in defOf(t).params) values.push(defOf(t).params[f.key]);
      else values.push(fieldValue(f, { ...defOf(t).params }) as string);
      for (const dflt of values) {
        assert(f.options!.includes(String(dflt)),
          `${t}.${f.key}: default ${JSON.stringify(dflt)} is not among its options `
          + `${JSON.stringify(f.options)} — the control opens showing nothing selected`);
      }
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

test("the units in use are the fourteen on record", () => {
  // Ten from the contract, plus `passes` and `cycles` from the 2026-08-14
  // export (FILTER CYCLE's total, TARGET POOL's per-target quota). Pinned as a
  // set so another is a deliberate act: units are the one place this surface
  // prints an unlocalised word next to a number.
  //
  // DELIBERATE PIN CHANGE (mosaic S3, spec 3.1): TARGET's grid added `%` (the
  // overlap) and `° (0 = not framed)` (the camera field, whose zero sentinel
  // must be legible at the control, as capture's goal's is).
  const units = new Set<string>();
  for (const t of TYPES) for (const f of defOf(t).fields) if (f.unit) units.add(f.unit);
  const expected = ["%", "% cover", "cycles", "frames", "frames each", "h", "h (0 = none)", "min", "passes", "s", "°", "° (0 = not framed)", "′", "″"];
  eq([...units].sort().join(" | "), expected.sort().join(" | "), "distinct unit suffixes");
});

test("capture's integration-goal unit keeps its zero sentinel", () => {
  const f = defOf("capture").fields.find((x) => x.key === "goal");
  eq(f?.unit, "h (0 = none)",
    "without the sentinel, 0 reads as 'shoot nothing' instead of 'no goal'");
});

// -------------------------------------------------------------------- help

test("exactly one field in the whole vocabulary carries help text, DUSK WINDOW's repeat", () => {
  // Pinned the same way as the cycleplan control above (#195): `help` exists
  // for exactly the field whose plain label used to promise something the
  // compile did not keep ("Single night" sounded like a no-op, and silently
  // meant "never auto-resume"). A second field growing one unnoticed is not
  // a defect by itself, but this count moving is the signal that a field's
  // own help needs the same compiled-behaviour check the next test runs on
  // this one.
  const found = TYPES.flatMap((t) =>
    defOf(t).fields.filter((f) => "help" in f && (f.help ?? "").length > 0)
      .map((f) => `${t}.${f.key}`));
  eq(found.join(","), "dusk.repeat", "fields carrying help text");
});

test("DUSK WINDOW's repeat help says what the compiled field does: Single "
  + "night means auto-resume does not arm across nights", () => {
  // server/astrodeck/flows/compile.py: `resume_across_nights = repeat !=
  // "Single night"`, and `ResumeArm.tick` reads that field to decide
  // whether a session dormant at dawn re-arms itself for the next dusk
  // (#195). The help text is this UI's only account of that compiled
  // behaviour — a copy that drifted from it would again promise a no-op
  // where the run instead goes dormant and stays there.
  //
  // NAMED MUTANT: in nodeDefs.ts, change dusk.repeat's `help` string to
  // describe only what the other two choices do, dropping the "Single
  // night: ..." sentence entirely (the shape a copy edit that trims
  // instead of updates would take). RED, observed:
  //     help does not name the option it explains first
  const help = defOf("dusk").fields.find((f) => f.key === "repeat")?.help ?? "";
  assert(help.length > 0, "dusk.repeat has no help text");
  assert(help.startsWith("Single night:"),
    "help does not name the option it explains first");
  assert(help.includes("does not start itself again"),
    "dusk.repeat's help never says Single night does not arm auto-resume");
  assert(help.includes("CONTINUE it by hand"),
    "help drops the operator's own recovery action for Single night");
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
    target: "M31 - Andromeda",
    safety: "clouds/rain/wind · fail closed",
    cloudwatch: "in >40% · clear 4m",
    dome: "bind to mount · fail closed",
    flatpanel: "dust-cover panel · 28500 ADU",
    slew: "±0.5′ · ASTAP",
    autofocus: "V-curve sweep · 9 pts",
    guide: "PHD2 · settle 1.5″",
    capture: "L · 120s · g100 · ×24",
    duskflats: "translucent lens cap · Sun −2° … −8° · ×15",
    calib: "darks → bias → flats · ×20 each, then wait",
    pool: "4 candidates · best available · quota ×45",
    cycle: "7 filters · 1/pass · ×45",
    // GN-08: the CONDITION node's default watchdog is now RELATIVE (a factor
    // of the post-focus baseline HFR, not an absolute pixel value) - see
    // nodeDefs.ts's condition.params and the module-header comment on why the
    // unit lives in the option label ("(x focus)") rather than a per-option
    // FieldDef.unit.
    condition: "hfr above (x focus) 1.3 · 3 frames",
    holdresume: "resume: re-center · refocus if hfr drifted",
    notify: "ntfy · rig-alerts",
    refocus: "autofocus, then resume",
    parkclose: "dust flap + dome · hold cold",
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
    "1 candidates · round robin · quota ×45", "pool counts comma-separated members");
  eq(defOf("dusk").sum({ ...defOf("dusk").params, repeat: "Nightly ×30" }),
    "Astro dusk -30m → dawn · nightly",
    "a repeat turns the window into a campaign, and the footer says so");
  eq(defOf("cycle").sum({ ...defOf("cycle").params, plan: "Ha 300, OIII 300", cycles: 12 }),
    "2 filters · 1/pass · ×12", "the cycle footer counts its own slots");
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
  // DELIBERATE PIN CHANGE (mosaic S3, spec 3.1): `created_as` joined. It is
  // vocabulary (what a new node is written with), not presentation, so the
  // §G-4 question is unchanged; nodeDefs.ts mirrors it as `createdAs`.
  eq(declared.join(","), "type,label,cat,ins,outs,params,optional_ins,created_as",
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
