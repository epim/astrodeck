// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T25: the capture screen of the panorama scanner inside the horizon sheet (SPEC-v2 2.3-2.5, 2.11, 3.5, 4.12). It owns
// what the scanner cannot: the <video> element (rendered and visible, handed to ScanView as its `videoSlot`), the
// buttons (Start, Pause and Resume, Review, Cancel), the camera choice, the "record this scan" box, the compass line
// (the one fact about the sensors that Start's own state does not show), the 500 ms `tick`, the visibility listener,
// and the download links for the report and the recording. ScanView draws what the scanner's status says; the
// scanner does the scanning; the sheet decides what to do with the result.
//
// Lifecycle. The scanner is created once per mount (`createScanner`, or `new PanoramaScanner`), started when the
// component mounts, given the declination closure, and stopped when it unmounts. The iOS motion prompt is NOT asked
// here: a mount effect is not inside the user's tap, so the sheet's scan button asks it (ruling S14) and mounts this
// component only on a yes. `start()` asks again as its own first statement, which is harmless.
//
// Ending. Review, a hidden tab and Cancel are the three ways out, and each hands the sheet the report (and the
// recording, when one was made) as data URLs, taken BEFORE the scanner is stopped because `stop()` ends the camera
// session the report describes (issue #66). Review runs `finish` one paint after the tap, so the "Working out the
// horizon" line is on screen while the synchronous work (2.8) holds the page; a hidden tab runs it at once, since
// nothing is being painted. A tab hidden before Start ends nothing, because there is no scan to keep: the camera may
// be taken away by the browser, which the cue and Cancel already cover.
//
// Privacy (4.12). `toTrue` is passed to the scanner and nowhere else: it is not stored, shown, logged or reported here,
// and the value it adds is not known to this file.
import { useEffect, useRef, useState } from 'react';
import type { JSX } from 'react';
import { ActionButton, Checkbox22 } from '../../../../ui';
import { cameraErrorText } from './cameraSource';
import { ERROR_TEXT } from './copy';
import { reportJson } from './report';
import { PanoramaScanner } from './scanner';
import { ScanView } from './ScanView';
import type { HorizonPoint, ScanPhase, ScanReport, ScanResult, ScannerLike, ScannerOptions, ScanStatus } from './types';

export interface PanoScannerControl {
  start(video: HTMLVideoElement, deviceId?: string): Promise<void>;
  finish(previous: readonly HorizonPoint[] | null, endedBy?: 'user' | 'hidden' | 'error'): ScanResult;
  stop(): void;
  setDeclination(toTrue: ((azMagDeg: number) => number) | null): void;
  setRecording(on: boolean): void;
  report(): ScanReport;
  recording(): string | null;
}

export interface PanoCaptureProps {
  previous: readonly HorizonPoint[];
  toTrue: ((azMagDeg: number) => number) | null;     // built by the sheet from the site position; null when unavailable
  sensorOnly: boolean;
  onFinish(result: ScanResult, links: { report: string; recording: string | null }): void;
  onCancel(links: { report: string | null; recording: string | null }): void;
  createScanner?: (o: ScannerOptions) => ScannerLike & PanoScannerControl;   // tests inject a fake; default new PanoramaScanner(o)
}

/** The stored camera choice, the one today's panel keeps (horizon.tsx), so the two scanners share a lens. */
const CAMERA_KEY = 'astrodeck.photosphere.camera';
/** `tick` runs every 500 ms (3.5): the time-based states while frames may be absent. */
const TICK_MS = 500;
/** The ultra-wide note (4.1), when the locked focal implies a short axis above this (the scanner's own test). */
const ULTRA_WIDE_SHORT_FOV_DEG = 60;

const ULTRA_WIDE_NOTE = 'This looks like the ultra-wide camera. Choose the main camera.';
const RECORD_LABEL = 'Record this scan for diagnosis';
const RECORD_DETAIL = 'Saves every camera picture and sensor reading of this scan in a file on this phone, to help fix the scanner. It includes photos of your surroundings. Use it for the S25 check.';
/** Today's lock note for the lens picker while a stream is opening (horizon.tsx): a second open strands both. */
const CAMERA_LOCKED = 'Opening the camera. The lens can be changed once it is ready.';
const WORKING_TEXT = 'Working out the horizon';
const NO_KEYFRAME_YET = 'Review opens once the first picture is kept. Turn a little first.';

const defaultScanner = (o: ScannerOptions): ScannerLike & PanoScannerControl => new PanoramaScanner(o);

function storedCamera(): string | undefined {
  try { return localStorage.getItem(CAMERA_KEY) ?? undefined; } catch { return undefined; }   // private browsing
}

function rememberCamera(deviceId: string | undefined): void {
  if (!deviceId) return;
  try { localStorage.setItem(CAMERA_KEY, deviceId); } catch { /* private browsing */ }
}

function forgetCamera(): void {
  try { localStorage.removeItem(CAMERA_KEY); } catch { /* private browsing */ }
}

const dataLink = (mime: string, text: string) => `data:${mime};charset=utf-8,${encodeURIComponent(text)}`;

/** The report and the recording as download links. A report that cannot be built must not block the way out, so it
 *  becomes a link to a note that says so, and a recording that cannot be built is simply absent. */
function linksOf(scanner: PanoScannerControl): { report: string; recording: string | null } {
  let report: string;
  try { report = dataLink('application/json', reportJson(scanner.report())); }
  catch (e) {
    const why = e instanceof Error ? e.message : 'unknown';
    report = dataLink('application/json', JSON.stringify({ format: 'astrodeck-pano-report', version: 2, error: `report unavailable: ${why}` }));
  }
  let recording: string | null = null;
  try {
    const text = scanner.recording();
    if (text !== null) recording = dataLink('application/x-ndjson', text);
  } catch { /* a recording that cannot be built is simply not offered */ }
  return { report, recording };
}

/** Run `fn` after the next paint: a frame, then a task, so the state set just before it is on screen first. */
function afterPaint(fn: () => void): () => void {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let frame = 0;
  const later = () => { timer = setTimeout(() => { timer = null; fn(); }, 0); };
  if (typeof requestAnimationFrame === 'function') frame = requestAnimationFrame(later);
  else later();
  return () => {
    if (frame !== 0 && typeof cancelAnimationFrame === 'function') cancelAnimationFrame(frame);
    if (timer !== null) clearTimeout(timer);
  };
}

// What this component shows, as a small value that is compared before it is stored: the scanner notifies on every
// camera frame, and nothing here needs to render at that rate (ScanView draws the frames).
interface Facts {
  phase: ScanPhase; canBegin: boolean; hasKeyframe: boolean; closed: boolean; error: string | null;
  farbled: boolean | null; ultraWide: boolean; cameras: string;
  compass: boolean;   // a north estimate exists: an absolute reading has arrived
}

function factsOf(scanner: ScannerLike): Facts {
  const s: ScanStatus = scanner.status;
  return {
    phase: s.phase, canBegin: scanner.canBegin, hasKeyframe: s.keyframes >= 1, closed: s.loop.closed, error: s.error,
    farbled: s.farbled, ultraWide: s.focal.state !== 'prior' && s.focal.shortFovDeg > ULTRA_WIDE_SHORT_FOV_DEG,
    cameras: `${scanner.cameraChoices.map(c => c.deviceId).join('|')}#${scanner.activeCameraId ?? ''}`,
    compass: s.north !== null,
  };
}

function sameFacts(a: Facts, b: Facts): boolean {
  return (Object.keys(a) as (keyof Facts)[]).every(k => a[k] === b[k]);
}

/** Why Start is locked, in words the user can act on. Null when it is not. */
function startReason(f: Facts): string | null {
  if (f.canBegin) return null;
  if (f.error !== null) return 'The scan cannot start: see the message above.';
  if (f.phase === 'idle' || f.phase === 'opening') return 'Opening camera';
  return 'Waiting for the camera and the motion sensor';
}

export function PanoCapture({ previous, toTrue, sensorOnly, onFinish, onCancel, createScanner }: PanoCaptureProps): JSX.Element {
  const [scanner] = useState(() => (createScanner ?? defaultScanner)({ sensorOnly }));
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);
  // The latest props, for the listeners and the timer that outlive a render.
  const latest = useRef({ previous, onFinish, onCancel });
  latest.current = { previous, onFinish, onCancel };

  const [facts, setFacts] = useState(() => factsOf(scanner));
  const factsRef = useRef(facts);
  const [record, setRecord] = useState(false);
  const [working, setWorking] = useState(false);
  const [openError, setOpenError] = useState<string | null>(null);

  const alive = useRef(false);
  const finished = useRef(false);
  const openSeq = useRef(0);
  const cancelPaint = useRef<(() => void) | null>(null);

  // ---- Open the camera ----

  /** Start the scanner on the video element; again, with a device id, for the camera choice. A superseded call (a newer
   *  choice, an unmount) is ignored whatever it finds. The stored choice is cleared when the device is gone or cannot
   *  satisfy the constraints, as today's panel does, so the next open falls back to the default rear camera. */
  const open = (deviceId?: string) => {
    const video = videoRef.current;
    if (!video) return;
    const seq = ++openSeq.current;
    setOpenError(null);
    scanner.start(video, deviceId ?? storedCamera()).then(
      () => { if (alive.current && seq === openSeq.current) rememberCamera(scanner.activeCameraId); },
      (e: unknown) => {
        if (!alive.current || seq !== openSeq.current) return;
        const name = typeof (e as { name?: unknown } | null)?.name === 'string' ? (e as { name: string }).name : '';
        if (name === 'OverconstrainedError' || name === 'NotFoundError') forgetCamera();
        setOpenError(cameraErrorText(e));
      },
    );
  };

  useEffect(() => {
    alive.current = true;
    finished.current = false;
    open();
    rootRef.current?.scrollIntoView?.({ block: 'start', behavior: 'smooth' });
    rootRef.current?.focus({ preventScroll: true });
    return () => {
      alive.current = false;
      openSeq.current++;
      cancelPaint.current?.();
      cancelPaint.current = null;
      scanner.stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scanner]);

  // The declination closure goes to the scanner and stays there (4.12).
  useEffect(() => { scanner.setDeclination(toTrue); }, [scanner, toTrue]);

  // What the buttons and notes show, kept in step with the scanner without rendering at the camera's frame rate.
  useEffect(() => {
    const sync = () => {
      const next = factsOf(scanner);
      if (sameFacts(factsRef.current, next)) return;
      factsRef.current = next;
      setFacts(next);
    };
    sync();
    return scanner.subscribe(sync);
  }, [scanner]);

  // The time-based states, outside the frame path (3.5).
  useEffect(() => {
    const id = setInterval(() => scanner.tick(performance.now()), TICK_MS);
    return () => clearInterval(id);
  }, [scanner]);

  // ---- Ending ----

  const finishNow = (endedBy: 'user' | 'hidden') => {
    if (finished.current) return;
    finished.current = true;
    cancelPaint.current?.();
    cancelPaint.current = null;
    let result: ScanResult;
    try { result = scanner.finish(latest.current.previous, endedBy); }
    catch (e) {
      finished.current = false;
      setWorking(false);
      setOpenError(e instanceof Error ? e.message : 'Could not work out the horizon. Try again.');
      return;
    }
    latest.current.onFinish(result, linksOf(scanner));
  };

  // A hidden tab ends a scan that has begun into the partial review (2.6). Before Start there is nothing to keep.
  useEffect(() => {
    const onVisibility = () => {
      if (document.visibilityState !== 'hidden') return;
      const phase = scanner.status.phase;
      if (phase === 'scanning' || phase === 'paused') finishNow('hidden');
    };
    document.addEventListener('visibilitychange', onVisibility);
    return () => document.removeEventListener('visibilitychange', onVisibility);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scanner]);

  const review = () => {
    if (finished.current || working) return;
    setWorking(true);
    cancelPaint.current = afterPaint(() => { cancelPaint.current = null; finishNow('user'); });
  };

  const cancel = () => {
    if (finished.current) return;
    finished.current = true;
    cancelPaint.current?.();
    cancelPaint.current = null;
    const { report, recording } = linksOf(scanner);
    latest.current.onCancel({ report, recording });
  };

  // ---- Render ----

  const begun = facts.phase === 'scanning' || facts.phase === 'paused' || facts.phase === 'finishing' || facts.phase === 'done';
  const opening = facts.phase === 'opening';
  const reason = startReason(facts);
  const choices = scanner.cameraChoices;
  const activeId = scanner.activeCameraId;
  const shownError = openError !== null && facts.error === null ? openError : null;

  return (
    <div ref={rootRef} tabIndex={-1} className="photosphere-capture" data-testid="pano-capture" data-phase={facts.phase}>
      <ScanView scanner={scanner} videoSlot={<>
        <video ref={videoRef} muted playsInline autoPlay aria-label="Live surroundings camera" data-testid="pano-video" />
        {opening && <div className="photosphere-opening" data-testid="pano-opening">Opening camera</div>}
      </>} />

      {!begun && !opening && facts.error === null && <p className="photosphere-detail" data-testid="pano-sensors">
        {`Compass: ${facts.compass ? 'ready' : 'no reading yet'}.`}
      </p>}
      {shownError !== null && <p role="alert" className="photosphere-error" data-testid="pano-open-error">{shownError}</p>}
      {facts.ultraWide && <p role="status" className="photosphere-detail" data-testid="pano-ultrawide">{ULTRA_WIDE_NOTE}</p>}

      {!begun && choices.length > 0 && <label className="photosphere-camera-choice">Camera
        {opening
          // Locked, it renders as a focusable chip carrying the current lens and the reason, as today's panel does: the
          // native `disabled` would take the control and its reason out of the accessibility tree.
          ? <button type="button" className="nx-btn nx-locked" data-kind="secondary" aria-disabled="true" data-locked="true"
              title={CAMERA_LOCKED} aria-label={`Camera for horizon scan - ${CAMERA_LOCKED}`} data-testid="pano-camera-locked">
              <span className="nx-btn-label">{choices.find(c => c.deviceId === activeId)?.label ?? 'Default rear camera'}</span>
            </button>
          : <select aria-label="Camera for horizon scan" data-testid="pano-camera" value={activeId ?? ''} onChange={e => open(e.target.value)}>
              {!activeId && <option value="">Default rear camera</option>}
              {choices.map(c => <option key={c.deviceId} value={c.deviceId}>{c.label}</option>)}
            </select>}
        <small>Choose another rear camera if this lens is blurry or noisy. Lens names come from your phone.</small>
      </label>}

      {!begun && <>
        <Checkbox22 checked={record} label={RECORD_LABEL} data-testid="pano-record"
          onChange={on => { setRecord(on); scanner.setRecording(on); }} />
        <p className="photosphere-detail">{RECORD_DETAIL}</p>
      </>}

      {working && <p role="status" className="photosphere-hint" data-testid="pano-working">{WORKING_TEXT}</p>}

      <div className="photosphere-actions">
        {!begun
          ? <ActionButton kind="primary" size="lg" lockedReason={reason} data-testid="pano-start"
              onPress={() => { scanner.setRecording(record); scanner.begin(); }}>Start</ActionButton>
          : <>
              <ActionButton kind="primary" size="lg" data-testid="pano-review" onPress={review}
                lockedReason={working ? WORKING_TEXT : facts.hasKeyframe ? null : NO_KEYFRAME_YET}>
                {facts.closed ? 'Review' : 'Review partial scan'}
              </ActionButton>
              {facts.phase === 'paused'
                ? <ActionButton kind="secondary" size="md" data-testid="pano-pause" onPress={() => scanner.resume()}>Resume</ActionButton>
                : <ActionButton kind="secondary" size="md" data-testid="pano-pause" onPress={() => scanner.pause()}
                    lockedReason={working ? WORKING_TEXT : null}>Pause</ActionButton>}
            </>}
        <ActionButton kind="secondary" size="md" data-testid="pano-cancel" onPress={cancel}>Cancel</ActionButton>
      </div>

      {!begun && reason !== null && <p className="photosphere-detail" data-testid="pano-start-reason">{reason}</p>}
      {facts.farbled === true && <p role="status" className="photosphere-detail" data-testid="pano-farbled">{ERROR_TEXT.farbled}</p>}
    </div>
  );
}
