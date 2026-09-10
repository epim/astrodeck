// inspectModel.ts - everything the INSPECT toolbar, histogram and readouts
// DECIDE, with no React in it (wave R7, T-R7-19).
//
// WHY A MODEL FILE AT ALL. The seven legacy widgets this area replaces
// (`components/preview/{PreviewToolbar,StretchHistogram,PreviewMeta,FrameStats,
// FrameFilmstrip,LiveStackReadout,FocusVerdict}.tsx`) carry their judgements
// inside their JSX: which download row would 404, why the clip mask cannot be
// drawn, when a max reading is really saturation, which repair to put in front
// of the operator. Those judgements are the part that took regressions to get
// right, so they are lifted here VERBATIM and graded by a test, while only the
// presentation is rebuilt in the design's vocabulary. The legacy files are not
// edited and keep serving `#/classic`.
//
// EM-DASHES. Four shared logic modules this area reads (`lib/focusVerdict.ts`,
// `components/preview/focusAdvice.ts`, `components/preview/linearReason.ts`,
// `lib/liveStack.ts`) were written before the copy rule and put an em-dash in
// their sentences. Those modules are LOGIC (wave plan section 2.1) and must not
// be edited, so `hyphens()` normalises their punctuation at the boundary. Every
// string this file writes itself already uses hyphens.

import type { OverlayToggles, PreviewInfo, StretchParams } from "../../../../types";
import { u } from "../../../../lib/base";
import { shareQuery } from "../../../../lib/share";
import { isExactWysiwyg, renderPath } from "../../../../lib/renderLevels";
import { linearUnavailableReason, type LinearState } from "../../../../components/preview/linearReason";
import { adviceFor, adviceLabel } from "../../../../components/preview/focusAdvice";
import { clippedFloor } from "../../../../components/preview/clippedFloor";
import { defocusMessage, focusState } from "../../../../lib/focusVerdict";
import { autofocusLevel, type AfLevel } from "../../../../lib/autofocus";
import { alignmentState, formatLiveStack } from "../../../../lib/liveStack";
import type { LiveStackInfo } from "../../../../types";
import type { Tone } from "../../../ui";

/** Copy rule at the boundary: hyphens, never em-dashes, in anything rendered. */
export function hyphens(s: string): string {
  return s.replace(/\s*[—–]\s*/g, " - ");
}

// --------------------------------------------------------------- the one lock
// THE ONE LEGITIMATE IMPOSSIBILITY on this screen, and it is still not a native
// `disabled`. A pre-stretched frame (NINA, or any source that hands us an
// already-rendered 8-bit image) carries no linear data, so black / mid / white
// have nothing to be re-derived FROM. That is a property of the frame, not a
// permission - but a control that greys out with no reason is the same dead
// rectangle either way, so it renders honest-locked like everything else:
// focusable, `aria-disabled`, and a press states this sentence.
export const NINA_LOCK_REASON =
  "this frame arrived already stretched, so black/mid/white cannot be re-derived";

// ------------------------------------------------------- what the rig still has
// `hub._trim_previews` enforces two memory caps on a Pi: the whole ring entry is
// dropped once a frame is PREVIEW_DISPLAY_KEEP behind the newest (from then on
// the display bytes, /png, /share.jpg and /fits all 404), and the heavy
// lossless/linear arrays are freed far sooner, at PREVIEW_LINEAR_KEEP.
//
// Every capability flag on a PreviewInfo (`has_lossless`, `data_is_linear`,
// `saved_local`) is stamped when the frame was CAPTURED and never revised, so a
// pinned frame goes on offering Full-res, Lossless and Stretched PNG long after
// the bytes behind them were freed, and an `<a download>` that 404s reports
// nothing at all - the tap just does nothing. Mirroring the two constants is the
// only way the toolbar can tell; keep them in step with `hub.py`.
export const DISPLAY_KEEP = 8; // hub.PREVIEW_DISPLAY_KEEP
export const LINEAR_KEEP = 2; // hub.PREVIEW_LINEAR_KEEP

// ------------------------------------------------------------------- overlays

export type ToggleId = "stars" | "clip" | "reticle" | "centerMark" | "tilt" | "bahtinov" | "objects";

export interface ToggleRow {
  id: ToggleId;
  /** The word on the control. Never a glyph alone. */
  label: string;
  /** What the overlay draws - the thing the pixels do not say. */
  hint: string;
  /** null => live. A sentence => this frame cannot support it, for THIS reason. */
  lockedReason: string | null;
}

/** Why the clip mask cannot be drawn on THIS frame, naming which half is
 *  missing. The legacy toolbar said "Clip mask needs linear data + known full
 *  well" for both cases, which sends an operator to look at the wrong thing. */
export function clipLockReason(p: PreviewInfo | null): string | null {
  if (!p) return "no frame has arrived yet";
  if (!p.data_is_linear) return "the clip mask needs linear data, and this frame arrived already stretched";
  if (p.full_well == null) return "the clip mask needs the sensor's full-well depth, which this camera does not report";
  return null;
}

/** The annotation set, as data. Availability carries its REASON: a dimmed row
 *  that will not say why is the defect the honest-disabled rule exists for.
 *
 *  Two rows (Spikes, Objects) EXIST only when the frame carries their data. A
 *  permanently locked "Objects" row on a rig that has never plate-solved a light
 *  reads as a broken feature rather than an absent measurement. */
export function toggleRows(
  p: PreviewInfo | null,
  avail: { starsAvailable: boolean; clipAvailable: boolean },
): ToggleRow[] {
  const rows: ToggleRow[] = [
    {
      id: "stars", label: "STARS", hint: "a ring per detected star, sized by its HFR",
      lockedReason: avail.starsAvailable ? null
        : "this frame carries no per-star measurements, so there are no rings to draw",
    },
    {
      id: "clip", label: "CLIP", hint: "a mask over pixels that hit full well - blown highlights",
      lockedReason: avail.clipAvailable ? null : clipLockReason(p),
    },
    { id: "reticle", label: "RETICLE", hint: "a full crosshair across the frame", lockedReason: null },
    { id: "centerMark", label: "CENTER", hint: "a small mark at the exact frame centre", lockedReason: null },
    {
      id: "tilt", label: "TILT", hint: "a corner-to-corner heatmap of star shape - sensor tilt",
      lockedReason: p?.tilt ? null
        : "this frame carries no tilt measurement - it needs stars spread into the corners",
    },
  ];
  if (p?.bahtinov?.geom) {
    rows.push({
      id: "bahtinov", label: "SPIKES",
      hint: "the fitted Bahtinov spike lines and their crossing", lockedReason: null,
    });
  }
  if (p?.field?.objects?.length) {
    rows.push({
      id: "objects", label: "OBJECTS",
      hint: "catalogued objects marked where this frame's plate solve puts them", lockedReason: null,
    });
  }
  return rows;
}

/** Is this overlay on AND drawable on this frame?
 *
 *  The stored preference and the overlay actually being drawn are two different
 *  facts, and the legacy summary reported the first while claiming the second:
 *  turn Stars on (it persists), then let a NINA frame arrive with no star list,
 *  and the stage drew no rings while the control still read engaged. A row the
 *  frame cannot support reads OFF here, and nothing is written to the store, so
 *  the preference is intact and lights up again on the first frame that carries
 *  the data.
 *
 *  `bahtinov` is opt-OUT (undefined reads as on), so it cannot be tested for
 *  truthiness like the others. */
export function toggleOn(overlays: OverlayToggles, row: ToggleRow): boolean {
  if (row.lockedReason) return false;
  if (row.id === "bahtinov") return overlays.bahtinov !== false;
  return !!(overlays as unknown as Record<string, boolean>)[row.id];
}

/** The store patch a press produces. Never called for a locked row. */
export function toggleNext(overlays: OverlayToggles, row: ToggleRow): Partial<OverlayToggles> {
  if (row.id === "bahtinov") return { bahtinov: overlays.bahtinov === false };
  const cur = (overlays as unknown as Record<string, boolean>)[row.id];
  return { [row.id]: !cur } as Partial<OverlayToggles>;
}

// ---------------------------------------------------------------------- zoom

/** Why the zoom cluster cannot act, or null when it can.
 *
 *  Non-null whenever the stage has no live frame of its own: the logo and the
 *  mid-run stand-in are painted OUTSIDE `.preview-transform`, so these controls
 *  move nothing, and a percentage that steps while the picture does not is the
 *  toolbar asserting a lie. */
export function zoomLockReason(hasFrame: boolean): string | null {
  return hasFrame ? null : "there is nothing on the stage to zoom yet";
}

/** The zoom readout. A dash, not a number, while the cluster is inert: the
 *  readout is the only control in the cluster that ASSERTS something, and "100%"
 *  over a picture the stage is not transforming is a wrong reading rather than a
 *  disabled one. */
export function zoomText(scalePct: number, locked: string | null): string {
  return locked ? "-" : `${scalePct}%`;
}

/** The linear-data facts the magnifier and the full-res export both gate on.
 *  Three ways to be false, and the legacy tooltips blamed NINA for all three. */
export function linearState(p: PreviewInfo | null): LinearState {
  return { hasFrame: !!p, isNina: !!p?.is_stretched, isLinear: !!p?.data_is_linear };
}

export function magnifierLockReason(p: PreviewInfo | null, available: boolean): string | null {
  if (available) return null;
  const r = linearUnavailableReason(linearState(p), "The magnifier");
  return r ? hyphens(r) : "the magnifier is unavailable on this frame";
}

// ------------------------------------------------------------------ downloads

export interface DownloadRow {
  id: string;
  label: string;
  /** null when the row is locked. Never a link the rig would 404. */
  href: string | null;
  /** The `download` attribute's filename. */
  filename?: string;
  /** What this export is, where that is not obvious from the label. */
  hint?: string;
  lockedReason: string | null;
  /** The one row the design fills: the shareable first-light JPEG. */
  primary?: boolean;
}

export interface DownloadInput {
  preview: PreviewInfo | null;
  /** The newest frame THIS browser has seen. */
  liveId: number | null;
  linkDown: boolean;
  stretch: StretchParams;
  shareMeta?: { target?: string; subs?: number };
}

/** How far behind the newest frame this one is. A client that missed frames can
 *  only ever UNDER-estimate the gap: it may still offer a download that 404s,
 *  but it can never hide one that would have worked. */
export function framesBehind(id: number | null | undefined, liveId: number | null): number {
  return id != null && liveId != null ? Math.max(0, liveId - id) : 0;
}

/** Why the whole Download control is dead. Once the ring entry is gone EVERY
 *  item behind it 404s - including the share JPEG and FITS, which have no
 *  capability flag of their own - so the honest place to say so is the trigger,
 *  not five separate rows. */
export function downloadLockReason(inp: DownloadInput): string | null {
  const id = inp.preview?.id;
  if (id == null) return "there is no frame to download yet";
  if (inp.linkDown) return "the rig is not reachable, so nothing can be fetched from it";
  const behind = framesBehind(id, inp.liveId);
  if (behind >= DISPLAY_KEEP) {
    return `frame #${id} is no longer on the rig: only the last ${DISPLAY_KEEP} frames stay in memory, `
      + `and this one is ${behind} back. Return to live, or open the saved sub from the gallery`;
  }
  return null;
}

/** The rows, each already knowing whether it can be served. */
export function downloadRows(inp: DownloadInput): DownloadRow[] {
  const p = inp.preview;
  if (!p) return [];
  const id = p.id;
  const behind = framesBehind(id, inp.liveId);
  const heavyFreed = behind >= LINEAR_KEEP;
  const heavyFreedReason = (what: string) =>
    `${what} needs the full-quality copy of frame #${id}, and the rig keeps that for the latest `
    + `${LINEAR_KEEP} frames only - this one is ${behind} frames back. Return to live to export `
    + "the current frame";

  // /render.png bakes the retained LINEAR array at explicit levels, so it exists
  // only on the linear path and only while that array is still held. Same
  // capability gate as the client LUT canvas and the clip mask - one truth.
  const renderCapable = !!p.data_is_linear && !p.is_stretched;
  const renderAvailable = renderCapable && !heavyFreed;
  // WYSIWYG honesty: in Auto with neutral Brightness the server reproduces the
  // on-screen stretch EXACTLY (it replays `preview.auto_levels`). In Manual - or
  // Auto with a Brightness nudge - the screen is a composition and the server's
  // single linear pass can only match it very closely. Say so; never claim
  // pixel-identity we cannot deliver.
  const renderHint = isExactWysiwyg(inp.stretch)
    ? "the image exactly as you see it, at full sensor resolution"
    : "full sensor resolution at your current levels, baked as a very close match rather than pixel-identical";

  // The stretched-PNG route only serves a real PNG when a lossless base is still
  // held OR the frame's own bytes are already PNG. For a JPEG source it 404s
  // from the start; for a linear frame it starts 404-ing once the base is freed.
  const pngCapable = p.has_lossless || p.mime === "image/png";
  const pngAvailable = (p.has_lossless && !heavyFreed) || p.mime === "image/png";

  const rows: DownloadRow[] = [
    {
      id: "render", label: "FULL-RES PNG", hint: renderHint,
      href: renderAvailable ? u(renderPath(id, inp.stretch, p)) : null,
      filename: `astrodeck_${id}.png`,
      lockedReason: renderAvailable ? null
        : renderCapable ? heavyFreedReason("A full-res export")
          : (hyphens(linearUnavailableReason(linearState(p), "A full-res export")
            ?? "a full-res export is unavailable on this frame")),
    },
    {
      id: "share", label: "SAVE FIRST LIGHT", hint: "a captioned JPEG sized for sharing",
      href: u(`/api/preview/${id}/share.jpg${shareQuery(inp.shareMeta?.target, inp.shareMeta?.subs)}`),
      filename: `firstlight_${id}.jpg`, lockedReason: null, primary: true,
    },
    {
      id: "png", label: "STRETCHED PNG", hint: "the display image at preview resolution",
      href: pngAvailable ? u(`/api/preview/${id}/png`) : null,
      filename: `preview_${id}.png`,
      lockedReason: pngAvailable ? null
        : pngCapable ? heavyFreedReason("A PNG export")
          : "this frame is JPEG-only, so there is no lossless source to export a PNG from",
    },
  ];
  if (p.has_lossless) {
    rows.push({
      id: "lossless", label: "LOSSLESS PNG", hint: "the undamaged display encode",
      href: heavyFreed ? null : u(`/api/preview/${id}/lossless.png`),
      filename: `preview_${id}_lossless.png`,
      lockedReason: heavyFreed ? heavyFreedReason("The lossless copy") : null,
    });
  }
  rows.push(
    p.saved_local
      ? {
        id: "fits", label: "FITS", hint: "the raw sub, as the rig saved it",
        href: u(`/api/preview/${id}/fits`), filename: `preview_${id}.fits`, lockedReason: null,
      }
      : {
        id: "fits", label: "FITS", hint: "the raw sub, as the rig saved it", href: null,
        lockedReason: "this frame's FITS was saved on the NINA host, not on the rig, so it cannot be fetched here",
      },
  );
  return rows;
}

// ------------------------------------------------------------------ histogram

/** The log-scaled histogram outline. Lifted from `StretchHistogram` unchanged -
 *  this is the plot, and the plot is a KEEP (wave plan section 2.3). */
export function histogramPath(data: number[] | undefined, w: number, h: number): string {
  const bins = data ?? [];
  if (!bins.length) return "";
  const logMax = Math.log10(Math.max(...bins) + 1);
  const bw = w / bins.length;
  let path = `M0,${h}`;
  for (let i = 0; i < bins.length; i++) {
    const bh = logMax > 0 ? (Math.log10(bins[i] + 1) / logMax) * h : 0;
    const x = i * bw;
    path += ` L${x.toFixed(2)},${(h - bh).toFixed(2)} L${(x + bw).toFixed(2)},${(h - bh).toFixed(2)}`;
  }
  path += ` L${w},${h} Z`;
  return path;
}

/** The dashed MTF transfer curve drawn over the plot in Advanced. */
export function curveFrom(sample: (x01: number) => number, w: number, h: number, steps = 64): string {
  let p = "";
  for (let i = 0; i <= steps; i++) {
    const x01 = i / steps;
    p += `${i === 0 ? "M" : "L"}${(x01 * w).toFixed(1)},${((1 - sample(x01)) * h).toFixed(1)}`;
  }
  return p;
}

/** Which domain the bars are counted in. We never call a stretched histogram
 *  "linear". */
export function histogramDomainLine(p: PreviewInfo | null): string {
  return (p?.histogram_domain ?? "display") === "linear"
    ? "counts of the linear data"
    : "counts of the displayed image";
}

/** Does this frame really rail? `full_well` derived, never a hardcoded 65535. */
export function isClipped(p: PreviewInfo | null): boolean {
  return !!p && p.full_well != null && p.data_is_linear && p.stats.max >= p.full_well;
}

// ------------------------------------------------------------------- readouts

/** A statistic the rig did not report is `--`, never a fabricated number.
 *  `Number(undefined)` is NaN and `Number(null)` is 0 - the second is the
 *  dangerous one, because 0 ADU is a plausible reading. */
export function statText(v: number | null | undefined, digits = 0): string {
  if (v == null || typeof v !== "number" || !Number.isFinite(v)) return "--";
  return digits > 0 ? v.toFixed(digits) : String(Math.round(v));
}

export interface MetaTile { label: string; value: string; sub?: string }

/** `PreviewMeta`'s line, as the design's readout row. */
export function metaTiles(p: PreviewInfo): MetaTile[] {
  return [
    {
      label: "SIZE", value: `${statText(p.data_width)}x${statText(p.data_height)}`,
      sub: p.bayer_pattern ? `OSC ${p.bayer_pattern}` : undefined,
    },
    { label: "EXPOSURE", value: `${statText(p.exposure_s, p.exposure_s < 1 ? 2 : 0)}s` },
    { label: "GAIN", value: statText(p.gain) },
    { label: "BIN", value: statText(p.binning) },
  ];
}

/** The header's live line: where the frame came from and what state its pixels
 *  are in. Neither is in the readout row, so this repeats nothing. */
export function sourceLine(p: PreviewInfo): string {
  const parts: string[] = [p.source === "nina" ? "NINA" : p.source];
  parts.push(p.is_stretched ? "already stretched" : p.data_is_linear ? "linear data" : "display data");
  if (p.saved_local === false && p.saved_path) parts.push("saved on the host");
  return parts.join(" · ");
}

export interface StatTile { label: string; value: string; sub?: string; tone?: Tone }

/** min / median / mean / max / sigma + HFR / HFR-arcsec / stars.
 *
 *  The max tile's warn tone is `full_well`-derived, not a hardcoded 65535: a
 *  camera that does not publish its well depth gets no verdict rather than a
 *  guessed one. */
export function statTiles(p: PreviewInfo, hfrGood: number, hfrWarn: number): StatTile[] {
  const s = (p.stats ?? {}) as Partial<PreviewInfo["stats"]>;
  const clipped = isClipped(p);
  const hfr = p.hfr;
  const hfrTone: Tone | undefined = hfr == null ? undefined
    : hfr <= hfrGood ? "good" : hfr <= hfrWarn ? "warn" : "bad";
  const tiles: StatTile[] = [
    { label: "MIN", value: statText(s.min) },
    { label: "MEDIAN", value: statText(s.median) },
    { label: "MEAN", value: statText(s.mean) },
    {
      label: "MAX", value: statText(s.max),
      tone: clipped ? "warn" : undefined,
      sub: clipped ? `full well ${p.full_well} ADU` : undefined,
    },
    { label: "SIGMA", value: statText(s.std) },
  ];
  if (hfr != null || p.stars != null) {
    tiles.push({ label: "HFR", value: `${statText(hfr, 2)} px`, tone: hfrTone, sub: "lower is sharper" });
    if (hfr != null && p.pixel_scale_arcsec != null) {
      tiles.push({ label: "HFR SKY", value: `${statText(hfr * p.pixel_scale_arcsec, 2)}"`, tone: hfrTone });
    }
    tiles.push({ label: "STARS", value: statText(p.stars) });
  }
  return tiles;
}

// ------------------------------------------------------------------ filmstrip

export function ageStr(tsSec: number, nowMs: number): string {
  const s = Math.max(0, Math.round(nowMs / 1000 - tsSec));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  return `${Math.round(s / 3600)}h`;
}

/** Glyph AND number, never colour alone. */
export function hfrGlyph(hfr: number | undefined, good: number, warn: number): { ch: string; tone: Tone } {
  if (hfr == null) return { ch: "·", tone: "dim" };
  if (hfr <= good) return { ch: "●", tone: "good" };
  if (hfr <= warn) return { ch: "▲", tone: "warn" };
  return { ch: "■", tone: "bad" };
}

/** WHICH TILE THE STAGE IS ACTUALLY PAINTING, not which id was last pinned.
 *
 *  A pin can be STRANDED: the ring holds a fixed number of frames, so a frame
 *  pinned from this strip is trimmed out after that many more arrive. The pin is
 *  not cleared, so the stage falls back to the newest entry while the strip was
 *  still handed the dead id - every tile then read unselected over a stage
 *  plainly showing one of them, and auto-follow died with it. Mirror the stage's
 *  own precedence instead of trusting the id blindly. */
export function stageFrameId(
  previews: PreviewInfo[], shownId: number | null, liveId: number | null,
): number | null {
  const held = (id: number | null): boolean => id != null && previews.some((p) => p.id === id);
  const newestId = previews.length ? previews[previews.length - 1].id : null;
  return held(shownId) ? shownId : held(liveId) ? liveId : newestId;
}

// ----------------------------------------------------------------- live stack

export interface LiveStackLineModel {
  headline: string;
  extras: string[];
  badge: { label: string; detail: string; tone: Tone } | null;
}

export function liveStackLine(ls: LiveStackInfo): LiveStackLineModel {
  const f = formatLiveStack(ls);
  const align = alignmentState(ls);
  const extras: string[] = [];
  if (f.rejected) extras.push(f.rejected);
  if ((ls.clipped ?? 0) > 0) extras.push(`${ls.clipped!.toLocaleString()} px clipped`);
  return {
    headline: f.headline,
    extras,
    // A clean pattern match stays silent: a badge that always reads "matched" is
    // chrome, and the frame count beside it already proves the stack is running.
    badge: align
      ? { label: align.label.toUpperCase(), detail: hyphens(align.detail), tone: align.tone as Tone }
      : null,
  };
}

// --------------------------------------------------------------- focus verdict

export interface FocusLineModel {
  word: string;
  tone: Tone;
  /** The numbers, or the repair. Mono. */
  detail: string;
  /** "sharper" / "softer" against the previous frame, or null. */
  trend: { word: string; tone: Tone } | null;
  /** The one-line action a warning always carries. */
  action: string | null;
}

/** The live frame's focus judgement, in the legacy's documented precedence:
 *  grossly defocused outranks everything (the star count is ring fragments and
 *  the HFR is the measurement box's ceiling), then saturation, which outranks
 *  few-stars BECAUSE IT CAUSES IT - a frame railed at full scale has no stars
 *  for the same reason it has no dynamic range, and the legacy order once told
 *  such a frame to LENGTHEN its exposure. */
export function focusLine(
  preview: PreviewInfo | null, prev: PreviewInfo | null, hfrGood: number, hfrWarn: number,
): FocusLineModel {
  if (!preview) {
    return { word: "AWAITING FIRST FRAME", tone: "dim", detail: "nothing has been exposed yet", trend: null, action: null };
  }
  const state = focusState(preview as { hfr?: number | null; stars?: number | null; defocus_r80?: number | null });
  const fw = preview.full_well;
  // A WARNING THAT IS ALWAYS ON CARRIES NO INFORMATION. Every deep-sky sub rails
  // a bright star core, so a `stats.max >= fw` test was lit permanently; the
  // floor is a fraction of the frame. `stats.clipped` is absent on a camera that
  // does not report its well depth, and there we stay quiet rather than guess.
  const clipped = fw != null && preview.data_is_linear
    && preview.stats.clipped != null
    && preview.stats.clipped >= clippedFloor(preview.data_width, preview.data_height);

  if (state.kind === "defocused" || state.kind === "soft") {
    const far = state.kind === "defocused";
    return {
      word: far ? "FAR OUT OF FOCUS" : "OUT OF FOCUS",
      tone: far ? "bad" : "warn",
      detail: hyphens(defocusMessage(state.r80)),
      trend: null, action: null,
    };
  }

  const advice = adviceFor({ fewStars: state.kind === "few-stars", clipped });
  if (advice) {
    return {
      word: adviceLabel(advice.kind).toUpperCase(),
      tone: advice.kind === "clipped" ? "warn" : "warn",
      detail: hyphens(advice.text),
      trend: null, action: null,
    };
  }

  const hfr = state.kind === "measured" ? state.hfr : preview.hfr!;
  const word = hfr <= hfrGood ? "GOOD" : hfr <= hfrWarn ? "FAIR" : "POOR";
  const tone: Tone = hfr <= hfrGood ? "good" : hfr <= hfrWarn ? "warn" : "bad";
  const arcsec = preview.pixel_scale_arcsec != null
    ? ` (${(hfr * preview.pixel_scale_arcsec).toFixed(1)}")` : "";
  const prevHfr = prev?.hfr ?? null;
  let trend: FocusLineModel["trend"] = null;
  if (prevHfr != null) {
    if (hfr < prevHfr - 0.05) trend = { word: "sharper than the last frame", tone: "good" };
    else if (hfr > prevHfr + 0.05) trend = { word: "softer than the last frame", tone: "warn" };
  }
  return {
    word, tone,
    detail: `median HFR ${hfr.toFixed(2)} px${arcsec} - lower is sharper`,
    trend,
    action: clipped ? "Stars are saturated - shorten the exposure or lower the gain." : null,
  };
}

// ---------------------------------------------------------- autofocus verdict

export interface AutofocusLineModel { word: string; tone: Tone; detail: string }

const AF_TONE: Record<AfLevel, Tone> = {
  excellent: "accent", good: "good", soft: "warn", failed: "bad", pending: "dim",
};

/** The completed SWEEP's verdict, from the engine's best HFR, the fit R-squared
 *  and the state - distinct from `focusLine`, which judges the live frame.
 *  Verdict word first, raw numbers second. */
export function autofocusLine(inp: {
  state?: string; hfr?: number | null; r2?: number | null; method?: string | null;
  pixelScaleArcsec?: number | null; hfrGood: number; hfrWarn: number; message?: string | null;
}): AutofocusLineModel {
  const level = autofocusLevel({
    state: inp.state, hfr: inp.hfr, r2: inp.r2, hfrGood: inp.hfrGood, hfrWarn: inp.hfrWarn,
  });
  const word = level === "pending"
    ? (inp.state === "running" ? "MEASURING" : "NO RESULT YET")
    : level.toUpperCase();
  let detail = "";
  if (level === "failed") {
    detail = hyphens(inp.message || "no V-curve minimum - check that there are stars in the frame");
  } else if (level === "pending") {
    detail = inp.state === "running" ? "sweeping focus" : "awaiting a sweep";
  } else if (inp.hfr != null) {
    const arcsec = inp.pixelScaleArcsec != null ? ` · ${(inp.hfr * inp.pixelScaleArcsec).toFixed(2)}"` : "";
    const r2s = inp.r2 != null ? ` · R2 ${inp.r2.toFixed(3)}` : "";
    const m = inp.method ? ` · ${inp.method}` : "";
    detail = `HFR ${inp.hfr.toFixed(2)} px${arcsec}${r2s}${m}`;
  }
  return { word, tone: AF_TONE[level], detail };
}
