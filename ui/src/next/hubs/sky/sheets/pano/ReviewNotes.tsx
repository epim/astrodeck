// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T29: the flagged review of the panorama scanner inside the horizon sheet (SPEC-v2 2.9). What the sheet keeps in
// horizon.tsx is state and the strip; what is here is everything the review says or draws that depends only on the
// draft: the line styles (which bins are certain), the notes under the strip with the "use the traced line" action,
// the north card with its nudge buttons and the no-compass lock, and the selected-point card with its Delete button.
//
// The line is what Save writes. A bin that is Measured, Edited or Kept publishes a value somebody stands behind and is
// drawn solid gold. A bin that is Low, Unknown or Tall publishes 90 (blocked) and is drawn red dotted; over a Low bin
// the tracer's own suggestion is drawn as well, thin and dashed, because that is what the action below would publish.
// A vertex sitting on one of those bins is hollow and amber. An edit turns the bins it touched into Edited (editDraft),
// so a point the user placed is never drawn as uncertain.
import type { JSX } from 'react';
import { ActionButton, Card } from '../../../../ui';
import { horizonAltAt } from '../../../../lib/horizonModel';
import { draftSummary } from '../horizonDraft';
import type { DraftSpan } from '../horizonDraft';
import { HX, HY } from '../horizonStrip';
import { fill, REVIEW_TEXT } from './copy';
import { BinState, PROFILE_BINS } from './types';
import type { HorizonDraft, HorizonPoint, NorthEstimate, ScanResult } from './types';

const BINS_PER_DEG = PROFILE_BINS / 360;

/** Bins whose published value somebody stands behind: the scan measured it, the user set it, or the earlier line stays. */
export function isCertainBin(state: number): boolean {
  return state === BinState.Measured || state === BinState.Edited || state === BinState.Kept;
}

/** A run of bins drawn in one style: `fromDeg` to `toDeg` on the strip's azimuth axis, inside [0, 360]. */
export interface LineRun { kind: 'certain' | 'blocked'; fromDeg: number; toDeg: number }

/** The strip's line cut into maximal runs of one style, in azimuth order. Wrapping is not joined: a run that touches
 *  north is two runs, one ending at 360 and one starting at 0, which is how the strip is drawn. */
export function lineRuns(draft: HorizonDraft): LineRun[] {
  const runs: LineRun[] = [];
  let first = 0;
  for (let i = 1; i <= PROFILE_BINS; i++) {
    const kind = (b: number): 'certain' | 'blocked' => (isCertainBin(draft.state[b]) ? 'certain' : 'blocked');
    if (i === PROFILE_BINS || kind(i) !== kind(first)) {
      runs.push({ kind: kind(first), fromDeg: first / BINS_PER_DEG, toDeg: i / BINS_PER_DEG });
      first = i;
    }
  }
  return runs;
}

/** The SVG path of `points` between two azimuths: the line's value at both ends and every vertex strictly between. */
export function runPathD(points: readonly HorizonPoint[], fromDeg: number, toDeg: number): string {
  if (points.length === 0) return '';
  const line = [...points];
  const inside = line.filter((p) => p.az > fromDeg && p.az < toDeg).sort((a, b) => a.az - b.az);
  const path: HorizonPoint[] = [{ az: fromDeg, alt: horizonAltAt(line, fromDeg) }, ...inside, { az: toDeg, alt: horizonAltAt(line, toDeg) }];
  return path.map((p, i) => `${i === 0 ? 'M' : 'L'}${HX(p.az).toFixed(1)},${HY(p.alt).toFixed(1)}`).join(' ');
}

/** The tracer's suggestion over Low bins, as paths (one per run of Low bins with a suggestion), stepping at bin centres. */
export function suggestionPaths(draft: HorizonDraft): string[] {
  const paths: string[] = [];
  let cur: string[] = [];
  const flush = () => { if (cur.length > 1) paths.push(cur.join(' ')); cur = []; };
  for (let i = 0; i < PROFILE_BINS; i++) {
    const s = draft.suggested[i];
    if (draft.state[i] === BinState.Low && Number.isFinite(s)) {
      cur.push(`${cur.length === 0 ? 'M' : 'L'}${HX((i + 0.5) / BINS_PER_DEG).toFixed(1)},${HY(s).toFixed(1)}`);
    } else flush();
  }
  flush();
  return paths;
}

/** True when the vertex sits on a bin that is not certain. A vertex on a bin edge belongs to both bins beside it. */
export function isUncertainVertex(draft: HorizonDraft, az: number): boolean {
  const at = ((az % 360) + 360) % 360 * BINS_PER_DEG;
  const right = Math.floor(at) % PROFILE_BINS;
  const sits = (b: number) => !isCertainBin(draft.state[b]);
  if (Math.abs(at - Math.round(at)) < 1e-9) return sits(right) || sits((right + PROFILE_BINS - 1) % PROFILE_BINS);
  return sits(right);
}

function degText(v: number): string {
  return Number.isInteger(v) ? String(v) : v.toFixed(1);
}

function spanLine(template: string, s: DraftSpan, extra: Record<string, string | number> = {}): string {
  return fill(template, { from: degText(s.fromDeg), to: degText(s.toDeg), ...extra });
}

export interface ReviewNotesProps {
  draft: HorizonDraft;
  result: Pick<ScanResult, 'endedBy' | 'partial' | 'loop'>;
  /** Why a write is refused right now, or null. The accept action changes the line, so it is locked with Save. */
  lockedReason: string | null;
  onExplain: (reason: string) => void;
  onAcceptTraced: () => void;
}

/** The notes under the strip (2.9): one line for each thing that is blocked and why, and the action for the Low ones. */
export function ReviewNotes({ draft, result, lockedReason, onExplain, onAcceptTraced }: ReviewNotesProps): JSX.Element {
  const sum = draftSummary(draft);
  const lowDeg = sum.low.reduce((t, s) => t + s.bins / BINS_PER_DEG, 0);
  return (
    <div data-testid="review-notes">
      {sum.low.length > 0 && <>
        <p className="photosphere-detail" data-testid="review-note-low">{fill(REVIEW_TEXT.lowSpans, { n: sum.low.length, d: degText(lowDeg) })}</p>
        <ActionButton kind="secondary" size="md" lockedReason={lockedReason} onExplain={onExplain} onPress={onAcceptTraced} data-testid="accept-traced">
          {REVIEW_TEXT.acceptLow}
        </ActionButton>
      </>}
      {sum.unseen.map((s, i) => <p key={`u${i}`} className="photosphere-detail" data-testid="review-note-unseen">{spanLine(REVIEW_TEXT.unseen, s)}</p>)}
      {sum.kept.map((s, i) => <p key={`k${i}`} className="photosphere-detail" data-testid="review-note-kept">{spanLine(REVIEW_TEXT.unseenKept, s)}</p>)}
      {sum.dark.map((s, i) => <p key={`d${i}`} className="photosphere-detail" data-testid="review-note-dark">{spanLine(REVIEW_TEXT.dark, s)}</p>)}
      {sum.tall.map((s, i) => <p key={`t${i}`} className="photosphere-detail" data-testid="review-note-tall">{spanLine(REVIEW_TEXT.tall, s, { d: Math.round(s.atLeastDeg) })}</p>)}
      {draft.lowLight && <p className="photosphere-error" role="status" data-testid="review-note-lowlight">{REVIEW_TEXT.lowLight}</p>}
      {result.endedBy === 'hidden' && <p className="photosphere-detail" data-testid="review-note-hidden">{REVIEW_TEXT.hiddenEnd}</p>}
      {result.loop.closed && result.loop.method === 'gyro' && <p className="photosphere-detail" data-testid="review-note-gyro">{REVIEW_TEXT.gyroClosure}</p>}
      {result.partial && <p className="photosphere-detail" data-testid="review-note-partial">{REVIEW_TEXT.partial}</p>}
    </div>
  );
}

/** The azimuth steps of the nudge buttons (2.9), in degrees. */
export const NUDGE_STEPS: readonly number[] = Object.freeze([-5, -0.5, 0.5, 5]);

export interface NorthCardProps {
  north: NorthEstimate | null;
  declinationApplied: boolean;
  /** The scan's raster is still in memory, so the panorama can be turned: without it the buttons are not offered. */
  canNudge: boolean;
  /** The user has tapped "North is right". */
  confirmed: boolean;
  lockedReason: string | null;
  onExplain: (reason: string) => void;
  onNudge: (deg: number) => void;
  onConfirm: () => void;
}

/** True when Save must stay locked: no absolute compass reading reached the scan and the user has not yet said north is right. */
export function northLocksSave(north: NorthEstimate | null, confirmed: boolean): boolean {
  return north === null && !confirmed;
}

/** The north card (2.9, 4.12): where north came from and how sure it is, whether it is true or magnetic, and the nudge. */
export function NorthCard({ north, declinationApplied, canNudge, confirmed, lockedReason, onExplain, onNudge, onConfirm }: NorthCardProps): JSX.Element {
  const sigma = north === null ? 0 : Math.round(north.sigmaDeg);
  return (
    <Card data-testid="north-card">
      {north === null
        ? <p className="photosphere-error" role="status" data-testid="north-source">{REVIEW_TEXT.noCompass}</p>
        : <p className="photosphere-detail" data-testid="north-source">{fill(north.stable ? REVIEW_TEXT.northScan : REVIEW_TEXT.northUnstable, { s: sigma })}</p>}
      <p className="photosphere-detail" data-testid="north-frame">{declinationApplied ? REVIEW_TEXT.northTrue : REVIEW_TEXT.northMagnetic}</p>
      {canNudge && <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }} data-testid="north-nudge">
        {NUDGE_STEPS.map((deg) => (
          <ActionButton key={deg} kind="secondary" size="md" lockedReason={lockedReason} onExplain={onExplain}
            ariaLabel={`Turn the panorama and the line ${deg > 0 ? '+' : '-'}${Math.abs(deg)} degrees`}
            onPress={() => onNudge(deg)} data-testid={`north-nudge-${deg}`}>
            {deg > 0 ? `+${deg}` : String(deg)}
          </ActionButton>
        ))}
      </div>}
      {north === null && !confirmed && (
        <ActionButton kind="primary" size="md" onPress={onConfirm} data-testid="north-is-right">{REVIEW_TEXT.northIsRight}</ActionButton>
      )}
    </Card>
  );
}

export interface SelectedPointProps {
  point: HorizonPoint;
  lockedReason: string | null;
  onExplain: (reason: string) => void;
  onDelete: () => void;
}

/** A still tap on a point selects it (O2): its azimuth and altitude, and the button that deletes it. */
export function SelectedPoint({ point, lockedReason, onExplain, onDelete }: SelectedPointProps): JSX.Element {
  return (
    <div data-testid="selected-point" style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
      <span className="photosphere-detail" data-testid="selected-point-readout">{`Azimuth ${degText(point.az)} deg, altitude ${degText(point.alt)} deg`}</span>
      <ActionButton kind="danger" size="md" lockedReason={lockedReason} onExplain={onExplain} onPress={onDelete} data-testid="delete-point">Delete point</ActionButton>
    </div>
  );
}
