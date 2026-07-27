// StretchHistogram.tsx — interactive display-domain histogram + stretch controls
// (stream O). Implements spec §5 "Histogram", §11/§12 perceptual+honesty rules.
//
// Honesty (the headline finding, decisions #6/#9/#10, §12):
//  - LINEAR path (sim/Alpaca): full client B/M/W. Primary = Auto toggle (sticky,
//    default on) + one Brightness slider. Advanced discloses B/M/W draggable
//    handles + a dashed MTF transfer-curve overlay (different luminance, not hue).
//  - NINA / pre-stretched (is_stretched): real B/M/W are TRULY DISABLED (lock
//    glyph + aria-disabled + defined dim token, never opacity:0.35). Panel titled
//    "Display levels (8-bit)"; only Brightness + Contrast act on the rendered img.
//    Persistent inline note. No CSS-filter masquerading as a real stretch.
//  - Histogram is labeled by histogram_domain; we never call a stretched
//    histogram "linear".
//  - Clip tag: when stats.max >= full_well the white handle shows "▲ CLIPPED" +
//    dashed outline (shape+text, not just a red glow).
//
// Handles: role=slider, aria-valuemin/max/now, keyboard arrows, focus ring, live
// numeric readout (% and ADU). 44x32 hit slop + scrub-nearest (drag anywhere
// moves the nearest handle, which enlarges while active) — §11.5, §9.
import { useMemo, useRef, useState } from "react";
import type { PreviewInfo, StretchParams } from "../../types";
import { Icon } from "../icons";
import { Toggle } from "../ui";
import { effectiveLevels, transfer } from "./lut";

const W = 256;
const H = 72;

/** The ONE sentence for "these levels cannot be moved on a NINA frame". It is
 *  rendered as visible text under the histogram AND carried in the locked
 *  handles' accessible name, so the reason reaches a sighted touch user and an
 *  assistive-tech user from the same string — never from a `title=`, which does
 *  not fire on the tablet this rig is driven from (UX #24). */
const NINA_LOCK_REASON =
  "Black, mid and white are fixed at the source — NINA pre-stretched this frame, so only the display can be adjusted.";

export function StretchHistogram({
  preview,
  stretch,
  onStretch,
  onDragChange,
}: {
  preview: PreviewInfo | null;
  stretch: StretchParams;
  onStretch: (s: Partial<StretchParams>) => void;
  onDragChange?: (dragging: boolean) => void;
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [active, setActive] = useState<"black" | "mid" | "white" | null>(null);

  const isNina = !!preview?.is_stretched;
  const domain = preview?.histogram_domain ?? "display";

  const histPath = useMemo(() => {
    const data = preview?.histogram ?? [];
    if (!data.length) return "";
    const logMax = Math.log10(Math.max(...data) + 1);
    const bw = W / data.length;
    let path = `M0,${H}`;
    for (let i = 0; i < data.length; i++) {
      const bh = logMax > 0 ? (Math.log10(data[i] + 1) / logMax) * H : 0;
      const x = i * bw;
      path += ` L${x.toFixed(2)},${(H - bh).toFixed(2)} L${(x + bw).toFixed(2)},${(H - bh).toFixed(2)}`;
    }
    path += ` L${W},${H} Z`;
    return path;
  }, [preview?.histogram]);

  // MTF transfer-curve overlay (Advanced, linear path) — dashed, different value.
  const curvePath = useMemo(() => {
    if (isNina || !stretch.advancedOpen) return "";
    let p = "";
    for (let i = 0; i <= 64; i++) {
      const x01 = i / 64;
      const y = transfer(stretch, x01);
      p += `${i === 0 ? "M" : "L"}${(x01 * W).toFixed(1)},${((1 - y) * H).toFixed(1)}`;
    }
    return p;
  }, [isNina, stretch]);

  const lv = effectiveLevels(stretch);
  const fw = preview?.full_well;
  const clipped = fw != null && preview != null && preview.data_is_linear && preview.stats.max >= fw;

  // Brightness slider behavior (P3-4). On NINA it's a display-only CSS-filter
  // nudge and must NOT touch auto. On the linear path in Auto mode it drives mid
  // via effectiveLevels (auto stays true). In Manual mode flipping auto:true here
  // would silently discard the user's B/M/W — instead nudge `mid` IN PLACE inside
  // the current [black,white] window and keep auto:false.
  const brightnessUpdate = (b: number): Partial<StretchParams> => {
    if (isNina || stretch.auto) {
      return { brightness: b, auto: isNina ? stretch.auto : true };
    }
    // Manual, linear: same brightness->mid mapping as Auto (0.5 - b*0.35),
    // clamped to stay strictly inside the manual black/white window.
    const mid = Math.min(Math.max(0.5 - b * 0.35, lv.black + 0.005), lv.white - 0.005);
    return { brightness: b, mid, auto: false };
  };

  // value (0..1) under a client x position
  const xToVal = (clientX: number) => {
    const r = svgRef.current?.getBoundingClientRect();
    if (!r) return 0;
    return Math.min(1, Math.max(0, (clientX - r.left) / r.width));
  };

  const nearestHandle = (v: number): "black" | "mid" | "white" => {
    const db = Math.abs(v - lv.black);
    const dm = Math.abs(v - lv.mid);
    const dw = Math.abs(v - lv.white);
    if (db <= dm && db <= dw) return "black";
    if (dw <= dm && dw <= db) return "white";
    return "mid";
  };

  const moveHandle = (which: "black" | "mid" | "white", v: number) => {
    // grabbing any handle flips Auto -> Manual (sticky, clearly labeled)
    const next: Partial<StretchParams> = stretch.auto ? { auto: false, ...lv } : {};
    if (which === "black") next.black = Math.min(v, lv.white - 0.01);
    else if (which === "white") next.white = Math.max(v, lv.black + 0.01);
    else next.mid = Math.min(Math.max(v, lv.black + 0.005), lv.white - 0.005);
    onStretch(next);
  };

  const onTrackPointerDown = (e: React.PointerEvent) => {
    if (isNina) return;
    const v = xToVal(e.clientX);
    const which = nearestHandle(v);
    setActive(which);
    onDragChange?.(true);
    (e.target as Element).setPointerCapture?.(e.pointerId);
    moveHandle(which, v);
  };
  const onTrackPointerMove = (e: React.PointerEvent) => {
    if (!active) return;
    moveHandle(active, xToVal(e.clientX));
  };
  const endDrag = () => {
    if (active) {
      setActive(null);
      onDragChange?.(false);
    }
  };

  const handleKey = (which: "black" | "mid" | "white") => (e: React.KeyboardEvent) => {
    if (isNina) return;
    const step = e.shiftKey ? 0.05 : 0.01;
    let d = 0;
    if (e.key === "ArrowLeft" || e.key === "ArrowDown") d = -step;
    else if (e.key === "ArrowRight" || e.key === "ArrowUp") d = step;
    else return;
    e.preventDefault();
    moveHandle(which, (which === "black" ? lv.black : which === "white" ? lv.white : lv.mid) + d);
  };

  if (!preview) {
    return <p className="text-dim text-xs">Awaiting first frame.</p>;
  }

  const Handle = ({ which, v, color }: { which: "black" | "mid" | "white"; v: number; color: string }) => {
    const pct = v * 100;
    const adu = Math.round(v * 65535);
    const isActive = active === which;
    const isClip = which === "white" && clipped;
    return (
      <div
        role="slider"
        // UX #24 (the remainder). `tabIndex={-1}` on the NINA-locked handle was
        // the native `disabled` defect wearing a different hat: it took the
        // control OUT of the tab order, so the one user who cannot see the
        // dimming — a keyboard/screen-reader user — could never land on it and
        // hear why it does nothing. `aria-disabled` alone is the house idiom
        // (ui.tsx UI-LOCKED): stay reachable, and SAY the reason. The reason
        // rides in the accessible name because there is no `title=` here on
        // purpose — title never fires on touch, and the tablet at the scope is
        // the primary field device. `handleKey`/`onTrackPointerDown` already
        // return early on `isNina`, so being focusable changes nothing about
        // what the handle can DO.
        tabIndex={0}
        aria-label={isNina ? `${which} point — unavailable. ${NINA_LOCK_REASON}` : `${which} point`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(pct)}
        aria-disabled={isNina}
        onKeyDown={handleKey(which)}
        className="absolute top-0 bottom-0 outline-none"
        style={{ left: `calc(${pct}% - 16px)`, width: 32, cursor: isNina ? "not-allowed" : "ew-resize" }}
      >
        {/* the visible thin line */}
        <span
          className="absolute top-0 bottom-0 left-1/2 -translate-x-1/2"
          style={{
            width: isActive ? 3 : 1.5,
            background: color,
            boxShadow: "0 0 0 1px var(--halo)",
          }}
        />
        {/* grip cap (enlarges when active) */}
        <span
          className="absolute left-1/2 -translate-x-1/2 -top-1 border"
          style={{
            width: isActive ? 14 : 9,
            height: isActive ? 14 : 9,
            background: color,
            borderColor: "var(--halo)",
          }}
        />
        {isClip && (
          <span className="absolute -top-5 left-1/2 -translate-x-1/2 whitespace-nowrap preview-chip text-warn flex items-center gap-0.5">
            <Icon name="alert" size={10} /> CLIPPED
          </span>
        )}
        {isActive && (
          <span className="absolute -bottom-5 left-1/2 -translate-x-1/2 whitespace-nowrap preview-chip mono">
            {Math.round(pct)}% · {adu}
          </span>
        )}
      </div>
    );
  };

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <span className="label">{isNina ? "Display levels (8-bit)" : "Stretch"}</span>
        <span className="text-[10px] text-dim mono">
          histogram of {domain === "linear" ? "linear data" : "displayed image"}
        </span>
      </div>

      {/* histogram + handles */}
      <div className="relative select-none" style={{ touchAction: "none" }}>
        <svg
          ref={svgRef}
          viewBox={`0 0 ${W} ${H}`}
          className="w-full block bg-black/40 border border-line"
          style={{ height: H }}
          onPointerDown={onTrackPointerDown}
          onPointerMove={onTrackPointerMove}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
        >
          {histPath && <path d={histPath} fill="var(--accent)" opacity={0.7} />}
          {clipped && (
            <line x1={W - 1} y1={0} x2={W - 1} y2={H} stroke="var(--warn)" strokeWidth={2} strokeDasharray="3 2" />
          )}
          {curvePath && (
            <path d={curvePath} fill="none" stroke="#e8eefc" strokeWidth={1} strokeDasharray="3 3" opacity={0.7} />
          )}
        </svg>
        {/* draggable handles overlaid (linear path; disabled lines on NINA).
            On NINA the handles are truly fixed "at the source": pin them to the
            neutral 0 / 0.5 / 1 positions independent of effectiveLevels(brightness)
            so dragging the NINA Brightness slider does NOT slide the disabled
            handles (honors the component's "fixed at the source" contract — P2-4). */}
        {stretch.advancedOpen && (
          <>
            <Handle which="black" v={isNina ? 0 : lv.black} color={isNina ? "var(--text-faint)" : "#9aa7bd"} />
            <Handle which="mid" v={isNina ? 0.5 : lv.mid} color={isNina ? "var(--text-faint)" : "var(--accent)"} />
            <Handle which="white" v={isNina ? 1 : lv.white} color={isNina ? "var(--text-faint)" : "#e8eefc"} />
          </>
        )}
      </div>

      {isNina && (
        <p className="text-[11px] text-dim flex items-start gap-1.5 leading-snug">
          <Icon name="lock" size={12} className="mt-0.5 shrink-0" />
          {NINA_LOCK_REASON}
        </p>
      )}

      {/* primary controls */}
      <div className="flex items-center gap-2">
        <Toggle checked={stretch.auto} onChange={(v) => onStretch({ auto: v })} label="Auto stretch" />
        <span className="label">Auto</span>
        <span className="text-[10px] text-dim">{stretch.auto ? "tracking each frame" : "manual"}</span>
      </div>

      <label className="flex items-center gap-2">
        <span className="label w-20 shrink-0">Brightness</span>
        <input
          type="range"
          min={-1}
          max={1}
          step={0.02}
          value={stretch.brightness}
          className="w-full accent-(--accent) cursor-pointer"
          aria-label="Brightness"
          onChange={(e) => onStretch(brightnessUpdate(Number(e.target.value)))}
        />
      </label>

      {isNina && (
        <label className="flex items-center gap-2">
          <span className="label w-20 shrink-0">Contrast</span>
          <input
            type="range"
            min={-1}
            max={1}
            step={0.02}
            value={stretch.contrast}
            className="w-full accent-(--accent) cursor-pointer"
            aria-label="Contrast"
            onChange={(e) => onStretch({ contrast: Number(e.target.value) })}
          />
        </label>
      )}

      <div className="flex items-center justify-between gap-2">
        <button
          className="btn !px-2 !py-1 text-[11px]"
          aria-expanded={stretch.advancedOpen}
          onClick={() => onStretch({ advancedOpen: !stretch.advancedOpen })}
        >
          {stretch.advancedOpen ? "▾ Advanced" : "▸ Advanced"}
        </button>
        {!stretch.auto && !isNina && (
          <button
            className="btn !px-2 !py-1 text-[11px]"
            onClick={() => onStretch({ auto: true })}
            title="Re-derive from this frame's auto levels"
          >
            <Icon name="refresh" size={12} className="inline mr-1" />
            Auto
          </button>
        )}
      </div>
    </div>
  );
}
