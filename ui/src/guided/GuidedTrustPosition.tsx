// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// The guided flows' answer to "the mount does not know where it points" (#929).
//
// After a power cycle the mount reports its HOME position wherever the tube is,
// and the server refuses every goto until the position is vouched for (#886, 409
// `position_unknown`). Its refusal advises TRUST POSITION, or a pad key to bring
// the tube home by eye first. The guided pages that point the telescope - the
// first image and the alignment field - had neither control, so the only way on
// was to leave Guided. This hosts the same attestation `MountView` and the new
// mount sheet host, in the same words (the constants in lib/slewController.ts),
// at the place the point button would be, and the pages show it INSTEAD of a
// button the server would refuse.
//
// The Polar tool hosts it too (#985), in Guided and in Pro: Start Alignment is a
// move from the believed position, refused by the same gate (`/api/polar/start`),
// and PolarView had no control the refusal's advice could name.
//
// Like the other two it advises no slew: the way out for a tube that is not at
// home is a pad key held by eye, and the pad lives in Pro's Mount tool.
import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { confirmDialog } from "../components/ConfirmDialog";
import { useCanControlMount } from "../lib/caps";
import {
  TRUST_POSITION_CONFIRM_BODY, TRUST_POSITION_CONFIRM_LABEL, TRUST_POSITION_CONFIRM_TITLE,
  POSITION_UNKNOWN_CODE, TRUST_POSITION_LABEL, positionKnown,
} from "../lib/slewController";
import { useStore } from "../store";

/** Does the live status say the mount does not know where it points? A stale
 *  link says nothing either way (the pages already explain that one), and a
 *  status that never carried the flag reads as known (`positionKnown`). */
function usePositionUnknown(): boolean {
  return useStore(s => s.wsPhase === "up" && !s.telemetryStale && !!s.status?.mount && !positionKnown(s.status.mount));
}

/** Is this the server's refusal of a move from an unknown position? The status
 *  normally flags the state first; this catches a goto that raced it. */
function isPositionRefusal(e: unknown): boolean {
  return e instanceof ApiError && e.status === 409 && e.code === POSITION_UNKNOWN_CODE;
}

/** What a guided page asks before it offers to point the telescope.
 *
 *  `needed`: show `GuidedTrustPosition` in place of the point button, because
 *  the status says the position is unknown or the server just refused a goto
 *  for it. `refusedBy(e)`: call it in the catch around the goto; true means the
 *  error was that refusal and is now shown as the control, not as an error line
 *  advising a pad that is not on this screen. `clear`: the attestation went
 *  through. A refusal is forgotten once the status itself flags the state,
 *  which then owns the display. */
export function usePositionGate(): { needed: boolean; refusedBy: (e: unknown) => boolean; clear: () => void } {
  const unknown = usePositionUnknown();
  const [refused, setRefused] = useState(false);
  useEffect(() => { if (unknown) setRefused(false); }, [unknown]);
  return {
    needed: unknown || refused,
    refusedBy: (e) => { if (!isPositionRefusal(e)) return false; setRefused(true); return true; },
    clear: () => setRefused(false),
  };
}

/** Why TRUST POSITION is out of service right now, or null. The claim is about
 *  a tube at rest, and a viewer may not make it. */
function trustBlocker(canMove: boolean, slewing: boolean, sending: boolean): string | null {
  if (!canMove) return "Saying where the telescope is needs operator or admin access.";
  if (slewing) return "The mount is moving. Say where the telescope is once it has stopped.";
  if (sending) return "Sending your answer to the mount.";
  return null;
}

/** `inPro`: the block is hosted by a Pro tool (the Polar tool, #985), where
 *  "switch to Pro" would send the operator to where they already are; Mount is
 *  one tab away. Everything else is the same words as in Guided. */
export function GuidedTrustPosition({ onTrusted, inPro = false }: { onTrusted?: () => void; inPro?: boolean }) {
  const canMove = useCanControlMount();
  const slewing = useStore(s => !!s.status?.mount?.slewing || !!s.status?.busy_lanes?.includes("goto"));
  const [sending, setSending] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const blocker = trustBlocker(canMove, slewing, sending);
  const trust = async () => {
    if (blocker) return;
    // Asked first, in the words of what is being claimed: one tap must not
    // switch off a guard on a word the operator did not mean.
    const sure = await confirmDialog({
      title: TRUST_POSITION_CONFIRM_TITLE,
      body: TRUST_POSITION_CONFIRM_BODY,
      tone: "warn",
      mode: "confirm",
      confirmLabel: TRUST_POSITION_CONFIRM_LABEL,
    });
    if (!sure) return;
    setSending(true); setProblem(null);
    try {
      const res = await api.post<{ position_known?: boolean }>("/api/mount/trust-position");
      // The answer is the driver's verdict afterwards: a driver that keeps its
      // own evidence and declines must not be reported as cleared. The page lets
      // go on the next status frame, never on this press.
      if (res?.position_known === false) {
        setProblem("The mount did not accept that its position is known. Switch to Pro and use Solve & Sync in Mount, where the telescope points now.");
        return;
      }
      useStore.getState().enqueueToast({
        level: "success",
        title: "Position trusted",
        detail: "Pointing unlocks with the next status update.",
      });
      onTrusted?.();
    } catch (e) {
      setProblem(e instanceof Error ? e.message : "The mount did not answer. Try again.");
    } finally { setSending(false); }
  };
  return <div className="guided-position-unknown" data-testid="guided-position-unknown">
    <h3>The mount doesn't know where the telescope points</h3>
    <p>It is reporting its home position, as it does after any power-up. Pointing is paused until it knows where the telescope really is.</p>
    <p>If the telescope is at its home or park position now, press {TRUST_POSITION_LABEL}. If it is anywhere else, {inPro ? "open Mount" : "switch to Pro, open Mount"} and hold a pad key to bring it home by eye, then press {TRUST_POSITION_LABEL} there.</p>
    <button className="btn btn-accent" disabled={!!blocker} aria-busy={sending || undefined} title={blocker ?? undefined} onClick={() => void trust()}>{TRUST_POSITION_LABEL}</button>
    {blocker && !sending && <p role="status">{blocker}</p>}
    {problem && <p role="alert" className="guided-warning">{problem}</p>}
  </div>;
}
