// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// RotatorCard.tsx — Equipment card for the rotator/CAA (spec §5.1). Readouts
// (sky PA + mechanical — both, honesty when unsynced), manual move/nudge/halt,
// reverse (only when supported), solve-driven "Rotate to PA", and the ROM
// (range-of-motion) block: a display-only SVG arc dial over segmented
// FULL/HALF/QUARTER, range-start with "Set to current position", tolerance.
// The dial is deliberately NOT drag-interactive — gloved hands at the scope;
// all input goes through the controls. Colors via theme vars only (night mode).
import { useEffect, useState, type JSX } from "react";
import type { RotatorConfig } from "../../types";
import { api, ApiError } from "../../api";
import { setRotatorConfig } from "../../api/backends";
import { useConfig, useSequence, useStatus, useStore } from "../../store";
import { accessPhrase, useCanConfigBackend, useCanControlCapture } from "../../lib/caps";
import { runIsLive } from "../../lib/lastSessionFrame";
import { adjustedPa, mod360 } from "../../lib/rotation";
import { allowedSweepDeg, arcPath, polarXY } from "../../lib/rotatorDial";
import { Panel, InfoDot } from "../ui";
import { Icon } from "../icons";
import { handleRadioKeyDown, rovingTabIndex } from "../../lib/radiogroup";
import ReadOnlyBadge from "../ReadOnlyBadge";

// Pure dial geometry lives in lib/rotatorDial (window-free so the assert-file
// tests can import it under tsx); re-export here so the helpers stay part of
// RotatorCard's public surface per the spec's contract.
export { allowedSweepDeg, arcPath, polarXY } from "../../lib/rotatorDial";

export const DEFAULT_ROTATOR_CFG: RotatorConfig = {
  range_type: "full",
  range_start_deg: 0,
  tolerance_deg: 1,
};

const RANGE_OPTIONS = ["full", "half", "quarter"] as const;

// TEST ROTATOR (#145, #594). The copy is the next-UI panel's own
// (`next/hubs/rig/rotator/RotatorPanel.tsx`), kept as a second copy rather
// than imported: this card serves `#/classic`, and importing across the
// legacy/next line drags one bundle into the other. The sentences are the same
// bar the button's name, which is in this card's sentence case; each side has a
// test that pins its own.
//
// A follow test that FAILED does not lock the button (#697): the server runs
// that test again on its own (two solves and the 20 degree step, the sign
// kept), which is the press that follows re-seating the coupling. Only a PASS
// locks it, and the follow test has an observing night (#709), so the lock
// lifts by itself when the night turns over.
const PREFLIGHT_NOTE =
  "Test rotator turns the rotator about 22 degrees and takes four plate solves: "
  + "two to learn which way the sky angle runs against the mechanical angle, two "
  + "to check the camera follows a 20 degree step. It measures only what this "
  + "connection has not measured yet. After a FAILED follow test it runs that "
  + "test again on its own (two solves and the 20 degree step, the sign kept), "
  + "so press it once the coupling has been re-seated.";

const PREFLIGHT_DONE_NOTE =
  "this connection has already measured the rotator tonight; it measures again "
  + "on the next observing night or after the rig reconnects.";

// WHAT THE RIG ANSWERS A PRESS WITH WHILE A RUN IS LIVE (#751, #698): 409
// `sequence_running`, its sentence built by `_refuse_while_sequence_runs` in
// `api/app.py` as `a sequence is running; <refused> refused, because <why>. Stop
// the run first`. Go and the nudges, Rotate to PA, Sync to sky and Test rotator
// are disabled while a run is live and carry their route's sentence as their
// title, word for word (the panel in `next/hubs/rig/rotator/RotatorPanel.tsx` has
// its own copy for the same reason this file keeps its own PREFLIGHT_NOTE;
// `w16RotatorCardSequenceLock.test.tsx` reads all five out of `app.py`). The
// reverse box joined them with #822: the rig refuses `/api/rotator/reverse`
// during a run too, because flipping the direction convention under a run
// changes what every later rotation means. Halt is not among them: the rig
// answers it.
const sequenceSentence = (refused: string, why: string): string =>
  `a sequence is running; ${refused} refused, because ${why}. Stop the run first`;

const SEQUENCE_MOVE = sequenceSentence(
  "rotator move", "it would turn the camera under the run's frames");
const SEQUENCE_ROTATE = sequenceSentence(
  "rotate to PA", "it would turn the camera under the run's frames");
const SEQUENCE_SYNC = sequenceSentence(
  "sync to sky", "it would re-calibrate the rotator's sky angle under the run");
const SEQUENCE_PREFLIGHT = sequenceSentence(
  "rotator preflight",
  "it turns the rotator about 22 degrees and takes four plate solves, which "
  + "would ruin the run's frames");
const SEQUENCE_REVERSE = sequenceSentence(
  "rotator reverse",
  "it would flip the rotator's direction convention under the run, changing "
  + "what every later rotation means");

// A disabled button's title is not shown on touch, so the lock is also said
// once in the card, naming what is dim and what is not.
const SEQUENCE_LOCK_NOTE =
  "A sequence is running, so Go, the nudges, Rotate to PA, Sync to sky, "
  + "Test rotator and reverse are locked until it stops. Halt stays live.";

// What the rig knows about this rotator, in one line. null/undefined (an older
// server) read as "not measured", which is not "failed".
function preflightLine(
  rot: { sky_sign?: 1 | -1 | null; trusted?: boolean | null },
): { text: string; failed: boolean } {
  if (rot.trusted === false) {
    return {
      text: "FAILED: the camera did not follow a 20 degree step, so rotation "
        + "is off. Press Test rotator to measure it again",
      failed: true,
    };
  }
  const sign = rot.sky_sign === 1 || rot.sky_sign === -1
    ? `sign ${rot.sky_sign === 1 ? "+1" : "-1"} measured`
    : "sign not measured";
  const follow = rot.trusted === true
    ? "follow test passed: the camera turned with a 20 degree step"
    : "follow test not run";
  return { text: `${sign}, ${follow}`, failed: false };
}

// Number("") is 0 (finite), which would silently read a blank field as a real
// 0° — route every raw-text->number conversion through this instead (SitePanel
// toNum precedent) so a blank field parses to NaN and is rejected at submit.
const toNum = (raw: string): number => (raw.trim() === "" ? NaN : Number(raw));

export default function RotatorCard(): JSX.Element | null {
  const status = useStatus();
  const config = useConfig();
  const canConfig = useCanConfigBackend();
  // The four motion routes (move/halt/reverse/rotate-to-pa) all require
  // control.capture server-side (server/astrodeck/api/app.py:2716-2775), NOT
  // config.backend — a distinct gate from the ROM config controls below.
  const canMove = useCanControlCapture();
  // `runIsLive`: running, paused, holding for cloud and winding down from an
  // abort are all `engine.running` on the rig, so all of them are refused. A
  // principal who cannot move the rotator is told that, not about the run.
  const runLive = runIsLive(useSequence()) && canMove;
  const rot = status?.rotator;

  const seed: RotatorConfig = { ...DEFAULT_ROTATOR_CFG, ...(config?.rotator ?? {}) };
  const [draft, setDraft] = useState<RotatorConfig>(seed);
  // Raw-string drafts for the ROM start/tolerance inputs (SitePanel.tsx
  // pattern): a controlled input driven from a re-parsed number wipes a
  // trailing "." or a lone leading "-" on every keystroke, since re-parsing
  // "1." back to the number 1 forces the input back to "1" before the user
  // can type the next digit. Keep the field's own text verbatim and only
  // parse/clamp at submit (onBlur / the buttons below).
  const [startText, setStartText] = useState<string>(String(seed.range_start_deg));
  const [toleranceText, setToleranceText] = useState<string>(String(seed.tolerance_deg));
  const [angle, setAngle] = useState<string>("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setDraft(seed);
    setStartText(String(seed.range_start_deg));
    setToleranceText(String(seed.tolerance_deg));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seed.range_type, seed.range_start_deg, seed.tolerance_deg]);

  if (!rot) return null;

  const run = async (fn: () => Promise<unknown>) => {
    setErr(null);
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message
        : e instanceof Error ? e.message : "rotator action failed");
    } finally {
      setBusy(false);
    }
  };

  const persist = (patch: Partial<RotatorConfig>) =>
    run(async () => {
      const next = { ...draft, ...patch };
      setDraft(next);
      try {
        await setRotatorConfig(next);
        await useStore.getState().loadConfig();
      } catch (e) {
        setDraft(seed);
        throw e;
      }
    });

  const move = (deg: number) =>
    run(() => api.post("/api/rotator/move", { position_deg: mod360(deg) }));

  const parsedAngle = Number(angle);
  const angleOk = angle.trim() !== "" && Number.isFinite(parsedAngle);
  const hint = angleOk ? adjustedPa(parsedAngle, rot, draft) : null;
  const preflight = preflightLine(rot);
  const preflightPassed = (rot.sky_sign === 1 || rot.sky_sign === -1)
    && rot.trusted === true;

  // --- dial geometry ---
  const size = 120, cx = 60, cy = 60, R = 48;
  const sweep = allowedSweepDeg(draft.range_type);
  const mechStart = draft.range_start_deg;
  const cur = polarXY(cx, cy, R, rot.mech_deg);
  const startMark = polarXY(cx, cy, R, mechStart);

  return (
    <Panel
      title={`Rotator · ${rot.name}`}
      right={
        <div className="flex items-center gap-2">
          {!canMove && <ReadOnlyBadge reason={`Read-only — moving the rotator needs ${accessPhrase("control.capture")}.`} />}
          <InfoDot label="About the rotator"
            content="Angles are sky position angle (PA); the mechanical readout is the raw device angle. The shaded arc is the allowed range of motion — set its start by rotating to a cable-safe position and pressing 'Set to current position'. Moves are refused during exposures." />
        </div>
      }
    >
      <div className="flex flex-wrap items-start gap-4">
        <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}
             role="img" aria-label={`Rotator at ${rot.mech_deg.toFixed(1)} degrees mechanical`}
             className="shrink-0">
          <circle cx={cx} cy={cy} r={R} fill="none"
                  stroke="var(--line-bright)" strokeWidth={2} />
          {sweep < 360 && (
            <path d={arcPath(cx, cy, R, mechStart, sweep)} fill="none"
                  stroke="var(--accent)" strokeOpacity={0.55} strokeWidth={5} />
          )}
          {sweep === 360 && (
            <circle cx={cx} cy={cy} r={R} fill="none"
                    stroke="var(--accent)" strokeOpacity={0.35} strokeWidth={5} />
          )}
          {sweep < 360 && (
            <circle cx={startMark.x} cy={startMark.y} r={3.5}
                    fill="var(--warn)" />
          )}
          <circle cx={cur.x} cy={cur.y} r={4.5} fill="var(--accent)"
                  stroke="var(--bg)" strokeWidth={1.5}>
            {rot.moving && (
              <animate attributeName="opacity" values="1;0.3;1" dur="1s"
                       repeatCount="indefinite" />
            )}
          </circle>
          <text x={cx} y={cy - 4} textAnchor="middle"
                className="mono" fill="var(--text)" fontSize="13">
            {rot.sky_deg.toFixed(1)}°
          </text>
          <text x={cx} y={cy + 12} textAnchor="middle"
                fill="var(--text-dim)" fontSize="9">
            mech {rot.mech_deg.toFixed(1)}°{rot.synced ? "" : " · unsynced"}
          </text>
        </svg>

        <div className="flex flex-col gap-2.5 min-w-[240px] flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="label w-20 shrink-0">Move to</span>
            <input className="field !py-1 w-20 mono" inputMode="decimal"
                   value={angle} placeholder="PA °"
                   onChange={(e) => setAngle(e.target.value)}
                   aria-label="Target position angle, degrees" />
            <button className="btn min-h-9" disabled={busy || !angleOk || !canMove || runLive}
                    title={runLive ? SEQUENCE_MOVE : undefined}
                    onClick={() => hint && void move(hint.target)}>Go</button>
            <button className="btn min-h-9" disabled={busy || !canMove || runLive}
                    title={runLive ? SEQUENCE_MOVE : undefined}
                    onClick={() => void move(rot.sky_deg - 1)}>−1°</button>
            <button className="btn min-h-9" disabled={busy || !canMove || runLive}
                    title={runLive ? SEQUENCE_MOVE : undefined}
                    onClick={() => void move(rot.sky_deg + 1)}>+1°</button>
            {/* Halt is urgent -> stays 1-tap even while `busy` (CaptureView Stop /
                SlewPad Stop precedent) but is still capability-gated for viewers. */}
            <button className="btn btn-danger min-h-9" disabled={!canMove}
                    onClick={() => void run(() => api.post("/api/rotator/halt"))}>
              Halt
            </button>
          </div>
          {hint?.adjusted && (
            <p className="text-[11px] text-warn leading-snug">
              ⚠ PA {Math.round(parsedAngle)}° is outside the range of motion —
              it will image as {Math.round(hint.target)}°.
            </p>
          )}
          <div className="flex items-center gap-2 flex-wrap">
            <button className="btn btn-accent min-h-9" disabled={busy || !canMove || runLive}
                    title={runLive ? SEQUENCE_ROTATE : undefined}
                    onClick={() => void run(() => api.post("/api/rotator/rotate-to-pa",
                      { target_pa_deg: angleOk ? mod360(parsedAngle) : rot.sky_deg }))}>
              Rotate to PA (plate solve)
            </button>
            {/* The other half of the same measurement, and the one that MOVES
                NOTHING. POST /api/rotator/sync-to-sky shipped in 0.2.65 with no
                caller anywhere in the UI, so the only way to establish the
                sky↔mechanical offset from the app was still to command a
                rotation nobody wanted — which is the complaint the route was
                added to answer. Placed beside Rotate deliberately: they are the
                same solve, and the difference between them is whether the
                camera turns. */}
            <button className="btn min-h-9" data-rotator-sync
                    disabled={busy || !canMove || runLive}
                    title={runLive ? SEQUENCE_SYNC
                      : "Plate-solve and tell the rotator its sky angle. Does not turn the camera."}
                    onClick={() => void run(() => api.post("/api/rotator/sync-to-sky", {}))}>
              Sync to sky (no movement)
            </button>
            {/* Measures the sign and checks the camera follows the rotator
                (#145, #594). Nothing in the product called those two
                measurements, so every automated rotation was refused as "sign
                not learned" until someone ran them by hand. Locked once the
                sign is measured and the follow test PASSED: a second press
                would change nothing. A FAILED test leaves it live (#697), so
                the owner can test again after re-seating the coupling. */}
            <button className="btn min-h-9" data-rotator-preflight
                    disabled={busy || !canMove || runLive || preflightPassed}
                    title={runLive ? SEQUENCE_PREFLIGHT
                      : preflightPassed ? PREFLIGHT_DONE_NOTE : PREFLIGHT_NOTE}
                    onClick={() => void run(() => api.post("/api/rotator/preflight", {}))}>
              Test rotator
            </button>
            {rot.can_reverse && (
              <label className="flex items-center gap-1.5 text-[11px] text-dim"
                     title={runLive ? SEQUENCE_REVERSE : undefined}>
                <input type="checkbox" checked={rot.reverse}
                       disabled={busy || !canMove || runLive}
                       onChange={(e) => void run(() => api.post("/api/rotator/reverse",
                         { reverse: e.target.checked }))} />
                reverse
              </label>
            )}
          </div>
          {runLive && (
            <p className="text-[11px] text-dim inline-flex items-center gap-1.5"
               data-rotator-sequence-lock>
              <Icon name="lock" size={11} />
              {SEQUENCE_LOCK_NOTE}
            </p>
          )}
          <p className={`text-[11px] leading-snug ${preflight.failed ? "text-bad" : "text-dim"}`}
             data-rotator-preflight-line>
            {preflight.text}
          </p>
          <p className="text-[11px] text-faint leading-snug">{PREFLIGHT_NOTE}</p>
          {!canMove && (
            <p className="text-[11px] text-dim inline-flex items-center gap-1.5">
              <Icon name="lock" size={11} />
              Read-only — moving the rotator needs {accessPhrase("control.capture")}.
            </p>
          )}

          <div className="border border-line bg-bg/60 px-3 py-2.5 flex flex-col gap-2">
            <span className="label">Range of motion</span>
            <div className="flex items-center gap-1.5" role="radiogroup"
                 aria-label="Mechanical range" aria-disabled={(!canConfig || busy) || undefined}>
              {RANGE_OPTIONS.map((rt, i) => {
                const rangeDisabled = !canConfig || busy;
                const activeIndex = RANGE_OPTIONS.indexOf(draft.range_type);
                const select = (idx: number) => {
                  if (!rangeDisabled) void persist({ range_type: RANGE_OPTIONS[idx] });
                };
                return (
                <button key={rt}
                        role="radio"
                        className={`btn min-h-9 uppercase text-[10px] tracking-wider ${
                          draft.range_type === rt ? "btn-accent" : ""}`}
                        disabled={rangeDisabled}
                        aria-checked={draft.range_type === rt}
                        // UX-20: roving tabindex + shared arrow-key model
                        tabIndex={rovingTabIndex(i, activeIndex)}
                        onClick={() => select(i)}
                        onKeyDown={(e) => { if (!rangeDisabled) handleRadioKeyDown(e, i, RANGE_OPTIONS.length, select); }}>
                  {rt}
                </button>
                );
              })}
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-[11px] text-dim w-20 shrink-0">Start</span>
              <input className="field !py-1 w-20 mono" inputMode="decimal"
                     value={startText}
                     disabled={!canConfig || busy || draft.range_type === "full"}
                     aria-label="Range start, mechanical degrees"
                     onChange={(e) => setStartText(e.target.value)}
                     onBlur={() => {
                       const v = toNum(startText);
                       if (Number.isFinite(v)) void persist({ range_start_deg: mod360(v) });
                       else setStartText(String(draft.range_start_deg));
                     }} />
              <button className="btn min-h-9"
                      disabled={!canConfig || busy || draft.range_type === "full"}
                      onClick={() => {
                        const v = mod360(rot.mech_deg);
                        setStartText(String(v));
                        void persist({ range_start_deg: v });
                      }}>
                Set to current position
              </button>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-[11px] text-dim w-20 shrink-0">Tolerance</span>
              <input className="field !py-1 w-16 mono" inputMode="decimal"
                     value={toleranceText}
                     disabled={!canConfig || busy}
                     aria-label="Rotate tolerance, degrees"
                     onChange={(e) => setToleranceText(e.target.value)}
                     onBlur={() => {
                       const v = toNum(toleranceText);
                       if (Number.isFinite(v)) void persist({ tolerance_deg: v });
                       else setToleranceText(String(draft.tolerance_deg));
                     }} />
              <span className="text-[11px] text-faint">° (mod-180)</span>
            </div>
          </div>

          {!canConfig && (
            <p className="text-[11px] text-dim inline-flex items-center gap-1.5">
              <Icon name="lock" size={11} />
              Read-only — changing the range of motion needs {accessPhrase("config.backend")}.
            </p>
          )}
          {err && (
            <p className="text-[11px] text-bad inline-flex items-center gap-1.5">
              <Icon name="alert" size={11} /> {err}
            </p>
          )}
        </div>
      </div>
    </Panel>
  );
}
