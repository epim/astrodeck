import { useStore } from "../store";
import { Icon } from "./icons";
import { fmtLogTime, severityWord } from "../lib/logFormat";
import { Overlay } from "./Overlay";

/**
 * Event-log drawer, available on ALL viewports.
 *
 * - lg+        : a docked right column, only when logOpen.
 * - below lg   : a bottom sheet that COEXISTS with the run panel — no dark scrim
 *                that would hide sequence progress; a transparent tap-outside
 *                catcher instead.
 * - a11y       : role="dialog", Escape to close, focus-trap, return-focus to the
 *                control that opened it — all from <Overlay/> now.
 *
 * REVIEW #44: the sheet used `bg-raise/95 backdrop-blur` and stacked with the
 * phone MORE sheet, so "08:23:14 [Info · hub] simulator rig connected" read
 * straight through the "Monitor" nav row. Overlay's `.overlay-surface` is
 * OPAQUE (`--bg-raise`, no alpha), which is the whole point of routing both
 * surfaces through it: two opaque sheets cannot bleed into each other.
 *
 * Both viewport treatments used to be mounted at once and toggled with
 * `hidden lg:flex` / `lg:hidden`, which is why this file carried a pair of refs
 * and a getClientRects() probe to find the visible one. Overlay picks the
 * geometry off a live media query and renders ONE node, so that whole class of
 * "the focus trap ran against a display:none copy" bug is gone.
 *
 * Open/close + the unseen-error badge live in the store (openLog resets
 * unseenError to 0). This component only renders; the LOG button + badge live in
 * the header (App.tsx) and read store.unseenError.
 */

const LEVEL_TONE: Record<string, string> = {
  error: "text-bad", warning: "text-warn",
};

export default function LogDrawer() {
  const logs = useStore((s) => s.logs);
  const logOpen = useStore((s) => s.logOpen);
  const closeLog = useStore((s) => s.closeLog);
  const openHelp = useStore((s) => s.openHelp);

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
            <span className="text-dim">{fmtLogTime(l.ts)}</span>{" "}
            <span className={LEVEL_TONE[l.data.level] ?? "text-accent2"}>
              [{severityWord(l.data.level)} · {l.data.source}]
            </span>{" "}
            <span className="text-ink/90">{l.data.message}</span>
          </span>
        </div>
      ))}
      {logs.length === 0 && <p className="text-dim text-xs">no events yet</p>}
    </div>
  );

  const header = (
    <header className="flex items-center justify-between px-3 py-1.5">
      <h2 className="panel-title">Event Log</h2>
      <button
        type="button"
        aria-label="Close event log"
        onClick={closeLog}
        className="text-dim hover:text-ink p-1 cursor-pointer min-h-[44px] min-w-[44px] flex items-center justify-center"
      >
        <Icon name="x" size={16} />
      </button>
    </header>
  );

  // NOV-9: always-reachable link into the troubleshooting page, on every
  // viewport — the reader doesn't have to already know a diagnosis exists.
  const footer = (
    <button
      type="button"
      onClick={() => { openHelp(); closeLog(); }}
      className="px-3 text-[11px] text-accent hover:underline min-h-[44px] w-full inline-flex items-center gap-1"
    >
      <Icon name="info" size={12} /> Troubleshooting guide →
    </button>
  );

  return (
    <Overlay
      open
      label="Event log"
      variant="dock"
      modal={false}
      scrim={false}
      dismissOnOutside
      trapFocus
      autoFocus
      onClose={closeLog}
      head={header}
      foot={footer}
      bodyClassName="px-3 py-2"
    >
      {rows}
    </Overlay>
  );
}
