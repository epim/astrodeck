// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// guideSettle.ts - which guiders honour the three dither SETTLE boxes (#679).
//
// SETTLE PX, SETTLE S and TIMEOUT S save to `GuideConfig.dither_settle_pixels /
// dither_settle_time_s / dither_settle_timeout_s` and ride every dither
// (`SequenceEngine._dither_settle_override`, and the DITHER NOW press). Only the
// PHD2 bridge acts on all three: `guide/phd2.py::dither` merges them over its own
// SETTLE. The native engine (`guide/native.py::dither`) manages its own settle
// pixel and time criteria inside the Rust guide engine and reads ONE field,
// `settle["timeout"]`, as how long Python waits. So on the native guider (and on
// the simulator, which runs it) two of the three boxes accepted a number, saved
// it, showed it again next visit, and changed nothing. That is a claim nothing
// keeps (#679, the family #560 opened).
//
// The server already says the same thing in one place: `_guider_and_settle`
// (api/app.py) prints the saved override in the Tonight brief for PHD2 only and
// keeps NATIVE_GUIDE_SETTLE for the native guider. This module is the UI's copy
// of that decision, so the sheet and the brief can no longer disagree. The
// constants below are written again here for the same reason app.py's are (the
// wheel exports none of them); w14GuiderSettleNative.test.tsx reads app.py,
// phd2.py and the Rust engine as text and fails if one drifts.
//
// LOCK ONLY WHEN THE PROVIDER IS KNOWN NATIVE. `providers.guide.kind` is
// undefined until the first status frame carrying `providers` lands, and it is
// "unavailable" when the resolver could not answer. Neither is evidence about
// the guider, so neither locks: a box that is editable for one status poll and
// then goes read-only is a smaller defect than a box that is read-only on a
// PHD2 rig whose status has not arrived.

/** `kind` and `label` are `providers.guide`'s own fields (ProviderChoiceView).
 *  Both are optional because the first status frame has not necessarily landed. */
export type GuideKind = string | undefined;

/** PHD2 is the one guider that applies all three boxes. The test is the exact
 *  one `_guider_and_settle` uses (`choice.kind == "backend" and choice.label ==
 *  "PHD2"`). NINA publishes no settle rule, so it is not "honoured" either: its
 *  boxes stay editable (nothing says they are dead) but nothing here promises
 *  they act. */
export function settleHonoured(kind: GuideKind, label: string | undefined): boolean {
  return kind === "backend" && label === "PHD2";
}

/** The native engine and the simulator that runs it. The same test the
 *  Guiding Assistant row uses for "is this the AstroDeck native guider", and
 *  `_guider_and_settle`'s `choice.kind in ("astrodeck", "sim")`. */
export function settleIgnoredByGuider(kind: GuideKind): boolean {
  return kind === "astrodeck" || kind === "sim";
}

/** What each guider settles on when a box is blank, in the boxes' own units.
 *  `native` is the Rust engine's DEFAULT_SETTLE_TOL_PX / DEFAULT_SETTLE_TIME_S /
 *  DEFAULT_SETTLE_TIMEOUT_S (native/crates/astro-guide/src/engine.rs;
 *  app.py's NATIVE_GUIDE_SETTLE is the first two). `phd2` is
 *  `guide.phd2.SETTLE`. TIMEOUT is 60 on both, but for different reasons: PHD2
 *  waits `timeout + 30`, while the native engine ENDS the settle window at 60 s
 *  itself, so a larger TIMEOUT S changes nothing there and a smaller one only
 *  shortens the wait. */
export const SETTLE_DEFAULTS = {
  native: { px: 1.5, s: 10, timeoutS: 60 },
  phd2: { px: 1.5, s: 8, timeoutS: 60 },
} as const;

/** The reason on a locked SETTLE PX / SETTLE S box (the lock's `title`). */
export const NATIVE_SETTLE_REASON =
  `The native guider settles on its own rule (${SETTLE_DEFAULTS.native.px} px `
  + `held for ${SETTLE_DEFAULTS.native.s} s); only TIMEOUT applies.`;

/** The footer note under the three boxes on the native guider. The PHD2 /
 *  not-yet-known wording lives beside the boxes (it carries a typographic
 *  apostrophe in JSX). */
export const NATIVE_SETTLE_NOTE =
  `Only TIMEOUT S applies on the native guider, and its engine ends any settle at `
  + `${SETTLE_DEFAULTS.native.timeoutS} s, so it can only shorten the wait. The `
  + `settle distance and time are its own. Leave it empty to use the default.`;

/** The three settle fields of a dither request, as typed text. */
export interface SettleText { px: string; s: string; timeoutS: string }

/** `""` and anything that is not a finite number is "no override", never 0: a
 *  blank box means "the guider's own default", and a 0 the guider obeys would
 *  be an instruction. */
export function optNum(v: string): number | undefined {
  const n = Number(v);
  return v.trim() !== "" && Number.isFinite(n) ? n : undefined;
}

/** The settle part of a `POST /api/guide/dither` body. On a guider that ignores
 *  pixels and time they are left OUT, so the request does not carry values
 *  nothing reads; the timeout is the one field the native path honours. Keys
 *  with no value stay `undefined` and never reach the wire (`JSON.stringify`). */
export function ditherSettleBody(
  ignoredByGuider: boolean, t: SettleText,
): { settle_pixels?: number; settle_time_s?: number; settle_timeout_s?: number } {
  return {
    settle_pixels: ignoredByGuider ? undefined : optNum(t.px),
    settle_time_s: ignoredByGuider ? undefined : optNum(t.s),
    settle_timeout_s: optNum(t.timeoutS),
  };
}
