// NavMoreSheet.tsx — the "More" overflow sheet (touch spec §5.2).
//
// Lets the mobile bottom nav show 5 primary tabs + a 6th fixed "More" button
// instead of cramming 9 unreadable items. This sheet hosts:
//   - the 3 overflow VIEWS (Guide / Plan / Power), each 56px with icon + label +
//     a live <Led>.
//   - global controls: LOG (outline-ring error badge), Lock Screen (enabled now
//     that lockAvailable is true; degrades to disabled-with-tooltip if it ever
//     isn't), Haptics toggle (HIDDEN when !supported — no dead control on iPad —
//     R26), Reverse-RA / Reverse-Dec, touch-size override.
//   - NIGHT is intentionally NOT here — it lives in the header only (R16).
//
// Dismiss on selection / backdrop / Esc. Fixed grid icon always (R15).
// Narrow store selectors throughout (R27).

import type { ViewName } from "../types";
import { useStore } from "../store";
import { Icon, type IconName } from "./icons";
import { Toggle } from "./ui";
import { Overlay, useMediaQuery } from "./Overlay";
import { haptics } from "../lib/haptics";
import { handleRadioKeyDown, rovingTabIndex } from "../lib/radiogroup";
import {
  useLockAvailable,
  useTouchSettings,
  useSetLocked,
  useSetTouch,
  useMonitorAwake,
  useSetMonitorAwake,
} from "../lib/touchStore";

// Overflow views reachable from the More sheet. Monitor + Settings are appended
// (F-B3) so they are reachable on a phone — Settings was otherwise unreachable and
// Monitor only transiently (via the run banner). The desktop rail already lists
// both; this restores parity on mobile.
export const OVERFLOW_VIEWS: { id: ViewName; label: string; icon: IconName }[] = [
  { id: "guide", label: "Guide", icon: "guide" },
  // Atlas before Plan, mirroring the desktop rail IA (spec §8 mobile "More" overflow).
  { id: "atlas", label: "Atlas", icon: "atlas" },
  // Plan left this sheet with the rail (#239 stage B). On a phone this sheet
  // IS the navigation, so keeping it here would make Plan a primary
  // destination on phones and an advanced one everywhere else. It is reached
  // from Flows and from Settings > Safety, on every form factor.
  { id: "power", label: "Power", icon: "power" },
  { id: "monitor", label: "Monitor", icon: "monitor" },
  // Tonight lands HERE on mobile (polish grab-bag Decision C): the 5-slot primary
  // bar holds the setup-critical tabs (touch spec R14) and reordering it would
  // violate the append-only nav rule (App.tsx Risk-10). So on a phone Tonight
  // costs two taps, and the nav comment says so rather than claiming otherwise.
  // The desktop rail lists it directly.
  { id: "tonight", label: "Tonight", icon: "moon" },
  { id: "settings", label: "Settings", icon: "settings" },
  // Reports is NOT primary nav (App.tsx VIEWS comment) but needs to stay
  // reachable on a phone even with the engine idle — ReportView's own picker
  // browses all past reports from GET /api/reports (report viewer spec §1.4).
  { id: "report", label: "Reports", icon: "download" },
  // NOV-9: troubleshooting/glossary page — mirrors "report", never primary nav.
  { id: "help", label: "Help", icon: "info" },
  // Gallery (2026-08-03). Same standing as Reports and Help: on the desktop rail
  // it is a first-class destination, and it appears HERE too because this sheet
  // is the phone's copy of that rail, not a duplicate of it. The five primary
  // slots stay the setup-critical tabs (touch spec R14), so on a phone browsing
  // the library costs two taps — stated rather than papered over, exactly as the
  // Tonight entry above states its own two-tap cost.
  { id: "gallery", label: "Gallery", icon: "gallery" },
  // Flows (milestone 2). Same standing as Gallery, Reports and Help: a
  // first-class rail destination on desktop, and HERE because this sheet is the
  // phone's copy of that rail, not a duplicate of it. The five primary slots
  // stay the setup-critical tabs (touch spec R14), so building a flow on a
  // phone costs two taps — stated, not papered over.
  { id: "flows", label: "Flows", icon: "bridge" },
];

const SIZING: { id: "auto" | "on" | "off"; label: string }[] = [
  { id: "auto", label: "Auto" },
  { id: "on", label: "Large" },
  { id: "off", label: "Off" },
];

function OverflowRow({ id, label, icon, onPick }: {
  id: ViewName; label: string; icon: IconName; onPick: (v: ViewName) => void;
}) {
  const active = useStore((s) => s.view === id);
  // small liveness dot per overflow view (guide guiding / seq running / power n/a)
  const led = useStore((s) => {
    if (id === "guide") return s.status?.guider?.guiding ? "on" : "off";
    if (id === "sequence") {
      const st = s.sequence.state;
      return st === "error" ? "bad" : st === "running" ? "busy" : st === "paused" ? "warn" : "off";
    }
    return "off";
  });
  return (
    <button
      onClick={() => onPick(id)}
      className={`flex items-center gap-3 w-full min-h-[56px] px-3 border-b border-line/60
        ${active ? "text-accent bg-accent/10" : "text-ink"}`}
    >
      <Icon name={icon} size={24} />
      <span className="font-display tracking-wide text-sm flex-1 text-left">{label}</span>
      <span className={`led led-${led}`} aria-hidden />
    </button>
  );
}

export default function NavMoreSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const setView = useStore((s) => s.setView);
  const openLog = useStore((s) => s.openLog);
  const unseenError = useStore((s) => s.unseenError);

  const lockAvailable = useLockAvailable();
  const setLocked = useSetLocked();
  const touch = useTouchSettings();
  const setTouch = useSetTouch();
  const monitorAwake = useMonitorAwake();
  const setMonitorAwake = useSetMonitorAwake();

  // Focus trap / initial focus / Escape / focus-restore + the portal + the
  // OPAQUE surface all come from <Overlay/>. Review #44: this sheet used
  // `.panel` (`--bg-panel` = rgba(12,14,22,0.85)) so an open event-log drawer
  // read straight through the nav rows underneath it — two translucent overlays
  // stacked. `.overlay-surface` has no alpha, which fixes it at the primitive.
  //
  // The sheet used to carry `sm:hidden` on its root. It now renders through a
  // portal, so the breakpoint has to be a real query rather than a utility on a
  // node that no longer wraps the scrim: a phone-width-only sheet must not
  // survive a rotate/resize past `sm` with its scrim covering the desktop rail.
  const wideScreen = useMediaQuery("(min-width: 640px)");
  if (!open || wideScreen) return null;

  const pick = (v: ViewName) => {
    setView(v);
    onClose();
  };

  return (
    <Overlay
      open
      label="More"
      variant="sheet"
      onClose={onClose}
      head={
        <div className="flex items-center justify-between px-4 py-3">
          <span className="font-display tracking-[0.2em] text-dim text-xs">MORE</span>
          <button
            onClick={onClose}
            aria-label="Close"
            className="tap min-h-[44px] min-w-[44px] inline-flex items-center justify-center text-dim"
          >
            <Icon name="x" size={18} />
          </button>
        </div>
      }
    >
        {/* overflow views */}
        <div>
          {OVERFLOW_VIEWS.map((v) => (
            <OverflowRow key={v.id} {...v} onPick={pick} />
          ))}
        </div>

        {/* global controls */}
        <div className="px-3 py-2 flex flex-col gap-1">
          {/* LOG with outline-ring error badge */}
          <button
            onClick={() => {
              openLog();
              onClose();
            }}
            className="flex items-center gap-3 w-full min-h-[56px] px-1"
          >
            <Icon name="alert" size={24} />
            <span className="font-display tracking-wide text-sm flex-1 text-left">Event Log</span>
            {unseenError > 0 && (
              <span className="inline-flex items-center justify-center min-w-[20px] h-5 px-1.5
                rounded-full border border-bad text-bad text-[10px] font-bold tabular-nums">
                {unseenError > 99 ? "99+" : unseenError}
              </span>
            )}
          </button>

          {/* Lock Screen. Review #24 + house rule §11.8: this used the NATIVE
              `disabled` attribute with the reason living only in `title=` — the
              one channel that never fires on the touch device this sheet exists
              for, and `disabled` strips the control (and therefore the reason)
              out of the a11y tree entirely. Now: dim + aria-disabled + a VISIBLE
              stated reason, matching LockedNote's shape. */}
          <button
            onClick={() => {
              if (!lockAvailable) return;
              haptics.warn();
              setLocked(true);
              onClose();
            }}
            aria-disabled={!lockAvailable || undefined}
            aria-label={lockAvailable ? undefined : "Lock Screen — unavailable on this display"}
            title={
              lockAvailable
                ? "Lock the screen (monitor-safe)"
                : "Screen lock unavailable"
            }
            className={`flex items-center gap-3 w-full min-h-[56px] px-1 ${
              lockAvailable ? "" : "opacity-50 cursor-not-allowed"
            }`}
          >
            <Icon name="lock" size={24} />
            <span className="font-display tracking-wide text-sm flex-1 text-left">Lock Screen</span>
          </button>
          {!lockAvailable && (
            <p className="flex items-center gap-1.5 text-[11px] text-dim px-1 pb-1">
              <Icon name="lock" size={12} aria-hidden />
              <span>Screen lock is unavailable on this display.</span>
            </p>
          )}

          {/* Monitor-awake (keep screen on while watching) — decoupled from lock */}
          <label className="flex items-center gap-3 w-full min-h-[56px] px-1">
            <Icon name="monitor" size={24} />
            <span className="font-display tracking-wide text-sm flex-1 text-left">Keep Awake</span>
            <Toggle
              checked={monitorAwake}
              onChange={setMonitorAwake}
              label="Keep screen awake as a monitor"
            />
          </label>

          {/* Haptics — HIDDEN entirely when unsupported (no dead control — R26) */}
          {haptics.supported && (
            <label className="flex items-center gap-3 w-full min-h-[56px] px-1">
              <Icon name="bridge" size={24} />
              <span className="font-display tracking-wide text-sm flex-1 text-left">Haptics</span>
              <Toggle
                checked={touch.hapticsEnabled}
                onChange={(v) => setTouch({ hapticsEnabled: v })}
                label="Haptic feedback"
              />
            </label>
          )}

          {/* Reverse-axis toggles */}
          <label className="flex items-center gap-3 w-full min-h-[56px] px-1">
            <Icon name="arrow-left" size={24} />
            <span className="font-display tracking-wide text-sm flex-1 text-left">Reverse RA</span>
            <Toggle
              checked={touch.reverseRa}
              onChange={(v) => setTouch({ reverseRa: v })}
              label="Reverse RA slew direction"
            />
          </label>
          <label className="flex items-center gap-3 w-full min-h-[56px] px-1">
            <Icon name="arrow-up" size={24} />
            <span className="font-display tracking-wide text-sm flex-1 text-left">Reverse Dec</span>
            <Toggle
              checked={touch.reverseDec}
              onChange={(v) => setTouch({ reverseDec: v })}
              label="Reverse Dec slew direction"
            />
          </label>

          {/* touch-size override (auto/on/off) */}
          <div className="flex items-center gap-3 w-full min-h-[56px] px-1">
            <Icon name="capture" size={24} />
            <span className="font-display tracking-wide text-sm flex-1 text-left">Touch size</span>
            <div className="inline-flex gap-1" role="radiogroup" aria-label="Touch target size">
              {SIZING.map((s, i) => {
                const activeIndex = SIZING.findIndex((o) => o.id === touch.touchSizing);
                const select = (idx: number) => setTouch({ touchSizing: SIZING[idx].id });
                return (
                <button
                  key={s.id}
                  role="radio"
                  aria-checked={touch.touchSizing === s.id}
                  // UX-20: roving tabindex + shared arrow-key model
                  tabIndex={rovingTabIndex(i, activeIndex)}
                  onClick={() => select(i)}
                  onKeyDown={(e) => handleRadioKeyDown(e, i, SIZING.length, select)}
                  className={`btn !py-1 !px-2 !text-[11px] min-h-[44px] ${
                    touch.touchSizing === s.id ? "!border-accent !text-accent bg-accent/10" : ""
                  }`}
                >
                  {s.label}
                </button>
                );
              })}
            </div>
          </div>
        </div>
    </Overlay>
  );
}
