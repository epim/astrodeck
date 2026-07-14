// tooltipPlace — pure placement math for the components/ui.tsx Tooltip bubble.
// Split out (tooltipMachine.ts precedent) so the npx-tsx assert tests can import
// it under plain Node — no React, no DOM. Takes plain rects/sizes in viewport
// coordinates and returns a viewport-clamped { left, top, side } for a
// position:fixed bubble.
//
// Contract: center the bubble on the trigger along the cross axis; place it on
// the preferred side; if it would not fit within `pad` of the viewport on that
// side, flip to the opposite side; then clamp both axes so the bubble stays
// within `pad` of every viewport edge (clamp, never scale, never off-screen).

export interface Rect { left: number; top: number; width: number; height: number }
export type Side = "top" | "bottom" | "left" | "right";
export interface Placement { left: number; top: number; side: Side }

const OPPOSITE: Record<Side, Side> = {
  top: "bottom", bottom: "top", left: "right", right: "left",
};

// Clamp v into [lo, hi]. When the viewport is smaller than bubble + 2*pad the
// range inverts (hi < lo); the low edge (pad) wins, so we never go past `pad`.
function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(v, hi));
}

// Does the bubble fit within `pad` of the viewport when placed on `side`?
function fits(
  side: Side, trigger: Rect, bubble: { width: number; height: number },
  viewport: { width: number; height: number }, gap: number, pad: number,
): boolean {
  switch (side) {
    case "top":    return trigger.top - gap - bubble.height >= pad;
    case "bottom": return trigger.top + trigger.height + gap + bubble.height <= viewport.height - pad;
    case "left":   return trigger.left - gap - bubble.width >= pad;
    case "right":  return trigger.left + trigger.width + gap + bubble.width <= viewport.width - pad;
  }
}

export function placeTooltip(opts: {
  trigger: Rect;
  bubble: { width: number; height: number };
  viewport: { width: number; height: number };
  side?: Side;
  gap?: number;
  pad?: number;
}): Placement {
  const { trigger, bubble, viewport } = opts;
  const gap = opts.gap ?? 6;
  const pad = opts.pad ?? 8;
  const preferred: Side = opts.side ?? "top";

  // Flip to the opposite side if the preferred side does not fit. If neither
  // fits (tiny viewport) we keep the flipped side and let the clamp below pin
  // the main axis — the brief's "keep the flipped side" rule.
  const side: Side = fits(preferred, trigger, bubble, viewport, gap, pad)
    ? preferred
    : OPPOSITE[preferred];

  // Cross-axis centering on the trigger.
  const centerX = trigger.left + trigger.width / 2 - bubble.width / 2;
  const centerY = trigger.top + trigger.height / 2 - bubble.height / 2;

  let left: number;
  let top: number;
  switch (side) {
    case "top":
      left = centerX;
      top = trigger.top - gap - bubble.height;
      break;
    case "bottom":
      left = centerX;
      top = trigger.top + trigger.height + gap;
      break;
    case "left":
      left = trigger.left - gap - bubble.width;
      top = centerY;
      break;
    case "right":
      left = trigger.left + trigger.width + gap;
      top = centerY;
      break;
  }

  // Clamp both axes. On the cross axis this pins a near-edge trigger inside the
  // viewport; on the main axis it is a no-op when the side fits and the pin for
  // the tiny-viewport case. Never scale — clamp only.
  left = clamp(left, pad, viewport.width - pad - bubble.width);
  top = clamp(top, pad, viewport.height - pad - bubble.height);

  return { left, top, side };
}
