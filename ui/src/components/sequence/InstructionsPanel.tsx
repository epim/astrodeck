// InstructionsPanel.tsx — conditional-sequencer editor (PRO-3). A THIN render
// over the tested pure helpers in lib/instructions.ts (defaultInstruction /
// describeInstruction / validateInstruction / labels). No run-time logic lives
// here — the backend evaluator (sequence/instructions.py) is authoritative; this
// only authors the plan.instructions list. Empty list === byte-identical run.
//
// Honest-disabled (§11.8): a viewer without control.mount sees the editor but it
// is dimmed + inert (pointer-events-none) + aria-disabled + a title explaining
// what access is needed — NEVER the native `disabled` attribute.

import { useMemo, useState } from "react";
import type {
  ActionKind, Condition, Instruction, Predicate, PredicateKind, SequencePlan,
  TriggerKind,
} from "../../types";
import {
  ACTION_LABELS, MAX_TERMS, MIN_TERMS, PREDICATE_LABELS, TRIGGER_LABELS,
  actionChangePatch, actionNeedsTarget, defaultInstruction, defaultPredicate,
  describeInstruction, predicateNeedsThreshold, predicateNeedsTime, toCompound,
  toFlat, triggerNeedsThreshold, triggerNeedsTime, validateInstruction,
} from "../../lib/instructions";
import {
  SIM_CAVEAT, defaultSnapshot, simulateInstructions, type SimSnapshot,
} from "../../lib/instructionSim";
import { accessPhrase } from "../../lib/caps";
import { IconButton, InfoDot, Panel, Toggle } from "../ui";

const TRIGGERS = Object.keys(TRIGGER_LABELS) as TriggerKind[];
const ACTIONS = Object.keys(ACTION_LABELS) as ActionKind[];
const PREDICATES = Object.keys(PREDICATE_LABELS) as PredicateKind[];
const LEVELS = ["info", "warning", "error"] as const;
const JUMP_HINT = "Target jumps need 2 or more targets.";

function num(v: string, fallback: number): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

/** One leaf term of a compound condition. Thin render; all shape logic lives in
 *  lib/instructions.ts. */
function TermRow({ term, idx, ruleIdx, canRemove, onPatch, onRemove }: {
  term: Predicate;
  idx: number;
  ruleIdx: number;
  canRemove: boolean;
  onPatch: (patch: Partial<Predicate>) => void;
  onRemove: () => void;
}) {
  const tag = `Rule ${ruleIdx + 1} condition ${idx + 1}`;
  return (
    <div className="flex items-center gap-2 flex-wrap">
      <select
        className="field !py-1"
        aria-label={`${tag} kind`}
        value={term.kind}
        onChange={(e) => {
          const kind = e.target.value as PredicateKind;
          onPatch({ ...defaultPredicate(kind), threshold: term.threshold || 1 });
        }}
      >
        {PREDICATES.map((k) => (
          <option key={k} value={k}>{PREDICATE_LABELS[k]}</option>
        ))}
      </select>
      {predicateNeedsThreshold(term.kind) && (
        <input
          className="field !w-20 !py-1"
          type="number"
          step="0.1"
          min="0"
          aria-label={`${tag} threshold`}
          value={term.threshold}
          onChange={(e) => onPatch({ threshold: Math.max(0, num(e.target.value, term.threshold)) })}
        />
      )}
      {predicateNeedsTime(term.kind) && (
        <input
          className="field !w-20 !py-1 mono text-center"
          placeholder="HH:MM"
          inputMode="numeric"
          aria-label={`${tag} time`}
          value={term.at_time ?? ""}
          onChange={(e) => onPatch({ at_time: e.target.value || null })}
        />
      )}
      <span className="flex-1" />
      {canRemove && (
        <IconButton icon="trash" tone="danger" size={12}
          label={`Remove ${tag.toLowerCase()}`} onClick={onRemove} />
      )}
    </div>
  );
}

/** The AND/OR editor. Only ever rendered inside the per-row `advanced`
 *  disclosure, so the novice single-condition path is untouched. */
function CompoundEditor({ when, ruleIdx, onChange, onDrop }: {
  when: Condition;
  ruleIdx: number;
  onChange: (c: Condition) => void;
  onDrop: () => void;
}) {
  const patchTerm = (i: number, patch: Partial<Predicate>) =>
    onChange({ ...when, terms: when.terms.map((t, n) => (n === i ? { ...t, ...patch } : t)) });
  return (
    <div className="flex flex-col gap-2 pl-2 border-l border-line/60">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-dim">match</span>
        <select
          className="field !py-1"
          aria-label={`Rule ${ruleIdx + 1} combine mode`}
          value={when.op}
          onChange={(e) => onChange({ ...when, op: e.target.value as Condition["op"] })}
        >
          <option value="all">all of these (AND)</option>
          <option value="any">any of these (OR)</option>
        </select>
        <span className="flex-1" />
        <button type="button" onClick={onDrop}
          className="tap min-h-[32px] text-dim hover:text-accent transition-colors cursor-pointer">
          back to a single condition
        </button>
      </div>
      {when.terms.map((t, i) => (
        <TermRow
          key={i}
          term={t}
          idx={i}
          ruleIdx={ruleIdx}
          canRemove={when.terms.length > MIN_TERMS}
          onPatch={(patch) => patchTerm(i, patch)}
          onRemove={() => onChange({ ...when, terms: when.terms.filter((_, n) => n !== i) })}
        />
      ))}
      {when.terms.length < MAX_TERMS && (
        <button
          type="button"
          className="btn btn-touch self-start text-[11px]"
          aria-label={`Add condition to rule ${ruleIdx + 1}`}
          onClick={() => onChange({ ...when, terms: [...when.terms, defaultPredicate("hfr_above")] })}
        >
          + condition
        </button>
      )}
    </div>
  );
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
  const when = ins.when ?? null;
  // Honest-disabled (§11.8): jumps are meaningless with fewer than 2 targets, so
  // the options are DIMMED + aria-disabled + explained — never natively disabled.
  const jumpsOk = targetNames.length >= 2;

  return (
    <div className="border-t border-line/60 pt-2 flex flex-col gap-2 text-[11px]">
      {/* --- trigger + (threshold | time) + action --- */}
      <div className="flex items-center gap-2 flex-wrap">
        {when ? (
          <button
            type="button"
            onClick={() => setAdv(true)}
            title="This rule combines several conditions — edit them under advanced."
            className="field !py-1 text-left text-accent cursor-pointer"
          >
            combined conditions ({when.op === "all" ? "AND" : "OR"})
          </button>
        ) : (
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
        )}

        {!when && triggerNeedsThreshold(ins.trigger) && (
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
        {!when && triggerNeedsTime(ins.trigger) && (
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
          title={jumpsOk ? undefined : JUMP_HINT}
          onChange={(e) => {
            const a = e.target.value as ActionKind;
            if (actionNeedsTarget(a) && !jumpsOk) return;   // honest-disabled: inert
            onPatch(actionChangePatch(ins, a, targetNames));
          }}
        >
          {ACTIONS.map((a) => (
            <option
              key={a}
              value={a}
              className={actionNeedsTarget(a) && !jumpsOk ? "opacity-40" : undefined}
              aria-disabled={actionNeedsTarget(a) && !jumpsOk ? true : undefined}
              title={actionNeedsTarget(a) && !jumpsOk ? JUMP_HINT : undefined}
            >
              {ACTION_LABELS[a]}
            </option>
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
      {/* --- jump destination (replaces the message row for run/skip_target) --- */}
      {actionNeedsTarget(ins.action) && (
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-dim">
            {ins.action === "run_target" ? "jump to" : "skip"}
          </span>
          <select
            className="field !py-1"
            aria-label={`Rule ${idx + 1} target to ${ins.action === "run_target" ? "jump to" : "skip"}`}
            value={ins.target_arg ?? ""}
            onChange={(e) => onPatch({ target_arg: e.target.value || null })}
          >
            <option value="">(choose a target)</option>
            {targetNames.map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
          <span className="text-dim text-[10px]">
            Jumps change which target runs next. “Once” is on so a rule can’t loop.
          </span>
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
          {/* --- compound AND/OR (advanced only; novices never see it) --- */}
          <div className="w-full flex flex-col gap-2">
            {when ? (
              <CompoundEditor
                when={when}
                ruleIdx={idx}
                onChange={(c) => onPatch({ when: c })}
                onDrop={() => onPatch({ when: null, ...toFlat(when) })}
              />
            ) : (
              <button
                type="button"
                onClick={() => onPatch({ when: toCompound(ins) })}
                className="self-start tap min-h-[32px] text-dim hover:text-accent transition-colors cursor-pointer"
              >
                + Combine conditions (AND/OR)
              </button>
            )}
          </div>
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

/** Dry run: "what would these rules do right now?". A THIN render over the pure
 *  `simulateInstructions` helper — no evaluation logic lives here. Read-only, so
 *  it stays usable for viewers who cannot edit the plan. */
function DryRunPanel({ rules, targetNames }: {
  rules: Instruction[];
  targetNames: string[];
}) {
  // every control is optional — the defaults render a useful preview instantly.
  const [snap, setSnap] = useState<SimSnapshot>(() => defaultSnapshot(targetNames));
  const outcomes = useMemo(() => simulateInstructions(rules, snap), [rules, snap]);
  const patch = (p: Partial<SimSnapshot>) => setSnap((s) => ({ ...s, ...p }));
  const firing = outcomes.filter((o) => o.wouldFire).length;

  return (
    <div className="mt-2 flex flex-col gap-2 text-[11px] rounded-md border border-line/60 p-2">
      <div className="flex items-center gap-3 flex-wrap">
        <label className="inline-flex items-center gap-2">
          <span className="text-dim w-16">HFR</span>
          <input
            type="range" min="0" max="10" step="0.1"
            aria-label="Preview HFR"
            value={snap.hfr ?? 0}
            onChange={(e) => patch({ hfr: num(e.target.value, 0) })}
          />
          <span className="mono w-8 text-right">{(snap.hfr ?? 0).toFixed(1)}</span>
        </label>
        <label className="inline-flex items-center gap-2">
          <span className="text-dim w-16">guide RMS</span>
          <input
            type="range" min="0" max="5" step="0.05"
            aria-label="Preview guide RMS"
            value={snap.guideRms ?? 0}
            onChange={(e) => patch({ guideRms: num(e.target.value, 0) })}
          />
          <span className="mono w-10 text-right">{(snap.guideRms ?? 0).toFixed(2)}"</span>
        </label>
      </div>
      <div className="flex items-center gap-3 flex-wrap">
        <label className="inline-flex items-center gap-2">
          <span className="text-dim">frame rejected</span>
          <Toggle checked={snap.frameRejected}
            onChange={(v) => patch({ frameRejected: v })}
            label="Preview frame rejected" />
        </label>
        <label className="inline-flex items-center gap-2">
          <span className="text-dim">target complete</span>
          <Toggle checked={snap.targetComplete}
            onChange={(v) => patch({ targetComplete: v })}
            label="Preview target complete" />
        </label>
        <label className="inline-flex items-center gap-2">
          <span className="text-dim">time</span>
          <input
            className="field !w-20 !py-1 mono text-center"
            placeholder="HH:MM"
            inputMode="numeric"
            aria-label="Preview time"
            value={snap.now}
            onChange={(e) => patch({ now: e.target.value })}
          />
        </label>
        <label className="inline-flex items-center gap-2">
          <span className="text-dim">target</span>
          <select
            className="field !py-1"
            aria-label="Preview active target"
            value={snap.activeTarget ?? ""}
            onChange={(e) => patch({ activeTarget: e.target.value || null })}
          >
            <option value="">(none)</option>
            {targetNames.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
      </div>

      <ul className="flex flex-col gap-1">
        {outcomes.map((o) => (
          <li key={o.id}
            className={`flex items-start gap-2 ${o.wouldFire ? "text-good" : "text-dim"}`}>
            <span aria-hidden>{o.wouldFire ? "●" : "○"}</span>
            <span className="flex-1 min-w-0">
              <span className="mono block truncate">{o.summary}</span>
              <span className="text-[10px]">
                {o.wouldFire ? "would fire" : `waiting: ${o.reason}`}
                {o.note ? ` — ${o.note}` : ""}
              </span>
            </span>
          </li>
        ))}
      </ul>
      <div className="text-[10px] text-dim">
        {firing} of {outcomes.length} would fire on this frame. {SIM_CAVEAT}
      </div>
    </div>
  );
}

export default function InstructionsPanel({ plan, setPlan, canWrite }: {
  plan: SequencePlan;
  setPlan: (p: SequencePlan) => void;
  canWrite: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [preview, setPreview] = useState(false);
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
            content="Optional when-this-then-that rules layered on the fixed plan. Each fires an existing engine action (notify / pause / refocus / dither / abort) or jumps the scheduler to another target. Advanced rules can combine conditions with AND/OR. No rules = the plan runs exactly as before." />
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

      {/* --- dry run: the one-tap novice affordance. Outside the write-gated
              block: previewing changes nothing, so viewers get it too. --- */}
      {open && instructions.length > 0 && (
        <>
          <button
            type="button"
            aria-expanded={preview}
            onClick={() => setPreview((v) => !v)}
            className="mt-2 w-full tap min-h-[44px] flex items-center gap-2 text-left text-[11px] text-dim hover:text-accent transition-colors cursor-pointer"
          >
            <span aria-hidden className="text-accent">◎</span>
            <span>Preview what these rules do</span>
            <span className="flex-1" />
            <span aria-hidden className="text-sm leading-none">{preview ? "▾" : "▸"}</span>
          </button>
          {preview && (
            <DryRunPanel rules={instructions} targetNames={targetNames} />
          )}
        </>
      )}
    </Panel>
  );
}
