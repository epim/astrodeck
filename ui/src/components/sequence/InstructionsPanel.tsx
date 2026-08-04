// InstructionsPanel.tsx — conditional-sequencer editor (PRO-3). A THIN render
// over the tested pure helpers in lib/instructions.ts (defaultInstruction /
// describeInstruction / validateInstruction / labels). No run-time logic lives
// here — the backend evaluator (sequence/instructions.py) is authoritative; this
// only authors the plan.instructions list. Empty list === byte-identical run.
//
// Honest-disabled (§11.8): a viewer without control.mount sees the editor but it
// is dimmed + inert (pointer-events-none) + aria-disabled via the shared
// `lockedProps`, with the reason as VISIBLE text via `LockedNote` — NEVER the
// native `disabled` attribute, and never `title=` alone (it never fires on touch).

import { useMemo, useState } from "react";
import type {
  ActionKind, Condition, Instruction, Predicate, PredicateKind, SequencePlan,
  TriggerKind,
} from "../../types";
import {
  ACTION_CONSEQUENCE, ACTION_GROUPS, ACTION_LABELS, MAX_TERMS, MIN_TERMS,
  PREDICATE_LABELS, PREDICATE_OF, THRESHOLD_HINT, TRIGGER_LABELS,
  actionChangePatch, actionNeedsTarget, defaultInstruction, defaultPredicate,
  describeInstruction, predicateChangePatch,
  predicateNeedsThreshold, predicateNeedsTime, toCompound, toFlat,
  triggerChangePatch, triggerNeedsThreshold, triggerNeedsTime,
  validateInstruction, type DescribeOpts,
} from "../../lib/instructions";
import {
  SIM_CAVEAT, defaultSnapshot, simulateInstructions, type SimSnapshot,
} from "../../lib/instructionSim";
import { accessPhrase } from "../../lib/caps";
import { useGuideRms } from "../../store";
import { Icon } from "../icons";
import { IconButton, InfoDot, LockedNote, Panel, Toggle, lockedProps } from "../ui";

const TRIGGERS = Object.keys(TRIGGER_LABELS) as TriggerKind[];
const PREDICATES = Object.keys(PREDICATE_LABELS) as PredicateKind[];
const LEVELS = ["info", "warning", "error"] as const;
const JUMP_HINT = "Target jumps need 2 or more targets.";

/** Copy for the collapsed row when the plan has no rules at all. */
export const NO_INSTRUCTIONS_SUMMARY = "Add conditional rules (optional)";

/** Collapsed summary of the rule list: the FIRST rule in full plus a "+N more"
 *  count. The previous summary joined EVERY rule with " · " into one string that
 *  CSS then truncated, so a 4-rule plan rendered as half of rule 1 and no hint
 *  that three others existed — the count was invisible and the visible text was a
 *  sentence fragment. First-plus-count keeps one complete, readable rule and
 *  states the remainder honestly; the full list is one tap away (this row is the
 *  disclosure's own trigger, so expanding it shows every rule unchanged).
 *
 *  Pure: takes the already-rendered descriptions, so it is testable without
 *  React, a store, or a plan fixture. */
export function instructionsSummary(descriptions: readonly string[]): string {
  if (descriptions.length === 0) return NO_INSTRUCTIONS_SUMMARY;
  const [first, ...rest] = descriptions;
  return rest.length === 0 ? first : `${first}  ·  +${rest.length} more`;
}

/** Threshold placeholder/hint for whichever metric this trigger reads. */
function thresholdHint(t: TriggerKind): string | null {
  const k = PREDICATE_OF[t];
  return k === "hfr_above" || k === "guide_rms_above" ? THRESHOLD_HINT[k] : null;
}

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
  const hint = term.kind === "hfr_above" || term.kind === "guide_rms_above"
    ? THRESHOLD_HINT[term.kind] : null;
  return (
    <div className="flex items-center gap-2 flex-wrap">
      <select
        className="field !py-1"
        aria-label={`${tag} kind`}
        value={term.kind}
        onChange={(e) => onPatch(predicateChangePatch(term, e.target.value as PredicateKind))}
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
          title={hint ?? undefined}
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
function RuleRow({ ins, idx, targetNames, descOpts, onPatch, onRemove }: {
  ins: Instruction;
  idx: number;
  targetNames: string[];
  descOpts: DescribeOpts;
  onPatch: (patch: Partial<Instruction>) => void;
  onRemove: () => void;
}) {
  const [adv, setAdv] = useState(false);
  const problems = validateInstruction(ins);
  const when = ins.when ?? null;
  // Jumps are meaningless with fewer than 2 targets. Honest-disabled means the
  // user is never offered a control that silently does nothing — and a styled/
  // aria-disabled <option> is exactly that, because browsers IGNORE both inside
  // a closed <select> (and title never renders on an option at all). So the
  // options are REMOVED from the list and a persistent note says why; the
  // onChange guard below stays as defence in depth.
  const jumpsOk = targetNames.length >= 2;
  const groups = ACTION_GROUPS
    .map((g) => ({
      ...g,
      // a rule authored earlier (when there WERE 2 targets) keeps its own option
      // so the select never renders a value it doesn't offer.
      actions: g.actions.filter(
        (a) => jumpsOk || !actionNeedsTarget(a) || a === ins.action),
    }))
    .filter((g) => g.actions.length > 0);
  const consequence = ACTION_CONSEQUENCE[ins.action];

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
            // Seed whatever the new trigger needs (threshold / clock) so the
            // author never lands straight on a red validation error.
            onChange={(e) => onPatch(triggerChangePatch(ins, e.target.value as TriggerKind))}
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
            title={thresholdHint(ins.trigger) ?? undefined}
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
          onChange={(e) => {
            const a = e.target.value as ActionKind;
            if (actionNeedsTarget(a) && !jumpsOk) return;   // defence in depth
            onPatch(actionChangePatch(ins, a, targetNames));
          }}
        >
          {groups.map((g) => (
            <optgroup key={g.label} label={g.label}>
              {g.actions.map((a) => (
                <option key={a} value={a}>{ACTION_LABELS[a]}</option>
              ))}
            </optgroup>
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

      {/* Visible hint, not only title=: title never fires on touch, and "4.0"
          means nothing without knowing what normal looks like. */}
      {!when && thresholdHint(ins.trigger) && (
        <p className="text-[10px] text-dim">{thresholdHint(ins.trigger)}</p>
      )}
      {/* Persistent note instead of a picker entry the browser would let them
          choose anyway (see the honest-disabled comment above). */}
      {!jumpsOk && (
        <p className="text-[10px] text-dim flex items-start gap-1">
          <span aria-hidden>·</span>
          <span>{JUMP_HINT} Add another target to this plan to jump between them.</span>
        </p>
      )}
      {/* What this action COSTS, in warn tone, the moment it is selected. */}
      {consequence && (
        <p className="text-[10px] text-warn flex items-start gap-1.5 leading-snug">
          <Icon name="alert" size={11} className="shrink-0 mt-px" />
          <span>{consequence}</span>
        </p>
      )}

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
            {ins.action === "run_target" ? "switch to" : "drop"}
          </span>
          <select
            className="field !py-1"
            aria-label={`Rule ${idx + 1} target to ${ins.action === "run_target" ? "switch to" : "drop"}`}
            value={ins.target_arg ?? ""}
            onChange={(e) => onPatch({ target_arg: e.target.value || null })}
          >
            <option value="">(choose a target)</option>
            {targetNames.map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
          <span className="text-dim text-[10px]">
            “Once” is on so a rule can’t loop.
          </span>
        </div>
      )}
      {/* abort reuses `message` as its reason */}
      {ins.action === "abort" && (
        <input
          className="field !py-1"
          placeholder="why the session stopped (optional — goes in the report)"
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
        {describeInstruction(ins, targetNames, descOpts)}
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
function DryRunPanel({ rules, targetNames, descOpts }: {
  rules: Instruction[];
  targetNames: string[];
  descOpts: DescribeOpts;
}) {
  // every control is optional — the defaults render a useful preview instantly.
  // `now` is seeded from the REAL clock (a hardcoded 22:00 made every time-based
  // rule preview wrong for anyone not imaging at 10pm).
  const [snap, setSnap] = useState<SimSnapshot>(() => defaultSnapshot(targetNames));
  const outcomes = useMemo(
    () => simulateInstructions(rules, snap, descOpts), [rules, snap, descOpts]);
  const patch = (p: Partial<SimSnapshot>) => setSnap((s) => ({ ...s, ...p }));
  const firing = outcomes.filter((o) => o.wouldFire).length;
  const rmsUnit = descOpts.rmsArcsec === false ? " px" : "\"";

  return (
    <div className="mt-2 flex flex-col gap-2 text-[11px] rounded-md border border-line/60 p-2">
      <div className="flex items-center gap-3 flex-wrap">
        <label className="inline-flex items-center gap-2">
          <span className="text-dim w-16">HFR</span>
          {/* .dimmer is the app's themed range (index.css) — an unclassed range
              renders UA grey/blue, the only non-red thing on a night screen. */}
          <input
            className="dimmer w-24"
            type="range" min="0" max="10" step="0.1"
            aria-label="Preview HFR"
            value={snap.hfr ?? 0}
            onChange={(e) => patch({ hfr: num(e.target.value, 0) })}
          />
          <span className="mono w-8 text-right">{(snap.hfr ?? 0).toFixed(1)}</span>
        </label>
        <label className="inline-flex items-center gap-2">
          <span className="text-dim w-16">guide error</span>
          <input
            className="dimmer w-24"
            type="range" min="0" max="5" step="0.05"
            aria-label="Preview guide error"
            value={snap.guideRms ?? 0}
            onChange={(e) => patch({ guideRms: num(e.target.value, 0) })}
          />
          <span className="mono w-12 text-right">
            {(snap.guideRms ?? 0).toFixed(2)}{rmsUnit}
          </span>
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
          <span className="text-dim">pretend the time is</span>
          <input
            className="field !w-20 !py-1 mono text-center"
            placeholder="HH:MM"
            inputMode="numeric"
            aria-label="Pretend the time is"
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
          // A firing Abort must not read as the same cheerful green ● as a
          // firing Notify: destructive outcomes get warn tone AND say so in
          // words (status is never colour-only).
          <li key={o.id}
            className={`flex items-start gap-2 ${
              !o.wouldFire ? "text-dim" : o.destructive ? "text-warn" : "text-good"}`}>
            {o.wouldFire && o.destructive
              ? <Icon name="alert" size={11} className="shrink-0 mt-0.5" />
              : <span aria-hidden>{o.wouldFire ? "●" : "○"}</span>}
            <span className="flex-1 min-w-0">
              <span className="mono block truncate">{o.summary}</span>
              <span className="text-[10px]">
                {o.wouldFire
                  ? (o.destructive
                      ? "would fire — this ends or abandons work"
                      : "would fire")
                  : `waiting: ${o.reason}`}
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
  // The guider only reports arcseconds when the guide scope's focal length is
  // known — otherwise its RMS is PIXELS, and printing ″ would be a lie. Same
  // default as GuideView and as GuideStats itself: unknown reads as PIXELS,
  // because the failure that matters is calling a pixel figure arcsec.
  const guideStats = useGuideRms();
  const descOpts = useMemo<DescribeOpts>(
    () => ({ rmsArcsec: guideStats?.is_arcsec === true }), [guideStats?.is_arcsec]);

  const patchAt = (idx: number, patch: Partial<Instruction>) =>
    setPlan({
      ...plan,
      instructions: instructions.map((ins, i) => (i === idx ? { ...ins, ...patch } : ins)),
    });
  const removeAt = (idx: number) =>
    setPlan({ ...plan, instructions: instructions.filter((_, i) => i !== idx) });
  const add = () =>
    setPlan({ ...plan, instructions: [...instructions, defaultInstruction()] });

  const lockReason = canWrite
    ? null
    : `Editing instructions needs ${accessPhrase("control.mount")}.`;
  const lock = lockedProps(lockReason);

  // Collapsed row text — first rule + "+N more" (never a truncated run-on).
  const summary = instructionsSummary(
    instructions.map((i) => describeInstruction(i, targetNames, descOpts)),
  );

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
        <span className="truncate">{summary}</span>
        <span className="flex-1" />
        <span aria-hidden className="text-sm leading-none">{open ? "▾" : "▸"}</span>
      </button>

      {/* The reason rides OUTSIDE the dimmed block so it stays at full contrast
          and is readable on a tablet, where `title=` never fires at all. */}
      {open && lockReason && <LockedNote reason={lockReason} className="mt-2" />}

      {open && (
        <div
          {...lock}
          className={`mt-2 flex flex-col gap-3 ${lock.className ?? ""}`}
          data-readonly={canWrite ? undefined : "viewer"}
        >
          {instructions.map((ins, idx) => (
            <RuleRow
              key={ins.id ?? idx}
              ins={ins}
              idx={idx}
              targetNames={targetNames}
              descOpts={descOpts}
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
            <DryRunPanel rules={instructions} targetNames={targetNames}
              descOpts={descOpts} />
          )}
        </>
      )}
    </Panel>
  );
}
