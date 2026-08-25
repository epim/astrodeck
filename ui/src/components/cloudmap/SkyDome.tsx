// SkyDome.tsx — the sky hemisphere with the cloud model painted on it and the
// scope's pointing marked, drawn on a 2D canvas.
//
// The geometry lives in lib/domeProjection.ts and is unit-tested without a
// browser, because the failures that matter here are the ones a screenshot
// cannot reveal: a dome drawn upside down, azimuth mirrored so eastern cloud is
// painted west, cells offset half a step. This file is the part that can only
// be got wrong in pixels.
//
// Canvas rather than SVG: a 15x36 grid is 540 quads and the panel repaints on a
// poll, so this is a thousand fills a minute in the DOM against one bitmap.
import { memo, useEffect, useRef } from "react";

import {
  NO_DATA_HATCH,
  STALE_CLOUD_ALPHA,
  domeCells,
  domeExtent,
  occlusionStyle,
  projectAltAz,
  type DomeGrid,
} from "../../lib/domeProjection";

export interface SkyDomeProps {
  grid: DomeGrid | null;
  /** Where the scope is looking, if it is connected. */
  pointing?: { alt: number; az: number } | null;
  /** Drawn dimmer than the pointing marker: where the run goes next. */
  target?: { alt: number; az: number; name?: string } | null;
  /** No data at all -- draw the empty dome rather than nothing, so the panel
   *  keeps its shape and the operator can see the model is simply off. */
  emptyNote?: string;
  /** The granule is old. The CLOUD recedes; the grid, the cardinals and the
   *  pointing marker do not, because those are still true. */
  stale?: boolean;
  /** What to write across a stale dome, e.g. "157m old". */
  staleNote?: string;
  height?: number;
}

const CARDINALS: [string, number][] = [["N", 0], ["E", 90], ["S", 180], ["W", 270]];

/** Altitude rings worth drawing. 30 and 60 are the ones people reason in;
 *  the horizon and zenith come free from the dome's own outline. */
const ALT_RINGS = [30, 60];

/** Text with a dark halo. Cardinals and labels land on whatever the weather
 *  happens to be painting -- a bright overcast dome swallowed the "N" entirely
 *  at low alpha, which is the one glyph that tells you which way you are
 *  looking. Stroke first, fill second. */
function haloText(ctx: CanvasRenderingContext2D, text: string, x: number,
                  y: number, fill: string): void {
  ctx.lineJoin = "round";
  ctx.lineWidth = 3;
  ctx.strokeStyle = "rgba(8,10,16,0.85)";
  ctx.strokeText(text, x, y);
  ctx.fillStyle = fill;
  ctx.fillText(text, x, y);
}

/** The "no reading" hatch, as a repeatable canvas pattern.
 *
 *  Built once per paint rather than per cell: at 540 cells the allocation, not
 *  the fill, is what would cost. The two corner segments are what make the tile
 *  seamless -- without them the diagonal breaks at every tile boundary and the
 *  hatch reads as a dotted grid instead of stripes. */
function hatchPattern(ctx: CanvasRenderingContext2D): CanvasPattern | null {
  const t = NO_DATA_HATCH.tile;
  const tile = document.createElement("canvas");
  tile.width = t;
  tile.height = t;
  const tc = tile.getContext("2d");
  if (!tc) return null;
  tc.fillStyle = NO_DATA_HATCH.ground;
  tc.fillRect(0, 0, t, t);
  tc.strokeStyle = NO_DATA_HATCH.stroke;
  tc.lineWidth = NO_DATA_HATCH.lineWidth;
  tc.beginPath();
  tc.moveTo(0, t); tc.lineTo(t, 0);
  tc.moveTo(-1, 1); tc.lineTo(1, -1);
  tc.moveTo(t - 1, t + 1); tc.lineTo(t + 1, t - 1);
  tc.stroke();
  return ctx.createPattern(tile, "repeat");
}

function ringPath(ctx: CanvasRenderingContext2D, altDeg: number,
                  cx: number, cy: number, r: number): void {
  ctx.beginPath();
  for (let az = 0; az <= 360; az += 3) {
    const p = projectAltAz(altDeg, az, cx, cy, r);
    if (az === 0) ctx.moveTo(p.x, p.y); else ctx.lineTo(p.x, p.y);
  }
  ctx.closePath();
}

export const SkyDome = memo(function SkyDome({
  grid, pointing, target, emptyNote, stale = false, staleNote, height = 300,
}: SkyDomeProps) {
  const ref = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const cv = ref.current;
    if (!cv) return;
    const parentW = cv.parentElement?.clientWidth ?? 0;
    const cssW = Math.max(220, parentW || 320);
    const cssH = height;
    // Device pixels, so the dome is not a blurry oval on a retina panel.
    const dpr = (typeof window !== "undefined" && window.devicePixelRatio) || 1;
    cv.width = Math.round(cssW * dpr);
    cv.height = Math.round(cssH * dpr);
    cv.style.width = `${cssW}px`;
    cv.style.height = `${cssH}px`;
    const ctx = cv.getContext("2d");
    if (!ctx) return;                       // headless/jsdom: nothing to draw
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);

    // Size against the dome's REAL extent. The highest point on screen is
    // altitude (90 - tilt) due north, not the zenith -- see domeExtent.
    const ext = domeExtent();
    const r = Math.min((cssW - 24) / 2, (cssH - 28) / (ext.top + ext.bottom));
    const cx = cssW / 2;
    const cy = 14 + r * ext.top;

    // ---- the far half of the horizon, so the dome reads as a solid volume
    ctx.strokeStyle = "rgba(150,170,200,0.18)";
    ctx.lineWidth = 1;
    ringPath(ctx, 0, cx, cy, r);
    ctx.stroke();

    // ---- cloud cells, far half first so the near half draws over it
    if (grid) {
      const hatch = hatchPattern(ctx);
      const cells = domeCells(grid);
      const drawn = cells.map((c) => {
        const mid = projectAltAz((c.altLo + c.altHi) / 2, (c.azLo + c.azHi) / 2,
                                 cx, cy, r);
        return { c, depth: mid.depth };
      });
      drawn.sort((a, b) => a.depth - b.depth);   // far (negative) first
      for (const { c, depth } of drawn) {
        const corners = [
          projectAltAz(c.altLo, c.azLo, cx, cy, r),
          projectAltAz(c.altLo, c.azHi, cx, cy, r),
          projectAltAz(c.altHi, c.azHi, cx, cy, r),
          projectAltAz(c.altHi, c.azLo, cx, cy, r),
        ];
        ctx.beginPath();
        ctx.moveTo(corners[0].x, corners[0].y);
        for (let i = 1; i < corners.length; i++) ctx.lineTo(corners[i].x, corners[i].y);
        ctx.closePath();
        const style = occlusionStyle(c.p);
        // The back of the dome is seen THROUGH the front. Halving its opacity
        // keeps it legible as context without letting a cloud bank behind the
        // observer read as one overhead.
        ctx.globalAlpha = (depth > 0 ? 1 : 0.45) * (stale ? STALE_CLOUD_ALPHA : 1);
        if (style.kind === "hatch") {
          // A gap is hatched, not shaded. See NO_DATA_HATCH for why: a shade
          // this faint is indistinguishable from clear sky, and an entire dome
          // of gaps is exactly what a site outside GOES coverage returns.
          ctx.fillStyle = hatch ?? style.ground;
          ctx.fill();
        } else {
          ctx.fillStyle = style.color;
          ctx.fill();
        }
      }
      ctx.globalAlpha = 1;
    }

    // ---- altitude rings and the horizon, over the cloud
    ctx.strokeStyle = "rgba(160,180,210,0.22)";
    for (const alt of ALT_RINGS) { ringPath(ctx, alt, cx, cy, r); ctx.stroke(); }
    ctx.strokeStyle = "rgba(170,190,220,0.45)";
    ringPath(ctx, 0, cx, cy, r);
    ctx.stroke();

    // ---- meridian and the prime vertical, for orientation
    ctx.strokeStyle = "rgba(160,180,210,0.16)";
    for (const az of [0, 90]) {
      ctx.beginPath();
      for (let alt = 0; alt <= 180; alt += 3) {
        const a = alt <= 90 ? alt : 180 - alt;
        const z = alt <= 90 ? az : (az + 180) % 360;
        const p = projectAltAz(a, z, cx, cy, r);
        if (alt === 0) ctx.moveTo(p.x, p.y); else ctx.lineTo(p.x, p.y);
      }
      ctx.stroke();
    }

    // ---- cardinal letters, on the horizon where they belong
    ctx.font = "600 10px ui-monospace, monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    for (const [label, az] of CARDINALS) {
      const p = projectAltAz(0, az, cx, cy, r);
      // Push clear of the rim, and further for the FAR cardinals: the dome
      // bulges above its own back horizon, so a 9 px nudge left "N" sitting on
      // painted cloud rather than beside the outline.
      const dx = p.x - cx, dy = p.y - cy;
      const len = Math.hypot(dx, dy) || 1;
      const out = p.facing ? 10 : 16;
      haloText(ctx, label, p.x + (dx / len) * out, p.y + (dy / len) * out,
               p.facing ? "rgba(210,225,245,0.9)" : "rgba(190,205,230,0.72)");
    }

    // ---- the next target, dimmer
    if (target && target.alt >= 0) {
      const p = projectAltAz(target.alt, target.az, cx, cy, r);
      ctx.strokeStyle = "rgba(140,200,255,0.55)";
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.arc(p.x, p.y, 5, 0, Math.PI * 2); ctx.stroke();
      if (target.name) {
        ctx.font = "500 9px ui-monospace, monospace";
        haloText(ctx, target.name, p.x, p.y - 12, "rgba(170,210,255,0.85)");
      }
    }

    // ---- where the scope is actually looking. Drawn last: it is the one mark
    // that must never be hidden behind a cloud cell.
    if (pointing && pointing.alt >= 0) {
      const p = projectAltAz(pointing.alt, pointing.az, cx, cy, r);
      const cross = () => {
        ctx.beginPath(); ctx.arc(p.x, p.y, 7, 0, Math.PI * 2); ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(p.x - 11, p.y); ctx.lineTo(p.x - 3, p.y);
        ctx.moveTo(p.x + 3, p.y); ctx.lineTo(p.x + 11, p.y);
        ctx.moveTo(p.x, p.y - 11); ctx.lineTo(p.x, p.y - 3);
        ctx.moveTo(p.x, p.y + 3); ctx.lineTo(p.x, p.y + 11);
        ctx.stroke();
      };
      // Dark underlay first: against a fully overcast dome the gold alone is
      // nearly invisible, and this is the one mark that must always read.
      ctx.strokeStyle = "rgba(8,10,16,0.8)";
      ctx.lineWidth = 4;
      cross();
      ctx.strokeStyle = "rgba(255,214,102,0.98)";
      ctx.lineWidth = 1.6;
      cross();
    }

    if (grid && stale && staleNote) {
      ctx.font = "600 11px ui-monospace, monospace";
      haloText(ctx, `${staleNote} - not the sky now`, cx, cy - r * 0.62,
               "rgba(240,190,120,0.92)");
    }

    if (!grid && emptyNote) {
      ctx.fillStyle = "rgba(190,205,225,0.55)";
      ctx.font = "500 11px ui-monospace, monospace";
      ctx.fillText(emptyNote, cx, cy - r * 0.35);
    }
  }, [grid, pointing, target, emptyNote, stale, staleNote, height]);

  return (
    <canvas
      ref={ref}
      role="img"
      aria-label={
        grid
          ? (stale
              ? `Sky dome showing modelled cloud occlusion from a stale reading${
                  staleNote ? `, ${staleNote}` : ""}, with the telescope's pointing marked`
              : "Sky dome showing modelled cloud occlusion, with the telescope's pointing marked")
          : (emptyNote || "Sky dome, no cloud data")
      }
      className="block mx-auto"
    />
  );
});
