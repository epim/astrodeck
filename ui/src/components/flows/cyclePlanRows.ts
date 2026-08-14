// cyclePlanRows.ts — the FILTER CYCLE slot table's rows, as pure data.
//
// WHY THIS IS NOT A TEXT BOX. The 2026-08-14 export is explicit: "Plan editor is
// structured, not freetext: one row per filter in the RIG'S WHEEL (from the
// equipment panel - never a hand-typed filter name)".
//
// The reason is a defect this rig has already produced twice. A filter name is
// not a label — it lands in the FITS `FILTER` header, in the saved filename, and
// in the calibration matcher's key. Type `OIII` on a wheel whose slot reads
// `Oiii` and the cycle asks for a filter that does not exist; the run finds out
// at 21:00, and what it does about it is worse than stopping. Rows read off the
// wheel cannot name a filter the wheel does not have.
//
// Splitting the row model out of the component is the same discipline as
// geometry.ts and autoLayout.ts: this file has no DOM and no React, so the
// interesting behaviour (which slots are offered, what a fresh tick exposes for,
// whether wheel order survives a round trip) is testable directly.
import { formatCyclePlan, parseCyclePlan, type CycleSlot } from "./nodeDefs";

/** The wheel this editor falls back to when no filter wheel is connected.
 *
 *  The prototype's own `WHEEL` constant, verbatim. It exists so the editor is
 *  usable on a laptop with no rig attached — building a flow is a planning
 *  activity and the handoff's definition of done requires every example to run
 *  "on the simulator with no hardware".
 *
 *  It is a FALLBACK, never a merge: a connected wheel replaces this list whole.
 *  Offering the union would put rows on screen for filters the operator does not
 *  own, which is the exact failure the structured editor exists to prevent. */
export const FALLBACK_WHEEL: readonly CycleSlot[] = [
  { filter: "L", exposure_s: 60 },
  { filter: "R", exposure_s: 60 },
  { filter: "G", exposure_s: 60 },
  { filter: "B", exposure_s: 60 },
  { filter: "Ha", exposure_s: 180 },
  { filter: "OIII", exposure_s: 180 },
  { filter: "SII", exposure_s: 180 },
];

/** Narrowband filters get 180s by default, broadband 60s (the export's numbers).
 *
 *  Matched case-insensitively and by PREFIX, because the same physical filter is
 *  spelled `Ha`, `HA`, `H-alpha`, `Oiii`, `OIII` and `S2` across wheels — this
 *  rig's own wheel says `Oiii` where the prototype says `OIII`. A default that
 *  only recognised one spelling would quietly hand a narrowband filter the 60s
 *  broadband default, and 60s of Ha is an empty frame. */
const NARROWBAND = ["ha", "h-a", "halpha", "h-alpha", "oiii", "o3", "sii", "s2", "nb"];

/** Single letters that mean narrowband, matched EXACTLY rather than by prefix.
 *
 *  This rig's wheel names its SII slot `S`, which no prefix in the list above
 *  catches — and a test caught exactly that, defaulting it to 60s. The SHO
 *  convention is common enough on wheels to be worth naming, and there is no
 *  broadband filter called S, H or O for it to collide with.
 *
 *  Exact, not prefix, because `s`-as-a-prefix would swallow `Sloan g` and any
 *  other broadband filter whose name happens to start with the letter. */
const NARROWBAND_LETTERS = ["s", "h", "o"];

export function defaultExposureFor(filter: string): number {
  const f = filter.trim().toLowerCase();
  if (NARROWBAND_LETTERS.includes(f)) return 180;
  return NARROWBAND.some((n) => f.startsWith(n)) ? 180 : 60;
}

/** One row of the editor. */
export interface CyclePlanRow {
  /** The wheel's own name for this slot, rendered verbatim. */
  filter: string;
  /** Is this slot part of the cycle? */
  on: boolean;
  /** Seconds, when `on`. Empty string when off — an exposure for a slot nobody
   *  ticked is a number with nothing to spend it on. */
  exposure: string;
}

/** What the editor shows, given the rig's wheel and the stored plan string.
 *
 *  ROW ORDER IS WHEEL ORDER, always, and the stored string's order does not
 *  influence it. The rows are a picture of the rig, so a plan saved on a
 *  different wheel still renders against THIS one, and any slot the plan names
 *  that this wheel lacks simply has no row — it is dropped on the next write
 *  rather than silently kept and shot.
 */
export function cyclePlanRows(
  wheel: readonly string[],
  plan: string | number | undefined,
): CyclePlanRow[] {
  const chosen = parseCyclePlan(plan);
  return wheel.map((filter) => {
    const hit = chosen.find((s) => s.filter === filter);
    return {
      filter,
      on: hit !== undefined,
      exposure: hit ? String(hit.exposure_s) : "",
    };
  });
}

/** The plan string after ticking or unticking `filter`.
 *
 *  A fresh tick gets `defaultExposureFor`, not 0 and not the previous value: a
 *  slot with no exposure is one `to_plan` refuses, and the refusal would arrive
 *  at RUN rather than at the click. */
export function toggleSlot(
  wheel: readonly string[],
  plan: string | number | undefined,
  filter: string,
): string {
  const rows = cyclePlanRows(wheel, plan);
  return writeRows(rows.map((r) => (
    r.filter === filter
      ? { ...r, on: !r.on, exposure: r.on ? "" : String(defaultExposureFor(filter)) }
      : r)));
}

/** The plan string after typing an exposure into `filter`'s box.
 *
 *  Unparseable input leaves the STORED value alone rather than writing 0. The
 *  operator is mid-keystroke — an empty box while clearing "60" to type "180"
 *  must not commit a zero-second slot, and a zero-second slot is a run-time
 *  refusal for a graph that looks fine on screen. */
export function setSlotExposure(
  wheel: readonly string[],
  plan: string | number | undefined,
  filter: string,
  raw: string,
): string {
  const n = parseInt(raw, 10);
  if (!Number.isFinite(n) || n <= 0) return String(plan ?? "");
  return writeRows(cyclePlanRows(wheel, plan).map((r) => (
    r.filter === filter ? { ...r, exposure: String(n) } : r)));
}

function writeRows(rows: readonly CyclePlanRow[]): string {
  return formatCyclePlan(rows
    .filter((r) => r.on && parseInt(r.exposure, 10) > 0)
    .map((r) => ({ filter: r.filter, exposure_s: parseInt(r.exposure, 10) })));
}

/** The wheel a cycle editor should offer, from `status.filterwheel`.
 *
 *  BLACKOUT SLOTS ARE EXCLUDED. An opaque slot passes no light, so a light frame
 *  through it is a black frame with `IMAGETYP=Light` on it — tracker #240 is
 *  eighteen such frames, and #188 already removed opaque slots from the focus
 *  sweep's picker for the same reason. Offering one here would be offering the
 *  operator a way to shoot a whole channel of nothing.
 *
 *  Unnamed slots are excluded too: a filter called "Slot 6" names nothing, and
 *  putting it in a FITS header is how a frame becomes unfilable.
 *
 *  `fromRig` is returned rather than inferred by the caller from a `connected`
 *  flag, because the question the UI has to answer is not "is a wheel attached"
 *  but "are these rows this rig's". A wheel that is attached and reports eight
 *  unnamed slots yields the fallback list, and saying "connected" over it would
 *  be the more confident of the two wrong answers. */
export function resolveWheel(
  names: readonly string[] | undefined,
  opaque: readonly boolean[] | undefined,
): { filters: string[]; fromRig: boolean } {
  const fallback = FALLBACK_WHEEL.map((s) => s.filter);
  if (!names || names.length === 0) return { filters: fallback, fromRig: false };
  const usable = names.filter((n, i) => {
    if (opaque?.[i]) return false;
    const t = (n ?? "").trim();
    return t !== "" && t.toLowerCase() !== `slot ${i + 1}`;
  });
  return usable.length > 0
    ? { filters: usable, fromRig: true }
    : { filters: fallback, fromRig: false };
}
