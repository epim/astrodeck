// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// rafPolyfill.ts -- the one requestAnimationFrame/cancelAnimationFrame shim
// every DOM-mounting UI test file needs.
//
// Before this file, 27 test files each hand-copied
// `g.requestAnimationFrame = (cb) => setTimeout(() => cb(0), 0)`, with no way
// for the shim to tell that the file's own root is gone (#652: "The
// unguarded rAF polyfill #614 suspected is copy-pasted into 14 more UI test
// files"). #614 suspected that shape of an intermittent "7/7 passed, then the
// child timed out at 60 s" on a loaded CI runner, and the suspicion is NOT
// confirmed: rAF was never seen to be called on flowInspectorNotes' render
// path at all. It also cannot be the mechanism, whatever a file does. Each
// child in run-tests.mjs (runOne) ends in `process.exit(...)`, and
// `process.exit` ends the process regardless of any timer, handle or frame
// still live, so a live setTimeout chain has nothing to hold the child past
// its tally. #664's instrumentation says the same from the other side: the
// children that froze wrote no heartbeat for the whole 60 s, which means the
// main thread was not running, and no pending timer does that. The cause is
// still unknown; the child now writes phase markers (run-tests.mjs,
// `timeoutPhase`) so the next freeze names where it stopped.
//
// What this guard IS for, so it is not mistaken for that fix: once a file's
// root has unmounted, a frame requested after that point should not fire a
// callback into a torn-down tree (a later case in the same file, or the
// teardown of the fake globals). That is ordinary test hygiene, it costs
// nothing, and it is pinned by its own late-frame regression test, which is
// still true. It does not, and is not claimed to, prevent the post-tally
// timeout.
//
// installAutoRaf gives every file that guard for free: once `suppress()` is
// called (right after that file's own `root.unmount()`), a newly requested
// frame returns 0 and schedules nothing. flowInspectorNotes pinned the
// behaviour with its own late-frame regression test, which still lives beside
// that file's other cases and now calls into this module instead of rolling
// its own `framesSuppressed` flag.
//
// Three files need something other than "fire automatically on a 0
// timestamp":
//   * holdButtonDom and w5PolarEasedScaleFinite drive frames BY HAND on a
//     controlled clock -- jsdom's own rAF is a 16ms timer, which cannot
//     express "no frames ran for a second and then one did", or hand a tween
//     a timestamp the TEST chose rather than whatever jsdom's clock
//     disagrees with performance.now() about. createManualRaf gives them a
//     queue to read, splice or reset exactly as their former inline copies
//     did.
//   * cameraDialDom and polarReticleDom need a REAL timestamp instead of a
//     frozen 0, because their tween computes elapsed time against a
//     performance.now() start and a frozen 0 makes progress permanently
//     negative, so the rAF chain never terminates. installAutoRaf's `now`
//     option covers that without a second code path.

export type FrameCallback = (t: number) => void;

export interface AutoRafOptions {
  /** Timestamp handed to a fired callback. Defaults to a constant 0, the
   *  shape every plain consumer of this module used before this fix. Pass
   *  something like `() => performance.now()` for a tween that reads real
   *  elapsed time against a performance.now() start. */
  now?: () => number;
}

export interface AutoRafHandle {
  /** Stops scheduling new frames. Call this right after a file's own root
   *  unmounts: a frame requested AFTER that point must not go on firing
   *  into a tree that is gone. (Hygiene, not the cure for #614 or #664: see
   *  the header.) Once called, requestAnimationFrame returns 0 and schedules
   *  nothing. */
  suppress(): void;
}

/** Installs a setTimeout(0)-backed requestAnimationFrame/cancelAnimationFrame
 *  pair on `target` (default globalThis), guarded against firing after
 *  `suppress()`. This is the shape every file that just wants a render to
 *  settle needs -- it does not drive frames by hand. */
export function installAutoRaf(
  target: any = globalThis,
  options: AutoRafOptions = {},
): AutoRafHandle {
  const now = options.now ?? (() => 0);
  let suppressed = false;
  target.requestAnimationFrame = (cb: FrameCallback): number =>
    suppressed ? 0 : (setTimeout(() => cb(now()), 0) as unknown as number);
  target.cancelAnimationFrame = (id: number): void => {
    clearTimeout(id as unknown as ReturnType<typeof setTimeout>);
  };
  return {
    suppress() { suppressed = true; },
  };
}

export interface ManualRafOptions {
  /** How cancelAnimationFrame behaves. "delete" (default): deletes the
   *  `pending[id - 1]` slot, leaving a hole so ids already handed out stay
   *  valid (holdButtonDom's semantics: a cancelled press must not shift the
   *  ids of frames still pending). "noop": ignores every call -- for a file
   *  whose own test logic discards stale queue contents between renders
   *  rather than relying on cancel to do it (w5PolarEasedScaleFinite: the
   *  component under test DOES call cancelAnimationFrame on every
   *  re-render, and the test deliberately lets that cancelled id sit in the
   *  queue because nothing there ever reads the queue by id). */
  cancel?: "delete" | "noop";
}

export interface ManualRafHandle {
  /** Pending callbacks, oldest first. Exposed directly (not copied) so a
   *  file's own pump/drain logic can read, filter, splice or replace it
   *  exactly as its former inline copy did -- including reassigning it
   *  wholesale (`rafQueue.pending = []`), which `request` and `cancel` both
   *  honour because they read this property fresh on every call rather than
   *  closing over a fixed array. */
  pending: FrameCallback[];
  /** requestAnimationFrame: pushes `cb` onto `pending` and returns its new
   *  length as the id, so an id-based cancel can address it by
   *  `pending[id - 1]`. */
  request(cb: FrameCallback): number;
  /** cancelAnimationFrame, per `options.cancel` above. */
  cancel(id: number): void;
}

/** A requestAnimationFrame that never fires on its own: callbacks only run
 *  when the test drains `pending` itself. This is what a deterministic-clock
 *  test needs: jsdom's own rAF is a 16ms timer, which cannot express "no
 *  frames ran for a second and then one did", or hand a tween a timestamp
 *  the test chose instead of whatever jsdom's clock disagrees with
 *  performance.now() about. */
export function createManualRaf(options: ManualRafOptions = {}): ManualRafHandle {
  const mode = options.cancel ?? "delete";
  const handle: ManualRafHandle = {
    pending: [],
    request(cb) { return handle.pending.push(cb); },
    cancel(id) { if (mode === "delete") delete handle.pending[id - 1]; },
  };
  return handle;
}
