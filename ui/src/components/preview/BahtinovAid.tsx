// NOV-12 Bahtinov focus verdict render. Mirrors FocusVerdict's structure — a
// word + tone class inside role="status" aria-live="polite" (never color alone).
// All logic lives in the pure, tsx-tested lib/bahtinov.ts.
//
// Polish grab-bag (a) adds the LIVE canvas overlay next to the text verdict:
//   - BahtinovOverlay — the spike lines + crossing vertex, drawn inside
//     PreviewStage's shared transform layer (see lib/bahtinovOverlay.ts for the
//     coordinate contract). Passive: pointer-events off, present only while the
//     aid is armed AND the server sent a valid fit.
//   - a small "Spikes on preview" opt-out in the panel (Decision B): the overlay
//     is ON by default so the novice gets the visual for free; this is the
//     expert's clean-canvas switch, and it lives HERE — next to the arm button —
//     rather than in a settings screen the novice would have to find.
import type { BahtinovGeom, PreviewInfo } from "../../types";
import { bahtinovAid, type BahtTone } from "../../lib/bahtinov";
import { bahtinovSpikeSegments } from "../../lib/bahtinovOverlay";
import { useOverlays, useStore } from "../../store";
import { InfoDot } from "../ui";

const TONE: Record<BahtTone, string> = {
  good: "text-good",
  warn: "text-warn",
  bad: "text-bad",
  neutral: "text-dim",
};

export function BahtinovAid({ preview }: { preview: PreviewInfo | null }) {
  const b = preview?.bahtinov;
  const v = bahtinovAid(b);
  const overlays = useOverlays();
  const setOverlays = useStore((s) => s.setOverlays);
  // undefined (a persisted pre-grab-bag overlay blob) reads as ON, matching the
  // shipped default — the opt-out is opt-OUT, never a hidden opt-in.
  const overlayOn = overlays.bahtinov !== false;
  const offset =
    b?.valid && b.offset_px != null ? `${b.offset_px.toFixed(1)} px` : null;
  return (
    <div role="status" aria-live="polite">
      <div className="flex items-baseline justify-between gap-2">
        <div className={`text-base font-semibold ${TONE[v.tone]}`}>{v.headline}</div>
        {offset && <div className={`mono text-sm ${TONE[v.tone]}`}>{offset}</div>}
      </div>
      <div className="text-xs text-dim mt-0.5">{v.detail}</div>
      {/* expert opt-out — only offered once there is something to hide, so an
          idle panel stays a two-line verdict (no dead control). */}
      {b?.geom && (
        // It was an 11px text row 104x16.5 CSS px tall — a third of the touch
        // floor, sitting directly above a 56px hero button, and its only
        // description lived in `title=`, which never fires on a fingertip. The
        // row now carries the house `tap` 44px floor and an InfoDot so the
        // description has a path that survives touch. `mx-4` on the InfoDot
        // wrapper keeps its 44px hit box (bought with -m-[15px] p-[15px]) from
        // overhanging the toggle it sits beside.
        <div className="mt-2 flex items-center">
          <button
            type="button"
            aria-pressed={overlayOn}
            onClick={() => setOverlays({ bahtinov: !overlayOn })}
            title="Draw the fitted spike lines and their crossing on the preview"
            className={`tap min-h-[44px] text-[11px] inline-flex items-center gap-1.5 pr-1 ${
              overlayOn ? "text-accent" : "text-dim"
            }`}
          >
            <span aria-hidden>{overlayOn ? "✦" : "○"}</span>
            Spikes on preview
          </button>
          <span className="mx-4 inline-flex items-center">
            <InfoDot
              label="About the spike overlay"
              content="Draws the three fitted Bahtinov spikes and their crossing over the live preview, so you can see what the number above is measuring. Turn it off for a clean image."
            />
          </span>
        </div>
      )}
    </div>
  );
}

/** The overlay itself — a thin <svg> shell over `bahtinovSpikeSegments`.
 *  Mounted by PreviewStage INSIDE the shared transform <svg>, so it pans/zooms
 *  with the image and sits in the same space as StarOverlay. */
export function BahtinovOverlay({
  geom, dispW, dispH, displayScale, inFocus,
}: {
  geom: BahtinovGeom;
  dispW: number;
  dispH: number;
  displayScale: number;
  inFocus: boolean;
}) {
  const { spikes, vertex } = bahtinovSpikeSegments(geom, dispW, dispH, displayScale);
  if (spikes.length === 0 && !vertex) return null;
  const r = Math.max(4, Math.min(dispW, dispH) * 0.012);
  return (
    <g style={{ pointerEvents: "none" }} aria-hidden>
      {spikes.map((s, i) => (
        <g key={i}>
          {/* dark halo under-stroke (§11.2) so the line survives a bright star */}
          <line
            x1={s.x1} y1={s.y1} x2={s.x2} y2={s.y2}
            stroke="var(--halo)"
            strokeWidth={s.central ? 4 : 3}
            vectorEffect="non-scaling-stroke"
          />
          <line
            x1={s.x1} y1={s.y1} x2={s.x2} y2={s.y2}
            // the central spike is the one you steer: thicker + accent, and it
            // reads as "locked" in --good the moment the fit says in-focus. The
            // WIDTH difference carries the role on its own (never color alone).
            stroke={s.central ? (inFocus ? "var(--good)" : "var(--accent)") : "var(--sky)"}
            strokeWidth={s.central ? 1.8 : 1}
            strokeDasharray={s.central ? undefined : "6 4"}
            vectorEffect="non-scaling-stroke"
            opacity={s.central ? 1 : 0.75}
          />
        </g>
      ))}
      {vertex && (
        <g>
          <circle
            cx={vertex.x} cy={vertex.y} r={r}
            fill="none" stroke="var(--halo)" strokeWidth={3}
            vectorEffect="non-scaling-stroke"
          />
          <circle
            cx={vertex.x} cy={vertex.y} r={r}
            fill="none"
            stroke={inFocus ? "var(--good)" : "var(--accent)"}
            strokeWidth={1.4}
            vectorEffect="non-scaling-stroke"
          />
          {/* SHAPE carries the focus state, not tint: on :root.night --accent
              (#ff3a3a) and --good (#ff3333) differ by 7/255 on one channel under
              a red display filter, so the colour swap alone is invisible. In
              focus the vertex gains a second, tight "locked" ring and its
              crosshairs go solid; out of focus they stay dashed. The word-bearing
              verdict rides above the image (PreviewStage). */}
          {inFocus && (
            <circle
              cx={vertex.x} cy={vertex.y} r={r * 0.45}
              fill="none" stroke="var(--good)" strokeWidth={1.4}
              vectorEffect="non-scaling-stroke"
            />
          )}
          {/* crosshair ticks: the vertex stays findable at any zoom even when the
              ring is small, and shape (not tint) says "this is the crossing". */}
          <line
            x1={vertex.x - r * 1.8} y1={vertex.y} x2={vertex.x + r * 1.8} y2={vertex.y}
            stroke={inFocus ? "var(--good)" : "var(--accent)"} strokeWidth={1}
            strokeDasharray={inFocus ? undefined : "4 3"}
            vectorEffect="non-scaling-stroke" opacity={0.8}
          />
          <line
            x1={vertex.x} y1={vertex.y - r * 1.8} x2={vertex.x} y2={vertex.y + r * 1.8}
            stroke={inFocus ? "var(--good)" : "var(--accent)"} strokeWidth={1}
            strokeDasharray={inFocus ? undefined : "4 3"}
            vectorEffect="non-scaling-stroke" opacity={0.8}
          />
        </g>
      )}
    </g>
  );
}
