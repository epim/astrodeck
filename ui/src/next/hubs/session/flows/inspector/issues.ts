// issues.ts - the compile result's `unmapped[]` split into the three statements
// it actually makes. Pure.
//
// COPIED from `components/flows/FlowInspector.tsx`'s `RIG_ADVISORY` and its
// three `useMemo` partitions (wave R7 section 2.1's "embedded helpers" rule:
// the helper moves into the rebuilding task's own module, the legacy file is
// neither edited nor imported, and `#/classic` keeps its copy).

import type { FlowUnmapped } from "../../../../../lib/flowsApi";

/** `note`-level entries that are NOT "answered another way".
 *
 *  The note level means one thing to `to_plan.losses` - do not block the run -
 *  and the ANSWERED ANOTHER WAY heading reads a second meaning into it: the
 *  thing you drew does happen, by some other part of the engine. That held
 *  while every note was about a wire.
 *
 *  `cooling.setpoint_c` is not about a wire. It says the run has no target
 *  temperature at all, on a camera that could hold one - the state that put 19
 *  lights on disk at +23 C against a -10 C library on 2026-08-22. Under
 *  ANSWERED ANOTHER WAY that sentence contradicts its own heading, which is
 *  exactly the failure the heading was split out to fix. So it gets its own
 *  list: still non-blocking, still note weight, but a statement about THIS RIG
 *  rather than about the canvas. */
export const RIG_ADVISORY: ReadonlySet<string> = new Set(["cooling.setpoint_c"]);

export interface UnmappedSplit {
  /** NOT HONOURED BY A RUN - something drawn on the canvas will not happen. */
  losses: FlowUnmapped[];
  /** BEFORE YOU RUN - rig state the canvas cannot show, at note weight. */
  advisories: FlowUnmapped[];
  /** ANSWERED ANOTHER WAY - the drawn thing happens, elsewhere in the engine. */
  notes: FlowUnmapped[];
}

/** Three lists off one field, because they make three different statements.
 *  A `note` is not a quieter `warn`, and one of the notes is not about the
 *  canvas at all. */
export function splitUnmapped(unmapped: readonly FlowUnmapped[]): UnmappedSplit {
  const losses: FlowUnmapped[] = [];
  const advisories: FlowUnmapped[] = [];
  const notes: FlowUnmapped[] = [];
  for (const u of unmapped) {
    if (u.level !== "note") losses.push(u);
    else if (RIG_ADVISORY.has(u.key)) advisories.push(u);
    else notes.push(u);
  }
  return { losses, advisories, notes };
}

/** The word a compile finding carries beside its tone.
 *
 *  Colour is never the only channel (ARCHITECTURE section 6): under `:root.night`
 *  every token collapses toward one hue, and a red line and an amber line become
 *  the same line. `null` for a note - it is not a finding against the graph. */
export function levelWord(level: string): string | null {
  if (level === "danger") return "DANGER";
  if (level === "warn") return "WARNING";
  return null;
}
