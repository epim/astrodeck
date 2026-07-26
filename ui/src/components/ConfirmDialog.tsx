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
import { Overlay } from "./Overlay";

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
  const confirmBtnRef = useRef<HTMLButtonElement>(null);
  const cancelBtnRef = useRef<HTMLButtonElement>(null);

  const mode: ConfirmMode = req?.mode ?? "confirm";

  // Focus trap / Escape / focus restore now come from <Overlay/>. What stays
  // here is the one behaviour Overlay's generic "focus the first control" rule
  // would get WRONG: initial focus belongs on the SAFEST control (Cancel for
  // confirm/hold, OK for a hard block), not on whatever renders first.
  useEffect(() => {
    if (!req) return;
    const safe = mode === "ok" ? confirmBtnRef.current : (cancelBtnRef.current ?? confirmBtnRef.current);
    safe?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [req]);

  if (!req) return null;

  const danger = req.tone === "danger";
  const confirmLabel = req.confirmLabel ?? (mode === "ok" ? "OK" : "Confirm");
  const cancelLabel = req.cancelLabel ?? "Cancel";
  const primary = req.confirmPrimary ?? false;

  return (
    <Overlay
      open
      role="alertdialog"
      label={req.title}
      variant="center"
      onClose={() => resolve(false)}
      surfaceClassName="sm:max-w-[440px]"
      bodyClassName="p-5"
      foot={
        <div className="flex flex-wrap justify-end gap-2 p-4">
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
      }
    >
      <h2 className={`panel-title mb-2 ${danger ? "!text-bad" : ""}`}>{req.title}</h2>
      {req.body && <p className="text-sm text-ink leading-relaxed break-words">{req.body}</p>}
    </Overlay>
  );
}
