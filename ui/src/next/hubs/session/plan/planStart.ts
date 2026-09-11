// planStart.ts - the ONE derivation of "can this plan start, and what happens
// when it does".
//
// It exists because two places need it - the checklist card in the body and the
// START button in the sheet's sticky footer - and a second copy is exactly the
// defect this wave is closing everywhere else: a button that prices one thing
// and fires another. `usePreflight` de-dupes its own altitude poller across
// consumers, so calling this hook twice costs one fetch loop, not two.
//
// `force` IS NOT AN OVERRIDE. It is true only when a LOW-HORIZON warning is
// present, and it exists because the server's horizon guard would otherwise
// 409 a run the operator has already accepted on this screen. Every other
// blocker still blocks.
//
// THE LOCK SENTENCE NAMES THE MISSING THING. "add targets and steps first" was
// true of an empty plan AND of a plan whose every step had been zeroed, which
// are different repairs; each branch below says which one it is. The sentences
// start lower-case because `LockNote` prints `Read-only - <reason>` and the
// honest-press toast quotes the same string.

import { api } from "../../../../api";
import { usePreflight, type PreflightVerdict } from "../../../../components/PreflightStrip";
import { accessPhrase, useCanControlMount } from "../../../../lib/caps";
import { useSeq, useStore } from "../../../../store";
import type { CheckItem } from "../../../../types";
import { isSequenceLive, planFrames, runningPlanName } from "./planModel";

export interface PlanStart {
  items: CheckItem[];
  verdict: PreflightVerdict;
  /** Null when START may fire. */
  startLockedReason: string | null;
  /** True only when the block is a MISSING CAPABILITY - the one case that earns
   *  the VIEW ONLY badge. A blocked pre-flight is not a permissions problem and
   *  dressing it as one would send the user to the wrong repair. */
  viewOnly: boolean;
  start: () => void;
}

export function usePlanStart(): PlanStart {
  const plan = useStore((s) => s.plan);
  const seq = useSeq();
  const canRun = useCanControlMount();
  const toast = useStore((s) => s.enqueueToast);
  const { items, verdict } = usePreflight(plan);

  const live = isSequenceLive(seq.state);
  const planName = runningPlanName(seq.plan_name, plan.name);

  // One blocker gets its DETAIL ("M31 (Ha): 5000s", "no telescope connected") -
  // that string is the whole repair. Several get named without details, because
  // a sentence nobody finishes reading is the same as no sentence.
  const blocked = items.filter((i) => i.status === "blocked");
  const blockedNames = blocked.length === 1
    ? blocked[0].label + (blocked[0].detail?.value ? ` - ${blocked[0].detail.value}` : "")
    : blocked.slice(0, 3).map((i) => i.label).join(", ")
      + (blocked.length > 3 ? ` and ${blocked.length - 3} more` : "");

  const frames = planFrames(plan);
  const startLockedReason: string | null =
    !canRun ? `starting a sequence needs ${accessPhrase("control.mount")}`
      : live ? `"${planName}" is running - stop it before starting another run`
        : plan.targets.length === 0
          ? "this plan has no targets - search the catalog to add one"
          : frames === 0
            ? "every target here has no steps - add one with ADD STEP"
            : verdict === "blocked"
              ? `pre-flight is blocked on ${blockedNames || "a check above"}`
              : null;

  const force = items.some((i) => i.id === "horizon" && i.status === "warn");

  const start = () => {
    void api.post("/api/sequence/start", { ...plan, force }).then(
      () => { toast({ level: "success", title: `Started "${plan.name}"` }); },
      (e: Error) => {
        toast({ level: "error", title: "The run did not start", detail: e.message });
      },
    );
  };

  return { items, verdict, startLockedReason, viewOnly: !canRun, start };
}
