// FlowInspectorColumn.tsx - the 284 px right column beside the Flows canvas,
// and the body of the `flowNode` sheet (parity row A11).
//
// WHICH NODE IT SHOWS is the one thing this component does not decide for
// itself. Selection and editing are separate state on purpose: on desktop the
// column follows `flows.sel`, while the pencil on a node card sets
// `flows.editNode` and only `editNode` opens the sheet - so a tap on the canvas
// gives an accent border and nothing else. `nodeId` overrides when the caller
// already knows, which is how the sheet passes `editNode` in.
//
// Two blocks, and they are separate components so their subscriptions are too:
// while a node is selected nothing here is subscribed to the compile's issue
// list, and while nothing is selected nothing is subscribed to a node record.
//
// EDITING A GRAPH IS UNGATED CLIENT-SIDE (wave R7 section 3.A row A3: the server
// gates the save, and RUN is not on this surface). The one lock this column
// carries is `flows.record.readonly` - an example flow, which `flowsSave`
// refuses. It is honest-disabled, never `disabled`: every control stays
// focusable, the value stays readable, and a press says why.

import { useId, useMemo, type JSX, type ReactNode } from "react";
import { useShallow } from "zustand/react/shallow";

import { useStore } from "../../../../../store";
import type { FlowIssue, FlowUnmapped } from "../../../../../lib/flowsApi";
import { Field, Label, LockNote, Mono, TextInput } from "../../../../ui";
import { explainLock } from "../../../../shell/explain";
import { FlowNodeEditor } from "./FlowNodeEditor";
import {
  CARRIED_TAG, FROM_RIG_TAG, MARK_RIG, NOTES_LEAD, levelWord, noteRows, splitUnmapped,
  type NoteRow,
} from "./issues";
import type { FlowFieldVariant } from "./FlowFieldRow";
import "./inspector.css";

/** Module-level frozen constants - a fresh `[]` inside a selector defeats
 *  `useShallow` on the first render of every consumer. */
const EMPTY_ISSUES: readonly FlowIssue[] = Object.freeze([]);
const EMPTY_UNMAPPED: readonly FlowUnmapped[] = Object.freeze([]);
const EMPTY_STRUCTURAL: readonly string[] = Object.freeze([]);

/** Why an example flow refuses every edit.
 *
 *  `LockNote` prints it as `Read-only - <reason>`, and the same string is what a
 *  locked control says when pressed, so the note and the toast are word for
 *  word the same sentence. (The legacy line used an em-dash; wave R7's copy rule
 *  is hyphens.) */
export const FLOW_READONLY_REASON =
  "example flows are not saved; duplicate this one to keep changes";

/** What the column says with nothing selected. It states what a tap and a drag
 *  DO, which is the one thing the ports themselves cannot. */
export const INSPECTOR_FOOTER =
  "Select a stage to edit its parameters. Drag from a right-side port onto a "
  + "left-side port to wire. Cyan ports carry the run cursor; amber ports carry events.";

export interface FlowInspectorColumnProps {
  variant?: FlowFieldVariant;
  /** When given, this node is shown instead of the current selection. `null` is
   *  meaningful - it means "the caller has nothing to show". */
  nodeId?: string | null;
  /** Passed through to `FlowNodeEditor` for a `calib` node; see its header. */
  calibSlot?: ReactNode;
}

export function FlowInspectorColumn({
  variant = "column", nodeId, calibSlot,
}: FlowInspectorColumnProps): JSX.Element {
  // A string|null selector, so selecting a DIFFERENT node re-renders this column
  // and nothing subscribed to some other node's status.
  const selId = useStore((s) => (s.flows.sel?.kind === "node" ? s.flows.sel.id : null));
  const id = nodeId !== undefined ? nodeId : selId;
  // The TYPE, not the record: this component only needs to know whether the id
  // resolves, and re-rendering the whole column on every keystroke in a field it
  // does not own would be the opposite of the write discipline.
  const resolves = useStore((s) =>
    (id ? s.flows.graph.nodes.some((n) => n.id === id) : false));
  const readonly = useStore((s) => s.flows.record?.readonly ?? false);
  const lockedReason = readonly ? FLOW_READONLY_REASON : null;

  const body = id && resolves
    ? (
      <FlowNodeEditor
        id={id}
        variant={variant}
        calibSlot={calibSlot}
        lockedReason={lockedReason}
        onExplain={explainLock}
      />
    )
    // The sheet only ever opens ON a node; with nothing to show it renders
    // nothing rather than the column's FLOW overview.
    : variant === "column" ? <InspectorOverview lockedReason={lockedReason} /> : null;

  return (
    <div
      className="nx-flowins"
      data-variant={variant}
      data-flows-inspector=""
      data-testid="flow-inspector"
    >
      {body}
    </div>
  );
}

// ────────────────────────────────────────────────── nothing-selected overview

function InspectorOverview({ lockedReason }: { lockedReason: string | null }): JSX.Element {
  const name = useStore((s) => s.flows.record?.name ?? "");
  const setName = useStore((s) => s.flowsSetName);
  const stages = useStore((s) => s.flows.graph.nodes.length);
  const wires = useStore((s) => s.flows.graph.edges.length);
  // These BUILD a value, so they need `useShallow`.
  const issues = useStore(useShallow((s) => s.flows.compiled?.issues ?? EMPTY_ISSUES));
  const unmapped = useStore(useShallow((s) => s.flows.compiled?.unmapped ?? EMPTY_UNMAPPED));
  const structural = useStore(useShallow((s) => s.flows.compiled?.structural ?? EMPTY_STRUCTURAL));
  const { losses, advisories, notes } = useMemo(() => splitUnmapped(unmapped), [unmapped]);
  const nameId = useId();

  return (
    <>
      <Label size={10}>FLOW</Label>

      <Field label="NAME" htmlFor={nameId}>
        <TextInput
          id={nameId}
          value={name}
          onChange={setName}
          ariaLabel="Flow name"
          lockedReason={lockedReason}
          data-testid="flow-name"
        />
      </Field>

      {/* An example flow is `readonly` server-side: `flowsSave` refuses it, so
          every edit made here would be discarded on close with nothing on screen
          to say so. */}
      <LockNote reason={lockedReason} data-testid="flow-readonly-note" />

      <div className="nx-flowins-stats">
        <span className="nx-flowins-stat">
          <Label size={10}>STAGES</Label>
          <Mono size={13} data-testid="flow-stage-count">{stages}</Mono>
        </span>
        <span className="nx-flowins-stat">
          <Label size={10}>WIRES</Label>
          <Mono size={13} data-testid="flow-wire-count">{wires}</Mono>
        </span>
      </div>

      <div className="nx-flowins-group" data-testid="flow-checks">
        <Label size={10}>CHECKS</Label>
        {issues.length === 0 ? (
          <p className="nx-flowins-line" data-level="good">ALL INPUTS WIRED</p>
        ) : (
          issues.map((iss, i) => (
            <Finding key={`${iss.level}:${i}`} level={iss.level} text={iss.text} />
          ))
        )}
      </div>

      {/* NOT HONOURED BY A RUN. Deliberately NOT folded into CHECKS: a doctor
          issue is advice about a good night, an unmapped entry is a statement
          that something drawn on the canvas will not happen. The operator finds
          out that their cloud rule is not running now, from a list on screen,
          instead of at 3 a.m. */}
      {(losses.length > 0 || structural.length > 0) && (
        <div className="nx-flowins-group" data-testid="flow-losses">
          <Label size={10}>NOT HONOURED BY A RUN</Label>
          {structural.map((text, i) => (
            <Finding key={`st:${i}`} level="danger" text={text} />
          ))}
          {losses.map((u) => (
            <Finding key={u.key} level={u.level} text={u.detail} />
          ))}
        </div>
      )}

      {/* BEFORE YOU RUN - rig state the canvas cannot show, at note weight.
          Today that is one row: this flow will run with no target temperature.
          It is not a loss and it does not block the run, but it is the last
          moment anyone can act on it for free. */}
      {advisories.length > 0 && (
        <div className="nx-flowins-group" data-testid="flow-advisories">
          <Label size={10}>BEFORE YOU RUN</Label>
          {advisories.map((u) => (
            <Finding key={u.key} level="warn" text={u.detail} />
          ))}
        </div>
      )}

      {/* FROM THE RIG - the `note` level, in ONE panel at note weight. Every
          row says the same thing about a different stage: the numbers on the
          card are shown for reference and the run takes the real ones from the
          rig's own settings. Nothing here is a lock, an error or a finding
          against the graph, so nothing here is amber - ten of these on a clean
          flow read as a failing build for as long as they were painted like
          one (2026-09-11, on the box). */}
      {notes.length > 0 && (
        <div className="nx-flowins-group" data-testid="flow-notes">
          <Label size={10}>{MARK_RIG}</Label>
          <p className="nx-flowins-lead">{NOTES_LEAD}</p>
          {noteRows(notes).map((r) => (
            <NoteLine key={r.key} row={r} />
          ))}
        </div>
      )}

      <p className="nx-flowins-foot">{INSPECTOR_FOOTER}</p>
    </>
  );
}

/** One row of the FROM THE RIG panel: which stage, what the plan carries, and
 *  what it takes from the rig instead - with the screen the real value lives on.
 *
 *  An engine that sends none of the three optional fields (an older rig, and
 *  every rig until the `to_plan` change lands) has only its own sentence, so the
 *  row prints that. The shape changes; the panel, the heading and the tone do
 *  not. */
function NoteLine({ row }: { row: NoteRow }): JSX.Element {
  // The key rides as an attribute rather than inside the testid: a rule key is
  // `instructions[dusk -> hold.pause]`, which no CSS selector can name.
  return (
    <div className="nx-flowins-note" data-testid="flow-note-row" data-note-key={row.key}>
      {row.name && <span className="nx-flowins-note-name">{row.name}</span>}
      {row.structured ? (
        <>
          {row.carried.length > 0 && (
            <p className="nx-flowins-note-line">
              <span className="nx-flowins-note-tag">{CARRIED_TAG}</span>
              {` ${row.carried.join(", ")}`}
            </p>
          )}
          {row.ignored.length > 0 && (
            <p className="nx-flowins-note-line">
              <span className="nx-flowins-note-tag">{FROM_RIG_TAG}</span>
              {` ${row.ignored.join(", ")}${row.source ? ` - ${row.source}` : ""}`}
            </p>
          )}
        </>
      ) : (
        <p className="nx-flowins-note-line">{row.detail}</p>
      )}
    </div>
  );
}

/** One compile finding. The level WORD rides beside the tone, because under
 *  `:root.night` every token collapses toward one hue and a warn line and a bad
 *  line would otherwise be the same line. */
function Finding({ level, text }: { level: string; text: string }): JSX.Element {
  const word = levelWord(level);
  return (
    <p className="nx-flowins-line" data-level={level}>
      {word && <span className="nx-flowins-word">{word}</span>}
      {text}
    </p>
  );
}

export default FlowInspectorColumn;
