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

import { useEffect, useRef } from "react";
import type { ViewName } from "../types";
import { useStore } from "../store";
import { Icon, type IconName } from "./icons";
import { Toggle } from "./ui";
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
  { id: "sequence", label: "Plan", icon: "plan" },
  { id: "power", label: "Power", icon: "power" },
  { id: "monitor", label: "Monitor", icon: "monitor" },
  { id: "settings", label: "Settings", icon: "settings" },
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

  // Focus management (F-focus-trap, mirrors the ConfirmHost primitive): capture the
  // opener, focus the first control inside the sheet on open, trap Tab within it,
  // and restore focus to the opener on close. Escape closes.
  const panelRef = useRef<HTMLDivElement>(null);
  const openerRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    openerRef.current = (document.activeElement as HTMLElement) ?? null;
    // initial focus: the first focusable control inside the sheet panel.
    const panel = panelRef.current;
    const first = panel?.querySelector<HTMLElement>(
      'button:not([disabled]), a[href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    );
    first?.focus();
    return () => {
      openerRef.current?.focus?.();
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
        return;
      }
      if (e.key !== "Tab") return;
      const panel = panelRef.current;
      if (!panel) return;
      const focusables = panel.querySelectorAll<HTMLElement>(
        'button:not([disabled]), a[href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      );
      if (focusables.length === 0) return;
      const firstEl = focusables[0];
      const lastEl = focusables[focusables.length - 1];
      if (e.shiftKey && document.activeElement === firstEl) {
        e.preventDefault();
        lastEl.focus();
      } else if (!e.shiftKey && document.activeElement === lastEl) {
        e.preventDefault();
        firstEl.focus();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [open, onClose]);

  if (!open) return null;

  const pick = (v: ViewName) => {
    setView(v);
    onClose();
  };

  return (
    <div
      className="fixed inset-0 z-40 sm:hidden flex flex-col justify-end"
      role="dialog"
      aria-modal="true"
      aria-label="More"
    >
      {/* backdrop dismiss */}
      <button
        aria-label="Close menu"
        className="absolute inset-0 bg-bg/60"
        onClick={onClose}
      />
      <div ref={panelRef} className="relative panel bg-panel rounded-t-xl border-t border-line2 max-h-[80vh] overflow-y-auto more-sheet-in">
        <div className="flex items-center justify-between px-4 py-3 border-b border-line">
          <span className="font-display tracking-[0.2em] text-dim text-xs">MORE</span>
          <button
            onClick={onClose}
            aria-label="Close"
            className="tap min-h-[44px] min-w-[44px] inline-flex items-center justify-center text-dim"
          >
            <Icon name="x" size={18} />
          </button>
        </div>

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

          {/* Lock Screen — gated on lockAvailable with tooltip-as-title */}
          <button
            onClick={() => {
              if (!lockAvailable) return;
              haptics.warn();
              setLocked(true);
              onClose();
            }}
            disabled={!lockAvailable}
            title={
              lockAvailable
                ? "Lock the screen (monitor-safe)"
                : "Screen lock unavailable"
            }
            className={`flex items-center gap-3 w-full min-h-[56px] px-1 ${
              lockAvailable ? "" : "opacity-40 cursor-not-allowed"
            }`}
          >
            <Icon name="lock" size={24} />
            <span className="font-display tracking-wide text-sm flex-1 text-left">Lock Screen</span>
          </button>

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
      </div>
    </div>
  );
}
