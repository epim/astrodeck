// StarOverlay.tsx — per-star HFR overlay inside the shared transform (stream O).
// (spec §5 "Star overlay", §11 perceptual rules, decisions log #2/#16)
//
// Coordinate space: star coords live in frame.data space; the display image is a
// downscale, so map by displayScale = display_width / data_width (decisions #2).
// Per-frame, never memoized across ids (binning changes data.shape).
//
// Encoding is NEVER color-alone (§11.1): quality is color + STROKE STYLE
//   good  = thin solid
//   warn  = medium solid
//   bad   = dashed
// Every circle gets a dark --halo under-stroke (§11.2).
//
// Decimation (#16): when zoomed out, keep the WORST (highest-HFR) star per
// ~24px screen cell — that's where tilt/coma show — and disclose the count to the
// caller via onDecimated.
import { useEffect, useMemo } from "react";
import type { StarMark } from "../../types";
import { ellipseGeom } from "../../lib/starEllipse";

interface Props {
  stars: StarMark[];
  displayScale: number; // display_width / data_width
  scale: number; // current zoom (to size cells in screen px)
  hfrGood: number;
  hfrWarn: number;
  selectedKey: string | null;
  onSelect: (s: StarMark | null) => void;
  onDecimated?: (shown: number, total: number) => void;
}

function quality(hfr: number, good: number, warn: number): "good" | "warn" | "bad" {
  if (hfr <= good) return "good";
  if (hfr <= warn) return "warn";
  return "bad";
}

export function StarOverlay({
  stars,
  displayScale,
  scale,
  hfrGood,
  hfrWarn,
  selectedKey,
  onSelect,
  onDecimated,
}: Props) {
  const shown = useMemo(() => {
    if (stars.length === 0) return [];
    // worst-star-per-cell decimation in SCREEN space. cell ~24 screen px.
    const cellDisplayPx = 24 / Math.max(scale, 1e-4); // cell size in display px
    const cell = cellDisplayPx; // grid step in display-pixel space
    const best = new Map<string, StarMark>();
    for (const s of stars) {
      const dx = s.x * displayScale;
      const dy = s.y * displayScale;
      const key = `${Math.floor(dx / cell)}:${Math.floor(dy / cell)}`;
      const cur = best.get(key);
      if (!cur || s.hfr > cur.hfr) best.set(key, s);
    }
    return [...best.values()];
  }, [stars, displayScale, scale]);

  useEffect(() => {
    onDecimated?.(shown.length, stars.length);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shown.length, stars.length]);

  return (
    <g>
      {shown.map((s) => {
        const cx = s.x * displayScale;
        const cy = s.y * displayScale;
        const r = Math.max(s.hfr * displayScale * 1.6, 4);
        const q = quality(s.hfr, hfrGood, hfrWarn);
        const color = q === "good" ? "var(--good)" : q === "warn" ? "var(--warn)" : "var(--bad)";
        const sw = q === "good" ? 1 : q === "warn" ? 1.6 : 1.6;
        const dash = q === "bad" ? "3 2" : undefined;
        const key = `${s.x},${s.y}`;
        const isSel = selectedKey === key;
        // ecc → SHAPE (ellipse aspect + orientation); HFR keeps color+style.
        // null when no ecc (Pass-1 / untrusted) → plain circle below.
        const geom = ellipseGeom(r, s.ecc, s.theta);
        return (
          <g
            key={key}
            data-no-pan
            style={{ cursor: "pointer" }}
            onPointerDown={(e) => {
              e.stopPropagation();
              // native listeners on the stage (gestures) don't see React's
              // synthetic stopPropagation — stop the native event too so a star
              // tap never starts a pan.
              e.nativeEvent.stopPropagation();
              onSelect(isSel ? null : s);
            }}
          >
            {geom ? (
              <>
                {/* dark halo under-stroke (elongation-oriented) */}
                <ellipse
                  cx={cx}
                  cy={cy}
                  rx={geom.rx}
                  ry={geom.ry}
                  transform={`rotate(${geom.rotDeg} ${cx} ${cy})`}
                  fill="none"
                  stroke="var(--halo)"
                  strokeWidth={sw + 2}
                  vectorEffect="non-scaling-stroke"
                />
                <ellipse
                  cx={cx}
                  cy={cy}
                  rx={geom.rx}
                  ry={geom.ry}
                  transform={`rotate(${geom.rotDeg} ${cx} ${cy})`}
                  fill="none"
                  stroke={color}
                  strokeWidth={isSel ? sw + 1 : sw}
                  strokeDasharray={dash}
                  vectorEffect="non-scaling-stroke"
                />
              </>
            ) : (
              <>
                {/* dark halo under-stroke */}
                <circle cx={cx} cy={cy} r={r} fill="none" stroke="var(--halo)" strokeWidth={sw + 2} vectorEffect="non-scaling-stroke" />
                <circle
                  cx={cx}
                  cy={cy}
                  r={r}
                  fill="none"
                  stroke={color}
                  strokeWidth={isSel ? sw + 1 : sw}
                  strokeDasharray={dash}
                  vectorEffect="non-scaling-stroke"
                />
              </>
            )}
            {/* invisible larger hit area for cold-thumb taps */}
            <circle cx={cx} cy={cy} r={r + 8} fill="transparent" />
          </g>
        );
      })}
    </g>
  );
}
