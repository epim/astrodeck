// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// resumeTitle.ts - the one decision behind the title on all three
// resumable-run cards (SESSION/NOW's Interrupted, the plan editor's
// PlanResume, and the Monitor's RecoveryCards): backlog ruling D-13
// (owner-approved 2026-09-30).
//
// "RESUME STOPPED RUN" after an operator's own STOP. "RESUME INTERRUPTED
// RUN" after anything else that ends a run out from under the operator - a
// restart, a crash, a safety stop, or (WP-44) a polite server shutdown. #487
// named the harm this closes: a card titled INTERRUPTED frames a deliberate
// STOP as something gone wrong, which reads worse than saying nothing.
//
// THIS DEPENDS ON WP-44 SPLITTING THE SHUTDOWN WORD OFF "ABORTED". Before
// WP-44, a polite server shutdown (Ctrl+C, a service stop under a run)
// finalized the report "aborted" too - the same word a real STOP leaves - so
// no rule could tell the two apart from `end_reason` alone (#565). WP-44 gave
// the shutdown its own word ("shutdown"), so "aborted" now means only an
// operator's STOP, and a SHUTDOWN reads as an interruption: the operator did
// not choose it, same as a restart or a crash.
//
// ONE HELPER, THREE CALLERS, so the decision is made once. Each caller is
// responsible for getting `end_reason` onto its own `rec` from
// `GET /api/sequence/recoverable` (server `_why_dormant`) - this module reads
// that value and nothing else.

/** The recoverable route's `end_reason` for an operator's own STOP: the word
 *  the engine's finalize stamps on the report only on the abort arm
 *  (`_finalize_report("aborted")`, keyed on `_aborting` since WP-44 split the
 *  shutdown arm off it). */
export const END_REASON_ABORTED = "aborted";

export const RESUME_STOPPED_TITLE = "RESUME STOPPED RUN";
export const RESUME_INTERRUPTED_TITLE = "RESUME INTERRUPTED RUN";

/** D-13's title for the recoverable route's `end_reason`. STOPPED only for
 *  the one word that means an operator pressed STOP; everything else - a
 *  restart, a crash, a safety stop, a shutdown, or no `end_reason` at all (an
 *  older server, or a caller that has not wired the field through yet) -
 *  reads as an interruption. A loose match here (treating every non-null
 *  reason as a stop) would be the #565 regression again: a shutdown is not a
 *  STOP, and must not read as one. */
export function resumeTitle(endReason: unknown): string {
  return endReason === END_REASON_ABORTED ? RESUME_STOPPED_TITLE : RESUME_INTERRUPTED_TITLE;
}
