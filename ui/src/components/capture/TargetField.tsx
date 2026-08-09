// TargetField.tsx — the Capture screen's target name (#182).
//
// WHAT THIS REPLACED: a bare `<input placeholder="M42">` backed by a
// component-local `useState("")`. It was the ONLY way the app could ever learn
// what it was pointed at, it was wiped on every tab switch (App.tsx renders
// <ViewBoundary key={view}/>, so leaving for the Atlas and coming back cleared
// a name the operator had typed), and leaving it blank filed the night under
// `untargeted/` with no OBJECT card in any file. Meanwhile every Go To had
// already plate-solved and thrown the answer away.
//
// It is still a text box, deliberately. Removing it would be wrong three ways:
// a dark/bias/flat has no sky to solve; per-frame solving is off by default so
// most rigs have no WCS at all; and an operator naming a mosaic panel or a test
// field has to be able to say what it is. What changed is that it is no longer
// the only source — it is an OVERRIDE over a value the sky proposes.
//
// THE PROPOSAL IS NEVER WRITTEN INTO THE BOX BY THE APP. `identifyState` keeps
// `value` as the typed string verbatim in every state; adopting is a tap. The
// reason is not politeness: this string is what `hub._capture_path` sanitizes
// into a folder AND uses as the key of the persisted per-target frame counter,
// so a value that could change on its own between frame 3 and frame 4 would
// split one night across two directories with two overlapping 0001… runs and
// no error anywhere.
//
// All three states, their copy and their transitions live in
// `lib/fieldIdentity.ts` and are tested there; this file is the shell.

import { Field } from "../ui";
import { Icon } from "../icons";
import { identifyState, objectCardPreview } from "../../lib/fieldIdentity";
import type { PreviewField } from "../../types";

export function TargetField({
  value,
  onChange,
  field,
  perFrameSolving,
  frameType,
  readOnly,
  readOnlyReason,
  onOpenSolveSettings,
}: {
  value: string;
  onChange: (v: string) => void;
  field?: PreviewField | null;
  /** config.solve_saved_lights — whether each saved light is solved. */
  perFrameSolving: boolean;
  frameType: string;
  readOnly?: boolean;
  readOnlyReason?: string | null;
  /** Where "turn on plate solving" goes. Omitted => the line states the fix in
   *  words but offers no button, which is honest on a screen that cannot get
   *  there rather than a control that does nothing. */
  onOpenSolveSettings?: () => void;
}) {
  const state = identifyState({ field, typed: value, perFrameSolving, frameType });
  const p = state.proposal;
  const willWrite = objectCardPreview(state);

  return (
    <div className="min-w-0">
      <Field
        label="Target name"
        hint={
          "Names the folder and the files on disk, and is what the app writes " +
          "into the FITS OBJECT card. Leave it blank and a confident plate " +
          "solve fills OBJECT for you — but the folder is still `untargeted`, " +
          "because a name that can change between frames must never name a " +
          "directory."
        }
      >
        <input
          className="field"
          data-capture-field="target"
          placeholder={p && state.kind === "proposed" ? p.id : "Name this target"}
          value={value}
          readOnly={readOnly}
          aria-readonly={readOnly || undefined}
          aria-describedby="target-name-note"
          onChange={(e) => onChange(e.target.value)}
        />
      </Field>

      {/* The proposal, as a PROPOSAL: dimmed, chipped with its provenance, and
          one tap from becoming an ordinary editable string. Until it is
          adopted the field's value is still "". */}
      {p && state.adoptable && !readOnly && (
        <button
          type="button"
          className="tap mt-1.5 w-full min-h-[44px] flex items-center gap-2 rounded border border-line px-2 text-left hover:border-accent"
          data-capture-adopt={p.id}
          onClick={() => onChange(p.id)}
        >
          <span
            className={`text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded border ${
              p.source === "solve"
                ? "border-accent text-accent"
                : "border-line text-dim"
            }`}
          >
            {/* WORD, not colour: this app is used in the dark under a red
                filter, where "the accent one" is not a distinction. */}
            {p.source === "solve" ? "solved" : "mount says"}
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-dim">
              {p.id}
              {p.label !== p.id ? ` · ${p.label}` : ""}
            </span>
            <span className="block truncate text-[11px] text-faint">{p.describe}</span>
          </span>
          <span className="text-[11px] text-accent shrink-0">Use {p.id}</span>
        </button>
      )}

      <p id="target-name-note" className="text-[11px] text-dim mt-1.5 leading-snug">
        {state.note}
      </p>

      {state.fix && onOpenSolveSettings && (
        <button
          type="button"
          className="tap mt-1 min-h-[44px] text-[11px] text-accent underline underline-offset-2"
          onClick={onOpenSolveSettings}
        >
          {state.fix.label}
        </button>
      )}

      {state.warning && (
        <p className="text-[11px] text-warn mt-1.5 leading-snug flex gap-1">
          <Icon name="alert" size={12} />
          <span>{state.warning}</span>
        </p>
      )}

      {/* What is actually going into the file, when it is NOT the box above —
          the one case where the app fills a header card the operator did not
          type. Stated, because an OBJECT card nobody chose is exactly the kind
          of helpfulness that becomes a mystery three months into a project. */}
      {!value.trim() && willWrite && (
        <p className="text-[11px] text-dim mt-1.5 leading-snug">
          Files will carry <span className="text-ink">OBJECT = {willWrite}</span>,
          from the solve. The folder stays <span className="text-ink">untargeted</span>.
        </p>
      )}

      {readOnly && readOnlyReason && (
        <p className="text-[11px] text-dim mt-1.5 leading-snug">{readOnlyReason}</p>
      )}
    </div>
  );
}

export default TargetField;
