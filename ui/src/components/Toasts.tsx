import { useEffect } from "react";
import { useStore } from "../store";
import { Icon } from "./icons";
import type { IconName } from "./icons";
import type { Toast, ToastLevel } from "../types";

/**
 * Renders the store toast queue.
 *
 * - Top-center on phone (clear of the bottom-nav / Abort row); bottom-right on
 *   desktop.
 * - A single shared aria-live region wraps the stack: assertive only when the
 *   newest toast is an error, else polite.
 * - One shared 500ms TTL sweeper batches all expired ids into a single dismiss
 *   pass so toasts don't dribble away one-per-tick. Sticky toasts (ttl===0) are
 *   never swept — that is the one focal sequence-fatal toast.
 * - Error toasts do NOT blink and are NOT full-saturation: border + icon carry
 *   the hue, the fill is a neutral-dark panel, so a sticky failure doesn't strobe
 *   dark-adapted eyes.
 */

const LEVEL_ICON: Record<ToastLevel, IconName> = {
  error: "x", warning: "alert", info: "info", success: "check",
};

const LEVEL_TEXT: Record<ToastLevel, string> = {
  error: "text-bad", warning: "text-warn", info: "text-accent", success: "text-good",
};

const LEVEL_BORDER: Record<ToastLevel, string> = {
  error: "border-bad/60", warning: "border-warn/60",
  info: "border-line2", success: "border-good/60",
};

function ToastCard({ t }: { t: Toast }) {
  const dismissToast = useStore((s) => s.dismissToast);
  const openLog = useStore((s) => s.openLog);

  return (
    <div
      className={`panel border ${LEVEL_BORDER[t.level]} bg-raise/95 backdrop-blur px-3 py-2.5
        w-full sm:w-[360px] pointer-events-auto`}
    >
      <div className="flex items-start gap-2.5">
        <Icon name={LEVEL_ICON[t.level]} size={16} className={`${LEVEL_TEXT[t.level]} mt-0.5 shrink-0`} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="text-sm text-ink leading-snug">
              {t.title}
              {/* SR-announced count so a coalescing/flapping toast isn't silent;
                  the visible chip below stays aria-hidden (visual redundancy). */}
              {t.count > 1 && <span className="sr-only"> ({t.count} times)</span>}
            </span>
            {t.count > 1 && (
              <span aria-hidden="true"
                className="mono text-[10px] text-dim border border-line px-1 leading-tight shrink-0">
                ×{t.count}
              </span>
            )}
          </div>
          {t.detail && (
            <p className="text-xs text-ink/85 leading-snug mt-0.5">{t.detail}</p>
          )}
        </div>
        <button
          type="button"
          aria-label="Dismiss"
          onClick={() => dismissToast(t.id)}
          className="text-dim hover:text-ink shrink-0 -mr-1 -mt-1 p-1 cursor-pointer"
        >
          <Icon name="x" size={14} />
        </button>
      </div>
      {t.action && (
        <div className="flex flex-col gap-2 mt-2.5">
          <button
            type="button"
            onClick={() => { if (t.action!.kind === "openLog") openLog(); }}
            className="btn !py-0 min-h-[44px] sm:min-h-0 sm:!py-1.5 w-full sm:w-auto"
          >
            {t.action.label}
          </button>
        </div>
      )}
    </div>
  );
}

export default function Toasts() {
  const toasts = useStore((s) => s.toasts);

  // Single shared sweeper: dismissExpired() does the expiry filter in the store
  // and removes all expired ids in one set() (deps [] so it is never torn down
  // mid-sweep). Sticky toasts (ttl===0) are preserved by dismissExpired.
  useEffect(() => {
    const id = window.setInterval(() => useStore.getState().dismissExpired(), 500);
    return () => window.clearInterval(id);
  }, []);

  if (toasts.length === 0) return null;

  const newest = toasts[toasts.length - 1];
  const assertive = newest.level === "error";

  return (
    <div
      aria-live={assertive ? "assertive" : "polite"}
      aria-atomic="false"
      className="fixed z-40 flex flex-col gap-2 pointer-events-none
        top-14 left-3 right-3
        sm:top-auto sm:bottom-6 sm:right-6 sm:left-auto sm:w-[360px]"
    >
      {toasts.map((t) => <ToastCard key={t.id} t={t} />)}
    </div>
  );
}
