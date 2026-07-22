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

import { useEffect, useMemo, useRef, useState, type JSX } from "react";
import type { CheckItem } from "../types";
import { Icon } from "./icons";
import type { SequencePlan } from "../types";
import type { PreflightActions } from "../lib/preflight";
import { preflightVerdict } from "../lib/preflight";
import { usePreflight } from "./PreflightStrip";
import { Checklist } from "./Checklist";

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
  const panelRef = useRef<HTMLDivElement>(null);
  const openerRef = useRef<HTMLElement | null>(null);

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

  // Focus trap + initial focus + Escape + restore.
  useEffect(() => {
    if (!open) return;
    openerRef.current = (document.activeElement as HTMLElement) ?? null;
    const panel = panelRef.current;
    const firstFocusable = panel?.querySelector<HTMLElement>(
      'button:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])',
    );
    firstFocusable?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }
      if (e.key !== "Tab" || !panel) return;
      const f = panel.querySelectorAll<HTMLElement>(
        'button:not([disabled]), a[href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      );
      if (f.length === 0) return;
      const first = f[0];
      const last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      openerRef.current?.focus?.();
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[60] flex items-end sm:items-center justify-center"
      role="presentation"
      onPointerDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="fixed inset-0 bg-black/70" aria-hidden />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={`Pre-flight for ${plan.name}`}
        className="panel relative z-[61] w-full sm:max-w-[560px] max-h-[88vh] sm:rounded-none rounded-t flex flex-col sheet-enter"
      >
        {/* header */}
        <header className="flex items-center justify-between gap-3 p-4 border-b border-line shrink-0">
          <h2 className="panel-title !text-ink truncate">Pre-flight — {plan.name}</h2>
          <VerdictPill items={shown} />
        </header>

        {/* body — pinned blockers first; rows flip live as the user fixes things */}
        <div className="overflow-y-auto p-2 grow">
          <Checklist items={ordered} />
        </div>

        {/* footer — names the first blocker; primary enables only when clear */}
        <footer className="flex flex-col gap-2 p-4 border-t border-line shrink-0">
          {hasBlocker && (
            <p className="text-[12px] text-dim">
              <span className="text-bad mono mr-1" aria-hidden>✕</span>
              {blockers[0].label}
              {blockers[0].detail?.value ? `: ${blockers[0].detail.value}` : ""}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <button type="button" className="btn" onClick={onClose}>
              Cancel
            </button>
            <button
              type="button"
              className="btn btn-accent min-h-12"
              disabled={hasBlocker}
              onClick={() => onProceed(force)}
            >
              <Icon name="play" size={14} className="inline -mt-0.5 mr-1" />Run Sequence
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
