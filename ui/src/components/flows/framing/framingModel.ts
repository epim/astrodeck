// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// framingModel.ts - the Target modal's pure model (#189 S4 item 1, S5 run
// mode; spec 2026-09-23 flows mosaic, 2.3-2.7, 3.1, 3.2 and A.2).
//
// PURE: no React, no store, no DOM, no fetch, no clock. The sheet
// (TargetFramingSheet.tsx) owns every one of those and hands this file what
// they said: the node's params, the rig block and the readouts the compile
// answered, the route's answer and the key it was asked for, the progress
// block, the live run's group, whether the page is offline, and the time.
// Everything a test needs to hold the modal to the spec is therefore
// reachable without a browser.
//
// WHAT THIS FILE REFUSES TO KNOW. Where the telescope points is the SERVER'S
// (framing.py is canonical for slew targets, and `to_plan` derives the panels
// from the node's params on every compile), so nothing here computes a panel
// centre; the sheet draws them from `POST /api/framing/mosaic`, or from the
// client mirror in lib/framing.ts while a drag is in flight. The RUN numbers
// are the compile's (spec 2.4: "every number comes from the server compile,
// never computed in the client"); `runLines` only spells them. Whether a
// re-frame keeps its counts is `reframe_carry`'s, asked through the route.
//
// The rig block and the readouts arrive from the server as JSON, so their
// types are declared HERE, structurally, rather than imported from a module
// that would pull the store into a pure file. The sky angle is the exception:
// `status.sky_angle` already has its one type, types.ts `SkyAngleRecord`,
// which a server test holds to the record the solve writes, and types.ts
// imports nothing, so the model takes a Pick of it and declares no copy
// (#408). Every reader treats a missing or non-finite number as "unknown",
// never as zero: a hop of 0 s is not "not measured", and a field of 0 x 0 deg
// is not a camera.
import { NODE_DEFS, TARGET_ANGLES, targetAngle } from "../nodeDefs";
import { DEFAULT_OVERLAP, mosaicTotalFov, wrapRaHours } from "../../../lib/framing";
import { panelStateOf, type PanelRunSource, type PanelRunState } from "../flowRunState";
import type { PanelState } from "../../atlas/PanelLayer";
import type { SequenceGroupState, SkyAngleRecord } from "../../../types";

// ------------------------------------------------------------------ types

export type Params = Record<string, string | number>;
export type TargetAngle = (typeof TARGET_ANGLES)[number];

/** What the compile route says about the live rig: its `rig` key, server
 *  `flows/readouts.py` `rig_readout`, field for field. That is the route's
 *  wording of `RigFacts`, NOT RigFacts itself: the hop travels as `hop_s`,
 *  with `hop_measured` beside it, so a reader of `hop_cost_s` would find
 *  nothing and print "not measured" under a measured hop. Every "unknown" is
 *  null (0 samples for the hop), so a route with no rig hands over nulls and
 *  every reader here says nothing rather than "no rotator". Held to the
 *  route's recorded answer (server/tests/fixtures/flow_readouts_m31.json) by
 *  framingModel.test.ts. */
export interface RigBlock {
  /** The imaging camera's field at BIN 1, [x_deg, y_deg]; null while the
   *  optics are unknown, never [0, 0]. */
  fov_deg: [number, number] | null;
  /** Provenance for display: "profile Refractor, matched 2026-09-23". */
  fov_from: string;
  /** Whether the ACTIVE profile has a rotator; null when nobody knows. */
  has_rotator: boolean | null;
  /** The MEASURED mean cost of one panel hop, seconds, and how many hops it
   *  is the mean of: null, 0 and false until a hop has been timed. Never the
   *  engine's seed. */
  hop_s: number | null;
  hop_samples: number;
  hop_measured: boolean;
}

/** What the model reads of `status.sky_angle`, the angle the last
 *  imaging-camera solve measured: a Pick of types.ts `SkyAngleRecord`, whose
 *  keys test_types_mirror_status.py holds to server
 *  `sky_angle.note_solved_rotation`. A Pick and not a copy (#408): S4's model
 *  declared its own record, looser than the server's and held to nothing, and
 *  the sheet's full record compiled against it only because it was looser.
 *  The keys are exactly the ones read below (`rec.x`), which
 *  framingModel.test.ts checks, so a test can build the five it needs.
 *  `exposed_at` joined in S7 (#439): the USE MEASURED line ages the frame. */
export type MeasuredAngle = Pick<SkyAngleRecord, "exposed_at" | "pa_deg" | "pier_side" | "solved_at" | "source">;

/** The compile's numbers for one block's RUN section (spec 2.4): one entry
 *  of the route's `readouts` key, server `flows/readouts.py` `_block`, field
 *  for field. The server computes every one of them ("every number comes
 *  from the server compile, never computed in the client"); `runLines`
 *  spells them and computes none. Held to the route's recorded answer
 *  (server/tests/fixtures/flow_readouts_m31.json) by framingModel.test.ts. */
export interface RunReadouts {
  node_id: string;
  /** "rotate" with the loop wire, "sequential" (panel-first) without it,
   *  "single" for a block of one panel, which makes no hops. */
  mode: "rotate" | "sequential" | "single";
  /** Panels the block shoots: the grid less its skipped panels. */
  panels: number;
  /** Steps each panel owes (a FILTER CYCLE's ticked slots, CAPTUREs): the
   *  spec's "7 filters". */
  steps: number;
  /** Passes a panel owes, its most-served step's count over its per-pass
   *  count rounded up: the spec's "45". Null when the block shoots in
   *  blocks, which has no pass. */
  rounds: number | null;
  /** One panel's subs, and every panel's. */
  subs_per_panel: number;
  subs_total: number;
  /** One pass's shutter seconds (null without a pass), one panel's, and
   *  every panel's. */
  pass_s: number | null;
  panel_s: number;
  total_s: number;
  /** The block's own visit settings; null for a single target. */
  passes: number | null;
  visit_min_s: number | null;
  /** Passes one visit makes (spec 5.3's bound), null unless the panels
   *  rotate; visits per panel and in all, null for a single target. */
  visit_passes: number | null;
  visits_per_panel: number | null;
  visits_total: number | null;
  /** The hop the idle is priced with (measured, or the engine's seed when
   *  `hop_measured` is false); null for a single target. */
  hop_s: number | null;
  /** Whether `hop_s` was measured. NULL FOR A SINGLE TARGET, as `hop_s` is:
   *  a block that makes no hop has no hop to have measured, and the server
   *  sends null there (readouts.py `_block`, held by
   *  test_a_single_target_has_no_group_numbers). Typed `boolean` alone, the
   *  reader refused every single target's answer, and the RUN section of
   *  five of the eight shipped Examples, the single-target ones, said its
   *  numbers could not be read (#409).
   *  `runLines` reads the rig block's flag, never this one. */
  hop_measured: boolean | null;
  /** Shutter over shutter plus hops, a fraction. PRESENT ONLY WITH A
   *  MEASURED HOP (absent, not null), so a seed never reads as a
   *  measurement. */
  efficiency?: number;
  /** The pre-flip idle of spec 5.7 / A.4 in seconds, 0 for none; null when
   *  the plan does not flip or for a single target, so there is no line.
   *  Not site data. */
  preflip_idle_s: number | null;
  /** `framing.angle_tolerance_deg` for this layout (A.2 combined with
   *  convergence); null for a single target, which has no seams. */
  angle_tolerance_deg: number | null;
  /** How the run refocuses (spec 5.6 step 6): on temperature, every
   *  `autofocus_every` frames, or once, at the first panel. */
  focus: "temperature" | "frames" | "once";
  autofocus_every: number;
  refocus_delta_c: number;
}

/** `reframe_carry`'s verdict, as the route answers it (spec 3.3). */
export interface ReframeAnswer {
  carry: boolean;
  /** Null where no move is measured: identity, grid, angle. */
  max_move_deg: number | null;
  threshold_deg: number;
  reason: "identity" | "grid" | "angle" | "unchanged" | "move";
}

/** What the sheet heard back from `POST /api/framing/mosaic`, tagged with
 *  the key of the request it answers (`requestKey`). `ok` false is a request
 *  that failed; it still answers only the spec it was asked for. */
export interface PanelAnswer {
  key: string;
  ok: boolean;
  reframe?: ReframeAnswer | null;
}

/** The two things the reframe question reads from a progress route block
 *  (lib/flowsApi `FlowProgressBlock`): what the shot panels banked, and what
 *  the skipped ones still hold. */
export interface ProgressBlockLike {
  banked: number;
  skipped?: ReadonlyArray<{ banked: number }>;
}

// ------------------------------------------------------ the draft and patch

/** The params the modal edits, in the order the sheet's sections show them.
 *  `counts` and `frameAnchor` are not here: the first is not offered
 *  (Revision 2 ruling 2) and the second is written by the server at save
 *  (ruling 3), so a draft that carried them could only ever write back what
 *  it read, or worse, an anchor the server did not choose. */
export const DRAFT_KEYS = [
  "name", "ra", "dec",
  "rows", "cols", "overlap", "fovX", "fovY", "fovFrom",
  "angle", "rotation",
  "skip", "passes", "minVisit", "order",
  "centerTol", "centerTries", "ifNotCentred",
] as const;
export type DraftKey = (typeof DRAFT_KEYS)[number];

/** The modal's working copy. Values stay as the controls hold them (text a
 *  field is being typed into is still text); they are coerced once, at the
 *  patch, by the rule `flowsSetParam` applies to every inspector edit. */
export type FramingDraft = Record<DraftKey, string | number>;

const TARGET_DEFAULTS: Params = NODE_DEFS.target.params;

/** One param as `flowsSetParam` would store it (flowsSlice.ts): COERCION
 *  KEYS OFF THE TYPE OF THE MISSING-KEY DEFAULT. A numeric default makes the
 *  value `parseFloat(raw)` when that is a FINITE number, and the default
 *  itself otherwise; any other key (a string default, or `angle`, which has
 *  none) stores the text as typed. Copied rule for rule, including
 *  `parseFloat`'s leniency ("12abc" is 12), because the modal and the
 *  inspector write one node: if they coerced differently, the same keystrokes
 *  would save two graphs. framingModel.test.ts holds this against the real
 *  slice action, and coerceParamFinite.test.ts against the slice's rule.
 *
 *  FINITE, NOT MERELY "NOT NaN" (#358). `parseFloat` reads "Infinity",
 *  "-Infinity" and "1e999" as an infinity, which is not NaN, and JSON writes
 *  an infinity as null. S5 fixed the store and left this copy on NaN, so the
 *  patch held an infinity the store then wrote as the default, the draft
 *  compile `framedGraph` builds from the patch sent null for it, and a node
 *  already at the default counted as framed for a change the store declined
 *  to write. S7 made the two agree. */
export function coerceParam(key: string, raw: string | number): string | number {
  const text = typeof raw === "number" ? String(raw) : raw;
  const base = TARGET_DEFAULTS[key];
  if (typeof base === "number") {
    const n = parseFloat(text);
    return Number.isFinite(n) ? n : base;
  }
  return text;
}

/** The value a key MEANS on a stored node: its own, the missing-key default
 *  when it has none, and for `angle` the one the server derives from
 *  `rotation` (`targetAngle`). */
function effective(params: Params, key: DraftKey): string | number {
  if (key === "angle") return targetAngle(params);
  const own = params[key];
  return own === undefined ? TARGET_DEFAULTS[key] : own;
}

/** A draft of a TARGET node's params (spec 3.1). A key the node does not
 *  carry reads as its missing-key default, which is what the compile reads
 *  it as, so the modal opens on the meaning the run would have. */
export function draftFromParams(params: Params): FramingDraft {
  const out = {} as FramingDraft;
  for (const key of DRAFT_KEYS) out[key] = effective(params, key);
  return out;
}

/** The smallest patch that makes a node mean what the draft says: each key
 *  coerced as `flowsSetParam` coerces it, and only the keys whose coerced
 *  value differs from what the node means now. An untouched draft is an
 *  empty patch, so opening the modal and pressing DONE writes nothing and
 *  never marks the flow dirty; and a key the node never carried is not
 *  written just because the modal showed its default. */
export function framingPatch(params: Params, draft: FramingDraft): Params {
  const patch: Params = {};
  for (const key of DRAFT_KEYS) {
    const next = coerceParam(key, draft[key]);
    if (next !== coerceParam(key, effective(params, key))) patch[key] = next;
  }
  return patch;
}

// ---------------------------------------------------- reading the draft

function numOf(draft: FramingDraft, key: DraftKey): number {
  const v = coerceParam(key, draft[key]);
  return typeof v === "number" ? v : NaN;
}

/** A field of the draft as text, read as the server reads a TARGET's text
 *  (S7 orchestrator ruling 6, server `identity._typed` and `compile._text`):
 *  text trimmed of Python's whitespace (`pyStrip`), a finite number as its
 *  text, 0 included, and anything else (null, a bool, NaN, an infinity, a
 *  list) blank. Until S7 this was `String(v).trim()`, so the number 0 was
 *  typed here and blank on the server, and trim() kept a lone NEL or U+001C
 *  the server strips and stripped a BOM the server keeps: one block, placed
 *  two ways (#387's residual). `true` and NaN were typed text on both sides
 *  then; ruling 6 makes them blank on both. */
function textOf(draft: FramingDraft, key: DraftKey): string {
  const v: unknown = draft[key];
  if (typeof v === "string") return pyStrip(v);
  if (typeof v === "number" && Number.isFinite(v)) return String(v);
  return "";
}

/** One side of the grid, as compile.py `_grid_dim` reads the stored number:
 *  a whole number of at least 1, else 1. */
function gridDim(v: number): number {
  return Number.isInteger(v) && v >= 1 ? v : 1;
}

/** The grid the compile will read once the draft is written. */
export function gridOf(draft: FramingDraft): { rows: number; cols: number } {
  return { rows: gridDim(numOf(draft, "rows")), cols: gridDim(numOf(draft, "cols")) };
}

/** The grid's upper bound on either side (`MosaicSpecIn`, `GRID_MAX`). */
export const GRID_MAX = 10;

/** The draft's angle mode, derived from `rotation` when it holds none. */
export function angleOf(draft: FramingDraft): string {
  const a = textOf(draft, "angle");
  return a !== "" ? a : targetAngle({ rotation: draft.rotation });
}

/** What the field LOOKS like, as server `save_rules.block_shape` reads it:
 *  the overlap as a fraction clamped to [0, 0.5] (a non-finite one is the
 *  missing-key overlap, `lib/framing.ts`'s `DEFAULT_OVERLAP`, the one overlap
 *  every framing starts from, spec 2.4), the angle the grid is laid out at
 *  (null for any angle, or a negative rotation), and the field (0 unless
 *  finite and positive). */
export interface Layout {
  rows: number;
  cols: number;
  overlap: number;
  rotation_deg: number | null;
  fov_x: number;
  fov_y: number;
}

export function layoutOf(draft: FramingDraft): Layout {
  const { rows, cols } = gridOf(draft);
  const pct = numOf(draft, "overlap");
  const overlap = Math.min(0.5, Math.max(0, Number.isFinite(pct) ? pct / 100 : DEFAULT_OVERLAP));
  const rot = numOf(draft, "rotation");
  const rotation = Number.isFinite(rot) ? rot : -1;
  const field = (k: DraftKey) => {
    const v = numOf(draft, k);
    return Number.isFinite(v) && v > 0 ? v : 0;
  };
  return {
    rows, cols, overlap,
    rotation_deg: angleOf(draft) === "Any angle" || rotation < 0 ? null : rotation,
    fov_x: field("fovX"), fov_y: field("fovY"),
  };
}

// ------------------------------------------ coordinates (preview only)
//
// A mirror of server `catalog/coords.py` parse_ra / parse_dec, enough to lay
// out the preview and to ask the route. THE SERVER'S READING IS CANONICAL:
// `to_plan` parses the stored text itself, so a text this mirror cannot read
// only costs the preview, never the run. One known difference: the server's
// `\d` and `float()` read any Unicode decimal digit, and this mirror reads
// ASCII digits only (#359).

/** Python's str.isspace() set, which its `\s` and strip() use. Built from
 *  code points so this source stays ASCII and every member is visible. */
const PY_SPACE_CODES = [
  0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x1c, 0x1d, 0x1e, 0x1f, 0x20, 0x85, 0xa0,
  0x1680, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007,
  0x2008, 0x2009, 0x200a, 0x2028, 0x2029, 0x202f, 0x205f, 0x3000,
];
const PY_SPACE = new Set(PY_SPACE_CODES);
const WS = PY_SPACE_CODES.map((c) => String.fromCharCode(c)).join("");

/** Python's `str.strip()`: JavaScript's trim() strips a BOM that Python
 *  keeps and keeps the four information separators Python strips. */
function pyStrip(s: string): string {
  let a = 0;
  let b = s.length;
  while (a < b && PY_SPACE.has(s.charCodeAt(a))) a++;
  while (b > a && PY_SPACE.has(s.charCodeAt(b - 1))) b--;
  return s.slice(a, b);
}

/** coords.py `_TYPOGRAPHIC`: primes, curly quotes, dashes and the no-break
 *  space mapped to their ASCII meaning before parsing. */
const TYPOGRAPHIC: ReadonlyMap<string, string> = new Map(([
  [0x2032, "'"], [0x2033, '"'], [0x2019, "'"], [0x201d, '"'], [0x2018, "'"],
  [0x201c, '"'], [0x2212, "-"], [0x2013, "-"], [0x2014, "-"], [0xa0, " "],
] as [number, string][]).map(([c, a]) => [String.fromCharCode(c), a] as [string, string]));

function fold(s: string): string {
  let out = "";
  for (const ch of s) out += TYPOGRAPHIC.get(ch) ?? ch;
  return out;
}

const DECIMAL = /^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/;
const DEGREE = String.fromCharCode(0xb0);
const RA_HMS = new RegExp(`^(\\d+)[h:${WS}]+(\\d+)[m:${WS}]+([\\d.]+)s?$`);
const RA_HM = new RegExp(`^(\\d+)[h:${WS}]+([\\d.]+)m?$`);
const DEC_DMS = new RegExp(`^(\\d+)[:${WS}]+(\\d+)[:${WS}]+([\\d.]+)$`);
const DEC_DM = new RegExp(`^(\\d+)[:${WS}]+([\\d.]+)$`);

/** Python's `float()` on the forms an operator types; NaN for anything else
 *  ("1.2.3", which `parseFloat` would read as 1.2). */
function pyFloat(s: string): number {
  const t = pyStrip(s);
  return DECIMAL.test(t) ? Number(t) : NaN;
}

/** RA in hours from the node's text, or null. */
export function parseRaHours(text: string | number): number | null {
  const t = pyStrip(fold(String(text)));
  let m = RA_HMS.exec(t);
  let v: number;
  if (m) v = pyFloat(m[1]) + pyFloat(m[2]) / 60 + pyFloat(m[3]) / 3600;
  else if ((m = RA_HM.exec(t))) v = pyFloat(m[1]) + pyFloat(m[2]) / 60;
  else v = pyFloat(t);
  return Number.isFinite(v) ? v : null;
}

/** Dec in degrees from the node's text, or null. */
export function parseDecDeg(text: string | number): number | null {
  const t = pyStrip(fold(String(text))).split(DEGREE).join(" ")
    .split("'").join(" ").split('"').join(" ");
  const sign = pyStrip(t).startsWith("-") ? -1 : 1;
  let a = 0;
  let b = t.length;
  const edge = (c: string) => c === "+" || c === "-" || c === " " || c === "\t";
  while (a < b && edge(t[a])) a++;
  while (b > a && edge(t[b - 1])) b--;
  const body = t.slice(a, b);
  let m = DEC_DMS.exec(body);
  let v: number;
  if (m) v = sign * (pyFloat(m[1]) + pyFloat(m[2]) / 60 + pyFloat(m[3]) / 3600);
  else if ((m = DEC_DM.exec(body))) v = sign * (pyFloat(m[1]) + pyFloat(m[2]) / 60);
  else v = pyFloat(t);
  return Number.isFinite(v) ? v : null;
}

/** Whether the operator typed coordinates: both RA and Dec non-blank as
 *  `textOf` reads them, the server's `identity.typed_coordinates` (ruling 6:
 *  text with something besides Python's whitespace, or a finite number, 0
 *  included). Without them the server places the block by its NAME, through
 *  the catalogue, and the modal has nothing to lay out until it is searched.
 *  Graded with the server on server/tests/fixtures/typed_coordinates_cases.json
 *  (`typedCoordinatesFixture.test.ts`). */
export function typedCoordinates(draft: FramingDraft): boolean {
  return textOf(draft, "ra") !== "" && textOf(draft, "dec") !== "";
}

/** The draft's centre, or null when it has no typed coordinates, they do not
 *  parse, or the Dec is off the sphere. RA folded into [0, 24). */
export function draftCentre(draft: FramingDraft): { ra_hours: number; dec_deg: number } | null {
  if (!typedCoordinates(draft)) return null;
  const ra = parseRaHours(draft.ra);
  const dec = parseDecDeg(draft.dec);
  if (ra === null || dec === null || dec < -90 || dec > 90) return null;
  return { ra_hours: wrapRaHours(ra), dec_deg: dec };
}

/** The draft's identity, SKIP EXCLUDED (spec D5 and 3.3: "skipping a panel is
 *  not part of the identity"): the centre to the identity key's precision, or
 *  the name for a block the catalogue places, and the layout to its key's
 *  places. Two drafts with one key are one geometry, so the modal knows a
 *  skip-only change for what it is and never asks about it. */
export function layoutKey(draft: FramingDraft): string {
  const l = layoutOf(draft);
  const centre = draftCentre(draft);
  const where = typedCoordinates(draft)
    ? (centre ? [centre.ra_hours.toFixed(6), centre.dec_deg.toFixed(6)]
      : [textOf(draft, "ra"), textOf(draft, "dec")])
    : ["name", textOf(draft, "name")];
  return JSON.stringify([
    where, l.rows, l.cols, l.overlap.toFixed(4),
    l.rotation_deg === null ? null : l.rotation_deg.toFixed(3),
    l.fov_x.toFixed(5), l.fov_y.toFixed(5),
  ]);
}

// ------------------------------------------------------------ SUGGEST GRID

/** One axis of SUGGEST GRID (ATL-05, spec 2.4): the panels it takes to span
 *  `sizeDeg` with frames of `fovDeg` overlapping by `overlap` (a fraction),
 *  `ceil((size - fov*ov) / (fov*(1-ov)))`, held to 1..GRID_MAX. The 1e-9 keeps
 *  an exact fit (5.0 deg in three 2.0 deg frames at 25%) from rounding up to
 *  a fourth column on float noise. A hint only: the catalogue carries one
 *  size and no axes or PA yet. */
export function suggestAxis(sizeDeg: number, fovDeg: number, overlap: number): number | null {
  if (!(sizeDeg > 0) || !(fovDeg > 0) || !Number.isFinite(sizeDeg + fovDeg)) return null;
  const ov = Math.min(0.5, Math.max(0, overlap));
  const n = Math.ceil((sizeDeg - fovDeg * ov) / (fovDeg * (1 - ov)) - 1e-9);
  return Math.min(GRID_MAX, Math.max(1, n));
}

/** SUGGEST GRID for an object `sizeArcmin` across, with the draft's field
 *  and overlap: columns along the field's width, rows along its height. Null
 *  when the size or the field is unknown. */
export function suggestGrid(sizeArcmin: number, draft: FramingDraft): { cols: number; rows: number } | null {
  const l = layoutOf(draft);
  const cols = suggestAxis(sizeArcmin / 60, l.fov_x, l.overlap);
  const rows = suggestAxis(sizeArcmin / 60, l.fov_y, l.overlap);
  return cols === null || rows === null ? null : { cols, rows };
}

// ------------------------------------------------------------------ locks

export const ANY_ANGLE_ON_A_GRID = "a grid is laid out at one camera angle";
/** DONE's lock on a grid with no angle (S5 orchestrator ruling 1, #411). */
export const NO_ANGLE_ON_A_GRID =
  "a grid is laid out at one camera angle: choose one, or USE MEASURED after a plate solve";
export const NO_ROTATOR =
  "the active profile has no rotator: set the camera's angle by hand and choose CAMERA FIXED AT";
export const NO_OPTICS = "set the camera and focal length in Settings > Optics";

function hasField(draft: FramingDraft): boolean {
  const l = layoutOf(draft);
  return l.fov_x > 0 && l.fov_y > 0;
}

function liveField(rig: RigBlock | null | undefined): [number, number] | null {
  const f = rig?.fov_deg;
  if (!Array.isArray(f) || f.length !== 2) return null;
  const [x, y] = f;
  return Number.isFinite(x) && Number.isFinite(y) && x > 0 && y > 0 ? [x, y] : null;
}

/** Each ANGLE segment's lock reason, null when it may be chosen (spec 2.4).
 *  ANY ANGLE locks on a grid larger than 1x1, because panels laid out at no
 *  angle do not tile (doctor M2). ROTATE TO locks only when the rig says
 *  there is NO rotator: unknown is not no (rig.py), and locking on a rig
 *  nobody asked about would take the choice from an operator who has one.
 *  CAMERA FIXED AT never commands anything, so it never locks. */
export function angleLocks(draft: FramingDraft, rig: RigBlock | null | undefined): Record<TargetAngle, string | null> {
  const { rows, cols } = gridOf(draft);
  return {
    "Any angle": rows * cols > 1 ? ANY_ANGLE_ON_A_GRID : null,
    "Rotate to PA": rig?.has_rotator === false ? NO_ROTATOR : null,
    "Camera fixed at PA": null,
  };
}

/** The GRID controls' lock (COLS, ROWS, OVERLAP, SUGGEST GRID): locked with
 *  the Settings sentence when there is no field to tile with, neither one the
 *  block snapshotted nor a live one MATCH CAMERA could take. A block framed
 *  on another night keeps its snapshot, so it stays editable while the rig is
 *  away. */
export function gridLock(draft: FramingDraft, rig: RigBlock | null | undefined): string | null {
  return hasField(draft) || liveField(rig) !== null ? null : NO_OPTICS;
}

/** MATCH CAMERA's lock: there is nothing to match without live optics. */
export function matchCameraLock(rig: RigBlock | null | undefined): string | null {
  return liveField(rig) === null ? NO_OPTICS : null;
}

/** MATCH CAMERA (spec 2.4): the live bin-1 field and where it came from,
 *  snapshotted into the draft. The draft unchanged when the rig has none. */
export function matchCamera(draft: FramingDraft, rig: RigBlock | null | undefined): FramingDraft {
  const f = liveField(rig);
  if (f === null) return draft;
  return { ...draft, fovX: f[0], fovY: f[1], fovFrom: rig?.fov_from ?? "" };
}

/** Choosing an ANGLE segment. ANY ANGLE writes rotation -1 (spec 2.4), the
 *  one value that means "none" (#150). A set mode keeps a set angle and turns
 *  "none" into 0, north up: a set mode with a negative rotation is doctor M2
 *  and refused at the run. Only a press on a segment comes here, never a grid
 *  change (S5 orchestrator ruling 1, #411): the 0 is then on screen, in the
 *  degree field beside the segment just pressed, for the operator to change. */
export function setAngleMode(draft: FramingDraft, mode: TargetAngle): FramingDraft {
  if (mode === "Any angle") return { ...draft, angle: mode, rotation: -1 };
  const rot = numOf(draft, "rotation");
  return { ...draft, angle: mode, rotation: Number.isFinite(rot) && rot >= 0 ? draft.rotation : 0 };
}

// ------------------------------------------- an angle for a grid (#411)
//
// S5 ORCHESTRATOR RULING 1 (#411; spec 1.8: "a default angle nobody chose is
// exactly the I-04 defect"). A grid is laid out at one camera angle, so a
// draft at ANY ANGLE that becomes one (a stepper, SUGGEST GRID) owes an angle.
// S4 paid it with ROTATE TO, whose "none" is 0, so a DONE after a column
// change commanded the rotator to PA 0: an angle nobody chose, set in the
// ANGLE section below GRID, off screen on a phone. Now nothing pays it but
// the operator. The draft stays at ANY ANGLE and DONE is locked until an angle
// is chosen (`gridAngleLock`); with a measurement in `status.sky_angle` the
// sheet OFFERS the measured angle (`angleOffer`), which applies only when
// pressed (`takeOffer`); and the readout strip says the angle the grid is laid
// out at (`stripAngle`), so the state is on screen wherever the operator is.
// The rotate handle sets the angle it is turned to, as before: that is an
// angle the operator chose.

/** Whether the draft is a grid with no angle to lay it out at: ANY ANGLE, or
 *  a set mode whose rotation reads as none (3.1). Doctor M2 refuses both. */
function angleOwed(draft: FramingDraft): boolean {
  const l = layoutOf(draft);
  return l.rows * l.cols > 1 && l.rotation_deg === null;
}

/** DONE's lock for a grid with no angle, or null. A single panel at ANY
 *  ANGLE owes none: it is a whole block, whose first solve's angle the run
 *  locks (Revision 2 ruling 9). */
export function gridAngleLock(draft: FramingDraft): string | null {
  return angleOwed(draft) ? NO_ANGLE_ON_A_GRID : null;
}

/** The measured angle, offered to a grid that owes one. */
export interface AngleOffer {
  mode: TargetAngle;
  /** Degrees, the measurement to a tenth, as USE MEASURED takes it. */
  rotation: number;
  /** The offer's button: "ROTATE TO 37.2 deg", in the segments' words. */
  label: string;
}

/** The offer, or null when the draft owes no angle or nothing was measured.
 *  ROTATE TO the measured angle, the one the camera was solved at; CAMERA
 *  FIXED AT it where the rig says there is no rotator (unknown is not no, as
 *  ROTATE TO's own lock reads it), or where the operator already chose CAMERA
 *  FIXED AT and it lacks only the angle. */
export function angleOffer(
  draft: FramingDraft, rec: MeasuredAngle | null | undefined, rig: RigBlock | null | undefined,
): AngleOffer | null {
  const pa = measuredPa(rec);
  if (pa === null || !angleOwed(draft)) return null;
  const fixed = angleOf(draft) === "Camera fixed at PA" || rig?.has_rotator === false;
  const mode: TargetAngle = fixed ? "Camera fixed at PA" : "Rotate to PA";
  return { mode, rotation: pa, label: `${fixed ? "CAMERA FIXED AT" : "ROTATE TO"} ${pa.toFixed(1)} deg` };
}

/** The draft once the operator presses the offer: its mode and angle, and
 *  nothing else. */
export function takeOffer(draft: FramingDraft, offer: AngleOffer): FramingDraft {
  return { ...draft, angle: offer.mode, rotation: offer.rotation };
}

/** The readout strip's angle line: the angle the grid is laid out at, "rotate
 *  to 30.0 deg" or "camera fixed at 30.0 deg"; "any angle" for a single panel
 *  that holds none, and "no angle" for a grid that owes one. */
export function stripAngle(draft: FramingDraft): string {
  const l = layoutOf(draft);
  if (l.rotation_deg === null) return l.rows * l.cols > 1 ? "no angle" : "any angle";
  const deg = `${l.rotation_deg.toFixed(1)} deg`;
  const mode = angleOf(draft);
  return mode === "Rotate to PA" ? `rotate to ${deg}`
    : mode === "Camera fixed at PA" ? `camera fixed at ${deg}` : deg;
}

// ------------------------------------------------ the route and DONE

/** The body of `POST /api/framing/mosaic` for the draft (`MosaicSpecIn`), or
 *  null when the route cannot answer it: no typed coordinates the mirror can
 *  read, no camera field (the route requires one above 0), or a grid past
 *  GRID_MAX. `anchor` is the node's stored `frameAnchor` text, sent only when
 *  it holds one, so the answer carries `reframe` (spec 3.3). */
export interface MosaicRequest {
  ra_hours: number;
  dec_deg: number;
  rows: number;
  cols: number;
  overlap: number;
  rotation_deg: number;
  fov_x_deg: number;
  fov_y_deg: number;
  anchor?: string;
}

export function mosaicRequest(draft: FramingDraft, anchor: string | number | undefined): MosaicRequest | null {
  const centre = draftCentre(draft);
  const l = layoutOf(draft);
  if (centre === null || l.fov_x <= 0 || l.fov_y <= 0) return null;
  if (l.rows > GRID_MAX || l.cols > GRID_MAX) return null;
  const req: MosaicRequest = {
    ra_hours: centre.ra_hours, dec_deg: centre.dec_deg,
    rows: l.rows, cols: l.cols, overlap: l.overlap,
    // Any angle travels as -1, what the anchor calls it, so the route's
    // reframe does not read a 1x1 at any angle as a crossing to PA 0.
    rotation_deg: l.rotation_deg ?? -1,
    fov_x_deg: l.fov_x, fov_y_deg: l.fov_y,
  };
  const a = String(anchor ?? "").trim();
  if (a !== "") req.anchor = a;
  return req;
}

/** The identity of one request: an answer is for the CURRENT spec only when
 *  its key equals the key of the request the draft makes now. */
export function requestKey(req: MosaicRequest): string {
  return JSON.stringify([req.ra_hours, req.dec_deg, req.rows, req.cols, req.overlap,
    req.rotation_deg, req.fov_x_deg, req.fov_y_deg, req.anchor ?? ""]);
}

/** Everything the sheet knows about the route, in one value. */
export interface ServerView {
  request: MosaicRequest | null;
  answer: PanelAnswer | null;
  offline: boolean;
  /** `CAP_VIEW_SITE_DERIVED`: the route answers only with it (a viewer's
   *  request is a 403, which is not an answer to wait for). */
  canViewSiteDerived: boolean;
}

export const WAITING_FOR_PANELS = "waiting for the server's panel positions";
export const OFFLINE_MIRROR =
  "panels from the offline mirror; the run computes them on the server";

function serverReachable(v: ServerView): boolean {
  return !v.offline && v.canViewSiteDerived;
}

/** The answer, when it is for the request the draft makes NOW, else null. An
 *  answer for a spec the operator has since dragged away from says where the
 *  panels WERE, and DONE on it would commit one layout under another's
 *  labels. */
export function currentAnswer(v: ServerView): PanelAnswer | null {
  if (v.request === null || v.answer === null) return null;
  return v.answer.key === requestKey(v.request) ? v.answer : null;
}

export interface DoneState {
  locked: boolean;
  /** Why DONE is locked, for the HonestButton. */
  reason: string | null;
  /** A standing chip beside DONE when it is open on the mirror. */
  chip: string | null;
}

/** DONE (spec 2.5). Locked, with the reason, until the route has answered
 *  for the current spec. Offline, or as a viewer, the route cannot answer,
 *  so DONE opens on the mirror and says so; that is true, because `to_plan`
 *  lays the panels out with the same `compute_mosaic` at every compile. A
 *  request that FAILED for the current spec opens it the same way: the
 *  server has said all it will about this layout. With no request at all (no
 *  coordinates or no field yet) there is nothing to wait for. */
export function doneState(v: ServerView): DoneState {
  if (v.request === null) return { locked: false, reason: null, chip: null };
  if (!serverReachable(v)) return { locked: false, reason: null, chip: OFFLINE_MIRROR };
  const a = currentAnswer(v);
  if (a === null) return { locked: true, reason: WAITING_FOR_PANELS, chip: null };
  return a.ok ? { locked: false, reason: null, chip: null }
    : { locked: false, reason: null, chip: OFFLINE_MIRROR };
}

// ------------------------------------------------- re-frame carry (2.5)

const arcmin = (deg: number) => `${(deg * 60).toFixed(1)}'`;

/** The server's reframe for the current spec, or null. */
export function currentReframe(v: ServerView): ReframeAnswer | null {
  const a = currentAnswer(v);
  return a !== null && a.ok && a.reframe ? a.reframe : null;
}

export const SERVER_DECIDES = "the server decides at save";

/** The live move line under the readout strip (spec 2.5), e.g. "moved 4.2'
 *  of the 10.0' this grid allows: counts carry over". Null when the block has
 *  no anchor to move against, or while the answer for this spec is on its
 *  way. Offline or as a viewer, the server alone can measure it. */
export function moveLine(v: ServerView): string | null {
  if (v.request === null || v.request.anchor === undefined) return null;
  if (!serverReachable(v)) return SERVER_DECIDES;
  const r = currentReframe(v);
  if (r === null) return null;
  switch (r.reason) {
    case "unchanged": return "unchanged against the anchor: counts carry over";
    case "grid": return "a new grid: counts restart";
    case "angle": return "any angle against a set angle: counts restart";
    case "identity": return "another object: counts restart";
    default: {
      if (r.max_move_deg === null) return SERVER_DECIDES;
      return r.carry
        ? `moved ${arcmin(r.max_move_deg)} of the ${arcmin(r.threshold_deg)} this grid allows: counts carry over`
        : `moved ${arcmin(r.max_move_deg)}, more than the ${arcmin(r.threshold_deg)} this grid allows: counts restart`;
    }
  }
}

const finiteOr0 = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : 0);

/** Every sub the ledger holds on this block: its shot panels' `banked` and
 *  what its skipped panels still hold, since a re-anchor re-keys those too. */
export function bankedSubs(progress: ProgressBlockLike | null | undefined): number {
  if (!progress) return 0;
  let n = finiteOr0(progress.banked);
  for (const s of progress.skipped ?? []) n += finiteOr0(s?.banked);
  return n;
}

export interface ReframeDecision {
  ask: boolean;
  question: string | null;
}

/** Whether DONE asks before it commits (spec 2.5, Revision 2 ruling 3), and
 *  the question, in the server's numbers.
 *
 *  It asks ONLY when the progress route reports banked subs AND the layout
 *  changed AND either the grid changed (which always restarts) or the
 *  server's reframe for this very spec says the counts do not carry. So:
 *  nothing banked never asks (there is nothing to lose); a skip-only change
 *  never asks (skip is not in the identity, and re-enabling a panel restores
 *  its progress); and offline, where only the grid rule can be known, a move
 *  is left to the save, whose answer lists every block it re-anchored. */
export function reframeDecision(a: {
  stored: Params;
  draft: FramingDraft;
  progress: ProgressBlockLike | null | undefined;
  view: ServerView;
}): ReframeDecision {
  const no: ReframeDecision = { ask: false, question: null };
  const banked = bankedSubs(a.progress);
  if (!(banked > 0)) return no;
  const before = draftFromParams(a.stored);
  if (layoutKey(before) === layoutKey(a.draft)) return no;
  const was = gridOf(before);
  const now = gridOf(a.draft);
  const live = livePanels(a.draft);
  const tail = `${live === 1 ? "the panel starts" : `all ${live} panels start`} from zero: ` +
    (banked === 1 ? "1 captured frame belongs to the old layout and stays on disk."
      : `${banked} captured frames belong to the old layout and stay on disk.`);
  if (was.rows !== now.rows || was.cols !== now.cols) {
    // A grid SIZE is columns x rows (S4 orchestrator ruling 1, #339).
    return { ask: true, question: `Changing the grid from ${was.cols}x${was.rows} to ${now.cols}x${now.rows} means ${tail}` };
  }
  const r = currentReframe(a.view);
  if (r === null || r.carry) return no;
  if (r.reason === "angle") return { ask: true, question: `Changing between any angle and a set angle means ${tail}` };
  if (r.reason === "identity") return { ask: true, question: `Framing another object means ${tail}` };
  if (r.max_move_deg === null) return { ask: true, question: `The server will restart the counts at save: ${tail}` };
  return {
    ask: true,
    question: `Re-framing moves the panels ${arcmin(r.max_move_deg)}, more than the ${arcmin(r.threshold_deg)} this grid allows, so ${tail}`,
  };
}

// ------------------------------------------------------------- readouts

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** Panels the draft shoots: its grid less the panels its skip names. */
export function livePanels(draft: FramingDraft): number {
  const { rows, cols } = gridOf(draft);
  return rows * cols - parseSkip(draft.skip, rows, cols).skip.length;
}

const pad2 = (n: number) => String(n).padStart(2, "0");

/** RA as the strip writes it, "00h42m44s", rounded to the second. */
export function stripRa(hours: number): string {
  const total = ((Math.round(hours * 3600) % 86400) + 86400) % 86400;
  return `${pad2(Math.floor(total / 3600))}h${pad2(Math.floor((total % 3600) / 60))}m${pad2(total % 60)}s`;
}

/** Dec as the strip writes it, "+41 16'", rounded to the arcminute, with
 *  the sign always written (a value that rounds to 0 is "+00 00'"). */
export function stripDec(deg: number): string {
  const total = Math.round(Math.abs(deg) * 60);
  const sign = deg < 0 && total > 0 ? "-" : "+";
  return `${sign}${pad2(Math.floor(total / 60))} ${pad2(total % 60)}'`;
}

/** The mono readout strip under the sky (spec 2.2): the grid's extent in
 *  the tangent plane, the panels it shoots, and where it is, e.g. "5.0 x 2.3
 *  deg | 6 panels | 00h42m44s +41 16'" with each | a middle dot (U+00B7). The
 *  extent is the client mirror's `mosaicTotalFov`, the same formula the
 *  server answers with, so it never waits on the route. A part the draft
 *  cannot say is left out.
 *
 *  The spec's example (2.2) reads "5.0 x 3.3 deg" for 6 panels; no 3x2 of
 *  the reference 2.0 x 1.33 deg camera at 25% is that tall ((2 - 0.25) x
 *  1.33 = 2.33 deg; 3.3 is a 3x3's height), so the tests pin the computed
 *  2.3. */
export function readoutStrip(draft: FramingDraft): string {
  const l = layoutOf(draft);
  const parts: string[] = [];
  if (l.fov_x > 0 && l.fov_y > 0) {
    const t = mosaicTotalFov(l.cols, l.rows, l.overlap, l.fov_x, l.fov_y);
    parts.push(`${t.total_fov_x_deg.toFixed(1)} x ${t.total_fov_y_deg.toFixed(1)} deg`);
  }
  const total = l.rows * l.cols;
  const live = livePanels(draft);
  parts.push(live === total ? plural(total, "panel", "panels") : `${live} of ${total} panels`);
  const c = draftCentre(draft);
  if (c !== null) parts.push(`${stripRa(c.ra_hours)} ${stripDec(c.dec_deg)}`);
  else if (!typedCoordinates(draft) && textOf(draft, "name") !== "") parts.push(textOf(draft, "name"));
  return parts.join(" " + String.fromCharCode(0xb7) + " ");
}

/** GRID's camera-field line (spec 2.4): "Tiled for 2.00 x 1.33 deg at bin 1
 *  (profile Refractor, matched 2026-09-23)". */
export function cameraFieldLine(draft: FramingDraft): string {
  const l = layoutOf(draft);
  if (!(l.fov_x > 0 && l.fov_y > 0)) return "No camera field recorded: MATCH CAMERA takes it from the rig";
  const from = textOf(draft, "fovFrom");
  return `Tiled for ${l.fov_x.toFixed(2)} x ${l.fov_y.toFixed(2)} deg at bin 1${from ? ` (${from})` : ""}`;
}

/** The share of the snapshot the live field may differ by on either axis
 *  before the banner shows (doctor `FIELD_DRIFT_SHARE`, M5). */
export const FIELD_DRIFT_SHARE = 0.02;

export interface DriftBanner {
  /** "loss" when the panels would leave gaps (M5's loss blocks the run
   *  until accepted), "warn" otherwise. */
  level: "warn" | "loss";
  text: string;
}

/** The optics-drift banner (spec 2.4, doctor M5): the live field differs
 *  from the block's snapshot by more than 2% on either axis. On a grid it
 *  says what the drift does to the seams in the doctor's own words, and when
 *  the live field is narrower than one step of the grid it is M5's sentence:
 *  the panels would leave gaps. A single panel has no seams to open. */
export function driftBanner(draft: FramingDraft, rig: RigBlock | null | undefined): DriftBanner | null {
  const live = liveField(rig);
  const l = layoutOf(draft);
  if (live === null || !(l.fov_x > 0 && l.fov_y > 0)) return null;
  const snap: [number, number] = [l.fov_x, l.fov_y];
  if (!live.some((lv, i) => Math.abs(lv - snap[i]) > FIELD_DRIFT_SHARE * snap[i])) return null;
  const head = `framed for ${snap[0].toFixed(2)} x ${snap[1].toFixed(2)} deg; ` +
    `this camera now images ${live[0].toFixed(2)} x ${live[1].toFixed(2)} deg`;
  if (l.rows * l.cols === 1) return { level: "warn", text: `${head}. Re-frame.` };
  const keep = 1 - l.overlap;
  if (live.some((lv, i) => lv < snap[i] * keep)) {
    return { level: "loss", text: `${head}, so the panels would leave gaps. Re-frame.` };
  }
  const so = live.every((lv, i) => lv >= snap[i])
    ? "the panels would overlap more than framed" : "the panels would overlap less than framed";
  return { level: "warn", text: `${head}, so ${so}. Re-frame.` };
}

/** How a solve's `source` reads after "by". */
const SOLVE_PHRASE: Record<string, string> = {
  "plate solve + sync": "the centring solve",
  "rotate to PA": "the rotate-to-angle solve",
  "rotator sync": "the rotator sync solve",
  "saved-frame WCS": "a saved frame's WCS",
  "guide-scope offset": "the guide-scope offset solve",
  "polar alignment": "the polar alignment solve",
};

function ago(seconds: number): string {
  if (seconds < 60) return "under a minute ago";
  const min = Math.floor(seconds / 60);
  return min < 120 ? `${min} min ago` : `${Math.floor(min / 60)} h ago`;
}

function measuredPa(rec: MeasuredAngle | null | undefined): number | null {
  const pa = rec?.pa_deg;
  return typeof pa === "number" && Number.isFinite(pa) ? Math.round(pa * 10) / 10 : null;
}

/** When the frame the angle was measured on was taken, unix seconds:
 *  `exposed_at` when it is finite, else `solved_at`, else null (#439).
 *  Freshness is judged on the exposure (types.ts `SkyAngleRecord`: "a stale
 *  frame can finish solving late"), as the engine's angle check and the
 *  ruling 9 angle lock judge it (#292). The saved-frame WCS stamp solves a
 *  frame after it has been saved, so its `solved_at` trails the exposure; an
 *  age from `solved_at` called a five-minute-old angle "under a minute ago".
 *  `solved_at` stays as the fallback for a record whose exposure time is
 *  null or absent, since the solve's time is then the best there is. */
function measuredAt(rec: MeasuredAngle): number | null {
  if (Number.isFinite(rec.exposed_at)) return rec.exposed_at;
  return Number.isFinite(rec.solved_at) ? rec.solved_at : null;
}

/** The USE MEASURED chip (spec 2.4), from `status.sky_angle`: "camera
 *  measured 37.2 deg, 14 min ago, by the centring solve, pier west". The age
 *  is the measured frame's (`measuredAt`), not the solve's. Null with no
 *  record. `nowS` is the caller's clock, in unix seconds. */
export function useMeasuredLine(rec: MeasuredAngle | null | undefined, nowS: number): string | null {
  const pa = measuredPa(rec);
  if (rec == null || pa === null) return null;
  const parts = [`camera measured ${pa.toFixed(1)} deg`];
  const at = measuredAt(rec);
  if (at !== null && Number.isFinite(nowS)) parts.push(ago(Math.max(0, nowS - at)));
  const src = String(rec.source ?? "").trim();
  parts.push(`by ${SOLVE_PHRASE[src] ?? (src ? `the ${src} solve` : "a solve")}`);
  if (rec.pier_side === "east" || rec.pier_side === "west") parts.push(`pier ${rec.pier_side}`);
  return parts.join(", ");
}

/** One tap on USE MEASURED: the grid laid out at the angle the camera sits
 *  at. That is the no-rotator workflow, so a draft at ANY ANGLE becomes
 *  CAMERA FIXED AT; a set mode keeps its mode and takes the angle. */
export function useMeasured(draft: FramingDraft, rec: MeasuredAngle | null | undefined): FramingDraft {
  const pa = measuredPa(rec);
  if (pa === null) return draft;
  const mode = angleOf(draft) === "Any angle" ? "Camera fixed at PA" : draft.angle;
  return { ...draft, angle: mode, rotation: pa };
}

/** The tolerance line (spec 2.4, A.2), from `framing.angle_tolerance_deg`:
 *  "this layout tolerates a camera error of 6.0 deg (convergence and angle
 *  error together use at most half the overlap at a four-panel corner)". A
 *  single row or column has no four-panel corner, so its figure is
 *  conservative and the line says so; a tolerance of 0 is doctor M15. Null
 *  for a single panel, or while the number is unknown. */
export function toleranceLine(toleranceDeg: number | null | undefined, draft: FramingDraft): string | null {
  const { rows, cols } = gridOf(draft);
  if (rows * cols === 1 || typeof toleranceDeg !== "number" || !Number.isFinite(toleranceDeg)) return null;
  if (toleranceDeg <= 0) {
    return "meridian convergence has used the half of the overlap this layout allows for angle error, so no camera error is tolerated: widen the overlap or use fewer columns";
  }
  const head = `this layout tolerates a camera error of ${toleranceDeg.toFixed(1)} deg`;
  if (rows === 1 || cols === 1) {
    return `${head}; a single row or column has no four-panel corner where a hole could open, so this is conservative`;
  }
  return `${head} (convergence and angle error together use at most half the overlap at a four-panel corner)`;
}

/** tonight.py `_duration`: "2 m 40 s", or "45 s" under a minute, rounded to
 *  the second as Python's round() rounds (half to even), so the modal and the
 *  STORY brief never spell one measured hop two ways. */
export function hopDuration(seconds: number): string {
  const f = Math.floor(seconds);
  const d = seconds - f;
  const s = d > 0.5 ? f + 1 : d < 0.5 ? f : (f % 2 === 0 ? f : f + 1);
  const m = Math.floor(s / 60);
  return m ? `${m} m ${s % 60} s` : `${s % 60} s`;
}

/** Hours with at most two decimals, trailing zeros dropped: 9.75, 58.5, 18. */
function hours(seconds: number): string {
  return String(Number((seconds / 3600).toFixed(2)));
}

const finiteNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** The RUN section's readouts (spec 2.4), spelled from the compile's numbers
 *  (`readouts[node_id]`) and the rig block's measured hop, in the spec's
 *  order. For the shipped default cycle on a 3x2:
 *    6 panels x 7 filters x 45 = 1890 subs
 *    9.75 h per panel, 58.5 h in all
 *    270 visits at 1 pass per visit
 *    hop: not measured on this rig yet
 *    meridian: no idle before the flip
 *    focus: refocus on temperature
 *
 *  Nothing here is arithmetic the server did not do. The product form of the
 *  first line is written only when the server's own three numbers multiply
 *  to its total; steps that owe different counts (an LRGB cycle and a
 *  CAPTURE of Ha in one lane) would make "x 45" a false sum, so those read
 *  as the total over panels and filters. A single target makes no hops and
 *  has no visit bound (the server answers null), so it has no visits line
 *  and no hop line: a hop readout for a block that never hops is noise. */
export function runLines(r: RunReadouts, rig: RigBlock | null | undefined): string[] {
  const lines: string[] = [];
  const panels = plural(r.panels, "panel", "panels");
  const filters = plural(r.steps, "filter", "filters");
  lines.push(finiteNum(r.rounds) && r.panels * r.steps * r.rounds === r.subs_total
    ? `${panels} x ${filters} x ${r.rounds} = ${plural(r.subs_total, "sub", "subs")}`
    : `${plural(r.subs_total, "sub", "subs")} over ${panels} and ${filters}`);
  lines.push(`${hours(r.panel_s)} h per panel, ${hours(r.total_s)} h in all`);
  if (finiteNum(r.visits_total)) {
    lines.push(finiteNum(r.visit_passes)
      ? `${plural(r.visits_total, "visit", "visits")} at ${plural(r.visit_passes, "pass", "passes")} per visit`
      // Panel-first: the visit bound does not apply, each panel is visited
      // once and shot to completion (the engine's `_visits_owed`).
      : `${plural(r.visits_total, "visit", "visits")}: each panel runs to completion`);
    // The MEASURED hop is the rig block's (the block's `hop_s` is the seed
    // until one is timed), and the efficiency is the server's, present only
    // with a measured hop.
    const hop = rig?.hop_s;
    const samples = rig?.hop_samples ?? 0;
    if (rig?.hop_measured === true && finiteNum(hop) && hop > 0 && samples >= 1) {
      let line = `hop ${hopDuration(hop)}, measured over ${plural(samples, "hop", "hops")}`;
      if (finiteNum(r.efficiency)) line += `; a visit is ${Math.round(100 * r.efficiency)}% shutter`;
      lines.push(line);
    } else {
      lines.push("hop: not measured on this rig yet");
    }
  }
  if (typeof r.preflip_idle_s === "number" && Number.isFinite(r.preflip_idle_s)) {
    const min = Math.round(r.preflip_idle_s / 6) / 10;
    lines.push(min > 0 ? `meridian: up to ${min.toFixed(1)} min idle before the flip`
      : "meridian: no idle before the flip");
  }
  if (r.focus === "temperature") lines.push("focus: refocus on temperature");
  else if (r.focus === "frames" && typeof r.autofocus_every === "number" && r.autofocus_every > 0) {
    lines.push(`focus: a sweep every ${plural(r.autofocus_every, "frame", "frames")}`);
  } else {
    // "once": neither is armed, and a hop owes no sweep (5.6 step 6). A
    // single target has no first panel, so its one sweep is at the start
    // (#413).
    const when = r.mode === "single" ? "at the start" : "at the first panel";
    lines.push(`focus: a sweep only ${when}; set a temperature delta to refocus as the night cools`);
  }
  return lines;
}

// ------------------------------------------------------------ run mode (2.6)
//
// While the flow's session runs, the sheet opens read-only and draws what the
// run is doing to each panel it framed: the one being shot, the ones set
// aside tonight with the engine's reason, and the ones done. Three facts, from
// two answers the sheet already holds and never from a count of its own:
//
//   - WHICH PANEL AND WHAT IS SET ASIDE come from the live `state.group`,
//     read through flowRunState: `groupForBlock` finds the group by the
//     progress block's `group_id` (the id the plan minted and the engine
//     publishes, never the name, which two blocks can share), and
//     `panelStateOf` says what one panel is doing, with the meridian rule
//     already applied (no panel is "being shot" across a meridian wait, for
//     an operator or for a viewer, who is not even told which panel is held).
//   - DONE comes from the progress route, which the slice re-reads while the
//     run's frames land (#214), so a panel turns hatched as its last sub is
//     banked, a re-read at most behind.
//
// NO TIME. Nothing here reads the group's `visit_elapsed_s`, the state's
// `live.meridian_eta_s` or any ETA: a meridian wait's end is timed by the
// crossing, which is the site's longitude (spec 5.10, 6.9), and a panel's
// state is all this sheet shows of the run.

/** What the live group says of each panel of the draft's grid, by label
 *  ("<row>-<col>", counted from 1, the engine's label and PanelLayer's): only
 *  the panels with something to draw are present.
 *
 *  Empty with no group, and empty when the progress block describes another
 *  grid than the draft's (`grid` is the progress block's): the group's labels
 *  name the panels of the plan that is running, and on another grid "2-2" is
 *  another piece of sky. PanelsSection's `progressByCell` refuses the counts
 *  for the same reason.
 *
 *  `run` is the sequence state the group came from, so the panel the run
 *  is on reads as shot only while the run is running, and as the current
 *  panel while it is paused, holding or stopping (#451, `panelStateOf`).
 *  Required, as `panelStateOf`'s is: a caller that left it out used to get
 *  "shooting now" for a held run's panel. */
export function runPanelsOf(
  group: SequenceGroupState | null | undefined,
  rows: number,
  cols: number,
  grid: { rows: number; cols: number } | null | undefined,
  run: PanelRunSource | null,
): Record<string, PanelRunState> {
  const out: Record<string, PanelRunState> = {};
  if (!group || !grid || grid.rows !== rows || grid.cols !== cols) return out;
  for (let r = 1; r <= rows; r++) {
    for (let c = 1; c <= cols; c++) {
      const label = `${r}-${c}`;
      const state = panelStateOf(label, group, run);
      if (state) out[label] = state;
    }
  }
  return out;
}

/** How the sky draws one panel (spec 2.3's five states), from the draft's
 *  skip, the progress route's counts and the live run's state for it.
 *
 *  SKIPPED first: a skipped panel is in no plan, so no run can say anything
 *  of it. Then the live states: SET ASIDE, which holds for the night whatever
 *  else the group does, and SHOOTING, ahead of DONE, because the progress
 *  count lags the run by up to a re-read and is never ahead of it, so a panel
 *  that is both is still the panel the visit is on. The CURRENT panel of a
 *  run that is paused, holding or stopping (#451) is drawn as SHOOTING is,
 *  with the corner ticks: it is still the panel the visit is on, and only
 *  the words say no exposure of it is being made. Then DONE, every owed
 *  sub banked, and PENDING. */
export function panelDrawState(
  panel: { skipped: boolean; banked: number; total: number },
  run: PanelRunState | null | undefined,
): PanelState {
  if (panel.skipped) return "skipped";
  if (run?.kind === "set_aside") return "set_aside";
  if (run?.kind === "shooting" || run?.kind === "current") return "shooting";
  return panel.total > 0 && panel.banked >= panel.total ? "done" : "pending";
}

// ------------------------------------------------------------ skip mirror
//
// compile.py `parse_skip`, character for character, because the modal draws
// a skipped panel from ITS reading and the run shoots what the server reads.
// Graded against server/tests/fixtures/skip_cases.json, which
// test_flows_skip_fixture.py grades parse_skip against too.
//
// Python's `\d` is every Unicode decimal digit and `int()` reads them all, so
// a fullwidth "3-1" from a phone keyboard skips panel 3-1 on the server; the
// mirror matches `\p{Nd}` and reads each digit by its place in its run of
// ten (Unicode encodes every decimal digit set as ten consecutive code points
// from zero). Two limits it cannot copy: a digit set newer than the server's
// Python knows, and an entry of more than 4300 digits, on which `int()`
// raises instead of answering (#328); both read as unread here.

const SKIP_RE = new RegExp(`^(\\p{Nd}+)[${WS}]*-[${WS}]*(\\p{Nd}+)$`, "u");
const ND = /^\p{Nd}$/u;

/** CPython's integer string limit (`sys.get_int_max_str_digits()`, 4300 by
 *  default). It counts EVERY digit, leading zeros included, so "000...01-1"
 *  past the limit makes `int()` raise although its value is 1; the mirror
 *  must not read that entry as panel 1-1 when the server cannot read it. */
const PY_INT_MAX_DIGITS = 4300;

/** Python's `int()` on a run of decimal digits, or NaN past the limit. */
function pyInt(digits: string): number {
  let n = 0;
  let count = 0;
  for (const ch of digits) {
    if (++count > PY_INT_MAX_DIGITS) return NaN;
    const cp = ch.codePointAt(0) as number;
    let start = cp;
    while (start > 0 && ND.test(String.fromCodePoint(start - 1))) start--;
    n = n * 10 + ((cp - start) % 10);
  }
  return n;
}

export interface SkipReading {
  /** 1-based [row, col], each panel once, in grid order. */
  skip: [number, number][];
  /** Every entry that names no panel of this grid, as typed. */
  unread: string[];
}

/** compile.py `parse_skip(text, rows, cols)`. */
export function parseSkip(text: string | number | null | undefined, rows: number, cols: number): SkipReading {
  const src = text === undefined || text === null || text === "" || text === 0 ? "" : String(text);
  const seen = new Set<number>();
  const skip: [number, number][] = [];
  const unread: string[] = [];
  for (const chunk of src.split(/[,;]/)) {
    const token = pyStrip(chunk);
    if (!token) continue;
    const m = SKIP_RE.exec(token);
    if (m === null) { unread.push(token); continue; }
    const r = pyInt(m[1]);
    const c = pyInt(m[2]);
    if (!(r >= 1 && r <= rows && c >= 1 && c <= cols)) { unread.push(token); continue; }
    const k = r * 1e6 + c;
    if (!seen.has(k)) { seen.add(k); skip.push([r, c]); }
  }
  skip.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  return { skip, unread };
}

/** Skip pairs back to the text the node stores: "3-1, 3-2", grid order,
 *  each once. */
export function formatSkip(pairs: ReadonlyArray<readonly [number, number]>): string {
  const uniq = new Map<string, [number, number]>();
  for (const [r, c] of pairs) uniq.set(`${r}-${c}`, [r, c]);
  return [...uniq.values()].sort((a, b) => a[0] - b[0] || a[1] - b[1])
    .map(([r, c]) => `${r}-${c}`).join(", ");
}

/** Tapping panel (row, col) on the sky: skip it, or take it back. The text
 *  comes back in grid order, and every entry the grid cannot read is KEPT, as
 *  typed, after the panels: a "4-1" on a grid cut to three rows names a panel
 *  the operator may grow back, and a tap on another panel must not delete
 *  what they wrote. A panel off the grid changes nothing. */
export function toggleSkip(text: string | number | undefined, rows: number, cols: number, row: number, col: number): string {
  const { skip, unread } = parseSkip(text, rows, cols);
  if (!(Number.isInteger(row) && Number.isInteger(col) && row >= 1 && row <= rows && col >= 1 && col <= cols)) {
    return text === undefined ? "" : String(text);
  }
  const has = skip.some(([r, c]) => r === row && c === col);
  const next = has ? skip.filter(([r, c]) => !(r === row && c === col)) : [...skip, [row, col] as [number, number]];
  return [formatSkip(next), ...unread].filter((s) => s !== "").join(", ");
}

// ------------------------------------------------ per-viewer storage (2.7)
//
// Survey choice and zoom are a convenience for the person looking, kept in
// their browser and keyed by flow and node, so framing M31 on one flow never
// opens another flow's block on M31's zoom. Nothing here is state the run
// reads. EVERY ACCESS IS INSIDE try/catch, the property read of
// `localStorage` included: private browsing, a cleared origin and an embedded
// WebView that throws on access all land on the defaults, and the modal
// renders the same with nothing stored.

export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export interface FramingViewPrefs {
  survey: string;
  /** The sky's width in degrees, or null for "fit the grid". */
  zoomDeg: number | null;
}

export const VIEW_PREFS_DEFAULT: Readonly<FramingViewPrefs> = { survey: "CDS/P/DSS2/color", zoomDeg: null };
export const VIEW_PREFS_PREFIX = "astrodeck-framing-view:";
/** Pinch range of the sky (spec 2.3): a stored zoom outside it is not one. */
export const ZOOM_MIN_DEG = 0.1;
export const ZOOM_MAX_DEG = 10;

/** The storage key for one block on one flow, or null for a flow with no id
 *  (unsaved): node ids repeat across unsaved flows ("n1"), so a key without
 *  the flow would hand one new flow's zoom to the next. Null too for an id
 *  `encodeURIComponent` cannot spell: it throws URIError on a lone surrogate,
 *  a node id is whatever string the flow file holds (the server's
 *  `FlowNode.id` is a bare str), and the sheet reads this in a useState
 *  initializer, where a throw would take the whole modal down. Such a block
 *  is simply not stored (#386). */
export function viewPrefsKey(flowId: string | null | undefined, nodeId: string): string | null {
  const f = String(flowId ?? "").trim();
  if (f === "" || !nodeId) return null;
  try {
    return `${VIEW_PREFS_PREFIX}${encodeURIComponent(f)}/${encodeURIComponent(nodeId)}`;
  } catch {
    return null;
  }
}

function resolveStorage(storage: StorageLike | null | undefined): StorageLike | null {
  if (storage !== undefined) return storage;
  return (globalThis as { localStorage?: StorageLike }).localStorage ?? null;
}

function surveyOf(v: unknown): string | null {
  return typeof v === "string" && v.trim() !== "" && v.length <= 200 ? v : null;
}

function zoomOf(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) && v >= ZOOM_MIN_DEG && v <= ZOOM_MAX_DEG ? v : null;
}

/** This viewer's survey and zoom for the block, or the defaults. Never
 *  throws. `storage` defaults to `localStorage`; a test passes its own. */
export function loadViewPrefs(flowId: string | null | undefined, nodeId: string, storage?: StorageLike | null): FramingViewPrefs {
  const key = viewPrefsKey(flowId, nodeId);
  if (key === null) return { ...VIEW_PREFS_DEFAULT };
  try {
    const raw = resolveStorage(storage)?.getItem(key);
    if (!raw) return { ...VIEW_PREFS_DEFAULT };
    const v = JSON.parse(raw) as { survey?: unknown; zoomDeg?: unknown } | null;
    return { survey: surveyOf(v?.survey) ?? VIEW_PREFS_DEFAULT.survey, zoomDeg: zoomOf(v?.zoomDeg) };
  } catch {
    return { ...VIEW_PREFS_DEFAULT };
  }
}

/** Keep this viewer's survey and zoom for the block; the keys not given keep
 *  what was stored. True when it was written. Never throws. */
export function saveViewPrefs(
  flowId: string | null | undefined, nodeId: string, prefs: Partial<FramingViewPrefs>, storage?: StorageLike | null,
): boolean {
  const key = viewPrefsKey(flowId, nodeId);
  if (key === null) return false;
  try {
    const s = resolveStorage(storage);
    if (!s) return false;
    const was = loadViewPrefs(flowId, nodeId, s);
    const next: FramingViewPrefs = {
      survey: surveyOf(prefs.survey) ?? was.survey,
      zoomDeg: prefs.zoomDeg === undefined ? was.zoomDeg : zoomOf(prefs.zoomDeg),
    };
    s.setItem(key, JSON.stringify(next));
    return true;
  } catch {
    return false;
  }
}
