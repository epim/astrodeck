// mosaicNightSummary.ts — pure reduction of a mosaic's per-panel peak altitudes into
// the few facts the Atlas "Tonight" panel states, INCLUDING the reason for every
// panel that has none.
//
// The defect this exists to close: the mosaic route answered for some panels and
// said nothing at all about the others. The server names the cause on the wire
// now (`transit_alt_error`, catalog/framing.py), but a reason that never reaches
// a screen is the same silence one layer down. Everything here is built so a
// panel without an altitude produces WORDS, never an omission — see
// `missingAltitudeReason`, which invents nothing but refuses to return "".
//
// The counts name WHICH panels (`panelSetText`). "3 of 9 panels never clear 30°"
// told the user a night would be wasted and left them nothing to act on; the
// server has sent row/col on every panel all along, and "row 3" is a grid edge
// they can pull in with the row stepper on the same screen — and the name those
// frames carry in the Plan if they send anyway.
//
// It lives next to the component rather than in lib/ because it is Atlas
// presentation logic (lib/visibility.ts is the night CHART's formatter) and
// because it is pure: no React, no fetch, so the component's one interesting
// decision is testable on its own (mosaicNightSummary.test.ts).

import { fmtAlt } from "../../lib/visibility";

/** The parts of a panel this file reads. `transit_alt_error` is on the wire but
 *  NOT yet in `types.ts` MosaicPanel, so this is where it is declared — the same
 *  shape FocusView uses to read `fit` off the raw focus event until the type
 *  catches up.
 *
 *  It is a STRUCTURAL SUBSET, not an alias-in-waiting: MosaicPanel additionally
 *  requires ra_hours, dec_deg and rotation_deg, which no fixture or caller here
 *  has any use for. MosaicPanel[] is already assignable to PanelNight[] (row and
 *  col are the only required fields), so `types.ts` needs exactly ONE addition —
 *  `transit_alt_error?: string` on MosaicPanel — and nothing here changes when
 *  it lands. Re-exporting PanelNight AS MosaicPanel would drag three required
 *  fields into every test fixture for no reader. */
export interface PanelNight {
  row: number;
  col: number;
  transit_alt?: number;
  transit_alt_error?: string;
}

/** Which panel, in the 1-based row-col identity the rest of the app already
 *  uses: AtlasView's `panelsToTargets` names every Plan target
 *  `"<target> ${row+1}-${col+1}"`, so "2-1" printed here is literally the row
 *  the user will find in the Plan. */
export interface PanelRef {
  row: number;
  col: number;
}

export interface MosaicNightSummary {
  total: number;
  /** Panels carrying a usable altitude. */
  answered: number;
  /** Peak-altitude spread across the answered panels; null when none are. */
  peak: { min: number; max: number } | null;
  /** Answered panels whose PEAK never reaches the horizon limit — i.e. panels
   *  that do not clear it at any point tonight, not panels that are low now. */
  belowLimit: number;
  /** WHICH those are. A count alone names a harm the user cannot act on; with
   *  the coordinates they can pull the grid in by a row, move the centre, or
   *  find those exact rows in the Plan. */
  belowLimitPanels: PanelRef[];
  /** Panels with no altitude at all. `total - answered`. */
  missing: number;
  /** WHICH those are — same reason. */
  missingPanels: PanelRef[];
  /** Grid extent as the payload in hand describes it, so a description can
   *  never inherit a shape from a stepper that moved after the request left. */
  grid: { rows: number; cols: number };
  /** Distinct server-stated causes, first-seen order. Empty iff missing === 0. */
  reasons: string[];
}

/** A panel is answered only by a real, finite number. `undefined` is the "we
 *  could not" case and `null`/NaN would be a non-measurement dressed as one. */
function altOf(p: PanelNight): number | null {
  const v = p.transit_alt;
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

export function summarisePanelNight(
  panels: PanelNight[],
  altLimitDeg: number,
): MosaicNightSummary {
  let answered = 0;
  let min = Infinity;
  let max = -Infinity;
  const reasons: string[] = [];
  const belowLimitPanels: PanelRef[] = [];
  const missingPanels: PanelRef[] = [];
  let rows = 0;
  let cols = 0;

  for (const p of panels) {
    // The grid extent is read off the panels themselves, never off the caller's
    // row/col steppers: those can already have moved on by the time an answer
    // lands, and a description of "row 3" against a grid that is now 2 deep is
    // the same class of defect as a stale altitude under a fresh heading.
    if (p.row + 1 > rows) rows = p.row + 1;
    if (p.col + 1 > cols) cols = p.col + 1;

    const alt = altOf(p);
    if (alt === null) {
      // A panel that lost its altitude AND its reason is the original bug
      // reappearing one layer up, so it gets a stated cause here rather than
      // being quietly folded into the count. We do not know WHY — and saying
      // exactly that is the honest reading of an absent key.
      const why = p.transit_alt_error?.trim() || "no reason was given";
      if (!reasons.includes(why)) reasons.push(why);
      missingPanels.push({ row: p.row, col: p.col });
      continue;
    }
    answered++;
    if (alt < min) min = alt;
    if (alt > max) max = alt;
    if (alt < altLimitDeg) belowLimitPanels.push({ row: p.row, col: p.col });
  }

  return {
    total: panels.length,
    answered,
    peak: answered > 0 ? { min, max } : null,
    belowLimit: belowLimitPanels.length,
    belowLimitPanels,
    missing: panels.length - answered,
    missingPanels,
    grid: { rows, cols },
    reasons,
  };
}

/** How many individual panels a list names before "and N more" takes over. A
 *  judgement, not a measurement: past roughly this many tokens the count is the
 *  part a glance can use and the roll-call is the part it skips. */
const MAX_LISTED = 6;

function joinList(xs: string[]): string {
  if (xs.length <= 1) return xs.join("");
  return `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`;
}

/** Where a set of panels sits in the grid, as words: "row 3", "rows 8, 9 and
 *  10", "panels 1-3, 2-3", "row 4 and panel 3-1". Null for an empty set.
 *
 *  Whole rows collapse because the panels that lose the night are a declination
 *  BAND far more often than a scatter — altitude falls off along dec, and a
 *  10x10 mosaic can put thirty panels under the limit. Thirty row-col tokens is
 *  a wall nobody reads; "rows 8, 9 and 10" is the same fact and is directly
 *  actionable on the row stepper the user is already looking at.
 *
 *  A single-column grid never says "row N" and a single-row grid never says
 *  "column N": the collapse would name the whole mosaic while sounding like a
 *  subset of it. */
export function panelSetText(
  refs: PanelRef[],
  grid: { rows: number; cols: number },
): string | null {
  if (refs.length === 0) return null;
  const key = (r: number, c: number) => `${r},${c}`;
  const present = new Set(refs.map((p) => key(p.row, p.col)));
  const consumed = new Set<string>();
  const parts: string[] = [];

  const fullRows: number[] = [];
  if (grid.cols > 1) {
    for (let r = 0; r < grid.rows; r++) {
      let all = true;
      for (let c = 0; c < grid.cols; c++) {
        if (!present.has(key(r, c))) { all = false; break; }
      }
      if (!all) continue;
      fullRows.push(r);
      for (let c = 0; c < grid.cols; c++) consumed.add(key(r, c));
    }
  }
  // Columns are only considered when no row collapsed. A full row and a full
  // column always intersect, so reporting both would name the shared panel
  // twice and inflate what the user thinks they are losing.
  const fullCols: number[] = [];
  if (grid.rows > 1 && fullRows.length === 0) {
    for (let c = 0; c < grid.cols; c++) {
      let all = true;
      for (let r = 0; r < grid.rows; r++) {
        if (!present.has(key(r, c))) { all = false; break; }
      }
      if (!all) continue;
      fullCols.push(c);
      for (let r = 0; r < grid.rows; r++) consumed.add(key(r, c));
    }
  }

  if (fullRows.length) {
    const ns = fullRows.map((r) => String(r + 1));
    parts.push(fullRows.length === 1 ? `row ${ns[0]}` : `rows ${joinList(ns)}`);
  }
  if (fullCols.length) {
    const ns = fullCols.map((c) => String(c + 1));
    parts.push(fullCols.length === 1 ? `column ${ns[0]}` : `columns ${joinList(ns)}`);
  }

  const rest = refs
    .filter((p) => !consumed.has(key(p.row, p.col)))
    .sort((a, b) => a.row - b.row || a.col - b.col)
    .map((p) => `${p.row + 1}-${p.col + 1}`);
  if (rest.length) {
    const shown = rest.slice(0, MAX_LISTED);
    const more = rest.length - shown.length;
    // Commas only inside the roll-call: "and" is spent joining the GROUPS, and
    // "row 2 and panels 1-2 and 3-2" reads as three coordinate things.
    parts.push(
      `${rest.length === 1 ? "panel" : "panels"} ${shown.join(", ")}` +
        (more > 0 ? ` and ${more} more` : ""),
    );
  }

  return parts.join(" and ");
}

/** "9 panels peak 58°–66°" / "all 9 panels peak 64°" — the spread the centre
 *  curve above cannot show, because it is one point and this is up to a hundred.
 *  Null when nothing was answered (the caller states the reason instead). */
export function peakSpreadText(s: MosaicNightSummary): string | null {
  if (!s.peak) return null;
  const lo = fmtAlt(s.peak.min);
  const hi = fmtAlt(s.peak.max);
  // Rounded to whole degrees by fmtAlt, so a sub-degree spread reads as one
  // value — printing "64°–64°" would imply a measured range that isn't there.
  if (lo === hi) {
    return s.answered === s.total
      ? `All ${s.total} panels peak ${lo} tonight`
      : `${s.answered} of ${s.total} panels peak ${lo} tonight`;
  }
  return s.answered === s.total
    ? `${s.total} panels peak ${lo}–${hi} tonight`
    : `${s.answered} of ${s.total} panels peak ${lo}–${hi} tonight`;
}

/** "3 of 9 panels never clear 30° tonight — row 3." Null when every answered
 *  panel clears the limit, so the line only appears when it is telling the user
 *  something.
 *
 *  The coordinates are the point, not decoration. A bare count told the user a
 *  night would be wasted and gave them nothing to do about it; "row 3" is a
 *  grid edge they can pull in with the row stepper on the same screen, and it
 *  is the name those frames carry in the Plan if they send anyway. The absolute
 *  case names no panels because the answer is all of them. */
export function belowLimitText(
  s: MosaicNightSummary,
  altLimitDeg: number,
): string | null {
  if (s.belowLimit <= 0) return null;
  if (s.belowLimit === s.answered && s.missing === 0) {
    return `No panel of this mosaic clears ${Math.round(altLimitDeg)}° tonight`
      + ` — their whole night is below your horizon limit.`;
  }
  const where = panelSetText(s.belowLimitPanels, s.grid);
  return `${s.belowLimit} of ${s.total} panels never clear ${Math.round(altLimitDeg)}° tonight`
    + `${where ? ` — ${where}` : ""}. Their whole night is below your horizon limit.`;
}

/** The sentence that replaces a blank cell: who is missing an altitude, and the
 *  server's own cause. Null when nothing is missing.
 *
 *  Multiple distinct causes are all real (one panel can hit an OSError while
 *  another gets an unusable value back), so they are joined rather than reduced
 *  to the first — with a count when there are more than two, because a wall of
 *  stack-shaped strings in a tooltip is its own kind of unreadable. */
export function missingAltitudeReason(s: MosaicNightSummary): string | null {
  if (s.missing <= 0) return null;
  let who: string;
  if (s.missing === s.total) {
    who = `No panel of this mosaic has a peak altitude`;
  } else {
    // Which ones, parenthesised so the em dash stays the one boundary between
    // the claim and the server's own words. A partial loss without coordinates
    // makes every panel suspect when only some are.
    const where = panelSetText(s.missingPanels, s.grid);
    who = `${s.missing} of ${s.total} panels have no peak altitude`
      + (where ? ` (${where})` : "");
  }
  const head = s.reasons.slice(0, 2).join("; ");
  const rest = s.reasons.length > 2 ? `; and ${s.reasons.length - 2} more` : "";
  return `${who} — ${head}${rest}`;
}

/** The short label on the read-only chip. The COUNT goes here (it is the part a
 *  glance needs); the cause rides in the chip's reason, which is reachable by
 *  tap, keyboard and screen reader. */
export function missingAltitudeLabel(s: MosaicNightSummary): string {
  return s.missing === s.total
    ? `No peak altitude for any panel`
    : `${s.missing} of ${s.total} panels: no peak altitude`;
}
