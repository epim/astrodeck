// quickModel.ts - the quick-session sheet's arithmetic, with no DOM in it
// (hub-sky plan D.2-D.4, T-SKY-3).
//
// THE ONE DECISION THIS FILE MAKES, and why it is not the README's.
//
// The design says "Time splits evenly across the checked filters", and the
// README's own formula is `per = hours*3600 / checked; need_f = floor(per /
// exp_f)` - a DIFFERENT count per filter. The server cannot be told that.
// `POST /api/flows/quick` takes ONE integer, `subs`, and `wizard.quick` passes
// it as `subs_per_filter`; the FILTER CYCLE node it lands in has a single
// `cycles` number and a `plan` string of "<name> <seconds>" pairs, so per-filter
// counts are not expressible in the node at all.
//
// So the sheet asks for PASSES: how many complete round-trips of the checked
// slots fit inside the chosen window.
//
//     passes = floor(hours * 3600 / sum(exposure of every checked filter))
//
// Every checked filter then shows the same count (one sub per pass) and its own
// total (passes x its exposure), which is exactly what the engine will do. The
// even split would have shown four different counts on screen and then sent one
// number the engine applies to all of them - a screen and an engine describing
// different nights, which is the defect class this repo already has a name for.
// Stated as a deviation in the task report.
//
// `allocateOsc` from `next/lib/allocation.ts` is still the one-channel branch's
// arithmetic, and `passesFor` reduces to it exactly when there is one filter -
// pinned by a test, so the two halves of the feature cannot drift.

import { allocateOsc } from "../../../lib/allocation";
import { fmtClock } from "../../../lib/format";
import { defaultExposureFor, resolveWheel } from "../../../../components/flows/cyclePlanRows";

/** The five sub lengths the exposure button cycles (proto logic.js:13). */
export const EXPOSURES: readonly number[] = [30, 60, 120, 180, 300];

/** Next value in the cycle; an unrecognised current value restarts at 30 s. */
export function nextExposure(current: number): number {
  const i = EXPOSURES.indexOf(current);
  return EXPOSURES[(i + 1) % EXPOSURES.length];
}

/** What the sheet calls the single channel of a rig with no wheel. A LABEL, not
 *  a slot: the payload's `filters` stays empty and the server builds a capture
 *  loop with no filter name, because an invented name is what would land in the
 *  FITS header. Same constant as `QuickFlow.OSC_LABEL`. */
export const OSC_LABEL = "OSC";

export interface WheelSlot {
  /** The wheel's own name for the slot, rendered verbatim and never typed. */
  name: string;
  /** Seconds. */
  exposure: number;
  narrowband: boolean;
  checked: boolean;
}

export interface WheelStatusLike {
  names?: string[];
  narrowband?: boolean[];
  opaque?: boolean[];
  exposures?: (number | null)[];
}

export interface WheelModel {
  slots: WheelSlot[];
  /** False when these names are the assumed seven rather than this rig's. */
  fromRig: boolean;
  /** One usable slot, or no wheel at all: the sheet shows one EXPOSURE row. */
  oneChannel: boolean;
}

/**
 * The rows, from the wheel the rig reports.
 *
 * `resolveWheel` is imported rather than reimplemented: it is where "a blackout
 * slot holds no glass" and "a slot called `Slot 3` is not a filter name" already
 * live, and a second copy of that rule is how a dark slot ends up in a cycle.
 *
 * `on` and `exp` are the persisted per-phone defaults (`astrodeck-next-sky-quick`).
 * A slot the operator has never touched is CHECKED and takes the wheel's own
 * pinned exposure, then `defaultExposureFor` - 180 s for narrowband, 60 s
 * otherwise - so a fresh sheet describes the rig rather than a guess.
 */
export function wheelModel(
  wheel: WheelStatusLike | null | undefined,
  on: Record<string, boolean>,
  exp: Record<string, number>,
): WheelModel {
  const { filters, fromRig } = resolveWheel(wheel?.names, wheel?.opaque);
  // Index back into the rig's own arrays by NAME, not by position: `filters`
  // has had the opaque and unnamed slots removed, so slot 4 of the usable list
  // is not slot 4 of the carousel and reading `exposures[4]` would hand a
  // narrowband slot a broadband default.
  const indexOf = (name: string): number =>
    fromRig ? (wheel?.names ?? []).findIndex((n) => n === name) : -1;

  const slots = filters.map((name) => {
    const i = indexOf(name);
    const flag = i >= 0 ? wheel?.narrowband?.[i] : undefined;
    const pinned = i >= 0 ? wheel?.exposures?.[i] : undefined;
    const fallback =
      typeof pinned === "number" && pinned > 0 ? Math.round(pinned) : defaultExposureFor(name);
    return {
      name,
      exposure: typeof exp[name] === "number" && exp[name] > 0 ? exp[name] : fallback,
      narrowband: typeof flag === "boolean" ? flag : defaultExposureFor(name) === 180,
      checked: typeof on[name] === "boolean" ? on[name] : true,
    };
  });
  return { slots, fromRig, oneChannel: !fromRig || slots.length <= 1 };
}

/**
 * Complete passes of the checked slots that fit in `hours`.
 *
 * Zero when nothing is checked, when the window is empty, or when one pass is
 * longer than the whole window - and zero is an ANSWER there, not a bug: it is
 * the sheet saying this window cannot hold a balanced set at these sub lengths.
 * The CTA reads it and refuses rather than posting `subs: 0`, which the server
 * would 422 (`Field(ge=1)`).
 */
export function passesFor(hours: number, slots: readonly WheelSlot[]): number {
  if (!(hours > 0)) return 0;
  let perPass = 0;
  for (const s of slots) if (s.checked && s.exposure > 0) perPass += s.exposure;
  if (perPass <= 0) return 0;
  return Math.floor((hours * 3600) / perPass);
}

export interface QuickRow {
  name: string;
  exposure: number;
  narrowband: boolean;
  checked: boolean;
  /** Subs of THIS filter: one per pass, so the same number on every row. */
  count: number;
  /** Seconds banked in this channel. */
  totalS: number;
}

/** One row per wheel slot, checked or not. Unchecked rows keep their exposure
 *  (the operator's choice survives a tick-off) and show no count. */
export function quickRows(hours: number, slots: readonly WheelSlot[]): QuickRow[] {
  const passes = passesFor(hours, slots);
  return slots.map((s) => ({
    name: s.name,
    exposure: s.exposure,
    narrowband: s.narrowband,
    checked: s.checked,
    count: s.checked ? passes : 0,
    totalS: s.checked ? passes * s.exposure : 0,
  }));
}

/** The one-channel branch. Identical arithmetic to `passesFor` over a single
 *  slot; routed through `allocateOsc` so the shared lib stays the source. */
export function oscCount(hours: number, exposureS: number): number {
  return allocateOsc(hours, exposureS).count;
}

/** The wheel colour for a filter name, as a CSS token with a literal fallback.
 *  Unknown names get the neutral line colour rather than a guessed hue - a
 *  wheel whose SII slot is called `S2` must not be painted as if it were L. */
export function filterColor(name: string): string {
  const key = name.trim().toUpperCase();
  const token = key === "L" ? "L"
    : key === "R" ? "R"
    : key === "G" ? "G"
    : key === "B" ? "B"
    : key.startsWith("HA") || key.startsWith("H-A") ? "Ha"
    : key.startsWith("OIII") || key === "O3" ? "OIII"
    : key.startsWith("SII") || key === "S2" ? "SII"
    : key === OSC_LABEL ? "OSC"
    : "";
  return token === "" ? "rgba(140,160,220,.45)" : `var(--nx-filter-${token})`;
}

// ------------------------------------------------------------- the window

/** "6h 00m", or "until dawn" at the last stop.
 *
 *  The proto's own `hrs()`/`fmt()` pair (logic.js:62, :245) rather than
 *  `next/lib/format.ts`'s `fmtDuration`: the design's own capture reads
 *  "GENERATE FLOW · 4 filters · 6h 00m", and `fmtDuration` renders that as
 *  "6h 0m". A zero-padded minute is what the screenshot pins. */
export function hoursLabel(hours: number, dawnHours: number | null): string {
  if (dawnHours != null && dawnHours > 0 && hours >= dawnHours - 0.05) return "until dawn";
  const min = Math.max(0, Math.round(hours * 60));
  const h = Math.floor(min / 60);
  const m = min % 60;
  return h > 0 ? `${h}h ${String(m).padStart(2, "0")}m` : `${m} min`;
}

/** The five stops the HOW LONG handle snaps to (proto logic.js:363). The last
 *  is dawn, so it moves with the night rather than being a sixth constant. */
export function hourStops(dawnHours: number | null): number[] {
  const stops = [1, 2, 3, 4];
  if (dawnHours != null && dawnHours > 0) {
    const kept = stops.filter((h) => h < dawnHours - 0.05);
    return [...kept, dawnHours];
  }
  return stops;
}

/** Snap a dragged duration to the nearest stop, so a drag and a tap agree. */
export function snapHours(hours: number, dawnHours: number | null): number {
  const stops = hourStops(dawnHours);
  let best = stops[0];
  let gap = Infinity;
  for (const s of stops) {
    const d = Math.abs(s - hours);
    if (d < gap) { gap = d; best = s; }
  }
  return best;
}

/** "04:14" - when the window closes, on the clock the operator is reading.
 *
 *  `fmtClock`'s day-delta suffix is deliberately suppressed (the instant is
 *  passed as its own "now"): every session worth setting up runs past midnight,
 *  so "(+1d)" would be printed on all of them and would say nothing. */
export function finishLabel(startMs: number, hours: number): string {
  const at = startMs + hours * 3600 * 1000;
  return fmtClock(at, at);
}

// ------------------------------------------------------------- the CTA

export interface PlanLineInput {
  oneChannel: boolean;
  /** Checked slots (mono) - the count is what the label says. */
  checkedCount: number;
  oscExposure: number;
  oscCount: number;
  hoursLabel: string;
  poolCount: number;
  panels: number;
}

/**
 * The CTA's tail: what pressing it will queue.
 *
 * With nothing ticked it says `pick a filter`, and the button is separately
 * honest-locked - the label alone would be a button that looks ready.
 */
export function planLine(inp: PlanLineInput): string {
  const head = inp.oneChannel
    ? `${inp.oscExposure}s × ${inp.oscCount} · `
    : inp.checkedCount > 0
      ? `${inp.checkedCount} filters · `
      : "pick a filter · ";
  const tail = inp.poolCount > 1
    ? ` · ${inp.poolCount} targets`
    : inp.panels > 1
      ? ` · ${inp.panels} panels`
      : "";
  return head + inp.hoursLabel + tail;
}
