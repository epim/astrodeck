// wizardModel.ts - Send to Flow Wizard: what the door hands over, what each
// step still has to ask, the one body GENERATE posts, and how the review reads
// the server's answer (#196; spec 2026-09-23 flows mosaic, Revision 2 ruling 4,
// D13, section 8 S6).
//
// PURE. No React, no store, no DOM: the stepped sheet (SendToWizardSheet.tsx)
// draws what this file decides, and wizardModel.test.ts grades the decisions
// without mounting anything. Both UIs mount that one sheet (D13), so every
// rule below exists once.
//
// THE GENERATOR LIVES ON THE SERVER, AND STAYS THERE. `POST /api/flows/wizard`
// (server `flows/wizard.py::generate_answer`) builds and saves the graph; this
// file only builds the BODY it is sent. The kind, the chip and the angle
// strings below are that module's own constants, mirrored, and
// wizardModel.test.ts reads wizard.py to hold each copy to its original: a
// client matching on "Mosaic " and a generator on "Mosaic" is the drift the
// server's header opens by describing.
//
// THE REVIEW PRINTS THE SERVER'S NUMBERS AND COMPUTES NONE. Subs and hours
// come from the compile route's `readouts` for the saved flow (spec 2.4: "every
// number comes from the server compile, never computed in the client"), read
// through framingApi's `compiledReadouts`, the one reader of that contract. A
// product of the answers typed here (rows x cols x filters x subs) would agree
// with the server on the easy case and disagree the first time a skip, a
// second capture stage or a per-pass count entered the plan, and the review is
// the screen the operator decides to RUN on.
//
// THE PREFILL TRAVELS AS ROUTE PARAMS IN #/next (`wizardParams` and
// `prefillFromParams`), so a reload or a shared link reopens the same wizard.
// The keys carry a `wz_` prefix because route params are one flat map for the
// whole sheet stack: the door underneath keeps its own keys beside them.

import { TARGET_ANGLES } from "../nodeDefs";
import { GRID_MAX, type RunReadouts } from "../framing/framingModel";
import { compiledReadouts } from "../framing/framingApi";
import type { FlowCompileResult, FlowUnmapped, FlowWizardAnswers } from "../../../lib/flowsApi";

// ------------------------------------------------------ the server's words
// Each mirrors `server/astrodeck/flows/wizard.py` (or `nodes.py`) character for
// character; wizardModel.test.ts parses that file and compares.

/** wizard.py `KIND_MOSAIC`: one TARGET laid out as a grid, loop wired. */
export const KIND_MOSAIC = "Mosaic";
/** wizard.py `KIND_DEEP_SKY`: one target, all night. A 1 x 1 framing, or a
 *  mosaic the operator chose to plan as one target, is this kind: the Mosaic
 *  kind refuses a grid of one panel. */
export const KIND_DEEP_SKY = "Deep-sky target";
/** wizard.py `OPT_WATCHDOG`. */
export const OPT_WATCHDOG = "HFR watchdog";

/** The automation chips a door's wizard sends: the NEW FLOW sheet's defaults
 *  (wizard.py `DEFAULT_OPTIONS`, Guiding and HFR watchdog) less Guiding, which
 *  this wizard asks as a step of its own and sends as `guiding`. Sending the
 *  chip as well would be two answers to one question, and the server refuses
 *  `guiding: false` beside a lit chip rather than pick one (`_door_guiding`). */
export const DOOR_OPTIONS: readonly string[] = [OPT_WATCHDOG];

/** wizard.py `UNGUIDED_EXPOSURE_DEFAULT`, seconds: the sub length an unguided
 *  lane is held to. A filter row longer than this on a lane with no GUIDE is
 *  refused by the generator (`_within_the_unguided_cap`), so the GUIDING step
 *  says so before GENERATE rather than after it. */
export const UNGUIDED_CAP_S = 30;

/** wizard.py `NO_OPTICS_REASON`, verbatim: why a mosaic cannot be laid out on
 *  a rig with no camera field. */
export const NO_OPTICS_REASON = "set the camera and focal length in Settings > Optics to plan a mosaic";

/** wizard.py `CYCLES_MAX`: the most subs of each filter a door may ask for. */
export const CYCLES_MAX = 10_000;

/** The two angles a mosaic may be laid out at (wizard.py `MOSAIC_ANGLES`,
 *  unpacked from `TARGET_ANGLES` there as here, so a fourth angle added to the
 *  node fails the parity test instead of being offered to a grid). */
export const MOSAIC_ANGLES = [TARGET_ANGLES[1], TARGET_ANGLES[2]] as const;
export type MosaicAngle = (typeof MOSAIC_ANGLES)[number];

/** The words each angle is called by on the sheet, the Target modal's. */
export const ANGLE_WORDS: Record<MosaicAngle, string> = {
  "Rotate to PA": "ROTATE TO",
  "Camera fixed at PA": "CAMERA FIXED AT",
};

/** Subs of each filter before anyone touches the count: the quick flow's
 *  `DEFAULT_SUBS`, so the two sheets that ask it start from one number. */
export const DEFAULT_CYCLES = 10;

// ------------------------------------------------------------------ steps

export type WizardStep = "target" | "framing" | "filters" | "guiding" | "review";

/** The stepped sheet's order (Revision 2 ruling 4). The stop condition and
 *  auto-resume steps are not built: they wait on #191 and #195. */
export const STEPS: readonly WizardStep[] = ["target", "framing", "filters", "guiding", "review"];

export const STEP_TITLE: Record<WizardStep, string> = {
  target: "TARGET",
  framing: "FRAMING",
  filters: "FILTERS",
  guiding: "GUIDING",
  review: "REVIEW",
};

// ---------------------------------------------------------------- prefill

/** What a door hands the wizard: the framing as the Sky FRAME or the Atlas
 *  holds it (#196 item 1). Every field says what ARRIVED; the sheet asks for
 *  whatever did not. */
export interface WizardPrefill {
  /** The target's name, "" when the door has none (a free-roam patch). */
  name: string;
  /** The centre as the TARGET node stores it ("00h 43m 15.0s",
   *  "+41 30' 00\""), "" when unknown. Sent as given: the generator parses it
   *  with the reader the run uses, and a framing's centre is not the
   *  catalogue's row for the name. */
  ra: string;
  dec: string;
  /** The angle mode the door framed at, or null when it had none (the Atlas
   *  holds a rotation and no mode). "Any angle" is a mode that holds no angle,
   *  so a mosaic that arrives with it is asked for one. */
  angleMode: (typeof TARGET_ANGLES)[number] | null;
  /** The PA the grid was framed at, degrees; null when the door had none. */
  paDeg: number | null;
  /** The grid. 1 x 1 is a single target. */
  rows: number;
  cols: number;
  /** Percent, as a TARGET holds it; null takes the generator's default
   *  (`framing.DEFAULT_OVERLAP`). */
  overlapPct: number | null;
  /** The panels the framing took out, in the TARGET's own `skip` syntax
   *  ("2-3, 1-1"); "" for none. Sent as typed: the generator reads it with
   *  the compile's own `parse_skip`. */
  skip: string;
  /** The camera field the door framed with, degrees at bin 1, or null. Not
   *  sent: the generator lays the grid out with the RIG's field, a fact the
   *  route injects (spec 1.8). Shown on the FRAMING step, and compared with
   *  the rig's field so a framing made with another camera says so. */
  fov: { xDeg: number; yDeg: number } | null;
}

/** A prefill that carries nothing: a wizard opened with no door asks every
 *  question and plans a single target. */
export const EMPTY_PREFILL: WizardPrefill = {
  name: "", ra: "", dec: "", angleMode: null, paDeg: null,
  rows: 1, cols: 1, overlapPct: null, skip: "", fov: null,
};

const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const blank = (s: string | null | undefined): boolean => (s ?? "").trim() === "";

/** The grid's panel count as drawn, skipped panels included. The review's
 *  panel count is the server's; this only decides whether the framing is a
 *  mosaic at all. */
function gridCells(p: WizardPrefill): number {
  return p.rows * p.cols;
}

/** Whether the door framed more than one panel. */
export function isMosaicFraming(p: WizardPrefill): boolean {
  return gridCells(p) > 1;
}

/** Whether the prefill carries a usable angle for a mosaic: one of the two
 *  mosaic modes AND a finite PA. Anything less is asked. */
export function angleArrived(p: WizardPrefill): boolean {
  return p.angleMode !== null && (MOSAIC_ANGLES as readonly string[]).includes(p.angleMode)
    && finite(p.paDeg);
}

// --------------------------------------------------------------- answers

/** What the operator types where the prefill had nothing, plus the three
 *  steps a door never answers (filters, counts, guiding). The sheet holds
 *  this; the body reads a prefilled value from the PREFILL and an asked one
 *  from here, so a field that arrived can never be overwritten by a blank. */
export interface WizardAnswers {
  name: string;
  ra: string;
  dec: string;
  /** The angle asked for a mosaic that arrived without one. */
  angleMode: MosaicAngle | null;
  /** Typed text; read by `paOf`. */
  pa: string;
  /** The operator took "plan it as one target" on a rig with no camera
   *  field (or chose to on any rig). */
  single: boolean;
  /** The FILTER CYCLE slot table, in wheel order ("L 60, R 60"), as
   *  cyclePlanRows' `toggleSlot` and `setSlotExposure` write it. */
  plan: string;
  /** What each ticked filter's exposure box holds while it is typed in,
   *  keyed by filter; absent for a box nobody has touched. The plan takes
   *  only a whole number of seconds (`setSlotExposure` leaves it alone
   *  otherwise), so a box cleared on the way from "60" to "180" would show
   *  empty over a plan still saying 60: `stepReason` refuses FILTERS while
   *  a ticked box holds anything the plan did not take. */
  drafts: Record<string, string>;
  /** Typed text; read by `cyclesOf`. */
  cycles: string;
  guiding: boolean;
}

/** The answers a sheet opens with. The angle question starts from what did
 *  arrive (a mode with no PA keeps its mode, a PA with "Any angle" keeps its
 *  number), so the operator finishes the angle rather than retyping it. No
 *  filter is ticked: which filters to shoot is the operator's answer, and a
 *  door knows nothing of it. Guiding starts on, as it does in both other
 *  sheets that ask it. */
export function initialAnswers(p: WizardPrefill): WizardAnswers {
  const mode = p.angleMode !== null && (MOSAIC_ANGLES as readonly string[]).includes(p.angleMode)
    ? (p.angleMode as MosaicAngle) : null;
  return {
    name: "", ra: "", dec: "",
    angleMode: mode,
    pa: finite(p.paDeg) ? String(p.paDeg) : "",
    single: false,
    plan: "",
    drafts: {},
    cycles: String(DEFAULT_CYCLES),
    guiding: true,
  };
}

/** A typed PA in degrees, or null for one that is not a finite number. */
export function paOf(text: string): number | null {
  const t = text.trim();
  if (t === "") return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
}

/** Typed subs of each filter as a whole number from 1 to `CYCLES_MAX`, or
 *  null. The generator's own reading (`checked_cycles`) refuses the rest. */
export function cyclesOf(text: string): number | null {
  const t = text.trim();
  if (!/^\d+$/.test(t)) return null;
  const n = Number(t);
  return n >= 1 && n <= CYCLES_MAX ? n : null;
}

/** The live rig as the wizard reads it: the camera field at bin 1 (null
 *  with no optics), from the same resolver the Target modal's MATCH CAMERA
 *  reads (`effectiveOptics` + `fovFromOptics`). */
export interface WizardRig {
  field: { xDeg: number; yDeg: number } | null;
}

/** The target the flow will carry: each field the door sent, else the one
 *  typed. Trimmed, as the generator trims. */
export function targetOf(p: WizardPrefill, a: WizardAnswers): { name: string; ra: string; dec: string } {
  return {
    name: (blank(p.name) ? a.name : p.name).trim(),
    ra: (blank(p.ra) ? a.ra : p.ra).trim(),
    dec: (blank(p.dec) ? a.dec : p.dec).trim(),
  };
}

/** Whether this answer plans a mosaic: a framing of more than one panel the
 *  operator has not chosen to plan as one target. */
export function plansMosaic(p: WizardPrefill, a: WizardAnswers): boolean {
  return isMosaicFraming(p) && !a.single;
}

/** The angle a mosaic is laid out at: the door's when it sent one, else the
 *  operator's; null while it is not both a mode and a finite PA. */
export function angleOf(p: WizardPrefill, a: WizardAnswers): { mode: MosaicAngle; paDeg: number } | null {
  if (angleArrived(p)) return { mode: p.angleMode as MosaicAngle, paDeg: p.paDeg as number };
  const pa = paOf(a.pa);
  return a.angleMode !== null && pa !== null ? { mode: a.angleMode, paDeg: pa } : null;
}

/** The filter rows the answer ticked, `[filter, seconds]`, read the way the
 *  generator reads them (`_ROW_RE`): a filter name, whitespace, whole
 *  seconds. cyclePlanRows writes nothing else. */
export function planRows(plan: string): Array<[string, number]> {
  const rows: Array<[string, number]> = [];
  for (const chunk of plan.split(",")) {
    const m = /^(\S+)\s+(\d+)$/.exec(chunk.trim());
    if (m) rows.push([m[1], Number(m[2])]);
  }
  return rows;
}

/** The ticked rows longer than the unguided cap, as "L 60 s". */
export function overCap(plan: string): string[] {
  return planRows(plan).filter(([, s]) => s > UNGUIDED_CAP_S).map(([f, s]) => `${f} ${s} s`);
}

// ------------------------------------------------------ what is missing

export const NEED_NAME = "Name the target: the frames are filed under its name.";
export const NEED_COORDS =
  "Type the target's RA and Dec: the run slews to the coordinates, never to a name.";
export const NEED_OPTICS =
  `This rig has no camera field, so the grid cannot be laid out: ${NO_OPTICS_REASON}, or plan it as one target.`;
export const NEED_ANGLE =
  "A mosaic is laid out at one camera angle: choose ROTATE TO or CAMERA FIXED AT, and type the PA in degrees.";
export const NEED_FILTER = "Tick at least one filter.";
export const NEED_CYCLES = `Set how many subs of each filter to take: a whole number from 1 to ${CYCLES_MAX}.`;

/** Why FILTERS waits on an exposure box, naming the filter. */
export function exposureReason(filter: string): string {
  return `Type ${filter}'s exposure in whole seconds, at least 1.`;
}

/** The first ticked filter whose exposure box holds text the plan did not
 *  take (not a whole number of seconds above 0), or null. */
export function badDraft(a: WizardAnswers): string | null {
  const ticked = new Set(planRows(a.plan).map(([f]) => f));
  for (const [f, text] of Object.entries(a.drafts)) {
    if (!ticked.has(f)) continue;
    const t = text.trim();
    if (!/^\d+$/.test(t) || Number(t) <= 0) return f;
  }
  return null;
}

/** Why the unguided cap stops this answer, naming the rows. */
export function capReason(rows: string[]): string {
  return `Unguided, a sub is held to ${UNGUIDED_CAP_S} s, and ${rows.join(", ")} `
    + `${rows.length === 1 ? "runs" : "run"} longer: turn guiding on, or shorten `
    + `${rows.length === 1 ? "it" : "them"} on FILTERS.`;
}

/** What `step` still needs before the sheet may move past it, in words, or
 *  null when it has everything. The ONE rule for the NEXT lock and the line
 *  under each step, so the reason a press refuses is the reason on screen.
 *
 *  Only a question that was ASKED can be missing: a field the door sent is
 *  never asked again, and a 1 x 1 framing is never asked for an angle (the
 *  generator plans a single target at any angle; the FRAMING step says so). */
export function stepReason(
  step: WizardStep, p: WizardPrefill, a: WizardAnswers, rig: WizardRig,
): string | null {
  switch (step) {
    case "target": {
      const t = targetOf(p, a);
      if (t.name === "") return NEED_NAME;
      if (t.ra === "" || t.dec === "") return NEED_COORDS;
      return null;
    }
    case "framing": {
      if (!plansMosaic(p, a)) return null;
      // The field first, as the generator checks it first (`_mosaic_params`):
      // with none, the angle would be asked for a grid that cannot exist.
      if (rig.field === null) return NEED_OPTICS;
      return angleOf(p, a) === null ? NEED_ANGLE : null;
    }
    case "filters": {
      if (planRows(a.plan).length === 0) return NEED_FILTER;
      const bad = badDraft(a);
      if (bad !== null) return exposureReason(bad);
      return cyclesOf(a.cycles) === null ? NEED_CYCLES : null;
    }
    case "guiding": {
      if (a.guiding) return null;
      const over = overCap(a.plan);
      return over.length > 0 ? capReason(over) : null;
    }
    case "review":
      return null;
  }
}

/** The first step with something missing, or null when GENERATE may be
 *  pressed. The review refuses to generate over a gap an earlier step still
 *  has, and names the step. */
export function firstGap(
  p: WizardPrefill, a: WizardAnswers, rig: WizardRig,
): { step: WizardStep; reason: string } | null {
  for (const step of STEPS) {
    const reason = stepReason(step, p, a, rig);
    if (reason !== null) return { step, reason };
  }
  return null;
}

// ------------------------------------------------------------------ body

/** THE ONE BODY `POST /api/flows/wizard` receives (server `FlowWizardBody`),
 *  from the prefill and the answers.
 *
 *  A key is left OUT, never sent as null or "", when there is nothing to say
 *  for it: the route reads a missing answer as "not given", and wizard.py's
 *  header explains why every door answer must be inert then. The keys come
 *  in the order the recorded answer's request lists them
 *  (server/tests/fixtures/wizard_mosaic_answer.json), which grades this
 *  function byte for byte.
 *
 *  A MOSAIC sends the grid, the angle and the skipped panels, and the kind
 *  that takes them; ONE TARGET (a 1 x 1 framing, or a mosaic planned as one)
 *  sends none of them, because the generator refuses a grid answer with any
 *  other kind (`_mosaic_answers_belong`), and the coordinates are then the
 *  framing's centre. */
export function wizardBody(p: WizardPrefill, a: WizardAnswers): FlowWizardAnswers {
  const t = targetOf(p, a);
  const mosaic = plansMosaic(p, a);
  const body: FlowWizardAnswers = {
    kind: mosaic ? KIND_MOSAIC : KIND_DEEP_SKY,
    options: [...DOOR_OPTIONS],
    target: t.name,
  };
  if (t.ra !== "") body.ra = t.ra;
  if (t.dec !== "") body.dec = t.dec;
  if (mosaic) {
    body.rows = p.rows;
    body.cols = p.cols;
    if (finite(p.overlapPct)) body.overlap_pct = p.overlapPct;
    const angle = angleOf(p, a);
    if (angle) {
      body.angle_mode = angle.mode;
      body.pa_deg = angle.paDeg;
    }
    if (!blank(p.skip)) body.skip = p.skip;
  }
  if (planRows(a.plan).length > 0) {
    body.cycle_plan = a.plan;
    const cycles = cyclesOf(a.cycles);
    if (cycles !== null) body.cycles = cycles;
  }
  body.guiding = a.guiding;
  return body;
}

// ------------------------------------------------------ the route params

/** The route param each prefill field travels in (#/next). */
export const WIZARD_PARAMS = {
  name: "wz_name", ra: "wz_ra", dec: "wz_dec", angle: "wz_angle", pa: "wz_pa",
  rows: "wz_rows", cols: "wz_cols", overlap: "wz_overlap", skip: "wz_skip",
  fovX: "wz_fovx", fovY: "wz_fovy",
} as const;

/** The prefill as route params. A field with nothing to say is left out,
 *  so a link carries only what the door knew. */
export function wizardParams(p: WizardPrefill): Record<string, string> {
  const out: Record<string, string> = {};
  const put = (k: string, v: string | null) => { if (v !== null && v !== "") out[k] = v; };
  put(WIZARD_PARAMS.name, p.name.trim());
  put(WIZARD_PARAMS.ra, p.ra.trim());
  put(WIZARD_PARAMS.dec, p.dec.trim());
  put(WIZARD_PARAMS.angle, p.angleMode);
  put(WIZARD_PARAMS.pa, finite(p.paDeg) ? String(p.paDeg) : null);
  put(WIZARD_PARAMS.rows, String(p.rows));
  put(WIZARD_PARAMS.cols, String(p.cols));
  put(WIZARD_PARAMS.overlap, finite(p.overlapPct) ? String(p.overlapPct) : null);
  put(WIZARD_PARAMS.skip, p.skip.trim());
  if (p.fov && finite(p.fov.xDeg) && finite(p.fov.yDeg)) {
    put(WIZARD_PARAMS.fovX, String(p.fov.xDeg));
    put(WIZARD_PARAMS.fovY, String(p.fov.yDeg));
  }
  return out;
}

/** `params` without any wizard key: what a door's own route keeps when the
 *  wizard is pushed over it. */
export function withoutWizardParams(params: Record<string, string>): Record<string, string> {
  const keys = new Set<string>(Object.values(WIZARD_PARAMS));
  return Object.fromEntries(Object.entries(params).filter(([k]) => !keys.has(k)));
}

const numParam = (v: string | undefined): number | null => {
  if (v === undefined || v.trim() === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

/** A grid side from a param: a whole number from 1 to GRID_MAX, else 1. A
 *  garbled link plans one panel rather than a grid nobody drew; the FRAMING
 *  step shows what arrived either way. */
const sideParam = (v: string | undefined): number => {
  const n = numParam(v);
  return n !== null && Number.isInteger(n) && n >= 1 && n <= GRID_MAX ? n : 1;
};

/** The prefill a route carries. Total: every field a link leaves out or
 *  garbles reads as "did not arrive", which the sheet then asks. */
export function prefillFromParams(params: Record<string, string>): WizardPrefill {
  const angle = params[WIZARD_PARAMS.angle];
  const fx = numParam(params[WIZARD_PARAMS.fovX]);
  const fy = numParam(params[WIZARD_PARAMS.fovY]);
  return {
    name: params[WIZARD_PARAMS.name] ?? "",
    ra: params[WIZARD_PARAMS.ra] ?? "",
    dec: params[WIZARD_PARAMS.dec] ?? "",
    angleMode: angle !== undefined && (TARGET_ANGLES as readonly string[]).includes(angle)
      ? (angle as (typeof TARGET_ANGLES)[number]) : null,
    paDeg: numParam(params[WIZARD_PARAMS.pa]),
    rows: sideParam(params[WIZARD_PARAMS.rows]),
    cols: sideParam(params[WIZARD_PARAMS.cols]),
    overlapPct: numParam(params[WIZARD_PARAMS.overlap]),
    skip: params[WIZARD_PARAMS.skip] ?? "",
    fov: fx !== null && fy !== null && fx > 0 && fy > 0 ? { xDeg: fx, yDeg: fy } : null,
  };
}

// -------------------------------------------------------- framing words

const deg1 = (v: number) => v.toFixed(1);
const deg2 = (v: number) => v.toFixed(2);

/** The camera field in words: "1.35 x 0.90 deg". */
export function fieldWords(f: { xDeg: number; yDeg: number }): string {
  return `${deg2(f.xDeg)} x ${deg2(f.yDeg)} deg`;
}

/** The angle in words, the Target modal's strip: "rotate to 30.0 deg". */
export function angleWords(mode: MosaicAngle, paDeg: number): string {
  return `${ANGLE_WORDS[mode].toLowerCase()} ${deg1(paDeg)} deg`;
}

/** How far apart two fields may be and still be one camera: 1%, well under
 *  a binning or reducer change (a factor of 2, or 0.8) and over the rounding
 *  the door and the rig's resolver may each apply. */
export const FIELD_TOLERANCE = 0.01;

/** When the rig's field is not the field the door framed with, the sentence
 *  that says so; null when they agree or either is unknown. The generator
 *  lays the grid out with the rig's field (spec 1.8), so a framing made with
 *  another camera tiles differently from what the door showed. */
export function fieldChanged(p: WizardPrefill, rig: WizardRig): string | null {
  if (!p.fov || !rig.field) return null;
  const off = (a: number, b: number) => Math.abs(a - b) > FIELD_TOLERANCE * Math.max(a, b);
  if (!off(p.fov.xDeg, rig.field.xDeg) && !off(p.fov.yDeg, rig.field.yDeg)) return null;
  return `This was framed with a ${fieldWords(p.fov)} field, and the rig's camera now sees `
    + `${fieldWords(rig.field)}: the wizard lays the grid out with the rig's field, so the panels `
    + "will not be the ones framed. EDIT FRAMING to see them at this field.";
}

// ------------------------------------------------------------ the answer

/** What `POST /api/flows/wizard` answered that the review reads: the saved
 *  flow's id and name, its TARGET blocks, and the generator's notes (why a
 *  mosaic became one target, a name the catalogue does not know). */
export interface SavedFlow {
  id: string;
  name: string;
  targets: Array<{ id: string; name: string }>;
  notes: string[];
}

/** The saved flow in an answer, or why it cannot be read. An answer with no
 *  id is a flow nobody can open or run, so it is a failure, not a review. */
export function savedFlowOf(v: unknown): { ok: true; flow: SavedFlow } | { ok: false; why: string } {
  if (v === null || typeof v !== "object" || Array.isArray(v)) {
    return { ok: false, why: "the server's answer is not a flow" };
  }
  const o = v as Record<string, unknown>;
  if (typeof o.id !== "string" || o.id === "") return { ok: false, why: "the server returned a flow with no id" };
  const graph = o.graph as { nodes?: unknown } | undefined;
  const nodes = Array.isArray(graph?.nodes) ? graph!.nodes as Array<Record<string, unknown>> : [];
  const targets = nodes
    .filter((n) => n && n.type === "target" && typeof n.id === "string")
    .map((n) => {
      const params = (n.params ?? {}) as Record<string, unknown>;
      return { id: n.id as string, name: typeof params.name === "string" ? params.name : "" };
    });
  const notes = Array.isArray(o.notes) ? o.notes.filter((s): s is string => typeof s === "string") : [];
  return {
    ok: true,
    flow: { id: o.id, name: typeof o.name === "string" ? o.name : "", targets, notes },
  };
}

// ------------------------------------------------------------ the review

/** Hours with at most two decimals, trailing zeros dropped (framingModel's
 *  RUN section spells them the same way): 0.67, 3.33, 18. A unit, not a sum:
 *  the seconds are the server's. */
export function hoursWords(seconds: number): string {
  return String(Number((seconds / 3600).toFixed(2)));
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** One TARGET block's numbers on the review, each read off the compile's
 *  `readouts` for it: the subs a panel owes and all of them, and the hours
 *  per panel and in all. A single target is one panel, so it says its subs
 *  and hours once. NOTHING HERE MULTIPLIES: the named mutant "review
 *  computes subs locally" is this function taking its subs from the answers
 *  instead, and wizardModel.test.ts feeds it a compile whose numbers no
 *  product of the answers can make. */
export function reviewLines(r: RunReadouts): string[] {
  if (r.panels > 1) {
    return [
      `${plural(r.panels, "panel", "panels")}: ${plural(r.subs_per_panel, "sub", "subs")} per panel, `
        + `${r.subs_total} in all`,
      `${hoursWords(r.panel_s)} h per panel, ${hoursWords(r.total_s)} h in all`,
    ];
  }
  return [
    `${plural(r.subs_total, "sub", "subs")} on one panel`,
    `${hoursWords(r.total_s)} h in all`,
  ];
}

/** What the review prints for one block: its lines, or why it has none. */
export type ReviewBlock =
  | { id: string; name: string; ok: true; lines: string[] }
  | { id: string; name: string; ok: false; why: string };

/** The compile answer's numbers for every TARGET block of the saved flow. */
export function reviewBlocks(flow: SavedFlow, compiled: FlowCompileResult | null): ReviewBlock[] {
  return flow.targets.map((t) => {
    const read = compiledReadouts(compiled, t.id);
    if (read === null) {
      return { id: t.id, name: t.name, ok: false, why: "the compile gives no numbers for this block; the doctor's notes below say why" };
    }
    if (!read.ok) return { id: t.id, name: t.name, ok: false, why: `the compile's numbers cannot be read: ${read.why}` };
    return { id: t.id, name: t.name, ok: true, lines: reviewLines(read.value) };
  });
}

/** The compile answer's lists, each read as a list whatever arrived: a key
 *  an older server does not send is an empty list, never a crash. */
function listOf<T>(v: unknown): T[] {
  return Array.isArray(v) ? (v as T[]) : [];
}

/** The doctor's issues, the broken wires and the losses (what the engine
 *  will not carry; a `note` is honoured elsewhere and is no loss). */
export function reviewFindings(compiled: FlowCompileResult | null): {
  issues: Array<{ text: string; level: string }>;
  structural: string[];
  losses: FlowUnmapped[];
} {
  const c = (compiled ?? {}) as Partial<FlowCompileResult>;
  return {
    issues: listOf<{ text: string; level: string }>(c.issues)
      .filter((i) => i && typeof i.text === "string"),
    structural: listOf<string>(c.structural).filter((s) => typeof s === "string"),
    losses: listOf<FlowUnmapped>(c.unmapped).filter((u) => u && u.level !== "note"),
  };
}

export const RUN_WAITING = "Waiting for the server's check of this flow.";

/** Why RUN is locked on this compile, or null when the compile clears it.
 *
 *  A DANGER from the doctor or A LOSS (a part of the graph the engine will
 *  not honour) locks RUN with its reason (#196: "locked with its reason when
 *  the doctor reports a danger or a loss"): the flow is saved, and the editor
 *  is where either is fixed or accepted, one press away on OPEN IN EDITOR. A
 *  broken wire locks it too, since `/run` refuses one. Warnings and notes do
 *  not: the generator's bar is "note level or better", and a warning is the
 *  doctor's advice, which the review prints. `failed` is the compile's own
 *  failure, which leaves nothing to decide on. The capability and camera
 *  locks are the RUN button's own (`runBlockedReason`), applied by the
 *  sheet. */
export function runLock(compiled: FlowCompileResult | null, failed: string | null): string | null {
  if (failed !== null) return `The server could not check this flow (${failed}), so RUN has nothing to decide on.`;
  if (compiled === null) return RUN_WAITING;
  const f = reviewFindings(compiled);
  if (f.structural.length > 0) {
    return `This flow has a broken wire: ${f.structural[0]}. OPEN IN EDITOR to fix it.`;
  }
  const danger = f.issues.find((i) => i.level === "danger");
  if (danger) return `The doctor reports a danger: ${danger.text}. OPEN IN EDITOR to fix it.`;
  const loss = f.losses[0];
  if (loss) {
    return `Part of this flow will not run as drawn: ${loss.detail}. OPEN IN EDITOR to fix it or accept it there.`;
  }
  return null;
}
