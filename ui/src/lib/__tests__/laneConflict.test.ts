// laneConflict.test.ts — the raw lane name must never reach a person.
//
//   Run directly:  npx tsx src/lib/__tests__/laneConflict.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT WENT WRONG. Every long operation on the rig runs as a named background
// task and a second request for the same name is refused with
// `HTTPException(409, f"'{name}' is already running")`. api.ts lifts that detail
// straight into ApiError.message, and roughly twenty controls hand it to
// showToast — so pressing a control the rig had already taken answered with a
// red toast reading `'guide_assistant' is already running`. That is the name of
// a Python coroutine slot. It does not say what the rig is doing and it does
// not say what to do instead.
//
// THE PART THAT IS EASY TO GET WRONG. A hand-written list of lanes rots the
// moment somebody adds a route, and the failure is silent: the new lane simply
// leaks its identifier again. So the last test here does not trust a list
// written in this repo's UI at all — it reads the server's own `_spawn("…")`
// call sites out of app.py and requires an entry for each. Adding a lane
// server-side without a sentence for it is a red test, not a 2 a.m. surprise.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { humanizeLaneConflict, humanizeSeqError, humanizeLog, MAPPED_BUSY_LANES } from "../humanize";
import { parseApiError } from "../apiError";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

/** The leak signature, not a blanket ban on the words.
 *
 *  Several lanes ARE ordinary English ("solve", "rotator", "dither"), so a
 *  substring check for the lane name would fail on sentences that are perfectly
 *  fine. What must never appear is the server's SHAPE — a quoted identifier —
 *  or a token no human writes: snake_case, or the dotted "system.update". */
function looksInternal(s: string): boolean {
  return /'[A-Za-z0-9_.-]+' is already running/.test(s) ||
    /[a-z]_[a-z]/.test(s) ||
    s.includes("system.update");
}

// ------------------------------------------------- the shape the server sends
test("the exact 409 body the UI provokes stops being a lane name", () => {
  // Verbatim from server/astrodeck/api/app.py:423 —
  //   raise HTTPException(409, f"'{name}' is already running")
  // which FastAPI serialises as {"detail": "'goto' is already running"}.
  const { message } = parseApiError(409, { detail: "'goto' is already running" }, "Conflict");
  assert(!looksInternal(message),
    `the toast still reads ${JSON.stringify(message)} — the lane id reached the user`);
  assert(/mount/i.test(message), `the message never says what is running: ${JSON.stringify(message)}`);
  assert(message.length > 30, "one word is not an explanation");
});

test("every lane the UI can provoke gets a sentence, not an identifier", () => {
  // PRECONDITION: this loop is worthless if the list is empty.
  assert(MAPPED_BUSY_LANES.length >= 15,
    `only ${MAPPED_BUSY_LANES.length} lanes are mapped — the table is not covering the rig`);
  for (const lane of MAPPED_BUSY_LANES) {
    const raw = `'${lane}' is already running`;
    const { message } = parseApiError(409, { detail: raw }, "Conflict");
    assert(message !== raw, `${lane}: the raw refusal passed straight through`);
    assert(!looksInternal(message), `${lane}: ${JSON.stringify(message)} still exposes the lane id`);
    // "what to do" is the half that makes a refusal actionable; every entry
    // ends in an instruction, and the cheapest check that one exists is that
    // the sentence has a second one.
    assert(/[.!] /.test(message), `${lane}: ${JSON.stringify(message)} names the problem but not the way out`);
  }
});

// ------------------------------------------------ the sentence names an ACTION
test("no lane sentence sends the user to a PAGE — two shells read these strings", () => {
  // Two front-ends import this file: the classic one (Mount / Focus / Capture /
  // Guide pages) and the new one (a Rig hub of device sheets plus a Capture
  // sub-screen — none of those pages exist there). "stop it on the Focus page"
  // was therefore an instruction half the readers cannot follow, and it goes
  // stale again the next time a screen is renamed. The fix that holds is to name
  // what is running and what ends it, so the sentence is true in any shell.
  //
  // The word itself is the check: no entry in this table needs it, and a
  // reworded sentence that reintroduces one ("open the Guide page") is exactly
  // the regression this guards.
  for (const lane of MAPPED_BUSY_LANES) {
    const { message } = parseApiError(409, { detail: `'${lane}' is already running` }, "Conflict");
    assert(!/\bpage\b/i.test(message),
      `${lane}: ${JSON.stringify(message)} points at a page — the new shell has none, ` +
      "so name the action that unblocks the lane instead");
    // House copy rule (ARCHITECTURE.md non-negotiable 5): hyphens, never em-dashes.
    assert(!message.includes("—"),
      `${lane}: ${JSON.stringify(message)} uses an em-dash`);
  }
});

test("the four reworded lanes still say what ends them", () => {
  // The half of the sentence that makes the refusal actionable. Graded literally,
  // against the text the code emits: a rewording that drops the way out (or
  // reintroduces a page name in place of it) fails here rather than shipping a
  // dead end. Kept to the four lanes whose old copy named a page.
  const ends: [string, RegExp][] = [
    ["goto", /stop the mount first/],
    ["autofocus", /stop the autofocus first/],
    ["capture", /abort the capture first/],
    ["guide_assistant", /stop the assistant first/],
  ];
  for (const [lane, ending] of ends) {
    const { message } = parseApiError(409, { detail: `'${lane}' is already running` }, "Conflict");
    assert(ending.test(message),
      `${lane}: ${JSON.stringify(message)} no longer names the action that unblocks it ` +
      `(expected to match ${ending})`);
    // ...and it must still say what is BUSY, not only what to press.
    assert(/already/.test(message),
      `${lane}: ${JSON.stringify(message)} stopped saying what is running`);
  }
});

test("the sequence-error sentences name an action too, not a screen", () => {
  // Same file, same two shells: these three fall through to the non-lane
  // branches of humanizeSeqError, which used to read "check the Mount page".
  const cases: [string, RegExp][] = [
    ["mount slew failed: no response", /check the mount is connected, unparked and tracking/],
    ["autofocus curve rejected", /re-run autofocus, or set focus by hand/],
    ["guiding star lost for 60 s", /re-run the calibration, or pick a brighter guide star/],
  ];
  for (const [detail, expected] of cases) {
    const s = humanizeSeqError(detail);
    assert(expected.test(s), `${JSON.stringify(detail)} -> ${JSON.stringify(s)}, expected ${expected}`);
    assert(!/\b(Mount|Focus|Capture|Guide)\s+page\b/i.test(s),
      `${JSON.stringify(s)} still sends the user to a page the new shell does not have`);
  }
});

test("an unknown lane is still a sentence — a leak must not be the default", () => {
  const { message } = parseApiError(409, { detail: "'brand_new_lane' is already running" }, "Conflict");
  assert(!message.includes("brand_new_lane"),
    `an unmapped lane leaked its id: ${JSON.stringify(message)}`);
  assert(/busy/i.test(message), `the fallback says nothing useful: ${JSON.stringify(message)}`);
});

// ------------------------------------------------------- the nested 409 shape
test("the same refusal raised with a machine-readable code is mapped too", () => {
  // HTTPException(409, detail={"detail": …, "code": "running"}) — the other
  // shape app.py uses. The message half must get the same treatment, and the
  // code half must survive, because call sites branch on it.
  const { message, code } = parseApiError(409, {
    detail: { detail: "'solve' is already running", code: "running" },
  });
  assert(code === "running", "the machine-readable code was lost in the rewrite");
  assert(!looksInternal(message), `nested shape leaked the lane: ${JSON.stringify(message)}`);
  assert(/plate-solve/i.test(message), `the nested shape was not mapped: ${JSON.stringify(message)}`);
});

// ----------------------------------------------------- nothing else is touched
test("every other error message passes through untouched", () => {
  // The regex is anchored on purpose. A rewrite layer that is even slightly
  // greedy is worse than the leak it fixed: it replaces a real diagnosis with
  // a guess.
  const cases = [
    "target is within 12 deg of the Sun (exclusion 15 deg)",
    "a sequence is already running",                    // engine's own wording
    "polar alignment is already running",               // polar/session.py
    "the mount reports 'goto' is already running somewhere in a longer line",
  ];
  for (const detail of cases) {
    const { message } = parseApiError(409, { detail });
    assert(message === detail, `rewrote an unrelated message: ${JSON.stringify(message)}`);
    assert(humanizeLaneConflict(detail) === null, `matched something it should not: ${detail}`);
  }
  assert(parseApiError(500, undefined, "Internal Server Error").message === "Internal Server Error",
    "the statusText fallback was mangled");
});

test("the log and sequence-error paths get the same branch", () => {
  // Same string, different transport: a route that 409s inside a sequence step
  // is logged verbatim, and the store toasts every error line.
  const logged = humanizeLog({ source: "goto", message: "'goto' is already running" });
  assert(!looksInternal(logged), `the log path still leaks: ${JSON.stringify(logged)}`);
  const seq = humanizeSeqError("'autofocus' is already running");
  assert(/autofocus run/i.test(seq), `the sequence path was not mapped: ${JSON.stringify(seq)}`);
});

// ------------------------------------------------- the guard against rot
test("the table covers every lane the SERVER can refuse, not every lane we remembered", () => {
  // Read app.py rather than a mirror of it. `npm test` and CI both run from a
  // full checkout, so a missing file here is a broken assumption, not a reason
  // to pass quietly — hence no try/catch.
  const appPy = fileURLToPath(new URL("../../../../server/astrodeck/api/app.py", import.meta.url));
  let src: string;
  try {
    src = readFileSync(appPy, "utf8");
  } catch {
    // Still a hard failure — see above; a silent skip would retire the guard.
    // But say WHY, because the bare ENOENT is unreadable: it surfaced once from
    // a CI-parity run that had copied only ui/ into a container, and the raw
    // errno gave no hint that the cause was the harness rather than the code.
    throw new Error(
      `cannot read ${appPy} — this check reads the SERVER's own _spawn() calls, ` +
      "so it needs the whole repo, not ui/ alone. If you are running the UI " +
      "suite from a partial copy, copy the repo root instead.",
    );
  }
  const lanes = new Set<string>();
  for (const m of src.matchAll(/_spawn\(\s*"([A-Za-z0-9_.-]+)"/g)) lanes.add(m[1]);
  // _spawn_connect keeps the connect task out of hub._busy and writes its own
  // refusal by hand, so it is not in the regex above.
  for (const m of src.matchAll(/HTTPException\(409,\s*"'([A-Za-z0-9_.-]+)' is already running"\)/g)) {
    lanes.add(m[1]);
  }

  // PRECONDITION: a regex that matched nothing would make this test a no-op
  // that passes forever — the exact shape it exists to prevent.
  assert(lanes.size >= 14,
    `only found ${lanes.size} lanes in app.py — the scan is broken, so this test proves nothing`);
  assert(lanes.has("goto") && lanes.has("system.update"),
    "the scan missed lanes known to exist — it is matching the wrong thing");

  const missing = [...lanes].filter((l) => !MAPPED_BUSY_LANES.includes(l)).sort();
  assert(missing.length === 0,
    `app.py can refuse ${missing.join(", ")} but lib/humanize.ts has no sentence for ${
      missing.length === 1 ? "it" : "them"
    } — a 409 on that lane shows the user the raw lane id`);
});

const total = passed + failed;
console.log(`laneConflict.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
