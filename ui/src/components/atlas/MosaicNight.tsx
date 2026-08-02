// MosaicNight — what tonight looks like across the WHOLE mosaic, and the stated
// reason for every panel it could not answer for.
//
// Why this exists. The "Tonight" chart above answers for exactly one point: the
// framing centre. A mosaic is up to 100 separate pointings spread over the sky —
// a 10-wide grid on a short refractor covers tens of degrees, and its corner
// panels can spend the entire night under the horizon limit while the centre
// transits comfortably. Sending that plan produced a run that quietly wasted
// hours on frames that were never going to be usable.
//
// The server has answered this per panel for a while (`transit_alt` /
// `transit_alt_error` in catalog/framing.py) and NOTHING on the screen read it.
// The half that was missing is the one that matters: a panel with no altitude
// must say WHY, not go blank. `missing === total` puts the cause in visible text
// (LockedNote — there is nothing else on the row to read); a partial loss puts
// the count in a focusable LockedChip whose reason is reachable by tap, keyboard
// and screen reader. Both are the house locked-family primitives, because "every
// blocked control says why" and an unanswerable readout is the same promise.
//
// It asks for TONIGHT with `transit_alt: true` and deliberately does NOT echo
// `night.date` back: `compute_night` reports the UTC date of the night's
// solar-midnight anchor while the mosaic route reads a date as the civil date of
// the EVENING, so at every longitude <= 0 that echo returns TOMORROW night's
// altitudes under tonight's chart. See MosaicSpecIn.transit_alt in
// catalog/framing.py for the measured round-trip.

import { memo, useEffect, useMemo, useState } from "react";
import { useConfig, useFraming, useStore } from "../../store";
import { useShallow } from "zustand/react/shallow";
import type { MosaicResult } from "../../types";
import { fovFromOptics, type OpticsLike } from "../../lib/framing";
import { api, ApiError } from "../../api";
import { LockedChip, LockedNote } from "../ui";
import { Icon } from "../icons";
import {
  summarisePanelNight,
  peakSpreadText,
  belowLimitText,
  missingAltitudeReason,
  missingAltitudeLabel,
  type PanelNight,
} from "./mosaicNightSummary";

export interface MosaicNightProps {
  /** Framing centre, ALREADY rounded by the caller to the same fetch key the
   *  night chart uses — so the curve and these panels can never end up
   *  describing two different points on the sky. */
  raHours: number;
  decDeg: number;
  /** The horizon limit the chart's dashed line marks (site horizon_min_deg). */
  altLimitDeg: number;
}

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; panels: PanelNight[] };

export const MosaicNight = memo(function MosaicNight({
  raHours,
  decDeg,
  altLimitDeg,
}: MosaicNightProps) {
  const framing = useFraming();
  const config = useConfig();
  const statusOptics = useStore(useShallow((s) => s.status?.optics));

  // The same merge AtlasView performs to size the frame on the canvas: the
  // config override wins when nonzero, else the camera-reported value. It is
  // duplicated rather than shared because the two live in different lanes this
  // run — if they ever drift, the panel positions this asks about stop matching
  // the rectangles the user is looking at, which is the whole reason the merge
  // is written out longhand here instead of reaching for config.optics alone.
  const optics = config?.optics ?? null;
  const liveOptics = statusOptics ?? config?.optics_computed ?? null;
  const mergedOptics: OpticsLike | null = useMemo(() => {
    if (!optics) return null;
    return {
      focal_length_mm: optics.focal_length_mm,
      pixel_size_um: optics.pixel_size_um || liveOptics?.pixel_size_um || 0,
      sensor_width_px: optics.sensor_width_px || liveOptics?.sensor_width_px || 0,
      sensor_height_px: optics.sensor_height_px || liveOptics?.sensor_height_px || 0,
    };
  }, [optics, liveOptics]);
  // No focal-length OVERRIDE here: the Atlas header's focal field is a draft
  // until it is committed, and an uncommitted number is not the rig. These
  // altitudes therefore describe the saved optics, and update when the draft is
  // saved (config changes -> this refetches).
  const fov = useMemo(() => fovFromOptics(mergedOptics), [mergedOptics]);

  const rows = framing?.mosaic.rows ?? 1;
  const cols = framing?.mosaic.cols ?? 1;
  const overlap = framing?.mosaic.overlap ?? 0;
  // Rotation tilts the grid off the dec axis, so it genuinely moves the panels;
  // rounded only enough to stop a rotate-drag firing a request per pointer tick.
  const rotationDeg = Math.round((framing?.rotation_deg ?? 0) * 10) / 10;
  const panelCount = rows * cols;

  // A 1x1 "mosaic" is the framing centre, which the chart above has already
  // answered for at a finer time step. Repeating it here would print two
  // slightly different numbers for one point and invite the user to work out
  // which is true. Optics-less rigs get nothing either: the frame size is
  // unknown, so panel POSITIONS would be a guess, and the page already carries
  // one standing banner saying optics are unset.
  const enabled = panelCount > 1 && fov.fov_x_deg > 0 && fov.fov_y_deg > 0;

  const [state, setState] = useState<State>({ kind: "loading" });
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    // Drop the previous answer the INSTANT the grid changes, before the
    // debounce — not the "keep the last chart up while refetching" idiom the
    // night curve uses. Those numbers belong to a different set of pointings the
    // moment a stepper moves, and a 2×2's range left sitting under a heading
    // that already reads 3×3 is the exact shape of defect this file is closing.
    setState({ kind: "loading" });
    // 400 ms — one request per settle while a stepper or the rotate handle is
    // being worked, not one per tick. Up to 100 panels of astropy ride on it.
    const timer = window.setTimeout(() => {
      api
        .post<MosaicResult>("/api/framing/mosaic", {
          ra_hours: raHours,
          dec_deg: decDeg,
          rows,
          cols,
          overlap,
          rotation_deg: rotationDeg,
          fov_x_deg: fov.fov_x_deg,
          fov_y_deg: fov.fov_y_deg,
          // "tonight", said out loud — never night.date (see the header note).
          transit_alt: true,
        })
        .then((res) => {
          if (!alive) return;
          // `transit_alt_error` is on the wire but not yet on types.ts
          // MosaicPanel; widen here rather than pretend the field is absent.
          setState({ kind: "ok", panels: res.panels as PanelNight[] });
        })
        .catch((e) => {
          if (!alive) return;
          const message =
            e instanceof ApiError ? e.message : "the request did not complete";
          setState({ kind: "error", message });
        });
    }, 400);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [
    enabled, raHours, decDeg, rows, cols, overlap, rotationDeg,
    fov.fov_x_deg, fov.fov_y_deg, reloadKey,
  ]);

  const summary = useMemo(
    () => (state.kind === "ok"
      ? summarisePanelNight(state.panels, altLimitDeg)
      : null),
    [state, altLimitDeg],
  );
  // Null when nothing is missing, never "" — see the render note below.
  const missingReason = summary ? missingAltitudeReason(summary) : null;

  if (!enabled) return null;

  return (
    <div className="flex flex-col gap-1.5 border-t border-line pt-3">
      <span className="label">
        Across the {rows}×{cols} mosaic
      </span>

      {state.kind === "loading" && (
        <p className="text-[12px] text-dim leading-snug">
          Working out each panel&apos;s peak altitude…
        </p>
      )}

      {state.kind === "error" && (
        <div className="flex flex-col items-start gap-2">
          <p className="flex items-start gap-1.5 text-[12px] text-warn leading-snug">
            <Icon name="alert" size={14} className="shrink-0 mt-0.5" />
            {/* Not "no data": the panels have no altitude BECAUSE this call
                failed, and the user is entitled to the difference between that
                and a mosaic that genuinely never rises. */}
            <span>
              No panel altitudes — {state.message}. The chart above is unaffected;
              it is the centre only.
            </span>
          </p>
          <button
            type="button"
            className="btn btn-touch"
            onClick={() => setReloadKey((k) => k + 1)}
          >
            <Icon name="refresh" size={14} />
            <span className="ml-1">Retry</span>
          </button>
        </div>
      )}

      {summary && (
        <>
          {/* The per-panel peak is sampled every 20 min server-side against the
              chart's 10 (visibility.transit_alt_for), so a panel can read a
              couple of tenths under the curve's transit. Rounded to whole
              degrees that is invisible — recorded so a future finer sample is
              not mistaken for a fix to a bug that was never there. */}
          {peakSpreadText(summary) && (
            <p className="text-[12px] text-ink leading-snug">
              {peakSpreadText(summary)}
            </p>
          )}

          {belowLimitText(summary, altLimitDeg) && (
            <p className="flex items-start gap-1.5 text-[12px] text-warn leading-snug">
              <Icon name="alert" size={14} className="shrink-0 mt-0.5" />
              <span>{belowLimitText(summary, altLimitDeg)}</span>
            </p>
          )}

          {/* The half this component exists for. Nothing answered => the reason
              IS the content, so it is visible text; a partial loss => the count
              is visible and the cause rides in the chip, reachable by tap,
              keyboard and screen reader. `missingReason` is derived above and
              null-checked here rather than defaulted to "": a reason that
              rendered empty would put the blank cell straight back. */}
          {missingReason && summary.answered === 0 && (
            <LockedNote reason={missingReason} />
          )}
          {missingReason && summary.answered > 0 && (
            <LockedChip reason={missingReason} className="text-[12px] text-dim">
              <span>{missingAltitudeLabel(summary)}</span>
            </LockedChip>
          )}
        </>
      )}
    </div>
  );
});

export default MosaicNight;
