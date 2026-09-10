// StretchPanel.tsx - the histogram and its controls, rebuilt (wave R7, T-R7-19;
// replaces `components/preview/StretchHistogram.tsx`, which is NOT edited and
// keeps serving `#/classic`).
//
// WHAT IS KEPT AND WHAT IS REBUILT. The PLOT is a keep (wave plan section 2.3):
// a log-scaled bar outline of the frame's own histogram with a dashed MTF
// transfer curve over it is a scientific plot, and the design language has no
// vocabulary for one. Its maths is lifted unchanged into `inspectModel.ts` and
// the shared `lut.ts` (`effectiveLevels`, `transfer`) is imported, never
// re-derived. Everything AROUND it is rebuilt: the Auto toggle is a `Switch`,
// Advanced is the `Disclosure` primitive, the read-only sentence is `LockNote`,
// and the `Panel` / `.btn` chrome is gone.
//
// THE HANDLES MOVED OFF THE PLOT AND ONTO THEIR OWN RAIL. In the legacy
// component the three 32 px handle boxes were absolutely positioned over the
// histogram, which is what made "open Advanced and the page gets wider and I
// can't see controls" possible at all - an absolutely positioned box that sticks
// out of its container does not stretch it, it propagates its overflow up the
// ancestor chain until something clips or scrolls. The rail below the plot is a
// normal, in-flow element of exactly the plot's width, so nothing can escape it;
// the plot still shows WHERE the three levels sit through the guide lines drawn
// at the same x positions, which is the reading the overlay existed for.
//
// THE ONE IMPOSSIBILITY, AND IT IS STILL NOT `disabled`. A pre-stretched frame
// carries no linear data, so black / mid / white have nothing to be re-derived
// from. That is a property of the frame, not a permission - but it renders
// honest-locked all the same (`NINA_LOCK_REASON`): focusable, `aria-disabled`,
// the reason in the accessible name AND on screen through `LockNote`, and a
// press that states it. Auto and Brightness keep working, because on that path
// they are a display-domain nudge that costs nothing.

import { useRef, useState, type JSX, type KeyboardEvent, type PointerEvent } from "react";
import type { PreviewInfo, StretchParams } from "../../../../types";
import { effectiveLevels, transfer } from "../../../../components/preview/lut";
import { Disclosure, Label, LockNote, Mono, ActionButton, Switch } from "../../../ui";
import { NxIcon } from "../../../icons";
import {
  NINA_LOCK_REASON, curveFrom, histogramDomainLine, histogramPath, isClipped,
} from "./inspectModel";

const W = 256;
const H = 72;

type Which = "black" | "mid" | "white";
const WHICH: readonly Which[] = ["black", "mid", "white"] as const;
const WHICH_LABEL: Record<Which, string> = { black: "black", mid: "mid", white: "white" };

/** One level handle on the rail: the line, the grip cap and the live readout.
 *
 *  HOISTED to module scope on purpose. Declared inside the component body it
 *  would be a NEW component type on every render, so React would tear each
 *  handle's DOM down and rebuild it after every `onStretch` - which drops
 *  keyboard focus, so the SECOND arrow key of a nudge goes to the body and does
 *  nothing, and destroys any pointer capture held mid-drag. */
function Handle({ which, v, active, lockedReason, clipped, onKeyDown }: {
  which: Which;
  v: number;
  active: boolean;
  lockedReason: string | null;
  clipped: boolean;
  onKeyDown: (e: KeyboardEvent) => void;
}): JSX.Element {
  const pct = v * 100;
  const adu = Math.round(v * 65535);
  return (
    <div
      // `data-handle` is how the rail's pointerdown tells a GRAB (the press
      // landed on this handle, so the value must not jump under the finger)
      // from the scrub-nearest gesture (the press landed on bare rail).
      data-handle={which}
      role="slider"
      // NEVER `tabIndex={-1}` on the locked handle: taking the control out of
      // the tab order is the native `disabled` defect wearing a different hat,
      // and it hides the reason from the one user who cannot see the dimming.
      tabIndex={0}
      aria-label={lockedReason
        ? `${WHICH_LABEL[which]} point, unavailable: ${lockedReason}`
        : `${WHICH_LABEL[which]} point, percent of the range`}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(pct)}
      aria-disabled={lockedReason ? true : undefined}
      title={lockedReason ?? undefined}
      onKeyDown={onKeyDown}
      className="nx-insp-handle"
      data-which={which}
      data-active={active ? "true" : "false"}
      data-locked={lockedReason ? "true" : undefined}
      style={{ left: `calc(${pct}% - 16px)` }}
    >
      <span className="nx-insp-handle-line" aria-hidden="true" />
      <span className="nx-insp-handle-grip" aria-hidden="true" />
      {which === "white" && clipped && (
        <span className="nx-insp-handle-tag" data-tone="warn">CLIPPED</span>
      )}
      <span className="nx-insp-handle-read">{Math.round(pct)}% · {adu}</span>
    </div>
  );
}

export interface StretchPanelProps {
  preview: PreviewInfo | null;
  stretch: StretchParams;
  onStretch: (s: Partial<StretchParams>) => void;
  /** The stage dims its overlays while a level is being dragged. */
  onDragChange?: (dragging: boolean) => void;
  onExplain: (reason: string) => void;
}

export function StretchPanel({
  preview, stretch, onStretch, onDragChange, onExplain,
}: StretchPanelProps): JSX.Element {
  const railRef = useRef<HTMLDivElement | null>(null);
  const [active, setActive] = useState<Which | null>(null);

  // The levels cannot be re-derived from a frame that arrived already stretched.
  const levelsLock = preview?.is_stretched ? NINA_LOCK_REASON : null;
  const isNina = !!preview?.is_stretched;
  const lv = effectiveLevels(stretch);
  const clipped = isClipped(preview);
  const advanced = stretch.advancedOpen;

  const plot = histogramPath(preview?.histogram, W, H);
  const curve = advanced && !isNina ? curveFrom((x) => transfer(stretch, x), W, H) : "";

  // On a pre-stretched frame the handles are fixed "at the source": pin them to
  // the neutral 0 / 0.5 / 1 positions independent of `effectiveLevels`, so
  // dragging Brightness does not slide three controls that are refusing to move.
  const shown = (w: Which): number => (isNina ? { black: 0, mid: 0.5, white: 1 }[w] : lv[w]);

  // Brightness. On a pre-stretched frame it is a display-only nudge and must NOT
  // touch `auto`. On the linear path in Auto it drives `mid` through
  // `effectiveLevels` and Auto stays on. In Manual, flipping `auto:true` here
  // would silently discard the user's B/M/W, so instead nudge `mid` IN PLACE
  // inside the current window and keep `auto:false`.
  const brightnessUpdate = (b: number): Partial<StretchParams> => {
    if (isNina || stretch.auto) return { brightness: b, auto: isNina ? stretch.auto : true };
    const mid = Math.min(Math.max(0.5 - b * 0.35, lv.black + 0.005), lv.white - 0.005);
    return { brightness: b, mid, auto: false };
  };

  const xToVal = (clientX: number): number => {
    const r = railRef.current?.getBoundingClientRect();
    if (!r || !r.width) return 0;
    return Math.min(1, Math.max(0, (clientX - r.left) / r.width));
  };

  const nearestHandle = (v: number): Which => {
    const db = Math.abs(v - lv.black);
    const dm = Math.abs(v - lv.mid);
    const dw = Math.abs(v - lv.white);
    if (db <= dm && db <= dw) return "black";
    if (dw <= dm && dw <= db) return "white";
    return "mid";
  };

  const grabbedHandle = (target: EventTarget | null): Which | null => {
    const el = (target as Element | null)?.closest?.("[data-handle]") ?? null;
    const w = el?.getAttribute("data-handle");
    return w === "black" || w === "mid" || w === "white" ? w : null;
  };

  const moveHandle = (which: Which, v: number): void => {
    // Grabbing any handle flips Auto to Manual - sticky, and the Auto switch
    // says so rather than snapping back on the next frame.
    const next: Partial<StretchParams> = stretch.auto ? { auto: false, ...lv } : {};
    if (which === "black") next.black = Math.min(v, lv.white - 0.01);
    else if (which === "white") next.white = Math.max(v, lv.black + 0.01);
    else next.mid = Math.min(Math.max(v, lv.black + 0.005), lv.white - 0.005);
    onStretch(next);
  };

  const onRailDown = (e: PointerEvent): void => {
    if (levelsLock) { onExplain(levelsLock); return; }
    const v = xToVal(e.clientX);
    const grabbed = grabbedHandle(e.target);
    const which = grabbed ?? nearestHandle(v);
    setActive(which);
    onDragChange?.(true);
    // Capture on the CONTAINER, never on `e.target`: the target may be a handle,
    // and the handles re-render on every `onStretch`. A capture is only worth
    // having on a node that outlives the drag.
    (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
    // Grabbing a handle must not shift it: the press lands anywhere in its 32 px
    // box, up to 6% of the rail away from the value it is holding.
    if (!grabbed) moveHandle(which, v);
  };
  const onRailMove = (e: PointerEvent): void => {
    if (!active || levelsLock) return;
    moveHandle(active, xToVal(e.clientX));
  };
  const endDrag = (): void => {
    if (!active) return;
    setActive(null);
    onDragChange?.(false);
  };

  const handleKey = (which: Which) => (e: KeyboardEvent): void => {
    if (levelsLock) {
      if (e.key.startsWith("Arrow")) { e.preventDefault(); onExplain(levelsLock); }
      return;
    }
    const step = e.shiftKey ? 0.05 : 0.01;
    let d = 0;
    if (e.key === "ArrowLeft" || e.key === "ArrowDown") d = -step;
    else if (e.key === "ArrowRight" || e.key === "ArrowUp") d = step;
    else return;
    e.preventDefault();
    moveHandle(which, (which === "black" ? lv.black : which === "white" ? lv.white : lv.mid) + d);
  };

  if (!preview) {
    return (
      <div className="nx-insp-stretch" data-testid="preview-histogram">
        <Label>STRETCH</Label>
        <Mono size={11} tone="dim">no frame yet, so there is nothing to plot</Mono>
      </div>
    );
  }

  return (
    <div className="nx-insp-stretch" data-testid="preview-histogram">
      <div className="nx-insp-stretch-head">
        <Label>{isNina ? "DISPLAY LEVELS (8-BIT)" : "STRETCH"}</Label>
        <Mono size={10} tone="dim">{histogramDomainLine(preview)}</Mono>
      </div>

      {/* The plot. A KEEP: log-scaled bars of this frame's own histogram, the
          full-well marker, and the dashed transfer curve while Advanced is open. */}
      <svg
        className="nx-insp-plot"
        viewBox={`0 0 ${W} ${H}`}
        style={{ height: H }}
        role="img"
        aria-label={`Histogram, ${histogramDomainLine(preview)}`}
        data-testid="preview-histogram-plot"
      >
        {plot && <path d={plot} fill="var(--accent)" opacity={0.7} />}
        {clipped && (
          <line x1={W - 1} y1={0} x2={W - 1} y2={H} stroke="var(--warn)" strokeWidth={2} strokeDasharray="3 2" />
        )}
        {advanced && WHICH.map((w) => (
          <line key={w} x1={shown(w) * W} y1={0} x2={shown(w) * W} y2={H}
            stroke="var(--text-faint)" strokeWidth={1} opacity={0.8} />
        ))}
        {curve && (
          <path d={curve} fill="none" stroke="#e8eefc" strokeWidth={1} strokeDasharray="3 3" opacity={0.7} />
        )}
      </svg>

      {/* Advanced: black / mid / white, and the curve over the plot above. */}
      <Disclosure
        summary="ADVANCED"
        sub={isNina
          ? "fixed at the source"
          : `B ${lv.black.toFixed(2)} · M ${lv.mid.toFixed(2)} · W ${lv.white.toFixed(2)}`}
        open={advanced}
        onToggle={(o) => onStretch({ advancedOpen: o })}
        data-testid="preview-stretch-advanced"
      >
        <div
          className="nx-insp-rail"
          ref={railRef}
          onPointerDown={onRailDown}
          onPointerMove={onRailMove}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
          data-locked={levelsLock ? "true" : undefined}
        >
          {WHICH.map((w) => (
            <Handle
              key={w}
              which={w}
              v={shown(w)}
              active={active === w}
              lockedReason={levelsLock}
              clipped={clipped}
              onKeyDown={handleKey(w)}
            />
          ))}
        </div>
        <LockNote reason={levelsLock} data-testid="preview-levels-lock" />
        {!levelsLock && (
          <p className="nx-insp-note">
            Drag a level, or focus one and use the arrow keys. Moving any of the three
            switches this frame to manual until you press RE-DERIVE.
          </p>
        )}
      </Disclosure>

      {/* Primary controls. Both keep working on a pre-stretched frame. */}
      <Switch
        checked={stretch.auto}
        onChange={(v) => onStretch({ auto: v })}
        label="Auto stretch"
        note={stretch.auto
          ? "levels re-derived from every frame as it lands"
          : "levels held where you put them until you turn this back on"}
        data-testid="preview-stretch-auto"
      />

      <label className="nx-insp-slider">
        <Label size={11}>BRIGHTNESS</Label>
        <input
          type="range" min={-1} max={1} step={0.02}
          value={stretch.brightness}
          aria-label="Brightness, minus one to one"
          onChange={(e) => onStretch(brightnessUpdate(Number(e.target.value)))}
          data-testid="preview-stretch-brightness"
        />
        <Mono size={10} tone="dim">{stretch.brightness.toFixed(2)}</Mono>
      </label>

      {isNina && (
        <label className="nx-insp-slider">
          <Label size={11}>CONTRAST</Label>
          <input
            type="range" min={-1} max={1} step={0.02}
            value={stretch.contrast}
            aria-label="Contrast, minus one to one"
            onChange={(e) => onStretch({ contrast: Number(e.target.value) })}
            data-testid="preview-stretch-contrast"
          />
          <Mono size={10} tone="dim">{stretch.contrast.toFixed(2)}</Mono>
        </label>
      )}

      {!stretch.auto && !isNina && (
        <ActionButton
          kind="ghost"
          glyph={<NxIcon name="refresh" size={16} />}
          onPress={() => onStretch({ auto: true })}
          data-testid="preview-stretch-rederive"
        >
          RE-DERIVE FROM THIS FRAME
        </ActionButton>
      )}
    </div>
  );
}
