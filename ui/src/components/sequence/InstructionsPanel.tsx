// InstructionsPanel.tsx — conditional-sequencer editor (PRO-3). A THIN render
// over the tested pure helpers in lib/instructions.ts (defaultInstruction /
// describeInstruction / validateInstruction / labels). No run-time logic lives
// here — the backend evaluator (sequence/instructions.py) is authoritative; this
// only authors the plan.instructions list. Empty list === byte-identical run.
//
// Honest-disabled (§11.8): a viewer without control.mount sees the editor but it
// is dimmed + inert (pointer-events-none) + aria-disabled + a title explaining
// what access is needed — NEVER the native `disabled` attribute.

import { useState } from "react";
import type { ActionKind, Instruction, SequencePlan, TriggerKind } from "../../types";
import {
  ACTION_LABELS, TRIGGER_LABELS, defaultInstruction, describeInstruction,
  triggerNeedsThreshold, triggerNeedsTime, validateInstruction,
} from "../../lib/instructions";
import { accessPhrase } from "../../lib/caps";
import { IconButton, InfoDot, Panel, Toggle } from "../ui";

const TRIGGERS = Object.keys(TRIGGER_LABELS) as TriggerKind[];
const ACTIONS = Object.keys(ACTION_LABELS) as ActionKind[];
const LEVELS = ["info", "warning", "error"] as const;

function num(v: string, fallback: number): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

/** One rule row. Holds only its own "advanced" disclosure state; the rule itself
 *  is controlled by the parent (plan SSOT). */
function RuleRow({ ins, idx, targetNames, onPatch, onRemove }: {
  ins: Instruction;
  idx: number;
  targetNames: string[];
  onPatch: (patch: Partial<Instruction>) => void;
  onRemove: () => void;
}) {
  const [adv, setAdv] = useState(false);
  const problems = validateInstruction(ins);

  return (
    <div className="border-t border-line/60 pt-2 flex flex-col gap-2 text-[11px]">
      {/* --- trigger + (threshold | time) + action --- */}
      <div className="flex items-center gap-2 flex-wrap">
        <select
          className="field !py-1"
          aria-label={`Rule ${idx + 1} trigger`}
          value={ins.trigger}
          onChange={(e) => onPatch({ trigger: e.target.value as TriggerKind })}
        >
          {TRIGGERS.map((t) => (
            <option key={t} value={t}>{TRIGGER_LABELS[t]}</option>
          ))}
        </select>

        {triggerNeedsThreshold(ins.trigger) && (
          <input
            className="field !w-20 !py-1"
            type="number"
            step="0.1"
            min="0"
            aria-label={`Rule ${idx + 1} threshold`}
            value={ins.threshold}
            onChange={(e) => onPatch({ threshold: Math.max(0, num(e.target.value, ins.threshold)) })}
          />
        )}
        {triggerNeedsTime(ins.trigger) && (
          <input
            className="field !w-20 !py-1 mono text-center"
            placeholder="HH:MM"
            inputMode="numeric"
            aria-label={`Rule ${idx + 1} time`}
            value={ins.at_time ?? ""}
            onChange={(e) => onPatch({ at_time: e.target.value || null })}
          />
        )}

        <span aria-hidden className="text-dim">→</span>

        <select
          className="field !py-1"
          aria-label={`Rule ${idx + 1} action`}
          value={ins.action}
          onChange={(e) => onPatch({ action: e.target.value as ActionKind })}
        >
          {ACTIONS.map((a) => (
            <option key={a} value={a}>{ACTION_LABELS[a]}</option>
          ))}
        </select>

        <span className="flex-1" />
        <Toggle
          checked={ins.enabled}
          onChange={(v) => onPatch({ enabled: v })}
          label={`Enable rule ${idx + 1}`}
        />
        <IconButton
          icon="trash"
          tone="danger"
          size={14}
          label={`Delete rule ${idx + 1}`}
          onClick={onRemove}
        />
      </div>

      {/* --- notify message + level --- */}
      {ins.action === "notify" && (
        <div className="flex items-center gap-2 flex-wrap">
          <input
            className="field !py-1 flex-1 min-w-[8rem]"
            placeholder="notification message"
            aria-label={`Rule ${idx + 1} message`}
            value={ins.message}
            onChange={(e) => onPatch({ message: e.target.value })}
          />
          <select
            className="field !py-1"
            aria-label={`Rule ${idx + 1} level`}
            value={ins.level}
            onChange={(e) => onPatch({ level: e.target.value as Instruction["level"] })}
          >
            {LEVELS.map((l) => (
              <option key={l} value={l}>{l}</option>
            ))}
          </select>
        </div>
      )}
      {/* abort reuses `message` as its reason */}
      {ins.action === "abort" && (
        <input
          className="field !py-1"
          placeholder="abort reason (optional)"
          aria-label={`Rule ${idx + 1} abort reason`}
          value={ins.message}
          onChange={(e) => onPatch({ message: e.target.value })}
        />
      )}

      {/* --- advanced: once / cooldown / only_target --- */}
      <button
        type="button"
        aria-expanded={adv}
        onClick={() => setAdv((v) => !v)}
        className="self-start tap min-h-[32px] inline-flex items-center gap-1 text-dim hover:text-accent transition-colors cursor-pointer"
      >
        <span aria-hidden className="text-sm leading-none">{adv ? "▾" : "▸"}</span>
        advanced
      </button>
      {adv && (
        <div className="flex items-center gap-3 flex-wrap pl-2">
          <label className="inline-flex items-center gap-2">
            <span className="text-dim">once per run</span>
            <Toggle
              checked={ins.once}
              onChange={(v) => onPatch({ once: v })}
              label={`Rule ${idx + 1} fire once per run`}
            />
          </label>
          <label className="inline-flex items-center gap-2">
            <span className="text-dim inline-flex items-center gap-1">
              cooldown s
              <InfoDot label="About cooldown"
                content="Minimum seconds between fires of this rule (0 = every frame boundary). Throttles e.g. a user's explicit refocus so it can't fire on every sub." />
            </span>
            <input
              className="field !w-16 !py-1"
              type="number"
              min="0"
              aria-label={`Rule ${idx + 1} cooldown seconds`}
              value={ins.cooldown_s}
              onChange={(e) => onPatch({ cooldown_s: Math.max(0, num(e.target.value, ins.cooldown_s)) })}
            />
          </label>
          <label className="inline-flex items-center gap-2">
            <span className="text-dim">only target</span>
            <select
              className="field !py-1"
              aria-label={`Rule ${idx + 1} only target`}
              value={ins.only_target ?? ""}
              onChange={(e) => onPatch({ only_target: e.target.value || null })}
            >
              <option value="">(any)</option>
              {targetNames.map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
          </label>
        </div>
      )}

      {/* --- live summary + validation --- */}
      <div className="mono text-[10px] text-dim truncate">
        {describeInstruction(ins, targetNames)}
      </div>
      {problems.length > 0 && (
        <ul className="text-[10px] text-warn list-disc pl-4">
          {problems.map((p) => <li key={p}>{p}</li>)}
        </ul>
      )}
    </div>
  );
}

export default function InstructionsPanel({ plan, setPlan, canWrite }: {
  plan: SequencePlan;
  setPlan: (p: SequencePlan) => void;
  canWrite: boolean;
}) {
  const [open, setOpen] = useState(false);
  const instructions = plan.instructions ?? [];
  const targetNames = plan.targets.map((t) => t.name).filter(Boolean);

  const patchAt = (idx: number, patch: Partial<Instruction>) =>
    setPlan({
      ...plan,
      instructions: instructions.map((ins, i) => (i === idx ? { ...ins, ...patch } : ins)),
    });
  const removeAt = (idx: number) =>
    setPlan({ ...plan, instructions: instructions.filter((_, i) => i !== idx) });
  const add = () =>
    setPlan({ ...plan, instructions: [...instructions, defaultInstruction()] });

  const lockTitle = canWrite ? undefined : `Editing instructions needs ${accessPhrase("control.mount")}.`;

  return (
    <Panel
      title="Instructions"
      right={
        <span className="text-[11px] text-dim inline-flex items-center gap-1">
          <InfoDot label="About sequence instructions"
            content="Optional when-this-then-that rules layered on the fixed plan. Each fires an existing engine action (notify / pause / refocus / dither / abort). No rules = the plan runs exactly as before." />
          {instructions.length > 0 ? `${instructions.length} rule${instructions.length === 1 ? "" : "s"}` : "none"}
        </span>
      }
    >
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="w-full tap min-h-[44px] flex items-center gap-2 text-left text-[11px] text-dim hover:text-accent transition-colors cursor-pointer"
      >
        <span aria-hidden className="text-accent">⚙</span>
        <span className="truncate">
          {instructions.length === 0
            ? "Add conditional rules (optional)"
            : instructions.map((i) => describeInstruction(i, targetNames)).join("  ·  ")}
        </span>
        <span className="flex-1" />
        <span aria-hidden className="text-sm leading-none">{open ? "▾" : "▸"}</span>
      </button>

      {open && (
        <div
          className={`mt-2 flex flex-col gap-3 ${canWrite ? "" : "opacity-40 pointer-events-none select-none"}`}
          aria-disabled={canWrite ? undefined : true}
          title={lockTitle}
          data-readonly={canWrite ? undefined : "viewer"}
        >
          {instructions.map((ins, idx) => (
            <RuleRow
              key={ins.id ?? idx}
              ins={ins}
              idx={idx}
              targetNames={targetNames}
              onPatch={(patch) => patchAt(idx, patch)}
              onRemove={() => removeAt(idx)}
            />
          ))}
          <button
            type="button"
            onClick={add}
            className="btn btn-touch self-start text-xs"
            aria-label="Add rule"
          >
            + Add rule
          </button>
        </div>
      )}
    </Panel>
  );
}
