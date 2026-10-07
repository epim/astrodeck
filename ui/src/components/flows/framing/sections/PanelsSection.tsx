// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// PanelsSection.tsx - the Target modal's PANELS section (#189 S4 item 1, S5
// run mode; spec 2026-09-23 flows mosaic, 2.4 PANELS, 2.6, 5.2, 6.9).
//
// One row per panel of the draft's grid: its run-order number, its `r-c`
// label (1-based, row 1 the north edge, col 1 the west edge at angle 0, the
// Plan's `<name> r-c`), an on/off toggle that edits `skip`, and a progress
// bar of what the ledger banked for it (the progress route, never a count
// kept here). Then the ORDER select.
//
// THE RUN ORDER, AS FAR AS THIS SIDE CAN KNOW IT. `sequence/panel_order.py`
// orders the panels at every pass (spec 5.2). Two of its three keys are known
// here: snake order from `compute_mosaic` (row 1 west to east, row 2 back,
// ...), and each panel's fraction banked, from the progress route. The third,
// least-recently-visited, is the engine's tie-break among equally complete
// panels and needs the ledger's timestamps, which the progress route does not
// carry; ResumeArm, which also has no visit times, orders exactly as this
// does. "Setting first" needs the site, so it is listed in snake order and
// says so. "Grid order" is the snake ROTATED at the run to start after the
// last-visited panel (5.2), which needs the ledger's visit times too, so it
// is listed from 1-1 and says so as well (#412 item 4): after the first visit
// its numbers are not the order the run takes. Skipped panels run in no order
// and are listed after, in grid order, so a panel toggled back on shows where
// it came from. In run mode a panel set aside tonight runs in no order
// tonight either, and is listed unnumbered between the two (#528).
//
// THE ALTITUDE COLUMN AND THE NIGHT CARD ARE SITE-DERIVED (spec 6.9). A
// panel's peak altitude tonight is a function of the site's latitude, so the
// column and `MosaicNightCard` render only for a holder of
// `view.site_derived`, and for anyone else they are ABSENT: no header, no
// empty cells, no card. An empty column tells a viewer there is something
// being withheld, and a "-" in every row reads as "these panels never rise".
//
// IN RUN MODE (spec 2.6) a row also says what the live run is doing to its
// panel, in a line under it: "shooting now"; the panel the run is on while
// it is paused, holding for cloud or stopping, worded by that state and
// never as shot (#451); or the set-aside words and the engine's reason in
// the engine's words. SINCE #573 (#534 follow-up; backlog ruling D-07,
// owner-approved 2026-09-30) the set-aside words are NOT always "set aside
// tonight": a centring set-aside that may still expire (the engine's
// `for_now`, carried here as `PanelRunState`'s `forNow`) reads "set aside
// for now: <reason>; tried once more tonight", because the engine plans to
// retry it the same night; every other one, and a centring one struck out
// a second time, reads "set aside tonight" as before. The reason is the one
// thing the sky cannot draw (its "!" says only THAT a panel is set aside),
// and without it the operator cannot tell a panel that will not centre from
// one the horizon took. A reason that already names its panel ("centring
// failed on 2-2 ...") is not prefixed with the label again (#509). The
// state is framingModel `runPanelsOf`'s, handed in; nothing here reads the
// run, and no time is shown (a meridian wait's end is the site's).
//
// RETRY SET-ASIDE PANELS (#600; backlog ruling D-07, owner-approved
// 2026-09-30). A panel set aside for the night stayed out of it until
// tomorrow even once the operator had fixed the cause, so while any row says
// "set aside" a button under the rows asks the run to take them back
// (`onRetry`, which the sheet points at `POST /api/sequence/retry-set-aside`
// with the live group's id). It is a RUN control, not an edit of the draft,
// and the sheet is read-only exactly while a run is live, so it stands
// OUTSIDE the fieldset that freezes the ORDER select and the SHOOT toggles
// (`frozen`): a button inside a disabled fieldset is natively disabled and
// would never fire in the one mode that shows it. A HonestButton, so a
// viewer reads why it is locked, in a line of its own as well as the tooltip
// (the sheet's "explained" banner is not drawn read-only).
//
// A DORMANT SESSION'S SET-ASIDE PANELS ARE ROWS TOO (#727, backlog wave 16).
// The rows above read set-aside state off the live `state.group`, so once a
// run ended owing a set-aside panel nothing said so and no button could be
// drawn, although the server's dormant retry (POST with `session_id`) works.
// The progress route now lists tonight's standing set-aside panels of a
// dormant session on the mosaic block (`set_aside`, server
// `progress._set_aside_tonight`), and `panelRows` gives each such panel the
// same `set_aside` row state the live run would, so it is worded, left out of
// the order and drawn dotted exactly as a live one is. A live state wins when
// both speak, a panel the draft skips is not set aside (an unsaved SKIP says
// it will not be shot at all), and a block whose grid is not the draft's says
// nothing, as the counts do not (`progressByCell`). No reason is shown: the
// route sends none, only the panel and whether it may still expire tonight.

import type { JSX, ReactNode } from "react";
import { HonestButton } from "../../../ui";
import type { FlowProgressBlock } from "../../../../lib/flowsApi";
import type { MosaicPanel } from "../../../../types";
import type { PanelRunState } from "../../flowRunState";
import { NODE_DEFS } from "../../nodeDefs";

export const ORDERS: readonly string[] =
  NODE_DEFS.target.fields.find((f) => f.key === "order")?.options ?? [];

export const SETTING_FIRST_NOTE =
  "setting first is ordered at the run, from the site: listed here in grid order";
export const GRID_ORDER_NOTE =
  "grid order is turned at the run so it starts after the last-visited panel: listed here from 1-1";
/** A run-mode row's line for the panel the run is shooting: PanelLayer's own
 *  words for the state, so the list and the sky's screen-reader label agree. */
export const SHOOTING_NOW = "shooting now";
/** The line for a panel set aside for the rest of the night, before the
 *  engine's reason. */
export const SET_ASIDE_TONIGHT = "set aside tonight";
/** The line for a panel set aside only FOR NOW (#573, #534 follow-up,
 *  backlog ruling D-07): a centring set-aside that may still expire
 *  tonight, before the engine's reason; `runLine` adds the "tried once
 *  more tonight" tail itself. */
export const SET_ASIDE_FOR_NOW = "set aside for now";
/** The lines for the panel the run is on while no exposure of it is being
 *  made (flowRunState's CURRENT, #451), one per run state. The hold's words
 *  name cloud only when the engine's `hold` does. */
export const CURRENT_PAUSED = "current panel, run paused";
export const CURRENT_HOLDING_FOR_CLOUD = "current panel, holding for cloud";
export const CURRENT_HOLDING = "current panel, run holding";
export const CURRENT_STOPPING = "current panel, run stopping";
/** The retry button's label (#600). */
export const RETRY_SET_ASIDE = "RETRY SET-ASIDE PANELS";

/** A run-mode row's line, or null for a panel the run is doing nothing to.
 *
 *  A set-aside's words follow `run.forNow` (#573, #534 follow-up, backlog
 *  ruling D-07): FOR NOW, with the "tried once more tonight" tail, for a
 *  centring set-aside that may still expire; TONIGHT, as before, for every
 *  other one. */
export function runLine(run: PanelRunState | null | undefined): string | null {
  if (!run) return null;
  if (run.kind === "shooting") return SHOOTING_NOW;
  if (run.kind === "current") {
    if (run.run === "paused") return CURRENT_PAUSED;
    if (run.run === "aborting") return CURRENT_STOPPING;
    return run.hold === "clouds" ? CURRENT_HOLDING_FOR_CLOUD : CURRENT_HOLDING;
  }
  const label = run.forNow ? SET_ASIDE_FOR_NOW : SET_ASIDE_TONIGHT;
  const tail = run.forNow ? "; tried once more tonight" : "";
  return run.reason ? `${label}: ${run.reason}${tail}` : `${label}${tail}`;
}

/** Does `text` name the panel `label` ("2-2") as a whole label: not inside
 *  a longer one, so "12-2" and "2-21" do not name 2-2. */
export function namesPanel(text: string, label: string): boolean {
  if (!label) return false;
  const at = (i: number) => (i >= 0 && i < text.length ? text[i] : "");
  for (let i = text.indexOf(label); i !== -1; i = text.indexOf(label, i + 1)) {
    if (!/[0-9]/.test(at(i - 1)) && !/[0-9]/.test(at(i + label.length))) return true;
  }
  return false;
}

/** A run-mode row's whole line, as PANELS prints it under the panel's row,
 *  or null for a panel the run is doing nothing to: the panel's label, then
 *  `runLine`. A set-aside reason that already names the panel stands alone
 *  after "set aside tonight:", with no label before it (#509): the engine's
 *  centring reason reads "centring failed on 2-2 on 3 consecutive visits
 *  ...", and "2-2: set aside tonight: centring failed on 2-2 ..." said the
 *  panel twice in one line, under a row whose PANEL cell already says it. A
 *  reason that does not name it ("guiding did not start on any panel of
 *  M31") keeps the label, or the line would not say which panel. */
export function runRowText(label: string, run: PanelRunState | null | undefined): string | null {
  const line = runLine(run);
  if (line === null) return null;
  if (run?.kind === "set_aside" && namesPanel(run.reason, label)) return line;
  return `${label}: ${line}`;
}

export interface PanelRow {
  /** 1-based, in the server's convention. */
  row: number;
  col: number;
  label: string;
  /** 1-based run order among the panels that run; null for a skipped one,
   *  and in run mode for one set aside tonight (#528). */
  order: number | null;
  skipped: boolean;
  /** Banked and owed-in-all subs from the progress route; `total` 0 when the
   *  route has no count for this panel (never run, or a grid it does not
   *  describe). */
  banked: number;
  total: number;
  /** Peak altitude tonight, degrees, from the route's `transit_alt`; null
   *  when not asked or not answered (`altError` then says why, when the
   *  server did). */
  peakAlt: number | null;
  altError: string | null;
  /** What the live run is doing to this panel (run mode, spec 2.6), or null:
   *  not in run mode, or nothing to draw. A dormant session's standing
   *  set-aside panel (#727) carries the `set_aside` state here too, from the
   *  progress block, when no live state speaks for it. */
  run?: PanelRunState | null;
}

/** One panel a DORMANT session holds a standing set-aside record for tonight
 *  (#727; server `progress._set_aside_tonight`, whose docstring is the
 *  contract). Words and flags only: no reason (free text), kind, night or
 *  time. `row` and `col` are 0-based, as every progress cell is. */
export interface DormantSetAside {
  target_id: string;
  name: string;
  row: number | null;
  col: number | null;
  /** The engine's `for_now`: a centring set-aside that may still expire
   *  tonight, which a restart takes up again when its time comes. */
  for_now: boolean;
}

/** The progress block as the route answers it once the route hands in the
 *  clock (#727): `FlowProgressBlock` plus `set_aside`, present only on a
 *  mosaic block of a dormant session that holds one. flowsApi.ts does not
 *  declare it yet, so it is read here through this widening, which is
 *  harmless once it does. */
export type ProgressBlockWithSetAside = FlowProgressBlock & {
  set_aside?: readonly DormantSetAside[];
};

/** Snake order (`compute_mosaic`): even rows west to east, odd rows back. */
export function snakeIndex(row0: number, col0: number, cols: number): number {
  return row0 * cols + (row0 % 2 === 0 ? col0 : cols - 1 - col0);
}

const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** Does the block's progress describe THIS grid? A grid change restarts every
 *  count (spec 2.5), so another grid's numbers, and its set-aside cells,
 *  name the wrong panels. A single target's block has no `grid` and one
 *  panel at 0,0. */
function describesGrid(progress: FlowProgressBlock, rows: number, cols: number): boolean {
  const g = progress.grid;
  return g ? g.rows === rows && g.cols === cols : rows * cols === 1;
}

/** The progress route's counts for this grid, keyed "r0,c0" (0-based), or an
 *  empty map when the block's progress describes another grid
 *  (`describesGrid`). */
function progressByCell(
  progress: FlowProgressBlock | null | undefined, rows: number, cols: number,
): Map<string, { banked: number; total: number }> {
  const out = new Map<string, { banked: number; total: number }>();
  if (!progress) return out;
  if (!describesGrid(progress, rows, cols)) return out;
  for (const p of progress.panels ?? []) {
    if (!finite(p.row) || !finite(p.col)) continue;
    out.set(`${p.row},${p.col}`, { banked: finite(p.banked) ? p.banked : 0, total: finite(p.total) ? p.total : 0 });
  }
  for (const s of progress.skipped ?? []) {
    if (!finite(s.row) || !finite(s.col)) continue;
    // A skipped panel owes nothing while skipped, so the route gives it no
    // total; what it still holds is its `banked`.
    out.set(`${s.row},${s.col}`, { banked: finite(s.banked) ? s.banked : 0, total: 0 });
  }
  return out;
}

/** The set-aside state a DORMANT session's standing set-aside panels get,
 *  keyed "r0,c0" (0-based), from the block's `set_aside` (#727): the state a
 *  live run publishes for the same panel, so `runLine` words it, and the
 *  order and the sky treat it, alike. `forNow` is the engine's flag. An empty
 *  reason, because the route sends none and `runLine` then says only
 *  "set aside tonight" or "set aside for now; tried once more tonight". Empty
 *  when the block describes another grid (`describesGrid`). */
function setAsideByCell(
  progress: ProgressBlockWithSetAside | null | undefined, rows: number, cols: number,
): Map<string, PanelRunState> {
  const out = new Map<string, PanelRunState>();
  if (!progress || !describesGrid(progress, rows, cols)) return out;
  // A null cell (an id that matches no cell of the grid) keys "null,null"
  // and so matches no panel; no guard is needed to keep it out.
  for (const s of progress.set_aside ?? []) {
    out.set(`${s.row},${s.col}`, { kind: "set_aside", reason: "", forNow: s.for_now === true });
  }
  return out;
}

/** The PANELS rows for a grid, in run order as described above. */
export function panelRows(a: {
  rows: number;
  cols: number;
  /** 1-based [row, col] pairs (framingModel `parseSkip`). */
  skip: ReadonlyArray<readonly [number, number]>;
  progress: ProgressBlockWithSetAside | null | undefined;
  /** The route's panels for the CURRENT spec, or null. */
  answerPanels: readonly MosaicPanel[] | null;
  order: string;
  /** Run mode: framingModel `runPanelsOf`'s answer, by label. Absent outside
   *  run mode, which leaves every row's `run` null. */
  run?: Readonly<Record<string, PanelRunState>>;
}): PanelRow[] {
  const { rows, cols } = a;
  const counts = progressByCell(a.progress, rows, cols);
  const dormantAside = setAsideByCell(a.progress, rows, cols);
  const skipped = new Set(a.skip.map(([r, c]) => `${r - 1},${c - 1}`));
  const alt = new Map<string, MosaicPanel>();
  for (const p of a.answerPanels ?? []) alt.set(`${p.row},${p.col}`, p);
  const cells: (PanelRow & { snake: number; fraction: number })[] = [];
  for (let r0 = 0; r0 < rows; r0++) {
    for (let c0 = 0; c0 < cols; c0++) {
      const k = `${r0},${c0}`;
      const n = counts.get(k) ?? { banked: 0, total: 0 };
      const ap = alt.get(k);
      cells.push({
        row: r0 + 1, col: c0 + 1, label: `${r0 + 1}-${c0 + 1}`, order: null,
        skipped: skipped.has(k), banked: n.banked, total: n.total,
        peakAlt: ap && finite(ap.transit_alt) ? ap.transit_alt : null,
        altError: ap?.transit_alt_error ?? null,
        // A LIVE STATE WINS; a dormant session's standing set-aside speaks
        // only where no run does, and never for a panel the draft skips
        // (#727: an unsaved SKIP says it will not be shot at all).
        run: a.run?.[`${r0 + 1}-${c0 + 1}`] ?? (skipped.has(k) ? null : dormantAside.get(k) ?? null),
        snake: snakeIndex(r0, c0, cols),
        fraction: n.total > 0 ? n.banked / n.total : 0,
      });
    }
  }
  const leastComplete = a.order === "Least complete first" || !ORDERS.includes(a.order);
  // A PANEL SET ASIDE IS NOT IN TONIGHT'S ORDER (#528), FOR NOW OR FOR THE
  // REST OF THE NIGHT ALIKE (#573): the engine owes it no visit in THIS
  // pass (spec 5.10, `_visits_owed`) and the recovery ladder leaves it out
  // either way, so numbering it would name, as the run's next panel, one it
  // is not about to visit; and least complete first always put it at 1,
  // since it banked nothing (observed on the S7 probe's forced-solve-failure
  // walk: "1 2-2" in PANELS and a "1" badge on the sky's dotted 2-2). In run
  // mode such a panel is listed after the panels that run, unnumbered, and
  // before the skipped ones; its row still says why, worded for now or for
  // the night, and the sky still draws it dotted with "!".
  const isAside = (c: PanelRow) => c.run?.kind === "set_aside";
  const live = cells.filter((c) => !c.skipped && !isAside(c)).sort((x, y) =>
    (leastComplete ? x.fraction - y.fraction : 0) || x.snake - y.snake);
  live.forEach((c, i) => { c.order = i + 1; });
  const aside = cells.filter((c) => !c.skipped && isAside(c))
    .sort((x, y) => x.row - y.row || x.col - y.col);
  const off = cells.filter((c) => c.skipped).sort((x, y) => x.row - y.row || x.col - y.col);
  return [...live, ...aside, ...off].map(({ snake: _s, fraction: _f, ...row }) => row);
}

export interface PanelsSectionProps {
  rows: PanelRow[];
  order: string;
  /** `view.site_derived`: the altitude column and the night card. */
  showAltitude: boolean;
  /** The MosaicNightCard, already built by the sheet for a holder of the site
   *  view, or null. Never rendered for anyone else. */
  nightCard: ReactNode;
  onOrder: (v: string) => void;
  /** 1-based row and col. */
  onToggle: (row: number, col: number) => void;
  /** Freeze the ORDER select and the SHOOT toggles (the sheet's `frozen`:
   *  read-only, a run live, or DONE writing). Their own fieldset, so the
   *  retry button below stays pressable while they are frozen. Absent is
   *  live, as every caller before #600 had it. */
  frozen?: boolean;
  /** RETRY SET-ASIDE PANELS (#600): the button is drawn when this is given
   *  AND some row says it is set aside, and not otherwise. */
  onRetry?: () => void;
  /** Why the button is locked, or null/absent for live: a viewer, or a retry
   *  already on its way. Said in a line of its own beside the tooltip. */
  retryLocked?: string | null;
  /** What the last press did, in words: the panels queued, or the server's
   *  refusal. */
  retryNote?: string | null;
  /** How a locked press explains itself (HonestButton's channel). The reason
   *  is already on screen below the button, so absent is a no-op. */
  explain?: (reason: string) => void;
}

export function PanelsSection(p: PanelsSectionProps): JSX.Element {
  const retryShown = p.onRetry !== undefined && p.rows.some((r) => r.run?.kind === "set_aside");
  return (
    <section className="tfs-section" aria-labelledby="tfs-panels-h" data-testid="framing-panels">
      <h3 id="tfs-panels-h" className="tfs-section-h">PANELS</h3>
      <fieldset className="tfs-fieldset" disabled={p.frozen === true}>
        <div className="tfs-row">
          <label className="tfs-label" htmlFor="tfs-order">ORDER</label>
          <select id="tfs-order" className="field tfs-input" value={p.order}
            onChange={(e) => p.onOrder(e.target.value)}>
            {ORDERS.map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
        </div>
        {p.order === "Setting first" && <div className="tfs-row tfs-note" data-testid="framing-order-note">{SETTING_FIRST_NOTE}</div>}
        {p.order === "Grid order" && <div className="tfs-row tfs-note" data-testid="framing-order-note">{GRID_ORDER_NOTE}</div>}
        <div className="tfs-panel-table" role="table" aria-label="Panels in run order">
          <div className="tfs-row tfs-panel tfs-panel-head" role="row">
            <span role="columnheader" className="tfs-c-order">#</span>
            <span role="columnheader" className="tfs-c-label">PANEL</span>
            <span role="columnheader" className="tfs-c-on">SHOOT</span>
            <span role="columnheader" className="tfs-c-bar">CAPTURED</span>
            {p.showAltitude && <span role="columnheader" className="tfs-c-alt" data-testid="framing-alt-head">PEAK</span>}
          </div>
          {p.rows.map((r) => [
            <div key={r.label} className={`tfs-row tfs-panel ${r.skipped ? "tfs-off" : ""}`}
              role="row" data-panel={r.label}>
              <span role="cell" className="tfs-c-order tfs-mono">{r.order ?? ""}</span>
              <span role="cell" className="tfs-c-label tfs-mono">{r.label}</span>
              <span role="cell" className="tfs-c-on">
                <button type="button" className="tfs-btn" aria-pressed={!r.skipped}
                  aria-label={`${r.skipped ? "Shoot" : "Skip"} panel ${r.label}`}
                  onClick={() => p.onToggle(r.row, r.col)}>
                  {r.skipped ? "OFF" : "ON"}
                </button>
              </span>
              <span role="cell" className="tfs-c-bar">
                {r.total > 0 ? (
                  <span className="tfs-bar" role="progressbar" aria-valuemin={0}
                    aria-valuemax={r.total} aria-valuenow={Math.min(r.banked, r.total)}
                    aria-label={`${r.label}: ${r.banked} of ${r.total} frames captured`}>
                    <span className="tfs-bar-fill" style={{ width: `${Math.round(100 * Math.min(1, r.banked / r.total))}%` }} />
                    <span className="tfs-bar-text tfs-mono">{`${r.banked}/${r.total}`}</span>
                  </span>
                ) : r.banked > 0 ? (
                  // A skipped panel owes nothing, but what it banked comes back
                  // when it is shot again (spec 2.5).
                  <span className="tfs-mono tfs-note">{`${r.banked} kept`}</span>
                ) : null}
              </span>
              {p.showAltitude && (
                <span role="cell" className="tfs-c-alt tfs-mono" data-testid="framing-alt-cell"
                  title={r.altError ?? undefined}>
                  {r.peakAlt !== null ? `${Math.round(r.peakAlt)}\u00b0` : r.altError ? "no peak" : ""}
                </span>
              )}
            </div>,
            // The run's line for this panel, a row of its own under it so a
            // long reason wraps rather than squeezing the bar.
            runRowText(r.label, r.run) !== null && (
              <div key={`${r.label}-run`} className="tfs-row tfs-note" role="row"
                data-testid="framing-panel-run" data-panel-run={r.label}>
                <span role="cell">{runRowText(r.label, r.run)}</span>
              </div>
            ),
          ])}
        </div>
      </fieldset>
      {retryShown && (
        <div className="tfs-row" data-testid="framing-retry">
          <HonestButton className="tfs-btn" reason={p.retryLocked ?? null}
            onClick={() => p.onRetry?.()} onExplain={(why) => p.explain?.(why)}>
            {RETRY_SET_ASIDE}
          </HonestButton>
        </div>
      )}
      {retryShown && p.retryLocked && (
        <div className="tfs-row tfs-note" data-testid="framing-retry-why">{p.retryLocked}</div>
      )}
      {retryShown && p.retryNote && (
        <div className="tfs-row tfs-note" role="status" data-testid="framing-retry-note">{p.retryNote}</div>
      )}
      {p.showAltitude && p.nightCard}
    </section>
  );
}

export default PanelsSection;
