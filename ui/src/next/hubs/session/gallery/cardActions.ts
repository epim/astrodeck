// cardActions.ts - the six verbs on a Gallery card, their availability, and
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
  deleteSession, getSession, patchSession, resumeSession,
} from "../../../../api/sessions";
import { confirmDialog } from "../../../../components/ConfirmDialog";
import { accessPhrase } from "../../../../lib/caps";
import { mergePreview, type MergePreview } from "../../../../lib/sessions";
import { useStore } from "../../../../store";
import type { SequencePlan } from "../../../../types";
import type { SessionCardData } from "./useSessionCards";

export const CONFIRM_ABANDON =
  "Removes it from the panel but keeps its ledger and thumbnails on disk. " +
  "Delete, by contrast, permanently removes them.";

export const CONFIRM_DELETE =
  "Removes the session ledger and thumbnails. Saved FITS frames are NOT " +
  "deleted. This cannot be undone.";

export const CONFIRM_AUTO_RESUME_NO_MONITOR =
  "No safety monitor is connected — the rig may start unattended in bad " +
  "weather. A persistent warning stays on this card while armed.";

export const ARMED_WITHOUT_MONITOR_CHIP =
  "auto-resume armed without a safety monitor - rig may start in bad weather";

/** What "Update from plan" is about to do to recorded progress, verbatim. */
export function mergeConfirmBody(m: MergePreview): string {
  return `${m.kept} step(s) keep recorded progress · ${m.added} new start at zero · `
    + `${m.dropped} with recorded frames dropped (their frames stay in the ledger `
    + "but stop counting toward any quota).";
}

export type VerbId = "resume" | "update" | "autoResume" | "report" | "abandon" | "delete";

export interface Verb {
  id: VerbId;
  label: string;
  /** null = live. Anything else is rendered on the control itself. */
  reason: string | null;
  danger?: boolean;
}

/**
 * The verb list for one card, in the order the design shows them.
 *
 * `control.mount` gates all five write verbs (the same capability the server
 * enforces on resume / PATCH / DELETE); REPORT is a read and needs none. The
 * per-status reasons mirror what the server would answer, so a tap that cannot
 * work is refused with the server's own logic rather than discovered in a 409.
 */
export function verbsFor(card: SessionCardData, canControl: boolean): Verb[] {
  const capReason = canControl ? null : `That needs ${accessPhrase("control.mount")}.`;
  const noSession = card.id ? null : "This night has a report but no session ledger, so there is nothing to act on.";
  const dormantOnly = card.status === "dormant" ? null : "Only a dormant session can be resumed or edited.";
  const notActive = card.status === "active" ? "The session is running - stop the run first." : null;

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
      `${r.remaining} frame${r.remaining === 1 ? "" : "s"} still owed`);
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
    toast("success", "Session abandoned", "Its ledger and thumbnails stay on disk.");
    after();
  } catch (e) {
    toast("error", "Could not abandon this session", say(e));
  }
}

export async function runDelete(id: string, name: string, after: () => void): Promise<void> {
  const go = await confirmDialog({
    title: `Delete "${name}"?`,
    body: CONFIRM_DELETE,
    tone: "danger",
    mode: "confirm",
    confirmLabel: "Delete",
  });
  if (!go) return;
  try {
    await deleteSession(id);
    toast("success", "Session deleted", "The saved FITS frames are untouched.");
    after();
  } catch (e) {
    toast("error", "Could not delete this session", say(e));
  }
}

/** Arming it without a safety monitor is the one that needs a sentence: the rig
 *  can start itself at dusk into weather nothing is watching. */
export async function runAutoResume(
  id: string, next: boolean, monitorConnected: boolean, after: () => void,
): Promise<void> {
  if (next && !monitorConnected) {
    const go = await confirmDialog({
      title: "Arm auto-resume with no safety monitor?",
      body: CONFIRM_AUTO_RESUME_NO_MONITOR,
      tone: "warn",
      mode: "confirm",
      confirmLabel: "Arm it",
    });
    if (!go) return;
  }
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
