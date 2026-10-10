// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w21RunBannerHold.test.ts - the persistent run banner survives a cloud hold
// (#959, WP-186, wave 21). The store half; the screens that print it are in
// w21RunBannerHoldDom.test.tsx.
//
//   Run directly:  npx tsx src/__tests__/w21RunBannerHold.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. The "sequence" handler in store.ts kept the run banner through
// "running", "paused" and "aborting" and sent every other string into the
// clear. "holding" is a live run (sequence/engine.py promotes a routine
// `running` publish to "holding" for as long as a cloud hold is up, so a night
// of cloud is hours of it), and it fell into the clear by omission. When the
// hold lifted the engine published "running" again, but the rising-edge rule
// only raises from idle / complete / aborted / error / nina_native, and
// "holding" is not one of them: the banner was gone for the rest of the run.
// The probe that found it sent running, holding, running, paused, running,
// aborting, aborted and printed the banner as ACTIVE, null, null, null, null,
// null, null.
//
// WHAT IS ASSERTED. The store is driven through handleEvent exactly as the
// WebSocket and the snapshot read drive it:
//   - the issue's own sequence, banner active at EVERY live step and gone at
//     the end;
//   - the whole SequenceState union from a raised banner, banner kept iff the
//     rig's engine.running is true in that state (a hand-written table, so a
//     change to runIsLive is judged here too);
//   - a page opened mid-hold, whose first frame is "holding" from the cold
//     default;
//   - a banner the operator dismissed stays dismissed through a hold, and the
//     next run raises it again.
//
// MUTANTS, each run from a byte backup of store.ts and restored byte-identical
// (md5sum compared). The failing lines are in the WP-186 report.
//   M1 "clear by omission"   the unfixed branch: running raises, paused and
//                            aborting keep, everything else clears
//   M2 "hold keeps, never raises"   `|| seq.state === "holding"` removed from
//                            the raise condition (the page-opened-mid-hold case)

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
/** The store as a browser has it before any frame has arrived. */
function coldStart(): void {
  useStore.setState({
    sequence: { state: "idle" }, runBanner: null, view: "capture", autoMonitor: false,
    selectedPreviewId: null, lastCaptureAtMs: null, lastFramesDone: null,
  } as never);
}
function banner(): { active: boolean; plan_name?: string; percent?: number } | null {
  return useStore.getState().runBanner;
}

// ------------------------------------------------ the issue's own sequence
test("running -> holding -> running -> paused -> running -> aborting -> aborted: the banner is up at every live step and gone at the end", () => {
  coldStart();
  const steps: Array<[State, number, boolean]> = [
    ["running", 10, true],
    ["holding", 11, true],
    ["running", 12, true],
    ["paused", 13, true],
    ["running", 14, true],
    ["aborting", 15, true],
    ["aborted", 15, false],
  ];
  const seen: string[] = [];
  for (const [state, pct, up] of steps) {
    send(state, pct);
    seen.push(`${state}=${banner() ? "ACTIVE" : "null"}`);
    assert(!!banner()?.active === up,
      `after "${state}" the banner is ${banner() ? "up" : "gone"}, expected ${up ? "up" : "gone"}; `
      + `the sequence so far read ${seen.join(", ")}`);
  }
});

test("the banner is not just up through a hold, it is the SAME run's banner: plan name carried, percent follows", () => {
  coldStart();
  send("running", 20);
  send("holding", 21);
  assert(banner()?.plan_name === "NGC7000 SHO", `the hold lost the plan name: ${JSON.stringify(banner())}`);
  assert(banner()?.percent === 21, `the banner froze at the pre-hold percent: ${JSON.stringify(banner())}`);
  send("holding", 22);
  send("running", 23);
  assert(banner()?.percent === 23,
    `the banner did not follow the run out of the hold: ${JSON.stringify(banner())}`);
  assert(useStore.getState().sequence.state === "running", "the frame never reached the sequence slice");
});

test("a long hold: many holding frames in a row keep the banner", () => {
  coldStart();
  send("running", 30);
  for (let i = 0; i < 40; i++) send("holding", 31);
  assert(!!banner()?.active, "the banner went in the middle of a long hold");
  send("running", 32);
  assert(!!banner()?.active, "the banner did not survive the end of the hold");
});

// ----------------------------------------- the whole union, from a raised banner
/** Whether the rig's engine.running is true in each state. Hand-written on
 *  purpose (see w18LiveRunPredicate.test.ts): the Record forces a state added
 *  to SequenceState to be classified here, and `tsc -b` fails until it is. */
const IS_LIVE: Record<State, boolean> = {
  running: true, paused: true, holding: true, aborting: true,
  idle: false, complete: false, aborted: false, error: false, nina_native: false,
};
for (const [state, live] of Object.entries(IS_LIVE) as Array<[State, boolean]>) {
  test(`from a raised banner, "${state}" ${live ? "keeps" : "clears"} it`, () => {
    coldStart();
    send("running", 40);
    assert(!!banner()?.active, "PRECONDITION: the rising edge did not raise the banner");
    send(state, 41);
    assert(!!banner()?.active === live,
      live
        ? `"${state}" is a live run on the rig and the banner is gone`
        : `"${state}" is not a live run and the banner is still up`);
    assert(runIsLive({ state }) === live,
      `this table and runIsLive disagree about "${state}": the banner's clear and the rest of the app would split`);
  });
}

for (const state of ["holding", "paused", "aborting"] as const) {
  test(`a banner that survived "${state}" is still the one that comes back to "running"`, () => {
    coldStart();
    send("running", 50);
    send(state, 51);
    send("running", 52);
    assert(!!banner()?.active, `the banner did not come back from "${state}"`);
    assert(banner()?.percent === 52, `the banner's percent is stale after "${state}": ${JSON.stringify(banner())}`);
  });
}

for (const end of ["complete", "aborted", "error", "idle"] as const) {
  test(`a hold that ends in "${end}" clears the banner`, () => {
    coldStart();
    send("running", 60);
    send("holding", 61);
    send(end, 61);
    assert(banner() === null, `the banner outlived the run: "${end}" after a hold left ${JSON.stringify(banner())}`);
  });
}

// ------------------------------------------------ a page opened mid-hold
test("a page opened mid-hold (first frame is \"holding\", from the cold default) raises the banner", () => {
  coldStart();
  send("holding", 70);
  assert(!!banner()?.active,
    "a browser that connects during a hold never gets a banner: its first frame is \"holding\"");
  assert(banner()?.plan_name === "NGC7000 SHO", `the banner has no plan name: ${JSON.stringify(banner())}`);
  send("running", 71);
  assert(!!banner()?.active, "the banner raised mid-hold went when the hold lifted");
});

// ---------------------------------------------------------- dismissal
test("a banner the operator dismissed stays dismissed through a hold, and the next run raises it again", () => {
  coldStart();
  send("running", 80);
  useStore.getState().dismissRunBanner();
  assert(banner() === null, "PRECONDITION: dismiss did not clear the banner");
  send("holding", 81);
  assert(banner() === null, "a hold re-raised a banner the operator had dismissed");
  send("running", 82);
  assert(banner() === null, "the end of a hold re-raised a banner the operator had dismissed");
  send("complete", 100);
  send("running", 5, "Next night");
  assert(banner()?.active === true && banner()?.plan_name === "Next night",
    `the next run did not raise its banner: ${JSON.stringify(banner())}`);
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nw21RunBannerHold: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
