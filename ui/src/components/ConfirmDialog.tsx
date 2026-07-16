// ConfirmDialog.tsx — promise-based confirm modal + the singleton <ConfirmHost/>
// (onboarding spec §1e). Resolves critique3 #2 (focus trap / initial focus /
// Escape / focus return) and #12 (a hard block uses a plain single OK — never a
// hold, since hold must only confirm dialogs that FIRE an action).
//
// The store already owns the `confirm` slice (ConfirmRequest, pushConfirm,
// resolveConfirm, useConfirm). `confirmDialog()` is a thin imperative wrapper
// over pushConfirm so any non-React call site can `await confirmDialog({...})`.
// <ConfirmHost/> renders the active request; the integrate phase mounts it once
// in App.tsx (that wiring is NOT in this lane).

import { useEffect, useRef, type ReactNode } from "react";
import { useStore, useConfirm } from "../store";
import { HoldButton } from "./ui";

export type ConfirmMode = "ok" | "confirm" | "hold";

export interface ConfirmOpts {
  title: string;
  body?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: "warn" | "danger";
  // "ok"      = single dismiss, resolves false (a hard block — no proceed path)
  // "confirm" = OK / Cancel
  // "hold"    = HoldButton proceed (only when the dialog FIRES an action)
  mode?: ConfirmMode;
  // See ConfirmRequest.confirmPrimary (store.ts) — makes the affirmative
  // button read as primary instead of Cancel (PLAN-01-gemini).
  confirmPrimary?: boolean;
}

/**
 * Imperative, promise-based confirm. Resolves true when the user proceeds,
 * false on cancel / dismiss / Escape. Routes through the store's `confirm` slice
 * so a single <ConfirmHost/> renders it.
 *
 * The store's ConfirmRequest.body is now `ReactNode` (F-D1) and ConfirmHost
 * renders `{req.body}` as-is, so rich bodies pass through unchanged — no longer
 * silently coerced/dropped at this boundary.
 */
export function confirmDialog(opts: ConfirmOpts): Promise<boolean> {
  return useStore.getState().pushConfirm({
    title: opts.title,
    body: opts.body,
    confirmLabel: opts.confirmLabel,
    cancelLabel: opts.cancelLabel,
    tone: opts.tone,
    mode: opts.mode ?? "confirm",
    confirmPrimary: opts.confirmPrimary,
  });
}

/**
 * The singleton modal host. Reads the active confirm request and renders a
 * focus-trapped dialog. Mount ONCE near the app root (done by the integrator).
 */
export function ConfirmHost() {
  const req = useConfirm();
  const resolve = useStore((s) => s.resolveConfirm);
  const panelRef = useRef<HTMLDivElement>(null);
  const confirmBtnRef = useRef<HTMLButtonElement>(null);
  const cancelBtnRef = useRef<HTMLButtonElement>(null);
  const openerRef = useRef<HTMLElement | null>(null);

  const mode: ConfirmMode = req?.mode ?? "confirm";

  // Capture the opener and restore focus on close.
  useEffect(() => {
    if (!req) return;
    openerRef.current = (document.activeElement as HTMLElement) ?? null;
    // initial focus on the SAFEST control (Cancel for confirm/hold; OK for "ok").
    const safe = mode === "ok" ? confirmBtnRef.current : (cancelBtnRef.current ?? confirmBtnRef.current);
    safe?.focus();
    return () => {
      openerRef.current?.focus?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [req]);

  // Escape cancels; Tab is trapped inside the panel.
  useEffect(() => {
    if (!req) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        resolve(false);
        return;
      }
      if (e.key !== "Tab") return;
      const panel = panelRef.current;
      if (!panel) return;
      const focusables = panel.querySelectorAll<HTMLElement>(
        'button:not([disabled]), a[href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      );
      if (focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [req, resolve]);

  if (!req) return null;

  const danger = req.tone === "danger";
  const confirmLabel = req.confirmLabel ?? (mode === "ok" ? "OK" : "Confirm");
  const cancelLabel = req.cancelLabel ?? "Cancel";
  const primary = req.confirmPrimary ?? false;

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center p-4"
      role="presentation"
      onPointerDown={(e) => {
        // outside-scrim click cancels
        if (e.target === e.currentTarget) resolve(false);
      }}
    >
      <div className="fixed inset-0 bg-black/70" aria-hidden />
      <div
        ref={panelRef}
        role="alertdialog"
        aria-modal="true"
        aria-label={req.title}
        className="panel relative z-[61] w-full max-w-[440px] p-5 sheet-enter"
      >
        <h2 className={`panel-title mb-2 ${danger ? "!text-bad" : ""}`}>{req.title}</h2>
        {req.body && <p className="text-sm text-ink leading-relaxed mb-4 break-words">{req.body}</p>}

        <div className="flex flex-wrap justify-end gap-2 mt-2">
          {mode !== "ok" && (
            <button
              ref={cancelBtnRef}
              type="button"
              className={`btn ${primary ? "btn-confirm-muted" : ""}`}
              onClick={() => resolve(false)}
            >
              {cancelLabel}
            </button>
          )}

          {mode === "ok" && (
            <button ref={confirmBtnRef} type="button" className="btn btn-accent" onClick={() => resolve(false)}>
              {confirmLabel}
            </button>
          )}

          {mode === "confirm" && (
            <button
              ref={confirmBtnRef}
              type="button"
              className={`btn ${primary ? "btn-confirm-primary" : danger ? "btn-danger" : "btn-accent"}`}
              onClick={() => resolve(true)}
            >
              {confirmLabel}
            </button>
          )}

          {mode === "hold" && (
            <HoldButton label={typeof confirmLabel === "string" ? confirmLabel : "confirm"} onConfirm={() => resolve(true)}>
              {(bind) => (
                <button
                  ref={confirmBtnRef}
                  type="button"
                  className="btn btn-danger relative overflow-hidden select-none"
                  style={{ touchAction: "none" }}
                  aria-label={bind["aria-label"]}
                  onPointerDown={bind.onPointerDown}
                  onPointerUp={bind.onPointerUp}
                  onPointerCancel={bind.onPointerUp}
                  onKeyDown={bind.onKeyDown}
                  onKeyUp={bind.onKeyUp}
                >
                  {/* luminance-based, hue-independent progress fill (light-on-dark) */}
                  <span
                    aria-hidden
                    className="absolute inset-y-0 left-0 confirmhold-fill pointer-events-none"
                    style={{
                      width: `${Math.round(bind.progress * 100)}%`,
                      background: "color-mix(in srgb, var(--text) 70%, transparent)",
                      transition: "width 80ms linear",
                    }}
                  />
                  <span className="relative">{bind.armed ? bind.hintLabel : confirmLabel}</span>
                </button>
              )}
            </HoldButton>
          )}
        </div>
      </div>
    </div>
  );
}
