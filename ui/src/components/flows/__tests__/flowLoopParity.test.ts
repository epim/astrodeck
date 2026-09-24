// flowLoopParity.test.ts - the flow-loop refusal is ONE sentence, in both the
// server and the editor (#149; spec 2026-09-23 section 1.4 item 5).
//
//   Run:  node --import tsx src/components/flows/__tests__/flowLoopParity.test.ts   (from ui/)
//
// WHY THIS PARSES models.py. `FLOW_LOOP_REFUSAL` lives twice: the server fills
// it in `FlowGraph.validation_errors` (the 422 a save or a /run gets), and
// `flowLoop.ts` fills it in both drop resolvers and in `flowsConnect` (the
// toast and the log line a drawn wire gets). An operator told one thing by the
// canvas and a different thing by the 422 has been told about two rules.
// `flowLoop.test.ts` pins the UI literal against a copy typed into that test,
// which proves only that one hand typed the same thing twice - the server can
// reword its constant and every UI test stays green. So this file reads the
// server's source and compares, the way `nodeDefs.test.ts` reads nodes.py. Do
// not replace the parser with a literal fixture.
//
// Mutants, each run WITHOUT touching either source file: the test was copied
// byte for byte into a scratch tree that mirrors `ui/src/components/flows/
// __tests__/` and `server/astrodeck/flows/`, beside a copy of models.py and a
// `flowLoop.ts` that re-exports the real module. The unmutated mirror passed
// 6/6, so the mirror reads its OWN models.py copy. One word was then changed
// in ONE of the two copies. Observed (5/6 each):
//
//   MUTANT "server literal, one word" (models.py copy: "runs once" -> "runs twice"):
//     x the UI's FLOW_LOOP_REFUSAL is the server's, character for character:
//       the canvas and the 422 now say different things; first difference at
//       character 67
//       server: "this flow loops back on itself at {src} -> {dst}; a flow lane runs twice"
//       ui:     "this flow loops back on itself at {src} -> {dst}; a flow lane runs once"
//
//   MUTANT "UI literal, one word" (mirror flowLoop.ts exports its own
//   FLOW_LOOP_REFUSAL with "loops back" -> "doubles back"):
//     x the UI's FLOW_LOOP_REFUSAL is the server's, character for character:
//       the canvas and the 422 now say different things; first difference at
//       character 10
//       server: "this flow loops back on itself at {src} -> {dst}; a flow lane runs once"
//       ui:     "this flow doubles back on itself at {src} -> {dst}; a flow lane runs once"

/* eslint-disable @typescript-eslint/no-explicit-any */

import { FLOW_LOOP_REFUSAL } from "../flowLoop";
// @ts-ignore  no @types/node guaranteed; tsx supplies fs at runtime
import { readFileSync } from "node:fs";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

// ================================================================ models.py

const MODELS_PY_REL = "../../../../../server/astrodeck/flows/models.py";
const modelsPySrc: string = (() => {
  try {
    return readFileSync(new URL(MODELS_PY_REL, import.meta.url), "utf8") as string;
  } catch (e) {
    // A MISSING models.py must fail, never skip: a skipped cross-check reads as
    // green while the only thing holding the two sentences together is gone.
    throw new Error(`cannot read ${MODELS_PY_REL} - the refusal cross-check cannot run: `
      + (e as Error).message);
  }
})();

const ESCAPES: Record<string, string> = {
  "\\": "\\", "'": "'", '"': '"', n: "\n", t: "\t",
};

/**
 * The string value of a module-level `NAME = <literal>` in Python source.
 *
 * Deliberately NOT a Python evaluator. It reads the one shape a message
 * constant takes - one string literal, or several implicitly concatenated,
 * optionally inside parentheses and across lines, with `#` comments between -
 * and THROWS on anything else (an f-string, a `.format`, a `+`, a name, a
 * triple quote, an escape it does not know). A constant it cannot read
 * honestly must stop the test, never be guessed at.
 */
function pyStringConstant(src: string, name: string): string {
  const head = new RegExp(`^${name}\\s*(?::[^=\\n]*)?=[ \\t]*`, "m").exec(src);
  if (!head) throw new Error(`${name} is not assigned at module level in models.py - did it move or get renamed?`);
  let i = head.index + head[0].length;
  const parens = src[i] === "(";
  if (parens) i++;
  const parts: string[] = [];
  for (;;) {
    const c = src[i];
    if (c === undefined) throw new Error(`${name}: the source ended inside the value`);
    if (c === " " || c === "\t" || c === "\r") { i++; continue; }
    if (c === "\\" && (src[i + 1] === "\n" || src.startsWith("\r\n", i + 1))) {
      i += src[i + 1] === "\n" ? 2 : 3;       // an explicit line continuation
      continue;
    }
    if (c === "\n") {
      if (!parens) break;                      // the end of the logical line
      i++;
      continue;
    }
    if (c === "#") {
      while (i < src.length && src[i] !== "\n") i++;
      continue;
    }
    if (c === ")" && parens) break;
    if (c === '"' || c === "'") {
      if (src.startsWith(c.repeat(3), i)) throw new Error(`${name}: a triple-quoted string is not read here`);
      let out = "";
      let j = i + 1;
      for (; j < src.length && src[j] !== c; j++) {
        if (src[j] === "\n") throw new Error(`${name}: an unterminated string`);
        if (src[j] === "\\") {
          const e = ESCAPES[src[j + 1]];
          if (e === undefined) throw new Error(`${name}: an escape this parser does not know: \\${src[j + 1]}`);
          out += e;
          j++;
          continue;
        }
        out += src[j];
      }
      if (j >= src.length) throw new Error(`${name}: an unterminated string`);
      parts.push(out);
      i = j + 1;
      continue;
    }
    throw new Error(`${name}: ${JSON.stringify(src.slice(i, i + 24))} is not a plain string literal - `
      + "the constant is no longer one sentence this test can read");
  }
  if (parts.length === 0) throw new Error(`${name}: no string literal found`);
  return parts.join("");
}

function firstDifference(a: string, b: string): number {
  const n = Math.min(a.length, b.length);
  for (let k = 0; k < n; k++) if (a[k] !== b[k]) return k;
  return n;
}

// ================================================================== parser
// The parser is checked on sources whose answer is not in doubt, so a parser
// that quietly returns "" or the first fragment cannot make the real
// comparison below meaningless.

test("parser sanity: implicit concatenation across lines, comments between", () => {
  const src = 'X = 1\nMSG = ("one, "  # first half\n       \'two\'\n       "\\"three\\"")\nY = 2\n';
  const got = pyStringConstant(src, "MSG");
  assert(got === 'one, two"three"', `read ${JSON.stringify(got)}`);
});

test("parser sanity: an unparenthesised literal stops at the end of its line", () => {
  const got = pyStringConstant('MSG = "alone"\nOTHER = "not me"\n', "MSG");
  assert(got === "alone", `read ${JSON.stringify(got)}`);
});

test("parser sanity: anything but plain literals is refused, not guessed", () => {
  for (const bad of [
    'MSG = f"loop at {src}"\n',
    'MSG = ("a " + "b")\n',
    'MSG = PREFIX + "b"\n',
    'MSG = """doc"""\n',
    'MSG = "a \\x41"\n',
  ]) {
    let threw = false;
    try { pyStringConstant(bad, "MSG"); } catch { threw = true; }
    assert(threw, `read ${JSON.stringify(bad)} instead of refusing it`);
  }
});

test("parser sanity: a missing constant fails loudly", () => {
  let msg = "";
  try { pyStringConstant("OTHER = 'x'\n", "FLOW_LOOP_REFUSAL"); } catch (e) { msg = (e as Error).message; }
  assert(/not assigned at module level/.test(msg), `a missing constant was not reported: ${msg || "(no error)"}`);
});

// ================================================================== parity

const SERVER = pyStringConstant(modelsPySrc, "FLOW_LOOP_REFUSAL");

test("the server's constant was read, placeholders and all", () => {
  // Both placeholders are what `flowLoopRefusal` fills with `.replace`, and
  // what the server fills with `.format(src=, dst=)`: a sentence with one of
  // them missing would name only one end of the loop in one of the editors.
  assert(SERVER.includes("{src}") && SERVER.includes("{dst}"),
    `models.py's FLOW_LOOP_REFUSAL lost a placeholder: ${JSON.stringify(SERVER)}`);
  assert(SERVER.indexOf("{src}") < SERVER.indexOf("{dst}"), "the source is named first");
});

test("the UI's FLOW_LOOP_REFUSAL is the server's, character for character", () => {
  if (FLOW_LOOP_REFUSAL !== SERVER) {
    throw new Error(
      "the canvas and the 422 now say different things; first difference at character "
      + `${firstDifference(SERVER, FLOW_LOOP_REFUSAL)}\n`
      + `  server: ${JSON.stringify(SERVER)}\n`
      + `  ui:     ${JSON.stringify(FLOW_LOOP_REFUSAL)}`);
  }
});

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`flowLoopParity.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export default { passed, failed, total };
export { passed, failed, total };
