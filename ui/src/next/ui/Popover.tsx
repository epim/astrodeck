import { useEffect, useLayoutEffect, useRef, useState, type JSX, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";

const ROOT_ID = "nx-popover-root";
const GUTTER = 8;

/** The host node, created on first use. A popover that has to wait for the
 *  shell to render a container is a popover that does not open on the first
 *  tap, so this makes its own. */
function popoverRoot(): HTMLElement | null {
  if (typeof document === "undefined") return null;
  let el = document.getElementById(ROOT_ID);
  if (!el) {
    el = document.createElement("div");
    el.id = ROOT_ID;
    document.body.appendChild(el);
  }
  return el;
}

/** Layers, profiles, the lens dial's helpers. Anchored under its trigger,
 *  flipped above when there is no room below, clamped to the viewport, and
 *  dismissed by a tap outside or Escape.
 *
 *  Dismiss listens on `pointerdown`, not `click`: a `click` listener fires
 *  AFTER the press has already landed on whatever is underneath, which on a
 *  phone means the tap that closes the popover also presses the button behind
 *  it. */
export function Popover({ open, anchorRef, onClose, children, align = "start", className = "", ...rest }: {
  open: boolean;
  anchorRef: RefObject<HTMLElement | null>;
  onClose: () => void;
  children: ReactNode;
  align?: "start" | "end";
  className?: string;
  "data-testid"?: string;
}): JSX.Element | null {
  const panel = useRef<HTMLDivElement | null>(null);
  const [pos, setPos] = useState<{ top: number; left: number; above: boolean }>({ top: 0, left: 0, above: false });

  useLayoutEffect(() => {
    if (!open) return;
    const a = anchorRef.current;
    if (!a || typeof window === "undefined") return;
    const r = a.getBoundingClientRect();
    const box = panel.current?.getBoundingClientRect();
    const w = box?.width ?? 0;
    const h = box?.height ?? 0;
    const vw = window.innerWidth || 0;
    const vh = window.innerHeight || 0;

    const below = r.bottom + GUTTER;
    const above = h > 0 && below + h > vh - GUTTER && r.top - GUTTER - h > GUTTER;
    const top = above ? r.top - GUTTER - h : below;

    let left = align === "end" ? r.right - w : r.left;
    if (vw > 0 && w > 0) left = Math.min(vw - GUTTER - w, Math.max(GUTTER, left));
    setPos({ top, left, above });
  }, [open, align, anchorRef, children]);

  useEffect(() => {
    if (!open || typeof document === "undefined") return;
    const onDown = (e: Event) => {
      const t = e.target as Node | null;
      if (t && (panel.current?.contains(t) || anchorRef.current?.contains(t))) return;
      onClose();
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("pointerdown", onDown, true);
    document.addEventListener("mousedown", onDown, true);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("pointerdown", onDown, true);
      document.removeEventListener("mousedown", onDown, true);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [open, onClose, anchorRef]);

  if (!open) return null;
  const host = popoverRoot();
  if (!host) return null;

  return createPortal(
    <div
      ref={panel}
      className={`nx-popover ${className}`.trim()}
      role="dialog"
      data-align={align}
      data-above={pos.above ? "true" : "false"}
      style={{ top: `${pos.top}px`, left: `${pos.left}px` }}
      data-testid={rest["data-testid"]}
    >
      {children}
    </div>,
    host,
  );
}
