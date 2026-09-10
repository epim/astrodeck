// useIncidents.ts - the ONE place the shell folds store slices into
// `next/lib/incidents.ts`'s pure `IncidentInputs`.
//
// Three parts of the chrome need the same answer - the amber banner on every
// other hub, the pulsing dot on the Session tab, and the same dot on the rail -
// and if each assembled its own inputs they would drift, which is exactly how a
// tab dot ends up pulsing for an incident the banner has stopped announcing.
// The derivation itself stays pure and tested in `lib/incidents.ts`; this file
// is only the wiring, and it holds no state of its own.

import { useStore } from "../../store";
import { deriveIncidents, type Incident, type IncidentInputs } from "../lib";

/** Every active incident, most severe first. `nowMs` is passed in by the caller
 *  so a render is a pure function of it - a component that wants a ticking
 *  "since" line owns its own clock rather than this hook forcing one on every
 *  consumer. */
export function useIncidents(nowMs: number): Incident[] {
  const sequence = useStore((s) => s.sequence);
  const safety = useStore((s) => s.safety);
  const wsPhase = useStore((s) => s.wsPhase);
  const telemetryStale = useStore((s) => s.telemetryStale);
  const wsLastEvent = useStore((s) => s.wsLastEvent);
  const mountOp = useStore((s) => s.mountOp);
  const focus = useStore((s) => s.focus);
  const lastAutofocusResult = useStore((s) => s.lastAutofocusResult);
  const guide = useStore((s) => s.guide);
  const status = useStore((s) => s.status);
  const lastCaptureAtMs = useStore((s) => s.lastCaptureAtMs);

  const inputs: IncidentInputs = {
    sequence: {
      state: sequence.state,
      detail: sequence.detail,
      // `SequenceState.hold` is a bare reason string ("clouds"); the incident
      // model wants the reason AND when it started. The engine does not send a
      // hold timestamp, so `since` is honestly absent rather than invented -
      // `sinceLine` renders "just now" instead of a made-up duration.
      hold: sequence.hold ? { reason: sequence.hold } : null,
      end_reason: sequence.end_reason,
    },
    safety: safety?.reading
      ? {
          is_safe: safety.reading.is_safe,
          reason: safety.reading.reason,
          stale: safety.reading.stale,
          ts: safety.reading.ts * 1000,
        }
      : null,
    wsPhase,
    telemetryStale,
    wsLastEvent,
    mountOp,
    focus: focus ? { state: focus.state, message: focus.message } : null,
    lastAutofocusResult: lastAutofocusResult
      ? { ok: lastAutofocusResult.state === "done", failed: lastAutofocusResult.state === "failed" }
      : null,
    guide,
    diskFreeBytes: typeof status?.disk?.free_gb === "number"
      ? status.disk.free_gb * 1e9
      : null,
    cooler: status?.camera?.cooler
      ? {
          on: status.camera.cooler.on,
          at_setpoint: status.camera.cooler.at_target,
          setpoint: status.camera.cooler.target_c ?? undefined,
          temperature: status.camera.temperature ?? undefined,
        }
      : null,
    lastCaptureAtMs,
    expectedFrameS: sequence.progress?.current_exposure_s ?? null,
  };

  return deriveIncidents(inputs, nowMs);
}
