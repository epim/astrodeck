// lastSessionFrameGuards.test.ts — the guards in lib/lastSessionFrame.ts that
// its own test file asserts but does not actually exercise.
//
//   Run directly:  npx tsx src/lib/__tests__/lastSessionFrameGuards.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// FOUND BY SABOTAGE. Each test below was written because deleting the line it
// names left the whole UI suite green. The existing lastSessionFrame.test.ts
// has a test for each of them — the assertions are there, in the right words —
// but every one of its fixtures is refused by a DIFFERENT guard first, so the
// named guard could be deleted without a single failure. A test whose subject
// is shadowed by another rule is a test that cannot fail.
//
// The rule each case restores: refuse for the reason under test, and prove it
// by showing the same inputs pass when only that reason is removed.

import type { GalleryFrame, SequenceState } from "../../types";
import { lastFrameAlt, pickSessionFrame } from "../lastSessionFrame";

let passed = 0, failed = 0;
const failures: string[] = [];
function test(n: string, f: () => void): void {
  try { f(); passed++; } catch (e) { failed++; failures.push(`x ${n}: ${(e as Error).message}`); }
}
function assert(c: boolean, m: string): void { if (!c) throw new Error(m); }

const NOW_MS = 1_755_400_000_000;
const NOW_S = NOW_MS / 1000;

function frame(over: Partial<GalleryFrame> = {}): GalleryFrame {
  return {
    path: "NGC 6946/Light_NGC6946_L_180s_0042.fits",
    name: "Light_NGC6946_L_180s_0042.fits",
    folder: "NGC 6946",
    night: "2026-08-16",
    ts: NOW_S - 60,
    local_date: "2026-08-17",
    local_clock: "23:14",
    target: "NGC 6946",
    filter: "L",
    frame_type: "Light",
    exposure_s: 180,
    bytes: 52_000_000,
    mtime: NOW_S - 55,
    ...over,
  };
}

const progress = {
  frames_done: 42, frames_total: 120, percent: 35, elapsed_s: 7800,
  rejected: 0, server_now_ms: NOW_MS,
};

// ------------------------------------------------- the run-state guard, alone
test("an idle rig REPORTING A TARGET is still refused", () => {
  // WHY THIS AND NOT THE EXISTING TEST. "an idle rig gets nothing, however
  // fresh the frame" passes `{ state: "idle" }` — a sequence with no target at
  // all — so `if (!want) return null` refuses it two lines later and
  // `if (!runIsLive(seq)) return null` never decides anything. Delete the
  // run-state guard and that test still passes.
  //
  // A real idle sequence carries the target it just finished: the engine keeps
  // publishing `target` through "complete" (SequenceState.target is not cleared
  // on the terminal states), which is exactly the shape that would paint the
  // last frame of a finished run where the live view goes.
  const finished: SequenceState = { state: "complete", target: "NGC 6946", progress };
  const idle: SequenceState = { state: "idle", target: "NGC 6946", progress };

  // PRECONDITION: this seq+frame pair differs from an accepted one ONLY in the
  // run state, so a refusal below can be nothing else.
  const live: SequenceState = { state: "running", target: "NGC 6946", progress };
  assert(pickSessionFrame([frame()], live, NOW_MS) != null,
    "the fixture is refused even under a running sequence — this test proves nothing");

  assert(pickSessionFrame([frame()], finished, NOW_MS) === null,
    "a finished run's own target still matched — the stage would keep the last " +
    "frame of a run that has ended sitting where the live view goes");
  assert(pickSessionFrame([frame()], idle, NOW_MS) === null,
    "an idle rig that remembers its target matched");
  assert(pickSessionFrame([frame()], { state: "error", target: "NGC 6946" }, NOW_MS) === null,
    "a rig that stopped on an error matched");
  assert(pickSessionFrame([frame()], { state: "nina_native", target: "NGC 6946" }, NOW_MS) === null,
    "NINA driving matched — we would paint a frame under a run we cannot see");
});

// ------------------------------------------- the untargeted-run guard, alone
test("an untargeted run does not match an untargeted frame", () => {
  // WHY THIS AND NOT THE EXISTING TEST. "a frame with no target at all gets
  // nothing" pairs an untargeted FRAME with a targeted run, and a targeted
  // frame with an untargeted RUN. Both are refused by the per-frame compare
  // (`"" !== "ngc6946"`). The case `if (!want) return null` exists for — the
  // one the module's own comment names — is BOTH sides empty, and nothing
  // asserted it. Delete the guard and the suite stays green.
  //
  // `target` falls back to the folder name server-side and is "" only when the
  // header was unreadable AND the frame sits at the library root; a run's
  // target is "" for a plan step the engine could not name. Neither is rare
  // enough to leave to chance, and "" === "" would make EVERY such frame a
  // candidate for EVERY such run.
  const headerless = frame({ target: "", folder: "", path: "orphan.fits" });
  const untargeted: SequenceState = { state: "running", target: "", progress };

  // PRECONDITION: the frame is otherwise perfectly acceptable — fresh, real
  // path, real ts — so what follows is the empty target and nothing else.
  assert(pickSessionFrame([{ ...headerless, target: "NGC 6946" }],
    { state: "running", target: "NGC 6946", progress }, NOW_MS) != null,
    "the fixture is unusable even with a target — this test proves nothing");

  assert(pickSessionFrame([headerless], untargeted, NOW_MS) === null,
    "an untargeted run matched a header-unreadable frame: two blanks compared " +
    "equal, so any orphan in the library can stand in for any run");
  assert(pickSessionFrame([headerless], { state: "running", progress }, NOW_MS) === null,
    "a run with an ABSENT target matched the same frame");
});

// ------------------------------------------------------------------ alt text
test("the alt text says the picture is not the live view", () => {
  // Nothing anywhere read lastFrameAlt. Its whole job is to tell someone who
  // cannot see the stage the one thing the sighted operator gets from the chip
  // — that this is not what the camera is pointing at right now. Replace the
  // body with "image" and the suite stayed green.
  const s = lastFrameAlt(frame());
  assert(/not the live view/i.test(s),
    `"${s}" does not tell a screen-reader user the picture is not live — the ` +
    "chip carries that for everyone else");
  assert(s.includes("NGC 6946"), `"${s}" omits the target`);
  assert(s.includes("23:14"), `"${s}" omits when it was taken`);
});

test("alt text survives a frame the server could not clock or name", () => {
  const s = lastFrameAlt(frame({ local_clock: "", target: "" }));
  assert(/not the live view/i.test(s), `"${s}"`);
  assert(!/undefined|null|NaN/.test(s), `"${s}" leaked a missing field`);
});

const total = passed + failed;
console.log(`lastSessionFrameGuards: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
