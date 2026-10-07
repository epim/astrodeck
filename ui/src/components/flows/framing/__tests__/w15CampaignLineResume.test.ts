// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w15CampaignLineResume.test.ts - the Target modal's campaign line says what an
// Automatic resume Off flow does at the end of its night (#712, WP-112, backlog
// wave 15; the Off twin of framingSections.test.tsx's resuming case).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/framing/__tests__/w15CampaignLineResume.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE CLAIM NOTHING KEPT. `campaignLine` ended "The session stays armed and
// resumes at the next dusk." for every block that spans more than one night.
// Since #195 a flow whose DUSK WINDOW has Automatic resume Off is disarmed where
// its night ends, so for exactly the flows an operator reads this line on, the
// sentence was false. `compile_plan` writes `resume_across_nights: false` only
// for an explicit Off, and the Tonight answer repeats it (server
// `tonight.resolve_tonight`; server/tests/test_w15_dusk_flats_claim.py holds
// that half), so the line reads it from the answer it is already handed.
//
// WHAT IS GRADED. The Off line says a subsequent night does not resume it by
// itself and to CONTINUE it by hand, and never says it stays armed; the
// resuming line is byte-identical to what framingSections.test.tsx pins; an
// answer with NO such key (an older server) and a non-boolean value read as
// resuming, since only an explicit boolean false is Off; and the viewer, short
// block and no-night nulls hold in the Off case too.
//
// MUTANT "the unconditional clause" (campaignLine's `if (t.resume_across_nights
// === false) { ... }` branch removed, so every block gets "The session stays
// armed and resumes at the next dusk."), run from a byte backup of
// framingApi.ts inside this worktree, sha256 compared on restore and the mutant
// text grepped out, 2026-10-07. Observed verbatim:
//   w15CampaignLineResume.test: 3/4 passed
//   x an Automatic resume Off flow's campaign line says CONTINUE by hand, never that the session stays armed: the Off line
//       expected "this is a campaign: about 2.1 nights of 7.5 h before hops. Automatic resume is off, so a subsequent night does not resume it by itself: CONTINUE it by hand."
//       got      "this is a campaign: about 2.1 nights of 7.5 h before hops. The session stays armed and resumes at the next dusk."

import type { RunReadouts } from "../framingModel";

// framingApi imports lib/api -> lib/base, which reads `window.location` AT
// IMPORT, so the stub is in place before the dynamic import (the same two
// lines framingReadoutsFixture.test.ts sets).
/* eslint-disable @typescript-eslint/no-explicit-any */
(globalThis as any).window = { location: { pathname: "/", origin: "http://local" } };
(globalThis as any).localStorage = { getItem: () => null, setItem() {}, removeItem() {} };

const { campaignLine } = await import("../framingApi");

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg}\n    expected ${JSON.stringify(want)}\n    got      ${JSON.stringify(got)}`);
}

// 16 h of shutter over 7.5 h nights: about 2.1 nights, the number
// framingSections.test.tsx's resuming case pins. Only `total_s` is read.
const R = { total_s: 16 * 3600 } as unknown as RunReadouts;
const NIGHT = { ok: true, night: { dusk_unix: 1000, dawn_unix: 1000 + 7.5 * 3600 } };
const SPANS = "this is a campaign: about 2.1 nights of 7.5 h before hops. ";
const RESUMES = SPANS + "The session stays armed and resumes at the next dusk.";
const OFF = SPANS
  + "Automatic resume is off, so a subsequent night does not resume it by itself: CONTINUE it by hand.";

test("an Automatic resume Off flow's campaign line says CONTINUE by hand, never that the session stays armed", () => {
  eq(campaignLine(R, { ...NIGHT, resume_across_nights: false }, true), OFF, "the Off line");
});

test("a resuming flow's campaign line is what it was", () => {
  eq(campaignLine(R, { ...NIGHT, resume_across_nights: true }, true), RESUMES, "an explicit true");
  eq(campaignLine(R, NIGHT, true), RESUMES, "no key at all (an older server's answer)");
});

test("only an explicit boolean false is Off", () => {
  for (const v of [null, undefined, 0, "", "false", "Off", "no"]) {
    eq(campaignLine(R, { ...NIGHT, resume_across_nights: v }, true), RESUMES,
      `resume_across_nights ${JSON.stringify(v)} reads as resuming`);
  }
});

test("the Off case keeps every null the resuming case has", () => {
  const off = { ...NIGHT, resume_across_nights: false };
  eq(campaignLine(R, off, false), null, "a viewer's campaign line");
  eq(campaignLine({ ...R, total_s: 7 * 3600 } as RunReadouts, off, true), null, "a block that fits one night");
  eq(campaignLine(R, { ok: false, night: null, resume_across_nights: false }, true), null, "an answer that laid out no night");
  eq(campaignLine(null, off, true), null, "no readouts");
});

// ------------------------------------------------------------------ report
const total = passed + failed;
console.log(`w15CampaignLineResume.test: ${passed}/${total} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total };
export default result;
