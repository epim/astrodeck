// MosaicNightCard.tsx - what tonight looks like across the WHOLE mosaic, and
// the stated reason for every panel that has none (review #33).
//
// The night arc on the quick sheet answers for exactly ONE point: the framing
// centre. A 3x2 mosaic on a short refractor covers tens of degrees, and its
// bottom row can spend the entire night under the horizon limit while the centre
// transits comfortably. The legacy Atlas said so (`components/atlas/MosaicNight`)
// and the new hub said nothing at all - so a mosaic that wastes two of its six
// panels looked exactly like one that does not.
//
// THE ARITHMETIC IS THE ATLAS'S, NOT A SECOND COPY. `mosaicNightSummary.ts` is
// pure, already tested, and already knows the two things worth knowing: the peak
// SPREAD across the panels, and WHICH panels have no answer plus the server's own
// reason for each. Re-deriving any of that here would be a second description of
// one night. Only the rendering is new, because the legacy component is built
// out of Tailwind classes and the legacy `LockedChip`/`LockedNote` primitives.
//
// IT ASKS FOR `transit_alt: true` AND NEVER ECHOES `night.date`: `compute_night`
// reports the UTC date of the night's solar-midnight anchor while the mosaic
// route reads a date as the civil date of the EVENING, so at every longitude
// <= 0 that echo returns TOMORROW night's altitudes under tonight's heading.

import { useEffect, useMemo, useState, type JSX } from "react";
import { Card, Mono } from "../../../ui";
import { api, ApiError } from "../../../../api";
import type { MosaicResult } from "../../../../types";
import {
  belowLimitText, missingAltitudeLabel, missingAltitudeReason, peakSpreadText,
  summarisePanelNight, type PanelNight,
} from "../../../../components/atlas/mosaicNightSummary";

/** One request per settle while a picker or the rotate dial is being worked -
 *  up to 100 panels of astropy ride on it. The Atlas's own 400 ms. */
export const MOSAIC_NIGHT_DEBOUNCE_MS = 400;

/**
 * The summary module writes EM DASHES; ARCHITECTURE non-negotiable 5 is
 * "hyphens, never em-dashes, in UI strings".
 *
 * Done here rather than in `mosaicNightSummary.ts` because that file is the
 * legacy Atlas's and is rendered by the legacy Atlas too, where the shipped copy
 * is verbatim-pinned. The EN dash in a numeric range ("58°–66°") is left alone:
 * it is a range, not a clause break, and a hyphen there reads as a minus sign.
 */
export function plainDashes(s: string): string {
  return s.replace(/—/g, "-");
}

export interface MosaicNightCardProps {
  raHours: number;
  decDeg: number;
  rows: number;
  cols: number;
  overlap: number;
  rotationDeg: number;
  fovXDeg: number;
  fovYDeg: number;
  /** The site's own horizon limit - the SAME number the night arc's floor line
   *  and the ranking's `alt_limit` use. A second constant here would let this
   *  card call a panel fine while the arc drew it under the line. */
  altLimitDeg: number;
}

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; panels: PanelNight[] };

export function MosaicNightCard(p: MosaicNightCardProps): JSX.Element | null {
  const panelCount = p.rows * p.cols;
  // A 1x1 "mosaic" IS the framing centre, which the quick sheet's arc already
  // answers for at a finer time step; printing it twice would put two slightly
  // different numbers for one point on screen. With no optics the panel
  // POSITIONS are unknown, so their altitudes would be a guess.
  const enabled = panelCount > 1 && p.fovXDeg > 0 && p.fovYDeg > 0;

  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    // The previous answer is dropped the INSTANT the grid changes, before the
    // debounce: those numbers belong to a different set of pointings the moment
    // a picker moves, and a 2x2's spread sitting under a heading that already
    // reads 3x2 is the exact defect this card exists to close.
    setState({ kind: "loading" });
    const timer = setTimeout(() => {
      api.post<MosaicResult>("/api/framing/mosaic", {
        ra_hours: p.raHours,
        dec_deg: p.decDeg,
        rows: p.rows,
        cols: p.cols,
        overlap: p.overlap,
        rotation_deg: p.rotationDeg,
        fov_x_deg: p.fovXDeg,
        fov_y_deg: p.fovYDeg,
        transit_alt: true,
      }).then((res) => {
        if (!alive) return;
        setState({ kind: "ok", panels: res.panels });
      }).catch((e) => {
        if (!alive) return;
        setState({
          kind: "error",
          message: e instanceof ApiError ? e.message : "the request did not complete",
        });
      });
    }, MOSAIC_NIGHT_DEBOUNCE_MS);
    return () => { alive = false; clearTimeout(timer); };
  }, [
    enabled, p.raHours, p.decDeg, p.rows, p.cols, p.overlap, p.rotationDeg,
    p.fovXDeg, p.fovYDeg,
  ]);

  const summary = useMemo(
    () => (state.kind === "ok" ? summarisePanelNight(state.panels, p.altLimitDeg) : null),
    [state, p.altLimitDeg],
  );
  const missing = summary ? missingAltitudeReason(summary) : null;
  const below = summary ? belowLimitText(summary, p.altLimitDeg) : null;
  const spread = summary ? peakSpreadText(summary) : null;

  if (!enabled) return null;

  return (
    <Card tone="default" data-testid="sky-mosaic-night">
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <div
          style={{
            fontFamily: "'Chakra Petch', system-ui, sans-serif", fontWeight: 600,
            fontSize: 10, letterSpacing: ".2em", color: "var(--text-faint)",
          }}
        >
          {`ACROSS THE ${p.rows}×${p.cols} MOSAIC`}
        </div>

        {state.kind === "loading" && (
          <Mono size={11} tone="dim">Working out each panel&apos;s peak altitude…</Mono>
        )}

        {state.kind === "error" && (
          // Not "no data": the panels have no altitude BECAUSE this call failed,
          // and the user is entitled to the difference between that and a mosaic
          // that genuinely never rises.
          <p data-testid="sky-mosaic-night-error" style={{ margin: 0, fontSize: 11.5, lineHeight: 1.5, color: "var(--warn, #ffb454)" }}>
            {`No panel altitudes - ${state.message}. The night arc on IMAGE THIS is unaffected; it is the centre only.`}
          </p>
        )}

        {summary && (
          <>
            {spread && (
              <p data-testid="sky-mosaic-spread" style={{ margin: 0, fontSize: 11.5, lineHeight: 1.5, color: "var(--text)" }}>
                {plainDashes(spread)}
              </p>
            )}
            {below && (
              <p
                data-testid="sky-mosaic-below"
                style={{ margin: 0, fontSize: 11.5, lineHeight: 1.5, color: "var(--warn, #ffb454)" }}
              >
                {plainDashes(below)}
              </p>
            )}
            {missing && (
              // A panel with no altitude must say WHY, not go blank - that is
              // the half the Atlas component was written for. `missingAltitudeReason`
              // already names WHICH panels and the server's own cause, so the
              // short label is only the chip's text and is not repeated here.
              <p
                data-testid="sky-mosaic-missing"
                style={{ margin: 0, fontSize: 11.5, lineHeight: 1.5, color: "var(--text-faint)" }}
                title={missingAltitudeLabel(summary)}
              >
                {plainDashes(missing)}
              </p>
            )}
          </>
        )}
      </div>
    </Card>
  );
}
