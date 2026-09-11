// helpModel.ts - the Help area's pure copy helpers (wave R7, T-R7-16).
//
// No React, no store, no fetch: everything here is a string in, a string out,
// so the parity test can compare what the screen renders against the constants
// the content actually comes from (`lib/troubleshoot.ts` TROUBLESHOOTING and
// `help.ts` HELP) rather than against a second copy of the copy.

import { HELP, type HelpKey } from "../../../../help";
import type { TroubleshootEntry } from "../../../../lib/troubleshoot";

/** How long the deep-linked entry stays highlighted before `clearHelpTopic()`
 *  spends the topic. Verbatim from `views/HelpView.tsx:60`; exported so the
 *  test asserts the DELAY and not just that some timer was armed. */
export const HIGHLIGHT_MS = 2500;

/** Em-dashes, en-dashes and typographic quotes out of shared copy.
 *
 *  `TROUBLESHOOTING` and `HELP` are rendered by BOTH front-ends, so their
 *  strings cannot be rewritten at the source without editing files this wave
 *  must not touch (`#/classic` keeps its own copy). Normalising at the render
 *  boundary is the fix the rest of the wave uses: the two lines below are
 *  copied from `hubs/session/plan/instructions/instructionsModel.ts:65` rather
 *  than imported, because areas do not import each other.
 *
 *  Substitutions only, never a reflow: the information is the sentence, and
 *  this changes punctuation. */
export function hyphenate(s: string): string {
  return s
    .replace(/—/g, "-")     // em dash
    .replace(/–/g, "-")     // en dash
    .replace(/[‘’]/g, "'")
    .replace(/[“”]/g, '"');
}

/** The glossary keys, in the order `help.ts` declares them. Declaration order
 *  is the author's order (the beginner-first set was appended deliberately),
 *  so it is kept rather than sorted. */
export const HELP_KEYS = Object.keys(HELP) as HelpKey[];

/** `hfrReject` -> `HFR REJECT`. The legacy view split the camel case and let
 *  CSS `capitalize` finish it, which produced "Hfr Reject"; the design's row
 *  title is caps anyway, so the split is done here and the case is not left to
 *  a stylesheet that a test cannot see. */
export function termLabel(k: string): string {
  return k.replace(/([A-Z])/g, " $1").trim().toUpperCase();
}

/** The one line a closed problem shows. Closed, the reader cannot see how long
 *  the fix is, so the step count is the news; for the entry a diagnosis
 *  deep-linked, WHY it is open and highlighted is the news, and saying it in
 *  words is what keeps the accent tint from carrying that state alone. */
export function problemSub(e: TroubleshootEntry, active: boolean): string {
  const n = e.steps.length;
  const steps = `${n} step${n === 1 ? "" : "s"}`;
  return active ? `opened by the error you tapped - ${steps}` : steps;
}

/** The glossary terms an entry cross-links, or null when it names none.
 *
 *  `TroubleshootEntry.seeAlso` has been declared, populated for seven of the
 *  eleven entries and guarded by `lib/__tests__/troubleshoot.test.ts` since
 *  NOV-9, and rendered by NOTHING - `views/HelpView.tsx` never reads it. The
 *  rebuild honours it: the terms are in the glossary immediately below, so
 *  naming them is a pointer, not a second definition. */
export function seeAlsoLine(e: TroubleshootEntry): string | null {
  const keys = e.seeAlso ?? [];
  if (keys.length === 0) return null;
  return `See also in the glossary: ${keys.map(termLabel).join(", ")}`;
}
