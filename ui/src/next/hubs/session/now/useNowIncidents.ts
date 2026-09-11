// useNowIncidents.ts - the ONE incident fold this hub renders from.
//
// The shell already folds the store into `next/lib/incidents.ts`'s pure inputs
// (`shell/useIncidents.ts`), and this reuses it rather than writing a second
// fold: two folds is how a tab dot ends up pulsing for an incident the card has
// stopped showing. What this adds on top is `refineIncident`.
//
// WHAT `refineIncident` IS STILL FOR, AFTER 2026-09-10 (INT-A).
//
// It was written as a COMPENSATION: `next/lib/incidents.ts` was another task's
// file and, as shipped, it promised four things the engine does not do
// ("Resumes after 3 clear frames", "Re-arms 30 minutes after the sensor reads
// dry", "falls back to the relay after 30 seconds", "3 fails, then a blind
// solve") and offered five buttons with no server verb behind them. The
// integration task corrected the lib itself: the promises are gone, the
// verbless actions are gone, safety no longer claims to resolve itself, and the
// titles for SAFETY TRIP, DISK LOW/CRITICAL and COOLER CANNOT HOLD now come out
// of the lib already composed from the live fields.
//
// So most of what `refineIncident` re-states is now a re-statement of what the
// lib already said, and deleting those branches is a safe follow-up in the file
// that owns them (`incidentActions.ts`, not this one). FIVE THINGS ARE NOT
// REDUNDANT and must survive that deletion, because each needs something the
// pure lib has no access to:
//
//   1. af    - the actual last-good focuser POSITION (`lastAutofocusResult`),
//              which the lib is not given.
//   2. link  - `telemetryStaleNotice(age)` from `lib/telemetry.ts`, which states
//              the age instead of a vague "catching up" that reads the same at
//              20 s and at 2 hours.
//   3. stall - the name of the configured escalation policy, from `config`.
//   4. cooler- the `ACCEPT <n>°C` label, whose number must be the one the POST
//              body will carry.
//   5. every kind - the ACTION table: the two honest additions this hub owns
//              (`STOP GUIDING` in place of the un-fireable CONTINUE UNGUIDED,
//              and `CONNECTION` at tablet width) plus the cloud card's
//              IGNORE/UNDO label swap.
//
// The phase pill and the incident card read THIS, so they cannot disagree - and
// the LINK LOST overlay on the live stack reads the same predicate, which is
// what the plan requires of it.

import { useEffect, useState } from "react";
import { useBreakpoint } from "../../../breakpoint";
import { useIncidents } from "../../../shell/useIncidents";
import type { Incident } from "../../../lib";
import {
  useConfig, useLastAutofocusResult, useSafety, useSeq, useStore,
  useTelemetryStale, useWsPhase,
} from "../../../../store";
import { refineIncident, type RefineContext } from "./incidentActions";

/** A slow clock. 10 s, not 1 s: the incident lines are minutes-resolution and a
 *  per-second re-render of this tree is a cost paid on a phone battery all
 *  night. The countdowns that DO need a second own their own tick. */
export function useNowMs(everyMs = 10_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), everyMs);
    return () => clearInterval(t);
  }, [everyMs]);
  return now;
}

/** The suggested warmer setpoint for the cooler gate's ACCEPT button.
 *
 *  One degree above where the sensor actually is, rounded up: the button's
 *  LABEL carries this number and so does the body it posts, so they can never
 *  say different things. A fixed "-5C" from the prototype would be a lie on a
 *  different night. Clamped to a physically sane band because no camera on this
 *  status payload reports its own setpoint limits. */
export function suggestedSetpointC(temperatureC: number | null | undefined): number | null {
  if (typeof temperatureC !== "number" || !Number.isFinite(temperatureC)) return null;
  return Math.min(20, Math.max(-40, Math.ceil(temperatureC + 1)));
}

export interface NowIncidents {
  incidents: Incident[];
  nowMs: number;
  /** Kinds the user has pressed WAIT / ACKNOWLEDGE / CONTINUE on. They stay in
   *  the list - the condition has not gone away - but the card drops to the
   *  "N more" row rather than holding the top of the screen. */
  dismissed: Record<string, true>;
  dismiss: (kind: string) => void;
  refineCtx: RefineContext;
}

export function useNowIncidents(): NowIncidents {
  const nowMs = useNowMs();
  const rawIncidents = useIncidents(nowMs);

  const seq = useSeq();
  const status = useStore((s) => s.status);
  const safety = useSafety();
  const focus = useStore((s) => s.focus);
  const lastAf = useLastAutofocusResult();
  const mountOp = useStore((s) => s.mountOp);
  const telemetryStale = useTelemetryStale();
  const wsPhase = useWsPhase();
  const wsLastEvent = useStore((s) => s.wsLastEvent);
  const lastCaptureAtMs = useStore((s) => s.lastCaptureAtMs);
  const weatherIgnored = useStore((s) => s.weather?.ignore_tonight === true);
  const config = useConfig();
  const bp = useBreakpoint();

  const [dismissed, setDismissed] = useState<Record<string, true>>({});

  const cfg = config as unknown as {
    escalation?: { on_no_progress?: string };
    safety?: { on_unsafe?: string };
  } | null;

  const refineCtx: RefineContext = {
    seq,
    status,
    safety,
    focus,
    lastAutofocusPosition: lastAf?.best?.position ?? null,
    mountOp,
    telemetryStale,
    wsPhase,
    wsLastEvent,
    lastCaptureAtMs,
    nowMs,
    weatherIgnored,
    escalation: cfg?.escalation?.on_no_progress ?? cfg?.safety?.on_unsafe ?? null,
    wide: bp !== "phone",
    coolerTargetC: suggestedSetpointC(status?.camera?.temperature),
  };

  const incidents = rawIncidents.map((i) => refineIncident(i, refineCtx));

  return {
    incidents,
    nowMs,
    dismissed,
    dismiss: (kind: string) => setDismissed((d) => ({ ...d, [kind]: true })),
    refineCtx,
  };
}
