// VisibilityPanel — tonight's altitude / transit / astro-dark / moon / best
// window for the framing center (design spec §6). The server
// (catalog/visibility.py) computes everything; this panel only draws it.
//
// Layers are differentiated by stroke / dash / hatch / glyph, NOT hue, so they
// survive the all-red night palette (spec §8 / critique C3-A6):
//   • target alt : solid 2px path
//   • moon alt   : dotted 1px path + ☾ glyph at its peak
//   • astro-dark : the only filled band — low opacity + diagonal hatch
//   • best window: [ ] bracket end-caps + label (advisory only; not a fill)
//   • alt-limit  : dashed horizontal + an HTML "30°" label
//   • NOW        : solid vertical + an HTML "NOW" label
// All text is real HTML (≥12px), never inside the scaled SVG viewBox.

import { memo, useEffect, useMemo, useRef, useState } from "react";
import type { VisibilityNight } from "../../types";
import { Panel, Stat } from "../ui";
import { Icon } from "../icons";
import { api, ApiError } from "../../api";
import {
  VIS_W,
  VIS_H,
  PLOT,
  buildGeometry,
  fmtTime,
  fmtWindow,
  fmtAlt,
  fmtHoursAboveLimit,
  moonSepGlyph,
  moonSepTone,
  darknessLabel,
  fmtMoonPhase,
} from "../../lib/visibility";

export interface VisibilityPanelProps {
  ra_hours: number;
  dec_deg: number;
  /** horizon limit (default 30); ties to the future per-site horizon setting. */
  altLimit?: number;
  /** lets the parent (AtlasView) lift the night so the Mosaic reality-check
   *  (Owner C) can cross-reference best_window / set time. */
  onNight?: (night: VisibilityNight | null) => void;
}

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; night: VisibilityNight };

export const VisibilityPanel = memo(function VisibilityPanel({
  ra_hours,
  dec_deg,
  altLimit = 30,
  onNight,
}: VisibilityPanelProps) {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [reloadKey, setReloadKey] = useState(0);
  // a stable "now" for the NOW line; refreshed each load (no per-second tick —
  // the curve is a tonight-scale plot where a 1px/min NOW drift is invisible).
  const nowRef = useRef(Date.now() / 1000);

  // Rounded fetch key (wave-1 §2): sub-arcminute drift must not refire the
  // server-side astropy ephemeris. 0.001 h ≈ 54″ RA; 0.01° = 36″ dec — both far
  // below anything visible on a tonight-scale chart.
  const keyRa = Math.round(ra_hours * 1000) / 1000;
  const keyDec = Math.round(dec_deg * 100) / 100;

  useEffect(() => {
    let alive = true;
    // 300 ms debounce (matches the survey debounce): a drag fires ONE request
    // per settle, not one per pointer-move tick. The previous chart stays up
    // while refetching — the loading skeleton only shows before the first data.
    const timer = window.setTimeout(() => {
      setState((prev) => (prev.kind === "ok" ? prev : { kind: "loading" }));
      nowRef.current = Date.now() / 1000;
      const url =
        `/api/visibility?ra=${encodeURIComponent(keyRa)}` +
        `&dec=${encodeURIComponent(keyDec)}` +
        `&alt_limit=${encodeURIComponent(altLimit)}`;
      api
        .get<VisibilityNight>(url)
        .then((night) => {
          if (!alive) return;
          setState({ kind: "ok", night });
          onNight?.(night);
        })
        .catch((e) => {
          if (!alive) return;
          const message =
            e instanceof ApiError ? e.message : "couldn't compute visibility";
          setState({ kind: "error", message });
          onNight?.(null);
        });
    }, 300);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
    // onNight intentionally omitted — it's a stable lift callback; re-fetch only
    // on the (rounded) target/limit or an explicit retry.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [keyRa, keyDec, altLimit, reloadKey]);

  return (
    <Panel title="Tonight">
      {state.kind === "loading" && <VisLoading />}
      {state.kind === "error" && (
        <VisError
          message={state.message}
          onRetry={() => setReloadKey((k) => k + 1)}
        />
      )}
      {state.kind === "ok" && (
        <VisChart night={state.night} nowUnix={nowRef.current} />
      )}
    </Panel>
  );
});

// ----------------------------------------------------------------- chart body

function VisChart({
  night,
  nowUnix,
}: {
  night: VisibilityNight;
  nowUnix: number;
}) {
  const geo = useMemo(() => buildGeometry(night, nowUnix), [night, nowUnix]);
  const belowLimit = night.never_rises_above_limit;
  const sep = night.moon.separation_deg;

  // alt-limit label y in HTML space is proportional within the SVG; we position
  // labels with % so they track the responsive SVG scale.
  const altLimitPct = (geo.altLimitY / VIS_H) * 100;
  const pctX = (x: number) => (x / VIS_W) * 100;

  return (
    <div className="flex flex-col gap-3">
      {/* curve + HTML label overlay */}
      <div className="relative w-full" style={{ aspectRatio: `${VIS_W} / ${VIS_H}` }}>
        <svg
          viewBox={`0 0 ${VIS_W} ${VIS_H}`}
          className="absolute inset-0 w-full h-full"
          role="img"
          aria-label={
            belowLimit
              ? `Target does not rise above ${night.alt_limit_deg}° tonight`
              : `Altitude curve; transits ${fmtTime(night.transit_unix)} at ${fmtAlt(night.transit_alt)}`
          }
        >
          <defs>
            <pattern
              id="vis-hatch"
              width="6"
              height="6"
              patternUnits="userSpaceOnUse"
              patternTransform="rotate(45)"
            >
              <line
                x1="0"
                y1="0"
                x2="0"
                y2="6"
                stroke="var(--accent)"
                strokeWidth="1"
                opacity="0.35"
              />
            </pattern>
          </defs>

          {/* plot frame baseline (horizon, alt 0) */}
          <line
            x1={PLOT.x}
            y1={PLOT.y + PLOT.h}
            x2={PLOT.x + PLOT.w}
            y2={PLOT.y + PLOT.h}
            stroke="var(--text-faint)"
            strokeWidth="1"
          />

          {/* astro-dark band — the ONLY fill; low-opacity hatch (non-hue layer) */}
          {geo.darkBand && geo.darkBand.w > 0 && (
            <rect
              x={geo.darkBand.x}
              y={PLOT.y}
              width={geo.darkBand.w}
              height={PLOT.h}
              fill="url(#vis-hatch)"
              stroke="var(--accent-dim)"
              strokeWidth="0.75"
              strokeDasharray="2 2"
            />
          )}

          {/* alt-limit dashed horizontal */}
          <line
            x1={PLOT.x}
            y1={geo.altLimitY}
            x2={PLOT.x + PLOT.w}
            y2={geo.altLimitY}
            stroke="var(--warn)"
            strokeWidth="1"
            strokeDasharray="4 3"
          />

          {/* moon altitude — dotted 1px */}
          {geo.moonPath && (
            <path
              d={geo.moonPath}
              fill="none"
              stroke="var(--text-dim)"
              strokeWidth="1"
              strokeDasharray="1 2.5"
              className="svg-halo"
            />
          )}

          {/* target altitude — solid 2px, the hero layer */}
          {geo.altPath && (
            <path
              d={geo.altPath}
              fill="none"
              stroke="var(--accent)"
              strokeWidth="2"
              strokeLinejoin="round"
              className="svg-halo"
            />
          )}

          {/* best-window brackets [ ] (advisory; not a fill) */}
          {geo.bestWindow && geo.bestWindow.w > 2 && (
            <g stroke="var(--good)" strokeWidth="1.5" fill="none">
              <path
                d={`M${(geo.bestWindow.x + 4).toFixed(1)} ${PLOT.y} H${geo.bestWindow.x.toFixed(1)} V${(PLOT.y + PLOT.h).toFixed(1)} H${(geo.bestWindow.x + 4).toFixed(1)}`}
              />
              <path
                d={`M${(geo.bestWindow.x + geo.bestWindow.w - 4).toFixed(1)} ${PLOT.y} H${(geo.bestWindow.x + geo.bestWindow.w).toFixed(1)} V${(PLOT.y + PLOT.h).toFixed(1)} H${(geo.bestWindow.x + geo.bestWindow.w - 4).toFixed(1)}`}
              />
            </g>
          )}

          {/* transit tick */}
          {geo.transitX != null && !night.transit_in_daylight && (
            <line
              x1={geo.transitX}
              y1={PLOT.y}
              x2={geo.transitX}
              y2={PLOT.y + PLOT.h}
              stroke="var(--text-faint)"
              strokeWidth="0.75"
              strokeDasharray="2 3"
            />
          )}

          {/* NOW line */}
          {geo.nowX != null && (
            <line
              x1={geo.nowX}
              y1={PLOT.y}
              x2={geo.nowX}
              y2={PLOT.y + PLOT.h}
              stroke="var(--accent)"
              strokeWidth="1"
              className="svg-halo"
            />
          )}
        </svg>

        {/* HTML label layer (≥12px real px, never inside the scaled SVG) */}
        <div className="absolute inset-0 pointer-events-none text-[11px] mono">
          {/* alt-limit label, pinned at the dashed line, right gutter */}
          <span
            className="absolute right-0 -translate-y-1/2 px-1 text-warn"
            style={{ top: `${altLimitPct}%` }}
          >
            {Math.round(night.alt_limit_deg)}°
          </span>
          {/* ☾ at the moon peak */}
          {geo.moonPeakX != null && (
            <span
              className="absolute top-1 -translate-x-1/2 text-dim"
              style={{ left: `${pctX(geo.moonPeakX)}%` }}
              aria-hidden
            >
              ☾
            </span>
          )}
          {/* NOW label */}
          {geo.nowX != null && (
            <span
              className="absolute bottom-0 -translate-x-1/2 px-1 text-accent"
              style={{ left: `${pctX(geo.nowX)}%` }}
            >
              NOW
            </span>
          )}
        </div>
      </div>

      {/* below-limit advisory (does NOT block framing; Send gates separately) */}
      {belowLimit && (
        <div className="flex items-start gap-1.5 text-xs text-warn">
          <Icon name="alert" size={14} className="shrink-0 mt-0.5" />
          <span>
            Does not rise above {Math.round(night.alt_limit_deg)}° tonight (peaks{" "}
            {fmtAlt(night.transit_alt)}). You can still send it, but it will sit
            low.
          </span>
        </div>
      )}

      {/* readouts — every one a glyph+text Stat (status by shape, not hue) */}
      <div className="grid grid-cols-2 gap-x-4 gap-y-2">
        <Stat
          label="Transit"
          glyph={<span aria-hidden>↑</span>}
          value={
            night.transit_in_daylight
              ? `dark peak ${fmtTime(night.transit_unix)}`
              : fmtTime(night.transit_unix)
          }
          unit={fmtAlt(night.transit_alt)}
          hint={
            night.transit_in_daylight
              ? "The geometric transit is in daylight; the value shown is the highest the target reaches while it's dark."
              : undefined
          }
        />
        <Stat
          label={darknessLabel(night.darkness_kind)}
          glyph={<Icon name="moon" size={12} />}
          value={
            night.dark_start_unix != null && night.dark_end_unix != null
              ? `${fmtTime(night.dark_start_unix)}–${fmtTime(night.dark_end_unix)}`
              : "—"
          }
          tone={night.darkness_kind === "none" ? "warn" : undefined}
          hint={
            night.darkness_kind === "none"
              ? "No astronomical or nautical darkness tonight (high latitude / summer); showing the darkest interval."
              : undefined
          }
        />
        <Stat
          label="Best window"
          glyph={<span aria-hidden>[</span>}
          value={fmtWindow(night.best_window)}
          unit={night.best_window ? `~${fmtAlt(night.best_window.mean_alt)}` : undefined}
          hint="Advisory — runs start immediately; time-gated scheduling comes with Autorun."
        />
        <Stat
          label="Above limit"
          glyph={<span aria-hidden>↑</span>}
          value={fmtHoursAboveLimit(night)}
          hint={`Hours the target spends above ${Math.round(night.alt_limit_deg)}° during the dark window.`}
        />
        <Stat
          label="Moon"
          glyph={<Icon name="moon" size={12} />}
          value={fmtMoonPhase(night.moon.illumination, night.moon.phase_name)}
          hint={
            night.moon.set_unix != null
              ? `Moon sets ${fmtTime(night.moon.set_unix)} — dark sky after.`
              : night.moon.rise_unix != null
                ? `Moon rises ${fmtTime(night.moon.rise_unix)}.`
                : "Moon stays below the horizon all night."
          }
        />
        <Stat
          label="Moon sep"
          glyph={<span aria-hidden>{moonSepGlyph(sep)}</span>}
          value={`${Math.round(sep)}°`}
          tone={moonSepTone(sep)}
          hint={
            sep < 15
              ? "Very close to the moon — heavy gradient/glow."
              : sep < 30
                ? "Near the moon — expect some gradient."
                : "Comfortable separation from the moon."
          }
        />
      </div>

      <p className="text-[11px] text-dim leading-snug">
        Best window is advisory — runs start immediately; scheduling comes with
        Autorun.
      </p>
    </div>
  );
}

// ----------------------------------------------------------------- states

function VisLoading() {
  return (
    <div className="flex flex-col gap-3">
      <div
        className="w-full bg-raise border border-line animate-pulse"
        style={{ aspectRatio: `${VIS_W} / ${VIS_H}` }}
      />
      <p className="text-xs text-dim">Computing tonight…</p>
    </div>
  );
}

function VisError({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="flex flex-col items-start gap-2">
      <div className="flex items-start gap-1.5 text-xs text-warn">
        <Icon name="alert" size={14} className="shrink-0 mt-0.5" />
        <span>Couldn&apos;t compute visibility — {message}</span>
      </div>
      <button type="button" className="btn btn-touch" onClick={onRetry}>
        <Icon name="refresh" size={14} />
        <span className="ml-1">Retry</span>
      </button>
    </div>
  );
}

export default VisibilityPanel;
