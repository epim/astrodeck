/** Hand-rolled SVG instrument graphs: histogram, V-curve, guide scatter.
 *  Night-safe strokes (data numbers -> --text-dim, gridline strokes -> --text-faint),
 *  larger relative label fonts, no preserveAspectRatio="none" for data graphs
 *  (use vector-effect non-scaling-stroke so strokes stay crisp without distortion),
 *  Histogram collapsed to a single <path>, guide graphs React.memo'd (perf review). */
import { memo, useMemo } from "react";
import type { FocusPoint } from "../types";
import { trendGeom, trendGeomTimed } from "../lib/reportChart";

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
      {/* UX #43: the y-axis TITLE used to sit at (12, padT+4) — the same baseline
          as the TOPMOST tick label, which is right-anchored at padL-6 and so
          reaches back to x≈26. The two overstruck each other ("HFR2.8", 40px² of
          overlap, both 11px). A y-axis title belongs rotated in the gutter, not
          on a tick's row: rotated -90° and centred on the plot it clears the
          tick column (which ends at x≈26) entirely and reads as an axis title
          rather than a stray datum. */}
      <text transform={`translate(14 ${padT + plotH / 2}) rotate(-90)`} textAnchor="middle"
        fill="var(--text-dim)" fontSize={11} fontFamily="IBM Plex Mono" letterSpacing="1.5">HFR</text>

      {/* empty frame hint — the grid stays put so points land into it (F6).
          UX #43: offset off the plot's vertical centre, because the f=0.5
          gridline runs exactly through padT + plotH/2 and was striking the
          text through. Sits in the clear band between the 0.25 and 0.5 lines. */}
      {!hasData && (
        <text x={padL + plotW / 2} y={padT + plotH * 0.38} textAnchor="middle"
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
          stroke="url(#af-fitg)" strokeWidth={2.4} strokeLinecap="round" vectorEffect="non-scaling-stroke" filter="drop-shadow(0 0 6px var(--glow))" />
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

      {/* x-axis. UX #43: with no data the extents were the 0..1 FALLBACK domain
          printed as if they were focuser positions — two placeholder numbers a
          reader has no way to tell from real ones. Print the extents only when
          they are measured; otherwise name the axis instead. */}
      {hasData ? (
        <>
          <text x={padL} y={h - 6} fill="var(--text-dim)" fontSize={11} fontFamily="IBM Plex Mono">{x0}</text>
          <text x={w - padR} y={h - 6} textAnchor="end" fill="var(--text-dim)" fontSize={11}
            fontFamily="IBM Plex Mono">{x1}</text>
        </>
      ) : (
        <text x={padL + plotW / 2} y={h - 6} textAnchor="middle" fill="var(--text-faint)"
          fontSize={11} fontFamily="IBM Plex Mono" letterSpacing="1.5">FOCUSER POSITION</text>
      )}
    </svg>
  );
});

// UX #15 / S3. RA and DEC were separated by HUE ALONE — `--accent` vs `--warn`,
// both `stroke-width: 1.5` with `stroke-dasharray: none`. In night mode the red
// LUT collapses those two hues to ~1.2:1 of each other, and telling RA drift
// (periodic error, wind) from a DEC excursion (backlash, polar misalignment) is
// the only reason this chart exists. So each series now carries a SECOND,
// non-hue channel that survives a monochrome palette: RA is the solid trace,
// DEC is dashed. The legend stops being two coloured words and draws the actual
// stroke beside each word, so the key is legible without perceiving colour at
// all — the same shape-over-hue rule `.led-on`/`.led-off`/`.led-bad` already
// follow.
const RA_DASH: string | undefined = undefined; // solid
const DEC_DASH = "7 4";

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
          <path d={line("ra")} fill="none" stroke="var(--accent)" strokeWidth={1.4}
            strokeDasharray={RA_DASH} vectorEffect="non-scaling-stroke" />
          <path d={line("dec")} fill="none" stroke="var(--warn)" strokeWidth={1.4}
            strokeDasharray={DEC_DASH} vectorEffect="non-scaling-stroke" />
        </>
      )}
      {/* Chart had no empty state at all — a bare grid reads as "guiding, dead
          flat" rather than "no data". Say which it is. */}
      {samples.length < 2 && (
        <text x={w / 2} y={mid + 16} textAnchor="middle" fill="var(--text-faint)"
          fontSize={11} fontFamily="IBM Plex Mono" letterSpacing="2">NO GUIDE DATA YET</text>
      )}
      {/* legend — each entry draws its OWN stroke pattern, not just its hue */}
      <line x1={4} x2={22} y1={10} y2={10} stroke="var(--accent)" strokeWidth={1.8}
        strokeDasharray={RA_DASH} vectorEffect="non-scaling-stroke" />
      <text x={26} y={14} fill="var(--accent)" fontSize={12} fontFamily="IBM Plex Mono">RA</text>
      <line x1={54} x2={72} y1={10} y2={10} stroke="var(--warn)" strokeWidth={1.8}
        strokeDasharray={DEC_DASH} vectorEffect="non-scaling-stroke" />
      <text x={76} y={14} fill="var(--warn)" fontSize={12} fontFamily="IBM Plex Mono">DEC</text>
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

// ------------------------------------------------------------------ TrendLine
/** hh:mm for a trend-axis caption. `t` is epoch SECONDS — the unit the report
 *  trends carry ([ts, v] from report.trends, same as ReportView's ev.ts). */
function fmtClock(t: number): string {
  return new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

// Night-safe time-series line (report viewer spec §3 Task 2). Reused by both the
// end-of-night report trends and the live in-acquisition strip.
//
// TWO x-axis modes, one primitive:
//   - `times` omitted  -> x by INDEX (`trendGeom`). The live in-acquisition strip
//     is index-native (one sample per sub, drawn as it arrives) and is untouched.
//   - `times` given    -> x by REAL TIME (`trendGeomTimed`), the report path: each
//     report series is downsampled independently, so a pause or a lights-only
//     series would otherwise plot with a lying x-axis. With real time the panels
//     line up in wall-clock and a gap reads as a gap. Faint start/end clock
//     captions are drawn under the line whenever the series actually spans time
//     (polish grab-bag Decision D) — dropped when it doesn't, rather than
//     printing the same minute twice.
export const TrendLine = memo(function TrendLine({
  values, times, label, unit = "", decimals = 2, w = 240, h = 56,
}: {
  values: number[]; times?: number[]; label: string; unit?: string;
  decimals?: number; w?: number; h?: number;
}) {
  const geom = useMemo(
    () =>
      times && times.length === values.length
        ? trendGeomTimed(values.map((v, i) => ({ t: times[i], v })), w, h)
        : trendGeom(values, w, h),
    [values, times, w, h],
  );
  const last = values.length ? values[values.length - 1] : null;
  return (
    <div className="flex flex-col gap-0.5">
      <div className="flex items-baseline justify-between">
        <span className="label !text-[9px]">{label}</span>
        <span className="mono text-[10px] text-dim tabular-nums">
          {last != null ? `${last.toFixed(decimals)}${unit}` : "—"}
        </span>
      </div>
      {geom ? (
        <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="w-full"
          style={{ height: h }} role="img" aria-label={`${label} trend`}>
          <path d={geom.d} fill="none" stroke="var(--accent)" strokeWidth={1.4}
            vectorEffect="non-scaling-stroke" />
        </svg>
      ) : (
        <div className="text-dim text-[10px] py-3 text-center">no data</div>
      )}
      {geom && (
        // y-range are DATA numbers, so they take the AA text rung (--text-dim,
        // `text-dim`) at the 10px floor — NOT an opacity-thinned variant of it.
        <div className="flex justify-between mono text-[10px] text-dim tabular-nums">
          <span>{geom.yMin.toFixed(decimals)}{unit}</span>
          <span>{geom.yMax.toFixed(decimals)}{unit}</span>
        </div>
      )}
      {/* real-time axis captions (only on the timed path, only when the series
          spans time). These are AXIS TICKS, which is exactly the role --text-faint
          is defined for ("ticks, gridline ghosts", index.css); `text-dim/50` was
          an invented rung BELOW that defined floor, at 9px. Now the defined
          `text-faint` token at the 10px type floor. */}
      {geom?.tMin != null && geom.tMax != null && (
        <div
          className="flex justify-between mono text-[10px] text-faint tabular-nums"
          title="Wall-clock span of this trend — the line is plotted on a real time axis"
        >
          <span>{fmtClock(geom.tMin)}</span>
          <span>{fmtClock(geom.tMax)}</span>
        </div>
      )}
    </div>
  );
});
