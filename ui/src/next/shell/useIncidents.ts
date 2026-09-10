// useIncidents.ts - the ONE place the shell folds store slices into
// `next/lib/incidents.ts`'s pure `IncidentInputs`.
//
// Four parts of the chrome need the same answer - the amber banner on every
// other hub, the pulsing dot on the Session tab, the same dot on the rail, and
// the sub-nav's NOW chip - and if each assembled its own inputs they would
// drift, which is exactly how a tab dot ends up pulsing for an incident the
// banner has stopped announcing. The derivation itself stays pure and tested in
// `lib/incidents.ts`; this file is only the wiring, and it holds no state.
//
// A FOLD THAT DROPS A FIELD SILENTLY TURNS OFF AN INCIDENT. Three kinds were
// unreachable from here until this fold carried their inputs: `cooler` needed
// the gate, which is `at_target` plus the engine's own `cooling to ...` detail;
// `guide` needed the guider's narration phase, which is the only place the
// engine says "lost"; `stall` needed the exposure the frame counter is measured
// against. Nothing said so - the tab dot simply never lit for them.

import { useMemo } from "react";
import { useShallow } from "zustand/react/shallow";
import { useStore } from "../../store";
import { deriveIncidents, type Incident, type IncidentInputs, type IncidentLogLine } from "../lib";

/** The rig's log ring, flattened to what the incident model reads. `LogLine.ts`
 *  is UNIX SECONDS on the RIG's clock (`events.py`), so the multiply here is
 *  what makes an incident's age the rig's answer rather than this phone's. */
function foldLogs(logs: { data: { level: string; message: string; source: string }; ts: number }[]): IncidentLogLine[] {
  return logs.map((l) => ({
    source: l.data.source,
    message: l.data.message,
    level: l.data.level,
    tsMs: l.ts * 1000,
  }));
}

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
  // The log ring is replaced on every line; `useShallow` keeps a render that
  // changes nothing this fold reads from re-deriving nine incidents.
  const logs = useStore(useShallow((s) => s.logs));

  const inputs: IncidentInputs = useMemo(() => ({
    sequence: {
      state: sequence.state,
      detail: sequence.detail,
      // `SequenceState.hold` is a bare reason string ("clouds"); the incident
      // model wants the reason AND when it started. The engine does not send a
      // hold timestamp, so `since` is honestly absent and the model falls back
      // to the rig's own "holding for clear sky" log line.
      hold: sequence.hold ? { reason: sequence.hold } : null,
      end_reason: sequence.end_reason,
      // The engine's own sky verdict. It is published beside `state` on EVERY
      // publish, which is what makes it survive a routine `running` publish
      // overwriting a `holding` state.
      sky: sequence.sky ?? null,
    },
    safety: safety?.reading
      ? {
          is_safe: safety.reading.is_safe,
          reason: safety.reading.reason,
          source: safety.reading.source,
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
    // `GuideStats.phase` is the engine's own narration phase; "lost" is its word
    // for a lost star. `guiding` alone is not: the guider is legitimately
    // stopped through every slew and every autofocus.
    guide: guide ? { guiding: guide.guiding, phase: guide.phase } : null,
    // The SERVER's thresholds, not a second opinion computed here: `low` and
    // `critical` are decided where the disk actually is.
    disk: status?.disk ?? null,
    cooler: status?.camera?.cooler
      ? {
          on: status.camera.cooler.on,
          at_target: status.camera.cooler.at_target,
          target_c: status.camera.cooler.target_c,
          temperature: status.camera.temperature ?? null,
          power: status.camera.cooler.power,
          can_report_power: status.camera.cooler.can_report_power,
        }
      : null,
    logs: foldLogs(logs),
    lastCaptureAtMs,
    expectedFrameS: sequence.progress?.current_exposure_s ?? null,
  }), [
    sequence, safety, wsPhase, telemetryStale, wsLastEvent, mountOp, focus,
    lastAutofocusResult, guide, status, logs, lastCaptureAtMs,
  ]);

  return deriveIncidents(inputs, nowMs);
}
