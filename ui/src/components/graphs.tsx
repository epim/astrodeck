/** Hand-rolled SVG instrument graphs: histogram, V-curve, guide scatter.
 *  Night-safe strokes (data numbers -> --text-dim, gridline strokes -> --text-faint),
 *  larger relative label fonts, no preserveAspectRatio="none" for data graphs
 *  (use vector-effect non-scaling-stroke so strokes stay crisp without distortion),
 *  Histogram collapsed to a single <path>, guide graphs React.memo'd (perf review). */
import { memo, useMemo } from "react";
import type { FocusPoint } from "../types";

export const Histogram = memo(function Histogram({ data }: { data: number[] }) {
  const w = 256, h = 64;
  const d = useMemo(() => {
    if (!data.length) return "";
    const logMax = Math.log10(Math.max(...data) + 1);
    const bw = w / data.length;
    // single filled path (one element instead of N rects)
    let path = `M0,${h}`;
    for (let i = 0; i < data.length; i++) {
      const bh = logMax > 0 ? (Math.log10(data[i] + 1) / logMax) * h : 0;
      const x = i * bw;
      path += ` L${x.toFixed(2)},${(h - bh).toFixed(2)} L${(x + bw).toFixed(2)},${(h - bh).toFixed(2)}`;
    }
    path += ` L${w},${h} Z`;
    return path;
  }, [data]);
  if (!d) return null;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-16 block">
      <path d={d} fill="var(--accent)" opacity={0.75} vectorEffect="non-scaling-stroke" />
    </svg>
  );
});

// ------------------------------------------------------------------ VCurve
// Autofocus hero (implementation brief §3 / design-reference §02): measured
// points WITH error whiskers, the engine's FITTED curve drawn from focus.fit.curve
// (stroke-dashoffset first-draw animation, reduced-motion => final state), a
// glowing best-focus marker + "BEST <position>", and the trendline cross when the
// engine emits focus.fit.trendlines. All strokes/fills are token vars (no hex).
//
// FocusFit mirrors the additive `fit` object the native engine publishes on the
// `focus` topic (server focus/native.py _fit_payload → astrodeck-native FitOutcome
// serialization). It is read here (and in FocusView) via the store's raw event —
// types.ts FocusEvent stays untouched, same pattern as `status.providers`.
export interface FocusFit {
  method?: string | null;
  r2?: number | null;
  r2s?: Record<string, number | null>;
  curve?: [number, number][]; // [[position, hfr], …] sampled fitted polyline
  trendlines?: {
    left?: { slope: number; r2: number };
    right?: { slope: number; r2: number };
    intersection?: [number, number] | null; // [position, hfr] of the trend cross
  } | null;
}

// A measured point may carry an optional per-point σ (HFR MAD) for the whisker.
type WhiskerPoint = FocusPoint & { sigma?: number };

export const VCurve = memo(function VCurve({ points, best, fit = null, running = false }: {
  points: WhiskerPoint[];
  best: { position: number; hfr: number | null } | null;
  fit?: FocusFit | null;
  running?: boolean;
}) {
  const w = 520, h = 300;
  const padL = 52, padR = 22, padT = 30, padB = 40;
  const plotW = w - padL - padR, plotH = h - padT - padB;

  const curve = fit?.curve && fit.curve.length > 1 ? fit.curve : null;
  const hasData = points.length > 0 || !!curve;

  // Domain spans points + fitted curve + whisker extents so nothing clips. With
  // no data yet (idle, or a run before its first point lands) we keep the axes
  // frame over a default HFR range rather than unmounting to a text fallback —
  // so measured points pop into a STABLE grid during the sweep (F6).
  const xsAll = [
    ...points.map((p) => p.position),
    ...(curve ? curve.map((c) => c[0]) : []),
    ...(best ? [best.position] : []),
  ];
  const ysAll = [...points.map((p) => p.hfr), ...(curve ? curve.map((c) => c[1]) : [])];
  for (const p of points) {
    const s = p.sigma ?? 0;
    if (s > 0) { ysAll.push(p.hfr + s); ysAll.push(p.hfr - s); }
  }
  const x0 = hasData ? Math.min(...xsAll) : 0, x1 = hasData ? Math.max(...xsAll) : 1;
  const yMin = hasData ? Math.min(...ysAll) : 1, yMax = hasData ? Math.max(...ysAll) : 6;
  const yspan = Math.max(yMax - yMin, 0.01);
  const y0 = Math.max(0, yMin - yspan * 0.12);
  const y1 = yMax + yspan * 0.12;

  const sx = (x: number) => padL + ((x - x0) / Math.max(x1 - x0, 1)) * plotW;
  const sy = (y: number) => padT + (1 - (y - y0) / Math.max(y1 - y0, 1e-6)) * plotH;

  const curvePath = curve
    ? curve.map((c, i) => `${i === 0 ? "M" : "L"}${sx(c[0]).toFixed(1)},${sy(c[1]).toFixed(1)}`).join(" ")
    : null;

  // Trendline cross (only when the engine bracketed an intersection).
  const tl = fit?.trendlines ?? null;
  const cross = tl?.intersection ?? null;
  const trendLeft = cross && tl?.left ? { x: x0, y: cross[1] + tl.left.slope * (x0 - cross[0]) } : null;
  const trendRight = cross && tl?.right ? { x: x1, y: cross[1] + tl.right.slope * (x1 - cross[0]) } : null;

  const bestHfr = best?.hfr ?? (cross ? cross[1] : null);

  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full block instr-fit" style={{ aspectRatio: `${w} / ${h}` }}>
      {/* Scoped motion: the fitted curve draws itself once; the best marker eases
          in once. prefers-reduced-motion snaps both to their final state. Only
          hero moment — no other element animates. */}
      <style>{`
        .af-fitline{stroke-dasharray:1;stroke-dashoffset:1;animation:af-draw 1.4s .25s ease forwards;}
        @keyframes af-draw{to{stroke-dashoffset:0;}}
        .af-bestmark{opacity:0;animation:af-pop .5s 1.5s ease forwards;}
        @keyframes af-pop{from{opacity:0;transform:translateY(5px);}to{opacity:1;transform:none;}}
        @media (prefers-reduced-motion:reduce){
          .af-fitline{stroke-dashoffset:0;animation:none;}
          .af-bestmark{opacity:1;transform:none;animation:none;}
        }
      `}</style>
      <defs>
        <linearGradient id="af-fitg" x1="0" x2="1">
          <stop offset="0" stopColor="var(--accent)" stopOpacity="0.5" />
          <stop offset="0.5" stopColor="var(--accent)" />
          <stop offset="1" stopColor="var(--accent)" stopOpacity="0.5" />
        </linearGradient>
      </defs>

      {/* horizontal gridlines + HFR tick labels */}
      {[0, 0.25, 0.5, 0.75, 1].map((f) => {
        const y = padT + f * plotH;
        const val = y1 - f * (y1 - y0);
        return (
          <g key={f}>
            <line x1={padL} x2={w - padR} y1={y} y2={y} stroke="var(--text-faint)"
              strokeDasharray={f === 1 ? undefined : "2 4"} vectorEffect="non-scaling-stroke" />
            <text x={padL - 6} y={y + 3} textAnchor="end" fill="var(--text-dim)" fontSize={11}
              fontFamily="IBM Plex Mono">{val.toFixed(1)}</text>
          </g>
        );
      })}
      <text x={12} y={padT + 4} fill="var(--text-dim)" fontSize={11} fontFamily="IBM Plex Mono">HFR</text>

      {/* empty frame hint — the grid stays put so points land into it (F6) */}
      {!hasData && (
        <text x={padL + plotW / 2} y={padT + plotH / 2} textAnchor="middle"
          fill="var(--text-faint)" fontSize={11} fontFamily="IBM Plex Mono" letterSpacing="2">
          {running ? "MEASURING…" : "RUN AUTOFOCUS"}
        </text>
      )}

      {/* trendline cross (data hue, kept distinct from the accent fit) */}
      {cross && trendLeft && trendRight && (
        <g stroke="var(--sky)" strokeWidth={1} strokeDasharray="3 4" vectorEffect="non-scaling-stroke" opacity={0.75}>
          <line x1={sx(trendLeft.x)} y1={sy(trendLeft.y)} x2={sx(cross[0])} y2={sy(cross[1])} />
          <line x1={sx(cross[0])} y1={sy(cross[1])} x2={sx(trendRight.x)} y2={sy(trendRight.y)} />
        </g>
      )}

      {/* fitted curve — the engine's hyperbola, drawn on first appearance */}
      {curvePath && (
        <path className="af-fitline" d={curvePath} pathLength={1} fill="none"
          stroke="url(#af-fitg)" strokeWidth={2.4} strokeLinecap="round" vectorEffect="non-scaling-stroke" />
      )}

      {/* measured points + error whiskers (whisker only when σ is known) */}
      {points.map((p, i) => {
        const s = p.sigma ?? 0;
        return (
          <g key={i}>
            {s > 0 && (
              <line x1={sx(p.position)} x2={sx(p.position)} y1={sy(p.hfr - s)} y2={sy(p.hfr + s)}
                stroke="var(--text-dim)" strokeWidth={1.2} vectorEffect="non-scaling-stroke" />
            )}
            <circle cx={sx(p.position)} cy={sy(p.hfr)} r={3.2} fill="var(--bg)"
              stroke="var(--accent)" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
          </g>
        );
      })}

      {/* glowing best-focus marker + "BEST <position>" */}
      {best && bestHfr != null && (
        <g className="af-bestmark">
          <line x1={sx(best.position)} x2={sx(best.position)} y1={sy(bestHfr)} y2={h - padB}
            stroke="var(--accent)" strokeWidth={1.2} strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />
          <circle cx={sx(best.position)} cy={sy(bestHfr)} r={5.5} fill="var(--accent)"
            style={{ filter: "drop-shadow(0 0 5px var(--glow))" }} />
          <text x={sx(best.position)} y={padT - 8} textAnchor="middle" fill="var(--accent)"
            fontSize={12} fontFamily="IBM Plex Mono">BEST {best.position}</text>
        </g>
      )}

      {/* x-axis position extents */}
      <text x={padL} y={h - 6} fill="var(--text-dim)" fontSize={11} fontFamily="IBM Plex Mono">{x0}</text>
      <text x={w - padR} y={h - 6} textAnchor="end" fill="var(--text-dim)" fontSize={11}
        fontFamily="IBM Plex Mono">{x1}</text>
    </svg>
  );
});

export const GuideGraph = memo(function GuideGraph({ samples }: { samples: { t: number; ra: number; dec: number }[] }) {
  const w = 420, h = 140, mid = h / 2;
  const scale = 20; // px per arcsec
  const n = Math.max(samples.length, 1);
  const sx = (i: number) => (i / Math.max(n - 1, 1)) * w;
  const line = (key: "ra" | "dec") =>
    samples.map((s, i) => `${i === 0 ? "M" : "L"}${sx(i)},${mid - s[key] * scale}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full block">
      {[-2, -1, 1, 2].map((a) => (
        <line key={a} x1={0} x2={w} y1={mid - a * scale} y2={mid - a * scale}
          stroke="var(--text-faint)" strokeDasharray="2 5" vectorEffect="non-scaling-stroke" />
      ))}
      <line x1={0} x2={w} y1={mid} y2={mid} stroke="var(--line-bright)" vectorEffect="non-scaling-stroke" />
      {samples.length > 1 && (
        <>
          <path d={line("ra")} fill="none" stroke="var(--accent)" strokeWidth={1.2} vectorEffect="non-scaling-stroke" />
          <path d={line("dec")} fill="none" stroke="var(--warn)" strokeWidth={1.2} vectorEffect="non-scaling-stroke" />
        </>
      )}
      <text x={4} y={14} fill="var(--accent)" fontSize={12} fontFamily="IBM Plex Mono">RA</text>
      <text x={30} y={14} fill="var(--warn)" fontSize={12} fontFamily="IBM Plex Mono">DEC</text>
      <text x={w - 4} y={mid - 2 * scale - 3} textAnchor="end" fill="var(--text-dim)" fontSize={11}
        fontFamily="IBM Plex Mono">+2"</text>
    </svg>
  );
});

export const GuideScatter = memo(function GuideScatter({ samples }: { samples: { ra: number; dec: number }[] }) {
  const size = 140, mid = size / 2, scale = 22;
  return (
    <svg viewBox={`0 0 ${size} ${size}`} className="w-[140px] h-[140px] block shrink-0">
      {[1, 2].map((r) => (
        <circle key={r} cx={mid} cy={mid} r={r * scale} fill="none" stroke="var(--text-faint)"
          vectorEffect="non-scaling-stroke" />
      ))}
      <line x1={mid} x2={mid} y1={0} y2={size} stroke="var(--text-faint)" vectorEffect="non-scaling-stroke" />
      <line x1={0} x2={size} y1={mid} y2={mid} stroke="var(--text-faint)" vectorEffect="non-scaling-stroke" />
      {samples.slice(-60).map((s, i, arr) => (
        <circle key={i} cx={mid + s.ra * scale} cy={mid - s.dec * scale} r={1.5}
          fill={i === arr.length - 1 ? "var(--good)" : "var(--accent)"}
          opacity={0.25 + (0.75 * i) / arr.length} />
      ))}
    </svg>
  );
});
