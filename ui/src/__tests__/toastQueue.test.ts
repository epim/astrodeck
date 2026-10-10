// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// toastQueue.test.ts — UX review #33 regression ("two identical toasts
// sometimes, zero other times").
//
// The safety UNSAFE notice is enqueued sticky (ttl: 0) so a later transient
// toast can't push it off. Two bugs in enqueueToast made that queue behave
// exactly as the reviewer described, and neither was visible from the server:
//
//   two   — the duplicate check only coalesced within a flat 5s of createdAt.
//           A sticky toast never ages out of the screen, so an identical re-trip
//           at +6s failed the age test and enqueued a SECOND identical card.
//   zero  — overflow eviction picked `find(t => t.ttl > 0 && t.kind==='generic')`
//           and fell back to next[0] — the OLDEST — when nothing matched. With
//           the queue full of sticky notices the fallback fired and threw away
//           the UNSAFE toast itself, which is why a DOM query for "UNSAFE:"
//           returned [] on a trip whose banner and pause fired correctly.
//
// Same inline-assert harness + browser stubs as store.test.ts (the store reads
// localStorage/document/window at import time):
//     npx tsx src/__tests__/toastQueue.test.ts

// ------------------------------------------------------------ browser stubs
class MemStorage {
  private m = new Map<string, string>();
  getItem(k: string): string | null {
    return this.m.has(k) ? (this.m.get(k) as string) : null;
  }
  setItem(k: string, v: string): void {
    this.m.set(k, String(v));
  }
  removeItem(k: string): void {
    this.m.delete(k);
  }
  clear(): void {
    this.m.clear();
  }
}
const g = globalThis as unknown as {
  localStorage?: Storage;
  document?: unknown;
  window?: unknown;
};
if (typeof g.localStorage === "undefined") {
  g.localStorage = new MemStorage() as unknown as Storage;
}
if (typeof g.window === "undefined") {
  g.window = { location: { pathname: "/", host: "localhost", protocol: "http:" } };
}
if (typeof g.document === "undefined") {
  const classList = {
    toggle(_c: string, _on?: boolean): void {},
    add(_c: string): void {},
    remove(_c: string): void {},
    contains(_c: string): boolean {
      return false;
    },
  };
  const style = {
    setProperty(_k: string, _v: string): void {},
    getPropertyValue(_k: string): string {
      return "";
    },
  };
  g.document = { documentElement: { classList, style } };
}

const { useStore } = await import("../store");
const { CLIP_AT } = await import("../lib/humanize");
import type { Toast } from "../types";

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
    failures.push(`✗ ${name}: ${(e as Error).message}`);
  }
}
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${String(b)}, got ${String(a)}`);
}

const UNSAFE = "UNSAFE: rain detected";
const toasts = (): Toast[] => useStore.getState().toasts;
const reset = (): void => useStore.setState({ toasts: [] });
/** Backdate every queued toast so the next enqueue looks like a LATER re-trip. */
function age(ms: number): void {
  useStore.setState({
    toasts: toasts().map((t) => ({ ...t, createdAt: t.createdAt - ms })),
  });
}
const unsafe = (): void =>
  useStore.getState().enqueueToast({ level: "error", title: UNSAFE, ttl: 0 });

// ====================================================================
// "two identical toasts sometimes"
// ====================================================================

test("#33: an identical STICKY toast coalesces however long ago it was raised", () => {
  reset();
  unsafe();
  age(60_000); // a minute of rain later, the monitor re-publishes the same edge
  unsafe();
  eq(toasts().length, 1, "still exactly one UNSAFE card");
  eq(toasts()[0].count, 2, "the ×N chip carries the repeat, not a second card");
});

test("#33: a timed toast still coalesces while it is on screen, and not after", () => {
  reset();
  const say = () => useStore.getState().enqueueToast({ level: "info", title: "Site saved" });
  say();
  age(2_000);
  say();
  eq(toasts().length, 1, "within the window: coalesced");
  age(60_000); // long gone from the screen
  say();
  eq(toasts().length, 2, "after it expired: a genuinely new toast");
});

test("#33: different reasons stay separate cards (coalescing is per-title)", () => {
  reset();
  unsafe();
  useStore.getState().enqueueToast({ level: "error", title: "UNSAFE: cloud sensor", ttl: 0 });
  eq(toasts().length, 2, "two distinct conditions, two cards");
});

// ====================================================================
// #933: identical means the detail too
// ====================================================================
//
// The coalescing key was title and level, and the merge keeps the FIRST toast's
// detail. A second failure under the same title with a different reason was
// counted as a repeat of the first and the operator read a stale reason for it.

const FLOW_NOT_OPENED = "That flow did not open";
const fail = (detail?: string): void =>
  useStore.getState().enqueueToast({ level: "error", title: FLOW_NOT_OPENED, detail });

test("#933: the same title with a different reason is a second toast carrying it", () => {
  reset();
  fail("no flow named X");
  fail("disk full");
  eq(toasts().length, 2, "the second reason was merged into the first toast");
  eq(toasts()[0].detail, "no flow named X", "the first reason changed:");
  eq(toasts()[1].detail, "disk full", "the operator would read the wrong reason:");
  eq(toasts()[0].count + toasts()[1].count, 2, "one failure each, not a x2 on the first:");
});

test("#933: a repeat of a reason still coalesces, whichever reason came between", () => {
  reset();
  fail("no flow named X");
  fail("disk full");
  fail("no flow named X");
  fail("disk full");
  eq(toasts().length, 2, "a repeated reason raised a new card");
  eq(toasts()[0].count, 2, "the first reason's repeat is not counted on it:");
  eq(toasts()[1].count, 2, "the second reason's repeat is not counted on it:");
});

test("#933: no detail and an empty detail are the same toast", () => {
  reset();
  fail(undefined);
  fail("");
  fail(undefined);
  eq(toasts().length, 1, "nothing to say is not a reason to raise a second card");
  eq(toasts()[0].count, 3, "the repeats are not counted:");
});

test("#933: a toast with a reason and one without are different failures", () => {
  reset();
  fail(undefined);
  fail("disk full");
  eq(toasts().length, 2, "a bare failure swallowed the one that says why");
});

// ====================================================================
// "zero other times"
// ====================================================================

test("#33: overflow never evicts the sticky UNSAFE toast", () => {
  reset();
  unsafe();
  // Fill and overflow the queue with ordinary transient toasts.
  for (const t of ["one", "two", "three", "four", "five"]) {
    useStore.getState().enqueueToast({ level: "info", title: t });
  }
  assert(toasts().some((t) => t.title === UNSAFE), "UNSAFE survived the flood");
  eq(toasts()[0].title, UNSAFE, "and it is still the oldest/top card");
});

test("#33: overflow with a full queue of sticky notices drops a non-error first", () => {
  reset();
  const stick = (level: "warning" | "error", title: string) =>
    useStore.getState().enqueueToast({ level, title, ttl: 0 });
  stick("error", UNSAFE);
  stick("warning", "Dew heater at 100%");
  stick("warning", "Disk 88 GB free");
  stick("warning", "Guiding degraded"); // overflows TOAST_MAX
  assert(toasts().some((t) => t.title === UNSAFE), "the sticky ERROR is kept");
  assert(!toasts().some((t) => t.title === "Dew heater at 100%"),
         "the oldest sticky non-error is what goes");
});

test("#33: the toast being enqueued is never its own eviction victim", () => {
  reset();
  const stick = (title: string) =>
    useStore.getState().enqueueToast({ level: "error", title, ttl: 0 });
  stick("a"); stick("b"); stick("c");     // queue full of sticky errors
  useStore.getState().enqueueToast({ level: "info", title: "just now" });
  assert(toasts().some((t) => t.title === "just now"),
         "the new arrival is on screen, not silently swallowed");
});

test("#33: the focal sequence toast is never evicted", () => {
  reset();
  useStore.getState().enqueueToast({ level: "error", title: "Sequence failed", kind: "sequence", ttl: 0 });
  for (const t of ["one", "two", "three", "four"]) {
    useStore.getState().enqueueToast({ level: "info", title: t });
  }
  assert(toasts().some((t) => t.kind === "sequence"), "sequence toast survives");
});

// ====================================================================
// showToast: a server refusal is not a log line (T-R7-21a item 12)
// ====================================================================
//
// `showToast` routes its message through `humanizeLog`, whose fall-through
// shortens a message past CLIP_AT characters (on a sentence boundary, #792; it
// was a cut at 137 characters wherever that fell). That rule is right for the
// LOG stream, where a raw line can be a stack trace and the whole text is one
// tap away in the drawer. It is wrong for a refusal: those are complete
// sentences, their repair is usually the LAST clause ("... stop the run, or
// clear the protection for this port in Power settings"), and there is no
// drawer behind a toast holding the rest. `power.tsx` already bypassed the
// whole helper to keep its refusal intact; `{ verbatim: true }` is that escape,
// spelled once, without giving up the mapping every caller wants.

/** Longer than CLIP_AT, with the repair LAST. If this ever shrinks under the
 *  budget the assertions below stop proving anything, so its length is asserted
 *  rather than assumed. */
const LONG_REFUSAL =
  "Mount 12V is protected while a run is live: switching it now would cut power to "
  + "something the sequence is using. The run holds the mount, the guide camera and "
  + "the dew heaters on that one port, and an unplanned loss of power during an "
  + "exposure costs the frame in flight and the guiding calibration, which takes "
  + "about ten minutes to rebuild, and the dew heaters take longer than that to "
  + "come back to temperature. Stop the run, or clear the protection for "
  + "this port in Power settings.";

test("the fixture is long enough to be shortened - the vacuity guard", () => {
  assert(LONG_REFUSAL.length > CLIP_AT,
    `the refusal fixture is ${LONG_REFUSAL.length} chars, under humanizeLog's budget of ${CLIP_AT}: `
    + "every assertion below would pass on a helper that shortens nothing");
});

test("showToast still shortens a very long message by default", () => {
  reset();
  useStore.getState().showToast("error", LONG_REFUSAL);
  const title = toasts()[0].title;
  assert(title.length < LONG_REFUSAL.length,
    "the default stopped shortening: every classic log toast just got longer");
  assert(title.endsWith("…"), `the shortened title lost its ellipsis: "${title}"`);
});

test("verbatim keeps the refusal WHOLE, including the second way out", () => {
  reset();
  useStore.getState().showToast("error", LONG_REFUSAL, { verbatim: true });
  eq(toasts()[0].title, LONG_REFUSAL,
    "the refusal was cut, and the clause that goes first is the one naming the "
    + "way out the user is standing in front of:");
  assert(/Power settings\.$/.test(toasts()[0].title),
    "the last clause - the repair - is missing from the toast");
});

test("verbatim does NOT turn off the mapping: a lane conflict is still a sentence", () => {
  reset();
  useStore.getState().showToast("error", "'goto' is already running", { verbatim: true });
  const title = toasts()[0].title;
  assert(!title.includes("'goto'"),
    `a lane id reached the user under verbatim: "${title}"`);
  assert(/already/i.test(title), `the lane sentence was lost: "${title}"`);
});

// ====================================================================
// showToast: honest copy that shares words with a rewrite (#792)
// ====================================================================
//
// `showToast` and the log-line toast both run their text through `humanizeLog`,
// whose rewrites used to fire on two bare words. The rule-by-rule cases are in
// lib/__tests__/humanizeRewrites.test.ts; these two prove the TOAST a person
// reads, on the two paths that reach it.

test("showToast: a sentence that mentions a plate-solve sync is shown, not 'Plate-solve failed'", () => {
  reset();
  const sentence =
    "Steps and nudges are measured from where the mount thinks it points, and it "
    + "does not know. A plate-solve sync, or TRUST POSITION, unlocks them.";
  assert(sentence.length <= CLIP_AT, "the fixture would be shortened by the default, which is not under test");
  useStore.getState().showToast("warning", sentence);
  eq(toasts()[0].title, sentence, "the refusal was replaced by a failure notice:");
});

test("a real solve failure on the log stream still reads as a plate-solve failure", () => {
  reset();
  useStore.getState().handleEvent({
    type: "log", ts: 0,
    data: { level: "error", source: "solve", message: "solve failed: plate solve failed: not enough stars" },
  });
  eq(toasts()[0].title, "Plate-solve failed - check focus/exposure, or solve manually.",
    "the rewrite stopped firing on the report it exists for:");
});

test("a long error log line is never cut in the middle of a sentence", () => {
  reset();
  const cause = `${"The mount did not confirm it was parked and may still be tracking, ".repeat(
    Math.floor((CLIP_AT - 60) / 68))}so the roof may not close.`;
  const repair = "Park the mount by hand from the pad, then close the roof.";
  assert(cause.length <= CLIP_AT && `${cause} ${repair}`.length > CLIP_AT, "fixture is the wrong size");
  useStore.getState().handleEvent({
    type: "log", ts: 0, data: { level: "error", source: "safety", message: `${cause} ${repair}` },
  });
  const title = toasts()[0].title;
  assert(title.startsWith(cause), `the cause was cut: "${title}"`);
  const shown = title.replace(/ …$/, "");
  assert(shown.endsWith(".") && `${cause} ${repair}`.startsWith(shown),
    `the toast stops inside a sentence: "${title}"`);
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\ntoastQueue.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
