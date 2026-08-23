// stateMeta(state) — the honest sequence-state presentation map for the Monitor
// (monitor spec §4.2 / resolves D3/E.1/E.2). Returns an icon NAME (from the
// project's Batch-1 icon set, NOT lucide-react — staying in-lane and consuming
// the landed `components/icons.tsx` primitive), a word that is ALWAYS shown, a
// tone, and whether the badge may blink (only when motion is allowed).
//
// Normative: never reuse SequenceView.tsx's pure-color logic — it collapses in
// night mode where good/warn/bad all read as red. The `label` word is the
// accessible channel; color and icon are redundant reinforcement.

import type { IconName } from "../components/icons";
import type { SequenceState } from "../types";

// Broader than the frozen design-system `Tone` (good|warn|bad): the Monitor also
// needs "accent" (complete / NINA-driving) and "dim" (idle). Kept local so the
// shared `Tone` contract is not widened for everyone.
export type StateTone = "good" | "warn" | "bad" | "accent" | "dim";

export interface StateMeta {
  icon: IconName; // render via <Icon name={icon} /> in the cell (lane 2B/2E)
  label: string; // ALWAYS rendered — the accessible, non-color channel
  tone: StateTone;
  blinkable: boolean; // may pulse only if prefers-reduced-motion is not set
}

const MAP: Record<SequenceState["state"], StateMeta> = {
  running: { icon: "monitor", label: "RUNNING", tone: "good", blinkable: true },
  paused: { icon: "pause", label: "PAUSED", tone: "warn", blinkable: false },
  // Still LIVE — the teardown is running and the rig has not stopped. It blinks
  // for the same reason RUNNING does: something is still happening. Without this
  // entry the fallback below reads IDLE over a rig that is winding down.
  // Also still LIVE. The engine PROMOTES a routine `running` publish to
  // "holding" while a cloud hold is up (sequence/engine.py `_set_state`), so
  // this string reaches the Monitor on a real overcast night — and until this
  // entry existed the fallback below badged it IDLE over a rig that was still
  // probing the sky on a timer and shooting hold darks. Warn, not good: the
  // plan is not progressing. Blinks because something is still happening.
  holding: { icon: "clock", label: "HOLDING", tone: "warn", blinkable: true },
  aborting: { icon: "stop", label: "ABORTING", tone: "warn", blinkable: true },
  complete: { icon: "check", label: "COMPLETE", tone: "accent", blinkable: false },
  aborted: { icon: "stop", label: "ABORTED", tone: "bad", blinkable: false },
  error: { icon: "alert", label: "ERROR", tone: "bad", blinkable: false },
  nina_native: { icon: "link", label: "NINA DRIVING", tone: "accent", blinkable: false },
  idle: { icon: "info", label: "IDLE", tone: "dim", blinkable: false },
};

/** Presentation metadata for a sequence state. Falls back to IDLE on anything
 *  unexpected so a cell never throws or renders a blank badge. */
export function stateMeta(state: SequenceState["state"]): StateMeta {
  return MAP[state] ?? MAP.idle;
}
