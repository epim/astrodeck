// FlowInspector.tsx — the 284px right column at desktop, and the body of the
// edit sheet at tablet/phone. §C.8.
//
// WHICH NODE IT SHOWS is the one thing this component does not decide for
// itself. Selection and editing are separate state on purpose (README §3): on
// desktop the column follows `sel`, on tablet the ✎ glyph sets `editNode` and
// ONLY `editNode` opens the sheet, so a tap on the canvas gives you an accent
// border and nothing else. `nodeId` overrides when the caller already knows —
// which is how §C.10 passes `editNode` into the sheet.
//
// Two blocks, and they are separate components so their subscriptions are too:
// while a node is selected nothing here is subscribed to the doctor's issue
// list, and while nothing is selected nothing is subscribed to a node record.
import { useMemo } from "react";
import { useShallow } from "zustand/react/shallow";
import { useStore } from "../../store";
import { Field, LockedNote } from "../ui";
import { NODE_DEFS } from "./nodeDefs";
import CalibrationMatrix from "./CalibrationMatrix";
import FlowFieldRow, { type FieldVariant } from "./FlowFieldRow";
// `FlowIssue` / `FlowUnmapped` live in the api client — flowsTypes re-exports
// only the second of the pair, so both are taken from their one home.
import type { FlowIssue, FlowUnmapped } from "../../lib/flowsApi";

/** Module-level frozen constants — a fresh `[]` inside a selector defeats
 *  `useShallow` on the first render of every consumer. §B.3. */
const EMPTY_ISSUES: readonly FlowIssue[] = Object.freeze([]);
const EMPTY_UNMAPPED: readonly FlowUnmapped[] = Object.freeze([]);
const EMPTY_STRUCTURAL: readonly string[] = Object.freeze([]);

/** The doctor's three levels (doctor.py: `warn | danger | note`) as ink tokens.
 *
 *  Colour is never the only channel: a `danger` line also carries the glyph and
 *  the WORD, because the night palette collapses good/warn/bad toward coral and
 *  a red line and an amber line are then the same line. */
const ISSUE_INK: Record<string, string> = {
  warn: "text-warn", danger: "text-bad", note: "text-dim",
};

/** `note`-level entries that are NOT "answered another way".
 *
 *  The note level means one thing to `to_plan.losses` — do not block the run —
 *  and the inspector's ANSWERED ANOTHER WAY heading reads a second meaning
 *  into it: the thing you drew does happen, by some other part of the engine.
 *  That held while every note was about a wire.
 *
 *  `cooling.setpoint_c` is not about a wire. It says the run has no target
 *  temperature at all, on a camera that could hold one — the state that put 19
 *  lights on disk at +23 °C against a -10 °C library on 2026-08-22. Under
 *  ANSWERED ANOTHER WAY that sentence contradicts its own heading, which is
 *  exactly the failure the heading was split out to fix in the first place.
 *  So it gets its own list: still non-blocking, still note-weight, but a
 *  statement about THIS RIG rather than about the canvas. */
const RIG_ADVISORY: ReadonlySet<string> = new Set(["cooling.setpoint_c"]);

const DELETE_BTN =
  "font-display font-semibold text-[10.5px] tracking-[0.12em] rounded-[10px] " +
  "border border-[color-mix(in_srgb,var(--bad)_45%,transparent)] bg-transparent " +
  "text-bad px-3 py-2 hover:border-bad " +
  "hover:shadow-[0_0_12px_color-mix(in_srgb,var(--bad)_28%,transparent)]";

export interface FlowInspectorProps {
  variant?: FieldVariant;
  /** When given, this node is shown instead of the current selection. `null` is
   *  meaningful — it means "the caller has nothing to show". */
  nodeId?: string | null;
}

export default function FlowInspector({ variant = "column", nodeId }: FlowInspectorProps) {
  // A string|null selector, so selecting a DIFFERENT node re-renders this
  // column and nothing subscribed to some other node's status.
  const selId = useStore((s) => (s.flows.sel?.kind === "node" ? s.flows.sel.id : null));
  const id = nodeId !== undefined ? nodeId : selId;
  // The TYPE, not the record: this component only needs to know whether the id
  // resolves, and re-rendering the whole column on every keystroke in a field
  // it does not own would be the opposite of the write discipline.
  const type = useStore((s) =>
    (id ? s.flows.graph.nodes.find((n) => n.id === id)?.type ?? null : null));

  const body = id && type
    ? <InspectorNode id={id} variant={variant} />
    // The sheet only ever opens ON a node; with nothing to show it renders
    // nothing rather than the desktop column's FLOW overview.
    : variant === "column" ? <InspectorOverview /> : null;

  if (variant === "sheet") return <div className="flex flex-col gap-3">{body}</div>;

  return (
    <div
      data-flows-inspector
      className="w-[284px] flex-none border-l border-line overflow-y-auto
                 px-3.5 pt-3.5 pb-6 flex flex-col gap-3
                 bg-[color-mix(in_srgb,var(--bg-raise)_60%,transparent)]"
    >
      {body}
    </div>
  );
}

// ─────────────────────────────────────────────────────────── selected state

function InspectorNode({ id, variant }: { id: string; variant: FieldVariant }) {
  // The one subscription to the node record on this surface; every field row
  // gets its value passed down rather than opening its own.
  const node = useStore((s) => s.flows.graph.nodes.find((n) => n.id === id));
  const select = useStore((s) => s.flowsSelect);
  const deleteSel = useStore((s) => s.flowsDeleteSel);

  if (!node) return null;
  const def = NODE_DEFS[node.type];
  const ink = `var(${def.colorVar})`;

  const onDelete = () => {
    // flowsDeleteSel deletes `sel`, and in the sheet the node on screen is
    // `editNode` — which is NOT necessarily the selection. Pointing the
    // selection at the node being shown first is what stops DELETE STAGE from
    // deleting a different stage than the one whose name is at the top of the
    // sheet. (The slice then clears `editNode` itself, which closes the sheet.)
    select({ kind: "node", id });
    deleteSel();
  };

  return (
    <>
      <div className="flex items-center gap-2">
        <span
          className="flex-none w-[7px] h-[7px] rounded-[2px]"
          style={{ background: ink, boxShadow: `0 0 6px ${ink}` }}
          aria-hidden
        />
        <span className="flex-1 min-w-0 font-display font-semibold text-[11px] tracking-[0.16em]">
          {def.label}
        </span>
        {/* DOME CONTROL and FLAT PANEL read RIG here while the palette rail
            files them under EQUIPMENT. Both sides agree; it is not a bug. */}
        <span className="font-mono text-[9px] px-1.5 py-[3px] rounded-[3px]
                         border border-dashed border-line2 text-dim">
          {def.cat}
        </span>
      </div>

      <div className="text-[11.5px] text-dim leading-[1.5] [text-wrap:pretty]">
        {def.desc}
      </div>

      {node.type === "calib" && <CalibrationMatrix variant={variant} />}

      {def.fields.map((f) => (
        <FlowFieldRow
          // KEYED BY NODE, not just by field. Two nodes of the same type have
          // the same field list, so a bare `f.key` reconciles the rows in place
          // when the selection moves between them — and the row's in-progress
          // text buffer would survive the switch and show the previous node's
          // half-typed value over this one's stored param.
          key={`${node.id}:${f.key}`}
          nodeId={node.id}
          field={f}
          value={node.params[f.key]}
          variant={variant}
        />
      ))}

      <button
        type="button"
        onClick={onDelete}
        className={`${DELETE_BTN} ${variant === "sheet" ? "mt-1 min-h-[44px]" : "mt-1.5"}`}
      >
        DELETE STAGE
      </button>
    </>
  );
}

// ────────────────────────────────────────────────── nothing-selected overview

function InspectorOverview() {
  const name = useStore((s) => s.flows.record?.name ?? "");
  const readonly = useStore((s) => s.flows.record?.readonly ?? false);
  const setName = useStore((s) => s.flowsSetName);
  const stages = useStore((s) => s.flows.graph.nodes.length);
  const wires = useStore((s) => s.flows.graph.edges.length);
  // These BUILD a value, so they need useShallow exactly as useCamera does.
  const issues = useStore(useShallow((s) => s.flows.compiled?.issues ?? EMPTY_ISSUES));
  const unmapped = useStore(useShallow((s) => s.flows.compiled?.unmapped ?? EMPTY_UNMAPPED));
  const structural = useStore(useShallow((s) => s.flows.compiled?.structural ?? EMPTY_STRUCTURAL));
  // Two lists off one field, because they make opposite statements. A `note`
  // entry is not a quieter loss - it says the drawn thing DOES happen, by some
  // other part of the engine - and `/start` does not gate on one either
  // (to_plan.losses). Partitioned here rather than in the store so the split
  // sits beside the two headings it feeds.
  const losses = useMemo(() => unmapped.filter((u) => u.level !== "note"), [unmapped]);
  const notes = useMemo(
    () => unmapped.filter((u) => u.level === "note" && !RIG_ADVISORY.has(u.key)),
    [unmapped]);
  // A THIRD LIST, because a third statement showed up. See RIG_ADVISORY.
  const advisories = useMemo(
    () => unmapped.filter((u) => u.level === "note" && RIG_ADVISORY.has(u.key)),
    [unmapped]);

  return (
    <>
      <div className="font-display font-semibold text-[10px] tracking-[0.22em] text-dim">
        FLOW
      </div>

      <Field label="NAME">
        <input
          type="text"
          className="field !text-[12px] !py-[7px] !px-[9px] !rounded-none"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </Field>

      {/* An example flow is `readonly` server-side: flowsSave refuses it, so
          every edit made here is discarded on close with nothing on screen to
          say so. Nothing is disabled — the design has no locked state in the
          inspector and a bare `disabled` would strip the reason out of the
          accessibility tree along with the control — the reason is simply
          stated, which is the honest-disabled contract's other half. */}
      {readonly && (
        <LockedNote reason="Example flow — edits are not saved. Duplicate it to keep changes." />
      )}

      <div className="grid grid-cols-2 gap-2.5">
        <div className="flex flex-col gap-0.5">
          <span className="label">STAGES</span>
          <span className="font-mono text-[13px]">{stages}</span>
        </div>
        <div className="flex flex-col gap-0.5">
          <span className="label">WIRES</span>
          <span className="font-mono text-[13px]">{wires}</span>
        </div>
      </div>

      <div className="flex flex-col gap-1.5">
        <span className="label">CHECKS</span>
        {issues.length === 0 ? (
          <div className="font-mono text-[10.5px] leading-[1.45] text-good">
            ✓ all inputs wired
          </div>
        ) : (
          issues.map((iss, i) => (
            <div
              key={`${iss.level}:${i}`}
              className={`font-mono text-[10.5px] leading-[1.45] ${ISSUE_INK[iss.level] ?? "text-dim"}`}
            >
              {iss.level === "danger" && <span>⚠ DANGER </span>}
              {iss.text}
            </div>
          ))
        )}
      </div>

      {/* NOT HONOURED BY A RUN — §C.8's recommended surface for `unmapped[]`
          and `structural[]`, both of which the API returns and the design never
          drew (§G-2, awaiting sign-off).
          It is deliberately NOT folded into CHECKS: a doctor issue is advice
          about a good night, an unmapped entry is a statement that something
          drawn on the canvas will not happen. to_plan.py exists so "the
          operator finds out that their cloud rule is not running now, from a
          list on screen, instead of at 3 a.m." */}
      {(losses.length > 0 || structural.length > 0) && (
        <div className="flex flex-col gap-1.5">
          <span className="label">NOT HONOURED BY A RUN</span>
          {structural.map((text, i) => (
            <div key={`st:${i}`} className="font-mono text-[10.5px] leading-[1.45] text-bad">
              <span>⚠ DANGER </span>{text}
            </div>
          ))}
          {losses.map((u) => (
            <div
              key={u.key}
              className={`font-mono text-[10.5px] leading-[1.45] ${
                ISSUE_INK[u.level] ?? "text-warn"}`}
            >
              {u.level === "danger" && <span>⚠ DANGER </span>}
              {u.detail}
            </div>
          ))}
        </div>
      )}

      {/* BEFORE YOU RUN — rig state the canvas cannot show, at note weight.
          Today that is one row: this flow will run with no target temperature.
          It is not a loss (nothing drawn is being dropped) and it does not
          block the run (an uncooled night is legitimate), but it is the last
          moment anyone can act on it for free. */}
      {advisories.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <span className="label">BEFORE YOU RUN</span>
          {advisories.map((u) => (
            <div key={u.key} className="font-mono text-[10.5px] leading-[1.45] text-warn">
              {u.detail}
            </div>
          ))}
        </div>
      )}

      {/* ANSWERED ANOTHER WAY — the `note` level, and it needed its own heading
          rather than a dimmer line under the one above. Every row here says the
          thing an operator drew DOES happen, by some other part of the engine:
          the cloud hold releases itself, the scheduler advances the pool. Under
          a heading reading NOT HONOURED BY A RUN each one read as its own
          contradiction, and the start route made them click past it. */}
      {notes.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <span className="label">ANSWERED ANOTHER WAY</span>
          {notes.map((u) => (
            <div key={u.key} className="font-mono text-[10.5px] leading-[1.45] text-dim">
              {u.detail}
            </div>
          ))}
        </div>
      )}

      <div className="text-[11px] text-faint leading-[1.55] [text-wrap:pretty]
                      border-t border-line pt-2.5">
        Select a stage to edit its parameters. Drag from a right-side port onto a
        left-side port to wire. Cyan ports carry the run cursor; amber ports
        carry events.
      </div>
    </>
  );
}
