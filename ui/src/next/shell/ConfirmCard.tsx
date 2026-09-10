// ConfirmCard.tsx - the design's bottom confirm card (ARCHITECTURE.md section 5).
//
// It reads the SAME `store.confirm` slice that `confirmDialog()` writes, so
// every reused component's `await confirmDialog({...})` keeps working with no
// change at the call site - and the legacy `ConfirmHost` is NOT mounted beside
// it, because two hosts on one slice would both render the same question.
//
// Three modes, from `components/ConfirmDialog.tsx`:
//   "ok"      a hard block - one dismiss, resolves false (there is no proceed)
//   "confirm" KEEP / the destructive verb
//   "hold"    the destructive verb has to be held down; the legacy `HoldButton`
//             is reused whole, because its frame-credit cap, its blur cancel and
//             its keyboard arm-then-confirm path are each a fixed bug with a
//             test, and a second implementation would start again from zero.
//
// Initial focus goes to the SAFEST control - KEEP for confirm/hold, the single
// dismiss for a hard block. Escape resolves false.

import { useEffect, useRef, type JSX } from "react";
import { useStore, useConfirm } from "../../store";
import { HoldButton } from "../../components/ui";

export function ConfirmCard(): JSX.Element | null {
  const req = useConfirm();
  const resolve = useStore((s) => s.resolveConfirm);
  const keepRef = useRef<HTMLButtonElement>(null);
  const okRef = useRef<HTMLButtonElement>(null);

  const mode = req?.mode ?? "confirm";

  useEffect(() => {
    if (!req) return;
    const safe = mode === "ok" ? okRef.current : (keepRef.current ?? okRef.current);
    safe?.focus();
    // Re-run per request, not per mode: a second question replacing a first
    // must take the focus with it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [req]);

  useEffect(() => {
    if (!req) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      e.stopPropagation();
      resolve(false);
    };
    // Capture phase: a sheet is also listening for Escape, and the question on
    // top of it is what Escape means while it is up.
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [req, resolve]);

  if (!req) return null;

  const danger = req.tone === "danger";
  const confirmLabel = req.confirmLabel ?? (mode === "ok" ? "OK" : "CONFIRM");
  const cancelLabel = req.cancelLabel ?? "KEEP";

  return (
    <div
      className="nx-confirm-scrim"
      role="alertdialog"
      aria-modal="true"
      aria-label={req.title}
      data-testid="confirm-scrim"
      onClick={(e) => { if (e.target === e.currentTarget) resolve(false); }}
    >
      <div className="nx-confirm-card" data-tone={danger ? "danger" : req.tone ?? "warn"}>
        <div className="nx-confirm-title">{req.title}</div>
        {req.body != null && <div className="nx-confirm-body">{req.body}</div>}
        <div className="nx-confirm-row">
          {/* Plain buttons, not the ActionButton primitive: the card's two
              controls are 48 px (between the primitive's md 44 and lg 52), and
              initial focus has to land on the safest one, which needs a ref the
              primitive does not expose. */}
          {mode !== "ok" && (
            <button
              ref={keepRef}
              type="button"
              className="nx-confirm-btn"
              data-kind="keep"
              onClick={() => resolve(false)}
              data-testid="confirm-keep"
            >
              {cancelLabel}
            </button>
          )}

          {mode === "ok" && (
            <button
              ref={okRef}
              type="button"
              className="nx-confirm-btn"
              data-kind="ok"
              onClick={() => resolve(false)}
              data-testid="confirm-ok"
            >
              {confirmLabel}
            </button>
          )}

          {mode === "confirm" && (
            <button
              ref={okRef}
              type="button"
              className="nx-confirm-btn"
              data-kind={danger ? "danger" : "ok"}
              onClick={() => resolve(true)}
              data-testid="confirm-yes"
            >
              {confirmLabel}
            </button>
          )}

          {mode === "hold" && (
            <HoldButton label={confirmLabel} onConfirm={() => resolve(true)}>
              {(bind) => (
                <button
                  ref={okRef}
                  type="button"
                  className="nx-hold"
                  aria-label={bind["aria-label"]}
                  data-testid="confirm-hold"
                  onPointerDown={bind.onPointerDown}
                  onPointerUp={bind.onPointerUp}
                  onPointerCancel={bind.onPointerUp}
                  onKeyDown={bind.onKeyDown}
                  onKeyUp={bind.onKeyUp}
                  onBlur={bind.onBlur}
                >
                  <span
                    aria-hidden="true"
                    className="nx-hold-fill"
                    style={{ width: `${Math.round(bind.progress * 100)}%` }}
                  />
                  <span className="nx-hold-text">{bind.armed ? bind.hintLabel : confirmLabel}</span>
                </button>
              )}
            </HoldButton>
          )}
        </div>
      </div>
    </div>
  );
}
