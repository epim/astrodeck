// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// horizonSuggestionModel.ts - the words and the numbers of the learned-horizon
// offer on the Site page (WP 3.5, #599, #1093). Pure: no fetch, no React.
import type { HorizonRegion, HorizonSuggestion } from "../../../../api/horizonSuggestion";
import { compassPoint } from "../finder/wind";

/** What the Site page says when no horizon is drawn for the site and none is on
 *  offer: what the operator can do about it (#1093). */
export const NO_HORIZON_GUIDE =
  "No horizon is drawn for this site, so nothing stops a slew into a tree. "
  + "Draw the line above or scan your surroundings. After a night in which the "
  + "solve frames show an obstruction, AstroDeck offers a line here for you to accept.";

/** The line under the offer that says what accepting does and does not do. */
export const ACCEPT_NOTE =
  "Nothing changes until you accept. Accepting raises the site's horizon over "
  + "these directions and never lowers it.";

const plural = (n: number, one: string, many: string): string => `${n} ${n === 1 ? one : many}`;

/** The degree sign, written as its code so this file stays ASCII. */
const DEGREE = String.fromCharCode(176);

const deg = (v: number): string => `${Math.round(v)}${DEGREE}`;

/** One region as a line: where it is and how high the mask rises. */
export function regionLine(r: HorizonRegion): string {
  const mid = r.az_from <= r.az_to ? (r.az_from + r.az_to) / 2 : ((r.az_from + r.az_to + 360) / 2) % 360;
  const height = r.alt_min === r.alt_max ? `up to ${deg(r.alt_max)}` : `${deg(r.alt_min)} to ${deg(r.alt_max)} high`;
  return `${compassPoint(mid)}, ${deg(r.az_from)} to ${deg(r.az_to)}, ${height} (${plural(r.frames, "frame", "frames")})`;
}

/** The headline of an offer: the evidence, in counts. */
export function offerSummary(s: HorizonSuggestion): string {
  return `${plural(s.frames, "frame", "frames")} over ${plural(s.nights, "night", "nights")} `
    + `showed the same obstruction in ${plural(s.regions.length, "direction", "directions")}.`;
}

/** What a failed accept says. A moved offer is not an error the operator
 *  caused: the page reloads it and asks again. */
export function acceptFailure(err: { code?: string; message?: string }): string {
  if (err.code === "suggestion_changed" || err.code === "no_suggestion") {
    return "The offer changed while you were looking. Review it again.";
  }
  return err.message ? `Could not store the horizon: ${err.message}` : "Could not store the horizon.";
}
