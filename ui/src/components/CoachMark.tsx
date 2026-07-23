// CoachMark.tsx — F-G-2: reusable one-time spotlight primitive (first-run
// wizard design spec §3, Task 3). Given a `data-coach="…"` anchor selector +
// a `seenKey` + copy, portals a dim layer with a transparent hole around the
// target (box-shadow spill trick) and a callout anchored via the already-
// tested `placeTooltip`. Dismiss -> markSeen(seenKey). Renders null once
// seen. Degrades to a centered callout (never a broken hole) if the target
// selector matches nothing. Thin render — the geometry (`spotlightRect`) and
// anchoring (`placeTooltip`) are the pure/tested parts (lib/coach.ts,
// lib/tooltipPlace.ts).

import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useHasSeen, useStore } from "../store";
import { spotlightRect, type Box } from "../lib/coach";
import { placeTooltip, type Placement } from "../lib/tooltipPlace";
import { Icon } from "./icons";

export function CoachMark(props: {
  targetSel: string;
  seenKey: string;
  title: string;
  body: ReactNode;
  side?: "top" | "bottom" | "left" | "right";
  onDismiss?: () => void;
}): JSX.Element | null {
  const { targetSel, seenKey, title, body, side = "bottom", onDismiss } = props;
  const seen = useHasSeen(seenKey);
  const markSeen = useStore((s) => s.markSeen);

  const [rect, setRect] = useState<Box | null>(null);
  const [found, setFound] = useState(true);
  const calloutRef = useRef<HTMLDivElement>(null);
  const [placement, setPlacement] = useState<Placement | null>(null);

  const dismiss = () => {
    markSeen(seenKey);
    onDismiss?.();
  };

  // Locate + measure the target; re-measure on resize/scroll while unseen.
  useLayoutEffect(() => {
    if (seen) return;
    const measure = () => {
      const el = document.querySelector<HTMLElement>(targetSel);
      if (!el) {
        setFound(false);
        setRect(null);
        return;
      }
      setFound(true);
      const r = el.getBoundingClientRect();
      setRect({ left: r.left, top: r.top, width: r.width, height: r.height });
    };
    measure();
    const scrollOpts = { capture: true, passive: true } as const;
    window.addEventListener("resize", measure, { passive: true });
    window.addEventListener("scroll", measure, scrollOpts);
    return () => {
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, scrollOpts);
    };
  }, [seen, targetSel]);

  // Measure the (already-mounted) callout and clamp it to the viewport via
  // the shared pure placement math, same idiom as ui.tsx's Tooltip.
  useLayoutEffect(() => {
    if (seen || !found || !rect) {
      setPlacement(null);
      return;
    }
    const bub = calloutRef.current;
    if (!bub) return;
    setPlacement(
      placeTooltip({
        trigger: rect,
        bubble: { width: bub.offsetWidth, height: bub.offsetHeight },
        viewport: { width: window.innerWidth, height: window.innerHeight },
        side,
      }),
    );
  }, [seen, found, rect, side]);

  // Escape / outside-click dismiss. A DOCUMENT-level pointerdown (the Tooltip
  // idiom, ui.tsx) rather than an overlay div, because the dim layer must stay
  // pointer-events:none over the hole so the user can click straight through
  // to the real, spotlighted control (never blocked). A click that lands on
  // neither the callout nor the (still-live) target counts as "on the dim".
  useEffect(() => {
    if (seen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") dismiss();
    };
    const onDoc = (e: Event) => {
      const t = e.target as Node;
      if (calloutRef.current?.contains(t)) return;
      const targetEl = document.querySelector<HTMLElement>(targetSel);
      if (targetEl?.contains(t)) return; // click-through to the spotlighted control
      dismiss();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDoc);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onDoc);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seen, targetSel]);

  if (seen) return null;

  const hasHole = found && !!rect;
  const hole = hasHole ? spotlightRect(rect!) : null;

  return createPortal(
    <>
      {hole ? (
        // Pointer-events:none — a click-through cutout so the user can still
        // operate the real control it spotlights; outside-dismiss is handled
        // by the document-level listener above, not by this layer.
        <div
          aria-hidden
          className="fixed z-[56] rounded-md"
          style={{
            left: hole.left,
            top: hole.top,
            width: hole.width,
            height: hole.height,
            boxShadow: "0 0 0 9999px rgba(0,0,0,0.55)",
            border: "1px solid var(--accent)",
            pointerEvents: "none",
          }}
        />
      ) : (
        <div
          className="fixed inset-0 z-[56]"
          style={{ background: "rgba(0,0,0,0.55)", pointerEvents: "none" }}
          aria-hidden
        />
      )}

      <div
        ref={calloutRef}
        role="dialog"
        aria-label={title}
        className={`panel sheet-enter p-4 max-w-[320px] z-[56] pointer-events-auto ${
          found ? "fixed" : "fixed inset-0 m-auto h-fit w-fit"
        }`}
        style={found ? { left: placement?.left ?? -9999, top: placement?.top ?? -9999 } : undefined}
      >
        <div className="flex items-start justify-between gap-3 mb-1.5">
          <h3 className="panel-title !mb-0">{title}</h3>
          <button
            type="button"
            aria-label="Dismiss"
            className="btn btn-touch inline-flex items-center justify-center p-0 shrink-0"
            onClick={dismiss}
          >
            <Icon name="x" size={14} />
          </button>
        </div>
        <p className="text-xs text-dim leading-relaxed mb-3">{body}</p>
        <div className="flex justify-end">
          <button type="button" className="btn btn-accent min-h-11" onClick={dismiss}>
            Got it
          </button>
        </div>
      </div>
    </>,
    document.body,
  );
}

export default CoachMark;
