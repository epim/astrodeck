// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w22RunBannerRaise.test.ts - the persistent run banner is RAISED on a rising
// edge into every live run state, not only running and holding (#984, WP-201,
// wave 22). The half of #959 that WP-186 left alone: it kept the banner up
// through a hold, and this raises it for the entries it did not cover.
//
//   Run directly:  npx tsx src/__tests__/w22RunBannerRaise.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. The "sequence" handler in store.ts raised the banner only for
// "running" and "holding" on a rising edge from RUN_RISING_FROM; "paused" and
// "aborting" only refreshed a banner that already existed. So a phone or second
// browser opened during a pause, or during an abort's ~210 s wind-down, had
// "paused" / "aborting" as its FIRST frame from the cold default and got no
// banner, and when the pause lifted the next frame was "running" arriving from
// "paused", which is not in RUN_RISING_FROM, so nothing raised it for the rest
// of the run. The probe: handleEvent from the cold default, paused gives
// runBanner null, then running gives null.
//
// WHAT IS ASSERTED, through handleEvent exactly as the WebSocket and the
// snapshot read drive the store:
//   - a cold first frame in each live state raises the banner (plan name and
//     percent carried), and a cold first frame in each state that is not live
//     raises nothing;
//   - the rising edge from EVERY state in RUN_RISING_FROM into EVERY live state
//     raises it (the table is hand-written, so a state added to either side is
//     judged here);
//   - a page opened mid-pause keeps its banner through the resume, and the
//     percent follows the run;
//   - a page opened mid-abort clears the banner when "aborted" lands;
//   - a banner the operator dismissed during a pause stays dismissed through
//     the resume, and a live-to-live step never re-raises it, so widening the
//     raise cannot turn a dismissal into a nag; the next run raises it again.
//
// MUTANTS, each run from a byte backup of store.ts and restored byte-identical
// (md5sum compared). The failing lines are in the WP-201 report.
//   M1 "raise running and holding only"   the unfixed branch: paused and
//                                         aborting refresh an existing banner
//                                         and never raise one

// ------------------------------------------------------------ browser stubs
class MemStorage {
  private m = new Map<string, string>();
  getItem(k: string): string | null { return this.m.has(k) ? (this.m.get(k) as string) : null; }
  setItem(k: string, v: string): void { this.m.set(k, String(v)); }
  removeItem(k: string): void { this.m.delete(k); }
  clear(): void { this.m.clear(); }
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
    contains(_c: string): boolean { return false; },
  };
  const style = {
    setProperty(_k: string, _v: string): void {},
    getPropertyValue(_k: string): string { return ""; },
  };
  g.document = { documentElement: { classList, style } };
}

const { useStore } = await import("../store");
const { runIsLive } = await import("../lib/lastSessionFrame");
import type { SequenceState } from "../types";

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }

type State = SequenceState["state"];

/** One sequence frame, as the WebSocket (or the snapshot read) delivers it. */
function send(state: State, percent: number, plan = "NGC7000 SHO"): void {
  useStore.getState().handleEvent({
    type: "sequence",
    data: {
      state,
      plan_name: plan,
      progress: { frames_done: percent, frames_total: 100, percent, elapsed_s: percent * 10, rejected: 0 },
    } as unknown as Record<string, unknown>,
    ts: 0,
  });
}
/** The store as a browser has it before any frame has arrived, with the engine
 *  last seen in `prev` (the cold default is "idle"). */
function openedAfter(prev: State): void {
  useStore.setState({
    sequence: { state: prev }, runBanner: null, view: "capture", autoMonitor: false,
    selectedPreviewId: null, lastCaptureAtMs: null, lastFramesDone: null,
  } as never);
}
function coldStart(): void { openedAfter("idle"); }
function banner(): { active: boolean; plan_name?: string; percent?: number } | null {
  return useStore.getState().runBanner;
}

/** Whether the rig's engine.running is true in each state. Hand-written on
 *  purpose (see w21RunBannerHold.test.ts): the Record forces a state added to
 *  SequenceState to be classified here, and `tsc -b` fails until it is. */
const IS_LIVE: Record<State, boolean> = {
  running: true, paused: true, holding: true, aborting: true,
  idle: false, complete: false, aborted: false, error: false, nina_native: false,
};
const LIVE = (Object.keys(IS_LIVE) as State[]).filter((s) => IS_LIVE[s]);
const NOT_LIVE = (Object.keys(IS_LIVE) as State[]).filter((s) => !IS_LIVE[s]);

// ------------------------------------------- a cold first frame, every state
for (const state of LIVE) {
  test(`a page opened during "${state}" (first frame, from the cold default) raises the banner`, () => {
    coldStart();
    send(state, 60);
    assert(banner()?.active === true,
      `the first frame is "${state}" and the run is live on the rig, but the banner is ${JSON.stringify(banner())}`);
    assert(banner()?.plan_name === "NGC7000 SHO", `the banner has no plan name: ${JSON.stringify(banner())}`);
    assert(banner()?.percent === 60, `the banner has the wrong percent: ${JSON.stringify(banner())}`);
    assert(runIsLive({ state }) === true,
      `this loop and runIsLive disagree about "${state}": the banner's raise and the rest of the app would split`);
  });
}

for (const state of NOT_LIVE) {
  test(`a page opened during "${state}" raises nothing`, () => {
    coldStart();
    send(state, 60);
    assert(banner() === null, `"${state}" is not a live run and the first frame raised ${JSON.stringify(banner())}`);
  });
}

// ------------------------------------- the rising edge, from every state it exists in
// RUN_RISING_FROM in store.ts: the states the engine sits in when NOT running.
const RISING_FROM: State[] = ["idle", "complete", "aborted", "error", "nina_native"];
for (const next of LIVE) {
  test(`the edge from each of ${RISING_FROM.join(", ")} into "${next}" raises the banner`, () => {
    for (const prev of RISING_FROM) {
      openedAfter(prev);
      send(next, 33);
      assert(banner()?.active === true,
        `"${prev}" -> "${next}" is a run starting (or being joined) and the banner is ${JSON.stringify(banner())}`);
    }
  });
}

// --------------------------------------------------------- paused, then resumed
test("a page opened during a pause keeps its banner through the resume, and the percent follows the run", () => {
  coldStart();
  send("paused", 41);
  assert(banner()?.active === true, "PRECONDITION: the paused first frame did not raise the banner");
  send("running", 42);
  assert(banner()?.active === true, "the resume took the banner away: it is the next frame, from \"paused\"");
  assert(banner()?.percent === 42, `the banner is stale after the resume: ${JSON.stringify(banner())}`);
  send("paused", 43);
  send("running", 44);
  assert(banner()?.percent === 44, `a second pause and resume lost the banner: ${JSON.stringify(banner())}`);
});

test("a page opened during a pause that ends in an abort clears the banner when the run ends", () => {
  coldStart();
  send("paused", 45);
  send("aborting", 45);
  assert(banner()?.active === true, "the wind-down of a run joined mid-pause has no banner");
  send("aborted", 45);
  assert(banner() === null, `the banner outlived the run: ${JSON.stringify(banner())}`);
});

// ---------------------------------------------------------------- aborting
test("a page opened during the abort wind-down raises the banner and clears it when \"aborted\" lands", () => {
  coldStart();
  send("aborting", 77);
  assert(banner()?.active === true,
    "a browser that connects during the wind-down never gets a banner: its first frame is \"aborting\"");
  send("aborting", 77);
  assert(banner()?.active === true, "the banner went in the middle of the wind-down");
  send("aborted", 77);
  assert(banner() === null, `the banner outlived the wind-down: ${JSON.stringify(banner())}`);
});

// ---------------------------------------------------------------- dismissal
test("a banner dismissed during a pause stays dismissed through the resume, and the next run raises it again", () => {
  coldStart();
  send("paused", 50);
  assert(banner()?.active === true, "PRECONDITION: the paused first frame did not raise the banner");
  useStore.getState().dismissRunBanner();
  assert(banner() === null, "PRECONDITION: dismiss did not clear the banner");
  send("paused", 51);
  assert(banner() === null, "another paused frame re-raised a banner the operator had dismissed");
  send("running", 52);
  assert(banner() === null, "the resume re-raised a banner the operator had dismissed");
  send("complete", 100);
  send("running", 5, "Next night");
  assert(banner()?.active === true && banner()?.plan_name === "Next night",
    `the next run did not raise its banner: ${JSON.stringify(banner())}`);
});

test("a live-to-live step never re-raises a dismissed banner: running, paused, holding, aborting", () => {
  coldStart();
  send("running", 10);
  useStore.getState().dismissRunBanner();
  for (const state of ["paused", "running", "holding", "paused", "aborting"] as const) {
    send(state, 11);
    assert(banner() === null, `"${state}" re-raised a banner the operator had dismissed: ${JSON.stringify(banner())}`);
  }
  send("aborted", 11);
  assert(banner() === null, "the end of the run left a banner");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nw22RunBannerRaise: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
