// quickPayload.ts - the quick flow's payload arithmetic, its coordinate
// formatters and its four strings, owned by `next` (wave R7 section 2.1's
// finding, ruling 6).
//
// WHY THIS IS A COPY AND NOT A `export ... from "../../../../../components/
// flows/QuickFlow"` PASS-THROUGH.
//
// These five helpers are pure, but they live INSIDE a 497-line legacy
// presentation module. Re-exporting them from that module keeps the promise's
// words and loses its point: `sky/sheets/quick.tsx` would still pull
// `QuickFlow.tsx` - and with it `Overlay`, `CatalogSearch`, `HonestButton` and
// the whole Tailwind tree behind them - into the lazily split next bundle,
// which is the cost D-FU-2 spent a wave undoing. Section 2.1 says the legacy
// file KEEPS ITS OWN COPY for `#/classic` and the rebuilding task owns the
// helper; that is what this file is. Nothing under `ui/src/components/**` is
// edited.
//
// The two copies cannot drift silently: `createDom.test.tsx`'s "no drift"
// section imports BOTH and asserts they agree on the same inputs - the same
// guard `quickModel.ts` uses to pin `passesFor` to `allocateOsc`.
//
// BOTH COORDINATES ALWAYS TRAVEL. `to_plan` reads ra/dec and never the name, so
// a quick flow carrying a name and no coordinates slews to the TARGET node's
// shipped M31 and files the frames under the object that was picked.

import type { CatalogEntry } from "../../../../../types";
import type { QuickFlowAnswers } from "../../../../../lib/flowsApi";

/** One exported string per failure, so a button's reason and any test asserting
 *  on it cannot drift apart. */
export const QUICK_SAVE_FAILED = "Could not create the quick flow";
export const QUICK_RUN_FAILED = "The flow was saved, but it did not start";

/** What the sheet calls the single channel of a rig with no filter wheel. It is
 *  a LABEL, not a slot: the payload's `filters` is empty in that case and the
 *  server builds a capture loop with no filter name, because an invented name
 *  is what would land in the FITS header. */
export const OSC_LABEL = "OSC";

/** Subs per filter, before anyone touches it. Ten of each is a night that
 *  produces something at every prefix and finishes inside a few hours at
 *  ordinary sub lengths; it is a starting point, not a recommendation. */
export const DEFAULT_SUBS = 10;

/** RA in hours -> `20h 34m 52s`, the format the TARGET node stores.
 *
 *  Sexagesimal rather than the decimal hours the catalog row carries, because
 *  the string ends up in a node field an operator reads and edits, and
 *  `20.581111` is not a coordinate anybody checks at a glance. The server's
 *  `parse_ra` accepts both; this is the one it renders back. */
export function raHms(hours: number): string {
  const wrapped = ((hours % 24) + 24) % 24;
  let h = Math.floor(wrapped);
  let m = Math.floor((wrapped - h) * 60);
  let s = Math.round(((wrapped - h) * 60 - m) * 60);
  if (s === 60) { s = 0; m += 1; }
  if (m === 60) { m = 0; h = (h + 1) % 24; }
  const p = (n: number): string => String(n).padStart(2, "0");
  return `${p(h)}h ${p(m)}m ${p(s)}s`;
}

/** Dec in degrees -> `+60° 09′ 14″`, the format the TARGET node stores.
 *
 *  THE SIGN IS ALWAYS WRITTEN. A dec is the one coordinate that carries one,
 *  and `41° 16′ 09″` read back as +41 by luck rather than by statement. */
export function decDms(deg: number): string {
  const sign = deg < 0 ? "-" : "+";
  const abs = Math.abs(deg);
  let d = Math.floor(abs);
  let m = Math.floor((abs - d) * 60);
  let s = Math.round(((abs - d) * 60 - m) * 60);
  if (s === 60) { s = 0; m += 1; }
  if (m === 60) { m = 0; d += 1; }
  const p = (n: number): string => String(n).padStart(2, "0");
  return `${sign}${p(d)}° ${p(m)}′ ${p(s)}″`;
}

export interface QuickTarget { name: string; ra: string; dec: string }

/** A catalog row as the TARGET node holds it. */
export function targetFromEntry(e: CatalogEntry): QuickTarget {
  return { name: e.name || e.id, ra: raHms(e.ra_hours), dec: decDms(e.dec_deg) };
}

/** The exact body `POST /api/flows/quick` receives.
 *
 *  Pure and exported so the payload can be graded without a DOM: the failure
 *  worth guarding against here is a handler that posts DEFAULTS - ten subs, all
 *  filters, guided - which returns a perfectly valid 200 and the wrong night. */
export function quickPayload(a: {
  target: QuickTarget;
  subs: number;
  /** Ticked slot names. Empty in the one-channel case, and empty is an ANSWER
   *  there, not a missing one. */
  filters: readonly string[];
  /** Keyed by slot name, or by OSC_LABEL when there is no wheel. */
  exposures: Readonly<Record<string, number>>;
  guided: boolean;
  run: boolean;
}): QuickFlowAnswers {
  const keys = a.filters.length > 0 ? a.filters : [OSC_LABEL];
  const exposures: Record<string, number> = {};
  for (const k of keys) {
    const v = a.exposures[k];
    if (Number.isFinite(v) && v > 0) exposures[k] = v;
  }
  return {
    target: a.target,
    subs: a.subs,
    filters: [...a.filters],
    exposures,
    guided: a.guided,
    run: a.run,
  };
}
