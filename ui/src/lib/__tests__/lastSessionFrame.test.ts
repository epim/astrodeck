// lastSessionFrame.test.ts — WHICH already-saved frame may stand in for the
// live one on the Capture stage, and when nothing may.
//
//   Run directly:  npx tsx src/lib/__tests__/lastSessionFrame.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// The whole value of this module is its REFUSALS. Painting the newest file on
// disk under a running sequence is trivial and wrong: a frame from last week
// sitting where the live view goes is a lie about what the camera is doing
// right now, and worse than the logo, which promises nothing. So most of what
// follows asserts that a candidate was turned down, and each of those is
// preceded by a positive case proving the picker can say yes at all — a
// refusal test against a function that always returns null is vacuous.

import type { GalleryFrame, SequenceState } from "../../types";
import {
  SESSION_FRAME_MAX_AGE_S,
  lastFrameLabel,
  pickSessionFrame,
  placeholderViewWidth,
  runIsLive,
  targetKey,
} from "../lastSessionFrame";
import { VIEW_WIDTH_STEPS } from "../frameView";

let passed = 0, failed = 0;
const failures: string[] = [];
function test(n: string, f: () => void): void {
  try { f(); passed++; } catch (e) { failed++; failures.push(`x ${n}: ${(e as Error).message}`); }
}
function assert(c: boolean, m: string): void { if (!c) throw new Error(m); }

// Tonight, on the RIG's clock. Every `ts` below is expressed against it.
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

/** A run in flight. `server_now_ms` is the field that makes the age test honest
 *  — `ts` is stamped by the RIG, so comparing it against the BROWSER's clock
 *  compares two clocks that a relay-connected user has no reason to expect to
 *  agree. */
function running(over: Partial<SequenceState> = {}): SequenceState {
  return {
    state: "running",
    target: "NGC 6946",
    progress: {
      frames_done: 42, frames_total: 120, percent: 35, elapsed_s: 7800,
      rejected: 0, server_now_ms: NOW_MS,
    },
    ...over,
  };
}

// ------------------------------------------------------------------ is it live
test("running, paused and aborting are all live runs", () => {
  assert(runIsLive({ state: "running" }), "running");
  assert(runIsLive({ state: "paused" }),
    "a paused run has frames on disk and a target on the mount — it is still a session");
  assert(runIsLive({ state: "aborting" }),
    "abort awaits the whole wind-down (~210s); a client that treats it as over " +
    "blanks the stage over a rig that is still imaging");
});

test("an idle or finished rig is not a live run", () => {
  for (const s of ["idle", "complete", "aborted", "error"] as const) {
    assert(!runIsLive({ state: s }), `${s} counted as live`);
  }
  assert(!runIsLive(null), "null");
  assert(!runIsLive(undefined), "undefined");
});

test("nina_native is not one of ours", () => {
  // NINA is driving; the target it is shooting is not published to us, so the
  // target test below could never pass. Excluded on purpose rather than left to
  // fail silently one layer down.
  assert(!runIsLive({ state: "nina_native" }),
    "nina_native accepted — we would fetch a listing we can never match");
});

// ------------------------------------------------------------- target identity
test("target identity ignores case, spacing and punctuation", () => {
  assert(targetKey("M 31") === targetKey("m31"),
    "the catalogue name a user typed and the OBJECT header the rig wrote " +
    "differ by a space more often than not");
  assert(targetKey("NGC 6946") === targetKey("ngc-6946"), "NGC 6946");
  assert(targetKey("") === "", "empty");
  assert(targetKey(null) === "", "null");
  assert(targetKey(undefined) === "", "undefined");
});

// ---------------------------------------------------------------- the positive
test("the newest frame of the running target is picked", () => {
  const older = frame({ ts: NOW_S - 600, path: "a.fits" });
  const newest = frame({ ts: NOW_S - 60, path: "b.fits" });
  const got = pickSessionFrame([newest, older], running(), NOW_MS);
  assert(got?.path === "b.fits", `picked ${got?.path ?? "nothing"}, expected b.fits`);
});

test("the listing's order is not trusted — the newest ts wins", () => {
  // The server sorts newest first, but this picker is the thing standing
  // between a stale file and the live view; it does its own arithmetic.
  const got = pickSessionFrame(
    [frame({ ts: NOW_S - 900, path: "old.fits" }), frame({ ts: NOW_S - 30, path: "new.fits" })],
    running(), NOW_MS,
  );
  assert(got?.path === "new.fits", `picked ${got?.path ?? "nothing"}`);
});

// ---------------------------------------------------------------- the refusals
test("an idle rig gets nothing, however fresh the frame", () => {
  // PRECONDITION: this exact frame IS accepted under a live run, so the refusal
  // below is the run state doing the work and not the frame being unusable.
  assert(pickSessionFrame([frame()], running(), NOW_MS) != null,
    "the fixture frame is refused even under a live run — this test proves nothing");
  assert(pickSessionFrame([frame()], { state: "idle" }, NOW_MS) === null,
    "a frame was offered under an idle rig — the stage would present last " +
    "night's picture as what the camera is looking at");
});

test("a different target gets nothing", () => {
  const other = frame({ target: "M 31", folder: "M 31", path: "m31.fits" });
  assert(pickSessionFrame([other], running(), NOW_MS) === null,
    "a frame of another object was offered for a run on NGC 6946");
});

test("a frame with no target at all gets nothing", () => {
  // `target` falls back to the folder name server-side and is "" only when the
  // header was unreadable AND the frame sits at the library root. Matching ""
  // against "" would make every such frame a candidate for every run.
  assert(pickSessionFrame([frame({ target: "" })], running(), NOW_MS) === null,
    "an untargeted frame matched a targeted run");
  assert(pickSessionFrame([frame()], running({ target: "" }), NOW_MS) === null,
    "a run with no target matched a frame");
  assert(pickSessionFrame([frame()], running({ target: undefined }), NOW_MS) === null,
    "a run with an absent target matched a frame");
});

test("last week's frame of the same target gets nothing", () => {
  const stale = frame({ ts: NOW_S - 7 * 86400, night: "2026-08-09" });
  assert(pickSessionFrame([stale], running(), NOW_MS) === null,
    "a week-old frame was offered as the current session — this is the exact " +
    "picture that is worse than the logo");
});

test("the age cut-off is where the constant says it is", () => {
  const inside = frame({ ts: NOW_S - (SESSION_FRAME_MAX_AGE_S - 60) });
  const outside = frame({ ts: NOW_S - (SESSION_FRAME_MAX_AGE_S + 60) });
  assert(pickSessionFrame([inside], running(), NOW_MS) != null, "just inside was refused");
  assert(pickSessionFrame([outside], running(), NOW_MS) === null, "just outside was accepted");
});

test("a fresh frame beside a stale one leaves the stale one alone", () => {
  const got = pickSessionFrame(
    [frame({ ts: NOW_S - 7 * 86400, path: "week.fits" }), frame({ ts: NOW_S - 90, path: "now.fits" })],
    running(), NOW_MS,
  );
  assert(got?.path === "now.fits", `picked ${got?.path ?? "nothing"}`);
});

// -------------------------------------------------------------------- the clock
test("the RIG's clock decides the age, not the browser's", () => {
  // A relay user in another timezone is not the problem — unix seconds are
  // absolute. A rig whose clock is simply WRONG is: an appliance with no RTC
  // that came up before NTP settled stamps `ts` hours away from the browser's
  // idea of now, and every frame it writes would then read as ancient.
  const browserAheadMs = NOW_MS + 4 * 3600 * 1000;
  assert(pickSessionFrame([frame()], running(), browserAheadMs) != null,
    "a browser clock 4h ahead of the rig refused a frame the rig wrote a " +
    "minute ago — the age was measured against the wrong clock");
});

test("with no server clock the browser's is used", () => {
  const seq = running({ progress: undefined });
  assert(pickSessionFrame([frame()], seq, NOW_MS) != null,
    "no progress block meant no answer at all");
  assert(pickSessionFrame([frame()], seq, NOW_MS + 4 * 3600 * 1000) === null,
    "with nothing better than the browser clock, its verdict has to stand");
});

test("a frame stamped well into the future is refused", () => {
  const future = frame({ ts: NOW_S + 30 * 86400 });
  assert(pickSessionFrame([future], running(), NOW_MS) === null,
    "a frame a month in the future was accepted — a clock we cannot reason " +
    "about must fall back to the empty state, not to guessing");
});

test("a frame a few seconds ahead of the last status frame is fine", () => {
  // `server_now_ms` rides the 2 s status publish, so a frame written since is
  // legitimately 'in the future' by a couple of seconds.
  assert(pickSessionFrame([frame({ ts: NOW_S + 3 })], running(), NOW_MS) != null,
    "the 2 s status cadence was treated as a broken clock");
});

test("a frame with no usable timestamp is refused", () => {
  for (const ts of [NaN, undefined as unknown as number, null as unknown as number]) {
    assert(pickSessionFrame([frame({ ts })], running(), NOW_MS) === null,
      `ts=${String(ts)} was accepted`);
  }
});

test("an empty listing is not an error", () => {
  assert(pickSessionFrame([], running(), NOW_MS) === null, "empty listing");
});

// --------------------------------------------------------------------- the copy
test("the label says it is not live and when it was taken", () => {
  const s = lastFrameLabel(frame());
  assert(/not live/i.test(s), `"${s}" does not say the picture is not live`);
  assert(s.includes("23:14"),
    `"${s}" omits the frame's clock — the operator cannot tell a 20-second-old ` +
    "stand-in from a twenty-minute-old one, which is the whole question");
});

test("a frame the server could not clock still says it is not live", () => {
  const s = lastFrameLabel(frame({ local_clock: "" }));
  assert(/not live/i.test(s), `"${s}"`);
  assert(!/undefined|NaN|:/.test(s.replace(/[^\w:]/g, "")), `"${s}" leaked a broken time`);
});

// ------------------------------------------------------------------ the fidelity
// A stage tall enough that the WIDTH is the binding constraint in these cases —
// the height bound is exercised on its own below.
const TALL = 100_000;

test("the stand-in is sized to the stage in DEVICE pixels", () => {
  // The owner's ruling: the gallery tile's 256px is the SCANNING size and says
  // nothing about a capture preview. A HiDPI stage 640 CSS px wide is 1664 real
  // pixels, and asking for 640 would put a blurred frame where focus is judged.
  const w = placeholderViewWidth(640, TALL, 2.6);
  assert(w >= Math.ceil(640 * 2.6),
    `asked for ${w} device px for a stage showing ${Math.ceil(640 * 2.6)} — upscaled`);
  assert((VIEW_WIDTH_STEPS as readonly number[]).includes(w),
    `${w} is not a rung the server renders and caches`);
  assert(w > 256, "the scanning-grid width leaked into a capture preview");
});

test("a wider stage asks for more", () => {
  const narrow = placeholderViewWidth(400, TALL, 1);
  const wide = placeholderViewWidth(1600, TALL, 1);
  assert(wide > narrow, `${wide} vs ${narrow} — the size is not following the stage`);
});

test("an unmeasured stage still asks for a real picture", () => {
  const w = placeholderViewWidth(0, 0, 1);
  assert((VIEW_WIDTH_STEPS as readonly number[]).includes(w), `${w} is not a rung`);
  assert(w >= 640, `${w} — a pre-layout stage fell back to a thumbnail`);
});

test("a nonsense devicePixelRatio cannot shrink the request", () => {
  assert(placeholderViewWidth(1280, TALL, 0) === placeholderViewWidth(1280, TALL, 1),
    "dpr 0 was multiplied in");
  assert(placeholderViewWidth(1280, TALL, NaN) === placeholderViewWidth(1280, TALL, 1),
    "dpr NaN was multiplied in");
});

// ------------------------------------------------------ the height bound
// The empty stage is `W x 380` with an `object-contain` picture in it, so past
// ~570 CSS px the picture is HEIGHT-limited and a width-only request buys bytes
// nothing can display. Measured before the bound existed: a 1090x380 stage at
// dpr 2 asked for the 2560 rung (603 KB) to paint 1138 device px, which the
// 1280 rung (126 KB) covers exactly.
test("a short, wide stage is not billed for pixels it cannot show", () => {
  const bounded = placeholderViewWidth(1090, 380, 2);
  const unbounded = placeholderViewWidth(1090, TALL, 2);
  // PRECONDITION: the width alone really would have asked for more.
  assert(unbounded > bounded,
    `the height bound changed nothing (${bounded} either way) — the case is not under test`);
  assert(bounded >= Math.ceil(380 * 1.5 * 2),
    `${bounded} is under the ${Math.ceil(380 * 1.5 * 2)} device px a 3:2 frame ` +
    "actually occupies in that box — the bound is cutting into the picture");
});

test("the bound assumes a frame wider than any sensor, so it cannot under-ask", () => {
  // Every real sub is between 1:1 and 3:2. For each, the device width the
  // browser will paint in a 1090x380 box must be <= what we asked for.
  for (const aspect of [1.0, 1.33, 1.497, 1.5]) {
    for (const dpr of [1, 2, 3]) {
      const asked = placeholderViewWidth(1090, 380, dpr);
      const painted = Math.ceil(Math.min(1090, 380 * aspect) * dpr);
      assert(asked >= painted,
        `a ${aspect}:1 frame at dpr ${dpr} paints ${painted} device px but only ` +
        `${asked} were requested — the stand-in would be upscaled`);
    }
  }
});

test("a stage with no measured height falls back to the width alone", () => {
  // Before first layout there is no honest height to bound by, and guessing one
  // is how you ask for fewer pixels than the screen will show.
  assert(placeholderViewWidth(1090, 0, 2) === placeholderViewWidth(1090, TALL, 2),
    "an unmeasured height was treated as a small one");
  assert(placeholderViewWidth(1090, NaN, 2) === placeholderViewWidth(1090, TALL, 2),
    "a NaN height was multiplied in");
});

const total = passed + failed;
console.log(`lastSessionFrame: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
