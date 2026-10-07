// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
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
// EXPORT PANELS and IMPORT PANELS (#178) move the grid to and from a file in
// the Telescopius shape. The server owns the file, every number in it and the
// angle convention (server catalog/panel_csv.py), so the only thing these two
// buttons do is move text: the export downloads what the server wrote, the
// import posts the file's text and hands the draft the server answers to the
// sheet, which merges it into the modal's draft (framingModel
// `mergeImportedPanels`) like any other edit. The convention sentence and the
// import's warnings are the server's too, shown once, in one block, from the
// last press.
//
// Presentational: every value and every lock arrives as a prop, decided by
// framingModel in the sheet, so this file holds no rule of its own. The one
// thing it does itself is the two network calls of the panel file, on a
// mutable object (`panelCsvApi`) so a test replaces the network.

import { useRef, useState } from "react";
import type { JSX } from "react";
import { api } from "../../../../api";
import { parseApiError } from "../../../../lib/apiError";
import { u } from "../../../../lib/base";
import { HonestButton } from "../../../ui";
import type { DriftBanner, Params, PanelCsvRequest } from "../framingModel";
import { GRID_MAX, PANEL_CSV_FILENAME, readPanelCsvImport } from "../framingModel";

/** The overlap's quick chips (spec 2.4). */
export const OVERLAP_CHIPS = [15, 25, 35] as const;
export const OVERLAP_STEP = 5;
export const OVERLAP_MAX = 50;

const BTN = "tfs-btn";

export const FRAMING_MOSAIC_CSV = "/api/framing/mosaic/csv";
export const FRAMING_MOSAIC_IMPORT = "/api/framing/mosaic/import";

/** The panel file's two calls, and the save of its text. Mutable so a test
 *  replaces the network and the download, as `framingApi` is replaced. */
export const panelCsvApi = {
  /** The file the server writes for `req`, and the convention sentence it
   *  sends beside it (header `X-Panel-Csv-Convention`). A refusal throws
   *  with the server's own reason. */
  exportCsv: async (req: PanelCsvRequest): Promise<{ text: string; convention: string }> => {
    const res = await fetch(u(FRAMING_MOSAIC_CSV), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    });
    if (!res.ok) {
      let body: unknown;
      try { body = await res.json(); } catch { /* no JSON body: statusText says it */ }
      throw new Error(parseApiError(res.status, body, res.statusText).message);
    }
    return { text: await res.text(), convention: res.headers.get("X-Panel-Csv-Convention") ?? "" };
  },
  /** The server's reading of a file's text: the draft, the warnings and the
   *  convention, unread (`readPanelCsvImport` checks it). */
  importCsv: (text: string): Promise<unknown> => api.post(FRAMING_MOSAIC_IMPORT, { text }),
  /** Hand `text` to the browser as a download named `filename`. */
  save: (filename: string, text: string): void => {
    const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },
};

/** A file's text: `File.text()`, or a FileReader where a WebView lacks it. */
function readFileText(file: File): Promise<string> {
  if (typeof file.text === "function") return file.text();
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result ?? ""));
    r.onerror = () => reject(r.error ?? new Error("the file could not be read"));
    r.readAsText(file);
  });
}

const messageOf = (e: unknown): string => (e instanceof Error ? e.message : String(e));

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

/** What the panel file's two buttons need from the sheet. */
export interface PanelCsvProps {
  /** `panelCsvRequest(draft)`: what EXPORT PANELS posts, null while locked. */
  request: PanelCsvRequest | null;
  /** Why EXPORT PANELS cannot act (`panelCsvExportLock`), or null. IMPORT
   *  PANELS needs no field and no angle: the file brings its own. */
  exportLock: string | null;
  /** The draft the server read from the file: the sheet merges it into the
   *  modal's draft, and DONE asks the usual re-frame question. */
  onImported: (draft: Params) => void;
}

/** The last press's block: the server's convention sentence and, for an
 *  import, its warnings. */
interface PanelCsvNote {
  convention: string;
  warnings: string[];
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
  /** The panel file's buttons; absent, the section has none. */
  panelCsv?: PanelCsvProps;
  explain: (reason: string) => void;
}

export function GridSection(p: GridSectionProps): JSX.Element {
  const csv = p.panelCsv;
  const [note, setNote] = useState<PanelCsvNote | null>(null);
  const fileInput = useRef<HTMLInputElement | null>(null);

  const exportPanels = (): void => {
    if (csv === undefined || csv.request === null) return;
    setNote(null);
    panelCsvApi.exportCsv(csv.request).then(({ text, convention }) => {
      panelCsvApi.save(PANEL_CSV_FILENAME, text);
      setNote({ convention, warnings: [] });
    }).catch((e: unknown) => p.explain(`could not export the panels: ${messageOf(e)}`));
  };

  const importPanels = (file: File): void => {
    if (csv === undefined) return;
    setNote(null);
    readFileText(file).then((text) => panelCsvApi.importCsv(text)).then((answer) => {
      const read = readPanelCsvImport(answer);
      if (!read.ok) { p.explain(`could not import the panels: ${read.why}`); return; }
      csv.onImported(read.value.draft);
      setNote({ convention: read.value.convention, warnings: read.value.warnings });
    }).catch((e: unknown) => p.explain(`could not import the panels: ${messageOf(e)}`));
  };

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
      {csv !== undefined && (
        <div className="tfs-row tfs-chips" role="group" aria-label="Panel file">
          <HonestButton className={BTN} reason={csv.exportLock} onClick={exportPanels} onExplain={p.explain}>
            EXPORT PANELS
          </HonestButton>
          <HonestButton className={BTN} reason={null} onExplain={p.explain}
            onClick={() => fileInput.current?.click()}>
            IMPORT PANELS
          </HonestButton>
          <input
            ref={fileInput} type="file" accept=".csv,text/csv,text/plain" hidden tabIndex={-1}
            aria-label="Panel file to import" data-testid="framing-panel-csv-file"
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (file) importPanels(file);
            }}
          />
        </div>
      )}
      {note && (
        <div role="status" data-testid="framing-panel-csv-note">
          <div className="tfs-row tfs-note" data-testid="framing-panel-csv-convention">{note.convention}</div>
          {note.warnings.map((w, i) => (
            <div key={i} className="tfs-row tfs-banner tfs-warn" data-testid="framing-panel-csv-warning">{w}</div>
          ))}
        </div>
      )}
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
