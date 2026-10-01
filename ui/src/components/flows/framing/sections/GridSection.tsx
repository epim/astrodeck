// GridSection.tsx - the Target modal's GRID section (#189 S4 item 1; spec
// 2026-09-23 flows mosaic, 2.4 GRID).
//
// COLS and ROWS steppers (1 to 10), OVERLAP from 0 to 50% in steps of 5 with
// quick chips at 15, 25 and 35, the camera-field line, MATCH CAMERA, and the
// optics-drift banner. Every control that cannot act says why, through
// HonestButton: a grid with no camera field to tile with is the Settings
// sentence (framingModel `gridLock`), and MATCH CAMERA with no live optics is
// the same sentence (`matchCameraLock`). A locked control stays in the tab
// order and in the accessibility tree with its reason, which a bare
// `disabled` would strip.
//
// Presentational: every value and every lock arrives as a prop, decided by
// framingModel in the sheet, so this file holds no rule of its own.

import type { JSX } from "react";
import { HonestButton } from "../../../ui";
import type { DriftBanner } from "../framingModel";
import { GRID_MAX } from "../framingModel";

/** The overlap's quick chips (spec 2.4). */
export const OVERLAP_CHIPS = [15, 25, 35] as const;
export const OVERLAP_STEP = 5;
export const OVERLAP_MAX = 50;

const BTN = "tfs-btn";

/** A 44 px row: label, minus, value, plus. Shared by GRID, RUN and
 *  CENTRING so every stepper in the sheet steps the same way. `lock` is the
 *  reason both buttons refuse, or null. */
export function Stepper(p: {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  unit?: string;
  lock: string | null;
  onChange: (next: number) => void;
  explain: (reason: string) => void;
  testId?: string;
}): JSX.Element {
  const step = p.step ?? 1;
  const down = p.lock ?? (p.value <= p.min ? `${p.label.toLowerCase()} is at its least, ${p.min}` : null);
  const up = p.lock ?? (p.value >= p.max ? `${p.label.toLowerCase()} is at its most, ${p.max}` : null);
  const shown = Number.isFinite(p.value) ? String(p.value) : "-";
  return (
    <div className="tfs-row" data-testid={p.testId}>
      <span className="tfs-label">{p.label}</span>
      <span className="tfs-stepper">
        <HonestButton
          className={BTN} reason={down}
          onClick={() => p.onChange(Math.max(p.min, p.value - step))}
          onExplain={p.explain}
        >
          <span aria-hidden>-</span><span className="sr-only">{`fewer ${p.label.toLowerCase()}`}</span>
        </HonestButton>
        <span className="tfs-value" aria-live="polite">{shown}{p.unit ?? ""}</span>
        <HonestButton
          className={BTN} reason={up}
          onClick={() => p.onChange(Math.min(p.max, p.value + step))}
          onExplain={p.explain}
        >
          <span aria-hidden>+</span><span className="sr-only">{`more ${p.label.toLowerCase()}`}</span>
        </HonestButton>
      </span>
    </div>
  );
}

export interface GridSectionProps {
  cols: number;
  rows: number;
  /** Percent, 0..50. */
  overlap: number;
  /** Why the grid controls cannot act (no field to tile with), or null. */
  lock: string | null;
  cameraLine: string;
  /** Why MATCH CAMERA cannot act (no live optics), or null. */
  matchLock: string | null;
  drift: DriftBanner | null;
  onCols: (n: number) => void;
  onRows: (n: number) => void;
  onOverlap: (pct: number) => void;
  onMatchCamera: () => void;
  explain: (reason: string) => void;
}

export function GridSection(p: GridSectionProps): JSX.Element {
  return (
    <section className="tfs-section" aria-labelledby="tfs-grid-h" data-testid="framing-grid">
      <h3 id="tfs-grid-h" className="tfs-section-h">GRID</h3>
      <Stepper label="COLS" value={p.cols} min={1} max={GRID_MAX} lock={p.lock}
        onChange={p.onCols} explain={p.explain} testId="framing-cols" />
      <Stepper label="ROWS" value={p.rows} min={1} max={GRID_MAX} lock={p.lock}
        onChange={p.onRows} explain={p.explain} testId="framing-rows" />
      <Stepper label="OVERLAP" value={p.overlap} min={0} max={OVERLAP_MAX} step={OVERLAP_STEP}
        unit="%" lock={p.lock} onChange={p.onOverlap} explain={p.explain} testId="framing-overlap" />
      <div className="tfs-row tfs-chips" role="group" aria-label="Overlap presets">
        {OVERLAP_CHIPS.map((pct) => (
          <HonestButton
            key={pct}
            className={`${BTN} ${p.overlap === pct ? "tfs-on" : ""}`}
            reason={p.lock}
            onClick={() => p.onOverlap(pct)}
            onExplain={p.explain}
          >
            {`${pct}%`}
          </HonestButton>
        ))}
      </div>
      <div className="tfs-row tfs-note" data-testid="framing-camera-line">{p.cameraLine}</div>
      <div className="tfs-row">
        <HonestButton className={BTN} reason={p.matchLock} onClick={p.onMatchCamera} onExplain={p.explain}>
          MATCH CAMERA
        </HonestButton>
      </div>
      {p.drift && (
        <div
          className={`tfs-row tfs-banner ${p.drift.level === "loss" ? "tfs-loss" : "tfs-warn"}`}
          role="status"
          data-testid="framing-drift"
        >
          {p.drift.text}
        </div>
      )}
    </section>
  );
}

export default GridSection;
