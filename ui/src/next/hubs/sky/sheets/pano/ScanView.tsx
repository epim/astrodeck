// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T18: the live screen of the panorama scanner (SPEC-v2 2.4, 2.5, 3.5, 4.11, D19). It shows what the scanner's
// status says and owns no scanning state of its own, and it has no buttons: PanoCapture owns Start, Pause, Review
// and Cancel. Parts, top to bottom:
//   - the preview box holding `videoSlot`, the two capture-slit lines and the roll tick, with the level rail beside it;
//   - the ribbon: a 2160 x 300 canvas holding the raster twice so the 360-degree wrap never shows a seam, kept in step
//     by dirty-rectangle `putImageData` and scrolled and cropped in CSS, with the live camera frame as a 4 x 8
//     triangle mesh on its own overlay canvas;
//   - the 720-cell coverage bar with N/E/S/W labels once a north estimate exists, the pace gauge and the cue line.
// Work happens in `requestAnimationFrame` (3.5): the scanner's subscribers only schedule a frame, the frame copies
// the dirty rectangles, moves the ribbon and publishes a snapshot to React, and the mesh is redrawn only when
// `status.frameNo` has changed and only in the live phases. Nothing here reads a canvas back.
import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { PANO_H, PANO_W, PROFILE_BINS, PixClass } from './types';
import type { DirtyRect, LiveTriangle, RibbonView, ScanPhase, ScanStatus, ScannerLike } from './types';
// The area's stylesheet carries the `.pano-*` rules. Double quotes on purpose: the r7 rule's test reads only that form.
import "../sheets.css";

export const RIBBON_SPAN_DEG = 150, RIBBON_ALT_TOP = 60, RIBBON_ALT_BOTTOM = -10;

export interface ScanViewProps { scanner: ScannerLike; videoSlot?: ReactNode; widthPx?: number /* 390 */ }

const DEFAULT_WIDTH_PX = 390;
const COPIES_W = PANO_W * 2;                 // the raster twice, side by side
const COLS_PER_DEG = PANO_W / 360;           // 3
const ROWS_PER_DEG = (PANO_H - 1) / 100;     // 2.99: row y is altitude 90 - y / 299 * 100
const ZERO_ROW = Math.round(90 * ROWS_PER_DEG);   // the raster row at altitude 0
const TINT_W = PROFILE_BINS * 2;             // one tint pixel per coverage cell, two copies
const LIVE_PHASES: readonly ScanPhase[] = ['scanning', 'paused', 'finishing', 'done'];
const RAIL_MIN_DEG = -10, RAIL_MAX_DEG = 50;  // the rail's scale: the bands of 4.4 sit well inside it
const ROLL_ALERT_DEG = 15;                    // the roll tick turns red above this (2.4)
const SLIT_HALF_DEG = 3;                      // the capture slit is the central 6 degrees (2.4)
const FALLBACK_SHORT_FOV_DEG = 43;            // the default lens before any status reports one
const MESH_ALPHA = 0.5;                       // the live frame is drawn at 50 % (2.5)
const PACE_IDLE_DEG_S = 5;                    // below this the gauge reads "Keep turning" (2.5)
const PACE_SCALE = 1.25;                      // the gauge ends 25 % past rateMax so the red zone has a width
const TINT_RGBA = [237, 189, 128, 72] as const;   // amber, for sensor-placed and blurred columns
const FULL_RECT: DirtyRect = { x: 0, y: 0, w: PANO_W, h: PANO_H };
const CELL_CLASS: readonly string[] = [
  'pano-cell pano-cell-none', 'pano-cell pano-cell-sensor', 'pano-cell pano-cell-blurred', 'pano-cell pano-cell-aligned',
];
const COMPASS_POINTS: readonly { label: string; bearing: number }[] = [
  { label: 'N', bearing: 0 }, { label: 'E', bearing: 90 }, { label: 'S', bearing: 180 }, { label: 'W', bearing: 270 },
];

type Ctx = CanvasRenderingContext2D | null;
type Band = { green: readonly [number, number]; amber: readonly [number, number] };

const mod = (a: number, n: number) => ((a % n) + n) % n;
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const px2 = (v: number) => Math.round(v * 100) / 100;
/** Round to a step of 1 / perUnit. Division, not multiplication, so 12.6 stays 12.6 and not 12.600000000000001. */
const quant = (v: number | null, perUnit: number) => (v === null || !Number.isFinite(v) ? null : Math.round(v * perUnit) / perUnit);

// ---- Exported pure helpers -------------------------------------------------

/** The window the ribbon shows for a live heading: 150 degrees wide, altitude 60 down to -10. The heading is a
 *  scan-frame azimuth, which is the raster's frame while scanning (north is composed only at the final render). */
export function ribbonViewFor(headingDeg: number, widthPx: number): RibbonView {
  return { centreAz: mod(headingDeg, 360), altTop: RIBBON_ALT_TOP, altBottom: RIBBON_ALT_BOTTOM, pxPerDeg: widthPx / RIBBON_SPAN_DEG, widthPx };
}

/** The level rail's colour for the optical axis's altitude (2.4): green inside the green band, amber inside the
 *  amber band, red outside both, none with no reading. Band ends are inclusive. */
export function railColour(elevationDeg: number | null, band: Band): 'green' | 'amber' | 'red' | 'none' {
  if (elevationDeg === null || !Number.isFinite(elevationDeg)) return 'none';
  const inside = (r: readonly [number, number]) => elevationDeg >= r[0] && elevationDeg <= r[1];
  return inside(band.green) ? 'green' : inside(band.amber) ? 'amber' : 'red';
}

// ---- Geometry --------------------------------------------------------------

interface Geometry {
  ppd: number;       // CSS px per degree, both axes
  boxH: number;      // the ribbon window's height: altitude 60 down to -10
  canvasW: number;   // the 2160 x 300 canvas as displayed: 3 columns per degree
  canvasH: number;   // 2.99 rows per degree
  trackTop: number;  // puts the centre of the row at altitude 60 on the top edge of the window
}

/** Row y is sampled at altitude 90 - y / 299 * 100, so a row's centre is its altitude: the canvas moves up by half a
 *  row. Columns need no such shift, because column x is sampled at (x + 0.5) / 1080 of the turn. */
function geometryFor(widthPx: number): Geometry {
  const ppd = widthPx / RIBBON_SPAN_DEG;
  return {
    ppd, boxH: px2((RIBBON_ALT_TOP - RIBBON_ALT_BOTTOM) * ppd), canvasW: px2(COPIES_W / COLS_PER_DEG * ppd),
    canvasH: px2(PANO_H / ROWS_PER_DEG * ppd), trackTop: px2(-(90 - RIBBON_ALT_TOP) * ppd - ppd / ROWS_PER_DEG / 2),
  };
}

/** translateX that puts a heading at the middle of the window. The window starts at the heading minus 75 degrees,
 *  taken into [0, 360), so it always lies inside the first 510 degrees of the 720 the canvas holds. */
function scrollPx(headingDeg: number, ppd: number): number {
  return -mod(headingDeg - RIBBON_SPAN_DEG / 2, 360) * ppd;
}

// ---- The snapshot React renders --------------------------------------------

interface Snap {
  phase: ScanPhase;
  cue: string; cueKey: string; cueKind: string;
  elevationDeg: number | null; colour: ReturnType<typeof railColour>; targetPitchDeg: number;
  greenLo: number; greenHi: number; amberLo: number; amberHi: number;
  rollDeg: number | null; rollAlert: boolean;
  pace: ScanStatus['pace']; rateDegS: number | null; rateMaxDegS: number;
  northOffsetDeg: number | null; shortFovDeg: number; coveredDeg: number; tallKey: string;
}
interface ViewState { snap: Snap; tall: readonly { from: number; to: number }[]; cov: Uint8Array }

/** What the screen shows, rounded to what a person can see. The class that a number falls in (rail colour, roll
 *  alert) is decided on the exact value, so rounding never moves a reading across a band edge. */
function snapOf(st: ScanStatus): Snap {
  return {
    phase: st.phase, cue: st.cue, cueKey: st.cueKey, cueKind: st.cueKind,
    elevationDeg: quant(st.elevationDeg, 10), colour: railColour(st.elevationDeg, st.pitchBand), targetPitchDeg: st.targetPitchDeg,
    greenLo: st.pitchBand.green[0], greenHi: st.pitchBand.green[1], amberLo: st.pitchBand.amber[0], amberHi: st.pitchBand.amber[1],
    rollDeg: quant(st.rollDeg, 10), rollAlert: st.rollDeg !== null && Math.abs(st.rollDeg) > ROLL_ALERT_DEG,
    pace: st.pace, rateDegS: quant(st.rateDegS, 2), rateMaxDegS: st.rateMaxDegS,
    northOffsetDeg: quant(st.northOffsetDeg, 10), shortFovDeg: st.focal.shortFovDeg, coveredDeg: Math.round(st.coveredDeg),
    tallKey: st.tallSpans.map(s => `${s.from}:${s.to}`).join(','),
  };
}

function sameSnap(a: Snap, b: Snap): boolean {
  return (Object.keys(a) as (keyof Snap)[]).every(k => a[k] === b[k]);
}

function sameBytes(a: ArrayLike<number>, b: ArrayLike<number>): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

/** The previous view when nothing on screen would change, so a notification that changes no pixel costs no render.
 *  Coverage is compared by content because the scanner may well mutate one array in place. */
function nextView(prev: ViewState | null, st: ScanStatus): ViewState {
  const snap = snapOf(st);
  if (prev === null) return { snap, tall: st.tallSpans.map(s => ({ from: s.from, to: s.to })), cov: Uint8Array.from(st.coverage) };
  const snapSame = sameSnap(prev.snap, snap), covSame = sameBytes(prev.cov, st.coverage);
  if (snapSame && covSame) return prev;
  return {
    snap: snapSame ? prev.snap : snap,
    tall: prev.snap.tallKey === snap.tallKey ? prev.tall : st.tallSpans.map(s => ({ from: s.from, to: s.to })),
    cov: covSame ? prev.cov : Uint8Array.from(st.coverage),
  };
}

// ---- Canvas work (called from the animation frame) -------------------------

/** A dirty rectangle as at most two pieces inside [0, PANO_W): a rectangle past column 1080 wraps to column 0. */
function wrapPieces(r: DirtyRect): DirtyRect[] {
  const y0 = clamp(Math.floor(r.y), 0, PANO_H), y1 = clamp(Math.ceil(r.y + r.h), 0, PANO_H);
  const x0 = Math.floor(r.x), x1 = Math.ceil(r.x + r.w);
  const h = y1 - y0, w = Math.min(x1 - x0, PANO_W);
  if (h <= 0 || w <= 0) return [];
  const x = mod(x0, PANO_W);
  if (x + w <= PANO_W) return [{ x, y: y0, w, h }];
  return [{ x, y: y0, w: PANO_W - x, h }, { x: 0, y: y0, w: x + w - PANO_W, h }];
}

/** The top of the painted run that contains altitude 0 in one raster column, or -1 when altitude 0 is unpainted. */
function photoTopRow(rgba: Uint8ClampedArray, x: number): number {
  const alpha = (y: number) => rgba[(y * PANO_W + x) * 4 + 3];
  if (alpha(ZERO_ROW) === 0) return -1;
  let y = ZERO_ROW;
  while (y > 0 && alpha(y - 1) !== 0) y--;
  return y;
}

/** Copy dirty rectangles of the live raster into both halves of the ribbon canvas, and redraw the dotted photo-top
 *  line for the same columns on its own canvas. `image` wraps the scanner's own buffer, so nothing is copied twice. */
function paintRibbon(ribbon: Ctx, top: Ctx, image: ImageData, rgba: Uint8ClampedArray, rects: readonly DirtyRect[]): void {
  if (top) top.fillStyle = '#f4dea2';
  for (const r of rects) {
    for (const p of wrapPieces(r)) {
      // The dots of this piece, found once for both halves: -1 where a column has no dot.
      const tops: number[] = [];
      if (top) for (let x = p.x; x < p.x + p.w; x++) tops.push(Math.floor(x / 3) % 2 === 0 ? photoTopRow(rgba, x) : -1);   // one degree on, one off
      for (let copy = 0; copy < 2; copy++) {
        const dx = copy * PANO_W;
        if (ribbon) ribbon.putImageData(image, dx, 0, p.x, p.y, p.w, p.h);
        if (!top) continue;
        top.clearRect(dx + p.x, 0, p.w, PANO_H);
        for (let i = 0; i < tops.length; i++) if (tops[i] >= 0) top.fillRect(dx + p.x + i, tops[i] - 1, 1, 3);
      }
    }
  }
}

/** The amber tint over sensor-placed and blurred columns: one pixel per coverage cell, stretched in CSS. */
function paintTint(ctx: Ctx, cov: ArrayLike<number>): void {
  if (!ctx || typeof ImageData === 'undefined') return;
  const buf = new Uint8ClampedArray(TINT_W * 4);
  for (let i = 0; i < PROFILE_BINS; i++) {
    if (cov[i] !== PixClass.Sensor && cov[i] !== PixClass.Blurred) continue;
    for (let copy = 0; copy < 2; copy++) buf.set(TINT_RGBA, (copy * PROFILE_BINS + i) * 4);
  }
  ctx.putImageData(new ImageData(buf, TINT_W, 1), 0, 0);
}

/** The affine map for `setTransform` that sends a triangle's source vertices onto its ribbon vertices, or null for a
 *  degenerate source triangle. */
function triangleAffine(t: LiveTriangle): [number, number, number, number, number, number] | null {
  const [sx0, sy0, sx1, sy1, sx2, sy2] = t.src, [dx0, dy0, dx1, dy1, dx2, dy2] = t.dst;
  const u1x = sx1 - sx0, u1y = sy1 - sy0, u2x = sx2 - sx0, u2y = sy2 - sy0;
  const det = u1x * u2y - u1y * u2x;
  if (!(Math.abs(det) > 1e-9)) return null;
  const v1x = dx1 - dx0, v1y = dy1 - dy0, v2x = dx2 - dx0, v2y = dy2 - dy0;
  const a = (v1x * u2y - v2x * u1y) / det, c = (v2x * u1x - v1x * u2x) / det;
  const b = (v1y * u2y - v2y * u1y) / det, d = (v2y * u1x - v1y * u2x) / det;
  return [a, b, c, d, dx0 - a * sx0 - c * sy0, dy0 - b * sx0 - d * sy0];
}

/** Edges that belong to one triangle only: the outline of the mesh. Vertices are matched to 0.01 px. */
function outlineEdges(tris: readonly LiveTriangle[]): [number, number, number, number][] {
  const seen = new Map<string, { n: number; seg: [number, number, number, number] }>();
  const key = (x: number, y: number) => `${Math.round(x * 100)},${Math.round(y * 100)}`;
  for (const t of tris) {
    for (let k = 0; k < 3; k++) {
      const ax = t.dst[2 * k], ay = t.dst[2 * k + 1], bx = t.dst[(2 * k + 2) % 6], by = t.dst[(2 * k + 3) % 6];
      const ka = key(ax, ay), kb = key(bx, by);
      const id = ka < kb ? `${ka}|${kb}` : `${kb}|${ka}`;
      const e = seen.get(id);
      if (e) e.n++;
      else seen.set(id, { n: 1, seg: [ax, ay, bx, by] });
    }
  }
  return [...seen.values()].filter(e => e.n === 1).map(e => e.seg);
}

/** Clear the overlay and draw the live frame (4.11): per triangle `save`, a triangular clip, the affine transform,
 *  `drawImage(source, 0, 0)` at 50 % alpha, `restore`; then a thin white outline of the whole mesh. Returns true
 *  when something was drawn. */
function drawMesh(ctx: Ctx, w: number, h: number, tris: readonly LiveTriangle[] | null, source: CanvasImageSource | null): boolean {
  if (!ctx) return false;
  ctx.clearRect(0, 0, w, h);
  if (!tris || tris.length === 0 || !source) return false;
  ctx.globalAlpha = MESH_ALPHA;
  for (const t of tris) {
    const m = triangleAffine(t);
    if (!m) continue;
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(t.dst[0], t.dst[1]); ctx.lineTo(t.dst[2], t.dst[3]); ctx.lineTo(t.dst[4], t.dst[5]);
    ctx.closePath();
    ctx.clip();
    ctx.setTransform(m[0], m[1], m[2], m[3], m[4], m[5]);
    ctx.drawImage(source, 0, 0);
    ctx.restore();
  }
  ctx.globalAlpha = 1;
  ctx.beginPath();
  for (const e of outlineEdges(tris)) { ctx.moveTo(e[0], e[1]); ctx.lineTo(e[2], e[3]); }
  ctx.strokeStyle = '#ffffff';
  ctx.lineWidth = 1;
  ctx.stroke();
  return true;
}

// ---- Parts -------------------------------------------------------------------

const railPct = (deg: number) => (clamp(deg, RAIL_MIN_DEG, RAIL_MAX_DEG) - RAIL_MIN_DEG) / (RAIL_MAX_DEG - RAIL_MIN_DEG) * 100;

function Rail({ s }: { s: Snap }) {
  const colour = s.colour;
  const band = (kind: string, lo: number, hi: number) => (
    <span className="pano-rail-band" data-band={kind} style={{ bottom: `${px2(railPct(lo))}%`, height: `${px2(railPct(hi) - railPct(lo))}%` }} />
  );
  const label = s.elevationDeg === null
    ? 'Tilt: no reading yet'
    : `Tilt: ${Math.round(s.elevationDeg)} degrees up, target ${Math.round(s.targetPitchDeg)}`;
  return (
    <div className="pano-rail" data-testid="pano-rail" data-colour={colour} role="img" aria-label={label}>
      {band('amber', s.amberLo, s.amberHi)}
      {band('green', s.greenLo, s.greenHi)}
      <span className="pano-rail-target" style={{ bottom: `${px2(railPct(s.targetPitchDeg))}%` }} />
      {s.elevationDeg !== null && <span className="pano-rail-marker" data-testid="pano-rail-marker" style={{ bottom: `${px2(railPct(s.elevationDeg))}%` }} />}
    </div>
  );
}

const Cells = memo(function Cells({ cov }: { cov: Uint8Array }) {
  const out: ReactNode[] = new Array(PROFILE_BINS);
  for (let i = 0; i < PROFILE_BINS; i++) out[i] = <span key={i} className={CELL_CLASS[cov[i]] ?? CELL_CLASS[0]} />;
  return <>{out}</>;
});

function Pace({ s }: { s: Snap }) {
  const rm = s.rateMaxDegS > 0 ? s.rateMaxDegS : 40;
  const scale = rm * PACE_SCALE;
  const pct = (v: number) => `${px2(clamp(v, 0, scale) / scale * 100)}%`;
  const rate = s.rateDegS ?? 0;
  const label = s.pace === 'idle' ? 'Keep turning' : `${Math.round(rate)}°/s`;
  const zone = (name: string, from: number, to: number) => <i className="pano-pace-zone" data-zone={name} style={{ flexBasis: `${px2((to - from) / scale * 100)}%` }} />;
  return (
    <div className="pano-pace" data-testid="pano-pace" data-pace={s.pace} role="meter" aria-label="Turning speed"
      aria-valuemin={0} aria-valuemax={Math.round(scale)} aria-valuenow={Math.round(clamp(rate, 0, scale))} aria-valuetext={label}>
      <div className="pano-pace-track">
        <div className="pano-pace-zones">
          {zone('idle', 0, PACE_IDLE_DEG_S)}
          {zone('good', PACE_IDLE_DEG_S, 0.7 * rm)}
          {zone('fast', 0.7 * rm, rm)}
          {zone('too-fast', rm, scale)}
        </div>
        <span className="pano-pace-marker" data-testid="pano-pace-marker" style={{ left: pct(rate) }} />
      </div>
      <span className="pano-pace-label">{label}</span>
    </div>
  );
}

// ---- The component -----------------------------------------------------------

export function ScanView({ scanner, videoSlot, widthPx = DEFAULT_WIDTH_PX }: ScanViewProps): JSX.Element {
  const rootRef = useRef<HTMLDivElement>(null);
  const trackRef = useRef<HTMLDivElement>(null);
  const ribbonRef = useRef<HTMLCanvasElement>(null);
  const tintRef = useRef<HTMLCanvasElement>(null);
  const topRef = useRef<HTMLCanvasElement>(null);
  const liveRef = useRef<HTMLCanvasElement>(null);
  const headRef = useRef<HTMLSpanElement>(null);
  const [view, setView] = useState<ViewState>(() => nextView(null, scanner.status));
  const viewRef = useRef(view);
  const [measured, setMeasured] = useState(0);
  // The width the ribbon is laid out for: the box the page actually gives it (360 px phones), else the nominal one.
  const W = measured > 0 ? measured : widthPx;
  const g = useMemo(() => geometryFor(W), [W]);
  const wRef = useRef(W);
  const gRef = useRef(g);
  const scheduleRef = useRef<(() => void) | null>(null);
  // Per-mount drawing state: contexts, the ImageData over the scanner's buffer, what the overlay last drew.
  const work = useRef({
    ctx: {} as { ribbon?: Ctx; tint?: Ctx; top?: Ctx; live?: Ctx },
    image: null as { pix: Uint8ClampedArray; img: ImageData } | null,
    full: true, tint: true, drawnFrame: -1, drawnW: 0, heading: 0, transform: '',
  });

  useLayoutEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    const measure = () => setMeasured(Math.max(0, Math.floor(el.getBoundingClientRect().width)));
    measure();
    if (typeof ResizeObserver !== 'undefined') {
      const ro = new ResizeObserver(measure);
      ro.observe(el);
      return () => ro.disconnect();
    }
    window.addEventListener('resize', measure);
    return () => window.removeEventListener('resize', measure);
  }, []);

  // A new width resizes the overlay canvas, which clears it: draw the mesh again on the next frame.
  useLayoutEffect(() => {
    wRef.current = W;
    gRef.current = g;
    work.current.transform = '';
    scheduleRef.current?.();
  }, [W, g]);

  useEffect(() => {
    const w = work.current;
    let alive = true, raf = 0;
    w.full = true; w.tint = true; w.drawnFrame = -1; w.drawnW = 0;
    const ctxFor = (key: 'ribbon' | 'tint' | 'top' | 'live', el: HTMLCanvasElement | null): Ctx => {
      if (w.ctx[key] === undefined && el) w.ctx[key] = el.getContext('2d');
      return w.ctx[key] ?? null;
    };

    const frame = () => {
      raf = 0;
      if (!alive) return;
      const st = scanner.status;
      const geo = gRef.current, width = wRef.current;

      // Dirty rectangles of the live raster, copied into the ribbon canvas.
      const dirty = scanner.ribbon.takeDirty();
      const rects = w.full ? [FULL_RECT] : dirty;
      w.full = false;
      if (rects.length > 0 && typeof ImageData !== 'undefined') {
        const pix = scanner.ribbon.pixels();
        if (pix.length === PANO_W * PANO_H * 4) {
          if (!w.image || w.image.pix !== pix) w.image = { pix, img: new ImageData(pix, PANO_W, PANO_H) };
          paintRibbon(ctxFor('ribbon', ribbonRef.current), ctxFor('top', topRef.current), w.image.img, pix, rects);
        }
      }

      // The scroll and the heading marker follow the live heading.
      if (st.headingDeg !== null && Number.isFinite(st.headingDeg)) w.heading = st.headingDeg;
      const transform = `translate3d(${scrollPx(w.heading, geo.ppd).toFixed(2)}px, 0, 0)`;
      if (trackRef.current && transform !== w.transform) { trackRef.current.style.transform = transform; w.transform = transform; }
      if (headRef.current) {
        headRef.current.style.display = st.headingDeg === null ? 'none' : '';
        headRef.current.style.left = `${(mod(w.heading, 360) / 360 * 100).toFixed(2)}%`;
      }

      // The live frame, only in a live phase and only after a new camera frame (or a resize, which cleared the
      // overlay). Outside the live phases the group is hidden: the mesh is not projected, drawn or timed, so a
      // hidden draw cannot skew `drawMs`, and the first live frame draws whatever `frameNo` was last drawn.
      if (!LIVE_PHASES.includes(st.phase)) {
        w.drawnFrame = -1;
      } else if (st.frameNo !== w.drawnFrame || width !== w.drawnW) {
        w.drawnFrame = st.frameNo;
        w.drawnW = width;
        const t0 = performance.now();
        const tris = scanner.ribbon.liveMesh(ribbonViewFor(w.heading, width));
        if (drawMesh(ctxFor('live', liveRef.current), width, Math.round(geo.boxH), tris, scanner.ribbon.liveSource())) {
          scanner.noteMeshDraw(performance.now() - t0);
        }
      }

      // What React shows: only a change that would alter the screen causes a render.
      const next = nextView(viewRef.current, st);
      if (w.tint || next.cov !== viewRef.current.cov) { paintTint(ctxFor('tint', tintRef.current), next.cov); w.tint = false; }
      if (next !== viewRef.current) { viewRef.current = next; setView(next); }
    };

    const schedule = () => { if (alive && raf === 0) raf = requestAnimationFrame(frame); };
    scheduleRef.current = schedule;
    const unsubscribe = scanner.subscribe(schedule);
    schedule();
    return () => {
      alive = false;
      unsubscribe();
      if (raf !== 0) cancelAnimationFrame(raf);
      scheduleRef.current = null;
    };
  }, [scanner]);

  const s = view.snap;
  const live = LIVE_PHASES.includes(s.phase);
  const shortFov = s.shortFovDeg > 0 && s.shortFovDeg < 180 ? s.shortFovDeg : FALLBACK_SHORT_FOV_DEG;
  // The slit is the central 6 degrees; in tangent space it is this fraction of the picture's short side.
  const slitHalf = Math.tan(SLIT_HALF_DEG * Math.PI / 180) / Math.tan(shortFov / 2 * Math.PI / 180) / 2 * 100;
  const roll = s.rollDeg;
  // A scan azimuth is a magnetic one minus the offset (status.northOffsetDeg is added to go the other way).
  const northAt = (bearing: number) => `${px2(mod(bearing - (s.northOffsetDeg ?? 0), 360) / 360 * 100)}%`;

  return (
    <div className="pano-view" ref={rootRef} data-testid="pano-view" data-phase={s.phase} style={{ maxWidth: widthPx }}>
      <div className="pano-top">
        <div className="pano-preview" data-testid="pano-preview">
          {videoSlot}
          <span className="pano-slit" data-side="left" aria-hidden="true" style={{ left: `${px2(50 - slitHalf)}%` }} />
          <span className="pano-slit" data-side="right" aria-hidden="true" style={{ left: `${px2(50 + slitHalf)}%` }} />
          <span className="pano-roll" data-testid="pano-roll" aria-hidden="true" hidden={roll === null}
            data-alert={s.rollAlert ? 'true' : 'false'}
            style={{ transform: `rotate(${clamp(roll ?? 0, -90, 90)}deg)` }} />
        </div>
        <Rail s={s} />
      </div>

      <div className="pano-live-group" data-testid="pano-live-group" hidden={!live}>
        <div className="pano-ribbon" data-testid="pano-ribbon" style={{ width: W, height: g.boxH }}>
          <div className="pano-ribbon-track" ref={trackRef} data-testid="pano-ribbon-track" style={{ top: g.trackTop, width: g.canvasW, height: g.canvasH }}>
            <canvas className="pano-ribbon-canvas" ref={ribbonRef} data-testid="pano-ribbon-canvas" width={COPIES_W} height={PANO_H}
              style={{ width: g.canvasW, height: g.canvasH }} />
            <canvas className="pano-tint" ref={tintRef} data-testid="pano-tint" width={TINT_W} height={1}
              style={{ width: g.canvasW, height: g.canvasH }} />
            <canvas className="pano-photo-top" ref={topRef} data-testid="pano-photo-top" width={COPIES_W} height={PANO_H}
              style={{ width: g.canvasW, height: g.canvasH }} />
            {view.tall.flatMap((span, i) => {
              const len = span.to >= span.from ? span.to - span.from : span.to + 360 - span.from;
              // Three copies, so a span that wraps past 360 shows whichever 150 degrees the window is on.
              return [-360, 0, 360].map(shift => (
                <span key={`${i}:${shift}`} className="pano-tall" data-testid="pano-tall"
                  style={{ left: px2((span.from + shift) * g.ppd), width: Math.max(2, px2(len * g.ppd)), top: -g.trackTop }} />
              ));
            })}
          </div>
          <canvas className="pano-live" ref={liveRef} data-testid="pano-live-canvas" width={Math.round(W)} height={Math.round(g.boxH)}
            style={{ width: W, height: g.boxH }} />
        </div>

        <div className="pano-bar" data-testid="pano-bar" role="progressbar" aria-label="Turn covered"
          aria-valuemin={0} aria-valuemax={360} aria-valuenow={s.coveredDeg}>
          <div className="pano-cells"><Cells cov={view.cov} /></div>
          <span className="pano-bar-head" ref={headRef} data-testid="pano-bar-head" />
          <div className="pano-compass-row">
            {s.northOffsetDeg !== null && COMPASS_POINTS.map(p => (
              <span key={p.label} className="pano-compass" data-testid="pano-compass" data-dir={p.label} style={{ left: northAt(p.bearing) }}>{p.label}</span>
            ))}
          </div>
        </div>

        <Pace s={s} />
      </div>

      <p className="pano-cue" role="status" data-testid="pano-cue" data-kind={s.cueKind} data-key={s.cueKey}>{s.cue}</p>
    </div>
  );
}
