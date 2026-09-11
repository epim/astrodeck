// Toasts.tsx - the new UI's toast stack (ARCHITECTURE.md section 5).
//
// Reads the SAME `store.toasts` the legacy `components/Toasts.tsx` reads, so
// every reused panel's `enqueueToast` lands here with no rewiring, and the
// store's dedupe / coalesce / severity-eviction rules keep applying.
//
// The design says 2.8 s. The STORE's ttl is honoured instead, and this is a
// deliberate deviation stated in the contract: a sticky toast (`ttl === 0`) is
// how an UNSAFE / sequence-fatal message is delivered, and 2.8 seconds is long
// enough to miss it while looking at the sky. The sweeper below never touches a
// sticky one - `dismissExpired` in the store is what decides, and it preserves
// them - so the deviation lives in one place rather than in a timer here.
//
// Top-centre on phone (clear of the tab bar), bottom-right at tablet and
// desktop, where the corner is empty and the middle is the work.

import { useEffect, type JSX } from "react";
import { useStore } from "../../store";
import type { Toast, ToastLevel } from "../../types";
import type { Breakpoint } from "../breakpoint";
import { nav } from "../router";
import { ActionButton } from "../ui";
import type { Tone } from "../ui";

const TONE: Record<ToastLevel, Tone> = {
  error: "bad", warning: "warn", info: "accent", success: "good",
};

/** The word, not only the border colour: under `:root.night` every token is the
 *  same red, so a tone that only paints a 1 px edge says nothing. */
const WORD: Record<ToastLevel, string> = {
  error: "FAILED", warning: "WARNING", info: "NOTE", success: "DONE",
};

function ToastCard({ t }: { t: Toast }): JSX.Element {
  const dismissToast = useStore((s) => s.dismissToast);
  const openLog = useStore((s) => s.openLog);
  const openHelp = useStore((s) => s.openHelp);

  return (
    <div className="nx-toast" data-tone={TONE[t.level]} data-testid={`toast-${t.level}`}>
      <span className="nx-toast-level">{WORD[t.level]}</span>
      <span className="nx-toast-body">
        <span className="nx-toast-title">
          {t.title}
          {t.count > 1 && <span className="nx-sr"> ({t.count} times)</span>}
          {t.count > 1 && <span aria-hidden="true"> x{t.count}</span>}
        </span>
        {t.detail && <span className="nx-toast-detail">{t.detail}</span>}
        {t.action && (
          <span className="nx-toast-action">
            <ActionButton
              kind="secondary"
              onPress={() => {
                // VIEW LOG has to NAVIGATE, not only write a flag (review #6).
                // `store.openLog()` sets `logOpen`, and `components/LogDrawer`
                // - the only thing that ever read it - is not mounted under
                // this root. So the sticky toast raised for every sequence
                // fatal (`store.ts:2029`, ttl 0) carried a button that did
                // nothing at all, on screen, after the worst thing that can
                // happen to a run.
                //
                // The route first, then `openLog()`: the store action is what
                // clears `unseenError`, so dropping it would leave the badge
                // lit over a log the user is looking at. This is exactly what
                // the sibling path already does (`incidentActions.ts:196`).
                if (t.action?.kind === "openLog") { nav.go("/monitor/log"); openLog(); }
                else if (t.action?.kind === "openHelp") openHelp(t.action.topic);
              }}
            >
              {t.action.label}
            </ActionButton>
          </span>
        )}
      </span>
      <button
        type="button"
        className="nx-toast-x"
        aria-label="Dismiss this message"
        onClick={() => dismissToast(t.id)}
      >
        &times;
      </button>
    </div>
  );
}

export function Toasts({ bp }: { bp: Breakpoint }): JSX.Element | null {
  const toasts = useStore((s) => s.toasts);

  // One shared 500 ms sweeper for the whole stack, exactly as the legacy host
  // does it: the store filters and removes every expired id in a single set(),
  // so toasts do not dribble away one per tick.
  useEffect(() => {
    const id = window.setInterval(() => useStore.getState().dismissExpired(), 500);
    return () => window.clearInterval(id);
  }, []);

  if (toasts.length === 0) return null;

  const newest = toasts[toasts.length - 1];

  return (
    <div
      className="nx-toasts"
      data-bp={bp}
      aria-live={newest.level === "error" ? "assertive" : "polite"}
      aria-atomic="false"
      data-testid="toasts"
    >
      {toasts.map((t) => <ToastCard key={t.id} t={t} />)}
    </div>
  );
}
