// tooltipMachine — pure open/close state machine for components/ui.tsx Tooltip.
// Split out (rotatorDial.ts precedent) so the npx-tsx assert tests can import
// it under plain Node — no React, no DOM.
//
// Why a machine (Wave-1 spec §4): the old Tooltip opened on mouseenter AND
// toggled on click, so a touch tap (which synthesizes mouseenter+click)
// flashed open-then-shut; and a mouse drifting off the 15px halo closed
// instantly under an unhoverable bubble. Mouse hover now runs through grace
// timers; touch taps toggle exactly once.

export type TooltipEvent =
  | "enter-mouse" | "leave-mouse"   // hover (pointerType === "mouse" only)
  | "tap"                            // touch/pen pointerup
  | "focus" | "blur"                 // keyboard focus (pointer-induced focus suppressed)
  | "escape" | "outside"             // dismissal
  | "open-timer" | "close-timer";    // grace-timer expiry

export interface TooltipState {
  open: boolean;
  pendingOpen: boolean;   // mouse entered; waiting out the open delay
  pendingClose: boolean;  // mouse left while open; waiting out the close grace
}

export const TOOLTIP_IDLE: TooltipState = { open: false, pendingOpen: false, pendingClose: false };
export const TOOLTIP_OPEN_DELAY_MS = 100;
export const TOOLTIP_CLOSE_GRACE_MS = 250;

export function tooltipNext(s: TooltipState, ev: TooltipEvent): TooltipState {
  switch (ev) {
    case "enter-mouse":
      // Re-enter during the close grace keeps it open (kills the drift flicker).
      if (s.open) return { open: true, pendingOpen: false, pendingClose: false };
      return { open: false, pendingOpen: true, pendingClose: false };
    case "open-timer":
      return s.pendingOpen ? { open: true, pendingOpen: false, pendingClose: false } : s;
    case "leave-mouse":
      if (s.pendingOpen) return TOOLTIP_IDLE;
      if (s.open) return { open: true, pendingOpen: false, pendingClose: true };
      return s;
    case "close-timer":
      return s.pendingClose ? TOOLTIP_IDLE : s;
    case "tap":
      // One tap = one toggle. (The old mouseenter+click pair fired both.)
      return { open: !s.open, pendingOpen: false, pendingClose: false };
    case "focus":
      return { open: true, pendingOpen: false, pendingClose: false };
    case "blur":
    case "escape":
    case "outside":
      return TOOLTIP_IDLE;
    default:
      return s;
  }
}
