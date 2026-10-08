// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// cardActions.ts - the verbs on a Gallery card (seven since ARM AS NEXT, #598), their availability, and
// the sentence each destructive one has to say first.
//
// EVERY CONFIRM BODY HERE IS VERBATIM (plan C.4). They are not decoration: each
// one names the consequence the verb's own label does not. "Abandon" and
// "Delete" sound like synonyms and are not - one keeps the ledger and the
// thumbnails on disk, the other removes them - so the difference is stated in
// the dialog where the decision is being made, not in a help page.
//
// AVAILABILITY IS A REASON, NEVER A HIDDEN CONTROL. A viewer sees the same six
// verbs an operator does, each carrying why it will not fire (ARCHITECTURE #8):
// a screen that hides what you cannot do teaches nothing about what you would
// need in order to do it.
//
// Confirms go through `confirmDialog`, which writes the store's `confirm`
// slice, so they render in the new shell's ConfirmCard with no extra wiring.

import { ApiError } from "../../../../api";
import {
  deleteSession, getSession, isUnreadableRow, listSessionRows, patchSession, restoreBody,
  restoreSession, resumeSession, unreadableDeleteBody, type DeleteSessionResult,
  type SessionPatch, type SessionPatchResult, type UnreadableListRow,
} from "../../../../api/sessions";
import { confirmDialog } from "../../../../components/ConfirmDialog";
import { accessPhrase } from "../../../../lib/caps";
import { disarmedWarningLine, type DisarmedSession } from "../../../../lib/disarmed";
import { mergePreview, type MergePreview } from "../../../../lib/sessions";
import { useStore } from "../../../../store";
import type { SequencePlan } from "../../../../types";
import type { QueueTarget } from "./sessionsIndex";
import type { SessionCardData } from "./useSessionCards";

export const CONFIRM_ABANDON =
  "Removes it from the panel but keeps its session log and thumbnails on disk. " +
  "Delete, by contrast, permanently removes them.";

export const CONFIRM_DELETE =
  "Removes the session log and thumbnails. Saved FITS frames are NOT " +
  "deleted. This cannot be undone.";

export const CONFIRM_AUTO_RESUME_NO_MONITOR =
  "No safety monitor is connected - the rig may start unattended in bad " +
  "weather. A persistent warning stays on this card while armed.";

/** The verb that makes a dormant session wait behind the live or armed one
 *  (#598, backlog ruling D-04). */
export const QUEUE_NEXT_LABEL = "ARM AS NEXT";

export const ARMED_WITHOUT_MONITOR_CHIP =
  "auto-resume armed without a safety monitor - rig may start in bad weather";

/** What "Update from plan" is about to do to recorded progress, verbatim. */
export function mergeConfirmBody(m: MergePreview): string {
  return `${m.kept} step(s) keep recorded progress · ${m.added} new start at zero · `
    + `${m.dropped} with recorded frames dropped (their frames stay in the session log `
    + "but stop counting toward any quota).";
}

export type VerbId =
  | "resume" | "update" | "autoResume" | "queueNext" | "report" | "abandon" | "delete";

export interface Verb {
  id: VerbId;
  label: string;
  /** null = live. Anything else is rendered on the control itself. */
  reason: string | null;
  danger?: boolean;
}

/** Why a write verb will not fire for this principal, or null when it will.
 *  One sentence for every card, including an unreadable file's DELETE (#242),
 *  so the two deletes on one shelf are gated by the same words. */
export function controlReason(canControl: boolean): string | null {
  return canControl ? null : `That needs ${accessPhrase("control.mount")}.`;
}

/**
 * The verb list for one card, in the order the design shows them.
 *
 * `control.mount` gates all five write verbs (the same capability the server
 * enforces on resume / PATCH / DELETE); REPORT is a read and needs none. The
 * per-status reasons mirror what the server would answer, so a tap that cannot
 * work is refused with the server's own logic rather than discovered in a 409.
 */
export function verbsFor(
  card: SessionCardData, canControl: boolean, queueTarget: QueueTarget | null = null,
): Verb[] {
  const capReason = controlReason(canControl);
  const noSession = card.id ? null : "This night has a report but no session log, so there is nothing to act on.";
  const dormantOnly = card.status === "dormant" ? null : "Only a dormant session can be resumed or edited.";
  const notActive = card.status === "active" ? "The session is running - stop the run first." : null;
  // ARM AS NEXT (#598). Offered exactly when the server would not answer 409:
  // dormant, not already armed in its own right (it would not need to wait),
  // not already waiting (the chip on the card cancels that), and with a live
  // or armed session to wait behind (`queueTargetFor`, the server's own choice).
  const queueReason = card.status !== "dormant"
    ? "Only a dormant session can wait for another one."
    : card.autoResume
      ? "This session is already armed, so it starts by itself and has nothing to wait for."
      : card.queuedBehind != null
        ? "This session already waits for another one; cancel it from the NEXT chip on the card."
        : queueTarget == null
          ? "Nothing else is running or armed to wait behind. Turn AUTO-RESUME on to arm this session instead."
          : null;

  return [
    {
      id: "resume",
      label: "RESUME",
      reason: noSession ?? capReason ?? dormantOnly,
    },
    {
      id: "update",
      label: "UPDATE FROM PLAN",
      reason: noSession ?? capReason ?? dormantOnly,
    },
    {
      id: "autoResume",
      label: card.autoResume ? "AUTO-RESUME ON" : "AUTO-RESUME OFF",
      reason: noSession ?? capReason ?? dormantOnly,
    },
    {
      id: "queueNext",
      label: QUEUE_NEXT_LABEL,
      reason: noSession ?? capReason ?? queueReason,
    },
    {
      id: "report",
      label: "REPORT",
      reason: card.reportId ? null : "No report was written for this night.",
    },
    {
      id: "abandon",
      label: "ABANDON",
      reason: noSession ?? capReason
        ?? (card.status === "dormant" || card.status === "complete"
          ? null : "Only a dormant or complete session can be abandoned."),
    },
    {
      id: "delete",
      label: "DELETE",
      danger: true,
      reason: noSession ?? capReason ?? notActive,
    },
  ];
}

function toast(level: "info" | "success" | "warning" | "error", title: string, detail?: string): void {
  useStore.getState().enqueueToast({ level, title, detail });
}

function say(e: unknown): string | undefined {
  return e instanceof ApiError ? e.message : e instanceof Error ? e.message : undefined;
}

export async function runResume(id: string, after: () => void): Promise<void> {
  try {
    const r = await resumeSession(id);
    toast("success", r.resumed ? "Session resumed" : "Nothing left to shoot",
      `${r.remaining} frame${r.remaining === 1 ? "" : "s"} remaining`);
    after();
  } catch (e) {
    toast("error", "Could not resume this session", say(e));
  }
}

export async function runAbandon(id: string, name: string, after: () => void): Promise<void> {
  const go = await confirmDialog({
    title: `Abandon "${name}"?`,
    body: CONFIRM_ABANDON,
    tone: "warn",
    mode: "confirm",
    confirmLabel: "Abandon",
  });
  if (!go) return;
  try {
    await patchSession(id, { status: "abandoned" });
    toast("success", "Session abandoned", "Its session log and thumbnails stay on disk.");
    after();
  } catch (e) {
    toast("error", "Could not abandon this session", say(e));
  }
}

/** The DELETE confirm's body for `id`, looked up fresh (#279).
 *
 *  `runDelete` is the SAME route a session card and an unreadable file's card
 *  both call (`UnreadableSessionCard`'s own comment says so on purpose), so it
 *  picks its own sentence rather than take one on faith from the caller: a
 *  stale card could otherwise confirm a backup the file no longer has, or the
 *  reverse. `CONFIRM_DELETE` is right for a readable session and for an
 *  unreadable file with no backup; `unreadableDeleteBody` is the one that
 *  names the backup when the row reports one (#266) - `DELETE` on an
 *  unreadable file otherwise keeps that backup and the thumbnails, so
 *  `CONFIRM_DELETE`'s "removes the session ledger and thumbnails" is false for
 *  it. A lookup that fails, or a row that has vanished, falls back to
 *  `CONFIRM_DELETE`: the confirm must still say something, and that sentence
 *  is the safe direction (it names MORE as gone than an unreadable file with a
 *  backup actually loses), never the reverse. */
async function deleteConfirmBody(id: string): Promise<string> {
  const row = await unreadableRowFor(id);
  return row ? unreadableDeleteBody(row) : CONFIRM_DELETE;
}

/** The unreadable row for `id` as the server lists it NOW, or null when there
 *  is none or the lookup failed.
 *
 *  ONE LOOKUP FOR BOTH CONFIRMS (#280). The DELETE and RESTORE sentences each
 *  depend on what the file is at the moment of the press (a backup beside a
 *  damaged file, or a backup that is all that is left), so neither takes it on
 *  faith from a card that may be a poll old. A failed lookup is not the action
 *  failing, so it answers null and each caller falls to its safe sentence. */
async function unreadableRowFor(id: string): Promise<UnreadableListRow | null> {
  try {
    const rows = await listSessionRows();
    const row = rows.find((r) => r.id === id);
    if (row && isUnreadableRow(row)) return row;
  } catch {
    // Not the action failing; the caller falls back to its own sentence.
  }
  return null;
}

/** The success toast's detail for a DELETE (#279). `backup_kept`/`detail`
 *  come from the server only when the deleted file was unreadable and a
 *  `.bak` survived it (#266); that `detail` already names the backup and says
 *  it remains, so it replaces the generic FITS sentence rather than sitting
 *  beside it unread. Every other delete keeps the FITS line, which is the
 *  answer to the question every delete raises. */
function deleteToastDetail(r: DeleteSessionResult): string {
  return r.backup_kept && r.detail ? r.detail : "The saved FITS frames are untouched.";
}

export async function runDelete(id: string, name: string, after: () => void): Promise<void> {
  const go = await confirmDialog({
    title: `Delete "${name}"?`,
    body: await deleteConfirmBody(id),
    tone: "danger",
    mode: "confirm",
    confirmLabel: "Delete",
  });
  if (!go) return;
  try {
    const r = await deleteSession(id);
    toast("success", "Session deleted", deleteToastDetail(r));
    after();
  } catch (e) {
    toast("error", "Could not delete this session", say(e));
  }
}

/** Put the backup beside an unreadable file in its place (#280).
 *
 *  A CONFIRM FIRST, because it replaces a damaged file whole (its bytes are
 *  gone afterwards) and because frames accepted since the backup was taken are
 *  not in it. The body is `restoreBody`'s, looked up fresh like the DELETE's:
 *  the lookup failing falls back to the damaged-file wording, which says
 *  MORE is lost, never less. The toast is the server's own `detail`, which
 *  says what came back and that the session is dormant with auto-resume off,
 *  so the card says nothing the server did not. */
export async function runRestore(id: string, name: string, after: () => void): Promise<void> {
  const row = await unreadableRowFor(id);
  const go = await confirmDialog({
    title: `Restore "${name}" from its backup?`,
    body: restoreBody(row ?? { id }),
    tone: "warn",
    mode: "confirm",
    confirmLabel: "Restore",
  });
  if (!go) return;
  try {
    const r = await restoreSession(id);
    toast("success", "Session restored", r.detail);
    after();
  } catch (e) {
    toast("error", "Could not restore this session", say(e));
  }
}

/** Arming it without a safety monitor is the one that needs a sentence: the rig
 *  can start itself at dusk into weather nothing is watching. */
async function confirmArmWithoutMonitor(): Promise<boolean> {
  return confirmDialog({
    title: "Arm auto-resume with no safety monitor?",
    body: CONFIRM_AUTO_RESUME_NO_MONITOR,
    tone: "warn",
    mode: "confirm",
    confirmLabel: "Arm it",
  });
}

export async function runAutoResume(
  id: string, next: boolean, monitorConnected: boolean, after: () => void,
): Promise<void> {
  if (next && !monitorConnected && !(await confirmArmWithoutMonitor())) return;
  try {
    await patchSession(id, { auto_resume: next });
    toast("success", next ? "Auto-resume armed" : "Auto-resume disarmed");
    after();
  } catch (e) {
    toast("error", "Could not change auto-resume", say(e));
  }
}

/** Re-point a dormant session at the CURRENT plan. The preview is computed from
 *  the session's own frozen plan, so the numbers in the dialog are the numbers
 *  the server will apply. */
export async function runUpdateFromPlan(
  id: string, plan: SequencePlan | null, after: () => void,
): Promise<void> {
  if (!plan) {
    toast("warning", "No plan is loaded", "Open a plan first; this replaces the session's frozen copy with it.");
    return;
  }
  let preview: MergePreview;
  try {
    const s = await getSession(id);
    preview = mergePreview(s, plan);
  } catch (e) {
    toast("error", "Could not read this session", say(e));
    return;
  }
  const go = await confirmDialog({
    title: `Update "${plan.name}" from the current plan?`,
    body: mergeConfirmBody(preview),
    tone: "warn",
    mode: "confirm",
    confirmLabel: "Update",
  });
  if (!go) return;
  try {
    await patchSession(id, { plan });
    toast("success", "Session updated from the plan");
    after();
  } catch (e) {
    toast("error", "Could not update this session", say(e));
  }
}

// ------------------------------------------------------------ the queue (#598)
//
// `SessionPatch` / `SessionPatchResult` (api/sessions.ts) do not carry the
// queue's fields and that file belongs to another work package, so the
// widening is local: `api.patch` never validates or strips a field (the
// response is exactly what the server sent), the same way NowEmpty reads
// `disarmed` off a narrower type.

type QueuePatch = SessionPatch & { queue_next?: boolean };
type QueuePatchResult = SessionPatchResult & {
  queued_behind?: QueueTarget | null;
  dequeued?: QueueTarget[];
  disarmed?: DisarmedSession[];
};

const patchQueue = (id: string, body: QueuePatch): Promise<QueuePatchResult> =>
  patchSession(id, body) as Promise<QueuePatchResult>;

/** ARM AS NEXT: wait behind the live or armed session and be armed when it
 *  COMPLETES. Nothing is armed or disarmed now, which is the whole point, and
 *  the toast says so; a queue of one means a session already waiting there
 *  loses its place, which is said in a second toast naming it (D-04). */
export async function runQueueNext(id: string, name: string, after: () => void): Promise<void> {
  try {
    const body: QueuePatch = { queue_next: true };
    const r = await patchQueue(id, body);
    const behind = r.queued_behind?.name || "the running session";
    toast("success", `"${name}" is next`,
      `It is armed when "${behind}" completes, never after a stop or a fault. `
      + `"${behind}" keeps its own auto-resume.`);
    if (r.dequeued && r.dequeued.length > 0) {
      const names = r.dequeued.map((d) => d.name || d.id).join(", ");
      toast("warning", "Another session lost its place in the queue",
        `${names} no longer waits behind "${behind}": only one can be next.`);
    }
    after();
  } catch (e) {
    toast("error", "Could not queue this session", say(e));
  }
}

/** CANCEL on the NEXT chip: the session waits for nothing again. */
export async function runCancelQueue(id: string, name: string, after: () => void): Promise<void> {
  try {
    const body: QueuePatch = { queue_next: false };
    await patchQueue(id, body);
    toast("success", `"${name}" is no longer next`, "It stays dormant and unarmed.");
    after();
  } catch (e) {
    toast("error", "Could not cancel the queue", say(e));
  }
}

/** ARM NOW on a stranded queue: the session it waited behind has gone, so
 *  nothing will ever promote it. Arms it in its own right, drops the dead
 *  marker in the same request, and names whatever that disarmed (D-04). */
export async function runArmNow(id: string, monitorConnected: boolean, after: () => void): Promise<void> {
  if (!monitorConnected && !(await confirmArmWithoutMonitor())) return;
  try {
    const body: QueuePatch = { auto_resume: true, queue_next: false };
    const r = await patchQueue(id, body);
    toast("success", "Auto-resume armed");
    if (r.disarmed && r.disarmed.length > 0) {
      toast("warning", "Arming this session disarmed another", disarmedWarningLine(r.disarmed));
    }
    after();
  } catch (e) {
    toast("error", "Could not arm this session", say(e));
  }
}
