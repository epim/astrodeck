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
import { useConfig, useStatus, useStore } from "../../store";
import { accessPhrase, useCanConfigBackend, useCanControlCapture } from "../../lib/caps";
import { adjustedPa, mod360 } from "../../lib/rotation";
import { allowedSweepDeg, arcPath, polarXY } from "../../lib/rotatorDial";
import { Panel, InfoDot } from "../ui";
import { Icon } from "../icons";
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
            <button className="btn min-h-9" disabled={busy || !angleOk || !canMove}
                    onClick={() => hint && void move(hint.target)}>Go</button>
            <button className="btn min-h-9" disabled={busy || !canMove}
                    onClick={() => void move(rot.sky_deg - 1)}>−1°</button>
            <button className="btn min-h-9" disabled={busy || !canMove}
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
            <button className="btn btn-accent min-h-9" disabled={busy || !canMove}
                    onClick={() => void run(() => api.post("/api/rotator/rotate-to-pa",
                      { target_pa_deg: angleOk ? mod360(parsedAngle) : rot.sky_deg }))}>
              Rotate to PA (plate solve)
            </button>
            {rot.can_reverse && (
              <label className="flex items-center gap-1.5 text-[11px] text-dim">
                <input type="checkbox" checked={rot.reverse} disabled={busy || !canMove}
                       onChange={(e) => void run(() => api.post("/api/rotator/reverse",
                         { reverse: e.target.checked }))} />
                reverse
              </label>
            )}
          </div>
          {!canMove && (
            <p className="text-[11px] text-dim inline-flex items-center gap-1.5">
              <Icon name="lock" size={11} />
              Read-only — moving the rotator needs {accessPhrase("control.capture")}.
            </p>
          )}

          <div className="border border-line bg-bg/60 px-3 py-2.5 flex flex-col gap-2">
            <span className="label">Range of motion</span>
            <div className="flex items-center gap-1.5" role="radiogroup"
                 aria-label="Mechanical range">
              {RANGE_OPTIONS.map((rt) => (
                <button key={rt}
                        role="radio"
                        className={`btn min-h-9 uppercase text-[10px] tracking-wider ${
                          draft.range_type === rt ? "btn-accent" : ""}`}
                        disabled={!canConfig || busy}
                        aria-checked={draft.range_type === rt}
                        onClick={() => void persist({ range_type: rt })}>
                  {rt}
                </button>
              ))}
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
