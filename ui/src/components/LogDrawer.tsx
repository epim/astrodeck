import { useEffect, useRef } from "react";
import { useStore } from "../store";
import { Icon } from "./icons";

/**
 * Event-log drawer, available on ALL viewports.
 *
 * - lg+        : a docked right column (as today), only when logOpen.
 * - below lg   : a bottom sheet (~55vh) that COEXISTS with the run panel — no
 *                full-screen scrim that would hide sequence progress; a light
 *                tap-outside catcher only over the area above the sheet.
 * - a11y       : role="dialog", Escape to close, focus-trap, return-focus to the
 *                control that opened it.
 *
 * Open/close + the unseen-error badge live in the store (openLog resets
 * unseenError to 0). This component only renders; the LOG button + badge live in
 * the header (App.tsx, owned by 1D) and read store.unseenError.
 */

const LEVEL_TONE: Record<string, string> = {
  error: "text-bad", warning: "text-warn",
};

export default function LogDrawer() {
  const logs = useStore((s) => s.logs);
  const logOpen = useStore((s) => s.logOpen);
  const closeLog = useStore((s) => s.closeLog);

  // Both dialog nodes (desktop docked column + mobile bottom sheet) are ALWAYS
  // mounted — Tailwind toggles them via `hidden lg:flex` / `lg:hidden`
  // (display:none), not by unmounting. A single shared ref would last-write-wins
  // to whichever renders last (the sheet), so on lg+ the focus/trap effect would
  // run against a display:none node and Tab would never trap. Keep one ref each
  // and pick the VISIBLE one at effect time.
  const deskRef = useRef<HTMLDivElement>(null);
  const sheetRef = useRef<HTMLDivElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);

  // Capture the element to return focus to, move focus into the drawer, trap Tab,
  // and restore focus on close.
  useEffect(() => {
    if (!logOpen) return;
    returnFocusRef.current = (document.activeElement as HTMLElement) ?? null;

    // Pick whichever dialog node is actually rendered. The sheet is
    // position:fixed so offsetParent is null even when visible — use
    // getClientRects().length to detect display:none instead.
    const isVisible = (el: HTMLElement | null) => !!el && el.getClientRects().length > 0;
    const node = isVisible(deskRef.current) ? deskRef.current : sheetRef.current;
    const focusables = () =>
      node
        ? Array.from(
            node.querySelectorAll<HTMLElement>(
              'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
            ),
          ).filter((el) => !el.hasAttribute("disabled"))
        : [];

    // Move focus into the drawer (the close button is first).
    focusables()[0]?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        closeLog();
        return;
      }
      if (e.key !== "Tab") return;
      const items = focusables();
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && active === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      returnFocusRef.current?.focus?.();
    };
  }, [logOpen, closeLog]);

  if (!logOpen) return null;

  const rows = (
    <div className="flex flex-col gap-1.5">
      {[...logs].reverse().map((l, i) => (
        <div key={i} className="text-[11px] mono leading-snug flex gap-1.5">
          <Icon
            name={l.data.level === "error" ? "x" : l.data.level === "warning" ? "alert" : "info"}
            size={12}
            className={`${LEVEL_TONE[l.data.level] ?? "text-accent2"} shrink-0 mt-px`}
          />
          <span>
            <span className={LEVEL_TONE[l.data.level] ?? "text-accent2"}>[{l.data.source}]</span>{" "}
            <span className="text-ink/90">{l.data.message}</span>
          </span>
        </div>
      ))}
      {logs.length === 0 && <p className="text-dim text-xs">no events yet</p>}
    </div>
  );

  const header = (
    <header className="flex items-center justify-between mb-2 shrink-0">
      <h2 className="panel-title">Event Log</h2>
      <button
        type="button"
        aria-label="Close event log"
        onClick={closeLog}
        className="text-dim hover:text-ink p-1 cursor-pointer min-h-[44px] sm:min-h-0 flex items-center"
      >
        <Icon name="x" size={16} />
      </button>
    </header>
  );

  return (
    <>
      {/* lg+ docked right column */}
      <aside
        ref={deskRef}
        role="dialog"
        aria-modal="false"
        aria-label="Event log"
        className="hidden lg:flex flex-col w-[340px] border-l border-line bg-raise/60
          backdrop-blur p-3 overflow-y-auto shrink-0"
      >
        {header}
        {rows}
      </aside>

      {/* below lg: bottom sheet that coexists with the run panel.
          A tap-catcher only over the area ABOVE the sheet (does NOT cover the
          sheet itself); no dark scrim so progress underneath stays visible. */}
      <div className="lg:hidden">
        <div
          className="fixed inset-x-0 top-0 bottom-[55vh] z-30"
          onClick={closeLog}
          aria-hidden="true"
        />
        <div
          ref={sheetRef}
          role="dialog"
          aria-modal="false"
          aria-label="Event log"
          className="fixed inset-x-0 bottom-0 z-40 h-[55vh] flex flex-col
            border-t border-line2 bg-raise/95 backdrop-blur p-3 sheet-enter"
        >
          {header}
          <div className="overflow-y-auto flex-1">{rows}</div>
        </div>
      </div>
    </>
  );
}
