// r7Disabled.test.ts - the new UI never uses the native `disabled` attribute.
//
//   Run directly:  npx tsx src/next/__tests__/r7Disabled.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// WHY THIS TEST EXISTS. Non-negotiable 6 (`ARCHITECTURE.md:31`): a control a
// user could plausibly want to press never gets the native `disabled`
// attribute. That attribute takes the control out of the accessibility tree,
// takes the REASON with it, and leaves a grey rectangle that cannot even be
// focused to ask why. The honest form is `ui/honest.ts`: dim, `aria-disabled`,
// still focusable, and a press states the reason (`useLock` -> `lockedReason`
// + `onExplain`).
//
// It is worth a source-reading test because `disabled={busy}` is the single
// most reflexive line in React, it type-checks, it looks careful, and a DOM
// test asserting "the button does not fire while busy" passes either way. The
// legacy tree is full of them - `RotatorCard.tsx` alone had eight, and
// `SkyPackEditor`'s note names three different refusals rendered as one grey
// button - which is exactly how the habit walks back in when a rebuilt panel
// is copied from the panel it replaced.
//
// THE IMPOSSIBILITY CASES, and why the list below is EMPTY. Wave R7 named two
// refusals that are not permissions but physics, and asked whether they earn a
// native `disabled`:
//
//   * a frame that arrived already stretched (NINA, `is_stretched`): black,
//     mid and white cannot be re-derived from it at all. Shipped as
//     `lockedReason` in `rig/inspect/StretchPanel.tsx` - the operator can
//     still focus the handle and read WHY it will not move, which a native
//     `disabled` would have withheld.
//   * the flows canvas on a phone: it opens on a tablet or desktop. Shipped as
//     a lock reason on the row, not a dead control.
//
// Both are impossibilities and both are still honest-disabled, so the
// exception list has no entries. If a genuinely native case ever appears, it
// goes in `NATIVE_OK` with the reason and the reviewer sees it in the diff.
//
// SABOTAGE (run, red, restored - see the task report): add `disabled={busy}`
// to any button under `next/` and "no native disabled attribute" goes red with
// the file and line.

/* eslint-disable @typescript-eslint/no-explicit-any */

const { readFileSync, readdirSync, statSync } = await import("node:fs");
const { fileURLToPath } = await import("node:url");

const NEXT = fileURLToPath(new URL("../", import.meta.url));
const SEP = NEXT.includes("\\") ? "\\" : "/";

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

// ------------------------------------------------------------------ corpus

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = `${dir}${SEP}${name}`;
    if (statSync(p).isDirectory()) walk(p, out);
    else out.push(p);
  }
  return out;
}

const stripComments = (s: string): string =>
  s.replace(/(^|[^:])\/\/[^\n]*/g, "$1").replace(/\/\*[\s\S]*?\*\//g, "");

const ALL = walk(NEXT.slice(0, -1)).filter((p) => /\.tsx?$/.test(p) && !p.endsWith(".d.ts"));
const isTest = (p: string): boolean =>
  p.includes(`${SEP}__tests__${SEP}`) || /\.test\.tsx?$/.test(p);
// Tests are excluded because a test ASSERTS on the attribute (`getAttribute
// ("disabled")` is how several of them prove it is absent), and reading that
// as an emission would make the guard unwritable.
const SOURCES = ALL.filter((p) => !isTest(p));

/** Native `disabled` in JSX: the bare attribute (`<button disabled>`), the
 *  bound one (`disabled={busy}`) and the literal (`disabled="disabled"`). The
 *  lookbehind drops `aria-disabled` and `data-disabled`; requiring `=`, `>`,
 *  `/` or `}` after the name drops prose ("flip disabled - pier risk"), record
 *  keys (`disabled: "warn"`) and optional props (`disabled?: boolean`). */
const NATIVE = /(?<![-\w])disabled\s*(=|\/>|>|\})/g;

/** Genuinely native cases, each with the reason. Empty on purpose - see the
 *  header: the two impossibility cases the wave named both ship as
 *  `lockedReason`. */
const NATIVE_OK: Record<string, string> = {};

interface Hit { file: string; line: number; text: string }

const HITS: Hit[] = [];
for (const p of SOURCES) {
  const text = stripComments(readFileSync(p, "utf8"));
  const rel = p.slice(NEXT.length).replace(/\\/g, "/");
  for (const m of text.matchAll(NATIVE)) {
    const at = m.index ?? 0;
    const line = text.slice(0, at).split("\n").length;
    const start = text.lastIndexOf("\n", at) + 1;
    HITS.push({ file: rel, line, text: text.slice(start, text.indexOf("\n", at)).trim() });
  }
}

// ================================================================ 1. vacuity
// A regex that matches nothing passes test 2 forever. This pins the detector
// against a positive control and against the honest idiom it is the negative
// half of.

test("the sweep can actually see a native disabled attribute", () => {
  assert(SOURCES.length >= 200, `only ${SOURCES.length} source modules under next/`);
  const probe = [
    '<button disabled>x</button>',
    '<button disabled={busy}>x</button>',
    '<input disabled />',
    '<input disabled="disabled" />',
  ];
  for (const s of probe) {
    assert(new RegExp(NATIVE.source).test(s), `the detector missed ${s}`);
  }
  const notNative = [
    '<button aria-disabled="true">x</button>',
    '<button data-disabled={x}>y</button>',
    'sub: `flip disabled - pier risk`',
    'const VERDICT = { disabled: "warn" };',
    'interface P { disabled?: boolean }',
  ];
  for (const s of notNative) {
    assert(!new RegExp(NATIVE.source).test(s), `the detector false-flagged ${s}`);
  }
});

// ============================================ 2. and there are none in the tree
test("no native disabled attribute anywhere under next/", () => {
  const problems = HITS
    .filter((h) => !NATIVE_OK[`${h.file}:${h.line}`])
    .map((h) => `${h.file}:${h.line}  ${h.text}`);
  assert(problems.length === 0,
    `${problems.length} native disabled attribute(s) - each of these has to become ` +
    `lockedReason + onExplain (ui/honest.ts): ${problems.join(" | ")}`);
});

// ============================================== 3. nor a `disabled` prop to pass
// The other route in: a next-side component that ACCEPTS `disabled` and
// forwards it. Then the attribute is written once, in a primitive, and every
// call site looks innocent. `lockedReason` is the only refusal channel.

test("no next-side component declares a disabled prop", () => {
  const problems: string[] = [];
  for (const p of SOURCES) {
    const text = stripComments(readFileSync(p, "utf8"));
    const rel = p.slice(NEXT.length).replace(/\\/g, "/");
    for (const m of text.matchAll(/(?<![-\w])disabled\??\s*:\s*(boolean|\(|true|false)/g)) {
      const line = text.slice(0, m.index ?? 0).split("\n").length;
      problems.push(`${rel}:${line} declares a \`disabled\` prop`);
    }
  }
  assert(problems.length === 0, problems.join("; "));
});

// ================================================ 4. the honest idiom is in use
// The negative rule above is only meaningful if the positive one is being
// followed - "no disabled attributes" is also true of a UI that hides every
// control it cannot offer, which is the other way to lose the reason.

test("the honest-disabled idiom is what the tree uses instead", () => {
  let aria = 0;
  let locked = 0;
  for (const p of SOURCES) {
    const text = stripComments(readFileSync(p, "utf8"));
    aria += [...text.matchAll(/aria-disabled/g)].length;
    locked += [...text.matchAll(/lockedReason/g)].length;
  }
  assert(aria >= 20, `only ${aria} aria-disabled sites - is the honest idiom in use?`);
  assert(locked >= 200, `only ${locked} lockedReason mentions across next/`);
});

// ---------------------------------------------------------------- report
const total = passed + failed;
console.log(`r7Disabled.test: ${passed}/${total} passed`);
for (const f of failures) console.error(f);
if (failed > 0) process.exitCode = 1;

export { passed, failed, total };
