// PlanInstructionsSection.tsx - the conditional sequencer, rebuilt.
//
// Optional when-this-then-that rules layered on the fixed plan. An empty list is
// a byte-identical run, which is why this whole section lives behind one
// disclosure whose closed row states the first rule in full plus a count: a plan
// with no rules should cost one line, and a plan with four should not have to be
// opened to find out what the first one does.
//
// A THIN RENDER OVER TESTED HELPERS. Every piece of grammar - which triggers
// need a threshold, which need a clock, what an action change does to the rest
// of the rule, what the rule means in English, what is wrong with it - comes
// from `lib/instructions.ts`, and the dry run from `lib/instructionSim.ts`. Both
// are already checked against the server's evaluator (`sequence/instructions.py`
// is authoritative). Nothing here decides anything; it authors
// `plan.instructions` and prints what those modules say.
//
// HONEST-DISABLED, ONCE. The legacy panel dimmed the whole editor with
// `pointer-events-none` and printed its own read-only sentence. Here the reason
// arrives as a prop, is stated once by `LockNote`, and rides on every control -
// so a locked press says why instead of being swallowed by a dead layer, and a
// viewer can still read every rule and still run the preview, which changes
// nothing.
//
// JUMPS WITH ONE TARGET ARE REMOVED, NOT DISABLED. A styled or aria-disabled
// `<option>` is ignored by every browser inside a closed `<select>`, and `title`
// never renders on an option at all - so an unusable jump would be selectable
// and silently wrong. The options are dropped from the list, a persistent note
// says why, and the change handler refuses one anyway.

import { useMemo, useState, type JSX, type ReactNode } from "react";

import {
  ACTION_CONSEQUENCE, ACTION_GROUPS, ACTION_LABELS, MAX_TERMS, MIN_TERMS,
  PREDICATE_LABELS, TRIGGER_LABELS,
  actionChangePatch, actionNeedsTarget, defaultInstruction, defaultPredicate,
  describeInstruction, predicateChangePatch, predicateNeedsThreshold,
  predicateNeedsTime, toCompound, toFlat, triggerChangePatch,
  triggerNeedsThreshold, triggerNeedsTime, validateInstruction,
  type DescribeOpts,
} from "../../../../../lib/instructions";
import {
  SIM_CAVEAT, defaultSnapshot, simulateInstructions, type SimSnapshot,
} from "../../../../../lib/instructionSim";
import { useGuideRms, useStore } from "../../../../../store";
import type {
  ActionKind, Condition, Instruction, Predicate, PredicateKind, SequencePlan,
  TriggerKind,
} from "../../../../../types";
import {
  ActionButton, Card, Chip, Disclosure, Field, Label, LockNote, Mono, NumberField,
  Switch, TextInput, lockedAttrs, lockedClass,
} from "../../../../ui";
import {
  JUMP_HINT, LEVELS, hyphenate, instructionsSummary, thresholdHint,
} from "./instructionsModel";
import "./instructions.css";

export interface PlanSectionProps {
  lockedReason: string | null;
  onExplain: (reason: string) => void;
}

const TRIGGERS = Object.keys(TRIGGER_LABELS) as TriggerKind[];
const PREDICATES = Object.keys(PREDICATE_LABELS) as PredicateKind[];

interface SelectOption { value: string; label: string }
interface SelectGroup { label: string; options: SelectOption[] }

/** The one picker in this area.
 *
 *  `next/ui` has no `<select>` primitive - the design uses `Segmented`, `Dial`
 *  and `Chip` rows, none of which fits a 12-entry action list inside a 420 px
 *  column. So this is a plain select in the area's own chrome, honest-disabled
 *  the same way every primitive is: `aria-disabled`, never the native
 *  `disabled`, the change refused rather than the control removed, and a press
 *  that states the reason. */
function RuleSelect({
  label, ariaLabel, value, options, groups, onChange, lockedReason, onExplain, testid,
}: {
  label: string;
  ariaLabel: string;
  value: string;
  options?: SelectOption[];
  groups?: SelectGroup[];
  onChange: (next: string) => void;
  lockedReason: string | null;
  onExplain: (reason: string) => void;
  testid?: string;
}): JSX.Element {
  return (
    <Field label={label} className="nx-planrule-field">
      <select
        className={lockedClass(lockedReason, "nx-planrule-select")}
        aria-label={ariaLabel}
        value={value}
        onChange={(e) => { if (!lockedReason) onChange(e.target.value); }}
        onMouseDown={(e) => {
          if (!lockedReason) return;
          e.preventDefault();
          onExplain(lockedReason);
        }}
        data-testid={testid}
        {...lockedAttrs(lockedReason)}
      >
        {options?.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        {groups?.map((g) => (
          <optgroup key={g.label} label={g.label}>
            {g.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </optgroup>
        ))}
      </select>
    </Field>
  );
}

function Note({ tone, children, testid }: {
  tone: "dim" | "warn"; children: ReactNode; testid?: string;
}): JSX.Element {
  return (
    <p className="nx-planrule-note" data-tone={tone} data-testid={testid}>{children}</p>
  );
}

/** One leaf term of a compound condition. Thin render; all shape logic lives in
 *  `lib/instructions.ts`. */
function TermRow({ term, idx, ruleIdx, canRemove, onPatch, onRemove, lockedReason, onExplain }: {
  term: Predicate;
  idx: number;
  ruleIdx: number;
  canRemove: boolean;
  onPatch: (patch: Partial<Predicate>) => void;
  onRemove: () => void;
  lockedReason: string | null;
  onExplain: (reason: string) => void;
}): JSX.Element {
  const tag = `Rule ${ruleIdx + 1} condition ${idx + 1}`;
  const hint = term.kind === "hfr_above" || term.kind === "guide_rms_above"
    ? thresholdHint(term.kind === "hfr_above" ? "on_hfr_above" : "on_guide_rms_above", term.relative)
    : null;
  return (
    <div className="nx-planrule-line" data-testid={`plan-rule-term-${ruleIdx}-${idx}`}>
      <RuleSelect
        label="CONDITION"
        ariaLabel={`${tag} kind`}
        value={term.kind}
        options={PREDICATES.map((k) => ({ value: k, label: PREDICATE_LABELS[k] }))}
        onChange={(v) => onPatch(predicateChangePatch(term, v as PredicateKind))}
        lockedReason={lockedReason}
        onExplain={onExplain}
      />
      {predicateNeedsThreshold(term.kind) && (
        <NumberField
          label="OVER"
          ariaLabel={`${tag} threshold`}
          value={term.threshold}
          onCommit={(n) => onPatch({ threshold: Math.max(0, n) })}
          min={0}
          step={0.1}
          hint={hint ?? undefined}
          lockedReason={lockedReason}
          onExplain={onExplain}
        />
      )}
      {predicateNeedsTime(term.kind) && (
        <Field label="AT" className="nx-planrule-field">
          <TextInput
            value={term.at_time ?? ""}
            onChange={(v) => onPatch({ at_time: v || null })}
            placeholder="HH:MM"
            mono
            ariaLabel={`${tag} time`}
            lockedReason={lockedReason}
          />
        </Field>
      )}
      {canRemove && (
        <ActionButton
          kind="danger"
          ariaLabel={`Remove ${tag.toLowerCase()}`}
          onPress={onRemove}
          lockedReason={lockedReason}
          onExplain={onExplain}
        >
          remove
        </ActionButton>
      )}
    </div>
  );
}

/** The AND/OR editor. Only ever rendered inside a rule's `advanced` disclosure,
 *  so the single-condition path a first-time author sees is untouched. */
function CompoundEditor({ when, ruleIdx, onChange, onDrop, lockedReason, onExplain }: {
  when: Condition;
  ruleIdx: number;
  onChange: (c: Condition) => void;
  onDrop: () => void;
  lockedReason: string | null;
  onExplain: (reason: string) => void;
}): JSX.Element {
  const patchTerm = (i: number, patch: Partial<Predicate>) =>
    onChange({ ...when, terms: when.terms.map((t, n) => (n === i ? { ...t, ...patch } : t)) });
  return (
    <div className="nx-planrule-compound" data-testid={`plan-rule-compound-${ruleIdx}`}>
      <div className="nx-planrule-line">
        <RuleSelect
          label="MATCH"
          ariaLabel={`Rule ${ruleIdx + 1} combine mode`}
          value={when.op}
          options={[
            { value: "all", label: "all of these (AND)" },
            { value: "any", label: "any of these (OR)" },
          ]}
          onChange={(v) => onChange({ ...when, op: v as Condition["op"] })}
          lockedReason={lockedReason}
          onExplain={onExplain}
        />
        <Chip lockedReason={lockedReason} onExplain={onExplain} onClick={onDrop}>
          back to a single condition
        </Chip>
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
          lockedReason={lockedReason}
          onExplain={onExplain}
        />
      ))}
      {when.terms.length < MAX_TERMS && (
        <Chip
          lockedReason={lockedReason}
          onExplain={onExplain}
          onClick={() => onChange({ ...when, terms: [...when.terms, defaultPredicate("hfr_above")] })}
        >
          add a condition
        </Chip>
      )}
    </div>
  );
}

function RuleRow({ ins, idx, targetNames, descOpts, onPatch, onRemove, lockedReason, onExplain }: {
  ins: Instruction;
  idx: number;
  targetNames: string[];
  descOpts: DescribeOpts;
  onPatch: (patch: Partial<Instruction>) => void;
  onRemove: () => void;
  lockedReason: string | null;
  onExplain: (reason: string) => void;
}): JSX.Element {
  const [adv, setAdv] = useState(false);
  const problems = validateInstruction(ins);
  const when = ins.when ?? null;
  const jumpsOk = targetNames.length >= 2;
  const groups: SelectGroup[] = ACTION_GROUPS
    .map((g) => ({
      label: g.label,
      // A rule authored when there WERE two targets keeps its own option, so
      // the select never renders a value it does not offer.
      options: g.actions
        .filter((a) => jumpsOk || !actionNeedsTarget(a) || a === ins.action)
        .map((a) => ({ value: a, label: ACTION_LABELS[a] })),
    }))
    .filter((g) => g.options.length > 0);
  const consequence = ACTION_CONSEQUENCE[ins.action];
  const hint = when ? null : thresholdHint(ins.trigger, ins.relative);

  return (
    <div className="nx-planrule" data-testid={`plan-rule-${idx}`}>
      <div className="nx-planrule-line">
        {when ? (
          <Chip
            onClick={() => setAdv(true)}
            data-testid={`plan-rule-combined-${idx}`}
          >
            combined conditions ({when.op === "all" ? "AND" : "OR"}) - edit under advanced
          </Chip>
        ) : (
          <RuleSelect
            label="WHEN"
            ariaLabel={`Rule ${idx + 1} trigger`}
            value={ins.trigger}
            options={TRIGGERS.map((t) => ({ value: t, label: TRIGGER_LABELS[t] }))}
            // Seed whatever the new trigger needs (threshold, clock) so the
            // author never lands straight on a red validation error.
            onChange={(v) => onPatch(triggerChangePatch(ins, v as TriggerKind))}
            lockedReason={lockedReason}
            onExplain={onExplain}
            testid={`plan-rule-trigger-${idx}`}
          />
        )}

        {!when && triggerNeedsThreshold(ins.trigger) && (
          <NumberField
            label="OVER"
            ariaLabel={`Rule ${idx + 1} threshold`}
            value={ins.threshold}
            onCommit={(n) => onPatch({ threshold: Math.max(0, n) })}
            min={0}
            step={0.1}
            hint={hint ?? undefined}
            lockedReason={lockedReason}
            onExplain={onExplain}
            data-testid={`plan-rule-threshold-${idx}`}
          />
        )}
        {!when && triggerNeedsTime(ins.trigger) && (
          <Field label="AT" className="nx-planrule-field">
            <TextInput
              value={ins.at_time ?? ""}
              onChange={(v) => onPatch({ at_time: v || null })}
              placeholder="HH:MM"
              mono
              ariaLabel={`Rule ${idx + 1} time`}
              lockedReason={lockedReason}
              data-testid={`plan-rule-time-${idx}`}
            />
          </Field>
        )}

        <RuleSelect
          label="THEN"
          ariaLabel={`Rule ${idx + 1} action`}
          value={ins.action}
          groups={groups}
          onChange={(v) => {
            const a = v as ActionKind;
            if (actionNeedsTarget(a) && !jumpsOk) return;   // defence in depth
            onPatch(actionChangePatch(ins, a, targetNames));
          }}
          lockedReason={lockedReason}
          onExplain={onExplain}
          testid={`plan-rule-action-${idx}`}
        />
      </div>

      <div className="nx-planrule-line">
        <Switch
          checked={ins.enabled}
          onChange={(v) => onPatch({ enabled: v })}
          label={`Enable rule ${idx + 1}`}
          lockedReason={lockedReason}
          onExplain={onExplain}
          data-testid={`plan-rule-enabled-${idx}`}
        />
        <ActionButton
          kind="danger"
          ariaLabel={`Delete rule ${idx + 1}`}
          onPress={onRemove}
          lockedReason={lockedReason}
          onExplain={onExplain}
          data-testid={`plan-rule-delete-${idx}`}
        >
          delete rule
        </ActionButton>
      </div>

      {/* A visible hint, not only `title=`: `title` never fires on touch, and
          "4.0" means nothing without knowing what normal looks like. */}
      {!when && hint && <Note tone="dim">{hint}</Note>}
      {!jumpsOk && <Note tone="dim" testid={`plan-rule-jumphint-${idx}`}>{JUMP_HINT}</Note>}
      {/* What this action COSTS, the moment it is selected. */}
      {consequence && (
        <Note tone="warn" testid={`plan-rule-consequence-${idx}`}>{hyphenate(consequence)}</Note>
      )}

      {ins.action === "notify" && (
        <div className="nx-planrule-line">
          <Field label="MESSAGE" className="nx-planrule-grow">
            <TextInput
              value={ins.message}
              onChange={(v) => onPatch({ message: v })}
              placeholder="notification message"
              ariaLabel={`Rule ${idx + 1} message`}
              lockedReason={lockedReason}
              data-testid={`plan-rule-message-${idx}`}
            />
          </Field>
          <RuleSelect
            label="LEVEL"
            ariaLabel={`Rule ${idx + 1} level`}
            value={ins.level}
            options={LEVELS.map((l) => ({ value: l, label: l }))}
            onChange={(v) => onPatch({ level: v as Instruction["level"] })}
            lockedReason={lockedReason}
            onExplain={onExplain}
          />
        </div>
      )}

      {actionNeedsTarget(ins.action) && (
        <div className="nx-planrule-line">
          <RuleSelect
            label={ins.action === "run_target" ? "SWITCH TO" : "DROP"}
            ariaLabel={`Rule ${idx + 1} target to ${ins.action === "run_target" ? "switch to" : "drop"}`}
            value={ins.target_arg ?? ""}
            options={[
              { value: "", label: "(choose a target)" },
              ...targetNames.map((n) => ({ value: n, label: n })),
            ]}
            onChange={(v) => onPatch({ target_arg: v || null })}
            lockedReason={lockedReason}
            onExplain={onExplain}
            testid={`plan-rule-target-${idx}`}
          />
          <Note tone="dim">"Once" is on so a rule cannot loop.</Note>
        </div>
      )}

      {ins.action === "abort" && (
        <Field label="REASON" className="nx-planrule-grow">
          <TextInput
            value={ins.message}
            onChange={(v) => onPatch({ message: v })}
            placeholder="why the session stopped (optional - goes in the report)"
            ariaLabel={`Rule ${idx + 1} abort reason`}
            lockedReason={lockedReason}
            data-testid={`plan-rule-abort-${idx}`}
          />
        </Field>
      )}

      <Disclosure
        summary="ADVANCED"
        sub={`fires ${ins.once ? "once per run" : "every time"}, cooldown ${ins.cooldown_s}s`}
        open={adv}
        onToggle={setAdv}
        data-testid={`plan-rule-advanced-${idx}`}
      >
        <div className="nx-planrule-adv">
          <Switch
            checked={ins.once}
            onChange={(v) => onPatch({ once: v })}
            label={`Rule ${idx + 1} fire once per run`}
            lockedReason={lockedReason}
            onExplain={onExplain}
          />
          <NumberField
            label="COOLDOWN"
            ariaLabel={`Rule ${idx + 1} cooldown seconds`}
            value={ins.cooldown_s}
            onCommit={(n) => onPatch({ cooldown_s: Math.max(0, Math.round(n)) })}
            unit="s"
            min={0}
            integer
            zeroMeans="every frame boundary"
            hint="Minimum time between fires of this rule. Throttles a rule that would otherwise fire on every sub."
            lockedReason={lockedReason}
            onExplain={onExplain}
          />
          <RuleSelect
            label="ONLY TARGET"
            ariaLabel={`Rule ${idx + 1} only target`}
            value={ins.only_target ?? ""}
            options={[
              { value: "", label: "(any)" },
              ...targetNames.map((n) => ({ value: n, label: n })),
            ]}
            onChange={(v) => onPatch({ only_target: v || null })}
            lockedReason={lockedReason}
            onExplain={onExplain}
          />
          {when ? (
            <CompoundEditor
              when={when}
              ruleIdx={idx}
              onChange={(c) => onPatch({ when: c })}
              onDrop={() => onPatch({ when: null, ...toFlat(when) })}
              lockedReason={lockedReason}
              onExplain={onExplain}
            />
          ) : (
            <Chip
              lockedReason={lockedReason}
              onExplain={onExplain}
              onClick={() => onPatch({ when: toCompound(ins) })}
            >
              combine conditions (AND/OR)
            </Chip>
          )}
        </div>
      </Disclosure>

      <Mono size={10} tone="dim">{describeInstruction(ins, targetNames, descOpts)}</Mono>
      {problems.length > 0 && (
        <ul className="nx-planrule-problems" data-testid={`plan-rule-problems-${idx}`}>
          {problems.map((p) => <li key={p}>{hyphenate(p)}</li>)}
        </ul>
      )}
    </div>
  );
}

/** Dry run: "what would these rules do right now?". A thin render over the pure
 *  `simulateInstructions`. Read-only, so it stays usable for a viewer who cannot
 *  edit the plan - previewing changes nothing. */
function DryRun({ rules, targetNames, descOpts }: {
  rules: Instruction[];
  targetNames: string[];
  descOpts: DescribeOpts;
}): JSX.Element {
  // `now` is seeded from the REAL clock; a hardcoded 22:00 made every
  // time-based rule preview wrong for anyone not imaging at 10pm.
  const [snap, setSnap] = useState<SimSnapshot>(() => defaultSnapshot(targetNames));
  const outcomes = useMemo(
    () => simulateInstructions(rules, snap, descOpts), [rules, snap, descOpts]);
  const patch = (p: Partial<SimSnapshot>) => setSnap((s) => ({ ...s, ...p }));
  const firing = outcomes.filter((o) => o.wouldFire).length;
  const rmsUnit = descOpts.rmsArcsec === false ? " px" : " arcsec";

  return (
    <div className="nx-planrule-sim" data-testid="plan-instructions-preview">
      <div className="nx-planrule-line">
        <NumberField
          label="HFR"
          ariaLabel="Preview HFR"
          value={snap.hfr ?? 0}
          onCommit={(n) => patch({ hfr: n })}
          min={0}
          max={10}
          step={0.1}
          lockedReason={null}
          data-testid="plan-sim-hfr"
        />
        <NumberField
          label="GUIDE ERROR"
          ariaLabel={`Preview guide error, ${rmsUnit.trim()}`}
          value={snap.guideRms ?? 0}
          onCommit={(n) => patch({ guideRms: n })}
          min={0}
          max={5}
          step={0.05}
          unit={rmsUnit.trim()}
          lockedReason={null}
        />
        <Field label="PRETEND THE TIME IS" className="nx-planrule-field">
          <TextInput
            value={snap.now}
            onChange={(v) => patch({ now: v })}
            placeholder="HH:MM"
            mono
            ariaLabel="Pretend the time is"
          />
        </Field>
      </div>
      <div className="nx-planrule-line">
        <Switch checked={snap.frameRejected} onChange={(v) => patch({ frameRejected: v })}
          label="Preview frame rejected" />
        <Switch checked={snap.targetComplete} onChange={(v) => patch({ targetComplete: v })}
          label="Preview target complete" />
        <RuleSelect
          label="TARGET"
          ariaLabel="Preview active target"
          value={snap.activeTarget ?? ""}
          options={[
            { value: "", label: "(none)" },
            ...targetNames.map((n) => ({ value: n, label: n })),
          ]}
          onChange={(v) => patch({ activeTarget: v || null })}
          lockedReason={null}
          onExplain={() => {}}
        />
      </div>

      <ul className="nx-planrule-outcomes">
        {outcomes.map((o) => (
          // A firing Abort must not read as the same cheerful mark as a firing
          // Notify: destructive outcomes get warn tone AND say so in words,
          // because status is never colour-only.
          <li key={o.id} data-fire={o.wouldFire ? (o.destructive ? "destructive" : "yes") : "no"}>
            <Mono size={10.5}>{hyphenate(o.summary)}</Mono>
            <Mono size={10} tone="dim">
              {o.wouldFire
                ? (o.destructive ? "would fire - this ends or abandons work" : "would fire")
                : `waiting: ${hyphenate(o.reason)}`}
              {o.note ? ` - ${hyphenate(o.note)}` : ""}
            </Mono>
          </li>
        ))}
      </ul>
      <Mono size={10} tone="dim">
        {firing} of {outcomes.length} would fire on this frame. {hyphenate(SIM_CAVEAT)}
      </Mono>
    </div>
  );
}

export function PlanInstructionsSection({ lockedReason, onExplain }: PlanSectionProps): JSX.Element {
  const plan = useStore((s) => s.plan);
  const setPlan = useStore((s) => s.setPlan);
  const [open, setOpen] = useState(false);
  const [preview, setPreview] = useState(false);

  const instructions = plan?.instructions ?? [];
  const targetNames = (plan?.targets ?? []).map((t) => t.name).filter(Boolean);

  // The guider reports arcseconds only when the guide scope's focal length is
  // known; otherwise its RMS is PIXELS, and printing arcsec would be a lie.
  // Same default as the guider sheet: unknown reads as pixels, because the
  // failure that matters is calling a pixel figure arcsec.
  const guideStats = useGuideRms();
  const descOpts = useMemo<DescribeOpts>(
    () => ({ rmsArcsec: guideStats?.is_arcsec === true }), [guideStats?.is_arcsec]);

  const write = (next: Instruction[]) => {
    if (plan) setPlan({ ...plan, instructions: next } as SequencePlan);
  };
  const patchAt = (idx: number, patch: Partial<Instruction>) =>
    write(instructions.map((ins, i) => (i === idx ? { ...ins, ...patch } : ins)));
  const removeAt = (idx: number) => write(instructions.filter((_, i) => i !== idx));
  const add = () => write([...instructions, defaultInstruction()]);

  const summary = instructionsSummary(
    instructions.map((i) => hyphenate(describeInstruction(i, targetNames, descOpts))),
  );

  return (
    <Card className="nx-planrules" data-testid="plan-instructions">
      <div className="nx-planrules-head">
        <Label>INSTRUCTIONS</Label>
        <Mono size={10} tone="dim">
          {instructions.length > 0
            ? `${instructions.length} rule${instructions.length === 1 ? "" : "s"} layered on the plan`
            : "no rules - the plan runs exactly as written"}
        </Mono>
      </div>

      <LockNote reason={lockedReason} data-testid="plan-instructions-lock" />

      <Disclosure
        summary="RULES"
        sub={summary}
        open={open}
        onToggle={setOpen}
        data-testid="plan-instructions-rules"
      >
        <div className="nx-planrules-list">
          {instructions.map((ins, idx) => (
            <RuleRow
              key={ins.id ?? idx}
              ins={ins}
              idx={idx}
              targetNames={targetNames}
              descOpts={descOpts}
              onPatch={(patch) => patchAt(idx, patch)}
              onRemove={() => removeAt(idx)}
              lockedReason={lockedReason}
              onExplain={onExplain}
            />
          ))}
          <ActionButton
            kind="secondary"
            ariaLabel="Add rule"
            onPress={add}
            lockedReason={lockedReason}
            onExplain={onExplain}
            data-testid="plan-instructions-add"
          >
            add rule
          </ActionButton>
        </div>
      </Disclosure>

      {open && instructions.length > 0 && (
        <Disclosure
          summary="PREVIEW"
          sub="what these rules would do on one frame right now"
          open={preview}
          onToggle={setPreview}
          data-testid="plan-instructions-preview-group"
        >
          <DryRun rules={instructions} targetNames={targetNames} descOpts={descOpts} />
        </Disclosure>
      )}
    </Card>
  );
}
