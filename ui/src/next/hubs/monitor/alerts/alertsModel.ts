// alertsModel.ts - the pure half of MONITOR - ALERTS' sink editor (wave R7,
// T-R7-10). Copy, one-line derivations and the lock-sentence composer, kept out
// of the components so every string can be asserted without a DOM and so no
// sentence is written twice.
//
// TRANSCRIBED, WITH ATTRIBUTION, from `components/settings/AlertsPanel.tsx`,
// which is NOT edited, NOT deleted, and keeps serving `#/classic`:
//   the destination line              AlertsPanel.tsx:582-585
//   the six lockedTitle verbs         :346, :549, :602, :610, :617, :684
//   the two read-only footers         :656, :695
//   the empty-sinks consequence copy  :561-577
//   the dead-man's-switch note        :665-669
//   the write-only "(unchanged)" /
//   "set" / "not set" markers         :106, :113, :670, :678
//
// Everything DECISION-shaped stays in `lib/alertSinks.ts` - `defaultDraft`,
// `validateDraft`, `deriveSinkHealth`, `deadmanVerdict`, `ALERT_KINDS`,
// `ALL_EVENTS`, `kindLabel` - and is imported, never re-derived. That module is
// shared with the legacy panel and is the reason the two renderings cannot
// disagree about what "Untested" means.

import { accessPhrase } from "../../../../lib/caps";
import type { AlertSink } from "../../../../types";

/** `lib/alertSinks.ts` writes three of its sentences with a long dash:
 *  `deriveSinkHealth`'s "Delivery is failing - retrying", `deadmanVerdict`'s
 *  "Monitor URL is not being reached - check it" and `validateDraft`'s "SMTP
 *  port must be 1-65535". The house rule is hyphens, and that module is shared
 *  with `#/classic`, so it is not edited from here: the dashes are normalised
 *  at the ONE boundary that renders them, and the lib fix is named in this
 *  task's report as a follow-up. Written as escapes so this file does not
 *  itself contain the characters a reviewer greps for. */
export const hyphens = (s: string): string => s.replace(/[\u2013\u2014]/g, "-");

/** Where this sink actually sends, in one line. The list row's whole job is to
 *  let someone tell two ntfy topics apart at 03:00, so the destination is
 *  visible text and not a tooltip. `AlertsPanel.tsx:582-585`. */
export function sinkDestination(s: AlertSink): string {
  if (s.kind === "email") return s.smtp_to || "no recipients";
  if (s.kind === "telegram") return s.chat_id || "no chat id";
  return s.url || "no url";
}

/** The write-only marker beside a secret's label: the server never sends the
 *  secret back, so "set" / "not set" is the only thing that can be said about
 *  it, and saying nothing is what made an empty box ambiguous. */
export const setMarker = (configured: boolean): string => (configured ? "set" : "not set");

/** The placeholder inside a secret box whose value is already stored. Leaving
 *  it blank and saving means UNCHANGED - the server's own contract
 *  (`api/alerts.ts`: "An empty `token` on update means unchanged"). */
export const SECRET_UNCHANGED = "(unchanged)";

/** An event id as a chip word: `run_start` -> `RUN START`. DERIVED from the id,
 *  never a hand-written list - `ALL_EVENTS` is the server's set and a
 *  hard-coded label table silently drops the next event it gains. */
export const eventLabel = (ev: string): string => ev.replace(/_/g, " ").toUpperCase();

// ------------------------------------------------------------------- gating
//
// SIX DISTINCT REFUSALS, not one. Every one of these presses a different route
// and a user who is told "Deleting needs admin access" while standing on the
// delete button learns something that "needs admin access" alone does not.
//
// `useLock({cap:"config.alerts"})` answers with either `needs <phrase>` - the
// capability half - or a standalone sentence such as "the rig is not
// reachable". Only the first composes with a verb, so the standalone case is
// returned VERBATIM rather than glued into a sentence that does not parse.

export const ADD_VERB = "Adding a sink";
export const TEST_VERB = "Testing";
export const EDIT_VERB = "Editing";
export const DELETE_VERB = "Deleting";
export const SAVE_VERB = "Saving";
export const DEADMAN_VERB = "Changing this";

export function lockSentence(verb: string, reason: string | null): string | null {
  if (!reason) return null;
  return reason.startsWith("needs ") ? `${verb} ${reason}` : reason;
}

/** The two footers, as `LockNote` reasons - the primitive prefixes
 *  "Read-only - ", so these are the remainder of the sentence and the full
 *  strings read:
 *
 *    Read-only - changing alerts needs <phrase>.
 *    Read-only - changing the monitor URL needs <phrase>.
 *
 *  Derived from `accessPhrase("config.alerts")` and never from a hard-coded
 *  role word: `config.alerts` is admin-only today, and a sentence that says so
 *  in its own words would go stale the day the role table moves. */
export const SINKS_LOCK_NOTE = `changing alerts needs ${accessPhrase("config.alerts")}.`;
export const DEADMAN_LOCK_NOTE =
  `changing the monitor URL needs ${accessPhrase("config.alerts")}.`;

// --------------------------------------------------------------------- copy

export const SINKS_INTRO =
  "Outbound push, webhook and email channels for run start and end, safety "
  + "transitions, reconnects, and warning or error logs.";

/** UX review #31, carried whole. The default state is `alerts: []` and
 *  `deadman_url: ""` - nothing is watching an unattended rig - and the old copy
 *  stated that as a neutral fact ("No alert sinks configured yet."). A rain
 *  abort then produced a red banner on a browser tab nobody was looking at.
 *  State the CONSEQUENCE, not the state. */
export const NOTHING_WATCHING_LEAD = "Nothing is watching this rig.";
export const NOTHING_WATCHING_BODY =
  "With no channel here, a cloud pause at 01:15 or a mount that stops tracking "
  + "reaches a browser tab and nowhere else - you find out in the morning. Add one "
  + "channel (ntfy is two taps and needs no account) and the rig can wake you.";

export const DEADMAN_NOTE =
  "An external healthcheck (healthchecks.io, Uptime-Kuma) that AstroDeck pings "
  + "periodically. Its ABSENCE - not a local error - is what pages you if the whole "
  + "box goes dark.";

export const MIN_LEVEL_HINT =
  "Applies to generic warning and error logs. State-change alerts always send.";

export const HEARTBEAT_HINT =
  "A periodic 'still running' ping, so silence means a problem rather than a quiet night.";

export const LOADING_CONFIG =
  "Waiting for the rig to send its alert configuration.";

/** The 409 both write paths share: someone else changed the config between the
 *  read and the write, and the panel has already reloaded it. */
export const CONFLICT_TOAST = "Config changed elsewhere - reloaded, re-apply your edit";
