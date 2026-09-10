// wizard.tsx - NEW FLOW, guided (wave R7 row A16; rebuild of
// `components/flows/FlowWizard.tsx` in the design's vocabulary).
//
// WHAT CHANGED, AND WHAT DID NOT.
//
// Not changed: the server generates AND saves, so there is one call and the id
// it returns is openable; the three answers travel exactly as the sheet holds
// them; START BLANK posts the same `POST /api/flows` the generated record would
// have gone through; `control.capture` gates both buttons and the sentences are
// the ones `accessPhrase` writes.
//
// Changed: it is a SHEET, not an `Overlay`. Its open state is the ROUTE
// (ARCHITECTURE.md section 5), so it survives a reload and the browser Back
// button closes it - which is why `flows.ui.wizardOpen` is no longer the thing
// that opens it. Nothing in the next UI reads that flag any more either: T-R7-20
// cut `FlowsCanvasHost` over to the rebuilt canvas, so the legacy `FlowWizard`
// that listened to it is not mounted here at all and the clear-on-close it
// existed for was writing to a listener with nobody on the other end.
//
// Changed: the hand-rolled radiogroup is `Segmented` (one tab stop, arrow keys,
// roving tabindex - the same model every other exclusive choice in the app
// uses), the automation pills are `Chip`s, and both em-dashes in the legacy
// copy are hyphens.
//
// The `i` DESIGN NOTES button is not rebuilt: `flows.ui.notesOpen` is written
// by `FlowHeader` and read by nothing in the repo (wave R7 section 6.1 defect
// 2). A control that promises a panel nobody wrote is worse than no control.

import { useState, type JSX } from "react";

import { accessPhrase, useCanControlCapture } from "../../../../../lib/caps";
import { flowsApi } from "../../../../../lib/flowsApi";
import { useStore } from "../../../../../store";
import { NxIcon } from "../../../../icons";
import { nav } from "../../../../router";
import { explainLock } from "../../../../shell/explain";
import {
  ActionButton, Card, Chip, Field, Label, LockNote, Mono, Segmented, Sheet, TextInput,
} from "../../../../ui";
import {
  AUTOMATIONS, AUTOMATION_DEFAULTS, BLANK_FAILED, BLANK_NAME, BLANK_TAGLINE, BUSY_REASON,
  GENERATE_FAILED, KIND_SUB, KINDS, TARGET_PLACEHOLDER, WIZARD_NOTE, blankNodes,
  type WizardKind,
} from "./wizardModel";
import "./create.css";

export function FlowNewSheet(): JSX.Element {
  const flowsOpen = useStore((s) => s.flowsOpen);
  const enqueueToast = useStore((s) => s.enqueueToast);
  const canCreate = useCanControlCapture();

  // The three answers live here, not in the store: nothing outside this sheet
  // reads them, and `FlowsUiState` deliberately carries only `wizardOpen`.
  const [kind, setKind] = useState<WizardKind>(KINDS[0]);
  const [autos, setAutos] = useState<readonly string[]>(AUTOMATION_DEFAULTS);
  const [target, setTarget] = useState("");
  const [creating, setCreating] = useState(false);
  const [generating, setGenerating] = useState(false);

  const close = (): void => { nav.back(); };

  const busy = creating || generating;
  const blankReason = !canCreate
    ? `Creating a flow needs ${accessPhrase("control.capture")}.`
    : busy ? BUSY_REASON : null;
  const generateReason = !canCreate
    ? `Generating a flow needs ${accessPhrase("control.capture")}.`
    : busy ? BUSY_REASON : null;

  /** The server generates AND saves, so there is one call and the id it returns
   *  is openable. */
  const generate = async (): Promise<void> => {
    setGenerating(true);
    try {
      const rec = (await flowsApi.generateFromWizard({
        kind,
        options: [...autos],
        target: target.trim(),
      })) as { id?: string };
      if (!rec?.id) throw new Error("the server returned a flow with no id");
      close();
      await flowsOpen(rec.id);
    } catch (e) {
      enqueueToast({
        level: "error",
        title: GENERATE_FAILED,
        detail: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setGenerating(false);
    }
  };

  const startBlank = async (): Promise<void> => {
    setCreating(true);
    try {
      const rec = (await flowsApi.create({
        name: BLANK_NAME,
        // No `folder`: the server's FlowRecord already defaults to "My flows",
        // and restating a default here is a second place for it to change.
        tagline: BLANK_TAGLINE,
        graph: { nodes: blankNodes(), edges: [] },
      })) as { id?: string };
      if (!rec?.id) throw new Error("the server returned a flow with no id");
      close();
      await flowsOpen(rec.id);
    } catch (e) {
      enqueueToast({
        level: "error",
        title: BLANK_FAILED,
        detail: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setCreating(false);
    }
  };

  return (
    <Sheet
      data-testid="session-flow-new"
      title="NEW FLOW"
      sub="guided - the server builds the graph, you edit it"
      icon={<NxIcon name="flows" size={18} />}
      backLabel="FLOWS"
      onBack={close}
      footer={(
        <div className="nx-create-foot">
          <ActionButton
            kind="primary"
            size="lg"
            full
            data-testid="flow-new-generate"
            lockedReason={generateReason}
            onExplain={explainLock}
            busy={generating}
            onPress={() => { void generate(); }}
          >
            {generating ? "GENERATING" : "GENERATE FLOW"}
          </ActionButton>
          <ActionButton
            kind="ghost"
            size="lg"
            data-testid="flow-new-blank"
            lockedReason={blankReason}
            onExplain={explainLock}
            busy={creating}
            onPress={() => { void startBlank(); }}
          >
            START BLANK
          </ActionButton>
        </div>
      )}
    >
      <div className="nx-create-stack">
        {/* 1. what kind of night */}
        <section className="nx-create-group" data-testid="flow-wizard-step" data-step="kind">
          <Label size={10}>WHAT ARE WE DOING TONIGHT?</Label>
          <Segmented<WizardKind>
            data-testid="flow-wizard-kind"
            label="What are we doing tonight"
            value={kind}
            onChange={setKind}
            options={KINDS.map((k) => ({ value: k, label: k, sub: KIND_SUB[k] }))}
          />
        </section>

        {/* 2. automation pills */}
        <section className="nx-create-group" data-testid="flow-wizard-step" data-step="automation">
          <div className="nx-create-head">
            <Label size={10}>ADD AUTOMATION</Label>
            {/* Not a count of the lit chips - the pixels already carry that.
                What they do not carry is that a chip is not a preference: the
                generator wires real nodes in for each one (`wizard.py`). */}
            <Mono size={10} tone="dim">each wires stages into the graph</Mono>
          </div>
          <div className="nx-create-chips" role="group" aria-label="Add automation">
            {AUTOMATIONS.map((a) => (
              <Chip
                key={a}
                data-testid="flow-wizard-auto"
                active={autos.includes(a)}
                onClick={() => setAutos((prev) => (
                  prev.includes(a) ? prev.filter((p) => p !== a) : [...prev, a]
                ))}
              >
                {a}
              </Chip>
            ))}
          </div>
        </section>

        {/* 3. the target, or the pool candidates */}
        <section className="nx-create-group" data-testid="flow-wizard-step" data-step="target">
          <Field label="TARGET (OR CANDIDATES, COMMA-SEPARATED)" htmlFor="flow-wizard-target">
            <TextInput
              id="flow-wizard-target"
              data-testid="flow-wizard-target"
              value={target}
              onChange={setTarget}
              placeholder={TARGET_PLACEHOLDER}
              mono
              ariaLabel="Target, or candidates separated by commas"
            />
          </Field>
        </section>

        <Card tone="default">
          <p className="nx-create-note" data-testid="flow-wizard-note">{WIZARD_NOTE}</p>
        </Card>

        {/* A capability note, and only when it applies. Saying it to everyone
            else would be a warning about nothing.
            THE SENTENCE IS THE SHEET'S, not one button's: neither GENERATE nor
            START BLANK can run, and `Creating a flow needs ...` is the one that
            covers both. Each button still carries its own verb in its own
            `lockedReason`, which is what a press says out loud - a tooltip is
            not readable on the touch screen this has to work on, so the note is
            what makes the refusal visible. */}
        <LockNote
          data-testid="flow-new-lock"
          reason={canCreate ? null : `Creating a flow needs ${accessPhrase("control.capture")}.`}
        />
      </div>
    </Sheet>
  );
}
