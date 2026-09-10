// ArmedRules.tsx - what will act on its own tonight, as chips.
//
// Every one of these is DERIVED from the plan the engine is running plus the
// rig's config. None of them is a fixture: the prototype's list is four fixed
// strings, and a chip that says "safety armed" over a rig with no safety
// monitor is the exact kind of reassurance this project has already been bitten
// by (a run that started unattended in bad weather).
//
// So `safety armed` has an opposite: `no safety monitor`, in bad tone, not
// amber - because it is not a rule that is armed, it is a rule that is absent.
//
// THE INSTRUCTION CHIPS ARE THE PHONE'S COPY of the when/then rules panel.
// Their words come from `lib/instructions.ts`'s own label maps; there is no
// `describeInstruction()` and inventing one would give the same rule two
// different names in two places. Editing them stays in the plan editor.

import type { JSX } from "react";

import {
  ACTION_CONSEQUENCE, ACTION_LABELS, isDestructiveAction, TRIGGER_LABELS,
} from "../../../../lib/instructions";
import { usePlan, useSafety, useStore } from "../../../../store";
import type { Instruction, SequencePlan } from "../../../../types";
import { Pill } from "../../../ui";
import { useActiveSession } from "./sessionData";

export interface ArmedChip {
  key: string;
  text: string;
  tone: "warn" | "bad";
  /** Held/long-pressed detail, when the rule has a consequence worth naming. */
  title?: string;
}

/** The chip list, pure, so the derivation can be read without a DOM. */
export function armedChips(
  plan: SequencePlan | null,
  opts: {
    weatherEnabled: boolean;
    weatherIgnored: boolean;
    safetyConnected: boolean;
  },
): ArmedChip[] {
  const out: ArmedChip[] = [];
  if (!plan) return out;

  if (opts.weatherEnabled) {
    out.push(opts.weatherIgnored
      ? { key: "cloud", text: "cloud hold overridden tonight", tone: "bad" }
      : { key: "cloud", text: "cloud hold armed", tone: "warn" });
  }
  if ((plan.hfr_reject_factor ?? 0) > 0) {
    out.push({ key: "hfr", text: `HFR watchdog ${plan.hfr_reject_factor!.toFixed(2)}x`, tone: "warn" });
  }
  if ((plan.min_stars ?? 0) > 0) {
    out.push({ key: "stars", text: `min ${plan.min_stars} stars`, tone: "warn" });
  }
  if ((plan.max_guide_rms ?? 0) > 0) {
    out.push({ key: "rms", text: `max guide ${plan.max_guide_rms}"`, tone: "warn" });
  }
  out.push(opts.safetyConnected
    ? { key: "safety", text: "safety armed", tone: "warn" }
    : { key: "safety", text: "no safety monitor", tone: "bad" });
  if (plan.cool_to != null) {
    out.push({ key: "cooler", text: `cooler gate ${plan.cool_to}°C`, tone: "warn" });
  }

  for (const i of plan.instructions ?? []) {
    if (i.enabled === false) continue;
    out.push(instructionChip(i));
  }
  return out;
}

/** One instruction as a chip. A compound condition (`when`) counts its terms
 *  rather than naming one of them, because naming the first would present an
 *  AND of four conditions as a single trigger. */
export function instructionChip(i: Instruction): ArmedChip {
  const action = ACTION_LABELS[i.action];
  const terms = i.when?.terms?.length ?? 0;
  const lead = terms > 1 ? `${terms} conditions` : TRIGGER_LABELS[i.trigger];
  return {
    key: i.id ?? `${i.trigger}:${i.action}`,
    text: `${lead} -> ${action}`,
    tone: isDestructiveAction(i.action) ? "bad" : "warn",
    title: ACTION_CONSEQUENCE[i.action],
  };
}

export function ArmedRules(): JSX.Element | null {
  const { session } = useActiveSession();
  const localPlan = usePlan();
  const safety = useSafety();
  const weatherEnabled = useStore((s) => s.weather?.enabled ?? s.config?.weather?.enabled ?? false);
  const weatherIgnored = useStore((s) => s.weather?.ignore_tonight === true);

  // The RUNNING copy first: the engine executes the plan it took at Run, and a
  // chip off the editor's copy would describe rules the night is not using.
  const plan = session?.plan ?? localPlan;
  const chips = armedChips(plan, {
    weatherEnabled,
    weatherIgnored,
    safetyConnected: safety?.connected === true,
  });
  if (chips.length === 0) return null;

  return (
    <div
      data-testid="now-armed-rules"
      style={{ display: "flex", gap: 6, flexWrap: "wrap" }}
    >
      {/* `Pill`, not `Chip`: these are read-outs, and `Chip` always renders a
          button - a row of eight tab stops that do nothing is worse than the
          four pixels of height it would buy. */}
      {chips.map((c) => (
        <Pill key={c.key} dashed tone={c.tone} ariaLabel={c.title ? `${c.text}. ${c.title}` : undefined}>
          {c.text}
        </Pill>
      ))}
    </div>
  );
}
