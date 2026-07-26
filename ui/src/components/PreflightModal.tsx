// PreflightModal.tsx — the full pre-flight gate before Run Sequence
// (onboarding spec §2b). Opens ONLY when there's a blocked item (or from the
// strip's "Review"). It explains + offers in-place Fix; it is not a mandatory
// gate on every run. Blocked rows are pinned to the top and visually separated;
// the footer primary enables only when no blockers remain (proceeding past
// remaining warnings is a single tap — never a hold).
//
// Live re-check is debounced 750ms and the layout is frozen (rows don't churn
// while read). Focus trap / initial focus / Escape / focus return via the modal
// shell. onProceed receives `force` (true only when a low-horizon warning was
// accepted upstream); the integrator threads it into POST /api/sequence/start.

import { useEffect, useMemo, useState, type JSX } from "react";
import type { CheckItem } from "../types";
import { Icon } from "./icons";
import type { SequencePlan } from "../types";
import type { PreflightActions } from "../lib/preflight";
import { preflightVerdict } from "../lib/preflight";
import { usePreflight } from "./PreflightStrip";
import { Checklist } from "./Checklist";
import { Overlay } from "./Overlay";

const DEBOUNCE_MS = 750;

/** A small word-bearing verdict pill — colour is tertiary, the word carries it. */
function VerdictPill({ items }: { items: CheckItem[] }) {
  const blocked = items.filter((i) => i.status === "blocked").length;
  const v = preflightVerdict(items);
  const word = v === "blocked" ? (blocked === 1 ? "1 TO FIX" : `${blocked} TO FIX`) : v === "warn" ? "WARN" : "READY";
  const tint = v === "blocked" ? "text-bad border-bad/50" : v === "warn" ? "text-warn border-warn/50" : "text-good border-good/50";
  return (
    <span className={`mono text-[11px] tracking-wider border px-2 py-0.5 ${tint}`}>{word}</span>
  );
}

export function PreflightModal({
  plan,
  actions,
  open,
  onClose,
  onProceed,
  force = false,
}: {
  plan: SequencePlan;
  actions?: PreflightActions;
  open: boolean;
  onClose: () => void;
  onProceed: (force: boolean) => void;
  force?: boolean;
}): JSX.Element | null {
  const { items: liveItems } = usePreflight(plan, actions);

  // Debounced snapshot — freeze the list for 750ms so it doesn't churn while read.
  const [shown, setShown] = useState<CheckItem[]>(liveItems);
  useEffect(() => {
    const id = window.setTimeout(() => setShown(liveItems), DEBOUNCE_MS);
    return () => window.clearTimeout(id);
  }, [liveItems]);
  // First open: show immediately (don't wait the debounce on entry).
  useEffect(() => {
    if (open) setShown(liveItems);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Blocked rows pinned to the top, then warn/disabled, then the rest — stable order.
  const ordered = useMemo(() => {
    const rank = (i: CheckItem) =>
      i.status === "blocked" ? 0 : i.status === "warn" || i.status === "disabled" ? 1 : 2;
    return [...shown].sort((a, b) => rank(a) - rank(b));
  }, [shown]);

  const blockers = shown.filter((i) => i.status === "blocked");
  const hasBlocker = blockers.length > 0;

  // Focus trap / initial focus / Escape / restore, the portal out of the view
  // tree, the viewport clamp, the opaque surface and the always-reachable footer
  // all come from <Overlay/> now. This modal is the reason that primitive
  // exists: it was measured at x=-10 with its status column at x=-80 on tablet,
  // and with CANCEL / RUN SEQUENCE at y=899..947 in a 900px viewport on desktop,
  // page scroll-locked so no gesture could reach them (review #3, S1).
  const blockerReason = hasBlocker
    ? `${blockers[0].label}${blockers[0].detail?.value ? `: ${blockers[0].detail.value}` : ""}`
    : null;

  return (
    <Overlay
      open={open}
      label={`Pre-flight for ${plan.name}`}
      onClose={onClose}
      variant="center"
      bodyClassName="p-2"
      head={
        <div className="flex items-center justify-between gap-3 p-4">
          <h2 className="panel-title !text-ink truncate">Pre-flight — {plan.name}</h2>
          <VerdictPill items={shown} />
        </div>
      }
      foot={
        <div className="flex flex-col gap-2 p-4">
          {blockerReason && (
            <p className="text-[12px] text-dim" id="preflight-blocker">
              <span className="text-bad mono mr-1" aria-hidden>✕</span>
              {blockerReason}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <button type="button" className="btn" onClick={onClose}>
              Cancel
            </button>
            {/* House rule §11.8 / review #24: a control the user WANTS to press is
                never natively `disabled` — that drops it out of the a11y tree
                along with the reason. aria-disabled keeps it focusable and named
                with the blocker, which is also the only channel that works on a
                touch device. The blocker text above stays the visible reason. */}
            <button
              type="button"
              className={`btn btn-accent min-h-12 ${hasBlocker ? "opacity-50 cursor-not-allowed" : ""}`}
              aria-disabled={hasBlocker || undefined}
              aria-describedby={hasBlocker ? "preflight-blocker" : undefined}
              aria-label={hasBlocker ? `Run Sequence — blocked: ${blockerReason}` : undefined}
              onClick={() => { if (!hasBlocker) onProceed(force); }}
            >
              <Icon name="play" size={14} className="inline -mt-0.5 mr-1" />Run Sequence
            </button>
          </div>
        </div>
      }
    >
      {/* pinned blockers first; rows flip live as the user fixes things */}
      <Checklist items={ordered} />
    </Overlay>
  );
}
