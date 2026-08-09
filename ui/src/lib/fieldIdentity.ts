// fieldIdentity.ts — "what is this target called", as a pure function (#182).
//
// The Capture screen used to answer that question with a text box and nothing
// else: if the operator did not type a name, the frames were filed under
// `untargeted/` and the FITS carried no OBJECT card at all. The rig has known
// the answer the whole time — every goto plate-solves — and threw it away.
//
// THE ONE RULE THIS FILE EXISTS TO ENCODE: the operator's string and the
// derived identification are two different values with two different
// lifetimes, and they are never merged. The typed string names the folder and
// keys the persisted frame counter (`hub._capture_path`); a derived name that
// changed between frame 3 and frame 4 — because the solve drifted onto a
// neighbour, or the mount was nudged — would split one night across two
// directories with two overlapping `0001…` runs and report nothing. So the
// derived name is a PROPOSAL: shown, offered, adoptable by a human, and never
// written into the field on its own.
//
// Pure and exported so the three states are plain assertions rather than a
// screenshot; `components/capture/TargetField.tsx` is a thin shell over it.

import type { FieldIdentification, PreviewField } from "../types";

export interface IdentifyInput {
  /** `preview.field` — absent when nothing has solved since the last slew. */
  field?: PreviewField | null;
  /** Exactly what the operator typed. Never trimmed away, never overwritten. */
  typed: string;
  /** `config.solve_saved_lights` — is per-frame plate solving switched on? */
  perFrameSolving: boolean;
  /** The frame type Single/Loop will shoot. A dark has no sky to identify. */
  frameType: string;
  /** Injected so age is testable without faking a clock. */
  nowMs?: number;
}

export interface FieldProposal {
  id: string;
  label: string;
  describe: string;
  sepArcmin: number;
  confident: boolean;
  runnerUp: string | null;
  /** `solve` = measured against the stars. `pointing` = THE MOUNT'S CLAIM. */
  source: "solve" | "pointing";
  ageS: number;
}

export interface IdentifyState {
  /** `typed` — the operator has said what this is; nothing may overwrite it.
   *  `proposed` — the sky named itself and the field is still empty.
   *  `none` — nothing is named, and `reason` says which thing is missing. */
  kind: "typed" | "proposed" | "none";
  /** The value the capture POST should carry: the typed string, verbatim. A
   *  proposal is NOT substituted here — adopting is a tap, not a default. */
  value: string;
  proposal: FieldProposal | null;
  /** The one line under the field. Always names a specific missing thing or
   *  carries a fact the operator cannot already see (a separation, a
   *  disagreement, an age) — never a restatement of the box above it. */
  note: string;
  /** Set when the fix is a setting the operator can go and change. */
  fix: { label: string; help: string } | null;
  /** May a tap put `proposal.id` into the box? */
  adoptable: boolean;
  /** Something is wrong with the evidence itself, in a sentence. */
  warning: string | null;
}

/** How far off centre still counts as "on it" for the confirmation wording. */
const ON_TARGET_ARCMIN = 30;

const SOLVE_HELP =
  "Plate-solving matches the stars in a saved frame against a catalogue, which " +
  "is what lets the app name the field for you. Turn on \"solve saved lights\" " +
  "in Settings to have every light frame solved as it is written.";

function ageSeconds(field: PreviewField, nowMs: number): number {
  return Math.max(0, Math.round(nowMs / 1000 - field.solved_at));
}

function toProposal(
  id: FieldIdentification, field: PreviewField, nowMs: number,
): FieldProposal {
  return {
    id: id.id,
    label: id.label,
    describe: id.describe,
    sepArcmin: id.sep_arcmin,
    confident: id.confident,
    runnerUp: id.runner_up,
    source: field.source,
    ageS: ageSeconds(field, nowMs),
  };
}

/** Ages a solve in words. Seconds matter here — a solve from ten seconds ago
 *  and one from forty minutes ago are different claims about the same sky. */
export function describeAge(ageS: number): string {
  if (ageS < 90) return `${Math.max(1, ageS)}s ago`;
  const min = Math.round(ageS / 60);
  if (min < 90) return `${min} min ago`;
  return `${Math.round(min / 60)}h ago`;
}

/** Anything that undermines the evidence, said out loud, or null. */
function evidenceWarning(field: PreviewField): string | null {
  // The condition that cost this rig a night. The AM5 has no brake and has been
  // found 50° from where it claimed; the plate is the truth and the mount's
  // readout is not, so when they disagree the app says so instead of quietly
  // agreeing with whichever one is on screen.
  if (field.pointing_disagrees_deg != null) {
    return `The mount reports a position ${field.pointing_disagrees_deg.toFixed(1)}° ` +
      `from where the last plate solve put it. Trust the solve.`;
  }
  if (field.catalog_degraded) {
    return "The deep-sky catalogue did not load, so this answer comes from the " +
      "64 built-in objects alone.";
  }
  return null;
}

export function identifyState(input: IdentifyInput): IdentifyState {
  const nowMs = input.nowMs ?? Date.now();
  const typed = input.typed;
  const hasTyped = typed.trim().length > 0;
  const base = {
    value: typed,
    proposal: null,
    fix: null,
    adoptable: false,
    warning: null,
  } as const;

  // A dark, bias or flat has no sky in it. Saying "not identified yet" there
  // would describe a fault that does not exist.
  if (input.frameType.toUpperCase() !== "LIGHT") {
    return {
      ...base,
      kind: hasTyped ? "typed" : "none",
      note: `A ${input.frameType.toLowerCase()} frame has no sky in it to identify.`,
    };
  }

  const field = input.field ?? null;

  if (!field) {
    return {
      ...base,
      kind: hasTyped ? "typed" : "none",
      note: input.perFrameSolving
        ? "Nothing has plate-solved since the last slew, so nothing has named " +
          "this field yet. Centring on a target with Go To solves, and so does " +
          "the next saved light."
        : "Naming the field automatically needs a plate solve. Every Go To " +
          "does one; turning on per-frame solving does one for each saved light.",
      fix: input.perFrameSolving
        ? null
        : { label: "Turn on plate solving for saved lights", help: SOLVE_HELP },
    };
  }

  const warning = evidenceWarning(field);

  if (!field.id) {
    // F2: a COMMON and CORRECT answer — mosaic panels, calibration fields, most
    // of the sky. It reads as a fact about the catalogue, not a failure.
    return {
      ...base,
      kind: hasTyped ? "typed" : "none",
      warning,
      note: field.source === "solve"
        ? "This field solved, and nothing in the catalogue lies inside it."
        : "The mount's reported position matches nothing in the catalogue.",
    };
  }

  const proposal = toProposal(field.id, field, nowMs);
  const aged = describeAge(proposal.ageS);
  // A pointing-derived answer is worded as what the MOUNT SAYS, never as what
  // the sky IS, everywhere it appears.
  const claim = proposal.source === "solve"
    ? `The sky here solves as ${proposal.id}`
    : `The mount reports it is on ${proposal.id}`;
  const hedge = proposal.confident
    ? ""
    : ` — though ${proposal.runnerUp ?? "another object"} in the same frame is a ` +
      "comparable match";

  if (hasTyped) {
    // THE OPERATOR'S STRING ALWAYS WINS, and the disagreement stays legible
    // beside it rather than being silently resolved either way.
    const same = typed.trim().toLowerCase() === proposal.id.trim().toLowerCase()
      || typed.trim().toLowerCase() === proposal.label.trim().toLowerCase();
    if (same) {
      return {
        ...base,
        kind: "typed",
        proposal,
        warning,
        note: proposal.sepArcmin <= ON_TARGET_ARCMIN
          ? `Confirmed by the plate solve ${aged} — ${proposal.sepArcmin.toFixed(1)}′ off frame centre.`
          : `The plate solve ${aged} agrees, but puts ${proposal.id} ` +
            `${proposal.sepArcmin.toFixed(1)}′ off frame centre.`,
      };
    }
    return {
      ...base,
      kind: "typed",
      proposal,
      adoptable: true,
      warning,
      note: `${claim}${hedge}. Your name is what gets written.`,
    };
  }

  return {
    ...base,
    kind: "proposed",
    proposal,
    adoptable: true,
    warning,
    note: proposal.source === "solve"
      ? `Identified from a plate solve ${aged} · ${proposal.sepArcmin.toFixed(1)}′ off frame centre${hedge}.`
      : `${claim}${hedge}. Nothing has solved to confirm it.`,
  };
}

/** What the FITS OBJECT card will end up saying, for the one line of UI that
 *  tells the operator what is about to be written to disk.
 *
 *  Mirrors `hub._object_cards` exactly, and is the only place the client makes
 *  that claim — a second half-remembered version of the adoption rule beside
 *  the field is how a screen ends up promising something the server does not do.
 */
export function objectCardPreview(s: IdentifyState): string {
  if (s.value.trim()) return s.value.trim();
  if (s.proposal && s.proposal.confident && s.proposal.source === "solve") {
    return s.proposal.id;
  }
  return "";
}
